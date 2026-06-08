"""浙江应用服务层：业务逻辑都在这里，ui/zhejiang_page.py 只负责展示。

浙江 3+3（7选3）；志愿单位是「学校+专业」，最多 80 个（不是院校专业组）。
推荐返回单份结果 {volunteers, reserve, stats}，按 推荐/候选/备选 展示。
"""

from __future__ import annotations

import json
import re
from typing import Any

from db import get_conn
from src.common.input.llm import REGION_EXPANSIONS
from src.zhejiang.config import PROVINCE_CONFIG
from src.zhejiang.input.profile import (
    VALID_SUBJECTS,
    Constraints,
    CityPreference,
    MajorPreference,
    Preferences,
    SchoolPreference,
    StudentProfile,
    _SUBJECT_ALIASES,
)
from src.zhejiang.input.filter import (
    filter_by_city,
    filter_by_constraints,
    filter_by_school_level,
    filter_by_subject,
    resolve_school_city,
)
from src.zhejiang.allocation.recommend import build_recommendations
from ui.form_helpers import format_sort_reason_for_display

PRIORITIES = ("学校优先", "城市优先", "专业优先")
RISKS = ("激进", "均衡", "保守")
RANK_MAX = 400_000


def _norm_subject(s: str) -> str:
    return _SUBJECT_ALIASES.get(s.strip(), s.strip())


def _fmt_req(req_json: str | None) -> str:
    try:
        req = json.loads(req_json or "{}")
    except Exception:  # noqa: BLE001
        return "不限"
    t = req.get("type", "NONE")
    subs = req.get("subjects", [])
    if t == "NONE":
        return "不限"
    if t == "UNKNOWN":
        return "❓"
    if t == "ALL_REQUIRED":
        return " + ".join(subs) + "（均须）"
    if t == "ANY_ONE":
        return " / ".join(subs) + "（任一）"
    return " / ".join(subs) or "自定义"


# ─── 表单 → 学生画像 ──────────────────────────────────────────────────────────

def build_profile(form: dict) -> StudentProfile:
    return StudentProfile(
        rank=int(form["rank"]),
        total_score=int(form.get("total_score") or 600),
        selected_subjects=list(form.get("selected_subjects") or []),
        priority_mode=form.get("main_priority", "均衡模式"),
        risk_preference=form.get("risk_preference", "均衡"),
        preferences=Preferences(
            cities=CityPreference(
                preferred=list(form.get("preferred_cities") or []),
                excluded_regions=list(form.get("excluded_regions") or []),
            ),
            majors=MajorPreference(preferred_majors=list(form.get("preferred_majors") or [])),
            schools=SchoolPreference(preferred_levels=list(form.get("school_levels") or [])),
        ),
        constraints=Constraints(accept_private=bool(form.get("accept_private", True))),
    )


def validate_form(form: dict) -> str | None:
    if form.get("main_priority") not in PRIORITIES:
        return "请选择「主排序」（学校优先 / 城市优先 / 专业优先）。"
    if len(form.get("selected_subjects") or []) != 3:
        return "请选择恰好 3 门选考科目。"
    return None


# ─── 推荐（单份结果）─────────────────────────────────────────────────────────

def recommend(form: dict) -> dict:
    from src.zhejiang.input.profile import MajorPreference as _MP  # noqa: F401
    profile = build_profile(form)
    main_priority = form["main_priority"]
    majors = list(form.get("preferred_majors") or [])
    levels = list(form.get("school_levels") or [])
    cities = list(form.get("preferred_cities") or [])
    risk = form.get("risk_preference", "均衡")

    with get_conn("zhejiang") as conn:
        eligible, _ = filter_by_subject(profile, year=2025, conn=conn)
        final, _ = filter_by_constraints(eligible, profile)
        if levels:
            final, _ = filter_by_school_level(final, levels)
        if cities and main_priority == "城市优先":
            final, _ = filter_by_city(final, cities)
        reco = build_recommendations(
            final, profile, main_priority=main_priority,
            preferred_majors=majors, preferred_categories=[], preferred_schools=[],
            preferred_cities=cities or None, risk_preference=risk,
            total=PROVINCE_CONFIG.total_volunteers, conn=conn,
        )
    reco["_pool"] = final  # 候选池：通过筛选的全部学校+专业
    return reco


# ─── 结果 → 可展示的行 ───────────────────────────────────────────────────────

_REF_YEARS = (2025, 2024, 2023)


def _history_score_rank_columns(program: dict) -> dict:
    """各年分数线 + 位次（与江苏/上海一致的双列展示）。"""
    by_year = {int(h["year"]): h for h in program.get("history", []) if h.get("year")}
    cols: dict = {}
    for year in _REF_YEARS:
        cols[f"{year}分数线"] = by_year.get(year, {}).get("min_score") or ""
        cols[f"{year}位次"] = by_year.get(year, {}).get("min_rank") or ""
    return cols


def volunteer_rows(programs: list[dict], main_priority: str) -> list[dict]:
    rows = []
    for idx, p in enumerate(programs, start=1):
        gi = p.get("gap_info") or {}
        rows.append({
            "序号": p.get("volunteer_no") or idx,
            "层级": gi.get("tier", ""),
            "学校": p.get("school_name", ""),
            "城市": p.get("school_city") or resolve_school_city(p.get("school_name", "")),
            "专业": p.get("major_name", ""),
            "专业匹配": p.get("_major_tag", ""),
            "均值位次": gi.get("weighted_avg"),
            "gap": gi.get("gap"),
            **_history_score_rank_columns(p),
            "排序理由": format_sort_reason_for_display(p, main_priority),
            "历史年数": gi.get("data_years"),
            "选科要求": _fmt_req(p.get("subject_requirement_json")),
            "⚠": "  ".join(p.get("_warnings") or []),
        })
    return rows


def candidate_rows(programs: list[dict]) -> list[dict]:
    return [{
        "学校": p.get("school_name", ""),
        "城市": resolve_school_city(p.get("school_name", "")),
        "专业": p.get("major_name", ""),
        "选科要求": _fmt_req(p.get("subject_requirement_json")),
        "⚠": "  ".join(p.get("_warnings") or []),
    } for p in programs]


def advisor_ctx(reco: dict) -> dict:
    """浙江顾问直接用推荐结果（已是 学校·专业 级）。"""
    return reco


# ─── AI 提取参数 → 表单字段 ──────────────────────────────────────────────────

def parse_advisor_params(text: str) -> dict | None:
    m = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except Exception:  # noqa: BLE001
        return None


def params_to_form(params: dict) -> dict:
    fill: dict[str, Any] = {}
    if params.get("rank"):
        try:
            fill["rank"] = max(1, min(RANK_MAX, int(params["rank"])))
        except (ValueError, TypeError):
            pass
    if params.get("total_score"):
        try:
            fill["total_score"] = max(200, min(750, int(params["total_score"])))
        except (ValueError, TypeError):
            pass
    if params.get("selected_subjects"):
        norm = [_norm_subject(s) for s in params["selected_subjects"]]
        subs = [s for s in norm if s in VALID_SUBJECTS][:3]
        if subs:
            fill["selected_subjects"] = subs
    if params.get("main_priority") in PRIORITIES:
        fill["main_priority"] = params["main_priority"]
    if params.get("risk_preference") in RISKS:
        fill["risk_preference"] = params["risk_preference"]
    if params.get("preferred_majors"):
        fill["preferred_majors"] = list(params["preferred_majors"])
        fill.setdefault("main_priority", "专业优先")
    if params.get("preferred_cities"):
        expanded: list[str] = []
        for c in params["preferred_cities"]:
            expanded.extend(REGION_EXPANSIONS.get(c, [c]))
        fill["preferred_cities"] = list(dict.fromkeys(expanded))
    return fill
