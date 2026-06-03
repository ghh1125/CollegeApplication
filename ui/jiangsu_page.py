"""Jiangsu (3+1+2 院校专业组) Streamlit page.

Rendered by main.py when the selected province is 江苏. Kept separate from the
Zhejiang flow because Jiangsu's volunteer unit is the 院校专业组 (not 学校+专业):
we recommend 40 院校专业组 and, where 招生计划 detail exists, list the majors inside.

MVP scope (per available data):
  - driven by official 院校专业组 投档线 (2023–2025, 物理类/历史类)
  - group-inner majors shown when available, else "组内专业待补充"
  - 专业偏好 = soft ordering / hint only (NOT a hard filter), since most groups
    do not yet have inner-major detail.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from db import get_conn
from src.jiangsu.input.profile import (
    CityPreference,
    MajorPreference,
    Preferences,
    SchoolPreference,
    StudentProfile,
)
from src.jiangsu.input.filter import (
    filter_by_city,
    filter_by_constraints,
    filter_by_school_level,
    filter_by_subject,
    resolve_school_city,
)
from src.jiangsu.allocation.recommend import build_recommendations, history_rank_columns

FIRST_CHOICES = ["物理", "历史"]
RESELECT = ["化学", "生物", "思想政治", "地理"]
PRIORITIES = ["请选择…", "学校优先", "城市优先", "专业优先"]


def _member_majors(group: dict) -> list[str]:
    """Real inner majors of a group (excluding the synthetic __GROUP__ threshold row)."""
    names: list[str] = []
    for m in group.get("_members", []):
        if m.get("major_code") == "__GROUP__":
            continue
        name = (m.get("major_name") or "").strip()
        if name and "合计" not in name:
            names.append(name)
    # de-dup preserving order
    seen: set[str] = set()
    return [n for n in names if not (n in seen or seen.add(n))]


def _groups_df(groups: list[dict]) -> pd.DataFrame:
    rows = []
    for idx, g in enumerate(groups, start=1):
        gi = g.get("gap_info", {})
        members = _member_majors(g)
        inner = "、".join(members[:8]) + ("…" if len(members) > 8 else "") if members else "组内专业待补充，以招生计划为准"
        rows.append({
            "序号": g.get("volunteer_no") or idx,
            "层级": gi.get("tier", ""),
            "学校": g.get("school_name", ""),
            "城市": g.get("school_city") or resolve_school_city(g.get("school_name", "")),
            "专业组": f"{g.get('sg_name', '')}组",
            "再选要求": (g.get("sg_info", "") or "").split("再选", 1)[-1] if "再选" in (g.get("sg_info") or "") else (g.get("sg_info") or ""),
            "专业匹配": g.get("_major_tag", ""),
            **history_rank_columns(g),
            "投档位次": gi.get("weighted_avg"),
            "组内专业": inner,
        })
    return pd.DataFrame(rows)


def render(province: str = "jiangsu") -> None:
    st.title("高考志愿推荐系统 · 江苏")
    if st.button("← 切换省份", key="js_back"):
        for k in list(st.session_state.keys()):
            if k != "js_back":
                del st.session_state[k]
        st.rerun()

    st.info(
        "**江苏为「院校专业组」模式**：本科批按 40 个院校专业组填报，投档检索的是专业组而非单个专业。"
        "本推荐基于江苏省教育考试院官方公布的 2023–2025 院校专业组投档线（物理类/历史类分列）。"
        "有招生计划明细的组会展开组内专业；暂无明细的组标注「待补充」，专业偏好仅作排序提示、不做硬性筛选。"
        "**最终填报请以《江苏招生考试》招生计划专刊和省考试院官方信息为准。**"
    )

    # ─── 表单 ─────────────────────────────────────────────────────────────────
    with st.sidebar:
        st.header("📋 考生信息（江苏 3+1+2）")
        rank = st.number_input("首选科类内全省位次", 1, 400_000, value=8000, step=100, key="js_rank")
        first_choice = st.radio("首选科目", FIRST_CHOICES, horizontal=True, key="js_first",
                                help="物理 / 历史 二选一，决定录取科类和位次池")
        reselect = st.multiselect("再选科目（选 2 门）", RESELECT, max_selections=2, key="js_reselect",
                                  help="从 化学/生物/思想政治/地理 选 2 门")
        st.divider()
        main_priority = st.selectbox("主排序", PRIORITIES, index=0, key="js_priority")
        st.caption("江苏组内专业明细尚不完整，「专业优先」目前作弱排序提示，不保证组内含目标专业。")
        risk_preference = st.selectbox("风险偏好", ["激进", "均衡", "保守"], index=1, key="js_risk")

        st.divider()
        st.markdown("**可选偏好**")
        preferred_majors = [s.strip() for s in st.text_input(
            "想读的专业方向（弱提示，逗号分隔）", key="js_majors",
            placeholder="如 计算机, 金融").split(",") if s.strip()]
        school_levels = st.multiselect("学校层次", ["985", "211", "双一流"], key="js_levels")
        preferred_cities = [c.strip() for c in st.text_input(
            "偏好城市（逗号分隔）", key="js_cities", placeholder="如 南京, 苏州").split(",") if c.strip()]

    # ─── 校验 ─────────────────────────────────────────────────────────────────
    if main_priority == "请选择…":
        st.warning("请在左侧选择「主排序」后查看推荐。"); st.stop()
    if len(reselect) != 2:
        st.warning("请在左侧选择恰好 2 门再选科目。"); st.stop()

    try:
        profile = StudentProfile(
            rank=int(rank), first_choice=first_choice, selected_subjects=reselect,
            risk_preference=risk_preference,
            preferences=Preferences(
                cities=CityPreference(preferred=preferred_cities),
                majors=MajorPreference(preferred_majors=preferred_majors),
                schools=SchoolPreference(preferred_levels=school_levels),
            ),
        )
    except Exception as e:  # noqa: BLE001
        st.error(f"输入有误：{e}"); st.stop()

    # ─── 管线 ─────────────────────────────────────────────────────────────────
    with get_conn("jiangsu") as conn:
        eligible, _ = filter_by_subject(profile, year=2025, conn=conn)
        final, _ = filter_by_constraints(eligible, profile)
        if school_levels:
            final, _ = filter_by_school_level(final, school_levels)
        # 城市偏好作硬过滤仅在城市优先时；否则留给排序
        if preferred_cities and main_priority == "城市优先":
            final, _ = filter_by_city(final, preferred_cities)
        reco = build_recommendations(
            final, profile, main_priority=main_priority,
            preferred_majors=preferred_majors, preferred_categories=[], preferred_schools=[],
            preferred_cities=preferred_cities or None, risk_preference=risk_preference,
            year=2025, conn=conn,
        )

    vols = reco["volunteers"]
    stats = reco["stats"]
    st.subheader(f"推荐院校专业组（{profile.subject_category}）")
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("志愿组数", stats["total"])
    c2.metric("冲", stats["冲"]); c3.metric("稳", stats["稳"])
    c4.metric("保", stats["保"]); c5.metric("垫", stats["垫"])

    df = _groups_df(vols)
    st.dataframe(
        df, width="stretch", hide_index=True, height=620,
        column_config={
            "序号": st.column_config.NumberColumn(width="small"),
            "层级": st.column_config.TextColumn(width="small"),
            "学校": st.column_config.TextColumn(width="medium"),
            "城市": st.column_config.TextColumn(width="small"),
            "专业组": st.column_config.TextColumn(width="small"),
            "再选要求": st.column_config.TextColumn(width="small"),
            "专业匹配": st.column_config.TextColumn(width="small"),
            "投档位次": st.column_config.NumberColumn(width="small"),
            "组内专业": st.column_config.TextColumn(width="large"),
        },
    )

    reserve = reco.get("reserve", [])
    if reserve:
        with st.expander(f"备选池（高危冲 / 数据不足，{len(reserve)} 组）"):
            st.dataframe(_groups_df(reserve), width="stretch", hide_index=True)

    st.caption(
        "说明：「投档位次」为该院校专业组近三年加权投档最低位次（组门槛），"
        "权重 2025:0.5 / 2024:0.3 / 2023:0.2。组内专业明细持续补充中。"
    )
