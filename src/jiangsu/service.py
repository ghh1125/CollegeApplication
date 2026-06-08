"""江苏应用服务层：业务逻辑都在这里，ui/jiangsu_page.py 只负责展示。

江苏 3+1+2：首选物理/历史（分两个位次池）+ 再选 2 门；志愿单位是院校专业组（40 个）。
"""

from __future__ import annotations

import json
import re
from typing import Any

from db import get_conn
from src.common.input.llm import REGION_EXPANSIONS
from src.jiangsu.config import PROVINCE_CONFIG
from src.jiangsu.input.profile import (
    FIRST_CHOICE_SUBJECTS,
    RESELECT_SUBJECTS,
    Constraints,
    CityPreference,
    MajorPreference,
    Preferences,
    SchoolPreference,
    StudentProfile,
    _SUBJECT_ALIASES,
)
from src.jiangsu.input.filter import (
    filter_by_city,
    filter_by_constraints,
    filter_by_school_level,
    filter_by_subject,
    resolve_school_city,
)
from src.jiangsu.allocation.recommend import build_recommendations

YEARS = (2025, 2024, 2023)
PRIORITIES = ("学校优先", "城市优先", "专业优先")
RISKS = ("激进", "均衡", "保守")
RANK_MAX = 400_000


def _norm_subject(s: str) -> str:
    return _SUBJECT_ALIASES.get(s.strip(), s.strip())


# ─── 表单 → 学生画像 ──────────────────────────────────────────────────────────

def build_profile(form: dict) -> StudentProfile:
    return StudentProfile(
        rank=int(form["rank"]),
        first_choice=form["first_choice"],
        selected_subjects=list(form.get("selected_subjects") or []),
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
    if form.get("first_choice") not in FIRST_CHOICE_SUBJECTS:
        return "请选择「首选科目」（物理 或 历史）。"
    if form.get("main_priority") not in PRIORITIES:
        return "请选择「主排序」（学校优先 / 城市优先 / 专业优先）。"
    if len(form.get("selected_subjects") or []) != 2:
        return "请选择恰好 2 门再选科目。"
    return None


TARGET_YEAR = 2026                  # 目标填报年：候选来自当年招生目录
REFERENCE_YEARS = (2025, 2024, 2023)  # 历史分数线/位次参考年


# ─── 目标年 + 历史参考推荐编排 ───────────────────────────────────────────────

def _build_recommendation_for_plan_year(conn: Any, form: dict, profile, plan_year: int, history_year: int) -> dict:
    main_priority = form["main_priority"]
    majors = list(form.get("preferred_majors") or [])
    levels = list(form.get("school_levels") or [])
    cities = list(form.get("preferred_cities") or [])
    risk = form.get("risk_preference", "均衡")

    eligible, _ = filter_by_subject(profile, year=plan_year, conn=conn)
    final, _ = filter_by_constraints(eligible, profile)
    if levels:
        final, _ = filter_by_school_level(final, levels)
    if cities and main_priority == "城市优先":
        final, _ = filter_by_city(final, cities)
    reco = build_recommendations(
        final, profile, main_priority=main_priority,
        preferred_majors=majors, preferred_categories=[], preferred_schools=[],
        preferred_cities=cities or None, risk_preference=risk,
        year=history_year, conn=conn,
    )
    reco["_pool"] = final
    return reco


def _available_plan_years(conn: Any) -> set[int]:
    rows = conn.execute("SELECT DISTINCT year FROM admission_plan").fetchall()
    return {int(r[0]) for r in rows if r[0] is not None}


def recommend_target_and_references(form: dict) -> dict:
    """目标年推荐 + 历史参考；2026 目录未导入时自动回退到最新有数据年（2025）。"""
    profile = build_profile(form)
    with get_conn("jiangsu") as conn:
        plan_years = _available_plan_years(conn)
        effective_target = TARGET_YEAR if TARGET_YEAR in plan_years else (
            max(plan_years) if plan_years else TARGET_YEAR)
        is_fallback = effective_target != TARGET_YEAR
        history_year = effective_target if effective_target in REFERENCE_YEARS else REFERENCE_YEARS[0]

        target = _build_recommendation_for_plan_year(
            conn, form, profile, plan_year=effective_target, history_year=history_year)
        target["_target_year"] = effective_target
        target["_is_fallback"] = is_fallback
        target["_tab_label"] = (
            f"{TARGET_YEAR} 目标志愿（招生目录 + {REFERENCE_YEARS[0]} 历史线）"
            if not is_fallback else
            f"{effective_target} 推荐（{TARGET_YEAR} 目录未发布，暂用 {effective_target} 目录）"
        )
        ref_years = [y for y in REFERENCE_YEARS if y < effective_target]
        references = {
            year: _build_recommendation_for_plan_year(conn, form, profile, plan_year=year, history_year=year)
            for year in ref_years
        }
    return {"target": target, "references": references}


def recommend_for_years(form: dict, years: tuple[int, ...] = YEARS) -> dict[int, dict]:
    """（兼容保留）各年独立推荐。"""
    profile = build_profile(form)
    recos: dict[int, dict] = {}
    with get_conn("jiangsu") as conn:
        for year in years:
            recos[year] = _build_recommendation_for_plan_year(conn, form, profile, plan_year=year, history_year=year)
    return recos


# ─── 推荐结果 → 可展示的行 ───────────────────────────────────────────────────

def member_majors(group: dict) -> list[str]:
    names: list[str] = []
    for m in group.get("_members", []):
        if m.get("major_code") == "__GROUP__":
            continue
        name = (m.get("major_name") or "").strip()
        if name and "合计" not in name:
            names.append(name)
    seen: set[str] = set()
    return [n for n in names if not (n in seen or seen.add(n))]


def history_score_rank_columns(group: dict, years: tuple[int, ...] = REFERENCE_YEARS) -> dict:
    by_year = {int(h["year"]): h for h in group.get("history", []) if h.get("year")}
    cols: dict = {}
    for year in years:
        cols[f"{year}分数线"] = by_year.get(year, {}).get("min_score") or ""
        cols[f"{year}位次"] = by_year.get(year, {}).get("min_rank") or ""
    return cols


def group_rows(groups: list[dict]) -> list[dict]:
    rows = []
    for idx, g in enumerate(groups, start=1):
        gi = g.get("gap_info", {})
        members = member_majors(g)
        inner = "、".join(members[:8]) + ("…" if len(members) > 8 else "") if members else "组内专业待补充，以招生计划为准"
        sg_info = g.get("sg_info", "") or ""
        rows.append({
            "序号": g.get("volunteer_no") or idx,
            "层级": gi.get("tier", ""),
            "学校": g.get("school_name", ""),
            "城市": g.get("school_city") or resolve_school_city(g.get("school_name", "")),
            "专业组": f"{g.get('sg_name', '')}组",
            "再选要求": sg_info.split("再选", 1)[-1] if "再选" in sg_info else sg_info,
            "专业匹配": g.get("_major_tag", ""),
            "参考位次": gi.get("weighted_avg"),
            "gap": gi.get("gap"),
            **history_score_rank_columns(g),
            "组内专业": inner,
        })
    return rows


def member_trend_rows(group: dict) -> list[dict]:
    rows = []
    seen: set[str] = set()
    for m in group.get("_members", []):
        if m.get("major_code") == "__GROUP__":
            continue
        name = (m.get("major_name") or "").strip()
        if not name or "合计" in name or name in seen:
            continue
        seen.add(name)
        t = m.get("_trend", {}) or {}
        hist = {int(h["year"]): h for h in m.get("history", []) if h.get("year")}

        def _rank(y: int) -> int | None:
            return t.get(y) or hist.get(y, {}).get("min_rank")

        def _score(y: int) -> int | None:
            return hist.get(y, {}).get("min_score")

        seq = [_rank(y) for y in (2023, 2024, 2025) if _rank(y)]
        arrow = ""
        if len(seq) >= 2:
            arrow = "↓更难" if seq[-1] < seq[0] else ("↑更易" if seq[-1] > seq[0] else "→持平")
        rows.append({"专业": name,
                     "2025分数线": _score(2025), "2025位次": _rank(2025),
                     "2024分数线": _score(2024), "2024位次": _rank(2024),
                     "2023分数线": _score(2023), "2023位次": _rank(2023),
                     "趋势": arrow})
    return rows


def pool_rows(programs: list[dict]) -> list[dict]:
    """候选池：通过筛选的全部专业（含所属院校专业组）。"""
    rows = []
    for p in programs:
        sg_info = p.get("sg_info", "") or ""
        rows.append({
            "学校": p.get("school_name", ""),
            "城市": resolve_school_city(p.get("school_name", "")),
            "专业组": f"{p.get('sg_name', '')}组",
            "专业": p.get("major_name", ""),
            "再选要求": sg_info.split("再选", 1)[-1] if "再选" in sg_info else sg_info,
        })
    return rows


def group_label(g: dict) -> str:
    return f"{g.get('volunteer_no')}. {g.get('school_name')} {g.get('sg_name', '')}组"


def group_as_volunteer(g: dict) -> dict:
    members = member_majors(g)
    return {
        "volunteer_no": g.get("volunteer_no"),
        "school_name": g.get("school_name", ""),
        "school_city": g.get("school_city", ""),
        "major_name": f"{g.get('sg_name','')}组（{('、'.join(members[:4])) if members else '组内专业待补充'}）",
        "gap_info": g.get("gap_info", {}),
    }


def advisor_ctx(reco: dict) -> dict:
    return {
        "volunteers": [group_as_volunteer(g) for g in reco.get("volunteers", [])],
        "stats": reco.get("stats", {}),
    }


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
    """AI 参数 → 江苏表单字段（首选物理/历史、再选2门、城市展开）。"""
    fill: dict[str, Any] = {}
    if params.get("rank"):
        try:
            fill["rank"] = max(1, min(RANK_MAX, int(params["rank"])))
        except (ValueError, TypeError):
            pass
    if params.get("first_choice") in FIRST_CHOICE_SUBJECTS:
        fill["first_choice"] = params["first_choice"]
    if params.get("selected_subjects"):
        norm = [_norm_subject(s) for s in params["selected_subjects"]]
        subs = [s for s in norm if s in RESELECT_SUBJECTS][:2]
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
