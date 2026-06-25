#!/usr/bin/env python3
"""Import 2026 Zhejiang undergraduate enrollment data into a dedicated table."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "data" / "zhejiang" / "college.db"
DEFAULT_JSONL = PROJECT_ROOT / "data" / "zhejiang" / "raw" / "enrollment_2026_zhejiang_undergrad.jsonl"
TARGET_TABLE = "admission_plan_2026"
SUBJECT_NAMES = ("物理", "化学", "生物", "思想政治", "历史", "地理", "技术")
SUBJECT_ABBREVIATIONS = {
    "物": "物理",
    "化": "化学",
    "生": "生物",
    "政": "思想政治",
    "史": "历史",
    "地": "地理",
    "技": "技术",
}


CREATE_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {TARGET_TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    year INTEGER NOT NULL DEFAULT 2026,
    province TEXT NOT NULL DEFAULT '浙江',
    batch TEXT NOT NULL,
    recruit_type TEXT DEFAULT 'MAJOR' CHECK (recruit_type IN ('MAJOR', 'CATEGORY')),
    school_code TEXT NOT NULL,
    school_name TEXT NOT NULL,
    major_code TEXT NOT NULL,
    major_name TEXT NOT NULL,
    plan_count INTEGER,
    subject_requirement TEXT,
    subject_requirement_text TEXT,
    subject_requirement_json TEXT,
    school_location TEXT,
    tuition INTEGER,
    duration TEXT,
    source_url TEXT,
    source_file TEXT,
    source_major TEXT,
    source_major_subtitle TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    subject_req_source TEXT,
    need_review INTEGER DEFAULT 0,
    CONSTRAINT admission_plan_2026_unique UNIQUE (
        year,
        province,
        batch,
        school_code,
        major_code
    )
);
"""

REQUIRED_COLUMNS = {
    "source_url": "TEXT",
    "source_file": "TEXT",
    "source_major": "TEXT",
    "source_major_subtitle": "TEXT",
    "subject_req_source": "TEXT",
    "need_review": "INTEGER DEFAULT 0",
}


def clean_text(value: Any) -> str:
    text = str(value or "").strip()
    return re.sub(r"\s+", " ", text.replace("\u3000", " "))


def parse_int(value: Any) -> int | None:
    text = clean_text(value)
    if not text or text in {"-", "--", "—"}:
        return None
    match = re.search(r"-?\d+", text.replace(",", ""))
    return int(match.group(0)) if match else None


def normalize_subject_text(text: str | None) -> str:
    value = clean_text(text)
    value = value.replace("生命科学", "生物")
    value = re.sub(r"(?<!思想)政治", "思想政治", value)
    for short, full in SUBJECT_ABBREVIATIONS.items():
        value = re.sub(rf"(?<![\u4e00-\u9fa5]){short}(?![\u4e00-\u9fa5])", full, value)
    return value


def subject_requirement_json_from_text(text: str | None) -> str:
    requirement = normalize_subject_text(text)
    if not requirement or "不限" in requirement or "不提科目要求" in requirement:
        return json.dumps({"type": "NONE", "subjects": []}, ensure_ascii=False)

    subjects = [subject for subject in SUBJECT_NAMES if subject in requirement]
    if not subjects:
        return json.dumps({"type": "UNKNOWN", "subjects": []}, ensure_ascii=False)

    if any(token in requirement for token in ("任选", "任意", "选考其中", "其中一门")):
        requirement_type = "ANY_ONE"
    elif "或" in requirement and "必选" not in requirement:
        requirement_type = "ANY_ONE"
    else:
        requirement_type = "ALL_REQUIRED"

    return json.dumps(
        {"type": requirement_type, "subjects": subjects},
        ensure_ascii=False,
    )


def normalize_major_name(value: Any) -> str:
    text = clean_text(value)
    text = text.replace("（", "(").replace("）", ")")
    return re.sub(r"\s+", "", text)


def duration_from_raw(value: Any) -> str | None:
    text = clean_text(value)
    if not text:
        return None
    if text.endswith("年"):
        return text
    number = parse_int(text)
    return f"{number}年" if number else text


def recruit_type_from_major_name(major_name: str, raw_major_type: Any = None) -> str:
    raw_type = clean_text(raw_major_type)
    if raw_type and "类" in raw_type:
        return "CATEGORY"
    if "类" in major_name or "试验班" in major_name:
        return "CATEGORY"
    return "MAJOR"


def stable_major_code(row: dict[str, Any], school_code: str) -> str:
    source = "|".join(
        [
            "2026",
            school_code,
            clean_text(row.get("major_full_name")),
            clean_text(row.get("major_group")),
            clean_text(row.get("source_url")),
        ]
    )
    digest = hashlib.sha1(source.encode("utf-8")).hexdigest()[:10]
    return f"ENR2026-{digest}"


def source_file_label(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return path.name


def ensure_admission_plan_2026_schema(conn: sqlite3.Connection) -> None:
    conn.execute(CREATE_TABLE_SQL)
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({TARGET_TABLE})")}
    for column, definition in REQUIRED_COLUMNS.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE {TARGET_TABLE} ADD COLUMN {column} {definition}")


def load_school_codes(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute(
        """
        SELECT school_name, school_code, COUNT(*) AS n
        FROM admission_plan
        WHERE province = '浙江'
        GROUP BY school_name, school_code
        ORDER BY school_name, n DESC
        """
    ).fetchall()
    result: dict[str, str] = {}
    for school_name, school_code, _count in rows:
        result.setdefault(str(school_name), str(school_code))
    return result


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


INSERT_SQL = f"""
INSERT INTO {TARGET_TABLE} (
    year,
    province,
    batch,
    recruit_type,
    school_code,
    school_name,
    major_code,
    major_name,
    plan_count,
    subject_requirement,
    subject_requirement_text,
    subject_requirement_json,
    school_location,
    tuition,
    duration,
    source_url,
    source_file,
    source_major,
    source_major_subtitle,
    subject_req_source,
    need_review
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (year, province, batch, school_code, major_code)
DO UPDATE SET
    school_name = EXCLUDED.school_name,
    major_name = EXCLUDED.major_name,
    plan_count = EXCLUDED.plan_count,
    subject_requirement = EXCLUDED.subject_requirement,
    subject_requirement_text = EXCLUDED.subject_requirement_text,
    subject_requirement_json = EXCLUDED.subject_requirement_json,
    tuition = EXCLUDED.tuition,
    duration = EXCLUDED.duration,
    source_url = EXCLUDED.source_url,
    source_file = EXCLUDED.source_file,
    source_major = EXCLUDED.source_major,
    source_major_subtitle = EXCLUDED.source_major_subtitle,
    subject_req_source = EXCLUDED.subject_req_source,
    need_review = EXCLUDED.need_review
"""


def row_to_values(row: dict[str, Any], school_codes: dict[str, str], source_file: Path) -> tuple[Any, ...] | None:
    year = parse_int(row.get("row_year")) or parse_int(row.get("query_year")) or 2026
    if year != 2026:
        return None

    school_name = clean_text(row.get("school_name"))
    major_name = normalize_major_name(row.get("major_full_name"))
    if not major_name:
        major_name = normalize_major_name(f"{row.get('major') or ''}{row.get('major_subtitle') or ''}")
    if not school_name or not major_name:
        return None

    school_code = school_codes.get(school_name)
    if not school_code:
        digest = hashlib.sha1(school_name.encode("utf-8")).hexdigest()[:6]
        school_code = f"UNKNOWN-{digest}"

    requirement_text = normalize_subject_text(row.get("elective_info"))
    return (
        2026,
        clean_text(row.get("province")) or "浙江",
        clean_text(row.get("query_batch")) or "本科批",
        recruit_type_from_major_name(major_name, row.get("major_type")),
        school_code,
        school_name,
        stable_major_code(row, school_code),
        major_name,
        parse_int(row.get("enroll_num")),
        requirement_text,
        requirement_text,
        subject_requirement_json_from_text(requirement_text),
        None,
        parse_int(row.get("tuition")),
        duration_from_raw(row.get("major_length")),
        clean_text(row.get("source_url")),
        source_file_label(source_file),
        clean_text(row.get("major")),
        clean_text(row.get("major_subtitle")),
        "ENROLLMENT_2026",
        0 if requirement_text else 1,
    )


def import_enrollment_2026(jsonl_path: Path = DEFAULT_JSONL, db_path: Path = DB_PATH) -> dict[str, int]:
    rows = read_jsonl(jsonl_path)
    conn = sqlite3.connect(db_path)
    try:
        ensure_admission_plan_2026_schema(conn)
        school_codes = load_school_codes(conn)
        inserted = 0
        skipped = 0
        with conn:
            for row in rows:
                values = row_to_values(row, school_codes, jsonl_path)
                if values is None:
                    skipped += 1
                    continue
                conn.execute(INSERT_SQL, values)
                inserted += 1
        total = conn.execute(f"SELECT COUNT(*) FROM {TARGET_TABLE}").fetchone()[0]
    finally:
        conn.close()
    return {"inserted": inserted, "skipped": skipped, "total": int(total)}


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--jsonl", type=Path, default=DEFAULT_JSONL)
    parser.add_argument("--db", type=Path, default=DB_PATH)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    summary = import_enrollment_2026(jsonl_path=args.jsonl, db_path=args.db)
    print(
        "admission_plan_2026: "
        f"写入 {summary['inserted']} 条，跳过 {summary['skipped']} 条，表内共 {summary['total']} 条"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
