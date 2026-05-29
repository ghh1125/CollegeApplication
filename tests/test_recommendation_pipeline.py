"""Tests for ranking and volunteer-list assembly."""

from __future__ import annotations

import sqlite3
import unittest


class RankPipelineTests(unittest.TestCase):
    """Behavioral tests for app.pipeline.rank."""

    def test_calculate_gap_renormalizes_available_year_weights(self) -> None:
        from app.pipeline.rank import calculate_gap

        result = calculate_gap(
            36500,
            [
                {"year": 2025, "min_rank": 40000},
                {"year": 2024, "min_rank": None},
                {"year": 2023, "min_rank": 30000},
            ],
        )

        self.assertEqual(result["weighted_avg"], 37143)
        self.assertEqual(result["gap"], 643)
        self.assertEqual(result["ratio"], 0.0173)
        self.assertEqual(result["tier"], "稳")
        self.assertEqual(result["data_years"], 2)

    def test_calculate_gap_returns_cushion_tier_for_very_safe_programs(self) -> None:
        from app.pipeline.rank import calculate_gap

        # ratio = (80000 - 36500) / 80000 = 0.54375 > 0.40 → 垫
        result = calculate_gap(36500, [{"year": 2025, "min_rank": 80000}])

        self.assertEqual(result["tier"], "垫")
        self.assertGreater(result["ratio"], 0.40)

    def test_calculate_gap_handles_missing_history(self) -> None:
        from app.pipeline.rank import calculate_gap

        result = calculate_gap(36500, [{"year": 2025, "min_rank": None}])

        self.assertEqual(
            result,
            {
                "weighted_avg": None,
                "gap": None,
                "ratio": None,
                "tier": "数据不足",
                "data_years": 0,
            },
        )

    def test_enrich_with_history_adds_history_and_sort_metadata(self) -> None:
        from app.pipeline.rank import enrich_with_history
        from scripts.init_db import execute_schema, load_schema_sql

        candidate = {
            "school_code": "0001",
            "school_name": "浙江大学",
            "major_code": "001",
            "major_name": "计算机科学与技术（竺可桢学院）",
        }
        with sqlite3.connect(":memory:") as conn:
            execute_schema(conn, load_schema_sql())
            conn.execute(
                """
                INSERT INTO school_master (school_code, school_name, province, city)
                VALUES ('0001', '浙江大学', '浙江', '杭州')
                """
            )
            conn.execute(
                """
                INSERT INTO major_subject_requirement (
                    normalized_major_name, major_category, requirement_type
                )
                VALUES ('计算机科学与技术', '计算机类', 'NONE')
                """
            )
            conn.executemany(
                """
                INSERT INTO historical_cutoff (
                    year, province, batch, school_code, school_name,
                    major_code, major_name, min_score, min_rank, plan_count
                )
                VALUES (?, '浙江', '普通类', '0001', '浙江大学',
                        '001', '计算机科学与技术', 650, ?, 20)
                """,
                [(2025, 39000), (2024, 37000), (2023, 36000)],
            )

            enriched = enrich_with_history([candidate], year=2025, conn=conn)

        self.assertEqual(
            enriched[0]["history"],
            [
                {"year": 2025, "min_rank": 39000, "min_score": 650, "plan_count": 20},
                {"year": 2024, "min_rank": 37000, "min_score": 650, "plan_count": 20},
                {"year": 2023, "min_rank": 36000, "min_score": 650, "plan_count": 20},
            ],
        )
        self.assertEqual(enriched[0]["school_city"], "杭州")
        self.assertEqual(enriched[0]["normalized_major_name"], "计算机科学与技术")
        self.assertEqual(enriched[0]["major_category"], "计算机类")
        self.assertTrue(enriched[0]["is_985"])
        self.assertTrue(enriched[0]["is_211"])
        self.assertTrue(enriched[0]["is_double_first_class"])

    def test_sort_candidates_preserves_tier_order_and_sorts_inside_tier(self) -> None:
        from app.pipeline.rank import sort_candidates

        candidates = [
            {
                "id": "steady-top",
                "school_city": "杭州",
                "major_name": "计算机科学与技术",
                "gap_info": {"tier": "稳"},
            },
            {
                "id": "rush-city",
                "school_city": "杭州",
                "major_name": "护理学",
                "gap_info": {"tier": "冲"},
            },
            {
                "id": "rush-major",
                "school_city": "兰州",
                "major_name": "计算机科学与技术",
                "gap_info": {"tier": "冲"},
            },
            {
                "id": "cushion",
                "school_city": "杭州",
                "major_name": "计算机科学与技术",
                "gap_info": {"tier": "垫"},
            },
        ]

        sorted_candidates = sort_candidates(
            candidates,
            main_priority="专业优先",
            city_first=True,
            preferred_majors=["计算机科学与技术"],
            preferred_categories=[],
            preferred_schools=[],
            preferred_cities=["杭州"],
        )

        ids = [candidate["id"] for candidate in sorted_candidates]
        # 冲 must come before 稳, 稳 before 垫
        self.assertLess(ids.index("rush-major"), ids.index("steady-top"))
        self.assertLess(ids.index("steady-top"), ids.index("cushion"))
        # Within 冲: major match outranks city match
        self.assertLess(ids.index("rush-major"), ids.index("rush-city"))


class BuilderPipelineTests(unittest.TestCase):
    """Behavioral tests for app.pipeline.builder."""

    def test_build_volunteer_list_allocates_available_tiers_and_reserve(self) -> None:
        from app.pipeline.builder import build_volunteer_list

        candidates = [
            {"id": "c1", "gap_info": {"tier": "冲"}},
            {"id": "c2", "gap_info": {"tier": "冲"}},
            {"id": "s1", "gap_info": {"tier": "稳"}},
            {"id": "b1", "gap_info": {"tier": "保"}},
            {"id": "d1", "gap_info": {"tier": "垫"}},
            {"id": "r1", "gap_info": {"tier": "高危冲"}},
            {"id": "r2", "gap_info": {"tier": "数据不足"}},
        ]

        result = build_volunteer_list(candidates, risk_preference="均衡")

        self.assertEqual([v["id"] for v in result["volunteers"]], ["c1", "c2", "s1", "b1", "d1"])
        self.assertEqual([v["volunteer_no"] for v in result["volunteers"]], [1, 2, 3, 4, 5])
        self.assertEqual([p["id"] for p in result["reserve"]], ["r1", "r2"])
        self.assertEqual(
            result["stats"],
            {"total": 5, "冲": 2, "稳": 1, "保": 1, "垫": 1, "备选池": 2},
        )

    def test_build_volunteer_list_fills_to_total_when_no_cushion_pool(self) -> None:
        from app.pipeline.builder import build_volunteer_list

        candidates = (
            [{"id": f"c{i}", "gap_info": {"tier": "冲"}} for i in range(30)]
            + [{"id": f"s{i}", "gap_info": {"tier": "稳"}} for i in range(30)]
            + [{"id": f"b{i}", "gap_info": {"tier": "保"}} for i in range(30)]
        )

        result = build_volunteer_list(candidates, risk_preference="均衡")

        self.assertEqual(len(result["volunteers"]), 80)
        self.assertEqual(result["stats"]["冲"], 20)
        self.assertEqual(result["stats"]["稳"], 30)
        self.assertEqual(result["stats"]["保"], 30)
        self.assertEqual(result["stats"]["垫"], 0)

    def test_build_volunteer_list_keeps_risk_tiers_in_order_after_backfill(self) -> None:
        from app.pipeline.builder import build_volunteer_list

        candidates = (
            [{"id": "c0", "gap_info": {"tier": "冲"}}]
            + [{"id": f"s{i}", "gap_info": {"tier": "稳"}} for i in range(35)]
            + [{"id": f"b{i}", "gap_info": {"tier": "保"}} for i in range(35)]
        )

        result = build_volunteer_list(candidates, risk_preference="均衡", total=70)
        tiers = [program["gap_info"]["tier"] for program in result["volunteers"]]
        tier_positions = {"冲": 0, "稳": 1, "保": 2, "垫": 3}

        self.assertEqual(
            [tier_positions[tier] for tier in tiers],
            sorted(tier_positions[tier] for tier in tiers),
        )


class RecommendationServiceTests(unittest.TestCase):
    """End-to-end recommendation service tests with frontend-style inputs."""

    def test_history_rank_columns_lists_all_years_with_blanks(self) -> None:
        from app.pipeline.recommend import history_rank_columns

        program = {
            "history": [
                {"year": 2025, "min_rank": 36000},
                {"year": 2023, "min_rank": None},
            ]
        }

        self.assertEqual(
            history_rank_columns(program),
            {"2025位次": "36000", "2024位次": "", "2023位次": ""},
        )

    def test_build_recommendations_uses_city_first_and_major_preferences(self) -> None:
        from app.models.profile import StudentProfile
        from app.pipeline.recommend import build_recommendations
        from scripts.init_db import execute_schema, load_schema_sql

        profile = StudentProfile(
            rank=36500,
            total_score=626,
            selected_subjects=["物理", "化学", "生物"],
        )
        # Both candidates share the same preferred major so major_score is tied at 100.
        # city_first should break the tie in favour of "city"; ruanke_rank should break
        # the tie in favour of "ranked" when city_first=False.
        candidates = [
            {
                "id": "city",
                "school_code": "0001",
                "school_name": "杭州测试大学",
                "major_code": "001",
                "major_name": "计算机科学与技术",
            },
            {
                "id": "ranked",
                "school_code": "0002",
                "school_name": "兰州测试大学",
                "major_code": "002",
                "major_name": "计算机科学与技术",
            },
        ]

        with sqlite3.connect(":memory:") as conn:
            execute_schema(conn, load_schema_sql())
            conn.executemany(
                """
                INSERT INTO school_master (school_code, school_name, province, city, ruanke_rank)
                VALUES (?, ?, '测试省', ?, ?)
                """,
                [
                    ("0001", "杭州测试大学", "杭州", None),   # in preferred city, no ruanke rank
                    ("0002", "兰州测试大学", "兰州", 50),     # better ruanke rank, wrong city
                ],
            )
            conn.executemany(
                """
                INSERT INTO historical_cutoff (
                    year, province, batch, school_code, school_name,
                    major_code, major_name, min_score, min_rank, plan_count
                )
                VALUES (2025, '浙江', '普通类', ?, ?, ?, ?, 620, 36000, 10)
                """,
                [
                    ("0001", "杭州测试大学", "001", "计算机科学与技术"),
                    ("0002", "兰州测试大学", "002", "计算机科学与技术"),
                ],
            )

            city_first = build_recommendations(
                candidates,
                profile,
                main_priority="专业优先",
                city_first=True,
                preferred_majors=["计算机科学与技术"],
                preferred_categories=[],
                preferred_schools=[],
                preferred_cities=["杭州"],
                risk_preference="均衡",
                total=2,
                conn=conn,
            )
            school_first = build_recommendations(
                candidates,
                profile,
                main_priority="专业优先",
                city_first=False,
                preferred_majors=["计算机科学与技术"],
                preferred_categories=[],
                preferred_schools=[],
                preferred_cities=["杭州"],
                risk_preference="均衡",
                total=2,
                conn=conn,
            )

        # Same major score → city is tiebreaker when city_first=True
        self.assertEqual([v["id"] for v in city_first["volunteers"]], ["city", "ranked"])
        # Same major score → ruanke_rank is tiebreaker when city_first=False
        self.assertEqual([v["id"] for v in school_first["volunteers"]], ["ranked", "city"])


if __name__ == "__main__":
    unittest.main()
