"""浙江重构 · 输入层测试（只测新输入模块，不涉及旧逻辑/LLM）。"""

from __future__ import annotations

import unittest

import pytest

pydantic = pytest.importorskip("pydantic")

from src.zhejiang.input.disciplines import (
    CATEGORY_NAMES,
    MAJOR_CLASS_NAMES,
    category_of,
    class_name,
    classes_grouped,
)
from src.zhejiang.input.student_input import Budget, StudentInput


class DisciplineTableTests(unittest.TestCase):
    def test_12_categories(self):
        # 12 个本科门类（军事学 11 不在招生口径）
        self.assertEqual(len(CATEGORY_NAMES), 12)
        self.assertEqual(CATEGORY_NAMES["08"], "工学")

    def test_class_lookup(self):
        self.assertEqual(class_name("0809"), "计算机类")
        self.assertEqual(category_of("0809"), "工学")
        self.assertEqual(class_name("0203"), "金融学类")

    def test_grouped_covers_all_classes(self):
        grouped = classes_grouped()
        total = sum(len(v) for v in grouped.values())
        self.assertEqual(total, len(MAJOR_CLASS_NAMES))
        self.assertIn("工学", grouped)


class StudentInputTests(unittest.TestCase):
    def _base(self, **kw):
        data = dict(rank=8000, total_score=600, selected_subjects=["物理", "化学", "生物"])
        data.update(kw)
        return StudentInput(**data)

    def test_minimal_valid(self):
        s = self._base()
        self.assertEqual(s.rank, 8000)
        self.assertEqual(s.budget, Budget.ANY)
        self.assertFalse(s.region.has_preference)

    def test_subjects_must_be_three(self):
        with self.assertRaises(Exception):
            self._base(selected_subjects=["物理", "化学"])

    def test_subject_alias_and_dedup(self):
        s = self._base(selected_subjects=["思想政治", "历史", "地理"])
        self.assertEqual(s.selected_subjects, ["政治", "历史", "地理"])
        with self.assertRaises(Exception):
            self._base(selected_subjects=["物理", "物理", "化学"])

    def test_major_classes_filter_invalid_and_dedup(self):
        s = self._base(major_classes=["0809", "9999", "0809", "0807"])
        self.assertEqual(s.major_classes, ["0809", "0807"])  # 去非法 + 去重保序

    def test_region_requires_provinces_when_preferred(self):
        with self.assertRaises(Exception):
            self._base(region={"has_preference": True, "provinces": []})
        s = self._base(region={"has_preference": True, "provinces": ["浙江", "上海", "浙江"]})
        self.assertEqual(s.region.provinces, ["浙江", "上海"])  # 去重保序=优先级

    def test_region_cleared_when_no_preference(self):
        s = self._base(region={"has_preference": False, "provinces": ["浙江"]})
        self.assertEqual(s.region.provinces, [])

    def test_rank_and_score_bounds(self):
        with self.assertRaises(Exception):
            self._base(rank=0)
        with self.assertRaises(Exception):
            self._base(total_score=800)

    def test_medical_and_scores_optional(self):
        s = self._base(
            medical={"height_cm": 175, "color_vision": "色弱", "naked_eye_vision": 4.6},
            subject_scores={"chinese": 120, "math": 130, "foreign": 125},
        )
        self.assertEqual(s.medical.color_vision, "色弱")
        self.assertEqual(s.subject_scores.math, 130)


if __name__ == "__main__":
    unittest.main()
