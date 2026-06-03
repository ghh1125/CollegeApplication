"""Tests for Jiangsu plan-detail normalization and ingest."""

from __future__ import annotations

import csv
import sqlite3

import pandas as pd


def test_normalize_frame_maps_plan_row_to_official_group() -> None:
    from scripts.parse_jiangsu_plan_details import normalize_frame

    frame = pd.DataFrame(
        [
            {
                "院校代号": "1101",
                "院校名称": "南京大学",
                "院校专业组": "04专业组(化学)",
                "科类": "物理类",
                "专业代号": "01",
                "专业名称": "计算机科学与技术",
                "计划数": "6",
                "学费": "6380",
                "学制": "四年",
            }
        ]
    )
    official = {
        (2025, "物理类", "1101", "04"): {
            "sg_info": "首选物理，再选化学",
        }
    }

    rows = normalize_frame(
        frame,
        year=2025,
        source_file=__import__("pathlib").Path("data/jiangsu/raw/plan_sources/2025/nju.xlsx"),
        official_groups=official,
    )

    assert rows[0]["school_code"] == "1101"
    assert rows[0]["special_group"] == "1101-04"
    assert rows[0]["major_code"] == "01"
    assert rows[0]["major_name"] == "计算机科学与技术"
    assert rows[0]["plan_count"] == 6
    assert rows[0]["tuition"] == 6380
    assert rows[0]["matched_official_group"] == 1


def test_parse_text_lines_handles_group_header_then_major_rows(tmp_path) -> None:
    from scripts.parse_jiangsu_plan_details import parse_text_lines

    source = tmp_path / "yangzhou.txt"
    source.write_text(
        "物理类\n"
        "138122 扬州大学22专业组(化学)51\n"
        "65 数学与应用数学(师范) 2 5500 四年\n"
        "66 物理学(师范) 3 5500 四年\n",
        encoding="utf-8",
    )
    source.with_suffix(".txt.url").write_text("https://example.test/plan", encoding="utf-8")
    official = {
        (2025, "物理类", "1381", "22"): {
            "school_name": "扬州大学",
            "sg_info": "首选物理，再选化学",
        }
    }

    rows = parse_text_lines(
        source.read_text(encoding="utf-8"),
        year=2025,
        source_file=source,
        official_groups=official,
    )

    assert [row["major_name"] for row in rows] == ["数学与应用数学(师范)", "物理学(师范)"]
    assert rows[0]["school_code"] == "1381"
    assert rows[0]["special_group"] == "1381-22"
    assert rows[0]["source_url"] == "https://example.test/plan"
    assert rows[0]["plan_count"] == 2
    assert rows[0]["tuition"] == 5500


def test_parse_text_lines_uses_subject_context_not_campus_as_requirement(tmp_path) -> None:
    from scripts.parse_jiangsu_plan_details import parse_text_lines

    source = tmp_path / "just.txt"
    source.write_text(
        "普通类（物理+不限）\n"
        "140204 江苏科技大学04专业组（镇江校区）204\n"
        "09 管理科学 85\n",
        encoding="utf-8",
    )
    official = {
        (2025, "物理类", "1402", "04"): {
            "school_name": "江苏科技大学",
            "sg_info": "首选物理，再选不限",
        }
    }

    rows = parse_text_lines(
        source.read_text(encoding="utf-8"),
        year=2025,
        source_file=source,
        official_groups=official,
    )

    assert rows[0]["major_name"] == "管理科学"
    assert rows[0]["sg_info"] == "首选物理，再选不限"
    assert "校区" not in rows[0]["sg_info"]


def test_normalize_frame_handles_combo_school_group_code() -> None:
    from scripts.parse_jiangsu_plan_details import normalize_frame

    frame = pd.DataFrame(
        [
            ["2025年江苏省分专业分批次计划"] * 7,
            ["普通计划-物理类本科批合计"] * 7,
            ["代号", "院校、专业组（再选科目要求）、专业名称及备注", "计划数", "2024年录取最低分", "2024年录取最低分位次", "学费", "备注"],
            ["110608", "南京信息工程大学08专业组(物理+化学)", "13", "", "", "", ""],
            ["61", "大气科学(拔尖学生培养基地)", "13", "635", "9940", "6050", "教育部拔尖学生培养基地"],
        ]
    )
    official = {
        (2025, "物理类", "1106", "08"): {
            "school_name": "南京信息工程大学",
            "sg_info": "首选物理，再选化学",
        }
    }

    rows = normalize_frame(
        frame,
        year=2025,
        source_file=__import__("pathlib").Path("data/jiangsu/raw/plan_sources/2025/nuist.htm"),
        official_groups=official,
    )

    assert rows[0]["school_code"] == "1106"
    assert rows[0]["special_group"] == "1106-08"
    assert rows[0]["major_code"] == "61"
    assert rows[0]["major_name"] == "大气科学(拔尖学生培养基地)"
    assert rows[0]["sg_info"] == "首选物理，再选化学"
    assert rows[0]["plan_count"] == 13


def test_parse_jszs_plan_page_maps_group_tables_to_official_groups(tmp_path) -> None:
    from scripts.parse_jiangsu_plan_details import parse_jszs_plan_page

    source = tmp_path / "nju_jszs.html"
    source.write_text(
        """
        <html><head><title>南京大学--大数据中心--江苏招生考试网</title></head>
        <body>
          <div class="center_box">
            <div class="center_box_title">
              <h4>2025年　普通类　历史　本科批次　　南京大学03专业组(不限)</h4>
            </div>
            <div class="center_box_table">
              <table>
                <tr><th>专业名称</th><th>首选科目</th><th>再选科目</th><th>计划数</th><th>变化情况</th><th>学制</th><th>学费</th></tr>
                <tr>
                  <td style="font-weight:bold;">人文科学试验班 <span>注</span></td>
                  <td>历史</td><td>(不限)</td><td>46</td><td>--</td><td>四</td><td>5720</td>
                </tr>
                <tr>
                  <td style="font-weight:bold;">汉语言文学</td>
                  <td>历史</td><td>(不限)</td><td>10</td><td>--</td><td>四</td><td>5720</td>
                </tr>
              </table>
            </div>
          </div>
        </body></html>
        """,
        encoding="utf-8",
    )
    source.with_suffix(".html.url").write_text(
        "https://gaoxiao.jszs.com/College/plannew.html?cid=5&yearno=2025",
        encoding="utf-8",
    )
    official = {
        (2025, "历史类", "1101", "03"): {
            "school_code": "1101",
            "school_name": "南京大学",
            "sg_info": "首选历史，再选不限",
        }
    }

    rows = parse_jszs_plan_page(source, 2025, official)

    assert [row["major_name"] for row in rows] == ["人文科学试验班", "汉语言文学"]
    assert rows[0]["school_code"] == "1101"
    assert rows[0]["special_group"] == "1101-03"
    assert rows[0]["plan_count"] == 46
    assert rows[0]["tuition"] == 5720
    assert rows[0]["matched_official_group"] == 1
    assert rows[0]["source_url"].startswith("https://gaoxiao.jszs.com/")


def test_ingest_plan_details_expands_group_cutoff_to_major_rows(tmp_path, monkeypatch) -> None:
    from src.jiangsu.input import ingest

    detail_dir = tmp_path / "plan_details"
    detail_dir.mkdir()
    path = detail_dir / "plan_details_2025_physics.csv"
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "year", "subject_category", "school_code", "school_name",
                "special_group", "sg_name", "sg_info", "major_code", "major_name",
                "plan_count", "tuition", "duration",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "year": "2025",
                "subject_category": "物理类",
                "school_code": "1101",
                "school_name": "南京大学",
                "special_group": "1101-04",
                "sg_name": "04",
                "sg_info": "首选物理，再选化学",
                "major_code": "01",
                "major_name": "计算机科学与技术",
                "plan_count": "6",
                "tuition": "6380",
                "duration": "四年",
            }
        )

    monkeypatch.setattr(ingest, "PLAN_DETAIL_DIR", detail_dir)

    with sqlite3.connect(":memory:") as conn:
        conn.executescript(ingest.SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute(
            """
            INSERT INTO historical_cutoff (
                year, subject_category, school_code, school_name,
                special_group, sg_name, sg_info, major_code, major_name,
                min_score, min_rank
            ) VALUES (
                2025, '物理类', '1101', '南京大学',
                '1101-04', '04', '首选物理，再选化学',
                '__GROUP__', '南京大学04专业组-首选物理再选化学',
                690, 126
            )
            """
        )

        inserted = ingest.ingest_plan_details(conn)
        plan = conn.execute(
            "SELECT major_name, plan_count, tuition FROM admission_plan"
        ).fetchone()
        cutoff = conn.execute(
            """
            SELECT major_name, min_score, min_rank
            FROM historical_cutoff
            WHERE major_code = '01'
            """
        ).fetchone()

    assert inserted == 1
    assert plan == ("计算机科学与技术", 6, 6380)
    assert cutoff == ("计算机科学与技术", 690, 126)


def test_ingest_plan_details_skips_unmatched_non_official_groups(tmp_path, monkeypatch) -> None:
    from src.jiangsu.input import ingest

    detail_dir = tmp_path / "plan_details"
    detail_dir.mkdir()
    path = detail_dir / "plan_details_2025_physics.csv"
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "year", "subject_category", "school_code", "school_name",
                "special_group", "sg_name", "sg_info", "major_code", "major_name",
                "plan_count", "tuition", "duration",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "year": "2025",
                "subject_category": "物理类",
                "school_code": "9999",
                "school_name": "未匹配大学",
                "special_group": "9999-01",
                "sg_name": "01",
                "sg_info": "首选物理，再选不限",
                "major_code": "01",
                "major_name": "测试专业",
                "plan_count": "1",
                "tuition": "5000",
                "duration": "四年",
            }
        )

    monkeypatch.setattr(ingest, "PLAN_DETAIL_DIR", detail_dir)

    with sqlite3.connect(":memory:") as conn:
        conn.executescript(ingest.SCHEMA_PATH.read_text(encoding="utf-8"))
        inserted = ingest.ingest_plan_details(conn)
        plan_count = conn.execute("SELECT COUNT(*) FROM admission_plan").fetchone()[0]
        cutoff_count = conn.execute(
            "SELECT COUNT(*) FROM historical_cutoff WHERE major_code = '01'"
        ).fetchone()[0]

    assert inserted == 0
    assert plan_count == 0
    assert cutoff_count == 0


def test_ingest_official_group_plans_fills_missing_groups_across_years(monkeypatch) -> None:
    from src.jiangsu.input import ingest

    official_rows = [
        {
            "year": "2025",
            "subject_category": "物理类",
            "school_code": "1101",
            "school_name": "南京大学",
            "special_group": "1101-04",
            "sg_name": "04",
            "sg_info": "首选物理，再选化学",
        },
        {
            "year": "2024",
            "subject_category": "历史类",
            "school_code": "1102",
            "school_name": "东南大学",
            "special_group": "1102-01",
            "sg_name": "01",
            "sg_info": "首选历史，再选不限",
        },
        {
            "year": "2022",
            "subject_category": "物理类",
            "school_code": "1103",
            "school_name": "河海大学",
            "special_group": "1103-02",
            "sg_name": "02",
            "sg_info": "首选物理，再选不限",
        },
    ]
    monkeypatch.setattr(ingest, "_iter_official_cutoffs", lambda: iter(official_rows))

    with sqlite3.connect(":memory:") as conn:
        conn.executescript(ingest.SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute(
            """
            INSERT INTO admission_plan (
                year, subject_category, school_code, school_name,
                special_group, sg_name, sg_info, major_code, major_name
            ) VALUES (
                2025, '物理类', '1101', '南京大学',
                '1101-04', '04', '首选物理，再选化学', '01', '计算机科学与技术'
            )
            """
        )

        inserted = ingest.ingest_official_group_plans(conn, years={2025, 2024, 2023})
        rows = conn.execute(
            """
            SELECT year, subject_category, school_code, major_code, major_name
            FROM admission_plan
            WHERE major_code = '__GROUP__'
            ORDER BY year, school_code
            """
        ).fetchall()

    assert inserted == 1
    assert rows == [
        (2024, "历史类", "1102", "__GROUP__", "东南大学01专业组-首选历史再选不限")
    ]
