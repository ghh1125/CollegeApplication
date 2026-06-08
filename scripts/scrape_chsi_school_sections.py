"""Scrape 阳光高考 school information tabs into common.db.

The live CHSI pages may return a JavaScript guard page to non-browser clients.
This script detects that case and skips the page instead of storing bad data.
The parser functions are intentionally pure so saved HTML can also be parsed.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import time
from collections.abc import Iterable
from datetime import datetime
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

COMMON_DB = PROJECT_ROOT / "data" / "common" / "common.db"
CHSI_BASE_URL = "https://gaokao.chsi.com.cn"
SEARCH_URL = f"{CHSI_BASE_URL}/sch/search--ss-on,option-qg,searchType-1,start-{{start}}.dhtml"
USER_AGENT = "Mozilla/5.0 (CollegeApplication school info crawler; contact: local research use)"

SECTION_TITLE_TO_KEY = {
    "学校简介": "overview",
    "院系设置": "departments",
    "专业介绍": "major_intro",
    "录取规则": "admission_rules",
    "奖学金设置": "scholarships",
    "食宿条件": "housing_dining",
    "联系办法": "contact",
    "答考生问": "faq",
}

CONTENT_SELECTORS = (
    ".content",
    ".yxk-content",
    ".sch-content",
    ".main",
    ".right",
    ".r_con",
    "#content",
)


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[dict[str, str]] = []
        self._active_href: str | None = None
        self._active_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        attrs_dict = {k.lower(): v or "" for k, v in attrs}
        self._active_href = attrs_dict.get("href", "")
        self._active_text = []

    def handle_data(self, data: str) -> None:
        if self._active_href is not None:
            self._active_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self._active_href is None:
            return
        self.links.append({"href": self._active_href, "text": _clean_text(" ".join(self._active_text))})
        self._active_href = None
        self._active_text = []


class _TextParser(HTMLParser):
    _SKIP_TAGS = {"script", "style", "noscript", "iframe", "form", "nav"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in self._SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self._SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(data)


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _summary(text: str, limit: int = 180) -> str:
    text = _clean_text(text)
    if len(text) <= limit:
        return text
    return text[:limit].rstrip("，。；,; ") + "…"


def _absolute_url(href: str, base_url: str = CHSI_BASE_URL) -> str:
    return urljoin(base_url, href)


def _links_from_html(html: str) -> list[dict[str, str]]:
    parser = _LinkParser()
    parser.feed(html)
    return parser.links


def _text_from_html(html: str) -> str:
    parser = _TextParser()
    parser.feed(html)
    return _clean_text(unescape(" ".join(parser.parts)))


def _body_html(html: str) -> str:
    match = re.search(r"<body\b[^>]*>(.*?)</body>", html, flags=re.IGNORECASE | re.DOTALL)
    return match.group(1) if match else html


def _content_html(html: str) -> str:
    for class_name in ("content", "yxk-content", "sch-content", "main", "right", "r_con"):
        pattern = (
            r"<(?P<tag>div|section|article)\b[^>]*class=[\"'][^\"']*"
            + re.escape(class_name)
            + r"[^\"']*[\"'][^>]*>(?P<body>.*?)</(?P=tag)>"
        )
        match = re.search(pattern, html, flags=re.IGNORECASE | re.DOTALL)
        if match:
            return match.group("body")
    match = re.search(r"<[^>]+id=[\"']content[\"'][^>]*>(.*?)</[^>]+>", html, flags=re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(1)
    return _body_html(html)


def is_chsi_guard_page(html: str) -> bool:
    """Return True when CHSI served its JavaScript guard instead of content."""
    if "$_ts" not in html and "_$_w" not in html:
        return False
    body_text = _text_from_html(_body_html(html))
    links = _links_from_html(html)
    return not body_text and not links


def parse_school_search_page(html: str, base_url: str = CHSI_BASE_URL) -> list[dict]:
    """Extract school ids/names from a CHSI school-search result page."""
    schools: list[dict] = []
    seen: set[str] = set()
    for link in _links_from_html(html):
        href = link["href"]
        match = re.search(r"schoolInfoMain--schId-(\d+)\.dhtml", href)
        if not match:
            continue
        school_id = match.group(1)
        school_name = _clean_text(link["text"])
        if not school_name or school_id in seen:
            continue
        schools.append({
            "school_id": school_id,
            "school_name": school_name,
            "url": _absolute_url(href, base_url),
        })
        seen.add(school_id)
    return schools


def parse_section_links(html: str, base_url: str = CHSI_BASE_URL) -> dict[str, dict]:
    """Extract known tab links such as 学校简介、录取规则、食宿条件."""
    sections: dict[str, dict] = {}
    for link in _links_from_html(html):
        title = _clean_text(link["text"])
        key = SECTION_TITLE_TO_KEY.get(title)
        if not key or key in sections:
            continue
        sections[key] = {
            "title": title,
            "url": _absolute_url(link["href"], base_url),
        }
    return sections


def parse_section_page(
    html: str,
    *,
    school_id: str,
    school_name: str,
    section_key: str,
    section_title: str,
    source_url: str,
) -> dict:
    """Parse one school tab page into a row for school_info_section."""
    content = _text_from_html(_content_html(html))
    return {
        "school_id": school_id,
        "school_name": school_name,
        "section_key": section_key,
        "section_title": section_title,
        "summary": _summary(content),
        "content": content,
        "source_name": "阳光高考",
        "source_url": source_url,
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
    }


def ensure_school_info_section_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS school_info_section (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            school_name TEXT NOT NULL,
            school_id TEXT,
            section_key TEXT NOT NULL,
            section_title TEXT NOT NULL,
            summary TEXT,
            content TEXT,
            source_name TEXT DEFAULT '阳光高考',
            source_url TEXT,
            fetched_at TEXT DEFAULT (datetime('now')),
            UNIQUE(school_name, section_key)
        )
        """
    )
    conn.commit()


def upsert_school_info_sections(conn: sqlite3.Connection, rows: Iterable[dict]) -> int:
    ensure_school_info_section_table(conn)
    sql = """
        INSERT INTO school_info_section (
            school_name, school_id, section_key, section_title, summary, content,
            source_name, source_url, fetched_at
        ) VALUES (
            :school_name, :school_id, :section_key, :section_title, :summary, :content,
            :source_name, :source_url, :fetched_at
        )
        ON CONFLICT(school_name, section_key) DO UPDATE SET
            school_id = excluded.school_id,
            section_title = excluded.section_title,
            summary = excluded.summary,
            content = excluded.content,
            source_name = excluded.source_name,
            source_url = excluded.source_url,
            fetched_at = excluded.fetched_at
    """
    count = 0
    for row in rows:
        if not row.get("school_name") or not row.get("section_key") or not row.get("content"):
            continue
        conn.execute(sql, row)
        count += 1
    conn.commit()
    return count


def _fetch(session: requests.Session, url: str, timeout: int = 12) -> str | None:
    response = session.get(url, timeout=timeout)
    if response.status_code in (403, 412):
        return None
    response.raise_for_status()
    response.encoding = response.apparent_encoding or response.encoding
    html = response.text
    return None if is_chsi_guard_page(html) else html


def scrape_school_sections(
    *,
    db_path: Path = COMMON_DB,
    limit: int | None = None,
    max_pages: int = 20,
    sleep_seconds: float = 1.0,
) -> dict[str, int]:
    """Fetch CHSI school tabs and store them in common.db."""
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    schools: list[dict] = []
    skipped_guard = 0
    for page in range(max_pages):
        html = _fetch(session, SEARCH_URL.format(start=page * 20))
        if html is None:
            skipped_guard += 1
            break
        batch = parse_school_search_page(html)
        if not batch:
            break
        schools.extend(batch)
        if limit and len(schools) >= limit:
            schools = schools[:limit]
            break
        time.sleep(sleep_seconds)

    rows: list[dict] = []
    for school in schools:
        main_html = _fetch(session, school["url"])
        if main_html is None:
            skipped_guard += 1
            continue
        for key, section in parse_section_links(main_html).items():
            section_html = _fetch(session, section["url"])
            if section_html is None:
                skipped_guard += 1
                continue
            row = parse_section_page(
                section_html,
                school_id=school["school_id"],
                school_name=school["school_name"],
                section_key=key,
                section_title=section["title"],
                source_url=section["url"],
            )
            if row["content"]:
                rows.append(row)
            time.sleep(sleep_seconds)

    with sqlite3.connect(db_path) as conn:
        inserted = upsert_school_info_sections(conn, rows)
    return {
        "schools": len(schools),
        "sections": inserted,
        "guard_pages": skipped_guard,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=COMMON_DB)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--max-pages", type=int, default=20)
    parser.add_argument("--sleep", type=float, default=1.0)
    args = parser.parse_args()

    stats = scrape_school_sections(
        db_path=args.db,
        limit=args.limit,
        max_pages=args.max_pages,
        sleep_seconds=args.sleep,
    )
    print(
        f"schools={stats['schools']} sections={stats['sections']} "
        f"guard_pages={stats['guard_pages']}"
    )


if __name__ == "__main__":
    main()
