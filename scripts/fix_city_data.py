"""Fix dirty city names and stale Wikipedia data in city_profile."""

from __future__ import annotations
import re
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DB_PATH = PROJECT_ROOT / "data" / "college.db"

# ── 1. City name corrections ──────────────────────────────────────────────────
CITY_RENAMES = {
    "揭州": "揭阳",       # 揭阳职业技术学院、潮汕职业技术学院实际在揭阳市
    "海西州德今哈": "德令哈",  # 青海柴达木职业技术学院在德令哈市（德令哈是正式地名）
}

# Cities whose province tag is also wrong (city_name → correct province)
CITY_PROVINCE_FIX = {
    "军校": None,   # delete entirely — not a real city
}


def stale_industry(text: str) -> bool:
    """Return True if industry_summary mentions only pre-2015 years (likely stale)."""
    if not text:
        return False
    years = [int(y) for y in re.findall(r'20(\d{2})年', text)]
    if not years:
        return False
    return max(years) < 15  # all years < 2015


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")

    # 1a. Fix school_master city names
    for old, new in CITY_RENAMES.items():
        n = conn.execute(
            "UPDATE school_master SET city=? WHERE city=?", (new, old)
        ).rowcount
        print(f"school_master: {old!r} → {new!r}  ({n} rows)")

    # 1b. Fix city_profile city names
    for old, new in CITY_RENAMES.items():
        exists = conn.execute(
            "SELECT 1 FROM city_profile WHERE city_name=?", (new,)
        ).fetchone()
        if exists:
            # New name already has a row — just delete the old one
            conn.execute("DELETE FROM city_profile WHERE city_name=?", (old,))
            print(f"city_profile: deleted duplicate {old!r}")
        else:
            n = conn.execute(
                "UPDATE city_profile SET city_name=? WHERE city_name=?", (new, old)
            ).rowcount
            print(f"city_profile: {old!r} → {new!r}  ({n} rows)")

    # 1c. Remove invalid city entries
    for city in CITY_PROVINCE_FIX:
        n = conn.execute("DELETE FROM city_profile WHERE city_name=?", (city,)).rowcount
        print(f"city_profile: deleted {city!r}  ({n} rows)")

    # 2. Clear stale Wikipedia industry_summary (pre-2015 data)
    rows = conn.execute(
        "SELECT city_name, industry_summary FROM city_profile "
        "WHERE source_name LIKE '维基%' AND industry_summary != ''"
    ).fetchall()
    stale_count = 0
    for city_name, industry in rows:
        if stale_industry(industry):
            conn.execute(
                "UPDATE city_profile SET industry_summary='' WHERE city_name=?",
                (city_name,)
            )
            stale_count += 1
            print(f"  cleared stale industry: {city_name}")
    print(f"Cleared {stale_count} stale Wikipedia industry summaries")

    # 3. Flag mismatched GDP/industry years: if GDP year in industry text doesn't match
    #    GDP field year, clear industry_summary to avoid misleading users
    rows2 = conn.execute(
        "SELECT city_name, gdp, industry_summary FROM city_profile "
        "WHERE source_name LIKE '维基%' AND gdp != '' AND industry_summary != ''"
    ).fetchall()
    mismatch_count = 0
    for city_name, gdp_field, industry in rows2:
        # Extract year from GDP field (rare, mainly from wikipedia parsing e.g. 2022年)
        industry_years = [int(y) for y in re.findall(r'20(\d{2})年', industry)]
        if industry_years and max(industry_years) < 19:  # before 2019
            conn.execute(
                "UPDATE city_profile SET industry_summary='' WHERE city_name=?",
                (city_name,)
            )
            mismatch_count += 1
    print(f"Cleared {mismatch_count} outdated Wikipedia industry summaries (pre-2019)")

    conn.commit()
    conn.close()

    # Summary
    conn2 = sqlite3.connect(DB_PATH)
    total, has_gdp, has_pop, has_industry = conn2.execute(
        "SELECT COUNT(*), "
        "SUM(CASE WHEN gdp!='' AND gdp IS NOT NULL THEN 1 ELSE 0 END), "
        "SUM(CASE WHEN population!='' AND population IS NOT NULL THEN 1 ELSE 0 END), "
        "SUM(CASE WHEN industry_summary!='' AND industry_summary IS NOT NULL THEN 1 ELSE 0 END) "
        "FROM city_profile"
    ).fetchone()
    print(f"\ncity_profile after fix: total={total} gdp={has_gdp} pop={has_pop} industry={has_industry}")
    conn2.close()


if __name__ == "__main__":
    main()
