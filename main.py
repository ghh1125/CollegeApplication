"""高考志愿推荐系统 —— 入口：落地页选省份 + 路由到各省页面。

运行：streamlit run main.py

各省的具体功能在 src/<province>/ 实现，展示在 ui/<province>_page.py。
本文件只负责：落地页（选省份）+ 路由。
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

st.set_page_config(page_title="高考志愿推荐系统", page_icon="🎓", layout="wide")

# ─── 省份定义 ──────────────────────────────────────────────────────────────────

_PROVINCE_GROUPS = [
    ("已支持省份", [
        ("浙江", "zhejiang", True),
    ]),
]

# 已接入的省份 → 渲染函数
_PROVINCE_PAGES = {
    "zhejiang": ("ui.zhejiang_page", "render"),
}


# ─── 落地页 ────────────────────────────────────────────────────────────────────

def _show_landing() -> None:
    st.title("高考志愿推荐系统")
    st.caption("选择你的省份，进入志愿推荐")
    st.info(
        "**使用前请阅读**\n\n"
        "本工具基于历史录取数据生成志愿参考方案，存在以下局限性，请知悉：\n\n"
        "- **数据存在滞后和缺失**：历史位次每年波动，部分学校/专业数据可能不完整或有误差\n"
        "- **仅供参考，不保证录取**：推荐结果不构成任何录取承诺，最终是否被录取以各省招生院校为准\n"
        "- **请以官方数据为准**：填报前务必核对所在省份教育考试院、学校招生章程及专业录取规则的官方公告\n"
        "- **AI 建议可能出错**：AI 对话助手的分析仅供参考，重要决策请结合家长、老师等多方意见综合判断\n\n"
        "志愿填报是人生重要节点，本工具只是辅助工具，最终决定权在你自己。"
    )
    st.divider()
    for region_name, provinces in _PROVINCE_GROUPS:
        st.markdown(f"**{region_name}**")
        cols = st.columns(8)
        for idx, (name, slug, available) in enumerate(provinces):
            with cols[idx % 8]:
                if available:
                    if st.button(name, key=f"prov_{slug}", use_container_width=True):
                        st.session_state["_province"] = slug
                        st.rerun()
                else:
                    st.button(name, key=f"prov_{slug}", use_container_width=True,
                              disabled=True, help="即将支持")
        st.write("")


# ─── 省份路由 ──────────────────────────────────────────────────────────────────

if "_province" not in st.session_state:
    _show_landing()
    st.stop()

_selected_province: str = st.session_state["_province"]

if _selected_province in _PROVINCE_PAGES:
    _module, _func = _PROVINCE_PAGES[_selected_province]
    import importlib
    getattr(importlib.import_module(_module), _func)()
    st.stop()

# 未接入的省份兜底
st.info("该省份数据暂未接入，敬请期待。")
if st.button("← 返回选择省份"):
    del st.session_state["_province"]
    st.rerun()
st.stop()
