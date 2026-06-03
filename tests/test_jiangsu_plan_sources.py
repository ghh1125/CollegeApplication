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


def test_jszs_search_cid_finds_exact_school(monkeypatch) -> None:
    from scripts import fetch_jiangsu_jszs_plan_pages as jszs

    class Response:
        text = """
        <a href="/College/home/cid/21.html">东南大学</a>
        <a href="/College/home/cid/1285.html">东南大学成贤学院</a>
        """

        def raise_for_status(self) -> None:
            return None

    def fake_get(url, headers, timeout):  # noqa: ANN001
        assert "%E4%B8%9C%E5%8D%97%E5%A4%A7%E5%AD%A6" in url
        assert timeout == 3
        return Response()

    monkeypatch.setattr(jszs.requests, "get", fake_get)

    assert jszs.search_cid("东南大学", timeout=3) == 21


def test_jszs_plan_url_builds_undergraduate_common_plan_url() -> None:
    from scripts.fetch_jiangsu_jszs_plan_pages import plan_url

    url = plan_url(cid=5, year=2025, subject="物理")

    assert url.startswith("https://gaoxiao.jszs.com/College/plannew.html?")
    assert "cid=5" in url
    assert "yearno=2025" in url
    assert "topchoose=%E7%89%A9%E7%90%86" in url
    assert "pici=%E6%9C%AC%E7%A7%91%E6%89%B9%E6%AC%A1" in url
