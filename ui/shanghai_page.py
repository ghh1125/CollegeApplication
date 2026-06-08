"""上海志愿填报页 —— 仅负责展示，所有业务逻辑在 src/shanghai/service.py。

5 模块工作流（session_state["sh_stage"]）：
  entry      入口：两个按钮 [我没想好选什么] / [我有清晰目标]
  profiling  ① 兴趣问卷：一题一题选 ABCD → src 分析推荐专业方向（只建议）
  working    ② 自然语言说需求→AI辅助填表 → ③ 生成 → ④ 手动改重生成 → ⑤ 解释

页面不写业务逻辑：推荐编排、参数解析/映射、结果转行等都调 service。
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
from src.common.input.user_profile import QUESTIONS as _PF_QUESTIONS, analyze_questionnaire
from src.shanghai import service as svc

SUBJECT_OPTIONS = ["物理", "化学", "生物", "思想政治", "历史", "地理"]
PRIORITIES = ["请选择…", "学校优先", "城市优先", "专业优先"]
_FILL_WELCOME = (
    "用一句话说说你的情况就行，我来帮你填好左侧表单，你再核对修改 👌\n\n"
    "例如：**位次8000，物理化学生物，专业优先，想学计算机，偏好上海**\n\n"
    "（必填：位次、选考3门、主排序；选填：专业方向 / 偏好城市 / 风险偏好）"
)


# ─────────────────────────────────────────────────────────────────────────────
# 入口路由
# ─────────────────────────────────────────────────────────────────────────────

def _reset_all() -> None:
    for k in list(st.session_state.keys()):
        if k.startswith("sh_") or k.startswith("_sh_"):
            del st.session_state[k]


def render(province: str = "shanghai") -> None:
    st.title("高考志愿推荐系统 · 上海")
    cols = st.columns([1, 1, 6])
    with cols[0]:
        if st.button("← 切换省份", key="sh_back"):
            _reset_all()
            st.session_state.pop("_province", None)
            st.rerun()
    stage = st.session_state.get("sh_stage", "entry")
    if stage != "entry":
        with cols[1]:
            if st.button("↺ 重新开始", key="sh_restart"):
                _reset_all()
                st.rerun()

    # AI 辅助填表写入的预填，须在 widget 渲染前应用
    if "_sh_pending_fill" in st.session_state:
        for _k, _v in st.session_state.pop("_sh_pending_fill").items():
            st.session_state[_k] = _v

    if stage == "entry":
        _render_entry()
    elif stage == "profiling":
        _render_profiling()
    else:
        _render_working()


def _render_entry() -> None:
    st.info(
        "**上海是「院校专业组」模式**：本科普通批最多填 24 个院校专业组，投档检索的是专业组而非单个专业，"
        "每组内再设 4 个专业志愿。推荐基于上海市教育考试院 2023–2025 官方院校专业组投档数据。"
        "**最终填报请以上海市教育考试院（上海招考热线）官方信息为准。**"
    )
    st.markdown("### 先选一个开始方式")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("#### 🤔 我还没想好选什么\n\n先做个小问卷，聊兴趣/性格/擅长科目，帮你找专业方向。")
        if st.button("没想好，先做兴趣问卷", use_container_width=True, key="sh_go_profiling"):
            st.session_state["sh_stage"] = "profiling"
            st.rerun()
    with c2:
        st.markdown("#### 🎯 我有清晰目标\n\n用一句话说需求，AI 帮你填好志愿信息，立刻生成方案。")
        if st.button("有目标，直接填志愿", use_container_width=True, type="primary", key="sh_go_working"):
            st.session_state["sh_stage"] = "working"
            st.rerun()


# ─────────────────────────────────────────────────────────────────────────────
# ① 兴趣问卷
# ─────────────────────────────────────────────────────────────────────────────

def _sidebar_api_key(caption: str) -> str | None:
    with st.sidebar:
        st.header("🔑 AI 设置")
        key = st.text_input("百炼 API Key", type="password", placeholder="sk-...", key="sh_api_key")
        st.caption(caption)
    return key.strip() or None


def _render_profiling() -> None:
    st.subheader("① 兴趣问卷 · 帮你找专业方向")
    api_key = _sidebar_api_key("⚠️ AI 建议仅供参考，由用户自行判断。")

    total = len(_PF_QUESTIONS)
    step = st.session_state.get("sh_pf_step", 0)
    answers: dict = st.session_state.setdefault("sh_pf_answers", {})

    nav1, nav2 = st.columns(2)
    with nav1:
        if st.button("← 返回入口", key="sh_pf_back"):
            st.session_state["sh_stage"] = "entry"
            st.rerun()
    with nav2:
        if st.button("跳过问卷，直接填志愿 →", key="sh_pf_skip"):
            st.session_state["sh_stage"] = "working"
            st.rerun()

    # 答题：一次一题
    if step < total:
        q = _PF_QUESTIONS[step]
        st.progress(step / total, text=f"第 {step + 1} / {total} 题")
        st.markdown(f"### {q['question']}")
        opts = q["options"]
        labels = [f"{k}. {v}" for k, v in opts.items()]
        prev = answers.get(q["key"])
        idx = list(opts).index(prev) if prev in opts else 0
        choice = st.radio("选一个最接近的", labels, index=idx, key=f"sh_pf_q{step}", label_visibility="collapsed")
        chosen = choice.split(".", 1)[0]
        b1, b2, _ = st.columns([1, 1, 5])
        with b1:
            if step > 0 and st.button("← 上一题", key=f"sh_pf_prev{step}"):
                answers[q["key"]] = chosen
                st.session_state["sh_pf_step"] = step - 1
                st.rerun()
        with b2:
            last = step == total - 1
            if st.button("看推荐 ✓" if last else "下一题 →", type="primary", key=f"sh_pf_next{step}"):
                answers[q["key"]] = chosen
                st.session_state["sh_pf_step"] = step + 1
                st.rerun()
        return

    # 答完：调 src 分析推荐
    st.success("问卷完成！下面是基于你回答的专业方向建议（仅供参考）。")
    st.caption("方向只是参考，最终读什么由你定。看完点「去填志愿信息」，AI 会帮你填表。")
    if "sh_pf_result" not in st.session_state:
        if not api_key:
            st.warning("请在左侧填入百炼 API Key，以生成专业方向推荐。")
        else:
            payload = [
                {"question": q["question"], "choice": answers.get(q["key"], ""),
                 "answer": q["options"].get(answers.get(q["key"], ""), "")}
                for q in _PF_QUESTIONS
            ]
            with st.chat_message("assistant"):
                try:
                    resp = st.write_stream(analyze_questionnaire(payload, api_key=api_key))
                except Exception as e:  # noqa: BLE001
                    resp = f"⚠️ 生成失败：{e}"; st.write(resp)
            st.session_state["sh_pf_result"] = resp
    else:
        with st.chat_message("assistant"):
            st.write(st.session_state["sh_pf_result"])

    r1, r2, _ = st.columns([1.2, 1.2, 4])
    with r1:
        if st.button("↺ 重新答题", key="sh_pf_redo"):
            for k in ("sh_pf_step", "sh_pf_answers", "sh_pf_result"):
                st.session_state.pop(k, None)
            st.rerun()
    with r2:
        if st.button("去填志愿信息 →", type="primary", key="sh_pf_to_form"):
            st.session_state["sh_stage"] = "working"
            st.rerun()


# ─────────────────────────────────────────────────────────────────────────────
# ② AI 辅助填表 ③ 生成 ④ 改需求 ⑤ 解释
# ─────────────────────────────────────────────────────────────────────────────

def _collect_form() -> dict:
    """从 session 读出表单值（AI 填或手填都存在 sh_ 键上）。"""
    return {
        "rank": st.session_state.get("sh_rank", 8000),
        "selected_subjects": st.session_state.get("sh_subjects", []),
        "main_priority": st.session_state.get("sh_priority", "请选择…"),
        "risk_preference": st.session_state.get("sh_risk", "均衡"),
        "preferred_majors": [s.strip() for s in st.session_state.get("sh_majors", "").split(",") if s.strip()],
        "school_levels": st.session_state.get("sh_levels", []),
        "preferred_cities": [c.strip() for c in st.session_state.get("sh_cities", "").split(",") if c.strip()],
    }


def _render_working() -> None:
    form_filled = bool(st.session_state.get("sh_form_filled", False))

    with st.sidebar:
        st.header("🔑 AI 设置")
        api_key = (st.text_input("百炼 API Key（AI 填写 / 报告 / 解释需要）", type="password",
                                 placeholder="sk-...", key="sh_api_key").strip() or None)
        st.caption("⚠️ 本工具及 AI 建议仅供参考，最终以官方为准，由用户自行负责。")
        if form_filled:
            st.divider()
            st.header("📋 志愿信息（可手动修改）")
            st.number_input("全市位次", 1, svc.RANK_MAX, step=100, key="sh_rank")
            st.multiselect("选考科目（选 3 门）", SUBJECT_OPTIONS, max_selections=3, key="sh_subjects")
            st.selectbox("主排序", PRIORITIES, key="sh_priority")
            st.selectbox("风险偏好", ["激进", "均衡", "保守"], key="sh_risk")
            st.divider()
            st.markdown("**可选偏好**")
            st.text_input("想读的专业方向（逗号分隔）", key="sh_majors", placeholder="如 计算机, 金融")
            st.multiselect("学校层次", ["985", "211", "双一流"], key="sh_levels")
            st.text_input("偏好城市（逗号分隔）", key="sh_cities", placeholder="如 上海, 南京")
        else:
            st.divider()
            st.info("先在右侧用一句话告诉小明你的情况，他帮你填好；填好后这里可手动微调。")

    nav1, _ = st.columns([1.4, 6])
    with nav1:
        if st.button("← 重新做兴趣问卷", key="sh_to_profiling"):
            st.session_state["sh_stage"] = "profiling"
            st.rerun()

    # ② AI 辅助填表
    _render_fill_assistant(api_key)
    if not form_filled:
        return

    # ③ 生成（可重复 = ④ 改需求重生成）
    st.info("信息已填好（左侧可手动修改）。点「🚀 生成志愿」，改了再点一次即可重新生成。")
    if st.button("🚀 生成志愿", type="primary", key="sh_generate"):
        st.session_state["sh_generated"] = True
    if not st.session_state.get("sh_generated"):
        return

    form = _collect_form()
    err = svc.validate_form(form)
    if err:
        st.warning(err)
        return
    try:
        recos = svc.recommend_for_years(form)
    except Exception as e:  # noqa: BLE001
        st.error(f"生成失败：{e}")
        return
    reco = recos[svc.YEARS[0]]
    st.session_state["_sh_advisor_ctx"] = svc.advisor_ctx(reco)

    _render_results(recos, form)
    st.divider()
    _render_explain(api_key, form)


def _render_fill_assistant(api_key) -> None:
    """② 自然语言 → AI 提取参数 → 确认填入表单。逻辑在 service。"""
    if st.session_state.get("sh_form_filled"):
        return
    st.subheader("② 告诉小明你的情况，他帮你填")
    if "sh_fill_chat" not in st.session_state:
        st.session_state["sh_fill_chat"] = [{"role": "assistant", "content": _FILL_WELCOME}]

    box = st.container(height=280)
    with box:
        for m in st.session_state["sh_fill_chat"]:
            with st.chat_message(m["role"]):
                st.write(m["content"])

    n = st.session_state.get("sh_fill_input_n", 0)
    c1, c2 = st.columns([7, 1])
    with c1:
        msg = st.text_input("输入", key=f"sh_fill_msg_{n}",
                            placeholder="如：位次8000，物理化学生物，专业优先，想学计算机", label_visibility="collapsed")
    with c2:
        send = st.button("发送", use_container_width=True, key="sh_fill_send")
    if send and msg.strip():
        st.session_state["_sh_fill_pending"] = msg.strip()
        st.session_state["sh_fill_input_n"] = n + 1
        st.rerun()

    pending = st.session_state.pop("_sh_fill_pending", None)
    if pending:
        if not api_key:
            st.warning("请在左侧填入百炼 API Key 才能用 AI 辅助填表")
        else:
            st.session_state["sh_fill_chat"].append({"role": "user", "content": pending})
            with box:
                with st.chat_message("user"):
                    st.write(pending)
                with st.chat_message("assistant"):
                    profile_ctx = {**_collect_form(), "selected_subjects": st.session_state.get("sh_subjects", [])}
                    resp = st.write_stream(chat_with_advisor(
                        st.session_state["sh_fill_chat"], profile_ctx=profile_ctx,
                        recommendation_ctx=None, api_key=api_key, province_config=svc.PROVINCE_CONFIG))
            st.session_state["sh_fill_chat"].append({"role": "assistant", "content": resp})
            params = svc.parse_advisor_params(resp)
            if params:
                st.session_state["sh_fill_params"] = params
            st.rerun()

    # 提取到参数 → 确认填入
    params = st.session_state.get("sh_fill_params")
    if params:
        st.markdown("**小明提取到的信息，确认后填入左侧表单：**")
        c1, c2 = st.columns(2)
        with c1:
            st.write(f"位次：**{params.get('rank', '—')}**")
            st.write(f"选考：**{'、'.join(params.get('selected_subjects', [])) or '—'}**")
            st.write(f"主排序：**{params.get('main_priority', '—')}**")
        with c2:
            st.write(f"风险：**{params.get('risk_preference', '—')}**")
            st.write(f"专业方向：**{'、'.join(params.get('preferred_majors', [])) or '未指定'}**")
            st.write(f"偏好城市：**{'、'.join(params.get('preferred_cities', [])) or '未指定'}**")
        if st.button("确认填入表单", type="primary", key="sh_fill_confirm"):
            fill = svc.params_to_form(params)
            pending_fill = {}
            if "rank" in fill: pending_fill["sh_rank"] = fill["rank"]
            if "selected_subjects" in fill: pending_fill["sh_subjects"] = fill["selected_subjects"]
            if "main_priority" in fill: pending_fill["sh_priority"] = fill["main_priority"]
            if "risk_preference" in fill: pending_fill["sh_risk"] = fill["risk_preference"]
            if "preferred_majors" in fill: pending_fill["sh_majors"] = ", ".join(fill["preferred_majors"])
            if "preferred_cities" in fill: pending_fill["sh_cities"] = ", ".join(fill["preferred_cities"])
            st.session_state["_sh_pending_fill"] = pending_fill
            st.session_state["sh_form_filled"] = True
            st.session_state.pop("sh_fill_params", None)
            st.rerun()


def _render_results(recos: dict, form: dict) -> None:
    st.subheader("③ 推荐院校专业组")
    st.info(
        "上海专业组**每年重新编排、组号跨年不是同一个组**，故不跨年加权，按年份分别给出：\n\n"
        "- **2025 推荐**：今年实际填报的 24 个院校专业组方案\n"
        "- **2024 / 2023 参考**：当年同位次能选到的组，看趋势用，**不能直接照填**"
    )
    if form["main_priority"] == "专业优先" and form["preferred_cities"]:
        st.info(f"ℹ️ 「专业优先」下 **{'、'.join(form['preferred_cities'])}** 只作同档内次要排序，不强制排前。"
                "想让该城市优先，请把主排序改成「城市优先」。")
    tabs = st.tabs(["2025 推荐（按此填报）", "2024 参考", "2023 参考"])
    for tab, year in zip(tabs, (2025, 2024, 2023)):
        with tab:
            _render_year_block(recos[year], year, primary=(year == 2025))

    v25 = recos[2025]["volunteers"]
    if v25:
        st.divider()
        st.markdown("**📈 某个专业近三年要多少位次能进**")
        st.caption("选一个学校的专业组，看组里每个专业最近三年的录取位次，判断越来越难考还是好考。"
                   "上海按专业组整体投档，同一年同组专业位次相同。")
        labels = [svc.group_label(g) for g in v25]
        sel = st.selectbox("选一个学校专业组", labels, key="sh_trend_sel")
        rows = svc.member_trend_rows(v25[labels.index(sel) if sel in labels else 0])
        if rows:
            st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
        else:
            st.info("该组暂无组内专业明细（待补充）。")

    st.caption("说明：上海填报单位是院校专业组（非单专业）。「投档位次」为各专业组当年官方投档最低位次，"
               "「gap」= 投档位次 − 你的位次。改了左侧信息后再点「🚀 生成志愿」即可重新生成。")


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
            "投档位次": st.column_config.NumberColumn(width="small", help=f"{year} 年该专业组官方投档最低位次"),
            "gap": st.column_config.NumberColumn(width="small", help="投档位次 - 你的位次，正数更安全"),
            "组内专业": st.column_config.TextColumn(width="large"),
        },
    )
    reserve = reco.get("reserve", [])
    if reserve:
        with st.expander(f"{year} 备选池（高危冲 / 数据不足，{len(reserve)} 组）"):
            st.dataframe(pd.DataFrame(svc.group_rows(reserve)), width="stretch", hide_index=True)


# ─── ⑤ 解释 / 问答 ──────────────────────────────────────────────────────────

def _render_explain(api_key, form: dict) -> None:
    ctx = st.session_state.get("_sh_advisor_ctx")
    if not (ctx and ctx.get("volunteers")):
        return
    vols = ctx["volunteers"]
    main_priority = form["main_priority"]
    st.subheader("⑤ AI 解读")
    if "sh_ai_chat" not in st.session_state:
        st.session_state["sh_ai_chat"] = []

    cA, cB = st.columns([1, 2])
    with cA:
        if st.button("📊 解读整体方案", use_container_width=True, key="sh_btn_report"):
            if not api_key:
                st.warning("请先填入左侧百炼 API Key")
            else:
                st.session_state["_sh_ai_fn"] = {"fn": "report", "label": "📊 解读整体方案",
                                                 "volunteers": vols, "stats": ctx["stats"], "profile": form}
                st.rerun()
    with cB:
        labels = [f"{v.get('volunteer_no')}. {v.get('school_name')} · {v.get('major_name')}" for v in vols]
        sel = st.selectbox("选一条志愿解释", labels, label_visibility="collapsed", key="sh_vol_select")
        if st.button("💬 解释这个专业组", use_container_width=True, key="sh_btn_explain"):
            if not api_key:
                st.warning("请先填入左侧百炼 API Key")
            else:
                v = vols[labels.index(sel) if sel in labels else 0]
                st.session_state["_sh_ai_fn"] = {"fn": "explain",
                                                 "label": f"💬 解释第{v.get('volunteer_no')}组：{v.get('school_name')}",
                                                 "volunteer": v, "profile": form}
                st.rerun()

    box = st.container(height=300)
    with box:
        for m in st.session_state["sh_ai_chat"]:
            with st.chat_message(m["role"]):
                st.write(m["content"])

    n = st.session_state.get("sh_ai_input_n", 0)
    c1, c2 = st.columns([7, 1])
    with c1:
        msg = st.text_input("输入", key=f"sh_ai_msg_{n}",
                            placeholder="问问这套方案，如「冲的会不会太多」", label_visibility="collapsed")
    with c2:
        send = st.button("发送", use_container_width=True, key="sh_ai_send")
    if send and msg.strip():
        st.session_state["_sh_pending_msg"] = msg.strip()
        st.session_state["sh_ai_input_n"] = n + 1
        st.rerun()

    fn = st.session_state.pop("_sh_ai_fn", None)
    pending = st.session_state.pop("_sh_pending_msg", None)

    if fn:
        if not api_key:
            st.warning("请先填入左侧百炼 API Key")
        else:
            st.session_state["sh_ai_chat"].append({"role": "user", "content": fn["label"]})
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
            st.session_state["sh_ai_chat"].append({"role": "assistant", "content": resp or "⚠️ 无返回"})
            st.rerun()

    if pending:
        if not api_key:
            st.warning("请先填入左侧百炼 API Key")
        else:
            st.session_state["sh_ai_chat"].append({"role": "user", "content": pending})
            with box:
                with st.chat_message("user"):
                    st.write(pending)
                with st.chat_message("assistant"):
                    sr = search_web(pending) if should_search(pending) else None
                    resp = st.write_stream(chat_with_advisor(
                        st.session_state["sh_ai_chat"], profile_ctx=form,
                        recommendation_ctx=ctx, search_results=sr,
                        api_key=api_key, province_config=svc.PROVINCE_CONFIG))
            st.session_state["sh_ai_chat"].append({"role": "assistant", "content": resp})
            st.rerun()
