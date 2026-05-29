"""Build school, major, and city profile tables from source-grounded data."""

from __future__ import annotations

import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
import urllib3


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

RAW_DIR = PROJECT_ROOT / "data" / "raw"
SCHOOL_RAW_PATH = RAW_DIR / "school_locations_raw.json"
SCHOOL_PROFILE_RAW_PATH = RAW_DIR / "school_profiles_raw.json"
SCHOOL_INFO_URL = "https://static-data.gaokao.cn/www/2.0/school/{school_id}/info.json"
MAJOR_INFO_URL = "https://static-data.gaokao.cn/www/2.0/special/{special_id}/info.json"
USER_AGENT = "Mozilla/5.0 (CollegeApplication data builder)"

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


CAPITAL_BY_PROVINCE = {
    "北京": "北京", "天津": "天津", "上海": "上海", "重庆": "重庆",
    "河北": "石家庄", "山西": "太原", "内蒙古": "呼和浩特",
    "辽宁": "沈阳", "吉林": "长春", "黑龙江": "哈尔滨",
    "江苏": "南京", "浙江": "杭州", "安徽": "合肥", "福建": "福州",
    "江西": "南昌", "山东": "济南", "河南": "郑州", "湖北": "武汉",
    "湖南": "长沙", "广东": "广州", "广西": "南宁", "海南": "海口",
    "四川": "成都", "贵州": "贵阳", "云南": "昆明", "西藏": "拉萨",
    "陕西": "西安", "甘肃": "兰州", "青海": "西宁", "宁夏": "银川",
    "新疆": "乌鲁木齐",
}

OFFICIAL_CITY_FACTS = {
    ("山东", "济南"): {
        "summary": "济南是山东省省会，山东半岛城市群核心城市之一，省会公共服务和区域总部资源更集中。",
        "gdp": "14210亿元",
        "population": "961.6万人",
        "industry_summary": "官方公报显示第三产业占比64.5%，信息传输、软件和信息技术服务业营业收入1014.8亿元。",
        "employment_summary": "官方公报显示城镇新增就业21万人，人才资源总量310万人。",
        "source_name": "济南市统计局2025年国民经济和社会发展统计公报",
        "source_url": "https://jntj.jinan.gov.cn/col/col18254/art/2026/art_7dc3135bb2209f961b3c65baa8ab3d2d.html",
    },
}


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _clip(value: Any, limit: int = 120) -> str:
    text = _clean(value)
    if len(text) <= limit:
        return text
    return text[:limit].rstrip("，。；,; ") + "…"


def _to_int(value: Any) -> int | None:
    try:
        if value in (None, "", "0"):
            return None
        return int(float(str(value)))
    except (TypeError, ValueError):
        return None


def school_profile_from_gaokao_payload(payload: dict, source_url: str) -> dict:
    """Extract a source-grounded school_profile row from gaokao.cn info data."""

    labels = [
        _clean(item.get("name"))
        for item in payload.get("label_list") or []
        if _clean(item.get("name"))
    ]
    content = _clean(payload.get("content"))
    return {
        "school_name": _clean(payload.get("name")),
        "school_id": _clean(payload.get("school_id")),
        "summary": _clip(content, 140),
        "content": content,
        "tags": "/".join(dict.fromkeys(labels)),
        "motto": _clean(payload.get("motto")),
        "founded_year": _clean(payload.get("create_date")),
        "school_type": _clean(payload.get("type_name")),
        "school_nature": _clean(payload.get("school_nature_name")),
        "education_level": _clean(payload.get("level_name")),
        "master_count": _to_int(payload.get("num_master")),
        "doctor_count": _to_int(payload.get("num_doctor")),
        "academician_count": _to_int(payload.get("num_academician")),
        "ruanke_rank": _to_int(payload.get("ruanke_rank")),
        "source_name": "阳光高考",
        "source_url": source_url,
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
    }


def _fetch_school_profile(school: dict, timeout: int = 8) -> dict | None:
    school_id = school.get("school_id")
    if not school_id:
        return None
    url = SCHOOL_INFO_URL.format(school_id=school_id)
    try:
        response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout, verify=False)
        response.raise_for_status()
        payload = response.json()
    except Exception:
        return None
    data = payload.get("data") or {}
    if not data.get("name"):
        return None
    return school_profile_from_gaokao_payload(data, url)


def fetch_school_profiles(limit: int | None = None, workers: int = 24) -> list[dict]:
    """Fetch school profiles for schools already discovered in raw school data."""

    if not SCHOOL_RAW_PATH.exists():
        raise FileNotFoundError(f"missing {SCHOOL_RAW_PATH}; run scrape_school_locations.py first")
    schools = json.loads(SCHOOL_RAW_PATH.read_text(encoding="utf-8"))
    if limit:
        schools = schools[:limit]

    profiles: list[dict] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_fetch_school_profile, school): school for school in schools}
        for index, future in enumerate(as_completed(futures), start=1):
            row = future.result()
            if row:
                profiles.append(row)
            if index % 200 == 0:
                print(f"  fetched {index}/{len(schools)}, valid {len(profiles)}")

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    SCHOOL_PROFILE_RAW_PATH.write_text(
        json.dumps(profiles, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return profiles


def build_major_profile_rows(description_rows: list[dict]) -> list[dict]:
    """Build major_profile rows, falling back from category rows to concrete majors."""

    fallback_by_level3: dict[str, dict] = {}
    for row in description_rows:
        level3 = _clean(row.get("level3"))
        intro = _clean(row.get("is_what"))
        if not level3 or not intro or _clean(row.get("name")).endswith("类"):
            continue
        fallback_by_level3.setdefault(level3, row)

    result: list[dict] = []
    for row in description_rows:
        source = row
        fallback_from = ""
        if not _clean(row.get("is_what")):
            fallback = fallback_by_level3.get(_clean(row.get("level3")))
            if fallback:
                source = fallback
                fallback_from = _clean(fallback.get("name"))

        special_id = row.get("special_id")
        result.append(
            {
                "major_name": _clean(row.get("name")),
                "special_id": _to_int(special_id),
                "summary": _clip(source.get("is_what"), 160),
                "learn_what": _clean(source.get("learn_what")),
                "career_direction": _clean(source.get("do_what")),
                "keywords": _clean(source.get("keywords")),
                "fallback_from": fallback_from,
                "source_name": "阳光高考",
                "source_url": MAJOR_INFO_URL.format(special_id=special_id) if special_id else "",
                "fetched_at": datetime.now().isoformat(timespec="seconds"),
            }
        )
    return [row for row in result if row["major_name"]]


def _city_tier_label(city: str) -> tuple[int, str]:
    from app.pipeline.rank import CITY_TIER

    tier = CITY_TIER.get(city, 1)
    label_by_tier = {5: "一线城市", 4: "一线城市", 3: "新一线城市", 2: "二线城市", 1: "普通地级市"}
    return tier, label_by_tier[tier]


def build_city_profile_rows(city_rows: list[dict]) -> list[dict]:
    """Build city_profile rows from structural data plus official fact seeds."""

    seen: set[tuple[str, str]] = set()
    profiles: list[dict] = []
    for row in city_rows:
        province = _clean(row.get("province"))
        city = _clean(row.get("city") or row.get("city_name"))
        if not province or not city or (province, city) in seen:
            continue
        seen.add((province, city))
        tier, tier_label = _city_tier_label(city)
        is_capital = 1 if CAPITAL_BY_PROVINCE.get(province) == city else 0
        base_summary = f"{city}位于{province}"
        if is_capital:
            base_summary += "，是省会/直辖市核心城市"
        base_summary += f"，按系统城市分层为{tier_label}。"

        official = OFFICIAL_CITY_FACTS.get((province, city), {})
        profiles.append(
            {
                "city_name": city,
                "province": province,
                "city_tier": tier,
                "tier_label": tier_label,
                "is_capital": is_capital,
                "summary": official.get("summary") or base_summary,
                "gdp": official.get("gdp", ""),
                "population": official.get("population", ""),
                "industry_summary": official.get("industry_summary", ""),
                "employment_summary": official.get("employment_summary", ""),
                "source_name": official.get("source_name") or "项目内置城市分层规则",
                "source_url": official.get("source_url", ""),
                "fetched_at": datetime.now().isoformat(timespec="seconds"),
            }
        )
    return profiles


def _rows_as_dicts(conn: Any, sql: str) -> list[dict]:
    cursor = conn.execute(sql)
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _upsert_many(conn: Any, table: str, rows: list[dict], conflict_columns: tuple[str, ...]) -> None:
    if not rows:
        return
    columns = list(rows[0])
    placeholders = ", ".join("?" for _ in columns)
    update_columns = [column for column in columns if column not in conflict_columns]
    assignments = ", ".join(f"{column}=excluded.{column}" for column in update_columns)
    conflict = ", ".join(conflict_columns)
    sql = (
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT({conflict}) DO UPDATE SET {assignments}"
    )
    conn.executemany(sql, [tuple(row.get(column) for column in columns) for row in rows])


def build_profiles(
    school_limit: int | None = None,
    fetch_schools: bool = True,
    workers: int = 24,
) -> dict[str, int]:
    """Create profile tables and populate them from available source data."""

    from app.db import get_conn
    from scripts.init_db import execute_schema, load_schema_sql

    with get_conn() as conn:
        execute_schema(conn, load_schema_sql())

    if fetch_schools or not SCHOOL_PROFILE_RAW_PATH.exists():
        school_profiles = fetch_school_profiles(limit=school_limit, workers=workers)
    else:
        school_profiles = json.loads(SCHOOL_PROFILE_RAW_PATH.read_text(encoding="utf-8"))
        if school_limit:
            school_profiles = school_profiles[:school_limit]

    with get_conn() as conn:
        major_rows = _rows_as_dicts(conn, "SELECT * FROM major_description")
        city_rows = _rows_as_dicts(
            conn,
            """
            SELECT DISTINCT province, city
            FROM school_master
            WHERE province IS NOT NULL AND province != ''
              AND city IS NOT NULL AND city != ''
            """,
        )
        major_profiles = build_major_profile_rows(major_rows)
        city_profiles = build_city_profile_rows(city_rows)

        _upsert_many(conn, "school_profile", school_profiles, ("school_name",))
        _upsert_many(conn, "major_profile", major_profiles, ("major_name",))
        _upsert_many(conn, "city_profile", city_profiles, ("city_name", "province"))

        return {
            "school_profile": conn.execute("SELECT COUNT(*) FROM school_profile").fetchone()[0],
            "major_profile": conn.execute("SELECT COUNT(*) FROM major_profile").fetchone()[0],
            "city_profile": conn.execute("SELECT COUNT(*) FROM city_profile").fetchone()[0],
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build source-grounded profile tables.")
    parser.add_argument("--school-limit", type=int, default=None, help="Limit school profile fetches for testing.")
    parser.add_argument("--workers", type=int, default=24)
    parser.add_argument("--no-fetch-schools", action="store_true", help="Reuse data/raw/school_profiles_raw.json.")
    args = parser.parse_args()

    counts = build_profiles(
        school_limit=args.school_limit,
        fetch_schools=not args.no_fetch_schools,
        workers=args.workers,
    )
    for table, count in counts.items():
        print(f"{table}: {count} rows")


if __name__ == "__main__":
    main()
