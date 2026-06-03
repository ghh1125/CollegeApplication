"""Fetch Jiangsu plan details for schools appearing in official cutoff files.

Official jseea cutoff data is authoritative for group thresholds, but it does
not list the majors inside each 院校专业组. This script fills that gap by
targeting only the schools present in data/jiangsu/raw/official/cutoff_*.csv
and fetching plan rows from the 掌上高考 plan endpoint.

Output:
    data/jiangsu/raw/plan/{year}/{school_id}.json

Run:
    python scripts/fetch_jiangsu_plans.py
    python scripts/fetch_jiangsu_plans.py --years 2025 --limit 20
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from pathlib import Path

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = PROJECT_ROOT / "data" / "jiangsu" / "raw"
OFFICIAL_DIR = RAW_DIR / "official"
SCHOOL_LIST_PATH = RAW_DIR / "school_list.json"

API_BASE = "https://api.zjzw.cn/web/api/"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36"
    ),
    "Referer": "https://www.gaokao.cn/",
}
JIANGSU_PROVINCE_ID = 32
PLAN_URI = "apidata/api/gkv3/plan/school"
DEFAULT_YEARS = (2025, 2024, 2023)
PAGE_SIZE = 100
DEFAULT_DELAY = 1.2
BACKOFFS = (3, 8, 20, 45)


def normalize_school_name(name: str | None) -> str:
    text = str(name or "").strip()
    text = text.replace("（", "(").replace("）", ")")
    text = re.sub(r"\s+", "", text)
    return text


def official_school_names(year: int) -> set[str]:
    names: set[str] = set()
    for path in OFFICIAL_DIR.glob(f"cutoff_{year}_*.csv"):
        with path.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                name = (row.get("school_name") or "").strip()
                if name:
                    names.add(name)
    return names


def load_school_id_map() -> dict[str, dict[str, str]]:
    if not SCHOOL_LIST_PATH.exists():
        raise FileNotFoundError(f"missing school list: {SCHOOL_LIST_PATH}")
    schools = json.loads(SCHOOL_LIST_PATH.read_text(encoding="utf-8"))
    mapping: dict[str, dict[str, str]] = {}
    for item in schools:
        name = item.get("name") or ""
        sid = str(item.get("school_id") or "")
        if name and sid:
            mapping[normalize_school_name(name)] = {"school_id": sid, "name": name}
    return mapping


def _get(params: dict) -> dict:
    """GET one API page with throttling backoff."""
    for wait in (0, *BACKOFFS):
        if wait:
            time.sleep(wait)
        try:
            resp = requests.get(API_BASE, params=params, headers=HEADERS, timeout=30)
            payload = resp.json()
            if str(payload.get("code")) == "0000" and isinstance(payload.get("data"), dict):
                return payload["data"]
        except Exception as exc:  # noqa: BLE001
            print(f"    请求异常: {exc}", file=sys.stderr)
    raise RuntimeError(f"API throttled or failed: {params}")


def fetch_plan_pages(school_id: str, year: int, delay: float) -> list[dict]:
    rows: list[dict] = []
    page = 1
    while True:
        data = _get(
            {
                "local_province_id": JIANGSU_PROVINCE_ID,
                "page": page,
                "size": PAGE_SIZE,
                "school_id": school_id,
                "uri": PLAN_URI,
                "year": year,
            }
        )
        items = data.get("item", [])
        if not items:
            break
        rows.extend(items)
        num_found = int(data.get("numFound") or 0)
        if len(rows) >= num_found or len(items) < PAGE_SIZE:
            break
        page += 1
        time.sleep(delay)
    return rows


def fetch_one(school: dict[str, str], year: int, delay: float, force: bool = False) -> int:
    out_dir = RAW_DIR / "plan" / str(year)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{school['school_id']}.json"
    if out_path.exists() and not force:
        return -1

    items = fetch_plan_pages(school["school_id"], year, delay)
    out_path.write_text(
        json.dumps(
            {
                "school": school,
                "year": year,
                "source": "zsgk_plan_targeted",
                "items": items,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    time.sleep(delay)
    return len(items)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=int, nargs="+", default=list(DEFAULT_YEARS))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    school_map = load_school_id_map()
    tasks: list[tuple[int, str, dict[str, str]]] = []
    unmatched: dict[int, list[str]] = {}
    for year in args.years:
        names = sorted(official_school_names(year))
        missing: list[str] = []
        for official_name in names:
            school = school_map.get(normalize_school_name(official_name))
            if school:
                tasks.append((year, official_name, school))
            else:
                missing.append(official_name)
        unmatched[year] = missing

    if args.limit:
        tasks = tasks[: args.limit]

    print(f"待抓取 school-year: {len(tasks)}")
    for year, missing in unmatched.items():
        if missing:
            print(f"{year} 未匹配学校 {len(missing)} 所，例如：{missing[:10]}")

    done = skipped = failed = 0
    for index, (year, official_name, school) in enumerate(tasks, start=1):
        try:
            count = fetch_one(school, year, args.delay, force=args.force)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"[{index}/{len(tasks)}] {year} {official_name}: 失败 {exc}")
            continue
        if count == -1:
            skipped += 1
            continue
        done += 1
        print(
            f"[{index}/{len(tasks)}] {year} {official_name} "
            f"({school['school_id']}): {count} 条"
        )

    print(f"完成：新增/更新 {done}，跳过 {skipped}，失败 {failed}")


if __name__ == "__main__":
    main()
