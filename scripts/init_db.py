"""Initialize SQLite tables from the project schema file."""

from __future__ import annotations

import re
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

SCHEMA_PATH = PROJECT_ROOT / "data" / "zhejiang" / "schema.sql"
TABLE_NAMES = (
    "admission_plan",
    "historical_cutoff",
    "program_mapping",
    "school_master",
    "major_master",
    "admission_rule",
    "major_admission_rule",
    "major_subject_requirement",
    "school_profile",
    "school_info_section",
    "major_profile",
    "city_profile",
)
SQLITE_REQUIRED_COLUMNS = {
    "admission_plan": {
        "subject_requirement_text": "TEXT",
        "subject_requirement_json": "TEXT",
        "subject_req_source": "TEXT",
        "need_review": "INTEGER DEFAULT 0",
    },
}


def ensure_create_table_if_not_exists(sql: str) -> str:
    """Make bare CREATE TABLE statements idempotent."""

    return re.sub(
        r"\bCREATE\s+TABLE\s+(?!IF\s+NOT\s+EXISTS\b)",
        "CREATE TABLE IF NOT EXISTS ",
        sql,
        flags=re.IGNORECASE,
    )


def load_schema_sql(path: Path = SCHEMA_PATH) -> str:
    """Read the schema SQL and ensure CREATE TABLE statements are idempotent."""

    return ensure_create_table_if_not_exists(path.read_text(encoding="utf-8"))


def execute_schema(conn: Any, schema_sql: str) -> None:
    """Execute schema SQL on an existing connection."""

    from db import get_cursor

    if hasattr(conn, "executescript"):
        conn.executescript(schema_sql)
        return

    with get_cursor(conn) as cursor:
        cursor.execute(schema_sql)


def table_row_counts(
    conn: Any,
    tables: Iterable[str] = TABLE_NAMES,
) -> dict[str, int]:
    """Return row counts for known schema tables."""

    from db import get_cursor

    counts: dict[str, int] = {}
    for table in tables:
        with get_cursor(conn) as cursor:
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            row = cursor.fetchone()
        counts[table] = int(row[0]) if row else 0
    return counts


def ensure_sqlite_schema_columns(conn: Any) -> None:
    """Add columns needed by newer SQLite schemas when tables already exist."""

    for table, required_columns in SQLITE_REQUIRED_COLUMNS.items():
        existing_columns = {
            row[1]
            for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        for column, definition in required_columns.items():
            if column not in existing_columns:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def initialize_database(schema_path: Path = SCHEMA_PATH) -> dict[str, int]:
    """Create tables and return row counts for confirmation output."""

    from db import get_conn

    with get_conn() as conn:
        execute_schema(conn, load_schema_sql(schema_path))
        ensure_sqlite_schema_columns(conn)
        return table_row_counts(conn)


def main() -> None:
    """Create database tables and print table row counts."""

    counts = initialize_database()
    for table, row_count in counts.items():
        print(f"{table}: {row_count} rows")


if __name__ == "__main__":
    main()
