#!/usr/bin/env python3
"""Scrape all verified admission charters from gaokao.chsi.com.cn listing page."""

import re, sqlite3, time, sys, json
from pathlib import Path

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE / "scripts"))
from scrape_chsi_admission_charters import (
    ensure_admission_charter_table, upsert_admission_charters,
    parse_charter_html, COMMON_DB, ZHEJIANG_DB,
)

CHROME = (
    str(Path.home()) +
    "/Library/Caches/ms-playwright/chromium-1223/chrome-mac-arm64"
    "/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
)
BASE_URL = "https://gaokao.chsi.com.cn"
LIST_URL = BASE_URL + "/zsgs/zhangcheng/listVerifedZszc--method-index,lb-1.dhtml"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"


def make_browser(p):
    browser = p.chromium.launch(
        executable_path=CHROME, headless=False,
        proxy={"server": "http://127.0.0.1:7897"},
        args=["--disable-blink-features=AutomationControlled"],
    )
    ctx = browser.new_context(user_agent=UA)
    ctx.add_init_script('Object.defineProperty(navigator,"webdriver",{get:()=>undefined})')
    return browser, ctx


def fetch_page(page, url, retries=5):
    for i in range(retries):
        try:
            resp = page.goto(url, timeout=25000)
            time.sleep(3)
            if resp and resp.status == 200:
                content = page.content()
                if len(content) > 500:
                    return content
        except Exception as e:
            print(f"  retry {i+1}: {e}")
        time.sleep(5)
    return None


def parse_entries(content):
    """Return list of (school_name, detail_url)."""
    # find rows: <a href="/zsgs/zhangcheng/...">school name</a>
    entries = re.findall(
        r'href="(/zsgs/zhangcheng/[^"]+\.dhtml)"[^>]*>\s*([^\n<]{2,40})\s*</a>',
        content,
    )
    # deduplicate, filter out non-school text
    seen = set()
    out = []
    for url, name in entries:
        name = name.strip()
        if name in seen or len(name) < 2:
            continue
        seen.add(name)
        out.append((name, BASE_URL + url))
    return out


def parse_pagination(content, current_url):
    """Return list of next page URLs."""
    # look for 下一页 link
    next_links = re.findall(r'href="([^"]*listVerifedZszc[^"]*)"[^>]*>[^<]*下一页', content)
    if next_links:
        return [BASE_URL + next_links[0] if next_links[0].startswith("/") else next_links[0]]
    return []


def fetch_charter(page, url):
    content = fetch_page(page, url, retries=3)
    return content


def parse_charter_content(html, school_name, source_url, year=2026):
    return parse_charter_html(html, year=year, school_name=school_name,
                               source_url=source_url, source_name="CHSI官网")


def already_fetched(school_name, year=2026):
    with sqlite3.connect(ZHEJIANG_DB) as conn:
        row = conn.execute(
            "SELECT fetch_status FROM admission_charter WHERE year=? AND school_name=?",
            (year, school_name),
        ).fetchone()
    return row and row[0] == "ok"


def flush(rows):
    if not rows:
        return 0
    total = 0
    for db_path in (COMMON_DB, ZHEJIANG_DB):
        with sqlite3.connect(db_path) as conn:
            ensure_admission_charter_table(conn)
            total += upsert_admission_charters(conn, rows)
    return total


def main():
    from playwright.sync_api import sync_playwright

    year = 2026
    total_inserted = 0
    total_found = 0

    with sync_playwright() as p:
        browser, ctx = make_browser(p)
        page = ctx.new_page()

        current_url = LIST_URL
        page_num = 1

        while current_url:
            print(f"\n=== Page {page_num}: {current_url} ===")
            content = fetch_page(page, current_url, retries=8)
            if not content:
                print("  FAILED to load page, stopping.")
                break

            entries = parse_entries(content)
            print(f"  found {len(entries)} schools on this page")
            if not entries:
                print("  no entries parsed, checking raw content snippet:")
                print(content[2000:3000])
                break

            rows = []
            for school_name, detail_url in entries:
                if already_fetched(school_name, year):
                    print(f"  skip (already ok): {school_name}")
                    continue
                total_found += 1
                print(f"  fetching: {school_name}")
                charter_html = fetch_charter(page, detail_url)
                if not charter_html:
                    print(f"    FAILED")
                    continue
                try:
                    row = parse_charter_content(charter_html, school_name, detail_url, year)
                    rows.append(row)
                except Exception as e:
                    print(f"    parse error: {e}")
                time.sleep(1)

            inserted = flush(rows)
            total_inserted += inserted
            print(f"  inserted {inserted} this page, total={total_inserted}")

            # pagination
            next_pages = parse_pagination(content, current_url)
            if next_pages:
                current_url = next_pages[0]
                page_num += 1
            else:
                print("  no more pages")
                break

        browser.close()

    print(f"\nDone. total_found={total_found} total_inserted={total_inserted}")


if __name__ == "__main__":
    main()
