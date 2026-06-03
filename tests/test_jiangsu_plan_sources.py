"""Tests for Jiangsu public admission-plan source discovery."""

from __future__ import annotations


def test_discover_related_links_finds_absolute_and_relative_attachments() -> None:
    from scripts.fetch_jiangsu_plan_sources import discover_related_links

    urls = discover_related_links(
        """
        <html><body>
          <a href="/upload/jiangsu-plan.xlsx">2025江苏招生计划附件</a>
          <a href="detail.html">江苏专业组招生计划</a>
          <a href="notice.html">普通通知</a>
        </body></html>
        """.encode("utf-8"),
        "https://example.edu/zs/index.html",
    )

    assert "https://example.edu/upload/jiangsu-plan.xlsx" in urls
    assert "https://example.edu/zs/detail.html" in urls
    assert "https://example.edu/zs/notice.html" not in urls


def test_official_school_names_sort_by_best_rank(tmp_path, monkeypatch) -> None:
    from scripts import fetch_jiangsu_plan_sources as sources

    official = tmp_path / "official"
    official.mkdir()
    (official / "cutoff_2025_physics.csv").write_text(
        "year,subject_category,school_code,school_name,special_group,sg_name,sg_info,min_score,min_rank,source_url\n"
        "2025,物理类,0002,B大学,0002-01,01,首选物理,650,500,\n"
        "2025,物理类,0001,A大学,0001-01,01,首选物理,670,100,\n"
        "2025,物理类,0003,C大学,0003-01,01,首选物理,620,2000,\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sources, "OFFICIAL_DIR", official)

    assert sources.official_school_names(2025) == ["A大学", "B大学", "C大学"]
