"""Backfill 2025/2024/2023 score and rank columns into Zhejiang 2026 plans.

The 2026 admission-plan crawler stores the current-year candidate pool in
``admission_plan_2026``. This script attaches historical cutoff data to those
rows so UI/export tables can read score/rank directly from the 2026 plan table.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "data" / "zhejiang" / "college.db"
YEARS = (2025, 2024, 2023)

HISTORY_COLUMNS = {
    "hist_2025_min_score": "INTEGER",
    "hist_2025_min_rank": "INTEGER",
    "hist_2024_min_score": "INTEGER",
    "hist_2024_min_rank": "INTEGER",
    "hist_2023_min_score": "INTEGER",
    "hist_2023_min_rank": "INTEGER",
    "hist_match_source": "TEXT",
}


def light_norm_major(value: str | None) -> str:
    """Normalize whitespace and bracket style while preserving bracket content."""

    s = str(value or "").replace("（", "(").replace("）", ")")
    s = s.replace("，", ",").replace("。", ".")
    return re.sub(r"\s+", "", s).strip()


def loose_norm_major(value: str | None) -> str:
    """Normalize major names for fallback matching.

    Parenthesized direction/campus text is removed only for the fallback layer.
    The fallback is accepted later only when a school/year has one unambiguous
    historical result for that loose name.
    """

    s = light_norm_major(value)
    s = re.sub(r"\([^)]*\)", "", s)
    s = re.sub(r"[、,/，；;·\-\s]", "", s)
    return s.strip()


def ensure_columns(conn: sqlite3.Connection) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(admission_plan_2026)")}
    for column, column_type in HISTORY_COLUMNS.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE admission_plan_2026 ADD COLUMN {column} {column_type}")


def build_history_maps(
    conn: sqlite3.Connection,
) -> tuple[
    dict[tuple[str, str], dict[int, dict[str, Any]]],
    dict[tuple[str, str], dict[int, dict[str, Any]]],
]:
    exact: dict[tuple[str, str], dict[int, dict[str, Any]]] = defaultdict(dict)
    loose_raw: dict[tuple[str, str], dict[int, set[tuple[int | None, int | None, str]]]] = (
        defaultdict(lambda: defaultdict(set))
    )

    for school_name, major_name, year, min_score, min_rank in conn.execute(
        """
        SELECT school_name, major_name, year, min_score, min_rank
        FROM historical_cutoff
        WHERE year IN (2025, 2024, 2023)
        """
    ):
        if not school_name or not major_name:
            continue
        entry = {
            "score": int(min_score) if min_score is not None else None,
            "rank": int(min_rank) if min_rank is not None else None,
            "major_name": str(major_name),
        }
        exact[(str(school_name), light_norm_major(major_name))][int(year)] = entry
        loose_raw[(str(school_name), loose_norm_major(major_name))][int(year)].add(
            (entry["score"], entry["rank"], entry["major_name"])
        )

    loose: dict[tuple[str, str], dict[int, dict[str, Any]]] = defaultdict(dict)
    for key, by_year in loose_raw.items():
        for year, values in by_year.items():
            score_rank_pairs = {(score, rank) for score, rank, _major in values}
            if len(score_rank_pairs) != 1:
                continue
            score, rank = next(iter(score_rank_pairs))
            major_names = sorted({_major for _score, _rank, _major in values})
            loose[key][year] = {"score": score, "rank": rank, "major_name": major_names[0]}
    return exact, loose


def match_history(
    school_name: str,
    major_name: str,
    exact: dict[tuple[str, str], dict[int, dict[str, Any]]],
    loose: dict[tuple[str, str], dict[int, dict[str, Any]]],
) -> tuple[dict[int, dict[str, Any]], dict[str, str]]:
    result: dict[int, dict[str, Any]] = {}
    sources: dict[str, str] = {}

    exact_hit = exact.get((school_name, light_norm_major(major_name)), {})
    loose_hit = loose.get((school_name, loose_norm_major(major_name)), {})
    for year in YEARS:
        if year in exact_hit:
            result[year] = exact_hit[year]
            sources[str(year)] = "name_exact"
        elif year in loose_hit:
            result[year] = loose_hit[year]
            sources[str(year)] = "name_loose_unambiguous"
    return result, sources


def backfill(conn: sqlite3.Connection, dry_run: bool = False) -> dict[str, int]:
    ensure_columns(conn)
    exact, loose = build_history_maps(conn)

    rows = conn.execute(
        "SELECT id, school_name, major_name FROM admission_plan_2026 ORDER BY id"
    ).fetchall()

    stats = {
        "total": len(rows),
        "updated": 0,
        "with_2025": 0,
        "with_2024": 0,
        "with_2023": 0,
        "with_any": 0,
        "with_all_three": 0,
    }

    sql = """
        UPDATE admission_plan_2026
        SET hist_2025_min_score = ?,
            hist_2025_min_rank = ?,
            hist_2024_min_score = ?,
            hist_2024_min_rank = ?,
            hist_2023_min_score = ?,
            hist_2023_min_rank = ?,
            hist_match_source = ?
        WHERE id = ?
    """

    for row_id, school_name, major_name in rows:
        history, sources = match_history(str(school_name), str(major_name), exact, loose)
        values: list[int | str | None] = []
        matched_years = 0
        for year in YEARS:
            entry = history.get(year, {})
            score = entry.get("score")
            rank = entry.get("rank")
            values.extend([score, rank])
            if rank:
                stats[f"with_{year}"] += 1
                matched_years += 1
        if matched_years:
            stats["with_any"] += 1
        if matched_years == len(YEARS):
            stats["with_all_three"] += 1

        values.append(json.dumps(sources, ensure_ascii=False, separators=(",", ":")) if sources else None)
        values.append(int(row_id))
        if not dry_run:
            conn.execute(sql, values)
        stats["updated"] += 1

    if not dry_run:
        conn.commit()
    return stats


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    conn = sqlite3.connect(str(args.db))
    try:
        stats = backfill(conn, dry_run=args.dry_run)
    finally:
        conn.close()

    print("=== 浙江2026专业表历史分数位次回填 ===")
    print(f"total:          {stats['total']}")
    print(f"with 2025:      {stats['with_2025']}")
    print(f"with 2024:      {stats['with_2024']}")
    print(f"with 2023:      {stats['with_2023']}")
    print(f"with any year:  {stats['with_any']}")
    print(f"with all years: {stats['with_all_three']}")
    if args.dry_run:
        print("dry-run:        no database changes written")


if __name__ == "__main__":
    main()
