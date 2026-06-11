#!/usr/bin/env python3
"""从掌上高考 API 补全 admission_plan 的 tuition(学费) 和 duration(学制)。

按 school_name 匹配 school_id，再按专业名（归一化）匹配 API 返回条目。
"""

from __future__ import annotations
import json, re, sqlite3, sys, time
from pathlib import Path
import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "data" / "zhejiang" / "college.db"
SCHOOL_LIST = PROJECT_ROOT / "data" / "zhejiang" / "raw" / "plan_school_list.json"

API_BASE = "https://api.zjzw.cn/web/api/"
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.gaokao.cn/"}


def _norm(name: str) -> str:
    s = re.sub(r"[（(].*?[）)]", "", str(name or ""))
    return re.sub(r"[\s　]+", "", s).strip()


def _api_call(school_id: str, prov_id: int, page: int, year: int) -> list[dict] | None:
    """Single API call. Returns item list, or None if unsuccessful."""
    try:
        resp = requests.get(
            API_BASE,
            params={"local_province_id": prov_id, "page": page, "size": 100,
                    "school_id": school_id, "uri": "apidata/api/gkv3/plan/school",
                    "year": year},
            headers=HEADERS, timeout=20,
        )
        data = resp.json()
        if str(data.get("code", "")) != "0000":
            return None
        raw = data.get("data")
        if isinstance(raw, dict):
            return raw.get("item", [])
    except Exception:
        pass
    return None


# Province IDs to try in order: 浙江(33), 北京(11), 广东(44)
_PROV_FALLBACKS = [33, 11, 44]


def fetch_plan(school_id: str, year: int = 2025) -> list[dict]:
    items: list[dict] = []
    page = 1
    prov_id = 33

    while True:
        batch = None
        for prov in _PROV_FALLBACKS if page == 1 else [prov_id]:
            for _ in range(2):  # 2 retries per province
                batch = _api_call(school_id, prov, page, year)
                if batch is not None:
                    prov_id = prov  # stick with this province for subsequent pages
                    break
                time.sleep(1.5)
            if batch is not None:
                break
            time.sleep(1)

        if batch is None:
            return items
        items.extend(batch)
        if len(batch) < 100:
            return items
        page += 1
        time.sleep(0.3)


def main():
    school_list = json.loads(SCHOOL_LIST.read_text(encoding="utf-8"))
    name_to_sid = {s["name"]: s["school_id"] for s in school_list}

    conn = sqlite3.connect(DB_PATH)
    # Get all schools + their DB rows
    db_rows = conn.execute(
        "SELECT rowid, school_name, major_code, major_name FROM admission_plan WHERE year=2025"
    ).fetchall()

    # Group by school
    from collections import defaultdict
    school_rows: dict[str, list] = defaultdict(list)
    for rowid, sn, mc, mn in db_rows:
        school_rows[sn].append((rowid, mc, mn))

    total_updated = 0
    done = 0
    for sn, rows in school_rows.items():
        sid = name_to_sid.get(sn)
        if not sid:
            done += 1
            continue

        items = fetch_plan(sid)
        if not items:
            done += 1
            if done % 100 == 0:
                print(f"  {done}/{len(school_rows)} schools, updated={total_updated}", flush=True)
            time.sleep(0.3)
            continue

        # Build norm-name → (tuition, length) map from API
        api_map: dict[str, tuple] = {}
        for it in items:
            nm = _norm(it.get("spname", ""))
            tuition = it.get("tuition")
            length = it.get("length", "")
            if nm:
                api_map[nm] = (tuition, length)

        updates = []
        for rowid, mc, mn in rows:
            nm = _norm(mn)
            if nm in api_map:
                tuition, length = api_map[nm]
                updates.append((tuition, length, rowid))

        if updates:
            conn.executemany(
                "UPDATE admission_plan SET tuition=?, duration=? WHERE rowid=?", updates
            )
            conn.commit()
            total_updated += len(updates)

        done += 1
        if done % 50 == 0 or done == len(school_rows):
            print(f"  {done}/{len(school_rows)} schools, updated={total_updated}", flush=True)
        time.sleep(2.0)

    conn.close()
    print(f"\nDone. Updated {total_updated} rows.")


if __name__ == "__main__":
    main()
