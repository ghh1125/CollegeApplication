"""Profile enrichment and source-grounded recommendation reason text."""

from __future__ import annotations

import re
from typing import Any


def _clean_text(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text.replace("；；", "；")


def _clip(value: Any, limit: int = 54) -> str:
    text = _clean_text(value)
    if len(text) <= limit:
        return text
    return text[:limit].rstrip("，。；,; ") + "…"


def _strip_label(text: str, label: str) -> str:
    text = _clean_text(text)
    for sep in ("：", ":"):
        prefix = f"{label}{sep}"
        if text.startswith(prefix):
            return text[len(prefix):].strip()
    return text


def _truthy_labels(program: dict) -> str:
    labels: list[str] = []
    if program.get("is_985"):
        labels.append("985")
    if program.get("is_211"):
        labels.append("211")
    if program.get("is_double_first_class"):
        labels.append("双一流")
    return "/".join(labels)


def _school_part(program: dict) -> str:
    name = _clean_text(program.get("school_name")) or "学校"
    profile = program.get("school_profile") or {}
    details: list[str] = []

    match_label = _clean_text(program.get("_school_match_label"))
    if match_label:
        details.append(match_label)

    summary = _strip_label(profile.get("summary") or "", name)
    if summary:
        details.append(_clip(summary, 68))

    tags = _clean_text(profile.get("tags")) or _truthy_labels(program)
    if tags and tags not in "；".join(details):
        details.append(tags)

    ruanke_rank = profile.get("ruanke_rank") or program.get("ruanke_rank")
    if ruanke_rank:
        details.append(f"软科第{ruanke_rank}")


    founded = _clean_text(profile.get("founded_year"))
    if founded:
        details.append(f"创办于{founded}年")

    master = profile.get("master_count")
    doctor = profile.get("doctor_count")
    if master or doctor:
        degree_bits = []
        if master:
            degree_bits.append(f"硕士点{master}个")
        if doctor:
            degree_bits.append(f"博士点{doctor}个")
        details.append("、".join(degree_bits))

    if not details:
        details.append("学校画像待补充")

    return f"{name}：" + "；".join(details)


def _major_part(program: dict) -> str:
    name = _clean_text(program.get("major_name")) or "专业"
    profile = program.get("major_profile") or {}
    details: list[str] = []

    match_label = _clean_text(program.get("_major_match_label"))
    if match_label:
        details.append(match_label)

    summary = _strip_label(profile.get("summary") or "", name)
    if summary:
        details.append(_clip(summary, 70))

    discipline_grade = _clean_text(program.get("discipline_grade"))
    if discipline_grade:
        details.append(f"学科评估{discipline_grade}")

    career = _clean_text(profile.get("career_direction"))
    if career:
        details.append("去向：" + _clip(career, 42))

    fallback_from = _clean_text(profile.get("fallback_from"))
    if fallback_from:
        details.append(f"fallback{fallback_from}")

    if not details:
        details.append("专业画像待补充")

    return f"{name}：" + "；".join(details)


def _city_part(program: dict) -> str:
    city = _clean_text(program.get("school_city")) or "城市"
    profile = program.get("city_profile") or {}
    details: list[str] = []

    match_label = _clean_text(program.get("_city_match_label"))
    if match_label:
        details.append(match_label)

    summary = _strip_label(profile.get("summary") or "", city)
    if summary:
        details.append(_clip(summary, 58))

    gdp = _clean_text(profile.get("gdp"))
    if gdp:
        details.append(f"GDP {gdp}")

    population = _clean_text(profile.get("population"))
    if population:
        details.append(f"常住人口{population}")

    industry = _clean_text(profile.get("industry_summary"))
    if industry:
        details.append(_clip(industry, 42))

    if not details:
        tier = _clean_text(profile.get("tier_label"))
        details.append(tier or "城市画像待补充")

    return f"{city}：" + "；".join(details)


def _risk_part(program: dict) -> str:
    gap_info = program.get("gap_info") or {}
    details: list[str] = []
    weighted_avg = gap_info.get("weighted_avg")
    gap = gap_info.get("gap")
    if weighted_avg is not None:
        details.append(f"均值位次{weighted_avg}")
    if gap is not None:
        details.append(f"gap {gap}")
    if not details:
        details.append("历史位次不足")
    return "风险：" + "；".join(details)


def build_profile_sort_reason(program: dict, main_priority: str) -> str:
    """Return reason text ordered by school/major/city priority."""

    tier = (program.get("gap_info") or {}).get("tier", "数据不足")
    parts_by_priority = {
        "学校优先": [_school_part, _major_part, _city_part, _risk_part],
        "专业优先": [_major_part, _school_part, _city_part, _risk_part],
        "城市优先": [_city_part, _school_part, _major_part, _risk_part],
    }
    parts = [fn(program) for fn in parts_by_priority.get(main_priority, parts_by_priority["学校优先"])]
    return f"{tier}；{main_priority}：" + "；".join(parts)


def _rows_as_dicts(conn: Any, sql: str, params: tuple = ()) -> list[dict]:
    cursor = conn.execute(sql, params)
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _load_school_profiles(conn: Any) -> dict[str, dict]:
    try:
        rows = _rows_as_dicts(conn, "SELECT * FROM school_profile")
    except Exception:
        return {}
    return {row["school_name"]: row for row in rows if row.get("school_name")}


def _load_major_profiles(conn: Any) -> dict[str, dict]:
    try:
        rows = _rows_as_dicts(conn, "SELECT * FROM major_profile")
    except Exception:
        return {}
    return {row["major_name"]: row for row in rows if row.get("major_name")}


def _load_city_profiles(conn: Any) -> tuple[dict[tuple[str, str], dict], dict[str, dict]]:
    try:
        rows = _rows_as_dicts(conn, "SELECT * FROM city_profile")
    except Exception:
        return {}, {}
    by_pair = {
        (row.get("province") or "", row.get("city_name") or ""): row
        for row in rows
        if row.get("city_name")
    }
    by_city = {row["city_name"]: row for row in rows if row.get("city_name")}
    return by_pair, by_city


def enrich_with_profiles(candidates: list[dict], conn: Any) -> list[dict]:
    """Attach school_profile, major_profile, and city_profile dictionaries."""

    school_profiles = _load_school_profiles(conn)
    major_profiles = _load_major_profiles(conn)
    city_by_pair, city_by_name = _load_city_profiles(conn)

    enriched: list[dict] = []
    for program in candidates:
        item = dict(program)
        school_name = item.get("school_name") or ""
        major_name = item.get("normalized_major_name") or item.get("major_name") or ""
        province = item.get("school_province") or ""
        city = item.get("school_city") or ""

        if school_name in school_profiles:
            item["school_profile"] = school_profiles[school_name]
        if major_name in major_profiles:
            item["major_profile"] = major_profiles[major_name]
        elif item.get("major_name") in major_profiles:
            item["major_profile"] = major_profiles[item["major_name"]]

        city_profile = city_by_pair.get((province, city)) or city_by_name.get(city)
        if city_profile:
            item["city_profile"] = city_profile

        enriched.append(item)

    return enriched
