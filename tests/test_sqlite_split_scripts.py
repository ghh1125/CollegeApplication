"""Tests for SQLite database setup and split data scripts."""

from __future__ import annotations

import importlib
import sqlite3
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class SQLiteConfigTests(unittest.TestCase):
    """SQLite and config behavior should not require .env."""

    def test_pyproject_removes_psycopg_and_keeps_scraping_deps(self) -> None:
        text = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")

        self.assertNotIn("psycopg", text)
        for dependency in ("requests", "beautifulsoup4", "lxml"):
            self.assertIn(f'"{dependency}"', text)

    def test_config_imports_without_database_url_or_dashscope_key(self) -> None:
        config_module = importlib.import_module("config")

        self.assertTrue(hasattr(config_module, "config"))

    def test_db_uses_project_sqlite_file(self) -> None:
        db = importlib.import_module("db")

        self.assertEqual(db.DB_PATH, PROJECT_ROOT / "data" / "college.db")
        with db.get_conn() as conn:
            self.assertIsInstance(conn, sqlite3.Connection)


class SQLiteSchemaTests(unittest.TestCase):
    """Schema should be executable by SQLite."""

    def test_schema_uses_sqlite_compatible_types(self) -> None:
        schema = (PROJECT_ROOT / "app" / "models" / "schema.sql").read_text(
            encoding="utf-8"
        )

        for postgres_fragment in ("BIGSERIAL", "BOOLEAN", "JSONB", "NOW()", "VARCHAR"):
            self.assertNotIn(postgres_fragment, schema)
        self.assertIn("TEXT DEFAULT (datetime('now'))", schema)
        self.assertIn("CHECK (recruit_type IN ('MAJOR', 'CATEGORY'))", schema)

    def test_schema_executes_on_sqlite(self) -> None:
        schema = (PROJECT_ROOT / "app" / "models" / "schema.sql").read_text(
            encoding="utf-8"
        )

        with sqlite3.connect(":memory:") as conn:
            conn.executescript(schema)
            count = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
            ).fetchone()[0]

        self.assertGreaterEqual(count, 7)


class SplitScriptTests(unittest.TestCase):
    """Download and load responsibilities should be separated."""

    def test_fetch_data_imports_without_config_or_database(self) -> None:
        module = importlib.import_module("scripts.fetch_data")

        self.assertFalse(hasattr(module, "run_ingestion"))
        self.assertTrue(hasattr(module, "download_missing_files"))

    def test_load_data_imports_without_requests(self) -> None:
        module = importlib.import_module("scripts.load_data")

        self.assertTrue(hasattr(module, "run_ingestion"))
        self.assertTrue(hasattr(module, "load_validation_summary"))

    def test_fetch_data_prints_file_row_counts(self) -> None:
        module = importlib.import_module("scripts.fetch_data")

        with tempfile.TemporaryDirectory() as tmpdir:
            raw_dir = Path(tmpdir)
            (raw_dir / "historical_cutoff_2025.csv").write_text(
                "school,score\nA,600\nB,590\n",
                encoding="utf-8",
            )

            summary = module.raw_file_row_counts(raw_dir)

        self.assertEqual(summary, {"historical_cutoff_2025.csv": 2})


if __name__ == "__main__":
    unittest.main()
