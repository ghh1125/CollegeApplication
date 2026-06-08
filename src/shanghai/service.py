"""上海应用服务层：业务逻辑都在这里，ui/shanghai_page.py 只负责展示。

页面调用本模块完成：构建学生画像、跑三年推荐、把推荐结果转成可展示的行、
解析 AI 提取的参数并映射回表单字段。页面本身不写业务逻辑。
"""

from __future__ import annotations

import json
import re
from typing import Any

from db import get_conn
from src.common.input.llm import REGION_EXPANSIONS
from src.shanghai.config import PROVINCE_CONFIG
from src.shanghai.input.profile import (
    SELECT_SUBJECTS,
    Constraints,
    CityPreference,
    MajorPreference,
    Preferences,
    SchoolPreference,
    StudentProfile,
    normalize_subject,
)
from src.shanghai.input.filter import (
    filter_by_city,
    filter_by_constraints,
    filter_by_school_level,
    filter_by_subject,
    resolve_school_city,
)
from src.shanghai.allocation.recommend import build_recommendations

TARGET_YEAR = 2026                  # 目标填报年份：候选来自当年招生目录
REFERENCE_YEARS = (2025, 2024, 2023)  # 历史分数线/位次参考年份
YEARS = REFERENCE_YEARS             # 各历史年独立成块；2025 为最新历史参考
PRIORITIES = ("学校优先", "城市优先", "专业优先")
RISKS = ("激进", "均衡", "保守")
RANK_MAX = 200_000


# ─── 表单 → 学生画像 ──────────────────────────────────────────────────────────

def build_profile(form: dict) -> StudentProfile:
    """从表单字典构建 StudentProfile（校验在 StudentProfile 内完成）。"""
    return StudentProfile(
        rank=int(form["rank"]),
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
    """返回错误提示文案；通过校验返回 None。"""
    if form.get("main_priority") not in PRIORITIES:
        return "请选择「主排序」（学校优先 / 城市优先 / 专业优先）。"
    if len(form.get("selected_subjects") or []) != 3:
        return "请选择恰好 3 门选考科目。"
    return None


# ─── 目标年 + 历史参考推荐编排（页面只拿结果）──────────────────────────────

def target_tab_label() -> str:
    return f"{TARGET_YEAR} 目标志愿（招生目录 + {REFERENCE_YEARS[0]} 历史线）"


def _build_recommendation_for_plan_year(
    conn: Any,
    form: dict,
    profile: StudentProfile,
    plan_year: int,
    history_year: int,
) -> dict:
    """Build recommendations from plan-year candidates and historical cutoffs."""
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
        year=history_year, use_group_history=(plan_year == history_year), conn=conn,
    )
    reco["_pool"] = final
    reco["_plan_year"] = plan_year
    reco["_history_years"] = REFERENCE_YEARS
    reco["_history_source_year"] = history_year
    if plan_year == TARGET_YEAR and not final:
        reco["_missing_plan_year"] = TARGET_YEAR
    return reco


def _available_plan_years(conn: Any) -> set[int]:
    rows = conn.execute("SELECT DISTINCT year FROM admission_plan").fetchall()
    return {int(r[0]) for r in rows if r[0] is not None}


def recommend_target_and_references(form: dict) -> dict:
    """返回「目标年推荐 + 历史参考」。

    目标年优先用 TARGET_YEAR(2026)；若 2026 招生目录尚未导入，则**自动回退**到
    最新有数据的年份（如 2025）当主推荐，避免主标签页空着。
    """
    profile = build_profile(form)
    with get_conn("shanghai") as conn:
        plan_years = _available_plan_years(conn)
        effective_target = TARGET_YEAR if TARGET_YEAR in plan_years else (
            max(plan_years) if plan_years else TARGET_YEAR)
        is_fallback = effective_target != TARGET_YEAR
        history_year = effective_target if effective_target in REFERENCE_YEARS else REFERENCE_YEARS[0]

        target = _build_recommendation_for_plan_year(
            conn, form, profile, plan_year=effective_target, history_year=history_year
        )
        target["_target_year"] = effective_target
        target["_is_fallback"] = is_fallback
        target["_tab_label"] = (
            f"{TARGET_YEAR} 目标志愿（招生目录 + {REFERENCE_YEARS[0]} 历史线）"
            if not is_fallback else
            f"{effective_target} 推荐（{TARGET_YEAR} 目录未发布，暂用 {effective_target} 目录）"
        )

        # 参考年：低于有效目标年的历史年（避免与目标年重复）
        ref_years = [y for y in REFERENCE_YEARS if y < effective_target]
        references = {
            year: _build_recommendation_for_plan_year(conn, form, profile, plan_year=year, history_year=year)
            for year in ref_years
        }
    return {"target": target, "references": references}


def recommend_for_years(form: dict, years: tuple[int, ...] = YEARS) -> dict[int, dict]:
    """跑各年独立推荐，返回 {year: recommendation}。上海专业组逐年重排、不跨年加权。"""
    profile = build_profile(form)

    recos: dict[int, dict] = {}
    with get_conn("shanghai") as conn:
        for year in years:
            recos[year] = _build_recommendation_for_plan_year(
                conn, form, profile, plan_year=year, history_year=year
            )
    return recos


# ─── 推荐结果 → 可展示的行（页面只 wrap 成表格）──────────────────────────────

def member_majors(group: dict) -> list[str]:
    """组内真实专业（去掉 __GROUP__ 兜底行和合计行，去重保序）。"""
    names: list[str] = []
    for m in group.get("_members", []):
        if m.get("major_code") == "__GROUP__":
            continue
        name = (m.get("major_name") or "").strip()
        if name and "合计" not in name:
            names.append(name)
    seen: set[str] = set()
    return [n for n in names if not (n in seen or seen.add(n))]


def history_score_rank_columns(group: dict, years: tuple[int, ...] = REFERENCE_YEARS) -> dict[str, int | str | None]:
    by_year: dict[int, dict] = {}
    for h in group.get("history", []):
        y = h.get("year")
        if y:
            by_year[int(y)] = h
    columns: dict[str, int | str | None] = {}
    for year in years:
        columns[f"{year}分数线"] = by_year.get(year, {}).get("min_score") or ""
        columns[f"{year}位次"] = by_year.get(year, {}).get("min_rank") or ""
    return columns


def group_rows(groups: list[dict]) -> list[dict]:
    """把推荐专业组转成表格行。"""
    rows = []
    for idx, g in enumerate(groups, start=1):
        gi = g.get("gap_info", {})
        members = member_majors(g)
        inner = "、".join(members[:8]) + ("…" if len(members) > 8 else "") if members else "组内专业待补充，以招生计划为准"
        rows.append({
            "序号": g.get("volunteer_no") or idx,
            "层级": gi.get("tier", ""),
            "学校": g.get("school_name", ""),
            "城市": g.get("school_city") or resolve_school_city(g.get("school_name", "")),
            "专业组": f"{g.get('sg_name', '')}组",
            "选科要求": g.get("sg_info", "") or "不限",
            "专业匹配": g.get("_major_tag", ""),
            "参考位次": gi.get("weighted_avg"),
            "gap": gi.get("gap"),
            **history_score_rank_columns(g),
            "组内专业": inner,
        })
    return rows


def member_trend_rows(group: dict) -> list[dict]:
    """组内每个专业的三年位次趋势行。"""
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
        history_by_year = {
            int(h["year"]): h for h in m.get("history", [])
            if h.get("year")
        }

        def _rank(year: int) -> int | None:
            return t.get(year) or history_by_year.get(year, {}).get("min_rank")

        def _score(year: int) -> int | None:
            return history_by_year.get(year, {}).get("min_score")

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
    return [{
        "学校": p.get("school_name", ""),
        "城市": resolve_school_city(p.get("school_name", "")),
        "专业组": f"{p.get('sg_name', '')}组",
        "专业": p.get("major_name", ""),
        "选科要求": p.get("sg_info", "") or "不限",
    } for p in programs]


def group_label(g: dict) -> str:
    return f"{g.get('volunteer_no')}. {g.get('school_name')} {g.get('sg_name', '')}组"


def group_as_volunteer(g: dict) -> dict:
    """把专业组整理成 explain_volunteer / report 需要的形状。"""
    members = member_majors(g)
    return {
        "volunteer_no": g.get("volunteer_no"),
        "school_name": g.get("school_name", ""),
        "school_city": g.get("school_city", ""),
        "major_name": f"{g.get('sg_name','')}组（{('、'.join(members[:4])) if members else '组内专业待补充'}）",
        "gap_info": g.get("gap_info", {}),
    }


def advisor_ctx(reco: dict) -> dict:
    """供 AI 解读的上下文（用主推荐方案）。"""
    return {
        "volunteers": [group_as_volunteer(g) for g in reco.get("volunteers", [])],
        "stats": reco.get("stats", {}),
    }


# ─── AI 提取参数 → 表单字段 ──────────────────────────────────────────────────

def parse_advisor_params(text: str) -> dict | None:
    """从 AI 回复里抽出 ```json {...}``` 参数块。"""
    m = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except Exception:  # noqa: BLE001
        return None


def params_to_form(params: dict) -> dict:
    """把 AI 提取的参数规范化成表单字段值（上海口径：6选3、城市展开）。"""
    fill: dict[str, Any] = {}
    if params.get("rank"):
        try:
            fill["rank"] = max(1, min(RANK_MAX, int(params["rank"])))
        except (ValueError, TypeError):
            pass
    if params.get("selected_subjects"):
        norm = [normalize_subject(s) for s in params["selected_subjects"]]
        subs = [s for s in norm if s in SELECT_SUBJECTS][:3]
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
