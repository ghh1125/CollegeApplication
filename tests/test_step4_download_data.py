"""Tests for Step 4 data download and ingestion orchestration."""

from __future__ import annotations

import csv
import importlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class FakeCursor:
    """Cursor double that records SQL calls."""

    def __init__(self, rows: list[tuple] | None = None) -> None:
        self.calls: list[tuple[str, tuple | None]] = []
        self.rows = rows or []
        self.closed = False

    def execute(self, query: str, params: tuple | None = None) -> None:
        self.calls.append((query, params))

    def fetchall(self) -> list[tuple]:
        return self.rows

    def fetchone(self) -> tuple | None:
        return self.rows[0] if self.rows else None

    def close(self) -> None:
        self.closed = True


class FakeConnection:
    """Connection double that returns a sequence of cursor doubles."""

    def __init__(self, cursors: list[FakeCursor] | None = None) -> None:
        self.cursors = cursors or [FakeCursor()]

    def cursor(self) -> FakeCursor:
        if len(self.cursors) == 1:
            return self.cursors[0]
        return self.cursors.pop(0)


class PyprojectDependencyTests(unittest.TestCase):
    """Ensure new scraping dependencies are declared."""

    def test_pyproject_declares_scraping_dependencies(self) -> None:
        text = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")

        for dependency in ("requests", "beautifulsoup4", "lxml"):
            self.assertIn(f'"{dependency}"', text)


class FetchScriptTests(unittest.TestCase):
    """Pure-function tests for scripts/fetch_data.py."""

    def setUp(self) -> None:
        self.module = importlib.import_module("scripts.fetch_data")

    def test_imports_without_network_or_database(self) -> None:
        self.assertTrue(hasattr(self.module, "main"))

    def test_html_table_records_are_extracted(self) -> None:
        html = """
        <table>
          <tr><th>学校代码</th><th>学校名称</th><th>专业代码</th><th>专业名称</th><th>最低分</th></tr>
          <tr><td>0001</td><td>浙江大学</td><td>001</td><td>工科试验班</td><td>680</td></tr>
        </table>
        """

        records = self.module.extract_table_records(html)

        self.assertEqual(
            records,
            [
                {
                    "学校代码": "0001",
                    "学校名称": "浙江大学",
                    "专业代码": "001",
                    "专业名称": "工科试验班",
                    "最低分": "680",
                }
            ],
        )

    def test_write_records_csv_creates_parent_and_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "nested" / "historical_cutoff_2025.csv"

            self.module.write_records_csv(
                [{"学校代码": "0001", "学校名称": "浙江大学"}],
                path,
            )

            self.assertTrue(path.exists())
            with path.open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows[0]["学校名称"], "浙江大学")

    def test_choose_admission_plan_file_prefers_2026_then_2025(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            raw_dir = Path(tmpdir)
            (raw_dir / "admission_plan_2025.csv").write_text("x\n")
            (raw_dir / "admission_plan_2026.csv").write_text("x\n")

            path, year = self.module.choose_admission_plan_file(raw_dir)

        self.assertEqual(year, 2026)
        self.assertEqual(path.name, "admission_plan_2026.csv")

    def test_filter_records_removes_notes_and_duplicates(self) -> None:
        records = [
            {
                "学校代号": "0001",
                "学校名称": "浙江大学",
                "专业代号": "001",
                "专业名称": "工科试验班",
                "计划数": "10",
                "分数线": "680",
                "位次": "120",
            },
            {
                "学校代号": "0001",
                "学校名称": "浙江大学",
                "专业代号": "001",
                "专业名称": "工科试验班",
                "计划数": "10",
                "分数线": "680",
                "位次": "120",
            },
            {
                "学校代号": "注：位次栏目为空的，表示该学校专业本轮投档人数未满。",
                "学校名称": "浙江大学",
                "专业代号": "",
                "专业名称": "",
                "计划数": "",
                "分数线": "",
                "位次": "",
            },
        ]

        filtered = self.module.filter_records_for_dataset(
            records,
            "historical_cutoff",
        )

        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["学校名称"], "浙江大学")

    def test_subject_requirement_candidate_links_include_excel_pdf_and_rar(self) -> None:
        html = """
        <a href="/files/subject.xlsx">2024年普通高校招生专业选考科目要求</a>
        <a href="/files/subject.pdf">选考科目要求 PDF</a>
        <a href="/files/subject.rar">2024年普通高校招生专业选考科目要求.rar</a>
        <a href="/files/other.xlsx">普通类投档线</a>
        """

        links = self.module.subject_requirement_candidate_links(
            html,
            "https://www.zjzs.net/col/col45/index.html",
        )

        self.assertEqual(
            [Path(url).suffix for _title, url in links],
            [".xlsx", ".pdf", ".rar"],
        )


class LoadScriptTests(unittest.TestCase):
    """Pure-function tests for scripts/load_data.py."""

    def setUp(self) -> None:
        self.module = importlib.import_module("scripts.load_data")

    def test_validation_summary_formats_expected_output(self) -> None:
        cursors = [
            FakeCursor([(2025, 3)]),
            FakeCursor([(2023, 1), (2024, 2), (2025, 3)]),
            FakeCursor([(2, 1)]),
        ]
        conn = FakeConnection(cursors)

        summary = self.module.load_validation_summary(conn)

        self.assertIn("admission_plan:    2025年 3 条", summary)
        self.assertIn(
            "historical_cutoff: 2023年 1 条 / 2024年 2 条 / 2025年 3 条",
            summary,
        )
        self.assertIn("subject_req matched: 2 条 / 未匹配: 1 条", summary)

    def test_subject_requirement_json_from_text_parses_default_and_required_subjects(self) -> None:
        none_json = json.loads(self.module.subject_requirement_json_from_text(""))
        required_json = json.loads(
            self.module.subject_requirement_json_from_text(
                "物理,化学(2门科目考生均须选考方可报考)"
            )
        )

        self.assertEqual(none_json, {"type": "NONE", "subjects": []})
        self.assertEqual(required_json["type"], "ALL")
        self.assertEqual(required_json["subjects"], ["物理", "化学"])

    def write_csv(self, path: Path, rows: list[dict[str, str]]) -> None:
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)


class SchemaStep4Tests(unittest.TestCase):
    """Schema checks for Step 4 ingestion and validation queries."""

    def test_schema_has_columns_required_by_ingest_and_validation(self) -> None:
        schema = (PROJECT_ROOT / "data" / "zhejiang" / "schema.sql").read_text(
            encoding="utf-8"
        )

        for fragment in (
            "school_name TEXT NOT NULL",
            "major_name TEXT NOT NULL",
            "plan_count INTEGER",
            "school_location TEXT",
            "need_human_review INTEGER DEFAULT 0",
        ):
            self.assertIn(fragment, schema)


if __name__ == "__main__":
    unittest.main()
