"""江苏志愿填报页 —— 仅负责展示，业务逻辑在 src/jiangsu/service.py。

5 模块工作流（session_state["js_stage"]）：
  entry      入口：两个按钮 [我没想好选什么] / [我有清晰目标]
  profiling  ① 兴趣问卷：一题一题选 ABCD → 推荐专业方向（只建议）
  working    ② 自然语言说需求→AI辅助填表 → ③ 生成 → ④ 手动改重生成 → ⑤ 解释

江苏 3+1+2：首选物理/历史 + 再选 2 门；志愿单位是院校专业组（40 个）。
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from src.common.input.llm import (
    chat_with_advisor,
    explain_volunteer,
    generate_overall_report,
    search_web,
    should_search,
)
from ui.questionnaire import render as _render_questionnaire
from src.common.reference import REGION_PROVINCES
from src.jiangsu import service as svc

EXCLUDE_OPTIONS = sorted({p for ps in REGION_PROVINCES.values() for p in ps})
FIRST_CHOICES = ["物理", "历史"]
RESELECT_OPTIONS = ["化学", "生物", "思想政治", "地理"]
PRIORITIES = ["请选择…", "学校优先", "城市优先", "专业优先"]
_FILL_WELCOME = (
    "用一句话说说你的情况就行，我来帮你填好左侧表单，你再核对修改 👌\n\n"
    "例如：**位次8000，物理+化学生物，专业优先，想学计算机，偏好南京**\n\n"
    "（必填：位次、首选物理/历史、再选2门、主排序；选填：专业方向 / 偏好城市 / 风险偏好）"
)


def _reset_all() -> None:
    for k in list(st.session_state.keys()):
        if k.startswith("js_") or k.startswith("_js_"):
            del st.session_state[k]


def render(province: str = "jiangsu") -> None:
    st.title("高考志愿推荐系统 · 江苏")
    cols = st.columns([1, 1, 6])
    with cols[0]:
        if st.button("← 切换省份", key="js_back"):
            _reset_all()
            st.session_state.pop("_province", None)
            st.rerun()
    stage = st.session_state.get("js_stage", "entry")
    if stage != "entry":
        with cols[1]:
            if st.button("↺ 重新开始", key="js_restart"):
                _reset_all()
                st.rerun()

    if "_js_pending_fill" in st.session_state:
        for _k, _v in st.session_state.pop("_js_pending_fill").items():
            st.session_state[_k] = _v

    if stage == "entry":
        _render_entry()
    elif stage == "profiling":
        _render_profiling()
    else:
        _render_working()


def _render_entry() -> None:
    st.info(
        "**江苏是「院校专业组」模式**：本科批最多填 40 个院校专业组，投档检索的是专业组而非单个专业。"
        "推荐基于江苏省教育考试院 2023–2025 官方院校专业组投档数据（物理类/历史类分列）。"
        "**最终填报请以《江苏招生考试》招生计划专刊和省考试院官方信息为准。**"
    )
    st.markdown("### 先选一个开始方式")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("#### 🤔 我还没想好选什么\n\n先做个小问卷，聊兴趣/性格/擅长科目，帮你找专业方向。")
        if st.button("没想好，先做兴趣问卷", use_container_width=True, key="js_go_profiling"):
            st.session_state["js_stage"] = "profiling"
            st.rerun()
    with c2:
        st.markdown("#### 🎯 我有清晰目标\n\n用一句话说需求，AI 帮你填好志愿信息，立刻生成方案。")
        if st.button("有目标，直接填志愿", use_container_width=True, type="primary", key="js_go_working"):
            st.session_state["js_stage"] = "working"
            st.rerun()


# ─── ① 兴趣问卷 ──────────────────────────────────────────────────────────────

def _render_profiling() -> None:
    with st.sidebar:
        st.header("🔑 AI 设置")
        api_key = (st.text_input("百炼 API Key", type="password", placeholder="sk-...", key="js_api_key").strip() or None)
        st.caption("⚠️ AI 建议仅供参考，由用户自行判断。")
    _render_questionnaire("js", api_key)


def _collect_form() -> dict:
    return {
        "rank": st.session_state.get("js_rank", 8000),
        "first_choice": st.session_state.get("js_first", "物理"),
        "selected_subjects": st.session_state.get("js_reselect", []),
        "main_priority": st.session_state.get("js_priority", "请选择…"),
        "risk_preference": st.session_state.get("js_risk", "均衡"),
        "preferred_majors": [s.strip() for s in st.session_state.get("js_majors", "").split(",") if s.strip()],
        "school_levels": st.session_state.get("js_levels", []),
        "preferred_cities": [c.strip() for c in st.session_state.get("js_cities", "").split(",") if c.strip()],
        "accept_private": st.session_state.get("js_private", True),
        "excluded_regions": st.session_state.get("js_excl", []),
    }


def _render_working() -> None:
    form_filled = bool(st.session_state.get("js_form_filled", False))

    with st.sidebar:
        st.header("🔑 AI 设置")
        api_key = (st.text_input("百炼 API Key（AI 填写 / 报告 / 解释需要）", type="password",
                                 placeholder="sk-...", key="js_api_key").strip() or None)
        st.caption("⚠️ 本工具及 AI 建议仅供参考，最终以官方为准，由用户自行负责。")
        if form_filled:
            st.divider()
            st.header("📋 志愿信息（可手动修改）")
            st.number_input("首选科类内全省位次", 1, svc.RANK_MAX, step=100, key="js_rank")
            st.radio("首选科目", FIRST_CHOICES, horizontal=True, key="js_first")
            st.multiselect("再选科目（选 2 门）", RESELECT_OPTIONS, max_selections=2, key="js_reselect")
            st.selectbox("主排序", PRIORITIES, key="js_priority")
            st.selectbox("风险偏好", ["激进", "均衡", "保守"], key="js_risk")
            st.divider()
            st.markdown("**可选偏好**")
            st.text_input("想读的专业方向（逗号分隔）", key="js_majors", placeholder="如 计算机, 金融")
            st.multiselect("学校层次", ["985", "211", "双一流"], key="js_levels")
            st.text_input("偏好城市（逗号分隔）", key="js_cities", placeholder="如 南京, 苏州")
            st.checkbox("接受民办院校", value=True, key="js_private")
            st.multiselect("排除省份（不想去的）", EXCLUDE_OPTIONS, key="js_excl")
        else:
            st.divider()
            st.info("先在右侧用一句话告诉小明你的情况，他帮你填好；填好后这里可手动微调。")

    nav1, _ = st.columns([1.4, 6])
    with nav1:
        if st.button("← 重新做兴趣问卷", key="js_to_profiling"):
            st.session_state["js_stage"] = "profiling"
            st.rerun()

    _render_fill_assistant(api_key)
    if not form_filled:
        return

    st.info("信息已填好（左侧可手动修改）。点「🚀 生成志愿」，改了再点一次即可重新生成。")
    if st.button("🚀 生成志愿", type="primary", key="js_generate"):
        st.session_state["js_generated"] = True
    if not st.session_state.get("js_generated"):
        return

    form = _collect_form()
    err = svc.validate_form(form)
    if err:
        st.warning(err)
        return
    try:
        recommendation = svc.recommend_target_and_references(form)
    except Exception as e:  # noqa: BLE001
        st.error(f"生成失败：{e}")
        return
    st.session_state["_js_advisor_ctx"] = svc.advisor_ctx(recommendation["target"])

    _render_results(recommendation, form)
    st.divider()
    _render_explain(api_key, form)


def _render_fill_assistant(api_key) -> None:
    if st.session_state.get("js_form_filled"):
        return
    st.subheader("② 告诉小明你的情况，他帮你填")
    if "js_fill_chat" not in st.session_state:
        st.session_state["js_fill_chat"] = [{"role": "assistant", "content": _FILL_WELCOME}]

    box = st.container(height=280)
    with box:
        for m in st.session_state["js_fill_chat"]:
            with st.chat_message(m["role"]):
                st.write(m["content"])

    n = st.session_state.get("js_fill_input_n", 0)
    c1, c2 = st.columns([7, 1])
    with c1:
        msg = st.text_input("输入", key=f"js_fill_msg_{n}",
                            placeholder="如：位次8000，物理+化学生物，专业优先，想学计算机", label_visibility="collapsed")
    with c2:
        send = st.button("发送", use_container_width=True, key="js_fill_send")
    if send and msg.strip():
        st.session_state["_js_fill_pending"] = msg.strip()
        st.session_state["js_fill_input_n"] = n + 1
        st.rerun()

    pending = st.session_state.pop("_js_fill_pending", None)
    if pending:
        if not api_key:
            st.warning("请在左侧填入百炼 API Key 才能用 AI 辅助填表")
        else:
            st.session_state["js_fill_chat"].append({"role": "user", "content": pending})
            with box:
                with st.chat_message("user"):
                    st.write(pending)
                with st.chat_message("assistant"):
                    resp = st.write_stream(chat_with_advisor(
                        st.session_state["js_fill_chat"], profile_ctx=_collect_form(),
                        recommendation_ctx=None, api_key=api_key, province_config=svc.PROVINCE_CONFIG))
            st.session_state["js_fill_chat"].append({"role": "assistant", "content": resp})
            params = svc.parse_advisor_params(resp)
            if params:
                st.session_state["js_fill_params"] = params
            st.rerun()

    params = st.session_state.get("js_fill_params")
    if params:
        st.markdown("**小明提取到的信息，确认后填入左侧表单：**")
        c1, c2 = st.columns(2)
        with c1:
            st.write(f"位次：**{params.get('rank', '—')}**")
            st.write(f"首选：**{params.get('first_choice', '—')}**")
            st.write(f"再选：**{'、'.join(params.get('selected_subjects', [])) or '—'}**")
            st.write(f"主排序：**{params.get('main_priority', '—')}**")
        with c2:
            st.write(f"风险：**{params.get('risk_preference', '—')}**")
            st.write(f"专业方向：**{'、'.join(params.get('preferred_majors', [])) or '未指定'}**")
            st.write(f"偏好城市：**{'、'.join(params.get('preferred_cities', [])) or '未指定'}**")
        if st.button("确认填入表单", type="primary", key="js_fill_confirm"):
            fill = svc.params_to_form(params)
            pf = {}
            if "rank" in fill: pf["js_rank"] = fill["rank"]
            if "first_choice" in fill: pf["js_first"] = fill["first_choice"]
            if "selected_subjects" in fill: pf["js_reselect"] = fill["selected_subjects"]
            if "main_priority" in fill: pf["js_priority"] = fill["main_priority"]
            if "risk_preference" in fill: pf["js_risk"] = fill["risk_preference"]
            if "preferred_majors" in fill: pf["js_majors"] = ", ".join(fill["preferred_majors"])
            if "preferred_cities" in fill: pf["js_cities"] = ", ".join(fill["preferred_cities"])
            st.session_state["_js_pending_fill"] = pf
            st.session_state["js_form_filled"] = True
            st.session_state.pop("js_fill_params", None)
            st.rerun()


def _render_results(recommendation: dict, form: dict) -> None:
    target = recommendation["target"]
    recos = recommendation["references"]
    ref_years = sorted(recos.keys(), reverse=True)
    target_year = target.get("_target_year", svc.TARGET_YEAR)
    cat = "物理类" if form["first_choice"] == "物理" else "历史类"
    st.subheader(f"③ 推荐院校专业组（{cat}）")
    st.info(
        f"推荐逻辑：**{target_year} 招生目录决定能填哪些院校专业组和组内专业**；"
        "**往年分数线和位次**用于估算风险。江苏专业组每年重新编排，组号跨年不可比。"
    )
    if target.get("_is_fallback"):
        st.warning(
            f"{svc.TARGET_YEAR} 江苏招生目录尚未发布/导入，**暂用 {target_year} 目录生成主推荐**；"
            f"{svc.TARGET_YEAR} 目录导入后会自动切换。下面历史参考标签页不能直接照填。"
        )
    if form["main_priority"] == "专业优先" and form["preferred_cities"]:
        st.info(f"ℹ️ 「专业优先」下 **{'、'.join(form['preferred_cities'])}** 只作同档内次要排序，不强制排前。"
                "想让该城市优先，请把主排序改成「城市优先」。")
    tabs = st.tabs([target.get("_tab_label", f"{target_year} 推荐")] + [f"{y} 历史参考" for y in ref_years])
    with tabs[0]:
        _render_year_block(target, target_year, primary=True)
    for tab, year in zip(tabs[1:], ref_years):
        with tab:
            _render_year_block(recos[year], year, primary=False)

    pool = target.get("_pool", [])
    if pool:
        with st.expander(f"{target_year} 候选池（符合你筛选条件的全部专业，{len(pool)} 条）"):
            st.dataframe(pd.DataFrame(svc.pool_rows(pool)), width="stretch", hide_index=True)

    v = target["volunteers"]
    if v:
        st.divider()
        st.markdown("**📈 目标专业往年分数线和位次**")
        st.caption("选一个专业组，看组里每个专业近三年的录取分数线和位次，判断越来越难考还是好考。"
                   "江苏按专业组整体投档，同一年同组专业位次相同。")
        labels = [svc.group_label(g) for g in v]
        sel = st.selectbox("选一个学校专业组", labels, key="js_trend_sel")
        rows = svc.member_trend_rows(v[labels.index(sel) if sel in labels else 0])
        if rows:
            st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
        else:
            st.info("该组暂无组内专业明细（待补充）。")

    st.caption("说明：江苏填报单位是院校专业组（非单专业）。「参考位次」来自往年同专业组官方投档数据，"
               "「gap」= 参考位次 − 你的位次。改了左侧信息后再点「🚀 生成志愿」即可重新生成。")


def _render_year_block(reco: dict, year: int, primary: bool) -> None:
    stats = reco["stats"]
    c = st.columns(5)
    c[0].metric("志愿组数", stats["total"])
    c[1].metric("冲", stats["冲"]); c[2].metric("稳", stats["稳"])
    c[3].metric("保", stats["保"]); c[4].metric("垫", stats["垫"])
    if not primary:
        st.caption(f"⚠️ {year} 年为历史参考：当年专业组编排与今年不同，**不能直接照填**，仅供看趋势。")
    st.dataframe(
        pd.DataFrame(svc.group_rows(reco["volunteers"])), width="stretch", hide_index=True, height=520,
        column_config={
            "参考位次": st.column_config.NumberColumn(width="small", help="往年录取位次参考，正数 gap 更安全"),
            "gap": st.column_config.NumberColumn(width="small", help="参考位次 - 你的位次，正数更安全"),
            "组内专业": st.column_config.TextColumn(width="large"),
        },
    )
    reserve = reco.get("reserve", [])
    if reserve:
        with st.expander(f"{year} 备选池（高危冲 / 数据不足，{len(reserve)} 组）"):
            st.dataframe(pd.DataFrame(svc.group_rows(reserve)), width="stretch", hide_index=True)


def _render_explain(api_key, form: dict) -> None:
    ctx = st.session_state.get("_js_advisor_ctx")
    if not (ctx and ctx.get("volunteers")):
        return
    vols = ctx["volunteers"]
    main_priority = form["main_priority"]
    st.subheader("⑤ AI 解读")
    if "js_ai_chat" not in st.session_state:
        st.session_state["js_ai_chat"] = []

    cA, cB = st.columns([1, 2])
    with cA:
        if st.button("📊 解读整体方案", use_container_width=True, key="js_btn_report"):
            if not api_key:
                st.warning("请先填入左侧百炼 API Key")
            else:
                st.session_state["_js_ai_fn"] = {"fn": "report", "label": "📊 解读整体方案",
                                                 "volunteers": vols, "stats": ctx["stats"], "profile": form}
                st.rerun()
    with cB:
        labels = [f"{v.get('volunteer_no')}. {v.get('school_name')} · {v.get('major_name')}" for v in vols]
        sel = st.selectbox("选一条志愿解释", labels, label_visibility="collapsed", key="js_vol_select")
        if st.button("💬 解释这个专业组", use_container_width=True, key="js_btn_explain"):
            if not api_key:
                st.warning("请先填入左侧百炼 API Key")
            else:
                v = vols[labels.index(sel) if sel in labels else 0]
                st.session_state["_js_ai_fn"] = {"fn": "explain",
                                                 "label": f"💬 解释第{v.get('volunteer_no')}组：{v.get('school_name')}",
                                                 "volunteer": v, "profile": form}
                st.rerun()

    box = st.container(height=300)
    with box:
        for m in st.session_state["js_ai_chat"]:
            with st.chat_message(m["role"]):
                st.write(m["content"])

    n = st.session_state.get("js_ai_input_n", 0)
    c1, c2 = st.columns([7, 1])
    with c1:
        msg = st.text_input("输入", key=f"js_ai_msg_{n}",
                            placeholder="问问这套方案，如「冲的会不会太多」", label_visibility="collapsed")
    with c2:
        send = st.button("发送", use_container_width=True, key="js_ai_send")
    if send and msg.strip():
        st.session_state["_js_pending_msg"] = msg.strip()
        st.session_state["js_ai_input_n"] = n + 1
        st.rerun()

    fn = st.session_state.pop("_js_ai_fn", None)
    pending = st.session_state.pop("_js_pending_msg", None)

    if fn:
        if not api_key:
            st.warning("请先填入左侧百炼 API Key")
        else:
            st.session_state["js_ai_chat"].append({"role": "user", "content": fn["label"]})
            with box:
                with st.chat_message("user"):
                    st.write(fn["label"])
                with st.chat_message("assistant"):
                    try:
                        if fn["fn"] == "explain":
                            resp = st.write_stream(explain_volunteer(
                                fn["volunteer"], fn["profile"], main_priority=main_priority, api_key=api_key))
                        else:
                            resp = st.write_stream(generate_overall_report(
                                fn["volunteers"], fn["stats"], fn["profile"],
                                main_priority=main_priority, api_key=api_key))
                    except Exception as e:  # noqa: BLE001
                        resp = f"⚠️ 生成失败：{e}"; st.write(resp)
            st.session_state["js_ai_chat"].append({"role": "assistant", "content": resp or "⚠️ 无返回"})
            st.rerun()

    if pending:
        if not api_key:
            st.warning("请先填入左侧百炼 API Key")
        else:
            st.session_state["js_ai_chat"].append({"role": "user", "content": pending})
            with box:
                with st.chat_message("user"):
                    st.write(pending)
                with st.chat_message("assistant"):
                    sr = search_web(pending) if should_search(pending) else None
                    resp = st.write_stream(chat_with_advisor(
                        st.session_state["js_ai_chat"], profile_ctx=form,
                        recommendation_ctx=ctx, search_results=sr,
                        api_key=api_key, province_config=svc.PROVINCE_CONFIG))
            st.session_state["js_ai_chat"].append({"role": "assistant", "content": resp})
            st.rerun()
