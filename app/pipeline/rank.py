"""Candidate ranking stage."""

from __future__ import annotations

import re
from typing import Any


YEAR_WEIGHTS = {2025: 0.5, 2024: 0.3, 2023: 0.2}

DEFAULT_PREFERRED_CITIES = ["北京", "上海", "广州", "深圳", "杭州", "南京", "宁波", "苏州"]

SCHOOL_QUALITY_SCORE = {
    "is_985": 90,
    "is_211": 80,
    "is_double_first_class": 80,
}

TIER_ORDER = ["冲", "稳", "保", "高危冲", "数据不足"]


def calculate_gap(student_rank: int, history: list[dict]) -> dict:
    """
    Calculate rank gap against weighted historical minimum ranks.

    history 格式：[{"year": 2025, "min_rank": 34200}, ...]
    """

    valid = [
        (int(h["year"]), int(h["min_rank"]))
        for h in history
        if h.get("year") in YEAR_WEIGHTS and h.get("min_rank")
    ]

    if not valid:
        return {
            "weighted_avg": None,
            "gap": None,
            "ratio": None,
            "tier": "数据不足",
            "data_years": 0,
        }

    total_w = sum(YEAR_WEIGHTS[year] for year, _rank in valid)
    weighted_avg = sum(YEAR_WEIGHTS[year] * rank / total_w for year, rank in valid)
    weighted_avg = round(weighted_avg)

    gap = weighted_avg - student_rank
    ratio = gap / weighted_avg

    if ratio > 0.15:
        tier = "保"
    elif 0 < ratio <= 0.15:
        tier = "稳"
    elif -0.15 <= ratio <= 0:
        tier = "冲"
    else:
        tier = "高危冲"

    return {
        "weighted_avg": weighted_avg,
        "gap": gap,
        "ratio": round(ratio, 4),
        "tier": tier,
        "data_years": len(valid),
    }


def normalize_major_name(name: str | None) -> str:
    """Normalize a major name for category lookup and preference matching."""

    text = re.sub(r"\s+", "", str(name or "").strip())
    text = text.replace("（", "(").replace("）", ")")
    return re.sub(r"\([^)]*\)", "", text)


def get_major_score(
    program: dict,
    preferred_majors: list,
    preferred_categories: list,
    expanded_major_names: set | None = None,
) -> int:
    """Score a program by major preference."""

    name = program.get("normalized_major_name", "")
    raw_name = program.get("major_name", "") or ""
    category = program.get("major_category", "")
    # Exact match
    if name in preferred_majors or raw_name in preferred_majors:
        return 100
    # Description-expanded match (e.g. "计算机" expands to {"计算机科学与技术", ...})
    if expanded_major_names and (name in expanded_major_names or raw_name in expanded_major_names):
        return 95
    # Keyword substring match
    if any(kw and (kw in name or kw in raw_name) for kw in preferred_majors):
        return 90
    if category in preferred_categories:
        return 85
    return 40


def get_school_score(program: dict, preferred_schools: list) -> int:
    """Score a program by school preference and school level."""

    if program.get("school_name") in preferred_schools:
        return 100
    if program.get("is_985"):
        return 90
    if program.get("is_211") or program.get("is_double_first_class"):
        return 80
    return 55


def sort_candidates(
    candidates: list[dict],
    main_priority: str,
    city_first: bool,
    preferred_majors: list,
    preferred_categories: list,
    preferred_schools: list,
    preferred_cities: list | None = None,
    expanded_major_names: set | None = None,
) -> list[dict]:
    """
    Sort candidates within each risk tier, then concatenate tiers.

    This keeps the tier boundary intact: a high-scoring “稳” program never jumps
    ahead of a “冲” program.
    """

    if preferred_cities is None:
        preferred_cities = DEFAULT_PREFERRED_CITIES

    groups = {tier: [] for tier in TIER_ORDER}
    for program in candidates:
        tier = program.get("gap_info", {}).get("tier", "数据不足")
        groups.setdefault(tier, []).append(program)

    def sort_key(program: dict) -> tuple:
        in_city = 1 if program.get("school_city") in preferred_cities else 0
        if main_priority == "专业优先":
            main_score = get_major_score(program, preferred_majors, preferred_categories, expanded_major_names)
        else:
            main_score = get_school_score(program, preferred_schools)
        # Lower ruanke_rank number = better school; unranked schools sort last
        ruanke = program.get("ruanke_rank")
        ruanke_score = -ruanke if ruanke else -99999

        # main_score is always primary; city is a tiebreaker (not override)
        if city_first:
            return (main_score, in_city, ruanke_score)
        return (main_score, ruanke_score)

    result: list[dict] = []
    for tier in TIER_ORDER:
        result.extend(sorted(groups.get(tier, []), key=sort_key, reverse=True))
    return result


def enrich_with_history(
    candidates: list[dict],
    year: int = 2025,
    conn: Any | None = None,
) -> list[dict]:
    """
    Attach historical cutoff rows and sorting metadata to candidate programs.

    Matching first uses school_code + major_code, then falls back to
    school_name + normalized major name for programs whose code changed.
    """

    from app.db import get_conn

    if conn is not None:
        return _enrich_with_history(candidates, year, conn)

    with get_conn() as managed_conn:
        return _enrich_with_history(candidates, year, managed_conn)


def _enrich_with_history(candidates: list[dict], year: int, conn: Any) -> list[dict]:
    from app.pipeline.filter import SCHOOL_LEVEL_MAP

    history_by_code, history_by_name = _load_history_indexes(conn, year)
    location_by_school = _load_school_locations(conn)
    major_category_by_name = _load_major_categories(conn)

    enriched: list[dict] = []
    for program in candidates:
        item = dict(program)
        school_code = str(item.get("school_code") or "")
        major_code = str(item.get("major_code") or "")
        school_name = str(item.get("school_name") or "")
        normalized_major_name = item.get("normalized_major_name") or normalize_major_name(
            item.get("major_name")
        )

        item["normalized_major_name"] = normalized_major_name
        item["major_category"] = item.get("major_category") or major_category_by_name.get(
            normalized_major_name,
            "",
        )

        item["history"] = history_by_code.get(
            (school_code, major_code),
            history_by_name.get((school_name, normalized_major_name), []),
        )

        province, city, ruanke_rank = location_by_school.get(school_name, ("", "", None))
        item["school_province"] = item.get("school_province") or province
        item["school_city"] = item.get("school_city") or city
        if item.get("ruanke_rank") is None:
            item["ruanke_rank"] = ruanke_rank

        item["is_985"] = item.get("is_985", school_name in SCHOOL_LEVEL_MAP["985"])
        item["is_211"] = item.get("is_211", school_name in SCHOOL_LEVEL_MAP["211"])
        item["is_double_first_class"] = item.get(
            "is_double_first_class",
            school_name in SCHOOL_LEVEL_MAP["双一流"],
        )
        enriched.append(item)

    return enriched


def _load_history_indexes(
    conn: Any,
    year: int,
) -> tuple[dict[tuple[str, str], list[dict]], dict[tuple[str, str], list[dict]]]:
    years = [history_year for history_year in YEAR_WEIGHTS if history_year <= year]
    placeholders = ", ".join("?" for _ in years)
    sql = f"""
        SELECT year, school_code, school_name, major_code, major_name,
               min_score, min_rank, plan_count
        FROM historical_cutoff
        WHERE year IN ({placeholders})
        ORDER BY year DESC
    """
    rows = conn.execute(sql, tuple(years)).fetchall()

    by_code: dict[tuple[str, str], list[dict]] = {}
    by_name: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        record = {
            "year": row[0],
            "min_rank": row[6],
            "min_score": row[5],
            "plan_count": row[7],
        }
        by_code.setdefault((str(row[1] or ""), str(row[3] or "")), []).append(record)
        by_name.setdefault((str(row[2] or ""), normalize_major_name(row[4])), []).append(record)
    return by_code, by_name


def _load_school_locations(conn: Any) -> dict[str, tuple[str, str, int | None]]:
    rows = conn.execute(
        "SELECT school_name, province, city, ruanke_rank FROM school_master"
    ).fetchall()
    return {
        str(name): (str(province or ""), str(city or ""), rank)
        for name, province, city, rank in rows
        if name
    }


def _load_major_categories(conn: Any) -> dict[str, str]:
    rows = conn.execute(
        """
        SELECT normalized_major_name, major_category
        FROM major_subject_requirement
        WHERE major_category IS NOT NULL AND major_category != ''
        """
    ).fetchall()
    return {
        normalize_major_name(name): str(category or "")
        for name, category in rows
        if name and category
    }
