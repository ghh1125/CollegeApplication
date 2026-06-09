"""抓浙江招生计划（掌上高考 plan 接口）：学费/学制/选科/计划数/专业。

只抓能结构化拿到的：tuition(学费)、length(学制)、sp_info/sp_xuanke(选科)、num(计划数)。
历史录取分数/位次已在 historical_cutoff，本脚本不抓 score。

特性：
  - 温和慢爬 + 限频指数退避；断点续传（一个 school_id 一个 JSON，已存跳过）。
  - 数据落地 data/zhejiang/raw/plan/{year}/{school_id}.json

运行：
  python scripts/fetch_zhejiang_plans.py                # 全量，默认 2025
  python scripts/fetch_zhejiang_plans.py --limit 10     # 先抓前 10 校验证
  python scripts/fetch_zhejiang_plans.py --years 2025 2024
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = PROJECT_ROOT / "data" / "zhejiang" / "raw"

API_BASE = "https://api.zjzw.cn/web/api/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://www.gaokao.cn/",
}
ZHEJIANG_PROVINCE_ID = 33
SCHOOL_LIST_URI = "apidata/api/gk/school/lists"
PLAN_URI = "apidata/api/gkv3/plan/school"

DEFAULT_YEARS = (2025,)
BASE_DELAY = 6.0
THROTTLE_SLEEPS = (20, 45, 90, 180)
PAGE_SIZE = 100


class ThrottledError(Exception):
    pass


def _get(params: dict) -> dict:
    for wait in (0, *THROTTLE_SLEEPS):
        if wait:
            time.sleep(wait)
        try:
            resp = requests.get(API_BASE, params=params, headers=HEADERS, timeout=30)
            payload = resp.json()
            if str(payload.get("code", "")) == "0000" and isinstance(payload.get("data"), dict):
                return payload["data"]
        except Exception as exc:  # noqa: BLE001
            print(f"    请求异常: {exc}", file=sys.stderr)
    raise ThrottledError(str(params.get("uri")))


def fetch_school_list() -> list[dict]:
    schools: list[dict] = []
    page = 1
    while True:
        data = _get({"local_province_id": ZHEJIANG_PROVINCE_ID, "page": page, "size": 30,
                     "uri": SCHOOL_LIST_URI, "year": DEFAULT_YEARS[0]})
        items = data.get("item", [])
        if not items:
            break
        for it in items:
            if it.get("school_id"):
                schools.append({"school_id": str(it["school_id"]), "name": it.get("name", "")})
        num_found = data.get("numFound", 0)
        if page % 20 == 0 or len(schools) >= num_found:
            print(f"  学校列表 page {page}: 累计 {len(schools)}/{num_found}")
        if len(schools) >= num_found:
            break
        page += 1
        time.sleep(BASE_DELAY)
    return schools


def _fetch_plan(school_id: str, year: int) -> list[dict]:
    all_items: list[dict] = []
    page = 1
    while True:
        data = _get({"local_province_id": ZHEJIANG_PROVINCE_ID, "page": page, "size": PAGE_SIZE,
                     "school_id": school_id, "uri": PLAN_URI, "year": year})
        items = data.get("item", [])
        if not items:
            break
        all_items.extend(items)
        num_found = data.get("numFound", 0)
        if len(all_items) >= num_found or len(items) < PAGE_SIZE:
            break
        page += 1
        time.sleep(BASE_DELAY)
    return all_items


def fetch_one(school: dict, year: int) -> int:
    out_dir = RAW_DIR / "plan" / str(year)
    out_dir.mkdir(parents=True, exist_ok=True)
    f = out_dir / f"{school['school_id']}.json"
    if f.exists():
        return -1
    try:
        items = _fetch_plan(school["school_id"], year)
    except ThrottledError:
        return -2
    f.write_text(json.dumps({"school": school, "year": year, "items": items},
                            ensure_ascii=False), encoding="utf-8")
    time.sleep(BASE_DELAY)
    return len(items)


def _schools_with_data(year: int) -> set[str]:
    ids: set[str] = set()
    d = RAW_DIR / "plan" / str(year)
    if d.exists():
        for f in d.glob("*.json"):
            try:
                if json.loads(f.read_text(encoding="utf-8")).get("items"):
                    ids.add(f.stem)
            except Exception:  # noqa: BLE001
                continue
    return ids


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="只抓前 N 所（验证用）")
    parser.add_argument("--years", type=int, nargs="+", default=list(DEFAULT_YEARS))
    args = parser.parse_args()

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    list_path = RAW_DIR / "plan_school_list.json"
    if list_path.exists():
        schools = json.loads(list_path.read_text(encoding="utf-8"))
        print(f"复用学校列表：{len(schools)} 所")
    else:
        print("抓取浙江招生学校列表…")
        schools = fetch_school_list()
        list_path.write_text(json.dumps(schools, ensure_ascii=False), encoding="utf-8")
        print(f"学校列表已保存：{len(schools)} 所")

    if args.limit:
        schools = schools[: args.limit]
        print(f"限制为前 {len(schools)} 所")

    years = sorted(args.years, reverse=True)
    done = throttled = 0

    def _scrape(subset: list[dict], year: int) -> None:
        nonlocal done, throttled
        for s in subset:
            n = fetch_one(s, year)
            if n == -1:
                continue
            if n == -2:
                throttled += 1
                print(f"[{year}] {s['name']}({s['school_id']}): 限频跳过")
            else:
                print(f"[{year}] {s['name']}({s['school_id']}): {n}条")
            done += 1
            if done % 50 == 0:
                print(f"--- 进度 {done}，限频 {throttled} ---")

    latest = years[0]
    print(f"=== {latest} 年全部 {len(schools)} 所 ===")
    _scrape(schools, latest)
    if len(years) > 1:
        active = [s for s in schools if s["school_id"] in _schools_with_data(latest)]
        print(f"=== 跳空优化：{latest} 有数据 {len(active)} 所，用于 {years[1:]} ===")
        for y in years[1:]:
            _scrape(active, y)

    print(f"完成。限频跳过 {throttled} 项，重跑可续抓。")


if __name__ == "__main__":
    main()
