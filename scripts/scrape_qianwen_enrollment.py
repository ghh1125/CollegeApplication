#!/usr/bin/env python3
"""Scrape Qianwen/Quark enrollment-plan rows into standalone files.

This script intentionally does not write SQLite tables. It reads school names
from the local Zhejiang admission plan DB, fetches Qianwen's SSR HTML page, and
extracts the embedded `enrollmentList` JSON.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "data" / "zhejiang" / "college.db"
RAW_DIR = PROJECT_ROOT / "data" / "zhejiang" / "raw"
DEFAULT_JSONL = RAW_DIR / "qianwen_enrollment_2025_zhejiang_undergrad.jsonl"
DEFAULT_CSV = RAW_DIR / "qianwen_enrollment_2025_zhejiang_undergrad.csv"
DEFAULT_STATUS = RAW_DIR / "qianwen_enrollment_2025_zhejiang_undergrad.status.jsonl"

BASE_URL = "https://p.qianwen.com/university/tab"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/136.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://p.qianwen.com/",
}


class ScrapeError(RuntimeError):
    pass


def _decode_escaped_json_field(html: str, field: str) -> dict[str, Any] | None:
    match = re.search(rf'"{re.escape(field)}":"((?:\\.|[^"\\])*)"', html)
    if not match:
        return None
    try:
        decoded = json.loads(f'"{match.group(1)}"')
        value = json.loads(decoded)
    except json.JSONDecodeError as exc:
        raise ScrapeError(f"failed to decode {field}: {exc}") from exc
    if isinstance(value, dict):
        return value
    raise ScrapeError(f"{field} decoded to {type(value).__name__}, expected object")


def parse_enrollment_page(html: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    enrollment_list = _decode_escaped_json_field(html, "enrollmentList")
    if not enrollment_list:
        return [], {}

    source_data = enrollment_list.get("data") or {}
    rows = source_data.get("dataSource") or []
    if not isinstance(rows, list):
        raise ScrapeError("enrollmentList.data.dataSource is not a list")

    enrollment_meta = _decode_escaped_json_field(html, "enrollment") or {}
    return [row for row in rows if isinstance(row, dict)], enrollment_meta


def qianwen_url(school_name: str, province: str, year: int, batch: str, genre: str) -> str:
    params_payload = {
        "province": province,
        "year": str(year),
        "batch": batch,
        "genre": genre,
    }
    query = {
        "app": "ZhaoShengJiHua",
        "university_name": school_name,
        "params": json.dumps(params_payload, ensure_ascii=False, separators=(",", ":")),
        "type": "luqu",
        "device": "pc",
        "bar": "pure",
    }
    return f"{BASE_URL}?{urlencode(query)}"


def load_school_names(db_path: Path, year: int, province: str) -> list[str]:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT DISTINCT school_name
            FROM admission_plan
            WHERE year = ? AND province = ?
            ORDER BY school_name
            """,
            (year, province),
        ).fetchall()
    finally:
        conn.close()
    return [str(row[0]) for row in rows if row and row[0]]


def existing_done_schools(jsonl_path: Path) -> set[str]:
    done: set[str] = set()
    if not jsonl_path.exists():
        return done
    with jsonl_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            school_name = record.get("school_name")
            if school_name:
                done.add(str(school_name))
    return done


def fetch_school(
    session: requests.Session,
    school_name: str,
    province: str,
    year: int,
    batch: str,
    genre: str,
    timeout: float,
    retries: int,
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    url = qianwen_url(school_name, province, year, batch, genre)
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            resp = session.get(url, headers=HEADERS, timeout=timeout)
            resp.raise_for_status()
            rows, meta = parse_enrollment_page(resp.text)
            return url, rows, meta
        except Exception as exc:  # noqa: BLE001 - keep crawler resilient
            last_error = exc
            if attempt < retries:
                time.sleep(min(8.0, 1.5 * attempt) + random.random())
    raise ScrapeError(str(last_error) if last_error else "unknown fetch error")


def normalize_row(
    raw: dict[str, Any],
    *,
    school_name: str,
    province: str,
    year: int,
    batch: str,
    source_url: str,
) -> dict[str, Any]:
    return {
        "source": "qianwen",
        "source_url": source_url,
        "school_name": school_name,
        "province": province,
        "query_year": year,
        "query_batch": batch,
        "row_year": raw.get("year"),
        "major": raw.get("major"),
        "major_subtitle": raw.get("major_subtitle"),
        "major_full_name": f"{raw.get('major') or ''}{raw.get('major_subtitle') or ''}",
        "enroll_num": raw.get("enroll_num"),
        "major_length": raw.get("major_length"),
        "tuition": raw.get("tuition"),
        "elective_info": raw.get("elective_info"),
        "major_group": raw.get("major_group"),
        "major_type": raw.get("major_type"),
        "has_gx_zy": raw.get("has_gx_zy"),
        "is_new": raw.get("is_new"),
        "raw": raw,
    }


CSV_FIELDS = [
    "source",
    "school_name",
    "province",
    "query_year",
    "query_batch",
    "row_year",
    "major",
    "major_subtitle",
    "major_full_name",
    "enroll_num",
    "major_length",
    "tuition",
    "elective_info",
    "major_group",
    "major_type",
    "has_gx_zy",
    "is_new",
    "source_url",
]


def append_records(jsonl_path: Path, csv_path: Path, records: list[dict[str, Any]]) -> None:
    if not records:
        return
    with jsonl_path.open("a", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")

    csv_exists = csv_path.exists()
    with csv_path.open("a", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        if not csv_exists:
            writer.writeheader()
        for record in records:
            writer.writerow({field: record.get(field) for field in CSV_FIELDS})


def append_status(status_path: Path, status: dict[str, Any]) -> None:
    with status_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(status, ensure_ascii=False, separators=(",", ":")) + "\n")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--province", default="浙江")
    parser.add_argument("--year", type=int, default=2025)
    parser.add_argument("--batch", default="本科批")
    parser.add_argument("--genre", default="综合")
    parser.add_argument("--output-jsonl", type=Path, default=DEFAULT_JSONL)
    parser.add_argument("--output-csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--status-jsonl", type=Path, default=DEFAULT_STATUS)
    parser.add_argument("--school", action="append", help="Only scrape this school; can repeat.")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--sleep", type=float, default=0.35)
    parser.add_argument("--timeout", type=float, default=25.0)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--allow-year-mismatch",
        action="store_true",
        help="Keep rows even when embedded row year differs from --year.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    if args.school:
        school_names = args.school
    else:
        school_names = load_school_names(args.db, args.year, args.province)

    if args.offset:
        school_names = school_names[args.offset :]
    if args.limit:
        school_names = school_names[: args.limit]

    for path in (args.output_jsonl, args.output_csv, args.status_jsonl):
        path.parent.mkdir(parents=True, exist_ok=True)

    if args.overwrite:
        for path in (args.output_jsonl, args.output_csv, args.status_jsonl):
            path.unlink(missing_ok=True)

    done = existing_done_schools(args.output_jsonl) if args.resume else set()
    session = requests.Session()

    total_rows = 0
    ok_schools = 0
    empty_schools = 0
    error_schools = 0
    started = time.time()

    print(
        f"Scraping {len(school_names)} schools: {args.province} {args.year} {args.batch}; "
        f"resume_done={len(done)}",
        flush=True,
    )

    for index, school_name in enumerate(school_names, start=1):
        if school_name in done:
            continue
        try:
            url, raw_rows, meta = fetch_school(
                session,
                school_name,
                args.province,
                args.year,
                args.batch,
                args.genre,
                args.timeout,
                args.retries,
            )
            year_mismatch_rows = [
                raw for raw in raw_rows if str(raw.get("year") or "") != str(args.year)
            ]
            if not args.allow_year_mismatch:
                raw_rows = [
                    raw for raw in raw_rows if str(raw.get("year") or "") == str(args.year)
                ]
            records = [
                normalize_row(
                    raw,
                    school_name=school_name,
                    province=args.province,
                    year=args.year,
                    batch=args.batch,
                    source_url=url,
                )
                for raw in raw_rows
            ]
            append_records(args.output_jsonl, args.output_csv, records)
            append_status(
                args.status_jsonl,
                {
                    "school_name": school_name,
                    "status": "ok" if records else "empty",
                    "row_count": len(records),
                    "raw_row_count": len(raw_rows) + len(year_mismatch_rows)
                    if not args.allow_year_mismatch
                    else len(raw_rows),
                    "year_mismatch_count": len(year_mismatch_rows),
                    "source_url": url,
                    "enroll_number": (meta.get("enroll_number") or {}).get(str(args.year))
                    if isinstance(meta.get("enroll_number"), dict)
                    else None,
                    "batches": (meta.get("prov_year_map_batch") or {}).get(
                        f"{args.province}_{args.year}"
                    )
                    if isinstance(meta.get("prov_year_map_batch"), dict)
                    else None,
                },
            )
            if records:
                ok_schools += 1
                total_rows += len(records)
            else:
                empty_schools += 1
        except Exception as exc:  # noqa: BLE001 - keep whole crawl moving
            error_schools += 1
            append_status(
                args.status_jsonl,
                {"school_name": school_name, "status": "error", "error": str(exc)},
            )

        if index % 25 == 0 or index == len(school_names):
            elapsed = time.time() - started
            print(
                f"[{index}/{len(school_names)}] ok={ok_schools} empty={empty_schools} "
                f"errors={error_schools} rows={total_rows} elapsed={elapsed:.1f}s",
                flush=True,
            )
        time.sleep(args.sleep + random.random() * min(args.sleep, 0.5))

    print(
        f"Done. ok={ok_schools} empty={empty_schools} errors={error_schools} rows={total_rows}",
        flush=True,
    )
    print(f"JSONL: {args.output_jsonl}", flush=True)
    print(f"CSV:   {args.output_csv}", flush=True)
    print(f"Status:{args.status_jsonl}", flush=True)
    return 0 if error_schools == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
