"""Backfill tuition text and single-subject requirements into 2026 plans.

This keeps current-year application data self-contained in ``admission_plan_2026``:

- ``tuition_text`` preserves raw non-standard tuition strings such as 港元 or
  split-year tuition.
- ``tuition`` stores the conservative numeric amount used by budget filtering.
- ``single_subject_requirement_*`` stores per-program Chinese/math/foreign
  minimum score requirements parsed from 2026 plan text and school charters.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "data" / "zhejiang" / "college.db"
DEFAULT_JSONL = PROJECT_ROOT / "data" / "zhejiang" / "raw" / "enrollment_2026_zhejiang_undergrad.jsonl"

COLUMNS = {
    "tuition_text": "TEXT",
    "single_subject_requirement_text": "TEXT",
    "single_subject_requirement_json": "TEXT",
    "foreign_min_score": "INTEGER",
    "math_min_score": "INTEGER",
    "chinese_min_score": "INTEGER",
}

SUBJECT_LABELS = {
    "foreign": "外语",
    "math": "数学",
    "chinese": "语文",
}

SUBJECT_ALIASES = {
    "外语": "foreign",
    "英语": "foreign",
    "日语": "foreign",
    "语文": "chinese",
    "数学": "math",
}

SCORE_PATTERNS = [
    re.compile(
        r"(?:高考)?(外语|英语|日语|语文|数学)(?:单科|考试)?成绩?"
        r"不(?:得)?低于\s*(\d{2,3})\s*分"
    ),
    re.compile(
        r"(外语|英语|日语|语文|数学)[^，。；;）)]{0,12}?"
        r"不(?:得)?低于\s*(\d{2,3})\s*分"
    ),
]


def clean_text(value: Any) -> str:
    text = str(value or "").strip()
    return re.sub(r"\s+", " ", text.replace("\u3000", " "))


def norm_key(value: Any) -> str:
    text = clean_text(value).replace("（", "(").replace("）", ")")
    return re.sub(r"\s+", "", text)


def parse_tuition_amount(value: Any) -> int | None:
    text = clean_text(value).replace(",", "")
    if not text or text in {"-", "--", "—"}:
        return None
    if "免学费" in text or text == "免费":
        return 0
    nums = [int(x) for x in re.findall(r"\d{1,6}", text)]
    nums = [n for n in nums if 0 <= n <= 300000]
    return max(nums) if nums else None


def extract_tuition_text_from_plan_text(text: str) -> str:
    text = clean_text(text)
    hkd = re.findall(r"\d{4,6}\s*港元", text)
    if hkd:
        # In Hong Kong school rows the program tuition is the largest HKD number;
        # smaller HKD numbers in the same note are dormitory fees.
        return max(hkd, key=lambda x: int(re.search(r"\d+", x).group(0)))  # type: ignore[union-attr]

    split_year = re.search(
        r"[^。；;()（）]{0,16}学费\d{3,6}元/学年[，,、][^。；;()（）]{0,24}学费\d{3,6}元/学年",
        text,
    )
    if split_year:
        return split_year.group(0)

    direct = re.search(r"\d{3,6}\s*元\s*/\s*(?:年|学年)", text)
    return direct.group(0) if direct else ""


def extract_subject_scores(text: str) -> dict[str, int]:
    result: dict[str, int] = {}
    text = clean_text(text)
    for pattern in SCORE_PATTERNS:
        for subject_raw, score_raw in pattern.findall(text):
            subject = SUBJECT_ALIASES.get(subject_raw)
            score = int(score_raw)
            if not subject or not (50 <= score <= 150):
                continue
            result[subject] = max(result.get(subject, 0), score)
    return result


def _norm(value: str) -> str:
    value = re.sub(r"[（(].*?[）)]", "", str(value or ""))
    return re.sub(r"\s+", "", value).strip()


def applicable_profile_scores(major_name: str, profile_json: str | None) -> dict[str, int]:
    if not profile_json:
        return {}
    try:
        profile = json.loads(profile_json)
    except (json.JSONDecodeError, TypeError):
        return {}

    result: dict[str, int] = {}
    for subject, score in (profile.get("school_wide") or {}).items():
        if subject in SUBJECT_LABELS and isinstance(score, int):
            result[subject] = max(result.get(subject, 0), score)

    major_norm = _norm(major_name)
    for item in profile.get("by_pattern") or []:
        pattern = clean_text(item.get("pattern"))
        subject = item.get("subject")
        score = item.get("min")
        if not pattern or subject not in SUBJECT_LABELS or not isinstance(score, int):
            continue
        if pattern in major_name or pattern in major_norm:
            result[subject] = max(result.get(subject, 0), score)
    return result


def format_requirement(scores: dict[str, int]) -> str:
    parts = []
    for subject in ("foreign", "math", "chinese"):
        score = scores.get(subject)
        if score:
            parts.append(f"{SUBJECT_LABELS[subject]}不低于{score}分")
    return "；".join(parts)


def ensure_columns(conn: sqlite3.Connection) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(admission_plan_2026)")}
    for column, definition in COLUMNS.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE admission_plan_2026 ADD COLUMN {column} {definition}")


def load_raw_tuition(jsonl_path: Path) -> dict[tuple[str, str], str]:
    if not jsonl_path.exists():
        return {}

    result: dict[tuple[str, str], str] = {}
    with jsonl_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            school = clean_text(row.get("school_name"))
            major = norm_key(row.get("major_full_name") or f"{row.get('major') or ''}{row.get('major_subtitle') or ''}")
            tuition = clean_text(row.get("tuition"))
            if school and major and tuition:
                result[(school, major)] = tuition
    return result


def backfill(conn: sqlite3.Connection, jsonl_path: Path = DEFAULT_JSONL) -> dict[str, int]:
    ensure_columns(conn)
    raw_tuition = load_raw_tuition(jsonl_path)

    profile_scores = {
        school_name: subject_json
        for school_name, subject_json in conn.execute(
            """
            SELECT school_name, subject_min_scores_json
            FROM school_profile
            WHERE subject_min_scores_json IS NOT NULL
              AND subject_min_scores_json != ''
            """
        )
    }

    rows = conn.execute(
        """
        SELECT id, school_name, major_name, source_major, source_major_subtitle,
               training_note, tuition, tuition_text
        FROM admission_plan_2026
        ORDER BY id
        """
    ).fetchall()

    stats = {
        "total": len(rows),
        "tuition_text": 0,
        "tuition_numeric_changed": 0,
        "single_subject": 0,
        "foreign": 0,
        "math": 0,
        "chinese": 0,
    }

    for row_id, school_name, major_name, source_major, subtitle, training_note, tuition, tuition_text in rows:
        text_parts = [major_name, source_major, subtitle, training_note]
        text_scores = extract_subject_scores(" ".join(clean_text(x) for x in text_parts if x))
        charter_scores = applicable_profile_scores(str(major_name), profile_scores.get(school_name))
        scores = dict(charter_scores)
        for subject, score in text_scores.items():
            scores[subject] = max(scores.get(subject, 0), score)

        raw_key = (clean_text(school_name), norm_key(f"{source_major or ''}{subtitle or ''}"))
        raw_tuition_text = raw_tuition.get(raw_key, "") or clean_text(tuition_text)
        if not raw_tuition_text or raw_tuition_text in {"待定", "-", "--", "—"}:
            from_plan_text = extract_tuition_text_from_plan_text(
                " ".join(clean_text(x) for x in [major_name, source_major, subtitle, training_note] if x)
            )
            if from_plan_text:
                raw_tuition_text = from_plan_text
        if not raw_tuition_text and tuition is not None:
            raw_tuition_text = str(tuition)
        numeric_tuition = parse_tuition_amount(raw_tuition_text)
        if numeric_tuition is None:
            numeric_tuition = tuition

        text = format_requirement(scores)
        json_text = (
            json.dumps(scores, ensure_ascii=False, separators=(",", ":"))
            if scores
            else None
        )

        conn.execute(
            """
            UPDATE admission_plan_2026
            SET tuition_text = ?,
                tuition = ?,
                single_subject_requirement_text = ?,
                single_subject_requirement_json = ?,
                foreign_min_score = ?,
                math_min_score = ?,
                chinese_min_score = ?
            WHERE id = ?
            """,
            (
                raw_tuition_text or None,
                numeric_tuition,
                text or None,
                json_text,
                scores.get("foreign"),
                scores.get("math"),
                scores.get("chinese"),
                row_id,
            ),
        )

        if raw_tuition_text:
            stats["tuition_text"] += 1
        if numeric_tuition != tuition:
            stats["tuition_numeric_changed"] += 1
        if scores:
            stats["single_subject"] += 1
        for subject in ("foreign", "math", "chinese"):
            if scores.get(subject):
                stats[subject] += 1

    conn.commit()
    return stats


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--jsonl", type=Path, default=DEFAULT_JSONL)
    args = parser.parse_args()

    conn = sqlite3.connect(str(args.db))
    try:
        stats = backfill(conn, jsonl_path=args.jsonl)
    finally:
        conn.close()

    print("=== 浙江2026专业表补充元数据 ===")
    for key, value in stats.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
