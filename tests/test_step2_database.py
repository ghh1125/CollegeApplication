"""Tests for database helpers, schema requirements, and init script."""

from __future__ import annotations

import importlib
import sqlite3
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = PROJECT_ROOT / "data" / "schema.sql"


class FakeCursor:
    """Small cursor double for context-manager tests."""

    def __init__(self) -> None:
        self.closed = False
        self.executed: list[str] = []
        self.result = (7,)

    def execute(self, query: str) -> None:
        self.executed.append(query)

    def fetchone(self) -> tuple[int]:
        return self.result

    def close(self) -> None:
        self.closed = True


class FakeConnection:
    """Small connection double for transaction behavior tests."""

    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False
        self.closed = False
        self.cursor_obj = FakeCursor()

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        self.closed = True

    def cursor(self) -> FakeCursor:
        return self.cursor_obj


class DatabaseHelperTests(unittest.TestCase):
    """Behavioral tests for app.db."""

    def test_get_conn_commits_on_success_and_closes(self) -> None:
        db = importlib.import_module("db")

        with tempfile.TemporaryDirectory() as tmpdir:
            original_path = db.DB_PATH
            db.DB_PATH = Path(tmpdir) / "college.db"
            try:
                with db.get_conn() as conn:
                    conn.execute("CREATE TABLE demo (value INTEGER)")
                    conn.execute("INSERT INTO demo VALUES (1)")
                with sqlite3.connect(db.DB_PATH) as conn:
                    value = conn.execute("SELECT value FROM demo").fetchone()[0]
            finally:
                db.DB_PATH = original_path

        self.assertEqual(value, 1)

    def test_get_conn_opens_existing_db_readonly(self) -> None:
        # Once a DB file exists, get_conn() must open it read-only so that
        # Streamlit Cloud's immutable filesystem doesn't cause write errors.
        db = importlib.import_module("db")

        with tempfile.TemporaryDirectory() as tmpdir:
            original_path = db.DB_PATH
            db.DB_PATH = Path(tmpdir) / "college.db"
            try:
                # First open: DB doesn't exist yet → read-write, create table
                with db.get_conn() as conn:
                    conn.execute("CREATE TABLE demo (value INTEGER)")
                # Second open: DB now exists → read-only, write must be rejected
                with self.assertRaises(Exception):
                    with db.get_conn() as conn:
                        conn.execute("INSERT INTO demo VALUES (1)")
            finally:
                db.DB_PATH = original_path

    def test_get_cursor_closes_cursor(self) -> None:
        conn = FakeConnection()
        db = importlib.import_module("db")

        with db.get_cursor(conn) as cursor:
            self.assertIs(cursor, conn.cursor_obj)
            self.assertFalse(cursor.closed)

        self.assertTrue(conn.cursor_obj.closed)


class InitDbScriptTests(unittest.TestCase):
    """Tests for scripts/init_db.py without a real database service."""

    def test_init_db_imports_without_connecting(self) -> None:
        module = importlib.import_module("scripts.init_db")

        self.assertTrue(hasattr(module, "main"))

    def test_ensure_create_table_if_not_exists_is_idempotent(self) -> None:
        module = importlib.import_module("scripts.init_db")

        plain_sql = "CREATE TABLE demo (id INT);"
        existing_sql = "CREATE TABLE IF NOT EXISTS demo (id INT);"

        self.assertEqual(
            module.ensure_create_table_if_not_exists(plain_sql),
            "CREATE TABLE IF NOT EXISTS demo (id INT);",
        )
        self.assertEqual(
            module.ensure_create_table_if_not_exists(existing_sql),
            existing_sql,
        )

    def test_table_row_counts_queries_each_table(self) -> None:
        module = importlib.import_module("scripts.init_db")
        conn = FakeConnection()

        counts = module.table_row_counts(conn, ("admission_plan",))

        self.assertEqual(counts, {"admission_plan": 7})
        self.assertEqual(
            conn.cursor_obj.executed,
            ["SELECT COUNT(*) FROM admission_plan"],
        )
        self.assertTrue(conn.cursor_obj.closed)

    def test_ensure_sqlite_schema_columns_adds_subject_requirement_columns(self) -> None:
        module = importlib.import_module("scripts.init_db")

        with sqlite3.connect(":memory:") as conn:
            conn.execute(
                """
                CREATE TABLE admission_plan (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    subject_requirement TEXT
                )
                """
            )

            module.ensure_sqlite_schema_columns(conn)
            columns = {
                row[1]
                for row in conn.execute("PRAGMA table_info(admission_plan)").fetchall()
            }

        self.assertIn("subject_requirement_text", columns)
        self.assertIn("subject_requirement_json", columns)

    def test_script_has_main_guard(self) -> None:
        script = PROJECT_ROOT / "scripts" / "init_db.py"

        self.assertIn(
            'if __name__ == "__main__"',
            script.read_text(encoding="utf-8"),
        )


class SchemaTests(unittest.TestCase):
    """Structural checks for the SQLite schema."""

    def test_schema_has_required_tables(self) -> None:
        schema = SCHEMA_PATH.read_text(encoding="utf-8")

        for table in (
            "admission_plan",
            "historical_cutoff",
            "program_mapping",
            "school_master",
            "major_master",
            "admission_rule",
            "major_admission_rule",
        ):
            self.assertIn(f"CREATE TABLE IF NOT EXISTS {table}", schema)

    def test_schema_has_new_step2_columns_and_constraints(self) -> None:
        schema = SCHEMA_PATH.read_text(encoding="utf-8")

        required_fragments = (
            "recruit_type TEXT DEFAULT 'MAJOR' CHECK (recruit_type IN ('MAJOR', 'CATEGORY'))",
            "subject_requirement_text TEXT",
            "subject_requirement_json TEXT",
            "mapping_direction TEXT DEFAULT 'BIDIRECTIONAL'",
            "valid_from_year INTEGER",
            "valid_to_year INTEGER",
            "physical_exam_required INTEGER",
            "physical_exam_detail TEXT",
            "foreign_language_required TEXT",
            "min_single_subject_scores TEXT",
            "is_5year INTEGER DEFAULT 0",
            "campus_location TEXT",
            "special_requirements TEXT",
            "source_url TEXT",
            "parsed_by_llm INTEGER DEFAULT 0",
            "human_verified INTEGER DEFAULT 0",
            "CONSTRAINT major_admission_rule_unique UNIQUE (year, school_name, major_name)",
        )

        for fragment in required_fragments:
            self.assertIn(fragment, schema)


if __name__ == "__main__":
    unittest.main()
