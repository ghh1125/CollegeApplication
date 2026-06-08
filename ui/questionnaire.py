"""模块① 兴趣问卷 —— 三省共用的渲染组件。

一题一题选 ABCD（每题都带「其他（自己填）」），可填家庭所在城市（用于「离家近」建议），
答完调 src 分析，给出专业方向 + 城市建议 + 填报倾向。页面只传 prefix 和 api_key。
"""

from __future__ import annotations

import streamlit as st

from src.common.input.user_profile import QUESTIONS, analyze_questionnaire

_OTHER = "其他（自己填）"


def render(prefix: str, api_key) -> None:
    """渲染整个问卷阶段。prefix 为省份键前缀（zj/js/sh）。"""
    stage_key = f"{prefix}_stage"
    total = len(QUESTIONS)
    step = st.session_state.get(f"{prefix}_pf_step", 0)
    answers: dict = st.session_state.setdefault(f"{prefix}_pf_answers", {})   # key -> "A".."D"/"其他"
    customs: dict = st.session_state.setdefault(f"{prefix}_pf_custom", {})     # key -> 自填文本

    st.subheader("① 兴趣问卷 · 帮你找专业方向")
    st.text_input("你家在哪个城市/省份？（选填，用于「离家近」的建议）",
                  key=f"{prefix}_pf_home", placeholder="如 杭州 / 江苏")

    nav1, nav2 = st.columns(2)
    with nav1:
        if st.button("← 返回入口", key=f"{prefix}_pf_back"):
            st.session_state[stage_key] = "entry"
            st.rerun()
    with nav2:
        if st.button("跳过问卷，直接填志愿 →", key=f"{prefix}_pf_skip"):
            st.session_state[stage_key] = "working"
            st.rerun()

    # ── 答题：一次一题 ──────────────────────────────────────────────────────
    if step < total:
        q = QUESTIONS[step]
        st.progress(step / total, text=f"第 {step + 1} / {total} 题")
        st.markdown(f"### {q['question']}")
        opts = q["options"]
        labels = [f"{k}. {v}" for k, v in opts.items()] + [_OTHER]
        prev = answers.get(q["key"])
        if prev == "其他":
            idx = len(labels) - 1
        elif prev in opts:
            idx = list(opts).index(prev)
        else:
            idx = 0
        choice = st.radio("选一个最接近的", labels, index=idx, key=f"{prefix}_pf_q{step}",
                          label_visibility="collapsed")
        is_other = choice == _OTHER
        chosen = "其他" if is_other else choice.split(".", 1)[0]
        custom_text = ""
        if is_other:
            custom_text = st.text_input("写下你的答案", value=customs.get(q["key"], ""),
                                        key=f"{prefix}_pf_qtext{step}", placeholder="说说你的真实想法…")

        b1, b2, _ = st.columns([1, 1, 5])
        with b1:
            if step > 0 and st.button("← 上一题", key=f"{prefix}_pf_prev{step}"):
                answers[q["key"]] = chosen
                if is_other:
                    customs[q["key"]] = custom_text
                st.session_state[f"{prefix}_pf_step"] = step - 1
                st.rerun()
        with b2:
            last = step == total - 1
            if st.button("看推荐 ✓" if last else "下一题 →", type="primary", key=f"{prefix}_pf_next{step}"):
                if is_other and not custom_text.strip():
                    st.warning("选了「其他」请填一下你的答案，或换一个选项。")
                else:
                    answers[q["key"]] = chosen
                    if is_other:
                        customs[q["key"]] = custom_text
                    st.session_state[f"{prefix}_pf_step"] = step + 1
                    st.rerun()
        return

    # ── 答完：调 src 分析 ───────────────────────────────────────────────────
    st.success("问卷完成！下面是基于你回答的综合建议（专业方向 + 城市 + 填报倾向，仅供参考）。")
    st.caption("这些只是参考，最终读什么由你定。看完点「去填志愿信息」，AI 会帮你填表。")
    if f"{prefix}_pf_result" not in st.session_state:
        if not api_key:
            st.warning("请在左侧填入百炼 API Key，以生成建议。")
        else:
            payload = []
            for q in QUESTIONS:
                ch = answers.get(q["key"], "")
                if ch == "其他":
                    payload.append({"question": q["question"], "choice": "其他",
                                    "answer": f"自己填：{customs.get(q['key'], '')}"})
                else:
                    payload.append({"question": q["question"], "choice": ch,
                                    "answer": f"选了：{q['options'].get(ch, '')}"})
            home = st.session_state.get(f"{prefix}_pf_home", "")
            with st.chat_message("assistant"):
                try:
                    resp = st.write_stream(analyze_questionnaire(payload, home=home, api_key=api_key))
                except Exception as e:  # noqa: BLE001
                    resp = f"⚠️ 生成失败：{e}"; st.write(resp)
            st.session_state[f"{prefix}_pf_result"] = resp
    else:
        with st.chat_message("assistant"):
            st.write(st.session_state[f"{prefix}_pf_result"])

    r1, r2, _ = st.columns([1.2, 1.2, 4])
    with r1:
        if st.button("↺ 重新答题", key=f"{prefix}_pf_redo"):
            for k in (f"{prefix}_pf_step", f"{prefix}_pf_answers", f"{prefix}_pf_custom", f"{prefix}_pf_result"):
                st.session_state.pop(k, None)
            st.rerun()
    with r2:
        if st.button("去填志愿信息 →", type="primary", key=f"{prefix}_pf_to_form"):
            st.session_state[stage_key] = "working"
            st.rerun()
