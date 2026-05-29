"""Candidate ranking stage."""

from __future__ import annotations

import re
from typing import Any


YEAR_WEIGHTS = {2025: 0.5, 2024: 0.3, 2023: 0.2}

DEFAULT_PREFERRED_CITIES = ["北京", "上海", "广州", "深圳", "杭州", "南京", "宁波", "苏州"]

TIER_ORDER = ["冲", "稳", "保", "垫", "高危冲", "数据不足"]

# 第四轮学科评估 grade → ordinal score (higher = better)
GRADE_ORDER = {"A+": 9, "A": 8, "A-": 7, "B+": 6, "B": 5, "B-": 4, "C+": 3, "C": 2, "C-": 1}

# City economic tier (higher = better economic base / job market)
# 5: 北上; 4: 广深; 3: 新一线; 2: 二线; 1: 其他
CITY_TIER: dict[str, int] = {
    # 一线
    "北京": 5, "上海": 5,
    "广州": 4, "深圳": 4,
    # 新一线（2024）
    "成都": 3, "杭州": 3, "重庆": 3, "武汉": 3, "西安": 3,
    "苏州": 3, "南京": 3, "长沙": 3, "天津": 3, "郑州": 3,
    "东莞": 3, "青岛": 3, "沈阳": 3, "宁波": 3, "昆明": 3,
    # 二线
    "福州": 2, "无锡": 2, "合肥": 2, "南宁": 2, "济南": 2,
    "长春": 2, "哈尔滨": 2, "温州": 2, "石家庄": 2, "贵阳": 2,
    "南昌": 2, "太原": 2, "厦门": 2, "大连": 2, "南通": 2,
    "烟台": 2, "常州": 2, "珠海": 2, "兰州": 2, "呼和浩特": 2,
}

# Maps normalized undergraduate major name → 第四轮 discipline code.
# Exact match tried first; substring fallback catches name variants.
MAJOR_DISCIPLINE_MAP: dict[str, str] = {
    # 计算机类 → 0812
    "计算机科学与技术": "0812", "人工智能": "0812",
    "数据科学与大数据技术": "0812", "网络工程": "0812",
    "信息安全": "0812", "物联网工程": "0812",
    "智能科学与技术": "0812", "计算机类": "0812",
    # 软件工程 → 0835
    "软件工程": "0835",
    # 电子科学 → 0809
    "电子科学与技术": "0809", "微电子科学与工程": "0809",
    "光电信息科学与工程": "0809", "集成电路设计与集成系统": "0809",
    # 信息与通信 → 0810
    "通信工程": "0810", "电子信息工程": "0810",
    "信息工程": "0810", "电子信息类": "0810",
    # 控制 → 0811
    "自动化": "0811", "机器人工程": "0811",
    "智能制造工程": "0811", "控制科学与工程": "0811",
    # 电气 → 0808
    "电气工程及其自动化": "0808", "电气工程": "0808",
    # 机械 → 0802
    "机械工程": "0802", "机械设计制造及其自动化": "0802",
    "机械电子工程": "0802", "车辆工程": "0802",
    # 数学 → 0701
    "数学与应用数学": "0701", "信息与计算科学": "0701",
    "统计学": "0701", "数学": "0701",
    # 物理 → 0702
    "物理学": "0702", "应用物理学": "0702",
    # 化学 → 0703
    "化学": "0703", "应用化学": "0703",
    # 生物 → 0710
    "生物科学": "0710", "生物技术": "0710",
    "生物工程": "0710", "生物信息学": "0710",
    # 环境 → 0830
    "环境科学": "0830", "环境工程": "0830",
    # 经济 → 0201 / 0202
    "经济学": "0201", "政治经济学": "0201",
    "金融学": "0202", "国际经济与贸易": "0202",
    "财政学": "0202", "金融工程": "0202",
    "保险学": "0202", "应用经济学": "0202",
    # 法学 → 0301
    "法学": "0301",
    # 外语 → 0502
    "英语": "0502", "日语": "0502", "德语": "0502",
    "法语": "0502", "西班牙语": "0502", "外国语言文学": "0502",
    # 新闻传播 → 0503
    "新闻学": "0503", "广告学": "0503",
    "广播电视学": "0503", "网络与新媒体": "0503", "新闻传播学类": "0503",
    # 管理科学 → 1201
    "管理科学": "1201", "信息管理与信息系统": "1201",
    "工业工程": "1201", "电子商务": "1201",
    # 工商管理 → 1202
    "工商管理": "1202", "市场营销": "1202",
    "会计学": "1202", "财务管理": "1202",
    "人力资源管理": "1202",
    # 公共管理 → 1204
    "行政管理": "1204", "公共事业管理": "1204",
    # 医学
    "临床医学": "1002", "麻醉学": "1002", "医学影像学": "1002",
    "药学": "1007", "护理学": "1011",
}


def _lookup_discipline_code(normalized_name: str) -> str | None:
    """Map a normalized major name to its 第四轮 discipline code."""
    if not normalized_name:
        return None
    if normalized_name in MAJOR_DISCIPLINE_MAP:
        return MAJOR_DISCIPLINE_MAP[normalized_name]
    for key, code in MAJOR_DISCIPLINE_MAP.items():
        if key in normalized_name or normalized_name in key:
            return code
    return None


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


def _major_level(
    program: dict,
    preferred_majors: list,
    preferred_categories: list,
    expanded_major_names: set | None = None,
) -> int:
    """Ordinal major-preference level: 4=exact/expanded, 3=keyword, 2=category, 1=none."""
    name = program.get("normalized_major_name", "")
    raw_name = program.get("major_name", "") or ""
    category = program.get("major_category", "")
    if name in preferred_majors or raw_name in preferred_majors:
        return 4
    if expanded_major_names and (name in expanded_major_names or raw_name in expanded_major_names):
        return 4
    if any(kw and (kw in name or kw in raw_name) for kw in preferred_majors):
        return 3
    if category in preferred_categories:
        return 2
    return 1


def _city_key(program: dict, preferred_cities: list) -> tuple[int, int]:
    """(in_preferred_list, city_tier) — higher is better on both."""
    city = program.get("school_city", "")
    in_preferred = 1 if city in preferred_cities else 0
    tier = CITY_TIER.get(city, 1)
    return (in_preferred, tier)


def _school_quality_key(program: dict, preferred_schools: list) -> tuple[int, int]:
    """(discipline_grade_score, -ruanke_rank) — higher is better on both."""
    if program.get("school_name") in preferred_schools:
        return (1000, 1000)
    disc_grade = GRADE_ORDER.get(program.get("discipline_grade") or "", 0)
    ruanke = program.get("ruanke_rank")
    ruanke_score = -ruanke if ruanke else -999
    return (disc_grade, ruanke_score)


# ── kept for backward-compatibility; not used internally ─────────────────────

def get_major_score(
    program: dict,
    preferred_majors: list,
    preferred_categories: list,
    expanded_major_names: set | None = None,
) -> int:
    level = _major_level(program, preferred_majors, preferred_categories, expanded_major_names)
    return {4: 100, 3: 90, 2: 85, 1: 40}[level]


def get_school_score(program: dict, preferred_schools: list) -> int:
    disc, ruanke = _school_quality_key(program, preferred_schools)
    if disc == 1000:
        return 200
    ruanke_base = max(0, -ruanke // 5) if ruanke != -999 else 0
    return disc * 5 + ruanke_base


# ─────────────────────────────────────────────────────────────────────────────

def sort_candidates(
    candidates: list[dict],
    main_priority: str,
    preferred_majors: list,
    preferred_categories: list,
    preferred_schools: list,
    preferred_cities: list | None = None,
    expanded_major_names: set | None = None,
    city_first: bool = False,  # deprecated: ignored; use main_priority="城市优先"
) -> list[dict]:
    """
    Sort candidates within each risk tier, then concatenate tiers.

    Priority chains (all within the same tier):
      专业优先: major_level > school_quality > city_match > gap
      学校优先: school_quality > major_level > city_match > gap
      城市优先: city_match > school_quality > major_level > gap
    """

    # Track whether user explicitly specified each dimension (before defaults kick in)
    has_major = bool(preferred_majors)
    has_city = bool(preferred_cities)  # True only when user explicitly specified cities

    if preferred_cities is None:
        preferred_cities = DEFAULT_PREFERRED_CITIES

    groups = {tier: [] for tier in TIER_ORDER}
    for program in candidates:
        tier = program.get("gap_info", {}).get("tier", "数据不足")
        groups.setdefault(tier, []).append(program)

    def sort_key(program: dict) -> tuple:
        major = _major_level(program, preferred_majors, preferred_categories, expanded_major_names)
        school = _school_quality_key(program, preferred_schools)
        city = _city_key(program, preferred_cities)
        ratio = (program.get("gap_info") or {}).get("ratio")
        gap = -abs(ratio) if ratio is not None else -1.0

        # If user explicitly specified a secondary preference, promote it above school quality.
        # 专业优先 + 指定城市 → (major, city, school, gap)
        # 学校优先 + 指定城市 → (school, city, major, gap)
        # 城市优先 + 指定专业 → (city, major, school, gap)
        if main_priority == "专业优先":
            return (major, city, school, gap) if has_city else (major, school, city, gap)
        elif main_priority == "学校优先":
            return (school, city, major, gap) if has_city else (school, major, city, gap)
        else:  # 城市优先
            return (city, major, school, gap) if has_major else (city, school, major, gap)

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
    discipline_grades = _load_discipline_grades(conn)

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

        disc_code = _lookup_discipline_code(normalized_major_name)
        item["discipline_grade"] = discipline_grades.get((school_name, disc_code), "") if disc_code else ""

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


def _load_discipline_grades(conn: Any) -> dict[tuple[str, str], str]:
    """Return {(school_name, discipline_code): grade} from discipline_evaluation."""
    try:
        rows = conn.execute(
            "SELECT school_name, discipline_code, grade FROM discipline_evaluation"
        ).fetchall()
        return {(str(row[0]), str(row[1])): str(row[2]) for row in rows}
    except Exception:
        return {}
