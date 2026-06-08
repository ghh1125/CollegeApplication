"""浙江志愿填报页 —— 仅负责展示，业务逻辑在 src/zhejiang/service.py。

5 模块工作流（session_state["zj_stage"]）：
  entry      入口：两个按钮 [我没想好选什么] / [我有清晰目标]
  profiling  ① 兴趣问卷：一题一题选 ABCD → 推荐专业方向（只建议）
  working    ② 自然语言说需求→AI辅助填表 → ③ 生成 → ④ 手动改重生成 → ⑤ 解释

浙江 3+3（7选3）；志愿单位是「学校+专业」，最多 80 个。
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
from src.common.reference import REGION_PROVINCES
from src.zhejiang import service as svc

EXCLUDE_OPTIONS = sorted({p for ps in REGION_PROVINCES.values() for p in ps})
SUBJECT_OPTIONS = ["物理", "化学", "生物", "历史", "地理", "思想政治", "技术"]
PRIORITIES = ["请选择…", "学校优先", "城市优先", "专业优先"]
_FILL_WELCOME = (
    "用一句话说说你的情况就行，我来帮你填好左侧表单，你再核对修改 👌\n\n"
    "例如：**位次8000，物理化学生物，专业优先，想学计算机，偏好杭州**\n\n"
    "（必填：位次、选考3门、主排序；选填：专业方向 / 偏好城市 / 风险偏好）"
)


def _reset_all() -> None:
    for k in list(st.session_state.keys()):
        if k.startswith("zj_") or k.startswith("_zj_"):
            del st.session_state[k]


def render(province: str = "zhejiang") -> None:
    st.title("高考志愿推荐系统 · 浙江")
    cols = st.columns([1, 1, 6])
    with cols[0]:
        if st.button("← 切换省份", key="zj_back"):
            _reset_all()
            st.session_state.pop("_province", None)
            st.rerun()
    stage = st.session_state.get("zj_stage", "entry")
    if stage != "entry":
        with cols[1]:
            if st.button("↺ 重新开始", key="zj_restart"):
                _reset_all()
                st.rerun()

    if "_zj_pending_fill" in st.session_state:
        for _k, _v in st.session_state.pop("_zj_pending_fill").items():
            st.session_state[_k] = _v

    if stage == "entry":
        _render_entry()
    elif stage == "profiling":
        _render_profiling()
    else:
        _render_working()


def _render_entry() -> None:
    st.info(
        "**浙江新高考 3+3（7选3）**：志愿单位是「学校+专业」，最多填 80 个，按近三年加权位次判冲稳保。"
        "推荐基于公开历史录取位次数据。**最终填报请以浙江省教育考试院官方信息为准。**"
    )
    st.markdown("### 先选一个开始方式")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("#### 🤔 我还没想好选什么\n\n先做个小问卷，聊兴趣/性格/擅长科目，帮你找专业方向。")
        if st.button("没想好，先做兴趣问卷", use_container_width=True, key="zj_go_profiling"):
            st.session_state["zj_stage"] = "profiling"
            st.rerun()
    with c2:
        st.markdown("#### 🎯 我有清晰目标\n\n用一句话说需求，AI 帮你填好志愿信息，立刻生成方案。")
        if st.button("有目标，直接填志愿", use_container_width=True, type="primary", key="zj_go_working"):
            st.session_state["zj_stage"] = "working"
            st.rerun()


# ─── ① 兴趣问卷 ──────────────────────────────────────────────────────────────

def _render_profiling() -> None:
    st.subheader("① 兴趣问卷 · 帮你找专业方向")
    with st.sidebar:
        st.header("🔑 AI 设置")
        api_key = (st.text_input("百炼 API Key", type="password", placeholder="sk-...", key="zj_api_key").strip() or None)
        st.caption("⚠️ AI 建议仅供参考，由用户自行判断。")

    total = len(_PF_QUESTIONS)
    step = st.session_state.get("zj_pf_step", 0)
    answers: dict = st.session_state.setdefault("zj_pf_answers", {})

    nav1, nav2 = st.columns(2)
    with nav1:
        if st.button("← 返回入口", key="zj_pf_back"):
            st.session_state["zj_stage"] = "entry"
            st.rerun()
    with nav2:
        if st.button("跳过问卷，直接填志愿 →", key="zj_pf_skip"):
            st.session_state["zj_stage"] = "working"
            st.rerun()

    if step < total:
        q = _PF_QUESTIONS[step]
        st.progress(step / total, text=f"第 {step + 1} / {total} 题")
        st.markdown(f"### {q['question']}")
        opts = q["options"]
        labels = [f"{k}. {v}" for k, v in opts.items()]
        prev = answers.get(q["key"])
        idx = list(opts).index(prev) if prev in opts else 0
        choice = st.radio("选一个最接近的", labels, index=idx, key=f"zj_pf_q{step}", label_visibility="collapsed")
        chosen = choice.split(".", 1)[0]
        b1, b2, _ = st.columns([1, 1, 5])
        with b1:
            if step > 0 and st.button("← 上一题", key=f"zj_pf_prev{step}"):
                answers[q["key"]] = chosen
                st.session_state["zj_pf_step"] = step - 1
                st.rerun()
        with b2:
            last = step == total - 1
            if st.button("看推荐 ✓" if last else "下一题 →", type="primary", key=f"zj_pf_next{step}"):
                answers[q["key"]] = chosen
                st.session_state["zj_pf_step"] = step + 1
                st.rerun()
        return

    st.success("问卷完成！下面是基于你回答的专业方向建议（仅供参考）。")
    st.caption("方向只是参考，最终读什么由你定。看完点「去填志愿信息」，AI 会帮你填表。")
    if "zj_pf_result" not in st.session_state:
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
            st.session_state["zj_pf_result"] = resp
    else:
        with st.chat_message("assistant"):
            st.write(st.session_state["zj_pf_result"])

    r1, r2, _ = st.columns([1.2, 1.2, 4])
    with r1:
        if st.button("↺ 重新答题", key="zj_pf_redo"):
            for k in ("zj_pf_step", "zj_pf_answers", "zj_pf_result"):
                st.session_state.pop(k, None)
            st.rerun()
    with r2:
        if st.button("去填志愿信息 →", type="primary", key="zj_pf_to_form"):
            st.session_state["zj_stage"] = "working"
            st.rerun()


# ─── ② AI 辅助填表 ③ 生成 ④ 改需求 ⑤ 解释 ─────────────────────────────────

def _collect_form() -> dict:
    return {
        "rank": st.session_state.get("zj_rank", 8000),
        "total_score": st.session_state.get("zj_total_score") or None,
        "selected_subjects": st.session_state.get("zj_subjects", []),
        "main_priority": st.session_state.get("zj_priority", "请选择…"),
        "risk_preference": st.session_state.get("zj_risk", "均衡"),
        "preferred_majors": [s.strip() for s in st.session_state.get("zj_majors", "").split(",") if s.strip()],
        "school_levels": st.session_state.get("zj_levels", []),
        "preferred_cities": [c.strip() for c in st.session_state.get("zj_cities", "").split(",") if c.strip()],
        "accept_private": st.session_state.get("zj_private", True),
        "excluded_regions": st.session_state.get("zj_excl", []),
    }


def _render_working() -> None:
    form_filled = bool(st.session_state.get("zj_form_filled", False))

    with st.sidebar:
        st.header("🔑 AI 设置")
        api_key = (st.text_input("百炼 API Key（AI 填写 / 报告 / 解释需要）", type="password",
                                 placeholder="sk-...", key="zj_api_key").strip() or None)
        st.caption("⚠️ 本工具及 AI 建议仅供参考，最终以官方为准，由用户自行负责。")
        if form_filled:
            st.divider()
            st.header("📋 志愿信息（可手动修改）")
            st.number_input("全省位次", 1, svc.RANK_MAX, step=100, key="zj_rank")
            st.number_input("总分（选填）", 200, 750, value=None, step=1, key="zj_total_score", placeholder="选填")
            st.multiselect("选考科目（选 3 门）", SUBJECT_OPTIONS, max_selections=3, key="zj_subjects")
            st.selectbox("主排序", PRIORITIES, key="zj_priority")
            st.selectbox("风险偏好", ["激进", "均衡", "保守"], key="zj_risk")
            st.divider()
            st.markdown("**可选偏好**")
            st.text_input("想读的专业方向（逗号分隔）", key="zj_majors", placeholder="如 计算机, 金融")
            st.multiselect("学校层次", ["985", "211", "双一流"], key="zj_levels")
            st.text_input("偏好城市（逗号分隔）", key="zj_cities", placeholder="如 杭州, 上海")
            st.checkbox("接受民办院校", value=True, key="zj_private")
            st.multiselect("排除省份（不想去的）", EXCLUDE_OPTIONS, key="zj_excl")
        else:
            st.divider()
            st.info("先在右侧用一句话告诉小明你的情况，他帮你填好；填好后这里可手动微调。")

    nav1, _ = st.columns([1.4, 6])
    with nav1:
        if st.button("← 重新做兴趣问卷", key="zj_to_profiling"):
            st.session_state["zj_stage"] = "profiling"
            st.rerun()

    _render_fill_assistant(api_key)
    if not form_filled:
        return

    st.info("信息已填好（左侧可手动修改）。点「🚀 生成志愿」，改了再点一次即可重新生成。")
    if st.button("🚀 生成志愿", type="primary", key="zj_generate"):
        st.session_state["zj_generated"] = True
    if not st.session_state.get("zj_generated"):
        return

    form = _collect_form()
    err = svc.validate_form(form)
    if err:
        st.warning(err)
        return
    try:
        reco = svc.recommend(form)
    except Exception as e:  # noqa: BLE001
        st.error(f"生成失败：{e}")
        return
    st.session_state["_zj_advisor_ctx"] = svc.advisor_ctx(reco)

    _render_results(reco, form)
    st.divider()
    _render_explain(api_key, form)


def _render_fill_assistant(api_key) -> None:
    if st.session_state.get("zj_form_filled"):
        return
    st.subheader("② 告诉小明你的情况，他帮你填")
    if "zj_fill_chat" not in st.session_state:
        st.session_state["zj_fill_chat"] = [{"role": "assistant", "content": _FILL_WELCOME}]

    box = st.container(height=280)
    with box:
        for m in st.session_state["zj_fill_chat"]:
            with st.chat_message(m["role"]):
                st.write(m["content"])

    n = st.session_state.get("zj_fill_input_n", 0)
    c1, c2 = st.columns([7, 1])
    with c1:
        msg = st.text_input("输入", key=f"zj_fill_msg_{n}",
                            placeholder="如：位次8000，物理化学生物，专业优先，想学计算机", label_visibility="collapsed")
    with c2:
        send = st.button("发送", use_container_width=True, key="zj_fill_send")
    if send and msg.strip():
        st.session_state["_zj_fill_pending"] = msg.strip()
        st.session_state["zj_fill_input_n"] = n + 1
        st.rerun()

    pending = st.session_state.pop("_zj_fill_pending", None)
    if pending:
        if not api_key:
            st.warning("请在左侧填入百炼 API Key 才能用 AI 辅助填表")
        else:
            st.session_state["zj_fill_chat"].append({"role": "user", "content": pending})
            with box:
                with st.chat_message("user"):
                    st.write(pending)
                with st.chat_message("assistant"):
                    resp = st.write_stream(chat_with_advisor(
                        st.session_state["zj_fill_chat"], profile_ctx=_collect_form(),
                        recommendation_ctx=None, api_key=api_key, province_config=svc.PROVINCE_CONFIG))
            st.session_state["zj_fill_chat"].append({"role": "assistant", "content": resp})
            params = svc.parse_advisor_params(resp)
            if params:
                st.session_state["zj_fill_params"] = params
            st.rerun()

    params = st.session_state.get("zj_fill_params")
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
        if st.button("确认填入表单", type="primary", key="zj_fill_confirm"):
            fill = svc.params_to_form(params)
            pf = {}
            if "rank" in fill: pf["zj_rank"] = fill["rank"]
            if "total_score" in fill: pf["zj_total_score"] = fill["total_score"]
            if "selected_subjects" in fill: pf["zj_subjects"] = fill["selected_subjects"]
            if "main_priority" in fill: pf["zj_priority"] = fill["main_priority"]
            if "risk_preference" in fill: pf["zj_risk"] = fill["risk_preference"]
            if "preferred_majors" in fill: pf["zj_majors"] = ", ".join(fill["preferred_majors"])
            if "preferred_cities" in fill: pf["zj_cities"] = ", ".join(fill["preferred_cities"])
            st.session_state["_zj_pending_fill"] = pf
            st.session_state["zj_form_filled"] = True
            st.session_state.pop("zj_fill_params", None)
            st.rerun()


def _render_results(reco: dict, form: dict) -> None:
    stats = reco["stats"]
    st.subheader("③ 推荐志愿（学校+专业）")
    c = st.columns(5)
    c[0].metric("志愿数", stats.get("total", 0))
    c[1].metric("冲", stats.get("冲", 0)); c[2].metric("稳", stats.get("稳", 0))
    c[3].metric("保", stats.get("保", 0)); c[4].metric("垫", stats.get("垫", 0))
    if form["main_priority"] == "专业优先" and form["preferred_cities"]:
        st.info(f"ℹ️ 「专业优先」下 **{'、'.join(form['preferred_cities'])}** 只作同档内次要排序，不强制排前。")

    st.dataframe(
        pd.DataFrame(svc.volunteer_rows(reco["volunteers"], form["main_priority"])),
        width="stretch", hide_index=True, height=560,
        column_config={
            "序号": st.column_config.NumberColumn(width="small"),
            "层级": st.column_config.TextColumn(width="small"),
            "学校": st.column_config.TextColumn(width="medium"),
            "专业": st.column_config.TextColumn(width="large"),
            "均值位次": st.column_config.NumberColumn(width="small", help="近三年加权录取位次"),
            "gap": st.column_config.NumberColumn(width="small", help="均值位次 - 你的位次，正数更安全"),
        },
    )
    reserve = reco.get("reserve", [])
    if reserve:
        with st.expander(f"备选池（高危冲 / 数据不足，{len(reserve)} 条）"):
            st.dataframe(pd.DataFrame(svc.volunteer_rows(reserve, form["main_priority"])),
                         width="stretch", hide_index=True)

    pool = reco.get("_pool", [])
    if pool:
        with st.expander(f"候选池（符合你筛选条件的全部学校+专业，{len(pool)} 条）"):
            st.dataframe(pd.DataFrame(svc.candidate_rows(pool)), width="stretch", hide_index=True)

    st.caption("说明：浙江志愿单位是「学校+专业」，「均值位次」按 2025:0.5/2024:0.3/2023:0.2 加权。"
               "改了左侧信息后再点「🚀 生成志愿」即可重新生成。")


def _render_explain(api_key, form: dict) -> None:
    ctx = st.session_state.get("_zj_advisor_ctx")
    if not (ctx and ctx.get("volunteers")):
        return
    vols = ctx["volunteers"]
    main_priority = form["main_priority"]
    st.subheader("⑤ AI 解读")
    if "zj_ai_chat" not in st.session_state:
        st.session_state["zj_ai_chat"] = []

    cA, cB = st.columns([1, 2])
    with cA:
        if st.button("📊 解读整体方案", use_container_width=True, key="zj_btn_report"):
            if not api_key:
                st.warning("请先填入左侧百炼 API Key")
            else:
                st.session_state["_zj_ai_fn"] = {"fn": "report", "label": "📊 解读整体方案",
                                                 "volunteers": vols, "stats": ctx["stats"], "profile": form}
                st.rerun()
    with cB:
        labels = [f"{v.get('volunteer_no')}. {v.get('school_name')} · {v.get('major_name')}" for v in vols]
        sel = st.selectbox("选一条志愿解释", labels, label_visibility="collapsed", key="zj_vol_select")
        if st.button("💬 解释这条志愿", use_container_width=True, key="zj_btn_explain"):
            if not api_key:
                st.warning("请先填入左侧百炼 API Key")
            else:
                v = vols[labels.index(sel) if sel in labels else 0]
                st.session_state["_zj_ai_fn"] = {"fn": "explain",
                                                 "label": f"💬 解释第{v.get('volunteer_no')}条：{v.get('school_name')}·{v.get('major_name')}",
                                                 "volunteer": v, "profile": form}
                st.rerun()

    box = st.container(height=300)
    with box:
        for m in st.session_state["zj_ai_chat"]:
            with st.chat_message(m["role"]):
                st.write(m["content"])

    n = st.session_state.get("zj_ai_input_n", 0)
    c1, c2 = st.columns([7, 1])
    with c1:
        msg = st.text_input("输入", key=f"zj_ai_msg_{n}",
                            placeholder="问问这套方案，如「冲的会不会太多」", label_visibility="collapsed")
    with c2:
        send = st.button("发送", use_container_width=True, key="zj_ai_send")
    if send and msg.strip():
        st.session_state["_zj_pending_msg"] = msg.strip()
        st.session_state["zj_ai_input_n"] = n + 1
        st.rerun()

    fn = st.session_state.pop("_zj_ai_fn", None)
    pending = st.session_state.pop("_zj_pending_msg", None)

    if fn:
        if not api_key:
            st.warning("请先填入左侧百炼 API Key")
        else:
            st.session_state["zj_ai_chat"].append({"role": "user", "content": fn["label"]})
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
            st.session_state["zj_ai_chat"].append({"role": "assistant", "content": resp or "⚠️ 无返回"})
            st.rerun()

    if pending:
        if not api_key:
            st.warning("请先填入左侧百炼 API Key")
        else:
            st.session_state["zj_ai_chat"].append({"role": "user", "content": pending})
            with box:
                with st.chat_message("user"):
                    st.write(pending)
                with st.chat_message("assistant"):
                    sr = search_web(pending) if should_search(pending) else None
                    resp = st.write_stream(chat_with_advisor(
                        st.session_state["zj_ai_chat"], profile_ctx=form,
                        recommendation_ctx=ctx, search_results=sr,
                        api_key=api_key, province_config=svc.PROVINCE_CONFIG))
            st.session_state["zj_ai_chat"].append({"role": "assistant", "content": resp})
            st.rerun()
