"""Fetch and parse 2026 admission charters into common.db.

The authoritative full-list entry is 阳光高考院校章程. CHSI often serves a
JavaScript guard page to plain HTTP clients, so this script also supports
school-official URLs and marks image-only pages for OCR instead of pretending
they were parsed as text.
"""

from __future__ import annotations

import argparse
import json
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
ZHEJIANG_DB = PROJECT_ROOT / "data" / "zhejiang" / "college.db"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)

CHARTER_LINK_RE = re.compile(r"(?:2026\s*年?)?.{0,12}(?:普通高校|普通本科|本科)?招生章程")
NOISE_IMAGE_RE = re.compile(r"(?:logo|banner|wx|weibo|wblogo|hdlogo|qrcode|icon|public/images)", re.I)

SECTION_LABELS = {
    "tuition_text": ("学费标准", "学费", "收费标准"),
    "housing_fee_text": ("住宿费标准", "住宿费"),
    "admission_rules_text": ("录取规则", "录取办法", "投档录取"),
    "language_requirement_text": ("外语语种", "外语单科", "外语成绩", "培养使用的外语"),
    "physical_requirement_text": ("身体健康状况要求", "身体健康要求", "体检", "健康状况"),
    "contact_text": ("网址及联系电话", "联系方式", "联系电话", "咨询电话"),
    "plan_policy_text": ("招生计划分配", "招生计划", "预留计划"),
}
ALL_LABELS = tuple(label for labels in SECTION_LABELS.values() for label in labels)


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


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[dict[str, str]] = []
        self._href: str | None = None
        self._title = ""
        self._text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        attrs_dict = {key.lower(): value or "" for key, value in attrs}
        self._href = attrs_dict.get("href", "")
        self._title = attrs_dict.get("title", "")
        self._text_parts = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self._href is None:
            return
        text = _clean_text(" ".join([*self._text_parts, self._title]))
        self.links.append({"href": self._href, "text": text})
        self._href = None
        self._title = ""
        self._text_parts = []


def _text_from_html(html: str) -> str:
    parser = _TextParser()
    parser.feed(html)
    return _clean_text(unescape(" ".join(parser.parts)))


def _links_from_html(html: str) -> list[dict[str, str]]:
    parser = _LinkParser()
    parser.feed(html)
    return parser.links


def _clean_text(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "").replace("\u3000", " ")).strip()
    return text.replace("√", "√ ").replace("□", " □ ")


def _clip(value: Any, limit: int = 700) -> str:
    text = _clean_text(value)
    if len(text) <= limit:
        return text
    return text[:limit].rstrip("，。；,; ") + "..."


def _absolute_url(href: str, base_url: str) -> str:
    return urljoin(base_url, href)


def is_guard_page(html: str) -> bool:
    """Return True when a site served anti-bot JavaScript instead of content."""

    if "$_ts" not in html and "_$jw" not in html and "_$_w" not in html:
        return False
    body = _body_html(html)
    body_text = _text_from_html(body)
    return not body_text


def _body_html(html: str) -> str:
    match = re.search(r"<body\b[^>]*>(.*?)</body>", html, flags=re.IGNORECASE | re.DOTALL)
    return match.group(1) if match else html


def _content_html(html: str) -> str:
    selectors = (
        ("id", "news1content"),
        ("class", "news1content"),
        ("class", "content"),
        ("class", "article"),
        ("class", "main"),
    )
    for attr, value in selectors:
        pattern = (
            r"<(?P<tag>div|section|article)\b[^>]*"
            + attr
            + r"=[\"'][^\"']*"
            + re.escape(value)
            + r"[^\"']*[\"'][^>]*>(?P<body>.*?)</(?P=tag)>"
        )
        match = re.search(pattern, html, flags=re.IGNORECASE | re.DOTALL)
        if match:
            return match.group("body")
    return _body_html(html)


def _title_from_html(html: str, fallback: str = "") -> str:
    patterns = (
        r"<h1\b[^>]*>(.*?)</h1>",
        r"<[^>]+class=[\"'][^\"']*news1title[^\"']*[\"'][^>]*>(.*?)</[^>]+>",
        r"<[^>]+class=[\"'][^\"']*article-title[^\"']*[\"'][^>]*>(.*?)</[^>]+>",
        r"<[^>]+class=[\"'][^\"']*title[^\"']*[\"'][^>]*>(.*?)</[^>]+>",
        r"<title\b[^>]*>(.*?)</title>",
    )
    for pattern in patterns:
        match = re.search(pattern, html, flags=re.IGNORECASE | re.DOTALL)
        if match:
            title = _text_from_html(match.group(1))
            if title:
                return title
    return fallback


def _attrs_from_tag(tag: str) -> dict[str, str]:
    return {
        key.lower(): value
        for key, value in re.findall(r"([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*[\"']([^\"']*)[\"']", tag)
    }


def _image_urls(content_html: str, base_url: str) -> list[str]:
    urls: list[str] = []
    for match in re.finditer(r"<img\b[^>]*>", content_html, flags=re.IGNORECASE):
        attrs = _attrs_from_tag(match.group(0))
        src = attrs.get("src") or ""
        if not src or NOISE_IMAGE_RE.search(src):
            continue
        width = attrs.get("width") or ""
        height = attrs.get("height") or ""
        if width.isdigit() and int(width) < 240 and (not height.isdigit() or int(height) < 240):
            continue
        urls.append(_absolute_url(src, base_url))
    return urls


def _extract_section(text: str, labels: tuple[str, ...], limit: int = 700) -> str:
    compact = _clean_text(text)
    positions: list[tuple[int, str]] = []
    for label in ALL_LABELS:
        pos = compact.find(label)
        if pos >= 0:
            positions.append((pos, label))
    if not positions:
        return ""
    positions.sort()
    for index, (pos, label) in enumerate(positions):
        if label not in labels:
            continue
        end = len(compact)
        for next_pos, _ in positions[index + 1 :]:
            if next_pos > pos + len(label):
                end = next_pos
                break
        return _clip(compact[pos:end], limit=limit)
    return ""


def _parse_table_pairs(content_html: str) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for row_match in re.finditer(r"<tr\b[^>]*>(.*?)</tr>", content_html, flags=re.IGNORECASE | re.DOTALL):
        row_html = row_match.group(1)
        cells = [
            _text_from_html(cell)
            for cell in re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", row_html, flags=re.IGNORECASE | re.DOTALL)
        ]
        cells = [cell for cell in cells if cell]
        if len(cells) < 2:
            continue
        label = cells[0]
        value = " ".join(cells[1:])
        for field, labels in SECTION_LABELS.items():
            if any(key in label for key in labels):
                pairs[field] = _clip(value)
    return pairs


def parse_charter_html(
    html: str,
    *,
    year: int,
    school_name: str,
    source_url: str,
    school_id: str | None = None,
    source_name: str = "高校官网",
) -> dict:
    """Parse one admission-charter page into a database row."""

    fetched_at = datetime.now().isoformat(timespec="seconds")
    if is_guard_page(html):
        return {
            "year": year,
            "school_name": school_name,
            "school_id": school_id,
            "province_scope": "浙江",
            "title": "",
            "content": "",
            "content_html": "",
            "image_urls": "[]",
            "source_name": source_name,
            "source_url": source_url,
            "source_type": "guard",
            "fetch_status": "guarded",
            "ocr_status": "blocked",
            "tuition_text": "",
            "housing_fee_text": "",
            "admission_rules_text": "",
            "language_requirement_text": "",
            "physical_requirement_text": "",
            "contact_text": "",
            "plan_policy_text": "",
            "fetched_at": fetched_at,
        }

    body_html = _body_html(html)
    content_html = _content_html(html)
    title = _title_from_html(html, fallback=f"{school_name}{year}年招生章程")
    text = _text_from_html(content_html)
    images = _image_urls(content_html, source_url)
    if not images:
        images = _image_urls(body_html, source_url)

    row = {
        "year": year,
        "school_name": school_name,
        "school_id": school_id,
        "province_scope": "浙江",
        "title": title,
        "content": text,
        "content_html": content_html,
        "image_urls": json.dumps(images, ensure_ascii=False),
        "source_name": source_name,
        "source_url": source_url,
        "source_type": "html",
        "fetch_status": "ok",
        "ocr_status": "not_needed",
        "tuition_text": "",
        "housing_fee_text": "",
        "admission_rules_text": "",
        "language_requirement_text": "",
        "physical_requirement_text": "",
        "contact_text": "",
        "plan_policy_text": "",
        "fetched_at": fetched_at,
    }
    table_pairs = _parse_table_pairs(content_html)
    for field, labels in SECTION_LABELS.items():
        row[field] = table_pairs.get(field) or _extract_section(text, labels)
    if images and not any(row[field] for field in SECTION_LABELS):
        row["source_type"] = "image"
        row["ocr_status"] = "needed"
    return row


def discover_charter_links(html: str, base_url: str, year: int = 2026) -> list[dict[str, str]]:
    """Find likely school-official admission-charter links from a page."""

    links: list[dict[str, str]] = []
    seen: set[str] = set()
    for link in _links_from_html(html):
        href = link["href"]
        text = link["text"]
        if not href or str(year) not in text:
            continue
        if not CHARTER_LINK_RE.search(text):
            continue
        url = _absolute_url(href, base_url)
        if url in seen:
            continue
        links.append({"title": text, "url": url})
        seen.add(url)
    return links


def ensure_admission_charter_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS admission_charter (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            year INTEGER NOT NULL,
            school_name TEXT NOT NULL,
            school_id TEXT,
            province_scope TEXT DEFAULT '浙江',
            title TEXT,
            content TEXT,
            content_html TEXT,
            image_urls TEXT,
            source_name TEXT DEFAULT '高校官网',
            source_url TEXT,
            source_type TEXT DEFAULT 'html',
            fetch_status TEXT DEFAULT 'ok',
            ocr_status TEXT DEFAULT 'not_needed',
            tuition_text TEXT,
            housing_fee_text TEXT,
            admission_rules_text TEXT,
            language_requirement_text TEXT,
            physical_requirement_text TEXT,
            contact_text TEXT,
            plan_policy_text TEXT,
            fetched_at TEXT DEFAULT (datetime('now')),
            UNIQUE(year, school_name)
        )
        """
    )
    conn.commit()


def upsert_admission_charters(conn: sqlite3.Connection, rows: Iterable[dict]) -> int:
    ensure_admission_charter_table(conn)
    sql = """
        INSERT INTO admission_charter (
            year, school_name, school_id, province_scope, title, content, content_html,
            image_urls, source_name, source_url, source_type, fetch_status, ocr_status,
            tuition_text, housing_fee_text, admission_rules_text,
            language_requirement_text, physical_requirement_text, contact_text,
            plan_policy_text, fetched_at
        ) VALUES (
            :year, :school_name, :school_id, :province_scope, :title, :content, :content_html,
            :image_urls, :source_name, :source_url, :source_type, :fetch_status, :ocr_status,
            :tuition_text, :housing_fee_text, :admission_rules_text,
            :language_requirement_text, :physical_requirement_text, :contact_text,
            :plan_policy_text, :fetched_at
        )
        ON CONFLICT(year, school_name) DO UPDATE SET
            school_id = excluded.school_id,
            province_scope = excluded.province_scope,
            title = excluded.title,
            content = excluded.content,
            content_html = excluded.content_html,
            image_urls = excluded.image_urls,
            source_name = excluded.source_name,
            source_url = excluded.source_url,
            source_type = excluded.source_type,
            fetch_status = excluded.fetch_status,
            ocr_status = excluded.ocr_status,
            tuition_text = excluded.tuition_text,
            housing_fee_text = excluded.housing_fee_text,
            admission_rules_text = excluded.admission_rules_text,
            language_requirement_text = excluded.language_requirement_text,
            physical_requirement_text = excluded.physical_requirement_text,
            contact_text = excluded.contact_text,
            plan_policy_text = excluded.plan_policy_text,
            fetched_at = excluded.fetched_at
    """
    count = 0
    for row in rows:
        if not row.get("school_name") or not row.get("year"):
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
    return response.text


def _iter_plan_schools(limit: int | None = None) -> list[dict]:
    sql = """
        SELECT DISTINCT p.school_name, s.school_id, s.source_url
        FROM admission_plan p
        LEFT JOIN school_profile s ON p.school_name = s.school_name
        WHERE p.school_name IS NOT NULL AND p.school_name != ''
        ORDER BY p.school_name
    """
    with sqlite3.connect(ZHEJIANG_DB) as conn:
        rows = conn.execute(sql).fetchall()
    schools = [
        {"school_name": r[0], "school_id": r[1], "profile_url": r[2]}
        for r in rows
    ]
    return schools[:limit] if limit else schools


def _site_from_profile_url(
    session: requests.Session,
    profile_url: str | None,
    *,
    timeout: int = 12,
) -> str:
    if not profile_url:
        return ""
    try:
        payload = _fetch(session, profile_url, timeout=timeout)
        if not payload:
            return ""
        data = json.loads(payload).get("data") or {}
        return data.get("site") or data.get("school_site") or ""
    except Exception:
        return ""


def fetch_explicit_sources(
    sources: list[str],
    *,
    year: int,
    sleep_seconds: float = 0.5,
    timeout: int = 12,
) -> list[dict]:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    rows: list[dict] = []
    for source in sources:
        if "=" not in source:
            raise ValueError("--source must be SCHOOL_NAME=URL")
        school_name, url = source.split("=", 1)
        html = _fetch(session, url, timeout=timeout)
        if html:
            rows.append(parse_charter_html(html, year=year, school_name=school_name, source_url=url))
        time.sleep(sleep_seconds)
    return rows


def _existing_charter_schools(year: int, db_path: Path = COMMON_DB) -> set[str]:
    if not db_path.exists():
        return set()
    try:
        with sqlite3.connect(db_path) as conn:
            ensure_admission_charter_table(conn)
            rows = conn.execute(
                "SELECT school_name FROM admission_charter WHERE year = ?",
                (year,),
            ).fetchall()
        return {row[0] for row in rows}
    except Exception:
        return set()


def _missing_plan_schools(year: int, limit: int | None = None, offset: int = 0) -> list[dict]:
    # Include both: no row yet (IS NULL) and placeholder rows (fetch_status='missing')
    sql = """
        SELECT p.school_name, s.school_id
        FROM (SELECT DISTINCT school_name FROM admission_plan) p
        LEFT JOIN admission_charter c ON c.year = ? AND c.school_name = p.school_name
        LEFT JOIN school_profile s ON s.school_name = p.school_name
        WHERE c.school_name IS NULL OR c.fetch_status = 'missing'
        ORDER BY p.school_name
    """
    with sqlite3.connect(ZHEJIANG_DB) as conn:
        rows = conn.execute(sql, (year,)).fetchall()
    schools = [{"school_name": r[0], "school_id": r[1]} for r in rows]
    if offset:
        schools = schools[offset:]
    return schools[:limit] if limit else schools


def missing_charter_row(year: int, school_name: str, school_id: str | None = None) -> dict:
    """Build a placeholder row so coverage status is explicit in the DB."""

    return {
        "year": year,
        "school_name": school_name,
        "school_id": school_id,
        "province_scope": "浙江",
        "title": f"{school_name}{year}年招生章程未抓取",
        "content": "",
        "content_html": "",
        "image_urls": "[]",
        "source_name": "待补充",
        "source_url": "",
        "source_type": "missing",
        "fetch_status": "missing",
        "ocr_status": "not_available",
        "tuition_text": "",
        "housing_fee_text": "",
        "admission_rules_text": "",
        "language_requirement_text": "",
        "physical_requirement_text": "",
        "contact_text": "",
        "plan_policy_text": "",
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
    }


def mark_missing_for_zhejiang_plan(year: int, db_path: Path = COMMON_DB) -> dict[str, int]:
    """Insert explicit missing rows for every Zhejiang-plan school without a charter."""

    with sqlite3.connect(db_path) as conn:
        ensure_admission_charter_table(conn)
        existing = {
            row[0]
            for row in conn.execute(
                "SELECT school_name FROM admission_charter WHERE year = ?",
                (year,),
            ).fetchall()
        }
        schools = _iter_plan_schools(limit=None)
        rows = [
            missing_charter_row(year, school["school_name"], school.get("school_id"))
            for school in schools
            if school["school_name"] not in existing
        ]
        inserted = upsert_admission_charters(conn, rows)
    return {"plan_schools": len(schools), "missing_inserted": inserted}


def _search_charter_url(school_name: str, year: int = 2026, max_results: int = 6) -> str:
    try:
        from ddgs import DDGS
    except Exception:
        return ""

    import subprocess, json as _json, sys as _sys, requests as _req

    query = f'"{school_name}" "{year}" "招生章程"'

    # Try Baidu first (better Chinese index), fall back to DDG
    results = []
    try:
        _baidu_url = (
            "https://www.baidu.com/s?wd="
            + _req.utils.quote(query)
            + "&rn=10"
        )
        _resp = _req.get(
            _baidu_url,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                "Accept-Language": "zh-CN,zh;q=0.9",
            },
            timeout=10,
        )
        import re as _re
        _hrefs = _re.findall(r'"url"\s*:\s*"(https?://[^"]+)"', _resp.text)
        _titles = _re.findall(r'<h3[^>]*>\s*<a[^>]*>([^<]+)</a>', _resp.text)
        for _h, _t in zip(_hrefs, _titles or [""]*len(_hrefs)):
            results.append({"href": _h, "title": _t, "body": _t})
        if not results:
            # parse Baidu result links another way
            _links = _re.findall(r'href="(https?://(?!www\.baidu)[^"]+)"[^>]*>[^<]*招生章程', _resp.text)
            results = [{"href": l, "title": "招生章程", "body": "招生章程"} for l in _links]
    except Exception:
        pass

    if not results:
        _code = (
            "import json,sys\n"
            "from ddgs import DDGS\n"
            f"q={_json.dumps(query)}\n"
            f"n={max_results}\n"
            "try:\n"
            "    r=list(DDGS().text(q,max_results=n))\n"
            "    print(json.dumps(r))\n"
            "except Exception as e:\n"
            "    print('[]')\n"
        )
        try:
            proc = subprocess.run(
                [_sys.executable, "-c", _code],
                capture_output=True, text=True, timeout=12,
            )
            results = _json.loads(proc.stdout.strip() or "[]")
        except Exception:
            pass

    fallback = ""
    for result in results:
        title = _clean_text(result.get("title") or "")
        body = _clean_text(result.get("body") or "")
        href = result.get("href") or result.get("url") or ""
        haystack = f"{title} {body}"
        if not href or "招生章程" not in haystack or str(year) not in haystack:
            continue
        if "gaokao.chsi.com.cn" in href:
            fallback = fallback or href
            continue
        return href
    return fallback


def fetch_missing_by_search(
    *,
    year: int,
    limit: int | None = None,
    offset: int = 0,
    sleep_seconds: float = 1.0,
    timeout: int = 10,
    flush_every: int = 20,
) -> dict[str, int]:
    """Use web search as a fallback for schools not found from homepage links."""

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    rows: list[dict] = []
    searched = 0
    found = 0
    failed = 0
    inserted = 0

    def _flush():
        nonlocal inserted, rows
        if not rows:
            return
        for db_path in (COMMON_DB, ZHEJIANG_DB):
            with sqlite3.connect(db_path) as conn:
                ensure_admission_charter_table(conn)
                inserted += upsert_admission_charters(conn, rows)
        rows = []

    for school in _missing_plan_schools(year, limit=limit, offset=offset):
        searched += 1
        try:
            url = _search_charter_url(school["school_name"], year=year)
        except Exception:
            continue
        if not url:
            continue
        found += 1
        try:
            html = _fetch(session, url, timeout=timeout)
            if not html:
                failed += 1
                continue
            rows.append(
                parse_charter_html(
                    html,
                    year=year,
                    school_name=school["school_name"],
                    school_id=school.get("school_id"),
                    source_url=url,
                    source_name="搜索发现",
                )
            )
        except Exception:
            failed += 1
        _flush()
        if searched % 50 == 0:
            print(f"  searched={searched} found={found} inserted={inserted} failed={failed}",
                  flush=True)
        time.sleep(sleep_seconds)
    _flush()
    return {
        "searched": searched,
        "found": found,
        "inserted": inserted,
        "failed": failed,
    }


def fetch_from_school_sites(
    *,
    year: int,
    limit: int | None = None,
    offset: int = 0,
    sleep_seconds: float = 1.0,
    timeout: int = 12,
    skip_existing: bool = False,
) -> dict[str, int]:
    """Best-effort discovery from known school official sites in school_profile."""

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    rows: list[dict] = []
    existing = _existing_charter_schools(year) if skip_existing else set()
    inspected = 0
    discovered = 0
    failed = 0
    skipped = 0
    schools = _iter_plan_schools(limit=None)
    if offset:
        schools = schools[offset:]
    if limit:
        schools = schools[:limit]
    for school in schools:
        if school["school_name"] in existing:
            skipped += 1
            continue
        site = _site_from_profile_url(session, school.get("profile_url"), timeout=timeout)
        if not site:
            continue
        inspected += 1
        try:
            homepage = _fetch(session, site, timeout=timeout)
            if not homepage:
                failed += 1
                continue
            links = discover_charter_links(homepage, site, year=year)
            if not links:
                continue
            discovered += 1
            html = _fetch(session, links[0]["url"], timeout=timeout)
            if html:
                rows.append(
                    parse_charter_html(
                        html,
                        year=year,
                        school_name=school["school_name"],
                        school_id=school.get("school_id"),
                        source_url=links[0]["url"],
                    )
                )
        except Exception:
            failed += 1
        time.sleep(sleep_seconds)
    with sqlite3.connect(COMMON_DB) as conn:
        inserted = upsert_admission_charters(conn, rows)
    return {
        "inspected": inspected,
        "discovered": discovered,
        "inserted": inserted,
        "failed": failed,
        "skipped": skipped,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=COMMON_DB)
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--source", action="append", default=[], help="SCHOOL_NAME=URL; can be repeated")
    parser.add_argument("--from-zhejiang-plan", action="store_true")
    parser.add_argument("--search-missing", action="store_true")
    parser.add_argument("--mark-missing", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--sleep", type=float, default=0.5)
    parser.add_argument("--timeout", type=int, default=12)
    parser.add_argument("--skip-existing", action="store_true")
    args = parser.parse_args()

    if args.source:
        rows = fetch_explicit_sources(
            args.source,
            year=args.year,
            sleep_seconds=args.sleep,
            timeout=args.timeout,
        )
        with sqlite3.connect(args.db) as conn:
            inserted = upsert_admission_charters(conn, rows)
        print(f"inserted={inserted}")
    elif args.from_zhejiang_plan:
        stats = fetch_from_school_sites(
            year=args.year,
            limit=args.limit,
            offset=args.offset,
            sleep_seconds=args.sleep,
            timeout=args.timeout,
            skip_existing=args.skip_existing,
        )
        print(" ".join(f"{key}={value}" for key, value in stats.items()))
    elif args.search_missing:
        stats = fetch_missing_by_search(
            year=args.year,
            limit=args.limit,
            offset=args.offset,
            sleep_seconds=args.sleep,
            timeout=args.timeout,
        )
        print(" ".join(f"{key}={value}" for key, value in stats.items()))
    elif args.mark_missing:
        stats = mark_missing_for_zhejiang_plan(args.year, db_path=args.db)
        print(" ".join(f"{key}={value}" for key, value in stats.items()))
    else:
        with sqlite3.connect(args.db) as conn:
            ensure_admission_charter_table(conn)
        print("admission_charter table ready")


if __name__ == "__main__":
    main()
