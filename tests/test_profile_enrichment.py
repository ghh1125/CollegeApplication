"""Tests for school, major, and city profile enrichment."""

from __future__ import annotations

import sqlite3
import unittest


class ProfileReasonTests(unittest.TestCase):
    """Deterministic reason text should follow the selected priority."""

    def _program(self) -> dict:
        return {
            "school_name": "山东大学",
            "school_city": "济南",
            "major_name": "数学类",
            "normalized_major_name": "数学类",
            "discipline_grade": "A+",
            "ruanke_rank": 22,
            "gap_info": {"tier": "冲", "weighted_avg": 7395, "gap": -605},
            "school_profile": {
                "summary": "教育部直属综合类985/211/双一流高校，创办于1901年。",
                "tags": "985/211/双一流",
            },
            "major_profile": {
                "summary": "数学类：研究数学理论与应用，适合数理基础强的学生。",
                "career_direction": "教育、金融、科研、数据分析。",
                "fallback_from": "数学与应用数学",
            },
            "city_profile": {
                "summary": "济南是山东省省会，二线城市。",
                "gdp": "14210亿元",
                "population": "961.6万人",
            },
        }

    def test_school_priority_reason_starts_with_school_profile(self) -> None:
        from src.ranking.profiles import build_profile_sort_reason

        reason = build_profile_sort_reason(self._program(), "学校优先")

        self.assertTrue(reason.startswith("冲；学校优先：山东大学："))
        self.assertLess(reason.index("山东大学："), reason.index("数学类："))
        self.assertLess(reason.index("数学类："), reason.index("济南："))
        self.assertIn("软科第22", reason)
        self.assertIn("gap -605", reason)

    def test_major_priority_reason_starts_with_major_profile(self) -> None:
        from src.ranking.profiles import build_profile_sort_reason

        reason = build_profile_sort_reason(self._program(), "专业优先")

        self.assertTrue(reason.startswith("冲；专业优先：数学类："))
        self.assertLess(reason.index("数学类："), reason.index("山东大学："))
        self.assertIn("学科评估A+", reason)
        self.assertIn("fallback数学与应用数学", reason)

    def test_city_priority_reason_starts_with_city_profile(self) -> None:
        from src.ranking.profiles import build_profile_sort_reason

        reason = build_profile_sort_reason(self._program(), "城市优先")

        self.assertTrue(reason.startswith("冲；城市优先：济南："))
        self.assertLess(reason.index("济南："), reason.index("山东大学："))
        self.assertIn("GDP 14210亿元", reason)
        self.assertIn("常住人口961.6万人", reason)


class ProfileDatabaseTests(unittest.TestCase):
    """Profile tables should attach structured data to candidate programs."""

    def test_enrich_with_profiles_attaches_school_major_and_city_profiles(self) -> None:
        from src.ranking.profiles import enrich_with_profiles
        from scripts.init_db import execute_schema, load_schema_sql

        with sqlite3.connect(":memory:") as conn:
            execute_schema(conn, load_schema_sql())
            conn.execute(
                """
                INSERT INTO school_profile (school_name, summary, source_url)
                VALUES ('山东大学', '山东大学学校画像', 'https://example.edu/sdu')
                """
            )
            conn.execute(
                """
                INSERT INTO major_profile (major_name, summary, fallback_from, source_url)
                VALUES ('数学类', '数学类专业画像', '数学与应用数学', 'https://example.edu/math')
                """
            )
            conn.execute(
                """
                INSERT INTO city_profile (city_name, province, summary, gdp, population, source_url)
                VALUES ('济南', '山东', '济南城市画像', '14210亿元', '961.6万人', 'https://example.gov/jinan')
                """
            )

            enriched = enrich_with_profiles(
                [
                    {
                        "school_name": "山东大学",
                        "school_province": "山东",
                        "school_city": "济南",
                        "normalized_major_name": "数学类",
                        "major_name": "数学类",
                    }
                ],
                conn,
            )

        self.assertEqual(enriched[0]["school_profile"]["summary"], "山东大学学校画像")
        self.assertEqual(enriched[0]["major_profile"]["fallback_from"], "数学与应用数学")
        self.assertEqual(enriched[0]["city_profile"]["gdp"], "14210亿元")


class ProfileScriptTests(unittest.TestCase):
    """Crawler/build helpers should be deterministic and source-grounded."""

    def test_school_profile_from_gaokao_payload_extracts_intro_and_labels(self) -> None:
        from scripts.build_profiles import school_profile_from_gaokao_payload

        row = school_profile_from_gaokao_payload(
            {
                "school_id": "126",
                "name": "山东大学",
                "content": "山东大学是一所历史悠久、学科齐全、实力雄厚的教育部直属重点综合性大学。",
                "motto": "学无止境，气有浩然",
                "create_date": "1901",
                "type_name": "综合类",
                "school_nature_name": "公办",
                "ruanke_rank": "22",
                "num_master": "56",
                "num_doctor": "53",
                "num_academician": "19",
                "label_list": [{"name": "985"}, {"name": "211"}, {"name": "双一流"}],
            },
            "https://static-data.gaokao.cn/www/2.0/school/126/info.json",
        )

        self.assertEqual(row["school_name"], "山东大学")
        self.assertIn("教育部直属重点综合性大学", row["summary"])
        self.assertEqual(row["tags"], "985/211/双一流")
        self.assertEqual(row["ruanke_rank"], 22)

    def test_build_major_profile_rows_falls_back_from_category_to_concrete_major(self) -> None:
        from scripts.build_profiles import build_major_profile_rows

        rows = build_major_profile_rows(
            [
                {
                    "special_id": 1,
                    "name": "数学类",
                    "level3": "51",
                    "is_what": "",
                    "learn_what": "",
                    "do_what": "",
                    "keywords": "",
                },
                {
                    "special_id": 2,
                    "name": "数学与应用数学",
                    "level3": "51",
                    "is_what": "数学与应用数学主要研究数学应用。",
                    "learn_what": "数学分析、高等代数。",
                    "do_what": "教育、金融、数据分析。",
                    "keywords": "数学",
                },
            ]
        )

        by_name = {row["major_name"]: row for row in rows}
        self.assertEqual(by_name["数学类"]["fallback_from"], "数学与应用数学")
        self.assertIn("数学与应用数学主要研究", by_name["数学类"]["summary"])


if __name__ == "__main__":
    unittest.main()
