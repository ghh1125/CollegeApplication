"""Raw admission data ingestion stage."""

from __future__ import annotations

import csv
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any


ADMISSION_PROVINCE = "浙江"
DEFAULT_BATCH = "普通类"

COLUMN_ALIASES = {
    "school_code": ("学校代码", "院校代码", "学校代号", "院校代号", "院校编号"),
    "school_name": ("学校名称", "院校名称", "学校", "院校"),
    "major_code": (
        "专业代码",
        "专业代号",
        "专业(类)代码",
        "专业（类）代码",
        "专业编号",
    ),
    "major_name": (
        "专业名称",
        "专业(类)名称",
        "专业（类）名称",
        "专业",
        "专业类名称",
    ),
    "plan_count": ("计划数", "招生计划数", "招生人数", "计划人数"),
    "tuition": ("学费", "收费标准", "学费标准"),
    "subject_requirement": ("选科要求", "选考科目要求", "选考科目范围", "科目要求"),
    "school_location": ("省份城市", "院校所在地", "学校所在地", "所在地", "办学地点"),
    "min_score": ("最低分", "分数线", "投档分", "最低投档分"),
    "min_rank": ("最低位次", "位次", "投档位次", "最低投档位次"),
}


def read_csv_records(path: str | Path) -> list[dict[str, str]]:
    """Read CSV rows as dictionaries while preserving code-like strings."""

    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def pick(row: dict[str, Any], field: str, default: str = "") -> str:
    """Return the first matching field value from a source row."""

    for key in COLUMN_ALIASES[field]:
        value = row.get(key)
        if value is not None and str(value).strip():
            return clean_text(value)
    return default


def clean_text(value: Any) -> str:
    """Normalize scraped cell text."""

    text = str(value).strip()
    return re.sub(r"\s+", " ", text.replace("\u3000", " "))


def parse_int(value: Any) -> int | None:
    """Parse integer-like scraped values such as ranks and plan counts."""

    text = clean_text(value)
    if not text or text in {"-", "--", "—"}:
        return None
    match = re.search(r"-?\d+", text.replace(",", ""))
    return int(match.group(0)) if match else None


def normalize_admission_plan_row(row: dict[str, Any], year: int) -> tuple:
    """Map a scraped admission-plan row to the database insert tuple."""

    return (
        year,
        ADMISSION_PROVINCE,
        DEFAULT_BATCH,
        "MAJOR",
        pick(row, "school_code"),
        pick(row, "school_name"),
        pick(row, "major_code"),
        pick(row, "major_name"),
        parse_int(pick(row, "plan_count")),
        pick(row, "subject_requirement"),
        pick(row, "school_location"),
        parse_int(pick(row, "tuition")),
    )


def normalize_historical_cutoff_row(row: dict[str, Any], year: int) -> tuple:
    """Map a scraped cutoff row to the database insert tuple."""

    return (
        year,
        ADMISSION_PROVINCE,
        DEFAULT_BATCH,
        pick(row, "school_code"),
        pick(row, "school_name"),
        pick(row, "major_code"),
        pick(row, "major_name"),
        parse_int(pick(row, "min_score")),
        parse_int(pick(row, "min_rank")),
        parse_int(pick(row, "plan_count")),
    )


@contextmanager
def connection_scope(conn: Any | None = None) -> Iterator[Any]:
    """Use an existing connection in tests or open a managed DB connection."""

    if conn is not None:
        yield conn
        return

    from app.db import get_conn

    with get_conn() as managed_conn:
        yield managed_conn


def ingest_admission_plan(path: str | Path, year: int, conn: Any | None = None) -> int:
    """Insert admission-plan CSV rows into SQLite."""

    from app.db import get_cursor

    rows = read_csv_records(path)
    sql = """
        INSERT INTO admission_plan (
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
            school_location,
            tuition
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (year, province, batch, school_code, major_code)
        DO UPDATE SET
            recruit_type = EXCLUDED.recruit_type,
            school_name = EXCLUDED.school_name,
            major_name = EXCLUDED.major_name,
            plan_count = EXCLUDED.plan_count,
            subject_requirement = EXCLUDED.subject_requirement,
            school_location = EXCLUDED.school_location,
            tuition = EXCLUDED.tuition
    """

    with connection_scope(conn) as active_conn:
        with get_cursor(active_conn) as cursor:
            for row in rows:
                cursor.execute(sql, normalize_admission_plan_row(row, year))
    return len(rows)


def ingest_historical_cutoff(
    path: str | Path,
    year: int,
    conn: Any | None = None,
) -> int:
    """Insert historical cutoff CSV rows into SQLite."""

    from app.db import get_cursor

    rows = read_csv_records(path)
    sql = """
        INSERT INTO historical_cutoff (
            year,
            province,
            batch,
            school_code,
            school_name,
            major_code,
            major_name,
            min_score,
            min_rank,
            plan_count
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (year, province, batch, school_code, major_code)
        DO UPDATE SET
            school_name = EXCLUDED.school_name,
            major_name = EXCLUDED.major_name,
            min_score = EXCLUDED.min_score,
            min_rank = EXCLUDED.min_rank,
            plan_count = EXCLUDED.plan_count
    """

    with connection_scope(conn) as active_conn:
        with get_cursor(active_conn) as cursor:
            for row in rows:
                cursor.execute(sql, normalize_historical_cutoff_row(row, year))
    return len(rows)


def build_program_mapping(year: int, conn: Any | None = None) -> None:
    """Build exact program mappings from the selected year's admission plan."""

    from app.db import get_cursor

    sql = """
        INSERT INTO program_mapping (
            source_school_code,
            source_major_code,
            school_code,
            major_code,
            normalized_program_name,
            mapping_direction,
            valid_from_year,
            need_human_review
        )
        SELECT DISTINCT
            school_code,
            major_code,
            school_code,
            major_code,
            major_name,
            'BIDIRECTIONAL',
            ?,
            0
        FROM admission_plan
        WHERE year = ?
        ON CONFLICT (
            source_school_code,
            source_major_code,
            school_code,
            major_code
        )
        DO UPDATE SET
            normalized_program_name = EXCLUDED.normalized_program_name,
            valid_from_year = EXCLUDED.valid_from_year,
            need_human_review = EXCLUDED.need_human_review
    """

    with connection_scope(conn) as active_conn:
        with get_cursor(active_conn) as cursor:
            cursor.execute(sql, (year, year))
