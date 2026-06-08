"""Tests for Shanghai 2026 target-volunteer copy and reference years."""

from __future__ import annotations

import unittest
import sqlite3


class ShanghaiTargetYearTests(unittest.TestCase):
    def test_2026_target_uses_2025_to_2023_history(self) -> None:
        from src.shanghai import service as svc

        self.assertEqual(svc.TARGET_YEAR, 2026)
        self.assertEqual(svc.REFERENCE_YEARS, (2025, 2024, 2023))
        self.assertEqual(svc.YEARS, svc.REFERENCE_YEARS)

    def test_target_tab_label_explains_2025_reference_line(self) -> None:
        from src.shanghai import service as svc

        self.assertIn("2026 目标志愿", svc.target_tab_label())
        self.assertIn("2025", svc.target_tab_label())

    def test_2026_plan_candidate_uses_same_major_historical_scores(self) -> None:
        from src.shanghai import service as svc

        form = {
            "rank": 9000,
            "selected_subjects": ["物理", "化学", "生物"],
            "main_priority": "专业优先",
            "risk_preference": "均衡",
            "preferred_majors": ["计算机"],
            "school_levels": [],
            "preferred_cities": [],
            "accept_private": True,
            "excluded_regions": [],
        }
        profile = svc.build_profile(form)
        with sqlite3.connect(":memory:") as conn:
            conn.executescript(
                """
                CREATE TABLE admission_plan (
                    id INTEGER PRIMARY KEY,
                    year INTEGER,
                    school_code TEXT,
                    school_name TEXT,
                    special_group TEXT,
                    sg_name TEXT,
                    sg_info TEXT,
                    major_code TEXT,
                    major_name TEXT,
                    plan_count INTEGER,
                    subject_requirement TEXT,
                    subject_requirement_json TEXT,
                    tuition INTEGER,
                    duration TEXT,
                    subject_category TEXT
                );
                CREATE TABLE historical_cutoff (
                    year INTEGER,
                    school_code TEXT,
                    school_name TEXT,
                    special_group TEXT,
                    min_score INTEGER,
                    min_rank INTEGER,
                    plan_count INTEGER,
                    major_code TEXT,
                    major_name TEXT,
                    subject_category TEXT
                );
                CREATE TABLE school_master (
                    school_name TEXT,
                    province TEXT,
                    city TEXT,
                    ruanke_rank INTEGER
                );
                CREATE TABLE major_subject_requirement (
                    normalized_major_name TEXT,
                    major_category TEXT
                );
                CREATE TABLE major_description (name TEXT);
                """
            )
            conn.execute(
                "INSERT INTO school_master VALUES ('上海测试大学', '上海', '上海', 80)"
            )
            conn.execute(
                "INSERT INTO major_subject_requirement VALUES ('计算机科学与技术', '计算机类')"
            )
            conn.execute("INSERT INTO major_description VALUES ('计算机科学与技术')")
            conn.execute(
                """
                INSERT INTO admission_plan (
                    year, school_code, school_name, special_group, sg_name, sg_info,
                    major_code, major_name, plan_count, subject_requirement,
                    subject_requirement_json, tuition, duration, subject_category
                ) VALUES (
                    2026, '1001', '上海测试大学', '2026-A01', '01', '物理+化学',
                    '080901', '计算机科学与技术', 4, '物理+化学',
                    '{"type":"ALL","subjects":["物理","化学"]}', 5000, '四年', '综合'
                )
                """
            )
            conn.executemany(
                """
                INSERT INTO historical_cutoff (
                    year, school_code, school_name, special_group, min_score,
                    min_rank, plan_count, major_code, major_name, subject_category
                ) VALUES (?, '1001', '上海测试大学', ?, ?, ?, 4, ?, '计算机科学与技术', '综合')
                """,
                [
                    (2025, "2025-X01", 545, 8200, "080901"),
                    (2024, "2024-X03", 541, 8600, "080901"),
                    (2023, "2023-X02", 538, 9100, "080901"),
                ],
            )

            reco = svc._build_recommendation_for_plan_year(
                conn, form, profile, plan_year=2026, history_year=2025
            )

        self.assertEqual(reco["_plan_year"], 2026)
        self.assertEqual(reco["_history_source_year"], 2025)
        self.assertEqual(reco["stats"]["total"], 1)
        group = reco["volunteers"][0]
        self.assertEqual(group["special_group"], "2026-A01")
        self.assertEqual(group["history"][0]["year"], 2025)
        self.assertEqual(group["history"][0]["min_score"], 545)
        self.assertEqual(group["history"][0]["min_rank"], 8200)
        rows = svc.group_rows([group])
        self.assertEqual(rows[0]["2025分数线"], 545)
        self.assertEqual(rows[0]["2025位次"], 8200)

    def test_2026_target_does_not_treat_same_group_number_as_same_group(self) -> None:
        from src.shanghai import service as svc

        form = {
            "rank": 9000,
            "selected_subjects": ["物理", "化学", "生物"],
            "main_priority": "专业优先",
            "risk_preference": "均衡",
            "preferred_majors": ["计算机"],
            "school_levels": [],
            "preferred_cities": [],
            "accept_private": True,
            "excluded_regions": [],
        }
        profile = svc.build_profile(form)
        with sqlite3.connect(":memory:") as conn:
            conn.executescript(
                """
                CREATE TABLE admission_plan (
                    id INTEGER PRIMARY KEY,
                    year INTEGER,
                    school_code TEXT,
                    school_name TEXT,
                    special_group TEXT,
                    sg_name TEXT,
                    sg_info TEXT,
                    major_code TEXT,
                    major_name TEXT,
                    plan_count INTEGER,
                    subject_requirement TEXT,
                    subject_requirement_json TEXT,
                    tuition INTEGER,
                    duration TEXT,
                    subject_category TEXT
                );
                CREATE TABLE historical_cutoff (
                    year INTEGER,
                    school_code TEXT,
                    school_name TEXT,
                    special_group TEXT,
                    min_score INTEGER,
                    min_rank INTEGER,
                    plan_count INTEGER,
                    major_code TEXT,
                    major_name TEXT,
                    subject_category TEXT
                );
                CREATE TABLE school_master (
                    school_name TEXT,
                    province TEXT,
                    city TEXT,
                    ruanke_rank INTEGER
                );
                CREATE TABLE major_subject_requirement (
                    normalized_major_name TEXT,
                    major_category TEXT
                );
                CREATE TABLE major_description (name TEXT);
                """
            )
            conn.execute(
                "INSERT INTO school_master VALUES ('上海测试大学', '上海', '上海', 80)"
            )
            conn.execute(
                "INSERT INTO major_subject_requirement VALUES ('计算机科学与技术', '计算机类')"
            )
            conn.execute("INSERT INTO major_description VALUES ('计算机科学与技术')")
            conn.execute(
                """
                INSERT INTO admission_plan (
                    year, school_code, school_name, special_group, sg_name, sg_info,
                    major_code, major_name, plan_count, subject_requirement,
                    subject_requirement_json, tuition, duration, subject_category
                ) VALUES (
                    2026, '1001', '上海测试大学', '01', '01', '物理+化学',
                    '080901', '计算机科学与技术', 4, '物理+化学',
                    '{"type":"ALL","subjects":["物理","化学"]}', 5000, '四年', '综合'
                )
                """
            )
            conn.executemany(
                """
                INSERT INTO historical_cutoff (
                    year, school_code, school_name, special_group, min_score,
                    min_rank, plan_count, major_code, major_name, subject_category
                ) VALUES (2025, '1001', '上海测试大学', '01', ?, ?, 4, ?, ?, '综合')
                """,
                [
                    (530, 10000, "__GROUP__", "上海测试大学01专业组"),
                    (550, 8000, "080901", "计算机科学与技术"),
                ],
            )

            reco = svc._build_recommendation_for_plan_year(
                conn, form, profile, plan_year=2026, history_year=2025
            )

        group = reco["volunteers"][0]
        self.assertEqual(group["history"][0]["min_score"], 550)
        self.assertEqual(group["history"][0]["min_rank"], 8000)


if __name__ == "__main__":
    unittest.main()
