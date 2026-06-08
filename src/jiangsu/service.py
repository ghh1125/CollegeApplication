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


# ─── 三年推荐编排 ─────────────────────────────────────────────────────────────

def recommend_for_years(form: dict, years: tuple[int, ...] = YEARS) -> dict[int, dict]:
    profile = build_profile(form)
    main_priority = form["main_priority"]
    majors = list(form.get("preferred_majors") or [])
    levels = list(form.get("school_levels") or [])
    cities = list(form.get("preferred_cities") or [])
    risk = form.get("risk_preference", "均衡")

    recos: dict[int, dict] = {}
    with get_conn("jiangsu") as conn:
        for year in years:
            eligible, _ = filter_by_subject(profile, year=year, conn=conn)
            final, _ = filter_by_constraints(eligible, profile)
            if levels:
                final, _ = filter_by_school_level(final, levels)
            if cities and main_priority == "城市优先":
                final, _ = filter_by_city(final, cities)
            recos[year] = build_recommendations(
                final, profile, main_priority=main_priority,
                preferred_majors=majors, preferred_categories=[], preferred_schools=[],
                preferred_cities=cities or None, risk_preference=risk,
                year=year, conn=conn,
            )
            if year == years[0]:
                recos[year]["_pool"] = final  # 候选池：通过筛选的全部专业（含所属组）
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
            "投档位次": gi.get("weighted_avg"),
            "gap": gi.get("gap"),
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
        seq = [t.get(y) for y in (2023, 2024, 2025) if t.get(y)]
        arrow = ""
        if len(seq) >= 2:
            arrow = "↓更难" if seq[-1] < seq[0] else ("↑更易" if seq[-1] > seq[0] else "→持平")
        rows.append({"专业": name, "2025位次": t.get(2025), "2024位次": t.get(2024),
                     "2023位次": t.get(2023), "趋势": arrow})
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
