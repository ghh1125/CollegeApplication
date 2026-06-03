"""Fetch Jiangsu 招生考试网 plan pages with group-inner majors.

Source pages look like:

    https://gaoxiao.jszs.com/College/plannew.html?cid=5&yearno=2025&ty=普通类&topchoose=物理&pici=本科批次

They contain 院校专业组 headers and group-inner major rows. This script maps
official cutoff school names to JSZS cids via the site's search page, downloads
physics/history plan pages, and stores them under:

    data/jiangsu/raw/plan_sources/{year}/

Normalization is handled by scripts/parse_jiangsu_plan_details.py.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import time
from html import unescape
from pathlib import Path
from urllib.parse import quote, urlencode

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = PROJECT_ROOT / "data" / "jiangsu" / "raw"
OFFICIAL_DIR = RAW_DIR / "official"
SOURCE_DIR = RAW_DIR / "plan_sources"
CID_CACHE_PATH = RAW_DIR / "jszs_cid_cache.json"

BASE_URL = "https://gaoxiao.jszs.com"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36"
    )
}
SUBJECTS = ("物理", "历史")


def safe_name(text: str) -> str:
    text = re.sub(r"[^\w\u4e00-\u9fa5()-]+", "_", text.strip())
    return text[:80] or "source"


def parse_int(value: object) -> int | None:
    text = str(value or "").strip().replace(",", "")
    match = re.search(r"\d+", text)
    return int(match.group(0)) if match else None


def official_school_names(year: int) -> list[str]:
    """Return official cutoff schools, sorted by best available rank."""
    best_metric: dict[str, tuple[int, int]] = {}
    for path in OFFICIAL_DIR.glob(f"cutoff_{year}_*.csv"):
        with path.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                name = (row.get("school_name") or "").strip()
                if not name:
                    continue
                rank = parse_int(row.get("min_rank"))
                score = parse_int(row.get("min_score"))
                metric = (rank if rank is not None else 10_000_000, -(score or 0))
                if name not in best_metric or metric < best_metric[name]:
                    best_metric[name] = metric
    return [name for name, _metric in sorted(best_metric.items(), key=lambda item: item[1])]


def load_cache() -> dict[str, int]:
    if not CID_CACHE_PATH.exists():
        return {}
    return {
        str(name): int(cid)
        for name, cid in json.loads(CID_CACHE_PATH.read_text(encoding="utf-8")).items()
    }


def save_cache(cache: dict[str, int]) -> None:
    CID_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CID_CACHE_PATH.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def search_cid(school_name: str, timeout: float) -> int | None:
    """Find one exact JSZS cid by school name."""
    url = (
        f"{BASE_URL}/College/index/sname/{quote(school_name)}/"
        "s/0-0-0-0-0-0-0-0.html"
    )
    resp = requests.get(url, headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
    matches: list[tuple[str, int]] = []
    html = resp.text
    for match in re.finditer(
        r"<a\b[^>]*href=[\"'](?P<href>[^\"']*/College/home/cid/(?P<cid>\d+)\.html)[\"'][^>]*>"
        r"(?P<label>.*?)</a>",
        html,
        flags=re.I | re.S,
    ):
        label = re.sub(r"<[^>]+>", " ", match.group("label"))
        label = " ".join(unescape(label).split())
        if label and label != "进入主页":
            matches.append((label, int(match.group("cid"))))

    for label, cid in matches:
        if label == school_name:
            return cid
    normalized = school_name.replace("（", "(").replace("）", ")").replace(" ", "")
    for label, cid in matches:
        if label.replace("（", "(").replace("）", ")").replace(" ", "") == normalized:
            return cid
    return None


def plan_url(cid: int, year: int, subject: str) -> str:
    query = urlencode(
        {
            "cid": cid,
            "yearno": year,
            "ty": "普通类",
            "topchoose": subject,
            "pici": "本科批次",
        }
    )
    return f"{BASE_URL}/College/plannew.html?{query}"


def save_plan_page(
    *,
    school_name: str,
    cid: int,
    year: int,
    subject: str,
    force: bool,
    timeout: float,
) -> Path | None:
    url = plan_url(cid, year, subject)
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]
    out_dir = SOURCE_DIR / str(year)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{safe_name(school_name)}_jszs_{subject}_{cid}_{digest}.html"
    if out.exists() and not force:
        return None

    resp = requests.get(url, headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
    if "专业组" not in resp.text or "专业名称" not in resp.text:
        return None
    out.write_bytes(resp.content)
    out.with_suffix(out.suffix + ".url").write_text(url, encoding="utf-8")
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=int, nargs="+", default=[2025])
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--offset", type=int, default=0, help="跳过排序后的前 N 所学校")
    parser.add_argument("--school", action="append", default=[], help="只抓指定学校，可重复")
    parser.add_argument("--delay", type=float, default=0.6)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    cache = load_cache()
    saved = skipped = missing = failed = 0
    for year in args.years:
        schools = official_school_names(year)
        if args.school:
            wanted = set(args.school)
            schools = [name for name in schools if name in wanted]
        if args.offset:
            schools = schools[args.offset:]
        if args.limit:
            schools = schools[: args.limit]
        print(f"=== {year}: JSZS 下载 {len(schools)} 所 ===", flush=True)
        for index, school_name in enumerate(schools, start=1):
            try:
                cid = cache.get(school_name)
                if cid is None:
                    cid = search_cid(school_name, timeout=args.timeout)
                    if cid is not None:
                        cache[school_name] = cid
                        save_cache(cache)
                    time.sleep(args.delay)
                if cid is None:
                    missing += 1
                    print(f"[{index}/{len(schools)}] {school_name}: 未找到 cid")
                    continue

                school_saved = 0
                for subject in SUBJECTS:
                    path = save_plan_page(
                        school_name=school_name,
                        cid=cid,
                        year=year,
                        subject=subject,
                        force=args.force,
                        timeout=args.timeout,
                    )
                    if path is None:
                        skipped += 1
                    else:
                        saved += 1
                        school_saved += 1
                    time.sleep(args.delay)
                print(
                    f"[{index}/{len(schools)}] {school_name} cid={cid}: "
                    f"保存 {school_saved} 页",
                    flush=True,
                )
            except Exception as exc:  # noqa: BLE001
                failed += 1
                print(f"[{index}/{len(schools)}] {school_name}: 失败 {exc}", flush=True)
    print(f"完成：保存 {saved} 页，跳过 {skipped} 页，未匹配 {missing}，失败 {failed}", flush=True)


if __name__ == "__main__":
    main()
