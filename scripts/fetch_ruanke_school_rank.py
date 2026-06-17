"""从软科中国大学排名（2026）抓取所有子榜数据，写入 DB。

建表 ruanke_school_rank：每所学校 × 每个子榜一行
  (school_name, rank_type, rank_value, rank_overall, score, year)

同时用 rankOverall（主榜对应排名）更新 school_master.ruanke_rank
和 school_profile.ruanke_rank，使所有学校有统一可比的主榜排名。

子榜列表（URL = /rankings/bcur/2026{id}）：
  11  主榜        590 所
  21  医药         60 所
  745 中医药        25 所
  22  财经         54 所
  23  语言         15 所
  25  政法         34 所
  24  民族         14 所
  26  体育         15 所
  14  合作办学        9 所
  15  民办主榜      173 所
  16  民办财经       37 所
  17  民办语言       10 所
  30  艺术         50 所
  10  总榜        816 所（含所有子榜，用于核验）

运行:
    python scripts/fetch_ruanke_school_rank.py
    python scripts/fetch_ruanke_school_rank.py --skip-total  # 跳过总榜（加快速度）
    python scripts/fetch_ruanke_school_rank.py --dry-run
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH  = BASE_DIR / "data" / "zhejiang" / "college.db"

RANK_TYPES = [
    (11,  "主榜",       "bcur/2026"),
    (21,  "医药",       "bcur/202621"),
    (745, "中医药",     "bcur/2026745"),
    (22,  "财经",       "bcur/202622"),
    (23,  "语言",       "bcur/202623"),
    (25,  "政法",       "bcur/202625"),
    (24,  "民族",       "bcur/202624"),
    (26,  "体育",       "bcur/202626"),
    (14,  "合作办学",   "bcur/202614"),
    (15,  "民办主榜",   "bcur/202615"),
    (16,  "民办财经",   "bcur/202616"),
    (17,  "民办语言",   "bcur/202617"),
    (30,  "艺术",       "bcur/202630"),
    (10,  "总榜",       "bcur/202610"),  # 放最后，用于核验
]

BASE_URL = "https://www.shanghairanking.cn/rankings/"


def fetch_rank_type(browser, path: str, type_name: str) -> list[dict]:
    url = BASE_URL + path
    page = browser.new_page()
    try:
        page.goto(url, wait_until="networkidle", timeout=40_000)
        page.wait_for_timeout(1500)
        data = page.evaluate("""() =>
            window.__NUXT__.data[0].univData.map(u => ({
                name:        u.univNameCn,
                ranking:     u.ranking,
                rankOverall: u.rankOverall,
                score:       u.score,
            }))
        """)
        print(f"  [{type_name:6s}] {len(data):>4} 所  首条: {data[0]['name'] if data else '—'}")
        return data
    except Exception as e:
        print(f"  [{type_name:6s}] ERROR: {e}")
        return []
    finally:
        page.close()


def _to_int(val) -> int | None:
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def save_to_db(all_records: dict[str, list[dict]], year: int = 2026, dry_run: bool = False) -> None:
    conn = sqlite3.connect(str(DB_PATH))

    # 建表
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS ruanke_school_rank (
            school_name   TEXT    NOT NULL,
            rank_type     TEXT    NOT NULL,
            rank_value    INTEGER,
            rank_overall  INTEGER,
            score         REAL,
            year          INTEGER NOT NULL,
            PRIMARY KEY (school_name, rank_type, year)
        );
    """)

    if not dry_run:
        conn.execute("DELETE FROM ruanke_school_rank WHERE year=?", (year,))

    total_rows = 0
    # 用 rankOverall 更新 school_master / school_profile
    # 注意：民办/艺术子榜的 rankOverall 是民办体系内部排名，不是主榜排名，不能用
    MAIN_RANK_TYPES = {"主榜", "医药", "中医药", "财经", "语言", "政法", "民族", "体育", "合作办学"}
    overall_map: dict[str, int] = {}

    for type_name, records in all_records.items():
        for rec in records:
            name        = rec["name"]
            rank_value  = _to_int(rec.get("ranking"))
            rank_overall = _to_int(rec.get("rankOverall"))
            score       = rec.get("score")

            # 只有主榜体系的子榜才有可靠的 rankOverall（主榜绝对排名）
            if type_name in MAIN_RANK_TYPES and rank_overall and name not in overall_map:
                overall_map[name] = rank_overall
            if type_name == "主榜" and rank_value and name not in overall_map:
                overall_map[name] = rank_value

            if not dry_run:
                conn.execute("""
                    INSERT OR REPLACE INTO ruanke_school_rank
                        (school_name, rank_type, rank_value, rank_overall, score, year)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (name, type_name, rank_value, rank_overall, score, year))
                total_rows += 1

    if not dry_run:
        # 先清空两张表的 ruanke_rank（以 2026 为准）
        conn.execute("UPDATE school_master  SET ruanke_rank = NULL")
        conn.execute("UPDATE school_profile SET ruanke_rank = NULL")

        upd_m = upd_p = no_match = 0
        for name, rank in overall_map.items():
            r1 = conn.execute(
                "UPDATE school_master  SET ruanke_rank=? WHERE school_name=?", (rank, name)
            ).rowcount
            r2 = conn.execute(
                "UPDATE school_profile SET ruanke_rank=? WHERE school_name=?", (rank, name)
            ).rowcount
            upd_m += r1
            upd_p += r2
            if r1 == 0:
                no_match += 1
                print(f"    [NO MATCH school_master] {name}  rank={rank}")

        conn.commit()
        print(f"\n写入 ruanke_school_rank: {total_rows} 行")
        print(f"更新 school_master.ruanke_rank: {upd_m} 所")
        print(f"更新 school_profile.ruanke_rank: {upd_p} 所")
        if no_match:
            print(f"⚠️  未匹配 school_master: {no_match} 所（见上方）")
    else:
        print(f"\n[dry-run] 共 {sum(len(v) for v in all_records.values())} 条记录，{len(overall_map)} 所学校有主榜排名")

    conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run",    action="store_true")
    parser.add_argument("--skip-total", action="store_true", help="跳过总榜（仅核验用，跳过可加快速度）")
    args = parser.parse_args()

    from playwright.sync_api import sync_playwright

    types_to_fetch = [
        t for t in RANK_TYPES
        if not (args.skip_total and t[1] == "总榜")
    ]

    all_records: dict[str, list[dict]] = {}

    print("抓取各子榜数据…")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for _, type_name, path in types_to_fetch:
            records = fetch_rank_type(browser, path, type_name)
            all_records[type_name] = records
        browser.close()

    print(f"\n共抓取 {len(all_records)} 个子榜")
    save_to_db(all_records, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.path.insert(0, str(BASE_DIR))
    main()
