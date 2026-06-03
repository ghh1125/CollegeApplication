"""Fetch Jiangsu 院校专业组 admission data from 掌上高考 (api.zjzw.cn).

For each school admitting in Jiangsu, pulls two endpoints per year:
  - apidata/api/gkv3/plan/school   招生计划（计划数、学费、专业组）
  - apidata/api/gk/score/special   历年录取（每专业录取位次、专业组）

Both carry subject category (物理类/历史类) and 专业组 fields (sg_name / sg_info /
special_group), which is everything the 院校专业组 recommendation model needs.

Design:
  - Resumable: one JSON file per (school_id, year, api); skips files already present.
  - Polite: fixed delay + exponential backoff on empty/error responses (the API
    throttles aggressive callers).
  - data lands in data/jiangsu/raw/{plan,score}/{year}/{school_id}.json

Usage:
  python scripts/fetch_jiangsu.py                 # full scrape, years 2024/2023/2022
  python scripts/fetch_jiangsu.py --limit 80      # first 80 schools (pipeline validation)
  python scripts/fetch_jiangsu.py --years 2024    # single year
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = PROJECT_ROOT / "data" / "jiangsu" / "raw"

API_BASE = "https://api.zjzw.cn/web/api/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://www.gaokao.cn/",
}
JIANGSU_PROVINCE_ID = 32
SCHOOL_LIST_URI = "apidata/api/gk/school/lists"
PLAN_URI = "apidata/api/gkv3/plan/school"
SCORE_URI = "apidata/api/gk/score/special"

DEFAULT_YEARS = (2025, 2024, 2023)
BASE_DELAY = 18.0         # 超慢速避限频
THROTTLE_SLEEPS = (20, 45, 90, 180)      # escalating waits if throttle still trips
PAGE_SIZE = 100           # per-school plan/score rows per page


class ThrottledError(Exception):
    """API returned the throttle signal repeatedly."""


def _get(params: dict) -> dict:
    """GET one page. Returns the 'data' dict on success.

    The API signals throttling by returning data as an empty *list* (or a non-0000
    code). A genuine no-data result is a *dict* with numFound==0. On throttle we
    sleep with escalating backoff and retry; if it never clears, raise ThrottledError
    so the caller can skip writing (and a later run resumes).
    """
    for wait in (0, *THROTTLE_SLEEPS):
        if wait:
            time.sleep(wait)
        try:
            resp = requests.get(API_BASE, params=params, headers=HEADERS, timeout=30)
            payload = resp.json()
            code = str(payload.get("code", ""))
            data = payload.get("data", {})
            if code == "0000" and isinstance(data, dict):
                return data  # success (item present, or genuine numFound==0)
            # list data / non-0000 → throttled, escalate
        except Exception as exc:  # noqa: BLE001
            print(f"    请求异常: {exc}", file=sys.stderr)
    raise ThrottledError(str(params.get("uri")))


def fetch_school_list() -> list[dict]:
    """Page through the national school list (schools that may admit in Jiangsu)."""
    schools: list[dict] = []
    page = 1
    while True:
        params = {
            "local_province_id": JIANGSU_PROVINCE_ID,
            "page": page,
            "size": 30,           # API hard-caps the list endpoint at 30 items/page
            "uri": SCHOOL_LIST_URI,
            "year": DEFAULT_YEARS[0],
        }
        data = _get(params)
        items = data.get("item", []) if isinstance(data, dict) else []
        if not items:
            break
        for it in items:
            sid = it.get("school_id")
            if sid:
                schools.append({"school_id": str(sid), "name": it.get("name", "")})
        num_found = data.get("numFound", 0)
        if page % 20 == 0 or len(schools) >= num_found:
            print(f"  学校列表 page {page}: 累计 {len(schools)}/{num_found}")
        if len(schools) >= num_found:
            break
        page += 1
        time.sleep(BASE_DELAY)
    return schools


def _fetch_paged(uri: str, school_id: str, year: int) -> list[dict]:
    """Fetch all pages of plan/score for one school+year (loose: no local_type_id,
    so both 物理类 and 历史类 come back; caller filters by local_type_name)."""
    all_items: list[dict] = []
    page = 1
    while True:
        params = {
            "local_province_id": JIANGSU_PROVINCE_ID,
            "page": page,
            "size": PAGE_SIZE,
            "school_id": school_id,
            "uri": uri,
            "year": year,
        }
        data = _get(params)
        items = data.get("item", []) if isinstance(data, dict) else []
        if not items:
            break
        all_items.extend(items)
        num_found = data.get("numFound", 0)
        if len(all_items) >= num_found or len(items) < PAGE_SIZE:
            break
        page += 1
        time.sleep(BASE_DELAY)
    return all_items


def fetch_one(uri_key: str, uri: str, school: dict, year: int) -> int:
    """Fetch+save one (school, year, api). Returns row count; -1 if already done,
    -2 if throttled (not written, so a rerun resumes)."""
    out_dir = RAW_DIR / uri_key / str(year)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{school['school_id']}.json"
    if out_path.exists():
        return -1  # already done
    try:
        items = _fetch_paged(uri, school["school_id"], year)
    except ThrottledError:
        return -2  # don't write; resume later
    out_path.write_text(
        json.dumps({"school": school, "year": year, "items": items}, ensure_ascii=False),
        encoding="utf-8",
    )
    time.sleep(BASE_DELAY)
    return len(items)


def _schools_with_data(year: int) -> set[str]:
    """School IDs that produced non-empty plan or score data for `year`."""
    ids: set[str] = set()
    for kind in ("plan", "score"):
        d = RAW_DIR / kind / str(year)
        if not d.exists():
            continue
        for f in d.glob("*.json"):
            try:
                if json.loads(f.read_text(encoding="utf-8")).get("items"):
                    ids.add(f.stem)
            except Exception:  # noqa: BLE001
                continue
    return ids


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="只抓前 N 所学校（验证用）")
    parser.add_argument("--years", type=int, nargs="+", default=list(DEFAULT_YEARS))
    args = parser.parse_args()

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    school_list_path = RAW_DIR / "school_list.json"
    if school_list_path.exists():
        schools = json.loads(school_list_path.read_text(encoding="utf-8"))
        print(f"复用已缓存学校列表：{len(schools)} 所")
    else:
        print("抓取江苏招生学校列表…")
        schools = fetch_school_list()
        school_list_path.write_text(json.dumps(schools, ensure_ascii=False), encoding="utf-8")
        print(f"学校列表已保存：{len(schools)} 所")

    if args.limit:
        schools = schools[: args.limit]
        print(f"限制为前 {len(schools)} 所")

    years = sorted(args.years, reverse=True)   # latest year first
    latest = years[0]

    def _scrape(school_subset: list[dict], year: int) -> None:
        nonlocal done, throttled
        for school in school_subset:
            for uri_key, uri in (("plan", PLAN_URI), ("score", SCORE_URI)):
                n = fetch_one(uri_key, uri, school, year)
                if n == -1:
                    continue
                if n == -2:
                    throttled += 1
                    print(f"[{year}] {school['name']}({school['school_id']}) {uri_key}: 限频跳过")
                else:
                    print(f"[{year}] {school['name']}({school['school_id']}) {uri_key}: {n}条")
            done += 1
            if done % 50 == 0:
                print(f"--- 进度 {done} 校年, 限频 {throttled} ---")

    done = throttled = 0
    # Pass 1: latest year, all schools (discovers which schools admit in 江苏)
    print(f"=== 第一遍：{latest} 年全部 {len(schools)} 所 ===")
    _scrape(schools, latest)

    # Pass 2: earlier years, only schools that had data in the latest year (跳空优化)
    if len(years) > 1:
        with_data = _schools_with_data(latest)
        active = [s for s in schools if s["school_id"] in with_data]
        print(f"=== 跳空优化：{latest} 年有数据的 {len(active)}/{len(schools)} 所，"
              f"用于 {years[1:]} 年 ===")
        for year in years[1:]:
            print(f"=== {year} 年 {len(active)} 所 ===")
            _scrape(active, year)

    print(f"完成。限频跳过 {throttled} 项，重跑本脚本可续抓。")


if __name__ == "__main__":
    main()
