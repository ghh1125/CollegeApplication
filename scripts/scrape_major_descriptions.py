"""
Scrape major description text from static-data.gaokao.cn.

Each standard major has a special_id and a description page containing:
  is_what    — what the major studies (includes "关键词：..." at the end)
  learn_what — main courses
  do_what    — career paths

Run:
    python scripts/scrape_major_descriptions.py
"""

from __future__ import annotations

import json
import re
import sqlite3
import ssl
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.request import urlopen

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "data" / "college.db"

BASE_URL = "https://static-data.gaokao.cn/www/2.0/special/{}/info.json"
MAX_ID = 2000
WORKERS = 60
TIMEOUT = 8

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE


def _extract_keywords(is_what: str) -> str:
    """Extract the '关键词：...' line from is_what text."""
    m = re.search(r"关键词[：:](.*)", is_what or "")
    return m.group(1).strip() if m else ""


def _fetch_special(special_id: int) -> Optional[dict]:
    url = BASE_URL.format(special_id)
    try:
        with urlopen(url, timeout=TIMEOUT, context=SSL_CTX) as r:
            payload = json.loads(r.read())
    except Exception:
        return None

    d = payload.get("data") or {}
    name = d.get("name", "").strip()
    if not name:
        return None

    is_what = d.get("is_what") or ""
    return {
        "special_id":    special_id,
        "name":          name,
        "national_code": d.get("code", ""),
        "level1":        d.get("level1_name") or d.get("level1") or "",
        "level2":        d.get("level2") or "",
        "level3":        d.get("level3") or "",
        "is_what":       is_what,
        "learn_what":    d.get("learn_what") or "",
        "do_what":       d.get("do_what") or "",
        "keywords":      _extract_keywords(is_what),
    }


def _upsert(conn: sqlite3.Connection, row: dict) -> None:
    conn.execute(
        """
        INSERT INTO major_description
            (special_id, name, national_code, level1, level2, level3,
             is_what, learn_what, do_what, keywords, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(special_id) DO UPDATE SET
            name=excluded.name, national_code=excluded.national_code,
            level1=excluded.level1, level2=excluded.level2, level3=excluded.level3,
            is_what=excluded.is_what, learn_what=excluded.learn_what,
            do_what=excluded.do_what, keywords=excluded.keywords,
            updated_at=excluded.updated_at
        """,
        (
            row["special_id"], row["name"], row["national_code"],
            row["level1"], row["level2"], row["level3"],
            row["is_what"], row["learn_what"], row["do_what"],
            row["keywords"], datetime.now().isoformat(timespec="seconds"),
        ),
    )


def main() -> None:
    print(f"Scanning gaokao.cn special IDs 1–{MAX_ID} …")
    results: list[dict] = []

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futures = {ex.submit(_fetch_special, i): i for i in range(1, MAX_ID + 1)}
        done = 0
        for fut in as_completed(futures):
            done += 1
            row = fut.result()
            if row:
                results.append(row)
            if done % 200 == 0:
                print(f"  {done}/{MAX_ID} scanned, {len(results)} found …")

    print(f"Found {len(results)} specialties. Writing to DB …")
    conn = sqlite3.connect(DB_PATH)
    try:
        for row in results:
            _upsert(conn, row)
        conn.commit()
    finally:
        conn.close()

    print(f"Done. {len(results)} rows upserted.")

    # Quick verification
    conn = sqlite3.connect(DB_PATH)
    total = conn.execute("SELECT COUNT(*) FROM major_description").fetchone()[0]
    sample = conn.execute(
        "SELECT special_id, name, keywords FROM major_description "
        "WHERE name LIKE '%计算机%' ORDER BY special_id LIMIT 5"
    ).fetchall()
    conn.close()
    print(f"DB total: {total}")
    print("Sample (计算机):")
    for r in sample:
        print(f"  id={r[0]}  name={r[1]}  kw={r[2]}")


if __name__ == "__main__":
    main()
