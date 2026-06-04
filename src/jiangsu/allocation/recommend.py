"""Jiangsu end-to-end recommendation: rank 院校专业组.

Jiangsu fills 院校专业组 (not single majors). Pipeline:
  1. enrich candidate rows with school/profile metadata via common.attach_history.
  2. override history by official group key (school_code + special_group).
  3. aggregate rows into 院校专业组 and calculate risk from group-level history.
  4. sort 专业组 (reuse common.sort_candidates, treating each group as a program).
  5. build 40-slot 志愿表 (reuse common.build_volunteer_list).
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from src.common.allocation.builder import build_volunteer_list
from src.common.ranking.profiles import enrich_with_profiles
from src.common.ranking.rank import (
    _major_level,
    attach_history,
    build_sort_reason,
    calculate_gap,
    load_all_history_data,
    major_tag_label,
    sort_candidates,
)
from src.jiangsu.config import JIANGSU_YEAR_WEIGHTS, PROVINCE_CONFIG

HISTORY_RANK_YEARS = (2025, 2024, 2023)


def _parse_int(value: Any) -> int | None:
    """Parse DB numeric values that may arrive as strings."""
    try:
        text = str(value).strip().replace(",", "")
        return int(float(text)) if text and text not in ("-", "--", "—", "None") else None
    except (TypeError, ValueError):
        return None


def load_group_history(
    conn: Any,
    year: int,
    subject_category: str,
) -> dict[tuple[str, str], list[dict]]:
    """Load Jiangsu group-level history keyed by (school_code, special_group).

    Official Jiangsu data is 院校专业组-level. Some group-member major rows may
    also exist for 2025 after parsing public plan details; those rows carry the
    same group threshold. For each group/year, keep the row with the largest
    min_rank (the group entrance threshold); if tied, prefer the synthetic
    official row major_code='__GROUP__'.
    """
    years = [y for y in JIANGSU_YEAR_WEIGHTS if y <= year]
    if not years:
        return {}

    placeholders = ", ".join("?" for _ in years)
    rows = conn.execute(
        f"""
        SELECT year, school_code, special_group, min_score, min_rank, plan_count, major_code
        FROM historical_cutoff
        WHERE year IN ({placeholders})
          AND subject_category = ?
          AND special_group IS NOT NULL
          AND special_group != ''
        """,
        (*years, subject_category),
    ).fetchall()

    best: dict[tuple[str, str, int], tuple] = {}
    for row in rows:
        row_year, school_code, special_group, _score, min_rank, _plan_count, major_code = row
        parsed_year = _parse_int(row_year)
        if parsed_year is None:
            continue
        key = (str(school_code or ""), str(special_group or ""), parsed_year)
        rank = _parse_int(min_rank)
        score = (rank is not None, rank or -1, str(major_code or "") == "__GROUP__")
        old = best.get(key)
        if old is None:
            best[key] = row
            continue
        old_rank = _parse_int(old[4])
        old_score = (old_rank is not None, old_rank or -1, str(old[6] or "") == "__GROUP__")
        if score > old_score:
            best[key] = row

    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in best.values():
        row_year, school_code, special_group, min_score, min_rank, plan_count, _major_code = row
        grouped[(str(school_code or ""), str(special_group or ""))].append({
            "year": _parse_int(row_year),
            "min_rank": _parse_int(min_rank),
            "min_score": _parse_int(min_score),
            "plan_count": _parse_int(plan_count),
        })

    return {
        key: sorted(records, key=lambda r: int(r["year"] or 0), reverse=True)
        for key, records in grouped.items()
    }


def _attach_group_history(
    programs: list[dict],
    group_history: dict[tuple[str, str], list[dict]],
    student_rank: int,
) -> list[dict]:
    """Attach group-level history to each member row and calculate gap."""
    enriched: list[dict] = []
    for program in programs:
        item = dict(program)
        key = (str(item.get("school_code") or ""), str(item.get("special_group") or ""))
        if key in group_history:
            item["history"] = [dict(record) for record in group_history[key]]
        else:
            item["history"] = item.get("history", [])
        # 江苏专业组逐年重新编号，同一组号跨年不是同一组（如西交03组：2025位次3556、
        # 2024位次12577），跨年加权会把不可比的组混在一起、算错冲稳保。
        # 因此只用「最新一年」的官方投档位次定档（history 已按年份降序）。
        _latest = next((h for h in item["history"] if h.get("min_rank")), None)
        item["gap_info"] = calculate_gap(
            student_rank, [_latest] if _latest else [], year_weights=JIANGSU_YEAR_WEIGHTS
        )
        enriched.append(item)
    return enriched


def expand_major_keywords(keywords: list[str], conn: Any) -> set[str]:
    """Expand a shorthand like 计算机 → standard major names via major_description."""
    if not keywords:
        return set()
    matched: set[str] = set()
    for kw in keywords:
        if not kw:
            continue
        rows = conn.execute(
            "SELECT name FROM major_description WHERE name LIKE ?", (f"%{kw}%",)
        ).fetchall()
        matched.update(r[0] for r in rows)
    return matched


def history_rank_columns(group: dict, years: tuple[int, ...] = HISTORY_RANK_YEARS) -> dict[str, str]:
    """Group-level historical 投档位次 by year (max member rank per year = 组门槛)."""
    by_year: dict[int, int] = {}
    for h in group.get("history", []):
        y, r = h.get("year"), h.get("min_rank")
        if y and r:
            by_year[int(y)] = int(r)
    for member in group.get("_members", []):
        for h in member.get("history", []):
            y, r = h.get("year"), h.get("min_rank")
            if y and r:
                by_year[int(y)] = max(by_year.get(int(y), 0), int(r))
    return {f"{y}位次": str(by_year[y]) if by_year.get(y) else "" for y in years}


def _aggregate_groups(
    enriched: list[dict],
    student_rank: int,
    preferred_majors: list[str],
    preferred_categories: list[str],
    expanded_major_names: set[str],
) -> list[dict]:
    """Group enriched 专业 rows into 院校专业组 candidate dicts."""
    buckets: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for prog in enriched:
        key = (str(prog.get("school_code") or ""), str(prog.get("special_group") or ""))
        buckets[key].append(prog)

    groups: list[dict] = []
    for (school_code, sgid), members in buckets.items():
        rep = max(members, key=lambda m: _major_level(
            m, preferred_majors, preferred_categories, expanded_major_names))
        group_history = [dict(record) for record in members[0].get("history", [])]

        group = {
            "school_code": school_code,
            "school_name": rep.get("school_name", ""),
            "school_city": rep.get("school_city", ""),
            "school_province": rep.get("school_province", ""),
            "ruanke_rank": rep.get("ruanke_rank"),
            "discipline_grade": rep.get("discipline_grade", ""),
            "school_best_grade": rep.get("school_best_grade", ""),
            "is_985": rep.get("is_985"), "is_211": rep.get("is_211"),
            "is_double_first_class": rep.get("is_double_first_class"),
            # 代表专业字段，供 sort_candidates 的 _major_level/_city_key 使用
            "major_name": rep.get("major_name", ""),
            "normalized_major_name": rep.get("normalized_major_name", ""),
            "major_category": rep.get("major_category", ""),
            "special_group": sgid,
            "sg_name": rep.get("sg_name", ""),
            "sg_info": rep.get("sg_info", ""),
            "subject_requirement_json": rep.get("subject_requirement_json"),
            "history": group_history,
            "_members": members,
            "_member_count": len(members),
        }
        # 江苏专业组逐年重新编号，同一组号跨年不可比（如西交03组 2025位次3556、
        # 2024位次12577），跨年加权会算错冲稳保。仅用最新一年官方投档位次定档。
        _latest = next((h for h in group_history if h.get("min_rank")), None)
        group["gap_info"] = calculate_gap(
            student_rank, [_latest] if _latest else [], year_weights=JIANGSU_YEAR_WEIGHTS
        )
        groups.append(group)
    return groups


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
    total: int | None = None,
    risk_allocation: dict | None = None,
    conn: Any | None = None,
) -> dict:
    """Build 江苏 40-院校专业组 recommendations from a filtered 专业 pool."""
    from db import get_conn

    if total is None:
        total = PROVINCE_CONFIG.total_volunteers
    if risk_allocation is None:
        risk_allocation = PROVINCE_CONFIG.risk_allocation
    if preferred_cities is None:
        preferred_cities = []

    def _run(db_conn: Any) -> dict:
        expanded = expand_major_keywords(preferred_majors, db_conn)

        # 1. enrich members with common metadata, then override history with
        #    Jiangsu's official 院校专业组-level history.
        data = load_all_history_data(
            db_conn, year,
            year_weights=JIANGSU_YEAR_WEIGHTS,
            subject_category=profile.subject_category,
        )
        enriched = attach_history(candidates, data)
        enriched = enrich_with_profiles(enriched, db_conn)
        group_history = load_group_history(db_conn, year, profile.subject_category)
        enriched = _attach_group_history(enriched, group_history, profile.rank)

        # 2. aggregate 专业 → 专业组
        groups = _aggregate_groups(
            enriched, profile.rank, preferred_majors, preferred_categories, expanded)

        # 3. sort groups (reuse common, each group looks like a program)
        sorted_groups = sort_candidates(
            groups, main_priority=main_priority,
            preferred_majors=preferred_majors, preferred_categories=preferred_categories,
            preferred_schools=preferred_schools, preferred_cities=preferred_cities,
            expanded_major_names=expanded,
        )

        # 4. allocate 40 slots
        result = build_volunteer_list(
            sorted_groups,
            risk_preference=risk_preference or profile.risk_preference,
            total=total, risk_allocation=risk_allocation,
        )

        # 5. annotate each group + tag member majors
        for coll in ("volunteers", "reserve"):
            for group in result.get(coll, []):
                group["sort_reason"] = build_sort_reason(
                    group, main_priority=main_priority,
                    preferred_majors=preferred_majors, preferred_categories=preferred_categories,
                    preferred_cities=preferred_cities, preferred_schools=preferred_schools,
                    expanded_major_names=expanded,
                )
                group["_major_tag"] = major_tag_label(
                    group, preferred_majors=preferred_majors,
                    preferred_categories=preferred_categories, expanded_major_names=expanded)
                for m in group.get("_members", []):
                    m["_major_tag"] = major_tag_label(
                        m, preferred_majors=preferred_majors,
                        preferred_categories=preferred_categories, expanded_major_names=expanded)
        return result

    if conn is not None:
        return _run(conn)
    with get_conn("jiangsu") as managed:
        return _run(managed)
