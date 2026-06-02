"""Candidate filtering stage: subject eligibility + hard constraints."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Any

REGION_PROVINCES: dict[str, list[str]] = {
    "东北": ["辽宁", "吉林", "黑龙江"],
    "西北": ["陕西", "甘肃", "青海", "宁夏", "新疆"],
    "西南": ["四川", "贵州", "云南", "西藏", "重庆"],
    "华北": ["北京", "天津", "河北", "山西", "内蒙古"],
    "华东": ["上海", "江苏", "浙江", "安徽", "福建", "江西", "山东"],
    "华中": ["河南", "湖北", "湖南"],
    "华南": ["广东", "广西", "海南"],
}

# Province names that appear verbatim in school names
_PROVINCE_NAMES: frozenset[str] = frozenset({
    "北京", "天津", "上海", "重庆",
    "浙江", "江苏", "广东", "四川", "湖北", "湖南", "河南", "山东",
    "陕西", "安徽", "福建", "辽宁", "吉林", "黑龙江", "内蒙古",
    "云南", "贵州", "甘肃", "青海", "新疆", "西藏", "宁夏",
    "江西", "河北", "山西", "海南", "广西",
})

# City names appearing in school names → province
_CITY_TO_PROVINCE: dict[str, str] = {
    "武汉": "湖北", "南京": "江苏", "苏州": "江苏", "无锡": "江苏",
    "南通": "江苏", "扬州": "江苏", "徐州": "江苏", "镇江": "江苏",
    "杭州": "浙江", "宁波": "浙江", "温州": "浙江", "绍兴": "浙江",
    "嘉兴": "浙江", "湖州": "浙江", "台州": "浙江",
    "广州": "广东", "深圳": "广东", "珠海": "广东", "佛山": "广东",
    "成都": "四川", "长沙": "湖南", "郑州": "河南", "济南": "山东",
    "西安": "陕西", "合肥": "安徽", "厦门": "福建", "福州": "福建",
    "南昌": "江西", "哈尔滨": "黑龙江", "沈阳": "辽宁", "大连": "辽宁",
    "长春": "吉林", "贵阳": "贵州", "昆明": "云南", "南宁": "广西",
    "兰州": "甘肃", "呼和浩特": "内蒙古", "太原": "山西", "石家庄": "河北",
    "海口": "海南", "银川": "宁夏", "西宁": "青海", "乌鲁木齐": "新疆",
    "青岛": "山东", "烟台": "山东", "南昌": "江西", "桂林": "广西",
}


@lru_cache(maxsize=1)
def _load_school_location_map() -> dict[str, tuple[str, str]]:
    """Load school_name → (province, city) from school_master. Cached once."""
    try:
        from app.db import get_conn, get_cursor
        with get_conn() as conn:
            with get_cursor(conn) as cur:
                cur.execute("SELECT school_name, province, city FROM school_master")
                return {
                    row[0]: (row[1] or "", row[2] or "")
                    for row in cur.fetchall()
                    if row[0]
                }
    except Exception:
        return {}


def _resolve_school_province(program: dict[str, Any]) -> str | None:
    """
    Determine which province a school is in.

    Priority:
      1. school_master DB lookup (accurate, covers 100% of admission_plan)
      2. school_location column keyword match (heuristic fallback)
      3. school_name keyword match (heuristic fallback)
      4. None – don't filter on unknown location
    """
    school_name = (program.get("school_name") or "").strip()

    # 1. DB lookup
    loc_map = _load_school_location_map()
    if school_name in loc_map:
        prov, _ = loc_map[school_name]
        if prov:
            return prov

    # 2. school_location heuristic
    location = (program.get("school_location") or "").strip()
    if location:
        for prov in _PROVINCE_NAMES:
            if prov in location:
                return prov
        for city, prov in _CITY_TO_PROVINCE.items():
            if city in location:
                return prov

    # 3. school_name keyword heuristic
    for prov in _PROVINCE_NAMES:
        if school_name.startswith(prov) or f"({prov}" in school_name or f"（{prov}" in school_name:
            return prov
    for city, prov in _CITY_TO_PROVINCE.items():
        if school_name.startswith(city):
            return prov

    return None


def resolve_school_city(school_name: str) -> str:
    """Return the city for a school (empty string if unknown)."""
    loc_map = _load_school_location_map()
    if school_name in loc_map:
        return loc_map[school_name][1]
    return ""


def _in_excluded_region(program: dict[str, Any], profile: Any) -> bool:
    """Return True if the school is in a region the student has excluded."""
    city_pref = profile.preferences.cities
    excluded_regions: list[str] = city_pref.excluded_regions

    school_prov = _resolve_school_province(program)
    if school_prov is None:
        # Unknown location – don't exclude (conservative)
        return False

    if not city_pref.accept_outside_zhejiang and school_prov != "浙江":
        return True

    for region in excluded_regions:
        provinces_in_region = REGION_PROVINCES.get(region, [region])
        if school_prov in provinces_in_region:
            return True

    return False


def _is_sino_foreign(program: dict[str, Any]) -> bool:
    """Detect sino-foreign cooperative programs from major_name."""
    major_name = program.get("major_name") or ""
    return "中外合作" in major_name


def _is_private(program: dict[str, Any]) -> bool:
    """
    Best-effort: detect private (民办) schools.
    Checks school_name for the '民办' marker.
    Falls back to False when unknown (don't over-filter).
    """
    school_name = program.get("school_name") or ""
    return "民办" in school_name


# ─── subject matching ────────────────────────────────────────────────────────

def _subject_match(
    req_json: str | None,
    selected: frozenset[str],
) -> tuple[bool, str]:
    """
    Check whether a student's selected subjects satisfy a requirement.

    Returns (eligible, reason_if_not).
    Uses the exact requirement stored per row in admission_plan –
    no major-name normalisation is done here.
    """
    if not req_json:
        return True, ""
    try:
        req = json.loads(req_json)
    except (json.JSONDecodeError, TypeError):
        return True, ""

    req_type: str = req.get("type", "NONE")
    req_subjects: list[str] = req.get("subjects", [])

    if req_type in ("NONE", "UNKNOWN"):
        return True, ""

    if req_type == "ALL_REQUIRED":
        missing = [s for s in req_subjects if s not in selected]
        if missing:
            return False, f"缺少必选科目：{'、'.join(missing)}"
        return True, ""

    if req_type == "ANY_ONE":
        if not req_subjects or any(s in selected for s in req_subjects):
            return True, ""
        return False, f"需至少选考其中一门：{'、'.join(req_subjects)}"

    if req_type == "CUSTOM":
        # Conservative: treat as ALL_REQUIRED
        missing = [s for s in req_subjects if s not in selected]
        if missing:
            return False, f"缺少科目（自定义要求）：{'、'.join(missing)}"
        return True, ""

    return True, ""


# ─── public API ──────────────────────────────────────────────────────────────

def load_admission_plans(conn: Any, year: int) -> list[dict]:
    """Load all admission plan rows for *year* from the DB. No filtering applied."""
    from app.db import get_cursor

    sql = """
        SELECT id, school_code, school_name, major_code, major_name,
               plan_count, subject_requirement, subject_requirement_text,
               subject_requirement_json, subject_req_source, need_review,
               school_location, tuition, duration
        FROM admission_plan
        WHERE year = ?
    """
    with get_cursor(conn) as cursor:
        cursor.execute(sql, (year,))
        cols = [d[0] for d in cursor.description]
        return [dict(zip(cols, row)) for row in cursor.fetchall()]


def apply_subject_filter(
    programs: list[dict],
    selected_subjects: list[str],
) -> tuple[list[dict], list[dict]]:
    """Pure filter — no DB access.

    Returns:
      eligible  – programs that pass subject check (with '_warnings' field added).
      excluded  – list of {"program": dict, "reason": str, "detail": str}
    """
    selected = frozenset(selected_subjects)
    eligible: list[dict] = []
    excluded: list[dict] = []

    for row in programs:
        row = dict(row)
        row["_warnings"] = []
        need_review = row.get("need_review") or 0
        req_type = ""
        try:
            req_type = json.loads(row.get("subject_requirement_json") or "{}").get("type", "")
        except (json.JSONDecodeError, TypeError):
            pass

        if need_review:
            row["_warnings"].append("选科要求需人工核对")
        if req_type == "UNKNOWN":
            row["_warnings"].append("选科要求未知")

        ok, reason = _subject_match(row.get("subject_requirement_json"), selected)
        if ok:
            eligible.append(row)
        else:
            excluded.append({"program": row, "reason": "选科不符", "detail": reason})

    return eligible, excluded


def filter_by_subject(
    profile: Any,
    year: int,
    conn: Any | None = None,
) -> tuple[list[dict], list[dict]]:
    """Load admission plans and apply subject filter. Backward-compatible wrapper."""
    from app.db import get_conn

    def _run(active_conn: Any) -> tuple[list[dict], list[dict]]:
        programs = load_admission_plans(active_conn, year)
        return apply_subject_filter(programs, profile.selected_subjects)

    if conn is not None:
        return _run(conn)
    with get_conn() as managed:
        return _run(managed)


def filter_by_constraints(
    programs: list[dict],
    profile: Any,
) -> tuple[list[dict], list[dict]]:
    """
    Apply hard constraint filters to a list of programs.

    Returns:
      final    – programs that pass all constraints
      excluded – list of {"program": dict, "reason": str, "detail": str}
    """
    checks = [
        (
            lambda p: not profile.constraints.accept_private and _is_private(p),
            "不接受民办",
            lambda p: f"学校含'民办'标识：{p.get('school_name')}",
        ),
        (
            lambda p: not profile.constraints.accept_sino_foreign and _is_sino_foreign(p),
            "不接受中外合作",
            lambda p: f"专业含'中外合作'：{p.get('major_name')}",
        ),
        (
            lambda p: bool(
                profile.constraints.max_tuition
                and p.get("tuition")
                and p["tuition"] > profile.constraints.max_tuition
            ),
            "学费超出预算",
            lambda p: f"学费 {p.get('tuition')} > 上限 {profile.constraints.max_tuition}",
        ),
        (
            lambda p: p.get("school_name") in profile.preferences.schools.excluded_schools,
            "排除学校",
            lambda p: f"学校在排除列表：{p.get('school_name')}",
        ),
        (
            lambda p: any(
                ex in (p.get("major_name") or "")
                for ex in profile.preferences.majors.excluded_majors
            ),
            "排除专业",
            lambda p: f"专业在排除列表：{p.get('major_name')}",
        ),
        (
            lambda p: _in_excluded_region(p, profile),
            "排除地区",
            lambda p: f"学校所在省份在排除地区",
        ),
    ]

    final: list[dict] = []
    excluded: list[dict] = []

    for program in programs:
        reject_reason = None
        reject_detail = ""
        for predicate, reason, detail_fn in checks:
            if predicate(program):
                reject_reason = reason
                reject_detail = detail_fn(program)
                break
        if reject_reason:
            excluded.append({
                "program": program,
                "reason": reject_reason,
                "detail": reject_detail,
            })
        else:
            final.append(program)

    return final, excluded


# ─── school-level sets ────────────────────────────────────────────────────────

_985: frozenset[str] = frozenset({
    "北京大学", "清华大学", "中国人民大学", "北京航空航天大学", "北京理工大学",
    "中国农业大学", "北京师范大学", "中央民族大学", "南开大学", "天津大学",
    "大连理工大学", "吉林大学", "哈尔滨工业大学", "复旦大学", "同济大学",
    "上海交通大学", "华东师范大学", "南京大学", "东南大学", "浙江大学",
    "中国科学技术大学", "厦门大学", "山东大学", "中国海洋大学", "武汉大学",
    "华中科技大学", "中南大学", "国防科技大学", "中山大学", "华南理工大学",
    "四川大学", "重庆大学", "电子科技大学", "西安交通大学", "西北工业大学",
    "兰州大学", "东北大学", "湖南大学", "西北农林科技大学",
})

_211_extra: frozenset[str] = frozenset({
    # 北京
    "北京交通大学", "北京工业大学", "北京科技大学", "北京化工大学", "北京邮电大学",
    "北京林业大学", "北京中医药大学", "北京外国语大学", "中国传媒大学",
    "中央财经大学", "对外经济贸易大学", "北京体育大学", "中国政法大学",
    "华北电力大学",
    # 华北
    "河北工业大学", "太原理工大学", "内蒙古大学",
    # 东北
    "辽宁大学", "大连海事大学", "延边大学", "东北师范大学",
    "哈尔滨工程大学", "东北农业大学", "东北林业大学",
    # 华东
    "华东理工大学", "东华大学", "上海外国语大学", "上海财经大学",
    "河海大学", "江南大学", "南京航空航天大学", "南京理工大学",
    "中国矿业大学", "南京农业大学", "南京师范大学", "中国药科大学",
    "苏州大学", "安徽大学", "合肥工业大学", "福州大学", "南昌大学",
    # 华中
    "郑州大学", "武汉理工大学", "华中农业大学",
    "华中师范大学", "中南财经政法大学", "湖南师范大学",
    # 华南
    "暨南大学", "华南师范大学", "广西大学", "海南大学",
    # 西南
    "四川农业大学", "西南大学", "西南交通大学", "西南财经大学",
    "云南大学", "贵州大学", "西藏大学",
    # 西北
    "长安大学", "西北大学", "西安电子科技大学", "陕西师范大学",
    "青海大学", "宁夏大学", "新疆大学", "石河子大学",
    # 行业特色
    "中国石油大学（北京）", "中国石油大学（华东）",
    "中国地质大学（武汉）", "中国地质大学（北京）",
    "海军军医大学", "空军军医大学",
})

_211: frozenset[str] = _985 | _211_extra

# 第二轮双一流建设高校完整名单（147所，2022年教育部公布）
_shuang_yiliu: frozenset[str] = frozenset({
    "北京大学", "中国人民大学", "清华大学", "北京交通大学", "北京工业大学",
    "北京航空航天大学", "北京理工大学", "北京科技大学", "北京化工大学", "北京邮电大学",
    "中国农业大学", "北京林业大学", "北京协和医学院", "北京中医药大学", "北京师范大学",
    "首都师范大学", "北京外国语大学", "中国传媒大学", "中央财经大学", "对外经济贸易大学",
    "外交学院", "中国人民公安大学", "北京体育大学", "中央音乐学院", "中国音乐学院",
    "中央美术学院", "中央戏剧学院", "中央民族大学", "中国政法大学",
    "南开大学", "天津大学", "天津工业大学", "天津医科大学", "天津中医药大学",
    "华北电力大学", "河北工业大学",
    "山西大学", "太原理工大学",
    "内蒙古大学",
    "辽宁大学", "大连理工大学", "东北大学", "大连海事大学",
    "吉林大学", "延边大学", "东北师范大学",
    "哈尔滨工业大学", "哈尔滨工程大学", "东北农业大学", "东北林业大学",
    "复旦大学", "同济大学", "上海交通大学", "华东理工大学", "东华大学",
    "上海海洋大学", "上海中医药大学", "华东师范大学", "上海外国语大学", "上海财经大学",
    "上海体育学院", "上海音乐学院", "上海大学", "上海科技大学",
    "南京大学", "苏州大学", "东南大学", "南京航空航天大学", "南京理工大学",
    "中国矿业大学", "南京邮电大学", "河海大学", "江南大学", "南京林业大学",
    "南京信息工程大学", "南京农业大学", "南京医科大学", "南京中医药大学",
    "中国药科大学", "南京师范大学",
    "浙江大学", "中国美术学院",
    "安徽大学", "中国科学技术大学", "合肥工业大学",
    "厦门大学", "福州大学",
    "南昌大学",
    "山东大学", "中国海洋大学", "中国石油大学（华东）",
    "郑州大学", "河南大学",
    "武汉大学", "华中科技大学", "中国地质大学（武汉）", "武汉理工大学",
    "华中农业大学", "华中师范大学", "中南财经政法大学",
    "湘潭大学", "湖南大学", "中南大学", "湖南师范大学",
    "中山大学", "暨南大学", "华南理工大学", "华南农业大学",
    "广州医科大学", "广州中医药大学", "华南师范大学",
    "海南大学",
    "广西大学",
    "四川大学", "重庆大学", "西南交通大学", "电子科技大学", "西南石油大学",
    "成都理工大学", "四川农业大学", "成都中医药大学", "西南大学", "西南财经大学",
    "贵州大学",
    "云南大学",
    "西藏大学",
    "西北大学", "西安交通大学", "西北工业大学", "西安电子科技大学",
    "长安大学", "西北农林科技大学", "陕西师范大学",
    "兰州大学",
    "青海大学",
    "宁夏大学",
    "新疆大学", "石河子大学",
    "中国矿业大学（北京）", "中国石油大学（北京）", "中国地质大学（北京）",
    "宁波大学", "南方科技大学", "中国科学院大学",
    "国防科技大学", "海军军医大学", "空军军医大学",
})

SCHOOL_LEVEL_MAP: dict[str, frozenset[str]] = {
    "985":    _985,
    "211":    _211,
    "双一流": _shuang_yiliu,
}


# ─── additional positive filters ─────────────────────────────────────────────

def filter_by_school_level(
    programs: list[dict],
    levels: list[str],
) -> tuple[list[dict], int]:
    """Keep only programs whose school is in any of the requested level sets."""
    if not levels:
        return programs, 0
    allowed: set[str] = set()
    for lv in levels:
        allowed |= SCHOOL_LEVEL_MAP.get(lv, frozenset())
    kept = [p for p in programs if p.get("school_name") in allowed]
    return kept, len(programs) - len(kept)


def filter_by_city(
    programs: list[dict],
    cities: list[str],
) -> tuple[list[dict], int]:
    """Keep only programs whose school's main-campus city is in *cities*."""
    if not cities:
        return programs, 0
    city_set = set(cities)
    loc_map = _load_school_location_map()

    def _match(p: dict) -> bool:
        name = p.get("school_name") or ""
        db_city = loc_map.get(name, ("", ""))[1]
        if db_city and db_city in city_set:
            return True
        # fallback: city keyword in school name
        return any(c in name for c in city_set)

    kept = [p for p in programs if _match(p)]
    return kept, len(programs) - len(kept)


def filter_by_major_keywords(
    programs: list[dict],
    keywords: list[str],
) -> tuple[list[dict], int]:
    """Keep only programs whose major_name contains at least one keyword."""
    if not keywords:
        return programs, 0
    kept = [
        p for p in programs
        if any(kw in (p.get("major_name") or "") for kw in keywords)
    ]
    return kept, len(programs) - len(kept)
