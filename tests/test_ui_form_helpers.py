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


if __name__ == "__main__":
    unittest.main()
