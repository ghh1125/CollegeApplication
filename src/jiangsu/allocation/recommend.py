"""Jiangsu end-to-end recommendation: rank per-major, aggregate into 院校专业组.

Jiangsu fills 院校专业组 (not single majors). Pipeline:
  1. enrich each candidate 专业 with history (录取位次) via common.attach_history,
     filtered to the student's 首选科类 pool (物理类/历史类) and 江苏 year weights.
  2. calculate_gap per 专业 (reuse common).
  3. aggregate 专业 → 专业组 (group by school + special_group):
       - 组投档位次 = 组内成员最低门槛（max rank number = easiest member to enter）
       - 组的专业匹配度 = 组内最匹配专业的等级（best member）
       - 组内列出成员专业及各自位次/匹配标签
  4. sort 专业组 (reuse common.sort_candidates, treating each group as a program)
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
from src.jiangsu.config import JIANGSU_YEAR_WEIGHTS

HISTORY_RANK_YEARS = (2025, 2024, 2023)


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
        # 组投档位次 = 成员中最低门槛（最大位次数 = 最容易进的专业决定进组线）
        member_ranks = [
            int(h["min_rank"])
            for m in members for h in m.get("history", [])
            if h.get("year") in JIANGSU_YEAR_WEIGHTS and h.get("min_rank")
        ]
        # 组的 gap：以组内"最容易进"的成员的加权均值位次为门槛
        # 取每个成员的 weighted_avg，组门槛 = max（最容易）
        member_avgs = [
            m["gap_info"]["weighted_avg"]
            for m in members
            if m.get("gap_info", {}).get("weighted_avg") is not None
        ]
        rep = max(members, key=lambda m: _major_level(
            m, preferred_majors, preferred_categories, expanded_major_names))
        best_level = _major_level(rep, preferred_majors, preferred_categories, expanded_major_names)

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
            "_members": members,
            "_member_count": len(members),
        }
        # 组门槛 gap：用成员最大 weighted_avg（最容易进的）作为进组位次
        if member_avgs:
            threshold = max(member_avgs)
            # 复刻 calculate_gap 的分档（用组门槛位次直接算 ratio）
            gap = threshold - student_rank
            ratio = gap / threshold
            if ratio > 0.40:
                tier = "垫"
            elif ratio > 0.15:
                tier = "保"
            elif 0 < ratio <= 0.15:
                tier = "稳"
            elif -0.15 <= ratio <= 0:
                tier = "冲"
            else:
                tier = "高危冲"
            group["gap_info"] = {
                "weighted_avg": threshold, "gap": gap, "ratio": round(ratio, 4),
                "tier": tier,
                "data_years": max((m["gap_info"].get("data_years", 0) for m in members), default=0),
            }
        else:
            group["gap_info"] = {"weighted_avg": None, "gap": None, "ratio": None,
                                 "tier": "数据不足", "data_years": 0}
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

    def _run(db_conn: Any) -> dict:
        expanded = expand_major_keywords(preferred_majors, db_conn)

        # 1. enrich members with history (filtered to 首选科类 pool + 江苏 years)
        data = load_all_history_data(
            db_conn, year,
            year_weights=JIANGSU_YEAR_WEIGHTS,
            subject_category=profile.subject_category,
        )
        enriched = attach_history(candidates, data)
        enriched = enrich_with_profiles(enriched, db_conn)
        for prog in enriched:
            prog["gap_info"] = calculate_gap(
                profile.rank, prog["history"], year_weights=JIANGSU_YEAR_WEIGHTS)

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
