"""Tests for 2026 admission-charter scraping helpers."""

from __future__ import annotations

import json
import sqlite3
import unittest


class ChsiAdmissionCharterTests(unittest.TestCase):
    def test_parse_html_charter_extracts_structured_fields(self) -> None:
        from scripts.scrape_chsi_admission_charters import parse_charter_html

        html = """
        <html><head><title>浙江工业大学本科招生网</title></head><body>
          <div class="news1title">浙江工业大学2026年普通高校招生章程</div>
          <div id="news1content">
            <table>
              <tr><td>一、院校全称</td><td>浙江工业大学</td></tr>
              <tr><td>九、专业教学培养使用的外语语种</td><td>学校外语公共课为英语。</td></tr>
              <tr><td>十一、录取规则</td><td>按照“分数优先、遵循志愿”原则录取，专业志愿间不设级差分。</td></tr>
              <tr><td>十二、收费标准 学费标准</td><td>工科类6000元/学年，其他专业5300元/学年。</td></tr>
              <tr><td>住宿费标准</td><td>不超过1600元/人学年。</td></tr>
            </table>
          </div>
        </body></html>
        """

        row = parse_charter_html(
            html,
            year=2026,
            school_name="浙江工业大学",
            source_url="https://example.edu/zjut",
        )

        self.assertEqual(row["title"], "浙江工业大学2026年普通高校招生章程")
        self.assertEqual(row["source_type"], "html")
        self.assertIn("6000元", row["tuition_text"])
        self.assertIn("1600元", row["housing_fee_text"])
        self.assertIn("分数优先", row["admission_rules_text"])
        self.assertIn("英语", row["language_requirement_text"])

    def test_parse_image_charter_marks_ocr_needed_and_keeps_image_url(self) -> None:
        from scripts.scrape_chsi_admission_charters import parse_charter_html

        html = """
        <html><body>
          <div id="news1content">
            <img src="/admin/upload/image/20260521111207_43906.png" width="950" height="3249" />
          </div>
        </body></html>
        """

        row = parse_charter_html(
            html,
            year=2026,
            school_name="杭州电子科技大学",
            source_url="https://zhaosheng.hdu.edu.cn/art.php?aid=2452",
        )

        self.assertEqual(row["source_type"], "image")
        self.assertEqual(row["ocr_status"], "needed")
        self.assertEqual(
            json.loads(row["image_urls"]),
            ["https://zhaosheng.hdu.edu.cn/admin/upload/image/20260521111207_43906.png"],
        )

    def test_discover_charter_links_finds_2026_admission_charter(self) -> None:
        from scripts.scrape_chsi_admission_charters import discover_charter_links

        html = """
        <html><body>
          <a href="/html/n2553.html">浙江工业大学2026年普通高校招生章程</a>
          <a href="/html/n1000.html">2025年普通高校招生章程</a>
        </body></html>
        """

        links = discover_charter_links(html, "https://zs.zjut.edu.cn/", year=2026)

        self.assertEqual(
            links,
            [{"title": "浙江工业大学2026年普通高校招生章程", "url": "https://zs.zjut.edu.cn/html/n2553.html"}],
        )

    def test_upsert_admission_charters_creates_and_updates_rows(self) -> None:
        from scripts.scrape_chsi_admission_charters import (
            ensure_admission_charter_table,
            upsert_admission_charters,
        )

        row = {
            "year": 2026,
            "school_name": "浙江工业大学",
            "school_id": "242",
            "province_scope": "浙江",
            "title": "旧标题",
            "content": "旧内容",
            "content_html": "<div>旧内容</div>",
            "image_urls": "[]",
            "source_name": "高校官网",
            "source_url": "https://example.edu/old",
            "source_type": "html",
            "fetch_status": "ok",
            "ocr_status": "not_needed",
            "tuition_text": "旧学费",
            "housing_fee_text": "",
            "admission_rules_text": "",
            "language_requirement_text": "",
            "physical_requirement_text": "",
            "contact_text": "",
            "plan_policy_text": "",
            "fetched_at": "2026-06-10T10:00:00",
        }

        with sqlite3.connect(":memory:") as conn:
            ensure_admission_charter_table(conn)
            upsert_admission_charters(conn, [row])
            updated = dict(row, title="新标题", tuition_text="工科类6000元/学年")
            upsert_admission_charters(conn, [updated])
            rows = conn.execute(
                "SELECT school_name, title, tuition_text FROM admission_charter"
            ).fetchall()

        self.assertEqual(rows, [("浙江工业大学", "新标题", "工科类6000元/学年")])

    def test_missing_charter_row_marks_explicit_missing_status(self) -> None:
        from scripts.scrape_chsi_admission_charters import missing_charter_row

        row = missing_charter_row(2026, "缺失大学", "999")

        self.assertEqual(row["year"], 2026)
        self.assertEqual(row["school_name"], "缺失大学")
        self.assertEqual(row["school_id"], "999")
        self.assertEqual(row["source_type"], "missing")
        self.assertEqual(row["fetch_status"], "missing")
        self.assertEqual(row["ocr_status"], "not_available")


if __name__ == "__main__":
    unittest.main()
