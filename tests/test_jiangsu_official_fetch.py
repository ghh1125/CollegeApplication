"""Tests for Jiangsu official-data parsing helpers."""

from __future__ import annotations


def test_parse_score_rank_text_handles_side_by_side_triples() -> None:
    from scripts.fetch_jiangsu_official import parse_score_rank_text

    rows = parse_score_rank_text(
        "分数 人数 累计人数\n700 3 25 699 4 29\n698 10 39",
        year=2025,
        subject_category="物理类",
        source_url="https://example.test/rank.pdf",
    )

    by_score = {row["score"]: row for row in rows}
    assert by_score[700]["same_score_count"] == 3
    assert by_score[700]["cumulative_rank"] == 25
    assert by_score[699]["cumulative_rank"] == 29
    assert by_score[698]["cumulative_rank"] == 39


def test_parse_cutoff_text_extracts_school_group_and_score() -> None:
    from scripts.fetch_jiangsu_official import parse_cutoff_text

    rows = parse_cutoff_text(
        "院校代号 院校、专业组（再选科目要求） 投档最低分\n"
        "1101 南京大学04专业组(不限) 690 语文数学总分 270\n"
        "1102 东南大学05专业组(化学) 660 语文数学总分 255\n",
        year=2025,
        subject_category="物理类",
        source_url="https://example.test/cutoff.pdf",
    )

    assert rows[0]["school_code"] == "1101"
    assert rows[0]["school_name"] == "南京大学"
    assert rows[0]["special_group"] == "1101-04"
    assert rows[0]["sg_info"] == "首选物理，再选不限"
    assert rows[0]["min_score"] == 690
    assert rows[1]["school_name"] == "东南大学"
    assert rows[1]["sg_info"] == "首选物理，再选化学"


def test_apply_rank_lookup_fills_min_rank_from_score() -> None:
    from scripts.fetch_jiangsu_official import apply_rank_lookup

    merged = apply_rank_lookup(
        [{"min_score": 660, "school_name": "东南大学"}],
        [{"score": 660, "cumulative_rank": 9458}],
    )

    assert merged[0]["min_rank"] == 9458


def test_parse_cutoff_text_handles_stacked_pdf_rows() -> None:
    from scripts.fetch_jiangsu_official import parse_cutoff_text

    rows = parse_cutoff_text(
        "1106 南京信息工程大学09专业组(化学)\n"
        "1106 南京信息工程大学10专业组(化学)\n"
        "1106 南京信息工程大学11专业组(化学)\n"
        "                                              627\n"
        "                                              606\n"
        "                                              593\n"
        "                                                    233\n",
        year=2025,
        subject_category="物理类",
        source_url="https://example.test/cutoff.pdf",
    )

    by_group = {row["sg_name"]: row for row in rows}
    assert by_group["09"]["min_score"] == 627
    assert by_group["10"]["min_score"] == 606
    assert by_group["11"]["min_score"] == 593


def test_lx91_range_rows_can_be_used_for_top_score_lookup(monkeypatch) -> None:
    from scripts import fetch_jiangsu_official as official

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "data": {
                    "list": [
                        {"score": "683-750", "num": "126", "total": 126},
                        {"score": "682", "num": "18", "total": 144},
                    ]
                }
            }

    def fake_post(*_args, **_kwargs):
        return FakeResponse()

    monkeypatch.setattr(official.requests, "post", fake_post)

    rows = official.fetch_lx91_score_rank(year=2025, subject_category="物理类")
    by_score = {row["score"]: row for row in rows}

    assert by_score[750]["cumulative_rank"] == 126
    assert by_score[690]["cumulative_rank"] == 126
    assert by_score[683]["cumulative_rank"] == 126
    assert by_score[682]["cumulative_rank"] == 144
