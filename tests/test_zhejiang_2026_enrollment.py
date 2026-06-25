from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path


class Zhejiang2026EnrollmentTests(unittest.TestCase):
    def test_scrape_defaults_use_neutral_2026_file_names_and_2025_school_source(self):
        from scripts import scrape_zhejiang_enrollment as scrape

        args = scrape.build_arg_parser().parse_args(["--year", "2026"])
        scrape.resolve_default_paths(args)

        self.assertEqual(args.output_jsonl.name, "enrollment_2026_zhejiang_undergrad.jsonl")
        self.assertEqual(args.output_csv.name, "enrollment_2026_zhejiang_undergrad.csv")
        self.assertEqual(args.status_jsonl.name, "enrollment_2026_zhejiang_undergrad.status.jsonl")
        self.assertNotIn("qianwen", str(args.output_jsonl).lower())
        self.assertEqual(args.school_source_year, 2025)

    def test_load_school_names_falls_back_to_2025_for_2026_crawl(self):
        from scripts import scrape_zhejiang_enrollment as scrape

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "college.db"
            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE admission_plan (
                    year INTEGER,
                    province TEXT,
                    school_name TEXT
                )
                """
            )
            conn.executemany(
                "INSERT INTO admission_plan VALUES (?, ?, ?)",
                [(2025, "浙江", "北京大学"), (2025, "浙江", "浙江大学")],
            )
            conn.commit()
            conn.close()

            names = scrape.load_school_names(db_path, year=2026, province="浙江")

        self.assertEqual(names, ["北京大学", "浙江大学"])

    def test_import_2026_writes_dedicated_table_without_touching_2025_plan(self):
        from scripts.import_zhejiang_enrollment_2026 import import_enrollment_2026

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = root / "college.db"
            jsonl_path = root / "enrollment_2026_zhejiang_undergrad.jsonl"
            jsonl_path.write_text(
                json.dumps(
                    {
                        "school_name": "浙江大学",
                        "province": "浙江",
                        "query_year": 2026,
                        "query_batch": "本科批",
                        "row_year": 2026,
                        "major": "工科试验班",
                        "major_subtitle": "(信息)",
                        "major_full_name": "工科试验班(信息)",
                        "enroll_num": "10",
                        "major_length": "4",
                        "tuition": "6000",
                        "elective_info": "物理、化学(2科必选)",
                        "source_url": "https://example.test/enroll",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE admission_plan (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    year INTEGER NOT NULL,
                    province TEXT NOT NULL,
                    batch TEXT NOT NULL,
                    school_code TEXT NOT NULL,
                    school_name TEXT NOT NULL,
                    major_code TEXT NOT NULL,
                    major_name TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                INSERT INTO admission_plan
                    (year, province, batch, school_code, school_name, major_code, major_name)
                VALUES (2025, '浙江', '普通类', '0001', '浙江大学', '001', '工科试验班(信息)')
                """
            )
            conn.commit()
            conn.close()

            summary = import_enrollment_2026(jsonl_path=jsonl_path, db_path=db_path)

            conn = sqlite3.connect(db_path)
            old_count = conn.execute("SELECT COUNT(*) FROM admission_plan").fetchone()[0]
            new_rows = conn.execute(
                """
                SELECT year, school_code, school_name, major_name, plan_count, tuition,
                       duration, subject_requirement_json, source_file
                FROM admission_plan_2026
                """
            ).fetchall()
            conn.close()

        self.assertEqual(summary["inserted"], 1)
        self.assertEqual(old_count, 1)
        self.assertEqual(len(new_rows), 1)
        row = new_rows[0]
        self.assertEqual(row[:7], (2026, "0001", "浙江大学", "工科试验班(信息)", 10, 6000, "4年"))
        self.assertEqual(
            json.loads(row[7]),
            {"type": "ALL_REQUIRED", "subjects": ["物理", "化学"]},
        )
        self.assertFalse(str(row[8]).startswith("/"))

    def test_subject_requirement_parser_expands_abbreviated_subjects(self):
        from scripts.import_zhejiang_enrollment_2026 import subject_requirement_json_from_text

        self.assertEqual(
            json.loads(subject_requirement_json_from_text("物、化、生(3科必选)")),
            {"type": "ALL_REQUIRED", "subjects": ["物理", "化学", "生物"]},
        )
        self.assertEqual(
            json.loads(subject_requirement_json_from_text("史、地、政(3科必选)")),
            {"type": "ALL_REQUIRED", "subjects": ["思想政治", "历史", "地理"]},
        )

    def test_2026_generated_major_code_can_use_name_based_history(self):
        from src.zhejiang.step1_screen import _history_for_program

        code_hist = {("0001", "001"): {2025: 9000}}
        name_hist = {("浙江大学", "工科试验班"): {2025: 9200, 2024: 9400}}

        ranks = _history_for_program(
            "0001",
            "ENR2026-abcdef12",
            "浙江大学",
            "工科试验班(信息)",
            code_hist,
            name_hist,
        )

        self.assertEqual(ranks, {2025: 9200, 2024: 9400})


if __name__ == "__main__":
    unittest.main()
