"""Fetch Shanghai admission raw CSVs.

Outputs:
    data/shanghai/raw/official/cutoff_{year}.csv
    data/shanghai/raw/official/score_rank_{year}.csv
    data/shanghai/raw/plan_details/plan_details_{year}.csv

Shanghai uses one 综合 rank pool and 院校专业组 volunteers. The structured
cutoff/plan source is 掌上高考 because it carries school_id and special_group;
official score-rank PDFs are used when machine-readable enough, with lx91 as a
fallback for the one-score-one-rank table.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = PROJECT_ROOT / "data" / "shanghai" / "raw"
OFFICIAL_DIR = RAW_DIR / "official"
DOWNLOAD_DIR = OFFICIAL_DIR / "downloads"
PLAN_DETAIL_DIR = RAW_DIR / "plan_details"
PLAN_API_DIR = RAW_DIR / "plan_api"

API_BASE = "https://api.zjzw.cn/web/api/"
SCORE_PROVINCE_URI = "apidata/api/gk/score/province"
PLAN_URI = "apidata/api/gkv3/plan/school"
SCHOOL_LIST_URI = "apidata/api/gk/school/lists"
LX91_SCORE_RANK_API = "https://api.lx91.com/api/v1/seating/list"

SHANGHAI_PROVINCE_ID = 31
SUBJECT_CATEGORY = "综合"
DEFAULT_YEARS = (2025, 2024, 2023)
PAGE_SIZE = 100
DEFAULT_DELAY = 0.12
BACKOFFS = (2.0, 6.0, 15.0)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36"
    ),
    "Referer": "https://www.gaokao.cn/",
}

OFFICIAL_SCORE_RANK_URLS = {
    2024: "https://www.shmeea.edu.cn/download/20240623/00.pdf",
    2023: "https://www.shmeea.edu.cn/download/20230623/2_0.pdf",
}


class ApiError(RuntimeError):
    """Raised when a remote API repeatedly fails."""


def clean_text(value: object) -> str:
    text = str(value or "").strip()
    text = text.replace("\u3000", " ").replace("（", "(").replace("）", ")")
    return re.sub(r"\s+", " ", text)


def parse_int(value: object) -> int | None:
    text = clean_text(value).replace(",", "")
    if not text or text.lower() == "nan" or text in {"-", "--", "—", "None"}:
        return None
    match = re.search(r"-?\d+", text)
    return int(match.group(0)) if match else None


def normalize_subject_text(value: object) -> str:
    """Normalize Shanghai subject aliases while preserving Shanghai-style text."""
    text = clean_text(value)
    text = text.replace("生命科学", "生物")
    text = text.replace("思想政治", "__POLITICS__")
    text = text.replace("政治", "思想政治")
    text = text.replace("__POLITICS__", "思想政治")
    return text


def normalize_sg_name(value: object) -> str:
    text = clean_text(value)
    match = re.search(r"([A-Za-z]?\d{1,3}|Q\d+)", text, flags=re.I)
    return match.group(1).upper() if match else text


def api_source_url(uri: str, **params: object) -> str:
    query = urlencode(
        {
            "local_province_id": SHANGHAI_PROVINCE_ID,
            "uri": uri,
            **{k: v for k, v in params.items() if v is not None},
        }
    )
    return f"{API_BASE}?{query}"


def api_get(params: dict, delay: float = 0.0, empty_list_ok: bool = False) -> dict:
    """Return the data dict from one 掌上高考 API request."""
    last_payload: object = None
    for wait in (0.0, *BACKOFFS):
        if wait:
            time.sleep(wait)
        resp = requests.get(API_BASE, params=params, headers=HEADERS, timeout=30)
        try:
            payload = resp.json()
        except ValueError as exc:
            raise ApiError(f"non-json response: {resp.status_code}") from exc
        last_payload = payload
        if str(payload.get("code")) == "0000" and isinstance(payload.get("data"), dict):
            if delay:
                time.sleep(delay)
            return payload["data"]
        if (
            str(payload.get("code")) == "0000"
            and isinstance(payload.get("data"), list)
            and empty_list_ok
        ):
            if delay:
                time.sleep(delay)
            return {"item": [], "numFound": 0}
    raise ApiError(f"API failed: {last_payload}")


def fetch_api_pages(
    *,
    uri: str,
    year: int,
    delay: float,
    school_id: str | int | None = None,
) -> list[dict]:
    rows: list[dict] = []
    page = 1
    while True:
        params: dict[str, object] = {
            "local_province_id": SHANGHAI_PROVINCE_ID,
            "page": page,
            "size": PAGE_SIZE,
            "uri": uri,
            "year": year,
        }
        if school_id is not None:
            params["school_id"] = school_id
        data = api_get(params, delay=delay, empty_list_ok=school_id is not None)
        items = data.get("item") or []
        if not items:
            break
        rows.extend(items)
        num_found = parse_int(data.get("numFound")) or 0
        if len(rows) >= num_found or len(items) < PAGE_SIZE:
            break
        page += 1
    return rows


def fetch_school_list(year: int, delay: float) -> list[dict]:
    cache = RAW_DIR / "school_list.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))

    schools: list[dict] = []
    page = 1
    while True:
        data = api_get(
            {
                "local_province_id": SHANGHAI_PROVINCE_ID,
                "page": page,
                "size": 30,
                "uri": SCHOOL_LIST_URI,
                "year": year,
            },
            delay=delay,
        )
        items = data.get("item") or []
        if not items:
            break
        for item in items:
            school_id = parse_int(item.get("school_id"))
            name = clean_text(item.get("name"))
            if school_id is not None and name:
                schools.append({"school_id": str(school_id), "name": name})
        num_found = parse_int(data.get("numFound")) or 0
        if len(schools) >= num_found or len(items) < 30:
            break
        page += 1
    cache.write_text(json.dumps(schools, ensure_ascii=False), encoding="utf-8")
    return schools


def is_undergraduate_regular_batch(item: dict) -> bool:
    return (
        clean_text(item.get("local_type_name")) == SUBJECT_CATEGORY
        and clean_text(item.get("local_batch_name")) == "本科批"
        and clean_text(item.get("zslx_name") or "普通类") in {"", "普通类", "-"}
    )


def cutoff_row_from_api(item: dict, rank_by_score: dict[int, int], year: int) -> dict | None:
    if not is_undergraduate_regular_batch(item):
        return None
    school_id = parse_int(item.get("school_id"))
    school_name = clean_text(item.get("name"))
    sg_name = normalize_sg_name(item.get("sg_name"))
    special_group = clean_text(item.get("special_group"))
    min_score = parse_int(item.get("min"))
    if school_id is None or not school_name or not sg_name or not special_group or min_score is None:
        return None
    min_rank = parse_int(item.get("min_section"))
    if min_rank is None:
        min_rank = rank_by_score.get(min_score)
    return {
        "year": year,
        "subject_category": SUBJECT_CATEGORY,
        "school_code": str(school_id),
        "school_name": school_name,
        "special_group": special_group,
        "sg_name": sg_name,
        "sg_info": normalize_subject_text(item.get("sg_info")),
        "min_score": min_score,
        "min_rank": min_rank or "",
        "source_url": api_source_url(SCORE_PROVINCE_URI, year=year),
    }


def fetch_cutoffs(year: int, rank_by_score: dict[int, int], delay: float) -> list[dict]:
    try:
        rows = fetch_api_pages(uri=SCORE_PROVINCE_URI, year=year, delay=delay)
        out_raw = DOWNLOAD_DIR / f"cutoff_api_{year}.json"
        out_raw.parent.mkdir(parents=True, exist_ok=True)
        out_raw.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    except ApiError:
        print(f"{year}: 全省 score/province 分页被限频，改为按学校抓取", flush=True)
        rows = fetch_cutoffs_by_school(year, delay=delay)

    cutoffs: list[dict] = []
    for item in rows:
        row = cutoff_row_from_api(item, rank_by_score, year)
        if row is not None:
            cutoffs.append(row)
    return dedupe(cutoffs, ("year", "subject_category", "school_code", "special_group"))


def fetch_cutoffs_by_school(year: int, delay: float) -> list[dict]:
    schools = fetch_school_list(year, delay=delay)
    rows: list[dict] = []
    out_dir = DOWNLOAD_DIR / "score_api" / str(year)
    out_dir.mkdir(parents=True, exist_ok=True)
    for index, school in enumerate(schools, start=1):
        school_id = school["school_id"]
        out = out_dir / f"{school_id}.json"
        if out.exists():
            items = json.loads(out.read_text(encoding="utf-8"))
        else:
            try:
                items = fetch_api_pages(
                    uri=SCORE_PROVINCE_URI,
                    year=year,
                    school_id=school_id,
                    delay=delay,
                )
            except ApiError as exc:
                print(f"{year} school_id={school_id} cutoff 失败：{exc}", file=sys.stderr)
                continue
            if items:
                out.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
        rows.extend(items)
        if index % 300 == 0:
            print(f"  {year} cutoff: {index}/{len(schools)} schools", flush=True)
    return rows


def download(url: str, target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    marker = target.with_suffix(target.suffix + ".url")
    if target.exists() and target.stat().st_size > 0 and marker.exists():
        if marker.read_text(encoding="utf-8").strip() == url:
            return target
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    target.write_bytes(resp.content)
    marker.write_text(url, encoding="utf-8")
    return target


def extract_pdf_text_raw(path: Path) -> str:
    pdftotext = shutil.which("pdftotext")
    if pdftotext:
        out = path.with_suffix(".txt")
        subprocess.run(
            [pdftotext, "-raw", str(path), str(out)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return out.read_text(encoding="utf-8", errors="ignore")

    from pypdf import PdfReader  # type: ignore

    reader = PdfReader(str(path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def parse_score_rank_text(text: str, *, year: int, source_url: str) -> list[dict]:
    rows: list[dict] = []
    for line in text.splitlines():
        line = clean_text(line)
        if not line or "分数" in line or "累计" in line:
            continue
        match = re.match(r"^(?P<score>\d{3})(?:分及以上)?\s+(?P<count>\d+)\s+(?P<rank>\d+)$", line)
        if not match:
            continue
        score = int(match.group("score"))
        count = int(match.group("count"))
        cumulative = int(match.group("rank"))
        if "分及以上" in line:
            for expanded_score in range(score, 661):
                rows.append(score_rank_row(year, expanded_score, count, cumulative, source_url))
        else:
            rows.append(score_rank_row(year, score, count, cumulative, source_url))
    return dedupe(rows, ("year", "subject_category", "score"))


def score_rank_row(
    year: int,
    score: int,
    same_score_count: int | str,
    cumulative_rank: int,
    source_url: str,
) -> dict:
    return {
        "year": year,
        "subject_category": SUBJECT_CATEGORY,
        "score": score,
        "same_score_count": same_score_count,
        "cumulative_rank": cumulative_rank,
        "source_url": source_url,
    }


def fetch_lx91_score_rank(year: int) -> list[dict]:
    resp = requests.post(
        LX91_SCORE_RANK_API,
        json={"province": "上海", "year": str(year), "km": SUBJECT_CATEGORY},
        headers=HEADERS,
        timeout=20,
    )
    resp.raise_for_status()
    items = ((resp.json().get("data") or {}).get("list") or [])
    rows: list[dict] = []
    for item in items:
        score_text = clean_text(item.get("score"))
        count = parse_int(item.get("num"))
        cumulative = parse_int(item.get("total"))
        if cumulative is None:
            continue
        range_match = re.fullmatch(r"(\d+)\s*-\s*(\d+)", score_text)
        if range_match:
            lo, hi = map(int, range_match.groups())
            scores = range(min(lo, hi), max(lo, hi) + 1)
        else:
            parsed = parse_int(score_text)
            scores = [parsed] if parsed is not None else []
        for score in scores:
            if 0 <= score <= 660:
                rows.append(
                    score_rank_row(
                        year,
                        score,
                        count if count is not None else "",
                        cumulative,
                        LX91_SCORE_RANK_API,
                    )
                )
    return dedupe(rows, ("year", "subject_category", "score"))


def fetch_score_rank(year: int) -> list[dict]:
    url = OFFICIAL_SCORE_RANK_URLS.get(year)
    if url:
        try:
            suffix = Path(url).suffix or ".pdf"
            path = download(url, DOWNLOAD_DIR / f"score_rank_{year}{suffix}")
            rows = parse_score_rank_text(extract_pdf_text_raw(path), year=year, source_url=url)
            if rows:
                return rows
        except Exception as exc:  # noqa: BLE001
            print(f"{year} 官方成绩分布表解析失败，使用 lx91：{exc}", file=sys.stderr)
    return fetch_lx91_score_rank(year)


def plan_row_from_api(item: dict, official_groups: set[tuple[int, str, str]]) -> dict | None:
    if not is_undergraduate_regular_batch(item):
        return None
    year = parse_int(item.get("year"))
    school_id = parse_int(item.get("school_id"))
    school_name = clean_text(item.get("name"))
    special_group = clean_text(item.get("special_group"))
    major_name = clean_text(item.get("spname") or item.get("sp_name"))
    if year is None or school_id is None or not school_name or not special_group or not major_name:
        return None
    source_url = api_source_url(PLAN_URI, year=year, school_id=school_id)
    key = (year, str(school_id), special_group)
    return {
        "year": year,
        "subject_category": SUBJECT_CATEGORY,
        "school_code": str(school_id),
        "school_name": school_name,
        "special_group": special_group,
        "sg_name": normalize_sg_name(item.get("sg_name")),
        "sg_info": normalize_subject_text(item.get("sg_info")),
        "major_code": clean_text(item.get("spcode")),
        "major_name": normalize_subject_text(major_name),
        "plan_count": parse_int(item.get("num")) or "",
        "tuition": parse_int(item.get("tuition")) or "",
        "duration": clean_text(item.get("length")),
        "source_url": source_url,
        "source_file": str(
            (PLAN_API_DIR / str(year) / f"{school_id}.json")
            .resolve()
            .relative_to(PROJECT_ROOT)
        ),
        "matched_official_group": 1 if key in official_groups else 0,
    }


def fetch_plan_details(
    *,
    year: int,
    school_ids: set[str],
    official_groups: set[tuple[int, str, str]],
    delay: float,
) -> list[dict]:
    rows: list[dict] = []
    for index, school_id in enumerate(sorted(school_ids, key=lambda x: int(x)), start=1):
        try:
            items = fetch_api_pages(uri=PLAN_URI, year=year, school_id=school_id, delay=delay)
        except ApiError as exc:
            print(f"{year} school_id={school_id} plan 失败：{exc}", file=sys.stderr)
            continue
        out = PLAN_API_DIR / str(year) / f"{school_id}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
        for item in items:
            row = plan_row_from_api(item, official_groups)
            if row is not None:
                rows.append(row)
        if index % 100 == 0:
            print(f"  {year} plan: {index}/{len(school_ids)} schools", flush=True)
    return dedupe(
        rows,
        ("year", "subject_category", "school_code", "special_group", "major_code", "major_name"),
    )


def dedupe(rows: list[dict], keys: tuple[str, ...]) -> list[dict]:
    best: dict[tuple, dict] = {}
    for row in rows:
        best[tuple(row.get(k, "") for k in keys)] = row
    return list(best.values())


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in fieldnames} for row in rows)


def collect_year(year: int, delay: float) -> None:
    score_rank_fields = [
        "year",
        "subject_category",
        "score",
        "same_score_count",
        "cumulative_rank",
        "source_url",
    ]
    cutoff_fields = [
        "year",
        "subject_category",
        "school_code",
        "school_name",
        "special_group",
        "sg_name",
        "sg_info",
        "min_score",
        "min_rank",
        "source_url",
    ]
    plan_fields = [
        "year",
        "subject_category",
        "school_code",
        "school_name",
        "special_group",
        "sg_name",
        "sg_info",
        "major_code",
        "major_name",
        "plan_count",
        "tuition",
        "duration",
        "source_url",
        "source_file",
        "matched_official_group",
    ]

    score_ranks = fetch_score_rank(year)
    if not score_ranks:
        print(f"{year}: 未找到成绩分布表，跳过")
        return
    rank_by_score = {int(row["score"]): int(row["cumulative_rank"]) for row in score_ranks}
    cutoffs = fetch_cutoffs(year, rank_by_score, delay=delay)
    if not cutoffs:
        print(f"{year}: 未找到本科批投档线，跳过")
        return

    school_ids = {str(row["school_code"]) for row in cutoffs}
    official_groups = {
        (int(row["year"]), str(row["school_code"]), str(row["special_group"]))
        for row in cutoffs
    }
    plan_details = fetch_plan_details(
        year=year,
        school_ids=school_ids,
        official_groups=official_groups,
        delay=delay,
    )

    write_csv(OFFICIAL_DIR / f"score_rank_{year}.csv", score_ranks, score_rank_fields)
    write_csv(OFFICIAL_DIR / f"cutoff_{year}.csv", cutoffs, cutoff_fields)
    write_csv(PLAN_DETAIL_DIR / f"plan_details_{year}.csv", plan_details, plan_fields)

    matched_cutoff = sum(1 for row in cutoffs if row.get("min_rank"))
    matched_plan = sum(1 for row in plan_details if row.get("matched_official_group") == 1)
    print(
        f"{year}: score_rank {len(score_ranks)} 行；"
        f"cutoff {len(cutoffs)} 行，位次 {matched_cutoff}/{len(cutoffs)}；"
        f"plan_details {len(plan_details)} 行，匹配 {matched_plan}/{len(plan_details)}"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=int, nargs="+", default=list(DEFAULT_YEARS))
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY)
    args = parser.parse_args()

    OFFICIAL_DIR.mkdir(parents=True, exist_ok=True)
    PLAN_DETAIL_DIR.mkdir(parents=True, exist_ok=True)
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    for year in args.years:
        collect_year(year, delay=args.delay)


if __name__ == "__main__":
    main()
