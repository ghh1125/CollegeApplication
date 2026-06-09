"""抓各校保研率（recommend_master_rate），写入 school_profile。

源：static-data.gaokao.cn 学校详情 info.json（与 build_profiles 同源，较稳）。
字段：recommend_master_rate（保研/推免率，%）。学校级数据。

特性：断点续传（缓存 data/common/raw/baoyan_cache.json）；温和延时；
      抓完写入 common.db 与 data/zhejiang/college.db 的 school_profile（加列）。

运行：python scripts/fetch_baoyan_rate.py [--limit N] [--delay 1.5]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
COMMON_DB = PROJECT_ROOT / "data" / "common" / "common.db"
TARGET_DBS = [COMMON_DB, PROJECT_ROOT / "data" / "zhejiang" / "college.db"]
CACHE = PROJECT_ROOT / "data" / "common" / "raw" / "baoyan_cache.json"
INFO_URL = "https://static-data.gaokao.cn/www/2.0/school/{sid}/info.json"
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.gaokao.cn/"}


def _ensure_column(db: Path) -> None:
    if not db.exists():
        return
    conn = sqlite3.connect(db)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(school_profile)")]
    if "recommend_master_rate" not in cols:
        conn.execute("ALTER TABLE school_profile ADD COLUMN recommend_master_rate REAL")
        conn.commit()
    conn.close()


def _schools() -> list[tuple[str, str]]:
    conn = sqlite3.connect(COMMON_DB)
    rows = conn.execute(
        "SELECT school_id, school_name FROM school_profile WHERE school_id IS NOT NULL AND school_id!=''"
    ).fetchall()
    conn.close()
    return [(str(r[0]), r[1]) for r in rows]


def _fetch_rate(sid: str) -> float | None:
    try:
        r = requests.get(INFO_URL.format(sid=sid), headers=HEADERS, timeout=20)
        data = r.json().get("data", {})
        v = data.get("recommend_master_rate")
        return float(v) if v not in (None, "", "0", "0.00") else (0.0 if str(v) in ("0", "0.00") else None)
    except Exception:  # noqa: BLE001
        return None


def _apply(cache: dict) -> None:
    """把缓存里的保研率写进各目标库。"""
    for db in TARGET_DBS:
        if not db.exists():
            continue
        conn = sqlite3.connect(db)
        n = 0
        for sid, rate in cache.items():
            if rate is None:
                continue
            n += conn.execute(
                "UPDATE school_profile SET recommend_master_rate=? WHERE school_id=?", (rate, sid)
            ).rowcount
        conn.commit()
        conn.close()
        print(f"  写入 {db.name}: {n} 行")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--delay", type=float, default=1.5)
    args = ap.parse_args()

    for db in TARGET_DBS:
        _ensure_column(db)
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    cache: dict = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}

    schools = _schools()
    if args.limit:
        schools = schools[: args.limit]
    todo = [(s, n) for s, n in schools if s not in cache]
    print(f"学校 {len(schools)} 所，待抓 {len(todo)} 所（已缓存 {len(schools)-len(todo)}）")

    for i, (sid, name) in enumerate(todo, 1):
        cache[sid] = _fetch_rate(sid)
        if i % 50 == 0:
            CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
            print(f"  进度 {i}/{len(todo)}  最近: {name} {cache[sid]}")
        time.sleep(args.delay)

    CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    have = sum(1 for v in cache.values() if v is not None)
    print(f"抓取完成：{have}/{len(cache)} 所有保研率")
    _apply(cache)


if __name__ == "__main__":
    main()
