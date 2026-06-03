"""Fetch and parse official Jiangsu admission data from jseea.cn.

This script targets the data needed by the Jiangsu 院校专业组 pipeline:

* 普通类本科批次平行志愿投档线: school + professional group + min_score.
* 普通高考逐分段统计表: score -> cumulative rank.

The output is normalized CSV under data/jiangsu/raw/official/:

* cutoff_{year}_{physics|history}.csv
* score_rank_{year}_{physics|history}.csv

Run:
    python scripts/fetch_jiangsu_official.py
    python scripts/fetch_jiangsu_official.py --years 2025 --subjects physics
"""

from __future__ import annotations

import argparse
import csv
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse

import pandas as pd
import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = PROJECT_ROOT / "data" / "jiangsu" / "raw"
OFFICIAL_DIR = RAW_DIR / "official"
DOWNLOAD_DIR = OFFICIAL_DIR / "downloads"

INDEX_ROOT = "https://www.jseea.cn/webfile/index/index_zkxx/"
LX91_SCORE_RANK_API = "https://api.lx91.com/api/v1/seating/list"
DEFAULT_YEARS = (2025, 2024, 2023)
SUBJECTS = {
    "physics": ("物理", "物理类"),
    "history": ("历史", "历史类"),
}
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36"
    )
}


@dataclass(frozen=True)
class Article:
    title: str
    url: str


def _slug_subject(subject_key: str) -> str:
    if subject_key not in SUBJECTS:
        raise ValueError(f"unknown subject key: {subject_key}")
    return subject_key


def _get(url: str) -> requests.Response:
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp


def _index_url(page: int) -> str:
    # jseea uses index.html, index_2.html, index_3.html... (no index_1.html).
    return urljoin(INDEX_ROOT, "index.html" if page <= 1 else f"index_{page}.html")


def discover_articles(max_pages: int = 80, sleep_seconds: float = 0.4) -> list[Article]:
    """Crawl the 招考信息 list pages and collect article links."""

    from bs4 import BeautifulSoup

    articles: list[Article] = []
    seen: set[str] = set()
    for page in range(max_pages):
        url = _index_url(page)
        try:
            html = _get(url).text
        except Exception as exc:  # noqa: BLE001
            print(f"跳过索引页 {url}: {exc}", file=sys.stderr)
            continue
        soup = BeautifulSoup(html, "lxml")
        page_count = 0
        for a in soup.find_all("a", href=True):
            title = " ".join(a.get_text(" ", strip=True).split())
            href = urljoin(url, a["href"])
            if not title or "/webfile/index/index_zkxx/" not in href or not href.endswith(".html"):
                continue
            if href in seen:
                continue
            seen.add(href)
            page_count += 1
            articles.append(Article(title=title, url=href))
        if page_count == 0 and page > 5:
            break
        time.sleep(sleep_seconds)
    return articles


def _contains_all(text: str, keywords: list[str]) -> bool:
    return all(keyword in text for keyword in keywords)


def find_article(
    articles: list[Article],
    *,
    year: int,
    kind: str,
    subject_key: str,
) -> Article | None:
    """Find the best official article for a year/kind/subject."""

    subject, _category = SUBJECTS[subject_key]
    if kind == "cutoff":
        keyword_sets = [
            [f"{year}年", "普通类本科批次", "平行志愿投档线", f"{subject}等科目类"],
            [f"{year}年", "本科批次", "投档线", f"{subject}等科目类"],
            [f"{year}年", "普通类本科批次", "平行志愿投档线"],
            [f"{year}年", "本科批次", "平行志愿投档线"],
        ]
    elif kind == "score_rank":
        keyword_sets = [
            [f"{year}年", "逐分段统计表", f"{subject}等科目类"],
            [f"{year}年", "逐分段统计表", subject],
            [f"{year}年", "第一阶段逐分段统计表"],
        ]
    else:
        raise ValueError(f"unknown kind: {kind}")

    for keywords in keyword_sets:
        for article in articles:
            title = article.title.replace(" ", "")
            if kind == "cutoff" and "征求" in title:
                continue
            if _contains_all(title, keywords):
                return article
    return None


def attachment_links(article_url: str) -> list[tuple[str, str]]:
    """Return (title, absolute_url) attachment/page links from one article."""

    from bs4 import BeautifulSoup

    html = _get(article_url).text
    soup = BeautifulSoup(html, "lxml")
    links: list[tuple[str, str]] = []
    for a in soup.find_all("a", href=True):
        title = " ".join(a.get_text(" ", strip=True).split()) or Path(a["href"]).name
        href = urljoin(article_url, a["href"])
        lower = href.lower()
        if any(lower.endswith(ext) for ext in (".pdf", ".xls", ".xlsx", ".csv")):
            links.append((title, href))
    if not links:
        links.append(("article", article_url))
    return links


def choose_link(
    links: list[tuple[str, str]],
    *,
    subject_key: str,
    preferred_kind: str,
) -> tuple[str, str] | None:
    """Choose the best attachment for subject and parser kind."""

    subject, _category = SUBJECTS[subject_key]
    preferred_exts = {
        "cutoff": (".xlsx", ".xls", ".csv", ".pdf", ".html"),
        "score_rank": (".xlsx", ".xls", ".csv", ".pdf", ".html"),
    }[preferred_kind]

    candidates = [
        item for item in links
        if any(item[1].lower().endswith(ext) for ext in preferred_exts)
    ]
    if not candidates:
        return None

    subject_hits = [
        item for item in candidates
        if subject in item[0] or subject in item[1] or f"{subject}等科目类" in item[0]
    ]
    if subject_hits:
        return subject_hits[0]
    return candidates[0]


def _suffix_from_url(url: str, default: str = ".html") -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    return suffix if suffix else default


def download_link(url: str, target: Path) -> Path:
    """Download url to target, reusing existing files."""

    target.parent.mkdir(parents=True, exist_ok=True)
    url_marker = target.with_suffix(target.suffix + ".url")
    if (
        target.exists()
        and target.stat().st_size > 0
        and url_marker.exists()
        and url_marker.read_text(encoding="utf-8") == url
    ):
        return target
    resp = _get(url)
    target.write_bytes(resp.content)
    url_marker.write_text(url, encoding="utf-8")
    return target


def extract_text(path: Path) -> str:
    """Extract text from HTML/TXT/PDF. Excel is handled by pandas separately."""

    suffix = path.suffix.lower()
    if suffix in {".html", ".htm"}:
        from bs4 import BeautifulSoup

        try:
            html = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            html = path.read_text(encoding="gb18030", errors="ignore")
        return BeautifulSoup(html, "lxml").get_text("\n", strip=True)

    if suffix in {".txt", ".csv"}:
        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return path.read_text(encoding="gb18030", errors="ignore")

    if suffix != ".pdf":
        return ""

    pdftotext = shutil.which("pdftotext")
    if pdftotext:
        out = path.with_suffix(".txt")
        subprocess.run(
            [pdftotext, "-layout", str(path), str(out)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return out.read_text(encoding="utf-8", errors="ignore")

    try:
        from pypdf import PdfReader  # type: ignore
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("PDF 解析需要 pdftotext 或 pypdf") from exc

    reader = PdfReader(str(path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _clean_text(value: object) -> str:
    text = str(value or "").strip()
    text = text.replace("\u3000", " ")
    return re.sub(r"\s+", " ", text)


def parse_school_group(raw: str, subject_category: str) -> tuple[str, str, str]:
    """Parse '南京大学04专业组(不限)' into school, group no, requirement text."""

    text = _clean_text(raw).replace("（", "(").replace("）", ")")
    match = re.search(r"(.+?)(\d{2,3})\s*专业组(?:\(([^)]*)\))?", text)
    if not match:
        return text, "", ""

    school_name = match.group(1).strip()
    sg_name = match.group(2).strip()
    req = (match.group(3) or "").strip()
    first = "物理" if subject_category == "物理类" else "历史"
    if not req:
        sg_info = f"首选{first}"
    elif "首选" in req or "再选" in req:
        sg_info = req
    else:
        sg_info = f"首选{first}，再选{req}"
    return school_name, sg_name, sg_info


def parse_score_rank_text(
    text: str,
    *,
    year: int,
    subject_category: str,
    source_url: str,
) -> list[dict]:
    """Parse score-rank text containing rows/triples of 分数 人数 累计人数."""

    best: dict[int, tuple[int, int]] = {}
    for line in text.splitlines():
        line = _clean_text(line)
        if not line:
            continue
        nums = [int(x) for x in re.findall(r"\d+", line)]
        if len(nums) < 3:
            continue
        for i in range(0, len(nums) - 2, 3):
            score, count, cumulative = nums[i : i + 3]
            if 100 <= score <= 750 and 0 <= count <= cumulative:
                if score not in best or cumulative > best[score][1]:
                    best[score] = (count, cumulative)

    return [
        {
            "year": year,
            "subject_category": subject_category,
            "score": score,
            "same_score_count": count,
            "cumulative_rank": cumulative,
            "source_url": source_url,
        }
        for score, (count, cumulative) in sorted(best.items(), reverse=True)
    ]


def _cell_values(row: pd.Series) -> list[str]:
    return [_clean_text(v) for v in row.tolist() if _clean_text(v) and _clean_text(v).lower() != "nan"]


def _find_column(columns: list[str], keywords: list[str]) -> str | None:
    for column in columns:
        if all(k in column for k in keywords):
            return column
    return None


def parse_score_rank_frame(
    frame: pd.DataFrame,
    *,
    year: int,
    subject_category: str,
    source_url: str,
) -> list[dict]:
    columns = [_clean_text(c) for c in frame.columns]
    frame = frame.copy()
    frame.columns = columns
    score_col = _find_column(columns, ["分数"])
    count_col = _find_column(columns, ["人数"])
    rank_col = _find_column(columns, ["累计"])

    records: list[dict] = []
    if score_col and count_col and rank_col:
        for _, row in frame.iterrows():
            score = _parse_int(row.get(score_col))
            count = _parse_int(row.get(count_col))
            cumulative = _parse_int(row.get(rank_col))
            if score and count is not None and cumulative:
                records.append(
                    {
                        "year": year,
                        "subject_category": subject_category,
                        "score": score,
                        "same_score_count": count,
                        "cumulative_rank": cumulative,
                        "source_url": source_url,
                    }
                )
    if records:
        return _dedupe_score_rank(records)

    text = "\n".join(" ".join(_cell_values(row)) for _, row in frame.iterrows())
    return parse_score_rank_text(
        text, year=year, subject_category=subject_category, source_url=source_url
    )


def parse_cutoff_text(
    text: str,
    *,
    year: int,
    subject_category: str,
    source_url: str,
) -> list[dict]:
    """Parse 投档线 text rows."""

    records: list[dict] = []
    pending: list[dict] = []

    def base_record(school_code: str, group_text: str) -> dict | None:
        school_name, sg_name, sg_info = parse_school_group(group_text, subject_category)
        if not school_name or not sg_name:
            return None
        return {
            "year": year,
            "subject_category": subject_category,
            "school_code": school_code,
            "school_name": school_name,
            "special_group": f"{school_code}-{sg_name}",
            "sg_name": sg_name,
            "sg_info": sg_info,
            "min_score": "",
            "min_rank": "",
            "source_url": source_url,
        }

    for raw_line in text.splitlines():
        line = _clean_text(raw_line)
        if not re.match(r"^\d{4}\s+", line):
            if pending:
                for score_text in re.findall(r"(?<!\d)([3-7]\d{2})(?!\d)", line):
                    if not pending:
                        break
                    row = pending.pop(0)
                    row["min_score"] = int(score_text)
                    records.append(row)
            continue
        match = re.match(
            r"^(?P<code>\d{4})\s+(?P<group>.+?专业组(?:\([^)]*\))*)\s*(?P<tail>.*)$",
            line,
        )
        if not match:
            continue
        row = base_record(match.group("code"), match.group("group"))
        if row is None:
            continue
        score_match = re.search(r"(?<!\d)([3-7]\d{2})(?!\d)", match.group("tail"))
        if score_match:
            row["min_score"] = int(score_match.group(1))
            records.append(row)
        else:
            pending.append(row)
    return _dedupe_cutoffs(records)


def parse_cutoff_frame(
    frame: pd.DataFrame,
    *,
    year: int,
    subject_category: str,
    source_url: str,
) -> list[dict]:
    columns = [_clean_text(c) for c in frame.columns]
    frame = frame.copy()
    frame.columns = columns

    code_col = _find_column(columns, ["院校", "代号"]) or _find_column(columns, ["代号"])
    group_col = (
        _find_column(columns, ["院校", "专业组"])
        or _find_column(columns, ["院校、专业组"])
        or _find_column(columns, ["专业组"])
    )
    score_col = (
        _find_column(columns, ["投档", "最低", "分"])
        or _find_column(columns, ["最低", "分"])
    )

    records: list[dict] = []
    if code_col and group_col and score_col:
        for _, row in frame.iterrows():
            code_match = re.search(r"\d{4}", _clean_text(row.get(code_col)))
            min_score = _parse_int(row.get(score_col))
            group_text = _clean_text(row.get(group_col))
            if not code_match or not min_score or not group_text:
                continue
            school_code = code_match.group(0)
            school_name, sg_name, sg_info = parse_school_group(group_text, subject_category)
            if not sg_name:
                continue
            records.append(
                {
                    "year": year,
                    "subject_category": subject_category,
                    "school_code": school_code,
                    "school_name": school_name,
                    "special_group": f"{school_code}-{sg_name}",
                    "sg_name": sg_name,
                    "sg_info": sg_info,
                    "min_score": min_score,
                    "min_rank": "",
                    "source_url": source_url,
                }
            )
    if records:
        return _dedupe_cutoffs(records)

    text = "\n".join(" ".join(_cell_values(row)) for _, row in frame.iterrows())
    return parse_cutoff_text(
        text, year=year, subject_category=subject_category, source_url=source_url
    )


def _parse_int(value: object) -> int | None:
    text = _clean_text(value).replace(",", "")
    if not text or text.lower() == "nan" or text in {"-", "--", "—"}:
        return None
    match = re.search(r"\d+", text)
    return int(match.group(0)) if match else None


def _dedupe_score_rank(records: list[dict]) -> list[dict]:
    best: dict[int, dict] = {}
    for record in records:
        score = int(record["score"])
        if score not in best or int(record["cumulative_rank"]) > int(best[score]["cumulative_rank"]):
            best[score] = record
    return [best[score] for score in sorted(best, reverse=True)]


def _dedupe_cutoffs(records: list[dict]) -> list[dict]:
    best: dict[tuple, dict] = {}
    for record in records:
        key = (
            record["year"],
            record["subject_category"],
            record["school_code"],
            record["special_group"],
        )
        best[key] = record
    return list(best.values())


def _read_frames(path: Path) -> list[pd.DataFrame]:
    suffix = path.suffix.lower()
    if suffix in {".xls", ".xlsx"}:
        sheets = pd.read_excel(path, sheet_name=None)
        return [frame for frame in sheets.values() if not frame.empty]
    if suffix == ".csv":
        return [pd.read_csv(path)]
    if suffix in {".html", ".htm"}:
        try:
            return pd.read_html(path, flavor="lxml")
        except (ImportError, ValueError):
            return []
    return []


def parse_source(
    path: Path,
    *,
    kind: str,
    year: int,
    subject_category: str,
    source_url: str,
) -> list[dict]:
    """Parse a downloaded source file."""

    records: list[dict] = []
    for frame in _read_frames(path):
        if kind == "cutoff":
            records.extend(
                parse_cutoff_frame(
                    frame,
                    year=year,
                    subject_category=subject_category,
                    source_url=source_url,
                )
            )
        else:
            records.extend(
                parse_score_rank_frame(
                    frame,
                    year=year,
                    subject_category=subject_category,
                    source_url=source_url,
                )
            )
    if records:
        return _dedupe_cutoffs(records) if kind == "cutoff" else _dedupe_score_rank(records)

    text = extract_text(path)
    if kind == "cutoff":
        return parse_cutoff_text(
            text,
            year=year,
            subject_category=subject_category,
            source_url=source_url,
        )
    return parse_score_rank_text(
        text,
        year=year,
        subject_category=subject_category,
        source_url=source_url,
    )


def apply_rank_lookup(cutoffs: list[dict], score_ranks: list[dict]) -> list[dict]:
    rank_by_score = {int(r["score"]): int(r["cumulative_rank"]) for r in score_ranks}
    merged: list[dict] = []
    for record in cutoffs:
        row = dict(record)
        row["min_rank"] = rank_by_score.get(int(row["min_score"]), "")
        merged.append(row)
    return merged


def fetch_lx91_score_rank(*, year: int, subject_category: str) -> list[dict]:
    """Fallback score-rank source when jseea publishes only an image.

    lx91 mirrors the one-score-one-rank table as JSON. We only use it for the
    mechanical score -> cumulative rank conversion; cutoff rows still come from
    official Jiangsu admissions data.
    """
    resp = requests.post(
        LX91_SCORE_RANK_API,
        json={"province": "江苏", "year": str(year), "km": subject_category},
        headers=HEADERS,
        timeout=20,
    )
    resp.raise_for_status()
    payload = resp.json()
    items = ((payload.get("data") or {}).get("list") or [])
    rows: list[dict] = []
    for item in items:
        score_text = _clean_text(item.get("score"))
        count = _parse_int(item.get("num"))
        cumulative = _parse_int(item.get("total"))
        if cumulative is None:
            continue
        range_match = re.fullmatch(r"(\d+)\s*-\s*(\d+)", score_text)
        if range_match:
            lo, hi = map(int, range_match.groups())
            scores = range(min(lo, hi), max(lo, hi) + 1)
        else:
            score = _parse_int(score_text)
            scores = [score] if score is not None else []
        for score in scores:
            if 100 <= score <= 750:
                rows.append(
                    {
                        "year": year,
                        "subject_category": subject_category,
                        "score": score,
                        "same_score_count": count if count is not None else "",
                        "cumulative_rank": cumulative,
                        "source_url": LX91_SCORE_RANK_API,
                    }
                )
    return _dedupe_score_rank(rows)


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def _target_path(kind: str, year: int, subject_key: str) -> Path:
    suffix = _slug_subject(subject_key)
    return OFFICIAL_DIR / f"{kind}_{year}_{suffix}.csv"


def collect_one(
    articles: list[Article],
    *,
    year: int,
    subject_key: str,
    kind: str,
    no_download: bool,
) -> tuple[list[dict], str]:
    """Download/reuse and parse one source."""

    subject, subject_category = SUBJECTS[subject_key]
    article = find_article(articles, year=year, kind=kind, subject_key=subject_key)
    if article is None:
        if kind == "score_rank":
            records = fetch_lx91_score_rank(year=year, subject_category=subject_category)
            if records:
                print(f"{year} {subject} score_rank: 未发现官方页，使用 lx91 JSON fallback")
                return records, LX91_SCORE_RANK_API
        print(f"未找到官方页面：{year} {subject} {kind}")
        return [], ""

    links = attachment_links(article.url)
    chosen = choose_link(links, subject_key=subject_key, preferred_kind=kind)
    if chosen is None:
        print(f"未找到附件：{year} {subject} {kind} {article.url}")
        return [], article.url

    title, url = chosen
    suffix = _suffix_from_url(url)
    download_path = DOWNLOAD_DIR / f"{kind}_{year}_{subject_key}{suffix}"
    if no_download and not download_path.exists():
        print(f"缺少本地文件，跳过：{download_path}")
        return [], url
    path = download_link(url, download_path)
    records = parse_source(
        path,
        kind=kind,
        year=year,
        subject_category=subject_category,
        source_url=url,
    )
    if kind == "score_rank" and not records:
        records = fetch_lx91_score_rank(year=year, subject_category=subject_category)
        if records:
            print(f"{year} {subject} score_rank: 官方页为图片，使用 lx91 JSON fallback")
    print(f"{year} {subject} {kind}: {len(records)} 行 ← {title}")
    return records, url


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=int, nargs="+", default=list(DEFAULT_YEARS))
    parser.add_argument("--subjects", nargs="+", choices=SUBJECTS, default=list(SUBJECTS))
    parser.add_argument("--max-pages", type=int, default=80)
    parser.add_argument("--no-download", action="store_true", help="只解析已下载文件")
    args = parser.parse_args()

    OFFICIAL_DIR.mkdir(parents=True, exist_ok=True)
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    print("扫描江苏考试院招考信息目录…")
    articles = discover_articles(max_pages=args.max_pages)
    print(f"发现 {len(articles)} 个招考信息页面")

    cutoff_fields = [
        "year", "subject_category", "school_code", "school_name", "special_group",
        "sg_name", "sg_info", "min_score", "min_rank", "source_url",
    ]
    score_rank_fields = [
        "year", "subject_category", "score", "same_score_count",
        "cumulative_rank", "source_url",
    ]

    for year in args.years:
        for subject_key in args.subjects:
            score_ranks, _rank_url = collect_one(
                articles,
                year=year,
                subject_key=subject_key,
                kind="score_rank",
                no_download=args.no_download,
            )
            cutoffs, _cutoff_url = collect_one(
                articles,
                year=year,
                subject_key=subject_key,
                kind="cutoff",
                no_download=args.no_download,
            )
            cutoffs = apply_rank_lookup(cutoffs, score_ranks) if score_ranks else cutoffs

            score_rank_path = _target_path("score_rank", year, subject_key)
            cutoff_path = _target_path("cutoff", year, subject_key)
            write_csv(score_rank_path, score_ranks, score_rank_fields)
            write_csv(cutoff_path, cutoffs, cutoff_fields)
            matched = sum(1 for row in cutoffs if row.get("min_rank"))
            print(f"写入 {score_rank_path.name}: {len(score_ranks)} 行")
            print(f"写入 {cutoff_path.name}: {len(cutoffs)} 行，位次匹配 {matched}/{len(cutoffs)}")


if __name__ == "__main__":
    main()
