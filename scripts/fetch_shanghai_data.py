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
STATIC_PLAN_BASE = "https://static-data.gaokao.cn/www/2.0/schoolspecialplan"

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
    2025: "https://www.shmeea.edu.cn/download/202506230/2/0.pdf",
    2024: "https://www.shmeea.edu.cn/download/20240623/00.pdf",
    2023: "https://www.shmeea.edu.cn/download/20230623/2_0.pdf",
}

OFFICIAL_CUTOFF_URLS = {
    2025: "https://www.shmeea.edu.cn/download/20250719/186.pdf",
    2024: "https://www.shmeea.edu.cn/download/20240719/198.pdf",
    2023: "https://www.shmeea.edu.cn/download/20230721/11115.pdf",
}

OFFICIAL_SCHOOL_ALIASES = {
    "上海交大": "上海交通大学",
    "上海交大医学": "上海交通大学医学院",
    "复旦医学": "复旦大学上海医学院",
    "华东师大": "华东师范大学",
    "华东理工": "华东理工大学",
    "上海财大": "上海财经大学",
    "上海外大": "上海外国语大学",
    "上海海事": "上海海事大学",
    "上海理工": "上海理工大学",
    "上海杉达": "上海杉达学院",
    "华东政法": "华东政法大学",
    "上经贸大": "上海对外经贸大学",
    "上海海洋": "上海海洋大学",
    "上海中医": "上海中医药大学",
    "上海师大": "上海师范大学",
    "上海电力": "上海电力大学",
    "上海健康": "上海健康医学院",
    "上海工技大": "上海工程技术大学",
    "上海电机": "上海电机学院",
    "上海二工大": "上海第二工业大学",
    "上海应技大": "上海应用技术大学",
    "上海立信": "上海立信会计金融学院",
    "上海政法": "上海政法学院",
    "上海商学": "上海商学院",
    "上外贤达": "上海外国语大学贤达经济人文学院",
    "上海建桥": "上海建桥学院",
    "上海兴伟": "上海兴伟学院",
    "上海中侨": "上海中侨职业技术大学",
    "上海视觉": "上海视觉艺术学院",
    "上海纽约": "上海纽约大学",
    "昆山杜克": "昆山杜克大学",
    "北京邮电": "北京邮电大学",
    "北京邮电(宏福)": "北京邮电大学(宏福校区)",
    "中国农大": "中国农业大学",
    "北京林大": "北京林业大学",
    "北京中医": "北京中医药大学",
    "北京师大": "北京师范大学",
    "北京外大": "北京外国语大学",
    "中央财大": "中央财经大学",
    "对外经贸": "对外经济贸易大学",
    "中国政法": "中国政法大学",
    "中国传媒": "中国传媒大学",
    "中央民族": "中央民族大学",
    "华北电力": "华北电力大学",
    "东北师大": "东北师范大学",
    "东北林大": "东北林业大学",
    "哈工程": "哈尔滨工程大学",
    "哈工大": "哈尔滨工业大学",
    "南京农大": "南京农业大学",
    "中国药大": "中国药科大学",
    "河海大学": "河海大学",
    "江南大学": "江南大学",
    "中国海大": "中国海洋大学",
    "华中科大": "华中科技大学",
    "华中农大": "华中农业大学",
    "华中师大": "华中师范大学",
    "中南财大": "中南财经政法大学",
    "华南理工": "华南理工大学",
    "西南财大": "西南财经大学",
    "西南交大": "西南交通大学",
    "西北农林": "西北农林科技大学",
    "电子科大": "电子科技大学",
    "西安交大": "西安交通大学",
    "西北工大": "西北工业大学",
}


class ApiError(RuntimeError):
    """Raised when a remote API repeatedly fails."""


class RateLimitError(ApiError):
    """Raised when 掌上高考 reports global request throttling."""


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
        if str(payload.get("code")) == "1069":
            raise RateLimitError(f"API rate limited: {payload}")
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


def _school_aliases(name: str) -> set[str]:
    aliases = {name}
    replacements = [
        ("交通大学", "交大"),
        ("师范大学", "师大"),
        ("师范学院", "师院"),
        ("财经大学", "财大"),
        ("外国语大学", "外大"),
        ("理工大学", "理工"),
        ("工业大学", "工大"),
        ("工程技术大学", "工技大"),
        ("科技大学", "科大"),
        ("农业大学", "农大"),
        ("林业大学", "林大"),
        ("中医药大学", "中医"),
        ("医科大学", "医大"),
        ("政法大学", "政法"),
        ("民族大学", "民族"),
    ]
    for old, new in replacements:
        if old in name:
            aliases.add(name.replace(old, new))
    for suffix in ("大学", "学院", "医学院", "职业技术大学", "职业技术学院"):
        if name.endswith(suffix):
            aliases.add(name[: -len(suffix)])
    return {clean_text(alias) for alias in aliases if clean_text(alias)}


def build_school_lookup(year: int, delay: float) -> dict[str, tuple[str, str]]:
    """Map official short school names to (school_id, full_name)."""
    schools = fetch_school_list(year, delay=delay)
    lookup: dict[str, tuple[str, str]] = {}
    alias_to_values: dict[str, set[tuple[str, str]]] = {}
    for school in schools:
        school_id = str(school["school_id"])
        name = clean_text(school["name"])
        lookup[name] = (school_id, name)
        for alias in _school_aliases(name):
            alias_to_values.setdefault(alias, set()).add((school_id, name))

    for alias, values in alias_to_values.items():
        if len(values) == 1 and alias not in lookup:
            lookup[alias] = next(iter(values))

    for short_name, full_name in OFFICIAL_SCHOOL_ALIASES.items():
        values = alias_to_values.get(full_name) or {(item["school_id"], item["name"]) for item in schools if item["name"] == full_name}
        if len(values) == 1:
            lookup[clean_text(short_name)] = next(iter(values))
    return lookup


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


def parse_official_cutoff_rows(
    *,
    year: int,
    rank_by_score: dict[int, int],
    school_lookup: dict[str, tuple[str, str]],
) -> list[dict]:
    url = OFFICIAL_CUTOFF_URLS.get(year)
    if not url:
        return []
    path = download(url, DOWNLOAD_DIR / f"cutoff_{year}.pdf")
    text = extract_pdf_text_raw(path)
    rows: list[dict] = []
    unmapped: set[str] = set()
    pattern = re.compile(
        r"^(?P<group_code>\d{5}[A-Z]?)\s+"
        r"(?P<school>.+?)\((?P<sg_name>[^()]+)\)\s+"
        r"(?P<score>\d{3}|\d{3}分及以上)(?:\s|$)"
    )
    for raw_line in text.splitlines():
        line = clean_text(raw_line)
        match = pattern.match(line)
        if not match:
            continue
        group_code = clean_text(match.group("group_code"))
        official_name = clean_text(match.group("school"))
        sg_name = normalize_sg_name(match.group("sg_name"))
        score_text = clean_text(match.group("score"))
        score = parse_int(score_text)
        if score is None:
            continue
        school_id, school_name = school_lookup.get(
            official_name,
            (group_code[:3], official_name),
        )
        if school_id == group_code[:3] and official_name not in school_lookup:
            unmapped.add(official_name)
        rows.append({
            "year": year,
            "subject_category": SUBJECT_CATEGORY,
            "school_code": str(school_id),
            "school_name": school_name,
            "special_group": group_code,
            "sg_name": sg_name,
            "sg_info": "",
            "min_score": score,
            "min_rank": rank_by_score.get(score, ""),
            "source_url": url,
        })
    if unmapped:
        sample = "、".join(sorted(unmapped)[:20])
        print(f"{year}: {len(unmapped)} 个官方简称未映射到 school_id，已用上海官方院校代码兜底：{sample}")
    return dedupe(rows, ("year", "subject_category", "school_code", "special_group"))


def fetch_cutoffs(year: int, rank_by_score: dict[int, int], delay: float) -> list[dict]:
    official_rows = parse_official_cutoff_rows(
        year=year,
        rank_by_score=rank_by_score,
        school_lookup=build_school_lookup(year, delay=delay),
    )
    if official_rows:
        return official_rows

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


def static_plan_source_url(year: int, school_id: str | int) -> str:
    return f"{STATIC_PLAN_BASE}/{school_id}/{year}/{SHANGHAI_PROVINCE_ID}.json?a=www.gaokao.cn"


def fetch_static_plan_items(year: int, school_id: str | int) -> tuple[list[dict], Path | None]:
    source_url = static_plan_source_url(year, school_id)
    out = PLAN_API_DIR / "static" / str(year) / f"{school_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        payload = json.loads(out.read_text(encoding="utf-8"))
    else:
        resp = requests.get(source_url, headers=HEADERS, timeout=30)
        if resp.status_code == 404:
            return [], None
        resp.raise_for_status()
        payload = resp.json()
        out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    data = payload.get("data") or {}
    if not isinstance(data, dict):
        return [], out
    items: list[dict] = []
    for group in data.values():
        if isinstance(group, dict):
            group_items = group.get("item") or []
            if isinstance(group_items, list):
                items.extend(group_items)
    return items, out


def is_static_undergraduate_regular_batch(item: dict) -> bool:
    return (
        clean_text(item.get("province")) == str(SHANGHAI_PROVINCE_ID)
        and clean_text(item.get("local_batch_name")) == "本科批"
        and clean_text(item.get("zslx_name") or "普通类") in {"", "普通类", "中外合作办学", "-"}
    )


def static_plan_row_from_item(
    item: dict,
    *,
    year: int,
    school_name: str,
    source_file: Path,
    official_group_names: set[tuple[int, str, str]],
) -> dict | None:
    if not is_static_undergraduate_regular_batch(item):
        return None
    school_id = parse_int(item.get("school_id"))
    special_group = clean_text(item.get("special_group"))
    sg_name = normalize_sg_name(item.get("sg_name"))
    major_name = clean_text(item.get("sp_name") or item.get("spname"))
    if school_id is None or not special_group or not sg_name or not major_name:
        return None
    major_code = clean_text(item.get("spcode"))
    if not major_code or major_code == "0":
        major_code = clean_text(item.get("special_id"))
    sg_info = normalize_subject_text(item.get("sg_info"))
    key = (year, str(school_id), sg_name)
    return {
        "year": year,
        "subject_category": SUBJECT_CATEGORY,
        "school_code": str(school_id),
        "school_name": school_name,
        "special_group": special_group,
        "sg_name": sg_name,
        "sg_info": sg_info,
        "major_code": major_code,
        "major_name": normalize_subject_text(major_name),
        "plan_count": parse_int(item.get("num")) or "",
        "tuition": parse_int(item.get("tuition")) or "",
        "duration": clean_text(item.get("length")),
        "source_url": static_plan_source_url(year, school_id),
        "source_file": str(source_file.resolve().relative_to(PROJECT_ROOT)),
        "matched_official_group": 1 if key in official_group_names else 0,
    }


def fetch_static_plan_details(
    *,
    year: int,
    school_ids: set[str],
    school_names: dict[str, str],
    official_group_names: set[tuple[int, str, str]],
) -> list[dict]:
    rows: list[dict] = []
    for index, school_id in enumerate(sorted(school_ids, key=lambda x: int(x)), start=1):
        try:
            items, source_file = fetch_static_plan_items(year, school_id)
        except Exception as exc:  # noqa: BLE001
            print(f"{year} school_id={school_id} static plan 失败：{exc}", file=sys.stderr)
            continue
        if source_file is None:
            continue
        school_name = school_names.get(str(school_id), "")
        for item in items:
            row = static_plan_row_from_item(
                item,
                year=year,
                school_name=school_name,
                source_file=source_file,
                official_group_names=official_group_names,
            )
            if row is not None:
                rows.append(row)
        if index % 200 == 0:
            print(f"  {year} static plan: {index}/{len(school_ids)} schools", flush=True)
    return dedupe(
        rows,
        ("year", "subject_category", "school_code", "special_group", "major_code", "major_name"),
    )


def remap_cutoffs_with_plan_groups(cutoffs: list[dict], plan_details: list[dict]) -> list[dict]:
    """Use static plan groups as the canonical Shanghai special_group ids."""
    group_map: dict[tuple[int, str, str], set[tuple[str, str]]] = {}
    for row in plan_details:
        year = parse_int(row.get("year"))
        school_code = clean_text(row.get("school_code"))
        sg_name = normalize_sg_name(row.get("sg_name"))
        special_group = clean_text(row.get("special_group"))
        sg_info = normalize_subject_text(row.get("sg_info"))
        if year is None or not school_code or not sg_name or not special_group:
            continue
        group_map.setdefault((year, school_code, sg_name), set()).add((special_group, sg_info))

    remapped: list[dict] = []
    ambiguous = 0
    missing = 0
    for row in cutoffs:
        item = dict(row)
        year = parse_int(item.get("year"))
        key = (year, clean_text(item.get("school_code")), normalize_sg_name(item.get("sg_name")))
        candidates = sorted(group_map.get(key, set()))
        if len(candidates) == 1:
            item["special_group"], item["sg_info"] = candidates[0]
        elif len(candidates) > 1:
            ambiguous += 1
        else:
            missing += 1
        remapped.append(item)
    if ambiguous or missing:
        print(f"  cutoff-plan 组映射：{len(cutoffs) - ambiguous - missing}/{len(cutoffs)}，"
              f"歧义 {ambiguous}，缺失 {missing}")
    return remapped


def fetch_plan_details(
    *,
    year: int,
    school_ids: set[str],
    official_groups: set[tuple[int, str, str]],
    delay: float,
) -> list[dict]:
    official_group_names = {
        (year_value, school_code, normalize_sg_name(group_id.split("-")[-1]))
        for year_value, school_code, group_id in official_groups
    }
    # This dynamic API is kept as a fallback. The main path is the static
    # schoolspecialplan JSON above, which does not trigger the 1069 rate limit.
    rows: list[dict] = []
    for index, school_id in enumerate(sorted(school_ids, key=lambda x: int(x)), start=1):
        try:
            items = fetch_api_pages(uri=PLAN_URI, year=year, school_id=school_id, delay=delay)
        except RateLimitError as exc:
            print(f"{year} plan API 被限频，停止组内专业抓取：{exc}", file=sys.stderr)
            break
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


def build_group_placeholders(cutoffs: list[dict]) -> list[dict]:
    """Fallback plan rows when group-inner plans are temporarily unavailable."""
    rows: list[dict] = []
    for row in cutoffs:
        sg_name = clean_text(row.get("sg_name"))
        school_name = clean_text(row.get("school_name"))
        rows.append({
            "year": row.get("year"),
            "subject_category": SUBJECT_CATEGORY,
            "school_code": row.get("school_code"),
            "school_name": school_name,
            "special_group": row.get("special_group"),
            "sg_name": sg_name,
            "sg_info": row.get("sg_info", ""),
            "major_code": "__GROUP__",
            "major_name": f"{school_name}{sg_name}专业组" if sg_name else f"{school_name}专业组",
            "plan_count": "",
            "tuition": "",
            "duration": "",
            "source_url": row.get("source_url", ""),
            "source_file": "",
            "matched_official_group": 1,
        })
    return rows


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
    school_names = {}
    for row in cutoffs:
        school_names.setdefault(str(row["school_code"]), str(row["school_name"]))
    official_group_names = {
        (int(row["year"]), str(row["school_code"]), normalize_sg_name(row["sg_name"]))
        for row in cutoffs
    }
    plan_details = fetch_static_plan_details(
        year=year,
        school_ids=school_ids,
        school_names=school_names,
        official_group_names=official_group_names,
    )
    if plan_details:
        cutoffs = remap_cutoffs_with_plan_groups(cutoffs, plan_details)
    else:
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
    if not plan_details:
        print(f"{year}: 未抓到组内专业明细，写入组级占位行以保持 raw CSV 可入库")
        plan_details = build_group_placeholders(cutoffs)

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
