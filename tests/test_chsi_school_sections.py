"""Tests for 阳光高考 school-section scraping helpers."""

from __future__ import annotations

import sqlite3
import unittest


class ChsiSchoolSectionTests(unittest.TestCase):
    def test_parse_school_search_page_extracts_school_links(self) -> None:
        from scripts.scrape_chsi_school_sections import parse_school_search_page

        html = """
        <html><body>
          <a href="/sch/schoolInfoMain--schId-395.dhtml">清华大学</a>
          <a href="https://gaokao.chsi.com.cn/sch/schoolInfoMain--schId-31.dhtml">复旦大学</a>
          <a href="/sch/schoolInfoMain--schId-395.dhtml">清华大学</a>
        </body></html>
        """

        schools = parse_school_search_page(html)

        self.assertEqual(
            schools,
            [
                {
                    "school_id": "395",
                    "school_name": "清华大学",
                    "url": "https://gaokao.chsi.com.cn/sch/schoolInfoMain--schId-395.dhtml",
                },
                {
                    "school_id": "31",
                    "school_name": "复旦大学",
                    "url": "https://gaokao.chsi.com.cn/sch/schoolInfoMain--schId-31.dhtml",
                },
            ],
        )

    def test_parse_section_links_keeps_known_chsi_tabs(self) -> None:
        from scripts.scrape_chsi_school_sections import parse_section_links

        html = """
        <html><body>
          <a href="/sch/schoolInfo--schId-395,categoryId-100,mindex-1.dhtml">学校简介</a>
          <a href="/sch/schoolInfo--schId-395,categoryId-101,mindex-2.dhtml">院系设置</a>
          <a href="/sch/schoolInfo--schId-395,categoryId-999,mindex-9.dhtml">无关栏目</a>
        </body></html>
        """

        links = parse_section_links(html)

        self.assertEqual(
            links,
            {
                "overview": {
                    "title": "学校简介",
                    "url": "https://gaokao.chsi.com.cn/sch/schoolInfo--schId-395,categoryId-100,mindex-1.dhtml",
                },
                "departments": {
                    "title": "院系设置",
                    "url": "https://gaokao.chsi.com.cn/sch/schoolInfo--schId-395,categoryId-101,mindex-2.dhtml",
                },
            },
        )

    def test_parse_section_page_strips_navigation_and_scripts(self) -> None:
        from scripts.scrape_chsi_school_sections import parse_section_page

        html = """
        <html><body>
          <div class="nav"><a>学校简介</a><a>录取规则</a></div>
          <div class="content">
            <h2>录取规则</h2>
            <p>按分数优先原则录取。</p>
            <script>window.noise = true;</script>
            <p>同分时参考语文、数学、外语成绩。</p>
          </div>
        </body></html>
        """

        row = parse_section_page(
            html,
            school_id="395",
            school_name="清华大学",
            section_key="admission_rules",
            section_title="录取规则",
            source_url="https://example.test/rules",
        )

        self.assertEqual(row["school_name"], "清华大学")
        self.assertEqual(row["section_key"], "admission_rules")
        self.assertIn("按分数优先原则录取", row["content"])
        self.assertIn("同分时参考语文、数学、外语成绩", row["content"])
        self.assertNotIn("window.noise", row["content"])

    def test_upsert_school_info_sections_creates_and_updates_rows(self) -> None:
        from scripts.scrape_chsi_school_sections import (
            ensure_school_info_section_table,
            upsert_school_info_sections,
        )

        with sqlite3.connect(":memory:") as conn:
            ensure_school_info_section_table(conn)
            upsert_school_info_sections(
                conn,
                [
                    {
                        "school_id": "395",
                        "school_name": "清华大学",
                        "section_key": "admission_rules",
                        "section_title": "录取规则",
                        "summary": "按分数优先原则录取。",
                        "content": "按分数优先原则录取。",
                        "source_url": "https://example.test/old",
                        "source_name": "阳光高考",
                        "fetched_at": "2026-06-08T12:00:00",
                    }
                ],
            )
            upsert_school_info_sections(
                conn,
                [
                    {
                        "school_id": "395",
                        "school_name": "清华大学",
                        "section_key": "admission_rules",
                        "section_title": "录取规则",
                        "summary": "更新后的录取规则。",
                        "content": "更新后的录取规则。",
                        "source_url": "https://example.test/new",
                        "source_name": "阳光高考",
                        "fetched_at": "2026-06-08T13:00:00",
                    }
                ],
            )

            rows = conn.execute(
                "SELECT school_name, section_key, summary, source_url FROM school_info_section"
            ).fetchall()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0], ("清华大学", "admission_rules", "更新后的录取规则。", "https://example.test/new"))

    def test_detects_chsi_guard_page(self) -> None:
        from scripts.scrape_chsi_school_sections import is_chsi_guard_page

        html = "<html><head><script>$_ts={};</script></head><body></body></html><script>_$_w();</script>"

        self.assertTrue(is_chsi_guard_page(html))

    def test_fetch_treats_412_as_guard_page(self) -> None:
        from scripts.scrape_chsi_school_sections import _fetch

        class FakeResponse:
            status_code = 412
            text = ""
            encoding = "utf-8"
            apparent_encoding = "utf-8"

            def raise_for_status(self) -> None:
                raise AssertionError("412 should be handled before raise_for_status")

        class FakeSession:
            def get(self, url: str, timeout: int):
                return FakeResponse()

        self.assertIsNone(_fetch(FakeSession(), "https://example.test"))


if __name__ == "__main__":
    unittest.main()
