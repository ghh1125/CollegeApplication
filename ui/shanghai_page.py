"""上海志愿填报页（3+3，院校专业组）——5 模块工作流。

工作流（session_state["sh_stage"]）：
  entry      入口：两个按钮 [我没想好选什么] / [我有清晰目标]
  profiling  ① 用户画像：聊兴趣/性格→推荐专业方向（只建议，不填表）
  working    ② 填志愿信息 → ③ 生成志愿 → ④ 改需求重生成 → ⑤ 解释单条/全部

设计要点：
  - ① 只给建议，不自动填②的表单；②的所有信息用户自己填。
  - 院校专业组推荐（24 个），按年分块、不跨年加权；专业偏好软排序。
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from db import get_conn
from src.common.input.llm import (
    chat_with_advisor,
    explain_volunteer,
    generate_overall_report,
    search_web,
    should_search,
)
from src.common.input.user_profile import QUESTIONS as _PF_QUESTIONS, analyze_questionnaire
from src.shanghai.config import PROVINCE_CONFIG as _SH_CONFIG
from src.shanghai.input.profile import (
    CityPreference,
    MajorPreference,
    Preferences,
    SchoolPreference,
    StudentProfile,
)
from src.shanghai.input.filter import (
    filter_by_city,
    filter_by_constraints,
    filter_by_school_level,
    filter_by_subject,
    resolve_school_city,
)
from src.shanghai.allocation.recommend import build_recommendations

SUBJECT_OPTIONS = ["物理", "化学", "生物", "思想政治", "历史", "地理"]
YEARS = (2025, 2024, 2023)
PRIORITIES = ["请选择…", "学校优先", "城市优先", "专业优先"]


# ─────────────────────────────────────────────────────────────────────────────
# 展示辅助
# ─────────────────────────────────────────────────────────────────────────────

def _member_majors(group: dict) -> list[str]:
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
        rows.append({
            "序号": g.get("volunteer_no") or idx,
            "层级": gi.get("tier", ""),
            "学校": g.get("school_name", ""),
            "城市": g.get("school_city") or resolve_school_city(g.get("school_name", "")),
            "专业组": f"{g.get('sg_name', '')}组",
            "选科要求": g.get("sg_info", "") or "不限",
            "专业匹配": g.get("_major_tag", ""),
            "投档位次": gi.get("weighted_avg"),
            "gap": gi.get("gap"),
            "组内专业": inner,
        })
    return pd.DataFrame(rows)


def _member_trends_df(group: dict) -> pd.DataFrame:
    rows = []
    for m in group.get("_members", []):
        if m.get("major_code") == "__GROUP__":
            continue
        name = (m.get("major_name") or "").strip()
        if not name or "合计" in name:
            continue
        t = m.get("_trend", {}) or {}
        seq = [(y, t.get(y)) for y in (2023, 2024, 2025) if t.get(y)]
        arrow = ""
        if len(seq) >= 2:
            first, last = seq[0][1], seq[-1][1]
            arrow = "↓更难" if last < first else ("↑更易" if last > first else "→持平")
        rows.append({"专业": name, "2025位次": t.get(2025), "2024位次": t.get(2024),
                     "2023位次": t.get(2023), "趋势": arrow})
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.drop_duplicates(subset=["专业"]).reset_index(drop=True)
    return df


def _render_year_block(reco: dict, year: int, primary: bool) -> None:
    stats = reco["stats"]
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("志愿组数", stats["total"])
    c2.metric("冲", stats["冲"]); c3.metric("稳", stats["稳"])
    c4.metric("保", stats["保"]); c5.metric("垫", stats["垫"])
    if not primary:
        st.caption(f"⚠️ {year} 年为历史参考：当年的专业组与投档位次，组的编排与今年不同，**不能直接照填**，仅供看趋势。")
    st.dataframe(
        _groups_df(reco["volunteers"]), width="stretch", hide_index=True, height=520,
        column_config={
            "序号": st.column_config.NumberColumn(width="small"),
            "层级": st.column_config.TextColumn(width="small"),
            "学校": st.column_config.TextColumn(width="medium"),
            "城市": st.column_config.TextColumn(width="small"),
            "专业组": st.column_config.TextColumn(width="small"),
            "选科要求": st.column_config.TextColumn(width="small"),
            "专业匹配": st.column_config.TextColumn(width="small"),
            "投档位次": st.column_config.NumberColumn(width="small", help=f"{year} 年官方该专业组投档最低位次（进组门槛）"),
            "gap": st.column_config.NumberColumn(width="small", help="投档位次 - 你的位次，正数更安全"),
            "组内专业": st.column_config.TextColumn(width="large"),
        },
    )
    reserve = reco.get("reserve", [])
    if reserve:
        with st.expander(f"{year} 备选池（高危冲 / 数据不足，{len(reserve)} 组）"):
            st.dataframe(_groups_df(reserve), width="stretch", hide_index=True)


def _group_as_volunteer(g: dict) -> dict:
    members = _member_majors(g)
    return {
        "volunteer_no": g.get("volunteer_no"),
        "school_name": g.get("school_name", ""),
        "school_city": g.get("school_city", ""),
        "major_name": f"{g.get('sg_name','')}组（{('、'.join(members[:4])) if members else '组内专业待补充'}）",
        "gap_info": g.get("gap_info", {}),
    }


def _reset_all() -> None:
    for k in list(st.session_state.keys()):
        if k.startswith("sh_") or k.startswith("_sh_"):
            del st.session_state[k]


# ─────────────────────────────────────────────────────────────────────────────
# 入口
# ─────────────────────────────────────────────────────────────────────────────

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

    # 对话/工作流可能写入的表单预填，须在 widget 渲染前应用
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
        st.markdown(
            "#### 🤔 我还没想好选什么\n\n"
            "先和小明聊聊**兴趣、性格、擅长的科目**，帮你找几个合适的专业方向，再去填志愿。"
        )
        if st.button("没想好，先聊聊兴趣", use_container_width=True, key="sh_go_profiling"):
            st.session_state["sh_stage"] = "profiling"
            st.rerun()
    with c2:
        st.markdown(
            "#### 🎯 我有清晰目标\n\n"
            "已经知道想读什么方向，直接填**位次、选考科目、偏好**，立刻生成志愿方案。"
        )
        if st.button("有目标，直接填志愿", use_container_width=True, type="primary", key="sh_go_working"):
            st.session_state["sh_stage"] = "working"
            st.rerun()


# ─────────────────────────────────────────────────────────────────────────────
# ① 用户画像（兴趣引导）
# ─────────────────────────────────────────────────────────────────────────────

def _render_profiling() -> None:
    st.subheader("① 兴趣问卷 · 帮你找专业方向")
    with st.sidebar:
        st.header("🔑 AI 设置")
        api_key = (st.text_input("百炼 API Key（分析推荐需要）", type="password",
                                 placeholder="sk-...", key="sh_api_key").strip() or None)
        st.caption("⚠️ AI 建议仅供参考，由用户自行判断。")

    total = len(_PF_QUESTIONS)
    step = st.session_state.get("sh_pf_step", 0)          # 当前题号
    answers: dict = st.session_state.setdefault("sh_pf_answers", {})

    nav1, nav2 = st.columns([1, 1])
    with nav1:
        if st.button("← 返回入口", key="sh_pf_back"):
            st.session_state["sh_stage"] = "entry"
            st.rerun()
    with nav2:
        if st.button("跳过问卷，直接填志愿 →", key="sh_pf_skip"):
            st.session_state["sh_stage"] = "working"
            st.rerun()

    # ── 答题阶段（一次一题）──────────────────────────────────────────────────
    if step < total:
        q = _PF_QUESTIONS[step]
        st.progress(step / total, text=f"第 {step + 1} / {total} 题")
        st.markdown(f"### {q['question']}")
        opts = q["options"]
        labels = [f"{k}. {v}" for k, v in opts.items()]
        prev = answers.get(q["key"])
        idx = list(opts).index(prev) if prev in opts else 0
        choice = st.radio("选一个最接近的", labels, index=idx, key=f"sh_pf_q{step}",
                          label_visibility="collapsed")
        chosen_key = choice.split(".", 1)[0]

        b1, b2, _ = st.columns([1, 1, 5])
        with b1:
            if step > 0 and st.button("← 上一题", key=f"sh_pf_prev{step}"):
                answers[q["key"]] = chosen_key
                st.session_state["sh_pf_step"] = step - 1
                st.rerun()
        with b2:
            last = step == total - 1
            if st.button("看推荐 ✓" if last else "下一题 →", type="primary", key=f"sh_pf_next{step}"):
                answers[q["key"]] = chosen_key
                st.session_state["sh_pf_step"] = step + 1
                st.rerun()
        return

    # ── 全部答完：LLM 分析推荐 ───────────────────────────────────────────────
    st.success("问卷完成！下面是基于你回答的专业方向建议（仅供参考）。")
    st.caption("方向只是参考，最终读什么由你定。看完点下面「去填志愿信息」自己填表。")

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
# ② 填信息 ③ 生成 ④ 改需求 ⑤ 解释
# ─────────────────────────────────────────────────────────────────────────────

def _render_working() -> None:
    # ── 侧边栏：API Key + 表单（② 用户自己填）──────────────────────────────
    with st.sidebar:
        st.header("🔑 AI 设置")
        api_key = (st.text_input("百炼 API Key（报告 / 解释需要）", type="password",
                                 placeholder="sk-...", key="sh_api_key").strip() or None)
        st.caption("⚠️ 本工具及 AI 建议仅供参考，最终以官方为准，由用户自行负责。")

        st.divider()
        st.header("📋 ② 填写志愿信息")
        rank = st.number_input("全市位次", 1, 200_000, value=8000, step=100, key="sh_rank")
        selected = st.multiselect("选考科目（选 3 门）", SUBJECT_OPTIONS, max_selections=3,
                                  key="sh_subjects", help="从 物理/化学/生物/思想政治/历史/地理 选 3 门")
        main_priority = st.selectbox("主排序", PRIORITIES, index=0, key="sh_priority")
        st.caption("「专业优先」按已解析的组内专业做软排序。")
        risk_preference = st.selectbox("风险偏好", ["激进", "均衡", "保守"], index=1, key="sh_risk")
        st.divider()
        st.markdown("**可选偏好**")
        preferred_majors = [s.strip() for s in st.text_input(
            "想读的专业方向（逗号分隔）", key="sh_majors", placeholder="如 计算机, 金融").split(",") if s.strip()]
        school_levels = st.multiselect("学校层次", ["985", "211", "双一流"], key="sh_levels")
        preferred_cities = [c.strip() for c in st.text_input(
            "偏好城市（逗号分隔）", key="sh_cities", placeholder="如 上海, 南京").split(",") if c.strip()]

    # ── 顶部导航 ─────────────────────────────────────────────────────────────
    nav1, nav2, _ = st.columns([1.4, 1, 5])
    with nav1:
        if st.button("← 重新做兴趣引导", key="sh_to_profiling"):
            st.session_state["sh_stage"] = "profiling"
            st.rerun()

    st.info("在左侧填写位次、选考 3 门、主排序等信息，然后点下面「🚀 生成志愿」。改了信息再点一次即可重新生成。")

    # ── ③ 生成（按钮触发，可重复 = ④ 改需求重生成）──────────────────────────
    gen = st.button("🚀 生成志愿", type="primary", key="sh_generate")
    if gen:
        st.session_state["sh_generated"] = True
    if not st.session_state.get("sh_generated"):
        return

    if main_priority == "请选择…":
        st.warning("请在左侧选择「主排序」。"); return
    if len(selected) != 3:
        st.warning("请在左侧选择恰好 3 门选考科目。"); return

    try:
        profile = StudentProfile(
            rank=int(rank), selected_subjects=selected, risk_preference=risk_preference,
            preferences=Preferences(
                cities=CityPreference(preferred=preferred_cities),
                majors=MajorPreference(preferred_majors=preferred_majors),
                schools=SchoolPreference(preferred_levels=school_levels),
            ),
        )
    except Exception as e:  # noqa: BLE001
        st.error(f"输入有误：{e}"); return

    recos = {}
    with get_conn("shanghai") as conn:
        for _Y in YEARS:
            _elig, _ = filter_by_subject(profile, year=_Y, conn=conn)
            _final, _ = filter_by_constraints(_elig, profile)
            if school_levels:
                _final, _ = filter_by_school_level(_final, school_levels)
            if preferred_cities and main_priority == "城市优先":
                _final, _ = filter_by_city(_final, preferred_cities)
            recos[_Y] = build_recommendations(
                _final, profile, main_priority=main_priority,
                preferred_majors=preferred_majors, preferred_categories=[], preferred_schools=[],
                preferred_cities=preferred_cities or None, risk_preference=risk_preference,
                year=_Y, conn=conn,
            )
    reco = recos[YEARS[0]]
    st.session_state["_sh_advisor_ctx"] = {
        "volunteers": [_group_as_volunteer(g) for g in reco["volunteers"]],
        "stats": reco["stats"],
    }
    profile_ctx = {
        "rank": int(rank), "selected_subjects": selected, "preferred_majors": preferred_majors,
        "preferred_cities": preferred_cities, "main_priority": main_priority,
        "risk_preference": risk_preference,
    }

    # ── 结果（③）+ 按年分块 ──────────────────────────────────────────────────
    st.subheader("③ 推荐院校专业组")
    st.info(
        "上海专业组**每年重新编排、组号跨年不是同一个组**，故不跨年加权，按年份分别给出：\n\n"
        "- **2025 推荐**：今年实际填报的 24 个院校专业组方案\n"
        "- **2024 / 2023 参考**：当年同位次能选到的组，看趋势用，**不能直接照填**"
    )
    if main_priority == "专业优先" and preferred_cities:
        st.info(
            f"ℹ️ 「专业优先」下 **{('、'.join(preferred_cities))}** 只作同档内次要排序，不强制排前。"
            "想让该城市优先，请把主排序改成「城市优先」。"
        )
    tab25, tab24, tab23 = st.tabs(["2025 推荐（按此填报）", "2024 参考", "2023 参考"])
    for _tab, _Y in ((tab25, 2025), (tab24, 2024), (tab23, 2023)):
        with _tab:
            _render_year_block(recos[_Y], _Y, primary=(_Y == 2025))

    # ── 组内专业历年趋势 ─────────────────────────────────────────────────────
    _v25 = recos[2025]["volunteers"]
    if _v25:
        st.divider()
        st.markdown("**📈 某个专业近三年要多少位次能进**")
        st.caption("选一个学校的专业组，看组里每个专业最近三年的录取位次，判断越来越难考还是好考。"
                   "上海按专业组整体投档，同一年同组专业位次相同。")
        _labels = [f"{g.get('volunteer_no')}. {g['school_name']} {g.get('sg_name','')}组" for g in _v25]
        _sel = st.selectbox("选一个学校专业组", _labels, key="sh_trend_sel")
        _tdf = _member_trends_df(_v25[_labels.index(_sel) if _sel in _labels else 0])
        if _tdf.empty:
            st.info("该组暂无组内专业明细（待补充）。")
        else:
            st.dataframe(_tdf, width="stretch", hide_index=True)

    # ── ⑤ 解释 / 问答 ────────────────────────────────────────────────────────
    st.divider()
    _render_explain(api_key, profile_ctx, main_priority)

    st.caption(
        "说明：上海填报单位是院校专业组（非单专业）。「投档位次」为各专业组当年官方投档最低位次，"
        "「gap」= 投档位次 − 你的位次。改了左侧信息后再点「🚀 生成志愿」即可重新生成。"
    )


def _render_explain(api_key, profile_ctx: dict, main_priority: str) -> None:
    """⑤ 解释模块：解释某条 / 全部 + 针对方案的问答。"""
    ctx = st.session_state.get("_sh_advisor_ctx")
    if not (ctx and ctx.get("volunteers")):
        return
    vols = ctx["volunteers"]
    st.subheader("⑤ AI 解读")

    if "sh_ai_chat" not in st.session_state:
        st.session_state["sh_ai_chat"] = []

    cA, cB = st.columns([1, 2])
    with cA:
        if st.button("📊 解读整体方案", use_container_width=True, key="sh_btn_report"):
            if not api_key:
                st.warning("请先填入左侧百炼 API Key")
            else:
                st.session_state["_sh_ai_fn"] = {
                    "fn": "report", "label": "📊 解读整体方案",
                    "volunteers": vols, "stats": ctx["stats"], "profile": profile_ctx}
                st.rerun()
    with cB:
        _labels = [f"{v.get('volunteer_no')}. {v.get('school_name')} · {v.get('major_name')}" for v in vols]
        _sel = st.selectbox("选一条志愿解释", _labels, label_visibility="collapsed", key="sh_vol_select")
        if st.button("💬 解释这个专业组", use_container_width=True, key="sh_btn_explain"):
            if not api_key:
                st.warning("请先填入左侧百炼 API Key")
            else:
                _v = vols[_labels.index(_sel) if _sel in _labels else 0]
                st.session_state["_sh_ai_fn"] = {
                    "fn": "explain", "label": f"💬 解释第{_v.get('volunteer_no')}组：{_v.get('school_name')}",
                    "volunteer": _v, "profile": profile_ctx}
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
                            placeholder="问问这套方案，如「冲的会不会太多」「南大那条值得冲吗」", label_visibility="collapsed")
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
                    sr = None
                    if should_search(pending):
                        with st.spinner("正在搜索最新资料…"):
                            sr = search_web(pending)
                    resp = st.write_stream(chat_with_advisor(
                        st.session_state["sh_ai_chat"], profile_ctx=profile_ctx,
                        recommendation_ctx=ctx, search_results=sr,
                        api_key=api_key, province_config=_SH_CONFIG))
            st.session_state["sh_ai_chat"].append({"role": "assistant", "content": resp})
            st.rerun()
