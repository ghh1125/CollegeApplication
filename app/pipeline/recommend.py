"""End-to-end recommendation assembly helpers."""

from __future__ import annotations

from typing import Any

from app.pipeline.builder import build_volunteer_list
from app.pipeline.rank import calculate_gap, enrich_with_history, sort_candidates


def expand_major_keywords(keywords: list[str], conn: Any) -> set[str]:
    """
    Return all standard major names whose description text contains any keyword.

    Searches name, is_what, learn_what, do_what, keywords fields.
    Used to expand a user-entered shorthand like "计算机" into
    {"计算机科学与技术", "软件工程", "网络工程", ...}.
    """
    if not keywords:
        return set()
    matched: set[str] = set()
    for kw in keywords:
        if not kw:
            continue
        like = f"%{kw}%"
        rows = conn.execute(
            """
            SELECT name FROM major_description
            WHERE name     LIKE ?
               OR keywords LIKE ?
            """,
            (like, like),
        ).fetchall()
        matched.update(r[0] for r in rows)
    return matched


HISTORY_RANK_YEARS = (2025, 2024, 2023)


def history_rank_columns(
    program: dict,
    years: tuple[int, ...] = HISTORY_RANK_YEARS,
) -> dict[str, str]:
    """Return fixed historical-rank columns, leaving missing years blank."""

    rank_by_year = {
        int(item["year"]): item.get("min_rank")
        for item in program.get("history", [])
        if item.get("year")
    }
    return {
        f"{year}位次": str(rank_by_year[year]) if rank_by_year.get(year) else ""
        for year in years
    }


def build_recommendations(
    candidates: list[dict],
    profile: Any,
    main_priority: str,
    preferred_majors: list[str],
    preferred_categories: list[str],
    preferred_schools: list[str],
    preferred_cities: list[str] | None = None,
    risk_preference: str | None = None,
    year: int = 2025,
    total: int = 80,
    conn: Any | None = None,
) -> dict:
    """Build final volunteers from a filtered candidate pool."""

    from app.db import get_conn

    def _run(db_conn: Any) -> dict:
        # Expand keyword shorthand → matched standard major names via description text
        expanded_major_names = expand_major_keywords(preferred_majors, db_conn)

        enriched = enrich_with_history(candidates, year=year, conn=db_conn)
        for program in enriched:
            program["gap_info"] = calculate_gap(profile.rank, program["history"])

        sorted_candidates = sort_candidates(
            enriched,
            main_priority=main_priority,
            preferred_majors=preferred_majors,
            preferred_categories=preferred_categories,
            preferred_schools=preferred_schools,
            preferred_cities=preferred_cities,
            expanded_major_names=expanded_major_names,
        )

        return build_volunteer_list(
            sorted_candidates,
            risk_preference=risk_preference or profile.risk_preference,
            total=total,
        )

    if conn is not None:
        return _run(conn)
    with get_conn() as managed_conn:
        return _run(managed_conn)

