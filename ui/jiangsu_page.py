"""Jiangsu (3+1+2 院校专业组) Streamlit page.

Rendered by main.py when the selected province is 江苏. Kept separate from the
Zhejiang flow because Jiangsu's volunteer unit is the 院校专业组 (not 学校+专业):
we recommend 40 院校专业组 and, where 招生计划 detail exists, list the majors inside.

Mirrors Zhejiang's UX (AI 对话顾问 + 自动填表 + 冲稳保结果表), adapted to:
  - 3+1+2 form (首选物理/历史 + 再选 2 门)，物理类/历史类 分池
  - 院校专业组 推荐（40 组），组内专业有则展开、无则「待补充」
  - 专业偏好 = 软排序提示（已有组内明细才生效），不做硬过滤
"""

from __future__ import annotations

import json
import re

import pandas as pd
import streamlit as st

from db import get_conn
from src.common.input.llm import (
    REGION_EXPANSIONS,
    chat_with_advisor,
    explain_volunteer,
    generate_overall_report,
    search_web,
    should_search,
)
from src.jiangsu.config import PROVINCE_CONFIG as _JS_CONFIG
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
_SUBJECT_ALIAS = {"政治": "思想政治", "思政": "思想政治"}

_WELCOME = (
    "你好！我是**小明**，你的江苏志愿填报助手 👋\n\n"
    "江苏是**院校专业组**模式（本科批最多填 40 个院校专业组）。告诉我以下信息，我来帮你生成推荐：\n\n"
    "**必填**\n"
    "- 首选科类内的全省位次（如：8000）\n"
    "- 首选科目：**物理 或 历史**\n"
    "- 再选 2 门：化学 / 生物 / 思想政治 / 地理\n"
    "- 主排序：**学校优先 / 城市优先 / 专业优先**\n\n"
    "**选填**：想读的专业方向、偏好城市、风险偏好（激进/均衡/保守）\n\n"
    "直接说就行，例如：位次8000，物理+化学生物，专业优先，想学计算机，偏好南京\n\n"
    "*⚠️ 江苏组内专业明细仍在补充；专业偏好作排序提示，不保证组内含目标专业。最终以官方招生计划为准。*"
)


def _parse_json_from_text(text: str) -> dict | None:
    m = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:  # noqa: BLE001
            return None
    return None


def _member_majors(group: dict) -> list[str]:
    """Real inner majors of a group (excluding the synthetic __GROUP__ threshold row)."""
    names: list[str] = []
    for m in group.get("_members", []):
        if m.get("major_code") == "__GROUP__":
            continue
        name = (m.get("major_name") or "").strip()
        if name and "合计" not in name:
            names.append(name)
    seen: set[str] = set()
    return [n for n in names if not (n in seen or seen.add(n))]


def _groups_df(groups: list[dict]) -> pd.DataFrame:
    rows = []
    for idx, g in enumerate(groups, start=1):
        gi = g.get("gap_info", {})
        members = _member_majors(g)
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
            **history_rank_columns(g),
            "均值位次": gi.get("weighted_avg"),
            "gap": gi.get("gap"),
            "组内专业": inner,
        })
    return pd.DataFrame(rows)


def _group_as_volunteer(g: dict) -> dict:
    """Shape a 专业组 into the dict explain_volunteer/report expect."""
    members = _member_majors(g)
    return {
        "volunteer_no": g.get("volunteer_no"),
        "school_name": g.get("school_name", ""),
        "school_city": g.get("school_city", ""),
        "major_name": f"{g.get('sg_name','')}组（{('、'.join(members[:4])) if members else '组内专业待补充'}）",
        "gap_info": g.get("gap_info", {}),
    }


def render(province: str = "jiangsu") -> None:
    st.title("高考志愿推荐系统 · 江苏")
    if st.button("← 切换省份", key="js_back"):
        for k in list(st.session_state.keys()):
            if k != "js_back":
                del st.session_state[k]
        st.rerun()

    # ── 对话填表 pending：必须在所有 widget 渲染前应用 ────────────────────────
    if "_js_pending_fill" in st.session_state:
        for _k, _v in st.session_state.pop("_js_pending_fill").items():
            st.session_state[_k] = _v

    st.info(
        "**江苏为「院校专业组」模式**：本科批按 40 个院校专业组填报，投档检索的是专业组而非单个专业。"
        "本推荐基于江苏省教育考试院 2023–2025 官方院校专业组投档线（物理类/历史类分列）。"
        "「组内专业」是该组已解析到的专业，暂无明细的组标注「待补充」；专业偏好仅作排序提示、不做硬筛选。"
        "**最终填报请以《江苏招生考试》招生计划专刊和省考试院官方信息为准。**"
    )

    # 表单门控：和浙江一致——先和小明对话收集参数，确认后才展开左侧表单与结果
    form_ready = bool(st.session_state.get("_js_form_ready", False))

    # ─── 侧边栏：API Key（常驻）+ 表单（门控后出现）──────────────────────────
    with st.sidebar:
        st.header("🔑 AI 设置")
        _user_key = st.text_input("百炼 API Key（AI 对话 / 报告 / 解释需要）", type="password",
                                  placeholder="sk-...", key="js_api_key")
        _api_key = _user_key.strip() or None
        st.caption("⚠️ 本工具及 AI 建议仅供参考，不构成填报指导，最终以官方为准，由用户自行负责。")

        if form_ready:
            st.divider()
            st.header("📋 考生信息（江苏 3+1+2）")
            rank = st.number_input("首选科类内全省位次", 1, 400_000, value=8000, step=100, key="js_rank")
            first_choice = st.radio("首选科目", FIRST_CHOICES, horizontal=True, key="js_first",
                                    help="物理 / 历史 二选一，决定录取科类和位次池")
            reselect = st.multiselect("再选科目（选 2 门）", RESELECT, max_selections=2, key="js_reselect",
                                      help="从 化学/生物/思想政治/地理 选 2 门")
            main_priority = st.selectbox("主排序", PRIORITIES, index=0, key="js_priority")
            st.caption("「专业优先」按已解析的组内专业做软排序；暂无明细的组不会被当作目标专业。")
            risk_preference = st.selectbox("风险偏好", ["激进", "均衡", "保守"], index=1, key="js_risk")

            st.divider()
            st.markdown("**可选偏好**")
            preferred_majors = [s.strip() for s in st.text_input(
                "想读的专业方向（弱提示，逗号分隔）", key="js_majors",
                placeholder="如 计算机, 金融").split(",") if s.strip()]
            school_levels = st.multiselect("学校层次", ["985", "211", "双一流"], key="js_levels")
            preferred_cities = [c.strip() for c in st.text_input(
                "偏好城市（逗号分隔）", key="js_cities", placeholder="如 南京, 苏州").split(",") if c.strip()]
        else:
            st.divider()
            st.info("先和上方小明对话（或直接说位次/选科/偏好），确认后参数会填到这里供你核对修改。")

    # 表单未就绪时的后备变量，供对话上下文使用
    if not form_ready:
        rank = st.session_state.get("js_rank", 8000)
        first_choice = st.session_state.get("js_first", "物理")
        reselect = st.session_state.get("js_reselect", [])
        main_priority = st.session_state.get("js_priority", "请选择…")
        risk_preference = st.session_state.get("js_risk", "均衡")
        preferred_majors, school_levels, preferred_cities = [], [], []

    # ─── 推荐管线（门控就绪 + 主排序已选 + 再选2门才跑）────────────────────
    reco = None
    if form_ready and main_priority != "请选择…" and len(reselect) == 2:
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

        with get_conn("jiangsu") as conn:
            eligible, _ = filter_by_subject(profile, year=2025, conn=conn)
            final, _ = filter_by_constraints(eligible, profile)
            if school_levels:
                final, _ = filter_by_school_level(final, school_levels)
            if preferred_cities and main_priority == "城市优先":
                final, _ = filter_by_city(final, preferred_cities)
            reco = build_recommendations(
                final, profile, main_priority=main_priority,
                preferred_majors=preferred_majors, preferred_categories=[], preferred_schools=[],
                preferred_cities=preferred_cities or None, risk_preference=risk_preference,
                year=2025, conn=conn,
            )
        # 顾问上下文：供 AI 解读已生成的方案
        st.session_state["_js_advisor_ctx"] = {
            "volunteers": [_group_as_volunteer(g) for g in reco["volunteers"]],
            "stats": reco["stats"],
        }

    _profile_ctx = {
        "rank": int(rank),
        "selected_subjects": [f"{first_choice}(首选)"] + reselect,
        "preferred_majors": preferred_majors,
        "preferred_cities": preferred_cities,
        "main_priority": main_priority if main_priority != "请选择…" else "未设置",
        "risk_preference": risk_preference,
    }

    # ─── AI 对话顾问 ─────────────────────────────────────────────────────────
    _render_advisor(_api_key, _profile_ctx, main_priority)

    # ─── 结果 ────────────────────────────────────────────────────────────────
    if not form_ready:
        return  # 门控未开：只显示对话，先收集参数
    if reco is None:
        if main_priority == "请选择…":
            st.warning("请在左侧选择「主排序」，或直接和上方小明对话。")
        elif len(reselect) != 2:
            st.warning("请在左侧选择恰好 2 门再选科目。")
        return

    stats = reco["stats"]
    st.subheader(f"推荐院校专业组（{first_choice}类）")
    if main_priority == "专业优先" and preferred_cities:
        st.info(
            f"ℹ️ 当前是「专业优先」：先按冲稳保和专业匹配排序，**{('、'.join(preferred_cities))}** 只作同档内的次要排序，"
            "不会强制排到最前。若希望偏好城市的专业组优先出现，请把主排序改成「城市优先」。"
        )
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("志愿组数", stats["total"])
    c2.metric("冲", stats["冲"]); c3.metric("稳", stats["稳"])
    c4.metric("保", stats["保"]); c5.metric("垫", stats["垫"])

    st.dataframe(
        _groups_df(reco["volunteers"]), width="stretch", hide_index=True, height=560,
        column_config={
            "序号": st.column_config.NumberColumn(width="small"),
            "层级": st.column_config.TextColumn(width="small"),
            "学校": st.column_config.TextColumn(width="medium"),
            "城市": st.column_config.TextColumn(width="small"),
            "专业组": st.column_config.TextColumn(width="small"),
            "再选要求": st.column_config.TextColumn(width="small"),
            "专业匹配": st.column_config.TextColumn(width="small"),
            "均值位次": st.column_config.NumberColumn(width="small", help="该专业组最新一年(2025)官方投档最低位次，作为进组门槛"),
            "gap": st.column_config.NumberColumn(width="small", help="均值位次 - 你的位次，正数更安全"),
            "组内专业": st.column_config.TextColumn(width="large"),
        },
    )

    reserve = reco.get("reserve", [])
    if reserve:
        with st.expander(f"备选池（高危冲 / 数据不足，{len(reserve)} 组）"):
            st.dataframe(_groups_df(reserve), width="stretch", hide_index=True)

    st.caption(
        "说明：江苏填报单位是院校专业组（非单专业）。江苏专业组每年重新编排、组号跨年不可比，"
        "因此冲稳保按**最新一年(2025)官方投档位次**判定；2024/2023 位次列仅作参考。组内专业明细持续补充中。"
    )


def _render_advisor(api_key, profile_ctx: dict, main_priority: str) -> None:
    """AI 对话顾问：收集参数→填表，或解读已生成方案。"""
    if "js_ai_chat" not in st.session_state:
        st.session_state["js_ai_chat"] = [{"role": "assistant", "content": _WELCOME}]

    with st.expander("💬 AI 对话顾问（小明）", expanded=True):
        _box = st.container(height=320)
        with _box:
            for _m in st.session_state["js_ai_chat"]:
                with st.chat_message(_m["role"]):
                    st.write(_m["content"])

        _n = st.session_state.get("js_ai_input_n", 0)
        col1, col2, col3 = st.columns([6, 1, 1])
        with col1:
            _msg = st.text_input("输入", key=f"js_ai_msg_{_n}",
                                 placeholder="说说你的情况或问题…", label_visibility="collapsed")
        with col2:
            _send = st.button("发送", use_container_width=True, key="js_ai_send")
        with col3:
            _clear = st.button("清除", use_container_width=True, key="js_ai_clear")

        if _clear:
            for k in ("js_ai_chat", "js_ai_parsed", "_js_advisor_ctx", "_js_form_ready"):
                st.session_state.pop(k, None)
            st.session_state["js_ai_input_n"] = _n + 1
            st.rerun()

        if _send and _msg.strip():
            st.session_state["_js_pending_msg"] = _msg.strip()
            st.session_state["js_ai_input_n"] = _n + 1
            st.rerun()

        # fn 注入：报告 / 解释
        _fn = st.session_state.pop("_js_ai_fn", None)
        _pending = st.session_state.pop("_js_pending_msg", None)

        if _fn:
            if not api_key:
                st.warning("请在左侧填入百炼 API Key 才能使用 AI 功能")
            else:
                st.session_state["js_ai_chat"].append({"role": "user", "content": _fn["label"]})
                with _box:
                    with st.chat_message("user"):
                        st.write(_fn["label"])
                    with st.chat_message("assistant"):
                        try:
                            if _fn["fn"] == "explain":
                                _resp = st.write_stream(explain_volunteer(
                                    _fn["volunteer"], _fn["profile"],
                                    main_priority=main_priority, api_key=api_key))
                            else:
                                _resp = st.write_stream(generate_overall_report(
                                    _fn["volunteers"], _fn["stats"], _fn["profile"],
                                    main_priority=main_priority, api_key=api_key))
                        except Exception as e:  # noqa: BLE001
                            _resp = f"⚠️ 生成失败：{e}"; st.write(_resp)
                st.session_state["js_ai_chat"].append({"role": "assistant", "content": _resp or "⚠️ 无返回"})
                st.rerun()

        if _pending:
            if not api_key:
                st.warning("请在左侧填入百炼 API Key 才能使用 AI 功能")
            else:
                st.session_state["js_ai_chat"].append({"role": "user", "content": _pending})
                with _box:
                    with st.chat_message("user"):
                        st.write(_pending)
                    with st.chat_message("assistant"):
                        _sr = None
                        if should_search(_pending) and st.session_state.get("_js_advisor_ctx"):
                            with st.spinner("正在搜索最新资料…"):
                                _sr = search_web(_pending)
                        _resp = st.write_stream(chat_with_advisor(
                            st.session_state["js_ai_chat"],
                            profile_ctx=profile_ctx,
                            recommendation_ctx=st.session_state.get("_js_advisor_ctx"),
                            search_results=_sr, api_key=api_key, province_config=_JS_CONFIG,
                        ))
                st.session_state["js_ai_chat"].append({"role": "assistant", "content": _resp})
                _parsed = _parse_json_from_text(_resp)
                if _parsed:
                    st.session_state["js_ai_parsed"] = _parsed
                st.rerun()

        # 提取到参数 → 确认填表
        if "js_ai_parsed" in st.session_state:
            _p = st.session_state["js_ai_parsed"]
            st.divider()
            st.markdown("**小明提取到的参数，确认后填入表单：**")
            pc1, pc2 = st.columns(2)
            with pc1:
                st.write(f"位次：**{_p.get('rank', '—')}**")
                st.write(f"首选：**{_p.get('first_choice', '—')}**")
                st.write(f"再选：**{'、'.join(_p.get('selected_subjects', [])) or '—'}**")
                st.write(f"主排序：**{_p.get('main_priority', '—')}**")
            with pc2:
                st.write(f"风险：**{_p.get('risk_preference', '—')}**")
                st.write(f"专业方向：**{'、'.join(_p.get('preferred_majors', [])) or '未指定'}**")
                st.write(f"偏好城市：**{'、'.join(_p.get('preferred_cities', [])) or '未指定'}**")

            if st.button("确认填入表单", type="primary", key="js_ai_confirm"):
                _fill: dict = {}
                if _p.get("rank"):
                    _fill["js_rank"] = max(1, min(400_000, int(_p["rank"])))
                if _p.get("first_choice") in FIRST_CHOICES:
                    _fill["js_first"] = _p["first_choice"]
                if _p.get("selected_subjects"):
                    _norm = [_SUBJECT_ALIAS.get(s, s) for s in _p["selected_subjects"]]
                    _fill["js_reselect"] = [s for s in _norm if s in RESELECT][:2]
                if _p.get("main_priority") in ["学校优先", "城市优先", "专业优先"]:
                    _fill["js_priority"] = _p["main_priority"]
                if _p.get("risk_preference") in ["激进", "均衡", "保守"]:
                    _fill["js_risk"] = _p["risk_preference"]
                if _p.get("preferred_majors"):
                    _fill["js_majors"] = ", ".join(_p["preferred_majors"])
                    if "js_priority" not in _fill:
                        _fill["js_priority"] = "专业优先"
                if _p.get("preferred_cities"):
                    _exp: list[str] = []
                    for _c in _p["preferred_cities"]:
                        _exp.extend(REGION_EXPANSIONS.get(_c, [_c]))
                    _fill["js_cities"] = ", ".join(dict.fromkeys(_exp))
                st.session_state["_js_pending_fill"] = _fill
                st.session_state["_js_form_ready"] = True
                st.session_state.pop("js_ai_parsed", None)
                st.session_state["js_ai_chat"].append({"role": "user", "content": "同意，参数已确认"})
                st.session_state["js_ai_chat"].append({"role": "assistant", "content": (
                    "收到！参数已填入左侧表单，推荐院校专业组正在生成 🎯\n\n"
                    "你也可以在左侧自由修改，或直接在这里说（如「换成城市优先」「想去南京」）让我更新。\n"
                    "方案生成后，下方有「生成总体报告」和「解释某个专业组」按钮。")})
                st.rerun()

        # 快捷按钮（方案已生成后）
        _ctx = st.session_state.get("_js_advisor_ctx")
        if _ctx and _ctx.get("volunteers"):
            _vols = _ctx["volunteers"]
            st.divider()
            _fn_profile = {**profile_ctx}
            if st.button("📊 生成总体报告", use_container_width=True, key="js_btn_report"):
                if not api_key:
                    st.warning("请先填入左侧百炼 API Key")
                else:
                    st.session_state["_js_ai_fn"] = {
                        "fn": "report", "label": "📊 生成总体分析报告",
                        "volunteers": _vols, "stats": _ctx["stats"], "profile": _fn_profile}
                    st.rerun()
            _labels = [f"{v.get('volunteer_no')}. {v.get('school_name')} · {v.get('major_name')}" for v in _vols]
            _sel = st.selectbox("选择要解释的院校专业组", options=_labels,
                                label_visibility="collapsed", key="js_vol_select")
            if st.button("💬 解释这个专业组", use_container_width=True, key="js_btn_explain"):
                if not api_key:
                    st.warning("请先填入左侧百炼 API Key")
                else:
                    _idx = _labels.index(_sel) if _sel in _labels else 0
                    _v = _vols[_idx]
                    st.session_state["_js_ai_fn"] = {
                        "fn": "explain",
                        "label": f"💬 解释第{_v.get('volunteer_no')}组：{_v.get('school_name')}",
                        "volunteer": _v, "profile": _fn_profile}
                    st.rerun()
