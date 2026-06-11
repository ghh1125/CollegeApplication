#!/usr/bin/env python3
"""Scrape 2026 admission charters from api.51sdx.com.

Scans charter IDs in range [START_ID, END_ID], saves to admission_charter table.
Only processes 普通类 (regular college entrance) charters; skips arts, sports,
保送, 港澳台, 春季, 高职, etc.

Resumes automatically (already-ok 普通类 records are skipped).

Usage:
    python scripts/scrape_51sdx_charters.py
    python scripts/scrape_51sdx_charters.py --start 54000 --end 59400
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import time
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.scrape_chsi_admission_charters import (
    COMMON_DB, ZHEJIANG_DB,
    ensure_admission_charter_table, upsert_admission_charters,
)

API = "https://api.51sdx.com/niuzy/report/getZszc"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://51sdx.com/",
}
TARGET_YEAR = 2026
DELAY = 1.0  # seconds between requests

# Only accept regular college-entrance charters; skip all special tracks.
ALLOWED_ZCTYPE = {"普通类"}


# ── already-fetched lookup ─────────────────────────────────────────────────────

def _fetched_names(year: int = TARGET_YEAR) -> set[str]:
    """School names that already have a 普通类 charter in DB."""
    names: set[str] = set()
    for db in (COMMON_DB, ZHEJIANG_DB):
        try:
            with sqlite3.connect(db) as conn:
                # For 51sdx records we stored zcType in ocr_status; for other
                # sources (chsi etc.) any ok record counts as done.
                for (sn,) in conn.execute(
                    """SELECT school_name FROM admission_charter
                       WHERE year=? AND fetch_status='ok'
                         AND (source_name != '51sdx' OR ocr_status = '普通类')""",
                    (year,)
                ):
                    names.add(sn)
        except Exception:
            pass
    return names


# ── HTML → structured sections ─────────────────────────────────────────────────

def _strip_html(html: str) -> str:
    text = re.sub(r"<[^>]+>", "", html)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return re.sub(r"\s+", " ", text).strip()


_TUITION_SECTION_RE = re.compile(
    r"(?:收费标准|学费标准|学费和住宿费|学费)[^\n]{0,5}\n?(.{20,600}?)(?=第\s*\d+\s*条|$)",
    re.DOTALL,
)
_PHYSICAL_SECTION_RE = re.compile(
    r"(?:体检标准|健康状况要求|体检要求|身体要求)[^\n]{0,5}\n?(.{20,800}?)(?=第\s*\d+\s*条|$)",
    re.DOTALL,
)
_LANG_SECTION_RE = re.compile(
    r"(?:外语语种要求|语种要求|外语要求)[^\n]{0,5}\n?(.{20,400}?)(?=第\s*\d+\s*条|$)",
    re.DOTALL,
)


def _extract_sections(text: str) -> dict[str, str]:
    """Extract tuition / physical / language sections from charter plain text."""
    def _grab(pattern):
        m = pattern.search(text)
        if m:
            s = re.sub(r"\s+", " ", m.group(1)).strip()
            return s[:600]
        return ""

    def _around(keyword: str, window: int = 300) -> str:
        idx = text.find(keyword)
        if idx < 0:
            return ""
        return text[max(0, idx - 20): idx + window].strip()

    tuition = _grab(_TUITION_SECTION_RE) or _around("学费")
    physical = _grab(_PHYSICAL_SECTION_RE) or _around("体检")
    language = _grab(_LANG_SECTION_RE) or _around("外语语种")

    return {
        "tuition_text": tuition[:500],
        "physical_requirement_text": physical[:600],
        "language_requirement_text": language[:400],
        "admission_rules_text": text[:2000],  # full text for subject score parsing
    }


# ── main ───────────────────────────────────────────────────────────────────────

def fetch_charter(charter_id: int, retries: int = 3) -> dict | None:
    for attempt in range(retries):
        try:
            r = requests.get(
                API,
                params={"id": charter_id, "channel": "sdxpc", "sourceId": 1},
                headers=HEADERS,
                timeout=15,
            )
            d = r.json()
            if d.get("resultCode") == "0000":
                return d.get("zc") or d.get("data")
        except Exception:
            pass
        if attempt < retries - 1:
            time.sleep(2)
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=int, default=54000)
    parser.add_argument("--end", type=int, default=59400)
    args = parser.parse_args()

    already_ok = _fetched_names()
    print(f"Already in DB (普通类 ok): {len(already_ok)} schools")

    # Ensure tables exist
    for db in (COMMON_DB, ZHEJIANG_DB):
        with sqlite3.connect(db) as conn:
            ensure_admission_charter_table(conn)

    total = args.end - args.start + 1
    found = skipped = inserted = skipped_type = errors = 0

    for cid in range(args.start, args.end + 1):
        zc = fetch_charter(cid)
        if not zc:
            errors += 1
            if (cid - args.start) % 200 == 0:
                print(f"  [{cid}] no data ({errors} errors so far)", flush=True)
            time.sleep(DELAY)
            continue

        year = int(zc.get("year") or 0)
        if year != TARGET_YEAR:
            time.sleep(DELAY * 0.3)
            continue

        zc_type = (zc.get("zcType") or "").strip()
        if zc_type not in ALLOWED_ZCTYPE:
            skipped_type += 1
            time.sleep(DELAY * 0.3)
            continue

        school_name = (zc.get("univName") or "").strip()
        if not school_name:
            time.sleep(DELAY)
            continue

        found += 1

        if school_name in already_ok:
            skipped += 1
            time.sleep(DELAY * 0.3)
            continue

        html = zc.get("zcHtml") or zc.get("zc") or ""
        text = _strip_html(html)
        sections = _extract_sections(text)

        row = {
            "school_name": school_name,
            "school_id": str(zc.get("univId") or ""),
            "year": TARGET_YEAR,
            "fetch_status": "ok",
            "source_url": f"https://51sdx.com/zszc/{cid}.html",
            "source_name": "51sdx",
            "source_type": "web",
            "province_scope": "",
            "title": zc.get("zcName") or "",
            "content": sections["admission_rules_text"],
            "content_html": html[:5000],
            "image_urls": "",
            "housing_fee_text": "",
            "contact_text": "",
            "plan_policy_text": "",
            "ocr_status": zc_type,  # store zcType for deduplication filter
            "fetched_at": zc.get("dataTime") or "",
            **{k: v for k, v in sections.items() if k != "admission_rules_text"},
            "admission_rules_text": sections["admission_rules_text"],
        }

        for db in (COMMON_DB, ZHEJIANG_DB):
            with sqlite3.connect(db) as conn:
                upsert_admission_charters(conn, [row])

        already_ok.add(school_name)
        inserted += 1

        progress = cid - args.start + 1
        if progress % 50 == 0 or inserted % 20 == 0:
            pct = progress / total * 100
            print(f"  [{cid}] {pct:.0f}% | found={found} inserted={inserted} skipped={skipped} type_skip={skipped_type} errors={errors}  ({school_name})", flush=True)

        time.sleep(DELAY)

    print(f"\nDone. found={found} inserted={inserted} skipped={skipped} type_skip={skipped_type} errors={errors}")


if __name__ == "__main__":
    main()
