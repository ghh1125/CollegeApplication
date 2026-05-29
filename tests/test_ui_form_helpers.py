"""Tests for Streamlit form helper logic."""

from __future__ import annotations

import unittest


class FormHelperTests(unittest.TestCase):
    """Pure helper tests for dynamic sidebar inputs."""

    def test_normalize_items_splits_commas_and_deduplicates(self) -> None:
        from app.ui.form_helpers import normalize_items

        self.assertEqual(
            normalize_items([" 计算机科学与技术，软件工程 ", "", "软件工程", "人工智能"]),
            ["计算机科学与技术", "软件工程", "人工智能"],
        )

    def test_split_major_preferences_moves_class_terms_to_categories(self) -> None:
        from app.ui.form_helpers import split_major_preferences

        majors, categories = split_major_preferences(["计算机科学与技术", "计算机类", "电子信息类"])

        self.assertEqual(majors, ["计算机科学与技术"])
        self.assertEqual(categories, ["计算机类", "电子信息类"])

    def test_queue_ai_message_clears_next_input_by_incrementing_key(self) -> None:
        from app.ui.form_helpers import queue_ai_message

        state = {}

        queued = queue_ai_message(state, " 位次8000，物理化学生物，专业优先 ", input_index=3)

        self.assertTrue(queued)
        self.assertEqual(state["_ai_pending_msg"], "位次8000，物理化学生物，专业优先")
        self.assertEqual(state["_ai_input_n"], 4)

    def test_queue_ai_message_ignores_blank_text(self) -> None:
        from app.ui.form_helpers import queue_ai_message

        state = {"_ai_input_n": 3}

        queued = queue_ai_message(state, "   ", input_index=3)

        self.assertFalse(queued)
        self.assertEqual(state, {"_ai_input_n": 3})

    def test_format_sort_reason_keeps_backend_reason(self) -> None:
        from app.ui.form_helpers import format_sort_reason_for_display

        self.assertEqual(
            format_sort_reason_for_display(
                {"sort_reason": "冲；专业优先：学科评估A+；gap -76"},
                "专业优先",
            ),
            "冲；专业优先：学科评估A+；gap -76",
        )

    def test_format_sort_reason_falls_back_when_backend_reason_missing(self) -> None:
        from app.ui.form_helpers import format_sort_reason_for_display

        reason = format_sort_reason_for_display(
            {
                "school_name": "浙江大学",
                "major_name": "应用生物科学",
                "school_city": "杭州",
                "ruanke_rank": 3,
                "gap_info": {"tier": "冲", "gap": -538},
            },
            "学校优先",
        )

        self.assertTrue(reason.startswith("冲；学校优先：浙江大学：软科第3"))
        self.assertIn("应用生物科学：专业画像待补充", reason)
        self.assertIn("风险：gap -538", reason)


if __name__ == "__main__":
    unittest.main()
