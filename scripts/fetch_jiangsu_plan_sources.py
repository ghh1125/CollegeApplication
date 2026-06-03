"""Find and download public Jiangsu admission-plan source files/pages.

This is the non-掌上高考 route for group-inner major details. It searches public
web pages for each school/year from official cutoff files, stores candidate
HTML/Excel/PDF sources, and leaves normalization to:

    python scripts/parse_jiangsu_plan_details.py

Output:
    data/jiangsu/raw/plan_sources/{year}/...

Run:
    python scripts/fetch_jiangsu_plan_sources.py --years 2025 --limit 20
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import re
import sqlite3
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = PROJECT_ROOT / "data" / "jiangsu" / "raw"
OFFICIAL_DIR = RAW_DIR / "official"
SOURCE_DIR = RAW_DIR / "plan_sources"
COMMON_DB = PROJECT_ROOT / "data" / "common" / "common.db"
DEFAULT_YEARS = (2025, 2024, 2023)
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36"
    )
}
SUPPORTED_EXTS = {".html", ".htm", ".xls", ".xlsx", ".csv", ".pdf", ".txt"}
ATTACHMENT_EXTS = {".xls", ".xlsx", ".csv", ".pdf", ".txt"}
PLAN_LINK_KEYWORDS = ("招生计划", "专业组", "计划表", "附件", "江苏")
PLAN_NAV_KEYWORDS = ("招生计划", "计划查询", "分省分专业", "招生专业", "报考指南", "本科招生")
EXCLUDED_SOURCE_KEYWORDS = ("专转本", "研究生", "硕士", "博士", "强基计划", "综合评价")
SOURCE_REQUIRED_KEYWORDS = ("招生计划", "专业组", "本科批", "普通类")
BLOCKED_DOMAINS = {
    "api.zjzw.cn",
    "www.gaokao.cn",
    "gaokao.cn",
}


def safe_name(text: str) -> str:
    text = re.sub(r"[^\w\u4e00-\u9fa5()-]+", "_", text.strip())
    return text[:80] or "source"


def official_school_names(year: int) -> list[str]:
    best_metric: dict[str, tuple[int, int]] = {}
    for path in OFFICIAL_DIR.glob(f"cutoff_{year}_*.csv"):
        with path.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                name = (row.get("school_name") or "").strip()
                if not name:
                    continue
                rank = parse_int(row.get("min_rank"))
                score = parse_int(row.get("min_score"))
                # rank is primary; if missing, high score comes first by using -score.
                metric = (rank if rank is not None else 10_000_000, -(score or 0))
                if name not in best_metric or metric < best_metric[name]:
                    best_metric[name] = metric
    return [name for name, _metric in sorted(best_metric.items(), key=lambda item: item[1])]


def parse_int(value: object) -> int | None:
    text = str(value or "").strip().replace(",", "")
    if not text or text in {"-", "--", "—"}:
        return None
    match = re.search(r"\d+", text)
    return int(match.group(0)) if match else None


def is_blocked_url(url: str, allow_zsgk: bool = False) -> bool:
    if allow_zsgk:
        return False
    host = urlparse(url).netloc.lower()
    return any(host == domain or host.endswith("." + domain) for domain in BLOCKED_DOMAINS)


def same_site(url: str, base_url: str) -> bool:
    host = urlparse(url).netloc.lower()
    base_host = urlparse(base_url).netloc.lower()
    return bool(host and base_host and (host == base_host or host.endswith("." + base_host)))


def admission_site_for_school(school_name: str) -> str:
    """Resolve a school's undergraduate admission site from cached gaokao profile."""
    if not COMMON_DB.exists():
        return ""
    with sqlite3.connect(COMMON_DB) as conn:
        row = conn.execute(
            "SELECT source_url FROM school_profile WHERE school_name = ?",
            (school_name,),
        ).fetchone()
    if not row or not row[0]:
        return ""
    try:
        resp = requests.get(row[0], headers=HEADERS, timeout=12)
        resp.raise_for_status()
        data = resp.json().get("data") or {}
    except Exception:  # noqa: BLE001
        return ""
    return (data.get("site") or data.get("school_site") or "").strip()


def search_urls(query: str, max_results: int = 6) -> list[str]:
    """Search public web with ddgs. Returns URLs only."""
    try:
        from ddgs import DDGS
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("ddgs is required for web source discovery") from exc

    urls: list[str] = []
    with DDGS(timeout=20) as ddgs:
        for result in ddgs.text(query, max_results=max_results):
            url = result.get("href") or result.get("url")
            if url and url not in urls:
                urls.append(url)
    return urls


def choose_suffix(url: str, content_type: str) -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix in SUPPORTED_EXTS:
        return suffix
    if "pdf" in content_type:
        return ".pdf"
    if "spreadsheet" in content_type or "excel" in content_type:
        return ".xlsx"
    if "csv" in content_type:
        return ".csv"
    if "text" in content_type:
        return ".txt"
    return ".html"


def decode_text(content: bytes) -> str:
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return content.decode("gb18030", errors="ignore")


def is_likely_plan_source(content: bytes, suffix: str) -> bool:
    """Filter obvious false positives from public search results."""
    if suffix not in {".html", ".htm", ".txt"}:
        return True
    text = decode_text(content)
    head = text[:12000]
    has_required_signal = "江苏" in head and any(keyword in head for keyword in SOURCE_REQUIRED_KEYWORDS)
    if any(keyword in head for keyword in EXCLUDED_SOURCE_KEYWORDS) and not has_required_signal:
        return False
    return has_required_signal


def download_url(url: str, *, year: int, school_name: str, index: int) -> Path | None:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=25)
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        print(f"  下载失败 {url}: {exc}")
        return None

    content_type = resp.headers.get("Content-Type", "").lower()
    suffix = choose_suffix(url, content_type)
    if suffix not in SUPPORTED_EXTS:
        return None
    if not is_likely_plan_source(resp.content, suffix):
        return None
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]
    out_dir = SOURCE_DIR / str(year)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{safe_name(school_name)}_{index}_{digest}{suffix}"
    if not out.exists():
        out.write_bytes(resp.content)
        out.with_suffix(out.suffix + ".url").write_text(url, encoding="utf-8")
    return out


def discover_related_links(html: bytes, base_url: str) -> list[str]:
    """Find attachment/detail links from one saved admission-plan page."""
    text_html = html.decode("utf-8", errors="ignore")
    link_items: list[tuple[str, str]] = []
    try:
        from bs4 import BeautifulSoup
    except Exception:  # noqa: BLE001
        for match in re.finditer(
            r"<a\b[^>]*href=[\"'](?P<href>[^\"']+)[\"'][^>]*>(?P<label>.*?)</a>",
            text_html,
            flags=re.I | re.S,
        ):
            label = re.sub(r"<[^>]+>", "", match.group("label"))
            link_items.append((label, match.group("href")))
    else:
        soup = BeautifulSoup(text_html, "lxml")
        link_items = [
            (" ".join(a.get_text(" ", strip=True).split()), a["href"])
            for a in soup.find_all("a", href=True)
        ]

    urls: list[str] = []
    for label, raw_href in link_items:
        href = urljoin(base_url, raw_href)
        suffix = Path(urlparse(href).path).suffix.lower()
        text = f"{label} {href}"
        if suffix in ATTACHMENT_EXTS or any(keyword in text for keyword in PLAN_LINK_KEYWORDS):
            if href not in urls:
                urls.append(href)
    return urls


def discover_nav_links(html: bytes, base_url: str, *, year: int) -> list[str]:
    """Find likely plan/navigation links on an admission website."""
    text_html = html.decode("utf-8", errors="ignore")
    try:
        from bs4 import BeautifulSoup
    except Exception:  # noqa: BLE001
        matches = re.finditer(
            r"<a\b[^>]*href=[\"'](?P<href>[^\"']+)[\"'][^>]*>(?P<label>.*?)</a>",
            text_html,
            flags=re.I | re.S,
        )
        link_items = [
            (re.sub(r"<[^>]+>", "", match.group("label")), match.group("href"))
            for match in matches
        ]
    else:
        soup = BeautifulSoup(text_html, "lxml")
        link_items = [
            (" ".join(a.get_text(" ", strip=True).split()), a["href"])
            for a in soup.find_all("a", href=True)
        ]

    urls: list[str] = []
    for label, raw_href in link_items:
        href = urljoin(base_url, raw_href)
        text = f"{label} {href}"
        if str(year) in text or any(keyword in text for keyword in PLAN_NAV_KEYWORDS):
            if href not in urls:
                urls.append(href)
    return urls


def download_with_related(
    url: str,
    *,
    year: int,
    school_name: str,
    index: int,
    allow_zsgk: bool,
) -> int:
    """Download a search hit and its obvious attachment/detail links."""
    path = download_url(url, year=year, school_name=school_name, index=index)
    if not path:
        return 0

    saved = 1
    if path.suffix.lower() not in {".html", ".htm"}:
        return saved

    for child_index, child_url in enumerate(discover_related_links(path.read_bytes(), url), start=1):
        if is_blocked_url(child_url, allow_zsgk=allow_zsgk):
            continue
        child = download_url(
            child_url,
            year=year,
            school_name=f"{school_name}_附件",
            index=index * 100 + child_index,
        )
        if child:
            saved += 1
    return saved


def crawl_admission_site(
    *,
    year: int,
    school_name: str,
    allow_zsgk: bool,
    max_pages: int = 24,
) -> int:
    """Crawl one official admission site for likely Jiangsu plan pages."""
    site = admission_site_for_school(school_name)
    if not site:
        return 0
    try:
        resp = requests.get(site, headers=HEADERS, timeout=18)
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        print(f"  招生网打不开 {school_name} {site}: {exc}")
        return 0

    queue = discover_nav_links(resp.content, site, year=year)
    seen = {site}
    saved = 0
    index = 1
    while queue and len(seen) <= max_pages:
        url = queue.pop(0)
        if url in seen or is_blocked_url(url, allow_zsgk=allow_zsgk):
            continue
        seen.add(url)
        if not same_site(url, site):
            continue
        count = download_with_related(
            url,
            year=year,
            school_name=school_name,
            index=index,
            allow_zsgk=allow_zsgk,
        )
        saved += count
        index += 1
        # Follow plan-index pages even if they were not specific to Jiangsu yet.
        try:
            child_resp = requests.get(url, headers=HEADERS, timeout=18)
            child_resp.raise_for_status()
        except Exception:  # noqa: BLE001
            continue
        for child in discover_nav_links(child_resp.content, url, year=year):
            if child not in seen and child not in queue:
                queue.append(child)
    return saved


def query_for_school(year: int, school_name: str) -> str:
    return f'"{school_name}" 江苏 {year} 招生计划 专业组 专业'


def queries_for_school(year: int, school_name: str) -> list[str]:
    return [
        f'"{school_name}" "{year}年江苏" "招生计划" "专业组"',
        f'"{school_name}" 江苏 {year} 招生计划 专业组 专业',
        f'"{school_name}" "{year}" "江苏省招生计划"',
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=int, nargs="+", default=list(DEFAULT_YEARS))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--school", action="append", default=[], help="只抓指定学校名，可重复")
    parser.add_argument("--url", action="append", default=[], help="直接下载指定计划源 URL，可重复")
    parser.add_argument("--search-too", action="store_true", help="配合 --url 使用时仍继续搜索学校")
    parser.add_argument("--max-results", type=int, default=6)
    parser.add_argument("--delay", type=float, default=1.0)
    parser.add_argument("--allow-zsgk", action="store_true", help="允许下载掌上高考页面")
    parser.add_argument("--skip-site-crawl", action="store_true", help="跳过招生官网站内发现")
    parser.add_argument("--search-web", action="store_true", help="启用 DDGS 搜索引擎补充")
    args = parser.parse_args()

    total_saved = 0
    if args.url:
        school_name = args.school[0] if args.school else "manual"
        for year in args.years:
            for url_index, url in enumerate(args.url, start=1):
                if is_blocked_url(url, allow_zsgk=args.allow_zsgk):
                    print(f"跳过被屏蔽域名：{url}")
                    continue
                count = download_with_related(
                    url,
                    year=year,
                    school_name=school_name,
                    index=url_index,
                    allow_zsgk=args.allow_zsgk,
                )
                total_saved += count
                print(f"[manual {year}] {url}: 保存 {count} 个源")
        if not args.search_too:
            print(f"完成：保存源文件 {total_saved} 个")
            return

    for year in args.years:
        schools = official_school_names(year)
        if args.school:
            wanted = set(args.school)
            schools = [school for school in schools if school in wanted]
        if args.limit:
            schools = schools[: args.limit]
        print(f"=== {year}: 搜索 {len(schools)} 所 ===")
        for school_index, school_name in enumerate(schools, start=1):
            saved = 0
            if not args.skip_site_crawl:
                saved += crawl_admission_site(
                    year=year,
                    school_name=school_name,
                    allow_zsgk=args.allow_zsgk,
                )
            if args.search_web:
                urls: list[str] = []
                for query in queries_for_school(year, school_name):
                    try:
                        for url in search_urls(query, max_results=args.max_results):
                            if url not in urls:
                                urls.append(url)
                    except Exception as exc:  # noqa: BLE001
                        print(f"[{school_index}/{len(schools)}] {school_name}: 搜索失败 {exc}")
                        continue
                for url_index, url in enumerate(urls, start=1):
                    if is_blocked_url(url, allow_zsgk=args.allow_zsgk):
                        continue
                    saved += download_with_related(
                        url,
                        year=year,
                        school_name=school_name,
                        index=1000 + url_index,
                        allow_zsgk=args.allow_zsgk,
                    )
            total_saved += saved
            print(f"[{school_index}/{len(schools)}] {school_name}: 保存 {saved} 个源")
            time.sleep(args.delay)
    print(f"完成：保存源文件 {total_saved} 个")


if __name__ == "__main__":
    main()
