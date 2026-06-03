"""Jiangsu filtering: subject eligibility (3+1+2) + hard constraints.

Subject logic differs from Zhejiang:
  - 首选科目 (物理/历史) selects the rank pool → handled by querying one
    subject_category; every loaded row already matches the student's 首选.
  - 再选科目 requirement (stored as subject_requirement_json by ingest):
      {"first_choice": "物理", "reselect_type": "NONE|ALL|ANY", "reselect": [...]}
    checked against the student's 2 再选 subjects.

National reference data (school levels, geography) is reused from src.common.reference.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from src.common.reference import (
    CITY_TO_PROVINCE,
    PROVINCE_NAMES,
    REGION_PROVINCES,
    SCHOOL_LEVEL_MAP,
)


@lru_cache(maxsize=1)
def _load_school_location_map() -> dict[str, tuple[str, str]]:
    """school_name → (province, city) from school_master (copied from common.db)."""
    try:
        from db import get_conn, get_cursor
        with get_conn("jiangsu") as conn:
            with get_cursor(conn) as cur:
                cur.execute("SELECT school_name, province, city FROM school_master")
                return {r[0]: (r[1] or "", r[2] or "") for r in cur.fetchall() if r[0]}
    except Exception:
        return {}


def resolve_school_city(school_name: str) -> str:
    loc = _load_school_location_map()
    return loc.get(school_name, ("", ""))[1] if school_name in loc else ""


def _resolve_school_province(program: dict[str, Any]) -> str | None:
    name = (program.get("school_name") or "").strip()
    loc = _load_school_location_map()
    if name in loc and loc[name][0]:
        return loc[name][0]
    location = (program.get("school_location") or "").strip()
    for prov in PROVINCE_NAMES:
        if prov in location or name.startswith(prov):
            return prov
    for city, prov in CITY_TO_PROVINCE.items():
        if city in location or name.startswith(city):
            return prov
    return None


def _in_excluded_region(program: dict[str, Any], profile: Any) -> bool:
    city_pref = profile.preferences.cities
    school_prov = _resolve_school_province(program)
    if school_prov is None:
        return False
    if not city_pref.accept_outside_jiangsu and school_prov != "江苏":
        return True
    for region in city_pref.excluded_regions:
        if school_prov in REGION_PROVINCES.get(region, [region]):
            return True
    return False


def _is_sino_foreign(p: dict) -> bool:
    text = (p.get("major_name") or "") + (p.get("sg_info") or "")
    return "中外合作" in text or "合作办学" in text


def _is_private(p: dict) -> bool:
    return "民办" in (p.get("school_name") or "")


# ─── subject (再选) matching ───────────────────────────────────────────────────

def _reselect_match(req_json: str | None, reselect: frozenset[str]) -> tuple[bool, str]:
    """Check student's 2 再选 subjects against a group's 再选 requirement."""
    if not req_json:
        return True, ""
    try:
        req = json.loads(req_json)
    except (json.JSONDecodeError, TypeError):
        return True, ""
    rtype = req.get("reselect_type", "NONE")
    subjects = req.get("reselect", [])
    if rtype == "NONE" or not subjects:
        return True, ""
    if rtype == "ALL":
        missing = [s for s in subjects if s not in reselect]
        return (False, f"再选缺少：{'、'.join(missing)}") if missing else (True, "")
    if rtype == "ANY":
        if any(s in reselect for s in subjects):
            return True, ""
        return False, f"再选需至少一门：{'、'.join(subjects)}"
    return True, ""


# ─── public API ────────────────────────────────────────────────────────────────

def load_admission_plans(conn: Any, year: int, subject_category: str) -> list[dict]:
    """Load admission_plan rows for one year + subject_category (物理类/历史类)."""
    from db import get_cursor
    sql = """
        SELECT id, school_code, school_name, special_group, sg_name, sg_info,
               major_code, major_name, plan_count, subject_requirement,
               subject_requirement_json, tuition, duration, subject_category
        FROM admission_plan
        WHERE year = ? AND subject_category = ?
    """
    with get_cursor(conn) as cur:
        cur.execute(sql, (year, subject_category))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def apply_subject_filter(
    programs: list[dict],
    reselect_subjects: list[str],
) -> tuple[list[dict], list[dict]]:
    """Keep programs whose 再选 requirement the student satisfies. Pure (no DB)."""
    reselect = frozenset(reselect_subjects)
    eligible: list[dict] = []
    excluded: list[dict] = []
    for row in programs:
        row = dict(row)
        row["_warnings"] = []
        ok, reason = _reselect_match(row.get("subject_requirement_json"), reselect)
        if ok:
            eligible.append(row)
        else:
            excluded.append({"program": row, "reason": "再选不符", "detail": reason})
    return eligible, excluded


def filter_by_subject(profile: Any, year: int, conn: Any | None = None) -> tuple[list[dict], list[dict]]:
    """Load the student's 首选科类 pool and apply 再选 filter."""
    from db import get_conn

    def _run(c: Any) -> tuple[list[dict], list[dict]]:
        programs = load_admission_plans(c, year, profile.subject_category)
        return apply_subject_filter(programs, profile.selected_subjects)

    if conn is not None:
        return _run(conn)
    with get_conn("jiangsu") as managed:
        return _run(managed)


def filter_by_constraints(programs: list[dict], profile: Any) -> tuple[list[dict], list[dict]]:
    """Apply hard constraints (same shape as Zhejiang, 江苏 region rule)."""
    checks = [
        (lambda p: not profile.constraints.accept_private and _is_private(p),
         "不接受民办", lambda p: f"学校含'民办'：{p.get('school_name')}"),
        (lambda p: not profile.constraints.accept_sino_foreign and _is_sino_foreign(p),
         "不接受中外合作", lambda p: f"含'中外合作'：{p.get('major_name')}"),
        (lambda p: bool(profile.constraints.max_tuition and p.get("tuition")
                        and p["tuition"] > profile.constraints.max_tuition),
         "学费超预算", lambda p: f"学费 {p.get('tuition')} > {profile.constraints.max_tuition}"),
        (lambda p: p.get("school_name") in profile.preferences.schools.excluded_schools,
         "排除学校", lambda p: f"在排除列表：{p.get('school_name')}"),
        (lambda p: any(ex in (p.get("major_name") or "")
                       for ex in profile.preferences.majors.excluded_majors),
         "排除专业", lambda p: f"在排除列表：{p.get('major_name')}"),
        (lambda p: _in_excluded_region(p, profile),
         "排除地区", lambda p: "学校所在省份在排除地区"),
    ]
    final: list[dict] = []
    excluded: list[dict] = []
    for program in programs:
        reason = detail = None
        for pred, rsn, dfn in checks:
            if pred(program):
                reason, detail = rsn, dfn(program)
                break
        (excluded.append({"program": program, "reason": reason, "detail": detail})
         if reason else final.append(program))
    return final, excluded


def filter_by_school_level(programs: list[dict], levels: list[str]) -> tuple[list[dict], int]:
    if not levels:
        return programs, 0
    allowed: set[str] = set()
    for lv in levels:
        allowed |= SCHOOL_LEVEL_MAP.get(lv, frozenset())
    kept = [p for p in programs if p.get("school_name") in allowed]
    return kept, len(programs) - len(kept)


def filter_by_city(programs: list[dict], cities: list[str]) -> tuple[list[dict], int]:
    if not cities:
        return programs, 0
    city_set = set(cities)
    loc = _load_school_location_map()

    def _match(p: dict) -> bool:
        name = p.get("school_name") or ""
        db_city = loc.get(name, ("", ""))[1]
        return (db_city in city_set) if db_city else any(c in name for c in city_set)

    kept = [p for p in programs if _match(p)]
    return kept, len(programs) - len(kept)
