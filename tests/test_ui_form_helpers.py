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


if __name__ == "__main__":
    unittest.main()
