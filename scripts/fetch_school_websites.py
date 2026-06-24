"""Fetch undergraduate admission and official website URLs for schools.

Source: static-data.gaokao.cn school info JSON, the same data used by
Qianwen/Quark college detail pages. In that payload:
  - data.site        -> 招生官网
  - data.school_site -> 学校官网

Writes to data/zhejiang/college.db school_profile:
  undergraduate_admission_url, official_website_url

The script is resumable through data/zhejiang/raw/school_website_links.json.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "data" / "zhejiang" / "college.db"
CACHE_PATH = PROJECT_ROOT / "data" / "zhejiang" / "raw" / "school_website_links.json"
INFO_URL = "https://static-data.gaokao.cn/www/2.0/school/{school_id}/info.json"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    ),
    "Referer": "https://p.qianwen.com/",
}

# Admission-plan names are not always identical to static-data school names.
# For website links only, these aliases should follow the user's interpretation
# of the applicable official site. Keep this narrow and explicit.
WEBSITE_ALIASES: dict[str, str] = {
    # Plan names with campus / branch suffixes use the main-campus admission site.
    "中国人民大学(苏州校区)": "中国人民大学",
    "北京交通大学(威海校区)": "北京交通大学",
    "北京航空航天大学（杭州国际校园）": "北京航空航天大学",
    "厦门大学(马来西亚分校招生专业)": "厦门大学",
    "合肥工业大学(宣城校区)": "合肥工业大学",
    "哈尔滨工业大学(威海)": "哈尔滨工业大学",
    "哈尔滨工业大学(深圳)": "哈尔滨工业大学",
    "大连理工大学(盘锦校区)": "大连理工大学",
    "山东大学威海分校": "山东大学",
    "电子科技大学(沙河校区)": "电子科技大学",
    "西南大学(荣昌校区)": "西南大学",
    "浙江师范大学 (金华职业技术学院教学点)": "浙江师范大学",

    # ASCII-parentheses variants in admission_plan map to the official names in
    # school_profile. These are independent school entities, not sub-campus
    # fallback to another school.
    "中国地质大学(北京)": "中国地质大学（北京）",
    "中国地质大学(武汉)": "中国地质大学（武汉）",
    "中国石油大学(北京)": "中国石油大学（北京）",
    "中国石油大学(北京)克拉玛依校区": "中国石油大学（北京）",
    "中国石油大学(华东)": "中国石油大学（华东）",
    "中国矿业大学(北京)": "中国矿业大学（北京）",

    "华北电力大学(北京)": "华北电力大学（北京）",
    "华北电力大学(保定)": "华北电力大学（北京）",
    "复旦大学医学院": "复旦大学",

    # School-name changes / upgrades reflected in Qwen static data.
    "天津市职业大学": "天津职业大学",
    "安徽科技学院": "安徽科技工程大学",
    "四川建筑职业技术学院": "四川建筑职业技术大学",
    "日照职业技术学院": "日照职业技术大学",
    "昆明冶金高等专科学校": "昆明冶金职业大学",
    "江苏建筑职业技术学院": "江苏建筑职业技术大学",
    "湖南理工学院": "湖南理工大学",
    "湖州师范学院": "湖州师范大学",
    "皖南医学院": "皖南医科大学",
    "绍兴文理学院": "绍兴大学",
    "襄阳职业技术学院": "襄阳职业技术大学",
    "贵州工商职业学院": "贵州工商职业大学",
    "重庆三峡学院": "重庆三峡科技大学",
    "闽江学院": "闽江大学",
    "黄冈职业技术学院": "黄冈职业技术大学",
    "黑龙江农业工程职业学院": "黑龙江农业工程职业技术大学",
    "香港城市大学(东莞)": "香港城市大学（东莞）",
    "江西泰豪动漫职业学院": "南昌科技职业大学",
    "淮阴工学院": "淮安大学",
    "滨州医学院": "山东医药大学",
}

WEBSITE_OVERRIDES: dict[str, tuple[str, str]] = {
    # Qwen has official-site data but an empty site/admission URL for these.
    "文华学院": ("https://zhaosheng.hustwenhua.net/", "https://www.hustwenhua.net/"),
    "华北科技学院": ("https://zsb.ncist.edu.cn/", "http://www.ncist.edu.cn/"),
    "黑龙江建筑职业技术学院": ("https://zs.hict.org.cn/", "https://www.hict.org.cn/"),
}


def _ensure_columns(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(school_profile)")}
    additions = {
        "undergraduate_admission_url": "TEXT",
        "official_website_url": "TEXT",
    }
    for column, col_type in additions.items():
        if column not in columns:
            conn.execute(f"ALTER TABLE school_profile ADD COLUMN {column} {col_type}")
    conn.commit()


def _load_schools(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    rows = conn.execute(
        """
        SELECT school_id, school_name
        FROM school_profile
        WHERE school_id IS NOT NULL AND school_id != ''
        ORDER BY CAST(school_id AS INTEGER)
        """
    ).fetchall()
    return [(str(school_id), str(school_name)) for school_id, school_name in rows]


def _load_cache() -> dict[str, dict[str, Any]]:
    if not CACHE_PATH.exists():
        return {}
    return json.loads(CACHE_PATH.read_text(encoding="utf-8"))


def _save_cache(cache: dict[str, dict[str, Any]]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def _clean_url(value: Any) -> str:
    url = str(value or "").strip()
    if not url:
        return ""
    if url.startswith("//"):
        return "https:" + url
    if not (url.startswith("http://") or url.startswith("https://")):
        return "http://" + url
    return url


def _fetch_one(session: requests.Session, school_id: str, school_name: str, timeout: float) -> dict[str, Any]:
    source_url = INFO_URL.format(school_id=school_id)
    try:
        response = session.get(source_url, headers=HEADERS, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") or {}
        return {
            "school_id": school_id,
            "school_name": school_name,
            "undergraduate_admission_url": _clean_url(data.get("site")),
            "official_website_url": _clean_url(data.get("school_site")),
            "status": "ok",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "school_id": school_id,
            "school_name": school_name,
            "undergraduate_admission_url": "",
            "official_website_url": "",
            "status": "error",
            "error": str(exc)[:300],
        }


def _apply_to_db(conn: sqlite3.Connection, cache: dict[str, dict[str, Any]]) -> int:
    updated = 0
    for school_id, row in cache.items():
        if row.get("status") != "ok":
            continue
        updated += conn.execute(
            """
            UPDATE school_profile
            SET undergraduate_admission_url = ?,
                official_website_url = ?
            WHERE school_id = ?
            """,
            (
                row.get("undergraduate_admission_url") or None,
                row.get("official_website_url") or None,
                school_id,
            ),
        ).rowcount
    conn.commit()
    return updated


def _apply_aliases(conn: sqlite3.Connection) -> int:
    """Insert/update school_profile rows for admission-plan name aliases."""

    applied = 0
    for alias_name, canonical_name in WEBSITE_ALIASES.items():
        canonical = conn.execute(
            """
            SELECT school_id, undergraduate_admission_url, official_website_url
            FROM school_profile
            WHERE school_name = ?
            """,
            (canonical_name,),
        ).fetchone()
        if not canonical:
            print(f"  alias skipped: {alias_name} -> {canonical_name} (canonical missing)")
            continue

        conn.execute(
            """
            INSERT INTO school_profile (
                school_name, school_id,
                undergraduate_admission_url, official_website_url,
                fetched_at
            )
            VALUES (?, ?, ?, ?, datetime('now'))
            ON CONFLICT(school_name) DO UPDATE SET
                school_id = excluded.school_id,
                undergraduate_admission_url = excluded.undergraduate_admission_url,
                official_website_url = excluded.official_website_url
            """,
            (alias_name, *canonical),
        )
        applied += 1

    conn.commit()
    return applied


def _apply_overrides(conn: sqlite3.Connection) -> int:
    """Insert/update manually verified website rows."""

    applied = 0
    for school_name, (admission_url, official_url) in WEBSITE_OVERRIDES.items():
        existing = conn.execute(
            "SELECT school_id FROM school_profile WHERE school_name = ?",
            (school_name,),
        ).fetchone()
        school_id = existing[0] if existing else None
        conn.execute(
            """
            INSERT INTO school_profile (
                school_name, school_id,
                undergraduate_admission_url, official_website_url,
                fetched_at
            )
            VALUES (?, ?, ?, ?, datetime('now'))
            ON CONFLICT(school_name) DO UPDATE SET
                school_id = excluded.school_id,
                undergraduate_admission_url = excluded.undergraduate_admission_url,
                official_website_url = excluded.official_website_url
            """,
            (school_name, school_id, admission_url, official_url),
        )
        applied += 1

    conn.commit()
    return applied


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="Only fetch first N schools.")
    parser.add_argument("--delay", type=float, default=0.2, help="Delay between requests.")
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--force", action="store_true", help="Refetch cached schools.")
    args = parser.parse_args()

    with sqlite3.connect(DB_PATH) as conn:
        _ensure_columns(conn)
        schools = _load_schools(conn)
        if args.limit:
            schools = schools[: args.limit]

        cache = _load_cache()
        todo = [
            (school_id, school_name)
            for school_id, school_name in schools
            if args.force or school_id not in cache
        ]
        print(f"学校 {len(schools)} 所，待抓 {len(todo)} 所，已缓存 {len(schools) - len(todo)} 所")

        session = requests.Session()
        for index, (school_id, school_name) in enumerate(todo, start=1):
            cache[school_id] = _fetch_one(session, school_id, school_name, args.timeout)
            if index % 50 == 0:
                _save_cache(cache)
                row = cache[school_id]
                print(
                    f"  进度 {index}/{len(todo)} {school_name} "
                    f"招生:{bool(row.get('undergraduate_admission_url'))} "
                    f"官网:{bool(row.get('official_website_url'))}"
                )
            if args.delay > 0:
                time.sleep(args.delay)

        _save_cache(cache)
        updated = _apply_to_db(conn, cache)
        aliases = _apply_aliases(conn)
        overrides = _apply_overrides(conn)

        ok = sum(1 for row in cache.values() if row.get("status") == "ok")
        admission = sum(1 for row in cache.values() if row.get("undergraduate_admission_url"))
        official = sum(1 for row in cache.values() if row.get("official_website_url"))
        print("=== 院校官网链接抓取完成 ===")
        print(f"缓存成功: {ok}/{len(cache)}")
        print(f"招生官网: {admission}")
        print(f"学校官网: {official}")
        print(f"写入数据库: {updated} 行")
        print(f"别名映射: {aliases} 行")
        print(f"手工补充: {overrides} 行")


if __name__ == "__main__":
    main()
