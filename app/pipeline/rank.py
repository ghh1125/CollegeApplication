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
    # 计算机 → 0812
    "计算机科学与技术": "0812", "人工智能": "0812",
    "数据科学与大数据技术": "0812", "网络工程": "0812",
    "信息安全": "0812", "物联网工程": "0812",
    "智能科学与技术": "0812", "计算机类": "0812",
    "计算机应用技术": "0812", "大数据技术": "0812",
    "云计算技术应用": "0812", "人工智能技术应用": "0812",
    "区块链技术应用": "0812", "数字媒体技术": "0812",
    "网络安全与执法": "0812",
    # 软件工程 → 0835
    "软件工程": "0835", "软件技术": "0835",
    "移动应用开发": "0835",
    # 电子科学 → 0809
    "电子科学与技术": "0809", "微电子科学与工程": "0809",
    "光电信息科学与工程": "0809", "集成电路设计与集成系统": "0809",
    "集成电路工程": "0809",
    # 信息与通信 → 0810
    "通信工程": "0810", "电子信息工程": "0810",
    "信息工程": "0810", "电子信息类": "0810",
    "通信技术": "0810", "移动通信技术": "0810",
    # 控制 → 0811
    "自动化": "0811", "机器人工程": "0811",
    "智能制造工程": "0811", "控制科学与工程": "0811",
    "工业机器人技术": "0811", "机电一体化技术": "0811",
    "无人机应用技术": "0811",
    # 电气 → 0808
    "电气工程及其自动化": "0808", "电气工程": "0808",
    "新能源科学与工程": "0808",
    # 机械 → 0802
    "机械工程": "0802", "机械设计制造及其自动化": "0802",
    "机械电子工程": "0802", "车辆工程": "0802",
    "机械制造与自动化": "0802", "新能源汽车技术": "0802",
    "汽车服务工程": "0802", "农业机械化及其自动化": "0802",
    # 土木 → 0814
    "土木工程": "0814", "建筑工程技术": "0814",
    "工程造价": "0814", "工程管理": "0814",
    "道路桥梁与渡河工程": "0814", "地下空间工程": "0814",
    # 建筑 → 0813
    "建筑学": "0813", "城乡规划": "0813",
    "风景园林": "0813", "建筑设计": "0813",
    # 水利 → 0815
    "水利水电工程": "0815", "港口航道与海岸工程": "0815",
    # 化工 → 0817
    "化学工程与工艺": "0817", "应用化工技术": "0817",
    "制药工程": "0817", "化工装备技术": "0817",
    # 材料 → 0805
    "材料科学与工程": "0805", "材料化学": "0805",
    "高分子材料与工程": "0805", "无机非金属材料工程": "0805",
    "金属材料工程": "0805",
    # 矿业 → 0819
    "采矿工程": "0819", "矿物加工工程": "0819",
    # 纺织 → 0821
    "纺织工程": "0821", "服装与服饰设计": "0821",
    # 轻工 → 0822
    "食品科学与工程": "0822", "食品质量与安全": "0822",
    "食品营养与检验教育": "0822",
    # 数学 → 0701
    "数学与应用数学": "0701", "信息与计算科学": "0701",
    "统计学": "0701", "数学": "0701", "数学类": "0701",
    # 物理 → 0702
    "物理学": "0702", "应用物理学": "0702", "物理类": "0702",
    # 化学 → 0703
    "化学": "0703", "应用化学": "0703", "化学类": "0703",
    # 生物 → 0710
    "生物科学": "0710", "生物技术": "0710",
    "生物工程": "0710", "生物信息学": "0710",
    "生物制药": "0710",
    # 环境 → 0830
    "环境科学": "0830", "环境工程": "0830",
    "环境监测技术": "0830",
    # 地学 → 0705/0706/0707
    "地理科学": "0705", "地质学": "0706",
    "地球物理学": "0707",
    # 农学 → 0901
    "农学": "0901", "园艺": "0901", "植物保护": "0901",
    "农业资源与环境": "0901", "应用生物科学": "0901",
    # 林学 → 0907
    "林学": "0907", "森林保护": "0907",
    # 经济 → 0201 / 0202
    "经济学": "0201", "政治经济学": "0201",
    "金融学": "0202", "国际经济与贸易": "0202",
    "财政学": "0202", "金融工程": "0202",
    "保险学": "0202", "应用经济学": "0202",
    "数字经济": "0202", "大数据与财务管理": "0202",
    "大数据与会计": "1202",  # 会计类方向
    # 法学 → 0301
    "法学": "0301", "法学类": "0301",
    "知识产权": "0301", "监狱学": "0301",
    # 政治学 → 0302
    "政治学与行政学": "0302", "国际政治": "0302",
    # 社会学 → 0303
    "社会学": "0303", "社会工作": "0303",
    # 民族学 → 0304
    "民族学": "0304",
    # 马克思主义 → 0305
    "思想政治教育": "0305",
    # 中文 → 0501
    "汉语言文学": "0501", "汉语言": "0501",
    "中国语言文学类": "0501", "汉语国际教育": "0501",
    "秘书学": "0501", "中国少数民族语言文学": "0501",
    # 外语 → 0502
    "英语": "0502", "日语": "0502", "德语": "0502",
    "法语": "0502", "西班牙语": "0502", "外国语言文学": "0502",
    "朝鲜语": "0502", "俄语": "0502", "阿拉伯语": "0502",
    "翻译": "0502", "商务英语": "0502",
    # 新闻传播 → 0503
    "新闻学": "0503", "广告学": "0503",
    "广播电视学": "0503", "网络与新媒体": "0503", "新闻传播学类": "0503",
    "数字出版": "0503", "广播电视编导": "0503", "播音与主持艺术": "0503",
    # 艺术学 → 1301/1302/1303/1304/1305
    "美术学": "1301", "绘画": "1301", "雕塑": "1301",
    "音乐学": "1302", "音乐表演": "1302",
    "戏剧影视文学": "1303", "表演": "1303",
    "舞蹈学": "1304", "舞蹈表演": "1304",
    "设计学类": "1305", "视觉传达设计": "1305",
    "环境设计": "1305", "产品设计": "1305",
    "工业设计": "1305",
    # 体育 → 0403/0404
    "体育教育": "0403", "运动训练": "0403",
    "社会体育指导与管理": "0403",
    # 教育学 → 0401
    "学前教育": "0401", "小学教育": "0401",
    "特殊教育": "0401", "教育学": "0401",
    # 管理科学 → 1201
    "管理科学": "1201", "信息管理与信息系统": "1201",
    "工业工程": "1201", "电子商务": "1201",
    "物流管理": "1201", "物流工程": "1201",
    "供应链管理": "1201", "大数据管理与应用": "1201",
    "现代物流管理": "1201",
    # 工商管理 → 1202
    "工商管理": "1202", "市场营销": "1202",
    "会计学": "1202", "财务管理": "1202",
    "人力资源管理": "1202", "审计学": "1202",
    "大数据与会计": "1202",
    # 旅游管理 → 1203
    "旅游管理": "1203", "酒店管理": "1203",
    "旅游管理与服务教育": "1203",
    # 公共管理 → 1204
    "行政管理": "1204", "公共事业管理": "1204",
    "土地资源管理": "1204", "城市管理": "1204",
    "社区管理与服务": "1204",
    # 农林经济 → 1203
    "农林经济管理": "1203",
    # 医学 → 各分支
    "临床医学": "1002", "麻醉学": "1002", "医学影像学": "1002",
    "口腔医学": "1003",
    "公共卫生与预防医学": "1004",
    "中医学": "1005", "中西医临床医学": "1005",
    "药学": "1007", "中药学": "1008",
    "护理学": "1011", "助产学": "1011",
    "医学检验技术": "1010", "医学影像技术": "1010",
    "卫生检验与检疫": "1010",
    # 心理学 → 0402
    "心理学": "0402", "应用心理学": "0402",
}


# ── 试验班/大类 关键词推断规则（按出现频率排序）─────────────────────────
# 当 MAJOR_DISCIPLINE_MAP 精确/子串匹配均失败时，用关键词模糊推断
_DISCIPLINE_KEYWORD_RULES: list[tuple[str, str]] = [
    # 关键词 → discipline_code（先出现的优先匹配）
    ("计算机", "0812"), ("软件", "0835"), ("人工智能", "0812"),
    ("大数据", "0812"), ("网络空间", "0812"), ("信息安全", "0812"),
    ("电子", "0809"), ("集成电路", "0809"), ("半导体", "0809"),
    ("通信", "0810"), ("信息与通信", "0810"),
    ("自动化", "0811"), ("控制", "0811"), ("机器人", "0811"), ("无人机", "0811"),
    ("电气", "0808"), ("电力", "0808"),
    ("机械", "0802"), ("车辆", "0802"), ("汽车", "0802"),
    ("土木", "0814"), ("建筑工程", "0814"), ("工程造价", "0814"),
    ("建筑", "0813"), ("规划", "0813"),
    ("化工", "0817"), ("化学工程", "0817"), ("制药", "0817"),
    ("材料", "0805"),
    ("经济管理", "1202"), ("工商管理", "1202"),
    ("经济", "0201"), ("金融", "0202"),
    ("法学", "0301"), ("法律", "0301"),
    ("汉语", "0501"), ("中文", "0501"), ("文学", "0501"),
    ("英语", "0502"), ("外语", "0502"), ("翻译", "0502"),
    ("新闻", "0503"), ("传媒", "0503"), ("广播", "0503"),
    ("数学", "0701"), ("统计", "0701"),
    ("物理", "0702"),
    ("化学", "0703"),
    ("生物", "0710"),
    ("环境", "0830"),
    ("医学", "1002"), ("临床", "1002"),
    ("口腔", "1003"),
    ("药学", "1007"), ("药", "1007"),
    ("护理", "1011"),
    ("管理", "1201"), ("物流", "1201"),
    ("旅游", "1203"), ("酒店", "1203"),
    ("教育", "0401"), ("师范", "0401"),
    ("农", "0901"),
]


def _lookup_discipline_code(normalized_name: str, raw_name: str = "") -> str | None:
    """
    Map a major name to its 第四轮 discipline code.

    Three-pass lookup:
    1. Exact match in MAJOR_DISCIPLINE_MAP (normalized name)
    2. Substring match (normalized name contained in / containing a map key)
    3. Keyword inference via _DISCIPLINE_KEYWORD_RULES (for 试验班/大类/etc.)
       Uses raw_name (before normalization) so bracket content is still available.
    """
    if not normalized_name:
        return None
    # Pass 1: exact
    if normalized_name in MAJOR_DISCIPLINE_MAP:
        return MAJOR_DISCIPLINE_MAP[normalized_name]
    # Pass 2: substring
    for key, code in MAJOR_DISCIPLINE_MAP.items():
        if key in normalized_name or normalized_name in key:
            return code
    # Pass 3: keyword inference on raw_name (brackets preserved) or normalized_name
    search_text = raw_name or normalized_name
    for keyword, code in _DISCIPLINE_KEYWORD_RULES:
        if keyword in search_text:
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


def _school_quality_key(program: dict, preferred_schools: list, major_first: bool = False) -> tuple[int, int]:
    """
    School quality signal — two orderings:
      major_first=True  (专业优先): disc_grade leads, falls back to school_best_grade
      major_first=False (学校优先): ruanke leads, disc_grade as tiebreaker
    """
    if program.get("school_name") in preferred_schools:
        return (1000, 1000)
    disc_grade = GRADE_ORDER.get(program.get("discipline_grade") or "", 0)
    # Fall back to the school's best discipline grade when specific one is missing
    if disc_grade == 0:
        disc_grade = GRADE_ORDER.get(program.get("school_best_grade") or "", 0)
    ruanke = program.get("ruanke_rank")
    ruanke_score = -ruanke if ruanke else -999
    if major_first:
        return (disc_grade, ruanke_score)
    else:
        return (ruanke_score, disc_grade)


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


def build_sort_reason(
    program: dict,
    main_priority: str,
    preferred_majors: list,
    preferred_categories: list,
    preferred_cities: list | None = None,
    preferred_schools: list | None = None,
    expanded_major_names: set | None = None,
) -> str:
    """Build a short deterministic explanation for why a program ranks where it does."""
    from app.pipeline.profiles import build_profile_sort_reason

    preferred_cities = preferred_cities or []
    preferred_schools = preferred_schools or []
    item = dict(program)

    major_level = _major_level(
        item,
        preferred_majors,
        preferred_categories,
        expanded_major_names,
    )
    if major_level == 4:
        item["_major_match_label"] = "匹配偏好专业"
    elif major_level == 3:
        item["_major_match_label"] = "匹配专业关键词"
    elif major_level == 2:
        item["_major_match_label"] = "匹配偏好专业类"

    if item.get("school_name") in preferred_schools:
        item["_school_match_label"] = "匹配偏好学校"
    city = item.get("school_city") or ""
    if city in preferred_cities:
        item["_city_match_label"] = f"匹配偏好城市{city}"

    return build_profile_sort_reason(item, main_priority)


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
        school = _school_quality_key(program, preferred_schools, major_first=(main_priority == "专业优先"))
        city = _city_key(program, preferred_cities)
        ratio = (program.get("gap_info") or {}).get("ratio")
        gap = -abs(ratio) if ratio is not None else -1.0

        # Boost explicitly-specified secondary preferences above school quality.
        # Rule: if you specified it, it gets pos 2; major beats city for the pos-2 slot.
        #
        # 专业优先: city boost when has_city (major is already primary)
        # 学校优先: major stays at pos 2 whenever has_major; city only gets pos 2 when
        #           user specified city but NOT major
        # 城市优先: major boost when has_major (city is already primary)
        if main_priority == "专业优先":
            return (major, city, school, gap) if has_city else (major, school, city, gap)
        elif main_priority == "学校优先":
            if has_city and not has_major:
                return (school, city, major, gap)
            return (school, major, city, gap)
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
    discipline_grades, school_best_grades = _load_discipline_grades(conn)

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

        disc_code = _lookup_discipline_code(
            normalized_major_name, raw_name=item.get("major_name") or ""
        )
        item["discipline_grade"] = discipline_grades.get((school_name, disc_code), "") if disc_code else ""
        item["school_best_grade"] = school_best_grades.get(school_name, "")

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


def _load_discipline_grades(conn: Any) -> tuple[dict[tuple[str, str], str], dict[str, str]]:
    """
    Return (by_school_disc, school_best):
      by_school_disc: {(school_name, discipline_code): grade}
      school_best:    {school_name: best_grade}  — highest grade across all disciplines
    """
    try:
        rows = conn.execute(
            "SELECT school_name, discipline_code, grade FROM discipline_evaluation"
        ).fetchall()
        by_school_disc = {(str(r[0]), str(r[1])): str(r[2]) for r in rows}
        school_best: dict[str, str] = {}
        for school, _disc, grade in rows:
            school = str(school)
            if school not in school_best or GRADE_ORDER.get(grade, 0) > GRADE_ORDER.get(school_best[school], 0):
                school_best[school] = grade
        return by_school_disc, school_best
    except Exception:
        return {}, {}
