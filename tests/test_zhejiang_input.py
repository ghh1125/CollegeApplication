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
from src.zhejiang.input import medical_rules as mr


class MedicalRuleTests(unittest.TestCase):
    def test_conditions_for(self):
        self.assertEqual(mr.conditions_for("正常"), [])
        self.assertEqual(mr.conditions_for("色弱"), ["色弱"])
        self.assertIn("色盲", mr.conditions_for("色盲", 4.5))
        self.assertIn("视力低于4.8", mr.conditions_for("色盲", 4.5))
        self.assertEqual(mr.conditions_for("正常", 4.9), ["视力低于5.0"])

    def test_blind_superset_of_weak(self):
        weak = set(mr.restricted_majors("色弱"))
        blind = set(mr.restricted_majors("色盲"))
        self.assertTrue(weak.issubset(blind))   # 色盲含色弱全部
        self.assertGreater(len(blind), len(weak))

    def test_class_codes_valid(self):
        for cond in mr.RULES:
            for c in mr.restricted_classes(cond):
                self.assertIn(c, MAJOR_CLASS_NAMES)

    def test_medical_classes_in_color_rules(self):
        # 「医学类各专业」→ 临床医学类(1002) 应在色弱/色盲整类里
        self.assertIn("1002", mr.restricted_classes("色弱"))
        self.assertIn("0703", mr.restricted_classes("色盲"))  # 化学类


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

    def test_major_categories_filter_invalid_and_dedup(self):
        s = self._base(major_categories=["08", "99", "08", "07"])
        self.assertEqual(s.major_categories, ["08", "07"])  # 去非法(99) + 去重保序

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


class PersonaTests(unittest.TestCase):
    def test_classify_boundaries(self):
        from src.zhejiang.persona import classify, LATEST_FIRST_SEGMENT_RANK
        self.assertEqual(classify(1).key, "elite")
        self.assertEqual(classify(5000).key, "elite")
        self.assertEqual(classify(5001).key, "premium")
        self.assertEqual(classify(50000).key, "premium")
        self.assertEqual(classify(50001).key, "core")
        self.assertEqual(classify(LATEST_FIRST_SEGMENT_RANK).key, "core")
        self.assertEqual(classify(LATEST_FIRST_SEGMENT_RANK + 1).key, "below")


class ScreeningTests(unittest.TestCase):
    """筛选+组装+排序（需真实库，缺库则跳过）。"""

    def setUp(self):
        import os
        if not os.path.exists("data/zhejiang/college.db"):
            self.skipTest("no zhejiang db")
        from src.zhejiang.screening import screen
        from src.zhejiang.input.student_input import StudentInput
        self.screen = screen
        self.SI = StudentInput

    def test_screen_filters_and_sorts(self):
        s = self.SI(rank=8000, total_score=620, selected_subjects=["物理", "化学", "生物"],
                    major_classes=["0809"])  # 计算机类
        rows = self.screen(s)
        self.assertGreater(len(rows), 0)
        # 全部是计算机类
        self.assertTrue(all(r["二级学科"] == "计算机类" for r in rows))
        # 按 2025 位次升序
        ranks = [r["2025最低位次"] for r in rows if r["2025最低位次"] is not None]
        self.assertEqual(ranks, sorted(ranks))
        # 列齐全
        for col in ("排序", "专业名称", "学科评估", "类别", "院校名称", "层次"):
            self.assertIn(col, rows[0])

    def test_filter_by_category_includes_all_classes(self):
        # 选一级学科「工学(08)」应包含其下多个专业类（计算机类0809 等）
        s = self.SI(rank=8000, total_score=620, selected_subjects=["物理", "化学", "生物"],
                    major_categories=["08"])
        rows = self.screen(s)
        self.assertGreater(len(rows), 0)
        self.assertTrue(all(r["类别"] == "工学" for r in rows))
        # 应不止一个专业类
        self.assertGreater(len({r["二级学科"] for r in rows}), 1)

    def test_medical_color_blind_excludes(self):
        # 色盲应剔除临床医学类(1002)
        s = self.SI(rank=8000, total_score=620, selected_subjects=["物理", "化学", "生物"],
                    major_classes=["1002"], medical={"color_vision": "色盲"})
        rows = self.screen(s)
        self.assertEqual(len(rows), 0)  # 临床医学类被色盲剔除


class FinalVolunteerTests(unittest.TestCase):
    """最终 80 志愿生成（需真实库，缺库则跳过）。"""

    def setUp(self):
        import os
        if not os.path.exists("data/zhejiang/college.db"):
            self.skipTest("no zhejiang db")
        from src.zhejiang.final_volunteers import generate
        from src.zhejiang.input.student_input import StudentInput
        self.generate = generate
        self.SI = StudentInput

    def test_generate_80_and_columns(self):
        s = self.SI(rank=8000, total_score=620, selected_subjects=["物理", "化学", "生物"])
        rows = self.generate(s)
        self.assertEqual(len(rows), 80)  # 凑满 80
        # 列齐全
        for col in ("序号", "冲稳保", "专业名称", "考研路径", "专业发展路径", "三年平均位次"):
            self.assertIn(col, rows[0])
        # 按 2025 位次升序编号
        r25 = [r["2025最低位次"] for r in rows]
        self.assertEqual(r25, sorted(r25))
        self.assertEqual([r["序号"] for r in rows], list(range(1, 81)))
        # 冲稳保都有
        self.assertEqual({r["冲稳保"] for r in rows}, {"冲", "稳", "保"})

    def test_exclude_keywords(self):
        s = self.SI(rank=8000, total_score=620, selected_subjects=["物理", "化学", "生物"])
        rows = self.generate(s, exclude_keywords=["护理"])
        self.assertTrue(all("护理" not in r["专业名称"] for r in rows))
