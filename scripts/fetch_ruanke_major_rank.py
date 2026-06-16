"""
软科中国大学专业排名（bcmr）2026 爬取脚本。

流程：
  1. 用 Playwright 加载总览页，提取 838 个专业的 code/name/门类/专业类
  2. 对每个专业直接调用 /api/pub/v1/bcmr/rank?year=2026&majorCode={code}
  3. 写入 college.db 的 ruanke_major_rank 表

表结构：
  major_code       TEXT    专业代码（如 010101）
  major_name       TEXT    专业名（如 哲学）
  cat_name         TEXT    门类（如 哲学）
  cls_name         TEXT    专业类（如 哲学类）
  school_name      TEXT    学校名
  province         TEXT    省份
  ranking          TEXT    名次（可能是 "1" "2-5" 等区间）
  grade            TEXT    评级（A+/A/B+/B/C+/C）
  score            REAL    得分
  year             INTEGER 年份

运行：
  uv run python scripts/fetch_ruanke_major_rank.py
  uv run python scripts/fetch_ruanke_major_rank.py --major 010101  # 只跑指定专业
"""

import argparse
import asyncio
import json
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))


# ── 数据库 ────────────────────────────────────────────────────────────────────

def init_table(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS ruanke_major_rank (
            major_code  TEXT NOT NULL,
            major_name  TEXT NOT NULL,
            cat_name    TEXT,
            cls_name    TEXT,
            school_name TEXT NOT NULL,
            province    TEXT,
            ranking     TEXT,
            grade       TEXT,
            score       REAL,
            year        INTEGER NOT NULL DEFAULT 2026,
            PRIMARY KEY (major_code, school_name, year)
        );
    """)
    conn.commit()


def upsert_rows(conn, rows: list[dict]):
    conn.executemany("""
        INSERT INTO ruanke_major_rank
            (major_code, major_name, cat_name, cls_name, school_name,
             province, ranking, grade, score, year)
        VALUES
            (:major_code, :major_name, :cat_name, :cls_name, :school_name,
             :province, :ranking, :grade, :score, :year)
        ON CONFLICT(major_code, school_name, year) DO UPDATE SET
            ranking = excluded.ranking,
            grade   = excluded.grade,
            score   = excluded.score
    """, rows)
    conn.commit()


# ── 专业列表（Playwright）────────────────────────────────────────────────────

async def fetch_major_list() -> list[dict]:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto("https://www.shanghairanking.cn/rankings/bcmr/2026",
                        wait_until="networkidle", timeout=40000)
        await asyncio.sleep(2)

        majors = await page.evaluate("""
            () => {
                const result = [];
                function walk(node, catName, clsName) {
                    if (!node) return;
                    if (node.children === null) {
                        if (node.univPublished > 0)
                            result.push({
                                code: node.code,
                                name: node.name,
                                cat_name: catName,
                                cls_name: clsName,
                                count: node.univPublished
                            });
                        return;
                    }
                    for (const child of (node.children || [])) {
                        const isCat = node.pid === 0;
                        walk(child,
                             isCat ? node.name : catName,
                             isCat ? '' : (node.children[0]?.pid !== 0 ? node.name : clsName));
                    }
                }
                for (const cat of window.__NUXT__.data[0].subjData) walk(cat, '', '');
                return result;
            }
        """)
        await browser.close()
    return majors


# ── 单专业排名（直接 API）────────────────────────────────────────────────────

_SSL_CTX = ssl._create_unverified_context()
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://www.shanghairanking.cn/rankings/bcmr/2026/",
}


def fetch_major_rank(code: str, year: int = 2026, retries: int = 3) -> list[dict]:
    url = f"https://www.shanghairanking.cn/api/pub/v1/bcmr/rank?year={year}&majorCode={code}"
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=_HEADERS)
            with urllib.request.urlopen(req, timeout=15, context=_SSL_CTX) as r:
                body = json.load(r)
            return body.get("data", {}).get("rankings", [])
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(5 * (attempt + 1))
            else:
                raise
        except Exception:
            if attempt < retries - 1:
                time.sleep(2)
            else:
                raise
    return []


# ── 主流程 ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--major", help="只爬指定专业代码（调试用）")
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--delay", type=float, default=0.5, help="请求间隔秒数")
    args = parser.parse_args()

    import sqlite3
    from db import get_db_path

    db_path = get_db_path("zhejiang")
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")

    with conn:
        init_table(conn)

        print("Step 1: 获取专业列表（Playwright）…")
        all_majors: list[dict] = asyncio.run(fetch_major_list())
        print(f"  共 {len(all_majors)} 个专业")

        if args.major:
            all_majors = [m for m in all_majors if m["code"] == args.major]
            if not all_majors:
                print(f"专业代码 {args.major} 未找到")
                return

        print(f"\nStep 2: 批量拉取排名（{len(all_majors)} 个专业）…")
        total_rows = 0
        for i, maj in enumerate(all_majors, 1):
            code, name = maj["code"], maj["name"]
            try:
                rankings = fetch_major_rank(code, args.year)
                rows = [
                    {
                        "major_code":  code,
                        "major_name":  name,
                        "cat_name":    maj.get("cat_name", ""),
                        "cls_name":    maj.get("cls_name", ""),
                        "school_name": r["univNameCn"],
                        "province":    r.get("province", ""),
                        "ranking":     r.get("ranking", ""),
                        "grade":       r.get("grade", ""),
                        "score":       r.get("score"),
                        "year":        args.year,
                    }
                    for r in rankings
                ]
                if rows:
                    upsert_rows(conn, rows)
                    total_rows += len(rows)
                print(f"  [{i:3d}/{len(all_majors)}] {code} {name:<20} {len(rows):3d}所")
            except Exception as e:
                print(f"  [{i:3d}/{len(all_majors)}] {code} {name} ERROR: {e}")

            if i % 50 == 0:
                print(f"  --- 已完成 {i}/{len(all_majors)}，累计 {total_rows} 行 ---")
            time.sleep(args.delay)

        print(f"\n完成！共写入 {total_rows} 行 → ruanke_major_rank 表")


if __name__ == "__main__":
    main()
