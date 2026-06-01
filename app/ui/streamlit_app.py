"""Streamlit 志愿筛选界面。运行：streamlit run app/ui/streamlit_app.py"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.models.profile import (
    Constraints,
    CityPreference,
    MajorPreference,
    Preferences,
    SchoolPreference,
    StudentProfile,
)
from app.pipeline.filter import (
    resolve_school_city,
    filter_by_city,
    filter_by_constraints,
    filter_by_major_keywords,
    filter_by_school_level,
    filter_by_subject,
)
from app.pipeline.recommend import build_recommendations, history_rank_columns
from app.ui.form_helpers import (
    format_sort_reason_for_display,
    normalize_items,
    queue_ai_message,
    split_major_preferences,
)
from app.db import get_conn
from app.llm.explain import (
    chat_with_advisor, search_web, should_search,
    explain_volunteer, generate_overall_report,
)


# ─── 页面配置 ────────────────────────────────────────────────────────────────

st.set_page_config(page_title="浙江高考志愿筛选", page_icon="🎓", layout="wide")
st.title("🎓 高考志愿筛选（面向浙江高考）")

# ─── 辅助 ────────────────────────────────────────────────────────────────────

SUBJECT_ORDER = ["物理", "化学", "生物", "历史", "地理", "思想政治", "技术"]

_SUBJECT_ALIAS = {
    "政治": "思想政治", "思政": "思想政治", "马克思": "思想政治",
    "生物学": "生物",
    "信息技术": "技术", "通用技术": "技术", "信息": "技术",
}


@st.cache_data(ttl=3600)
def _load_school_detail(school_name: str) -> dict:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT school_id, summary, tags, motto, founded_year, school_type, school_nature, ruanke_rank "
            "FROM school_profile WHERE school_name = ? LIMIT 1",
            (school_name,),
        ).fetchone()
    if not row:
        return {}
    return {
        "logo_url": f"https://static-data.gaokao.cn/upload/logo/{row[0]}.jpg" if row[0] else None,
        "summary": row[1] or "",
        "tags": row[2] or "",
        "motto": row[3] or "",
        "founded_year": row[4] or "",
        "school_type": row[5] or "",
        "school_nature": row[6] or "",
        "ruanke_rank": row[7],
    }


@st.cache_data(ttl=3600)
def _load_major_detail(major_name: str) -> dict:
    from app.pipeline.rank import normalize_major_name
    norm = normalize_major_name(major_name)
    with get_conn() as conn:
        row = conn.execute(
            "SELECT summary, learn_what, career_direction FROM major_profile WHERE major_name = ? LIMIT 1",
            (norm,),
        ).fetchone()
        if row:
            return {"summary": row[0] or "", "learn_what": row[1] or "", "career_direction": row[2] or ""}
        row2 = conn.execute(
            "SELECT is_what, learn_what, do_what FROM major_description WHERE name = ? LIMIT 1",
            (norm,),
        ).fetchone()
        if row2:
            return {"summary": row2[0] or "", "learn_what": row2[1] or "", "career_direction": row2[2] or ""}
    return {}


def _show_program_detail(program: dict) -> None:
    school_name = program.get("school_name", "")
    major_name  = program.get("major_name", "")

    # ── 学校头部（始终渲染）────────────────────────────────────────────────────
    tiers = []
    if program.get("is_985"):               tiers.append("985")
    if program.get("is_211"):               tiers.append("211")
    if program.get("is_double_first_class"): tiers.append("双一流")
    tier_str = "　".join(f"`{t}`" for t in tiers) if tiers else ""

    try:
        school = _load_school_detail(school_name)
    except Exception:
        school = {}
    try:
        major = _load_major_detail(major_name)
    except Exception:
        major = {}

    col_logo, col_info = st.columns([1, 5])
    with col_logo:
        logo = school.get("logo_url")
        if logo:
            try:
                st.image(logo, width=80)
            except Exception:
                pass
    with col_info:
        st.markdown(f"## {school_name}")
        if tier_str:
            st.markdown(tier_str)
        meta = []
        if program.get("school_city"):  meta.append(f"📍 {program['school_city']}")
        if school.get("founded_year"):  meta.append(f"创办 {school['founded_year']}")
        if school.get("school_type"):   meta.append(school["school_type"])
        if school.get("ruanke_rank"):   meta.append(f"软科第 {school['ruanke_rank']}")
        if meta:
            st.caption("  ｜  ".join(meta))

    if school.get("motto"):
        st.caption(f"校训：{school['motto']}")
    if school.get("tags"):
        st.caption("🏷 " + school["tags"])
    if school.get("summary"):
        st.write(school["summary"][:320] + ("…" if len(school["summary"]) > 320 else ""))

    st.divider()

    # ── 专业（始终渲染）────────────────────────────────────────────────────────
    st.markdown(f"### 📚 {major_name}")

    history  = program.get("history") or []
    gap_info = program.get("gap_info") or {}
    if history:
        rank_by_year = {h["year"]: h.get("min_rank") for h in history}
        parts = [f"{y}年 **{rank_by_year[y]}**" for y in [2025, 2024, 2023] if rank_by_year.get(y)]
        if parts:
            st.markdown("**历史最低位次**：" + "　|　".join(parts))
    if gap_info.get("tier"):
        st.markdown(f"**录取把握**：{gap_info['tier']}　gap {gap_info.get('gap', '—')}")

    if major.get("summary"):
        st.markdown("**专业简介**")
        st.write(major["summary"][:240] + ("…" if len(major["summary"]) > 240 else ""))
    if major.get("learn_what"):
        st.markdown("**主要学什么**")
        st.write(major["learn_what"][:240] + ("…" if len(major["learn_what"]) > 240 else ""))
    if major.get("career_direction"):
        st.markdown("**就业方向**")
        st.write(major["career_direction"][:240] + ("…" if len(major["career_direction"]) > 240 else ""))
    if not school and not major:
        st.info("暂无该学校/专业的详细介绍数据。")


@st.cache_data(ttl=3600)
def _load_major_options() -> list[str]:
    """Load major names: standard catalog + all actual programs from admission_plan."""
    with get_conn() as conn:
        std = {r[0] for r in conn.execute("SELECT name FROM major_description").fetchall()}
        actual = {r[0] for r in conn.execute("SELECT DISTINCT major_name FROM admission_plan").fetchall()}
    return sorted(std | actual)


@st.cache_data(ttl=3600)
def _load_province_city_map() -> dict[str, list[str]]:
    """Return {province: sorted list of cities} from school_master."""
    _INVALID = {"军校", ""}
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT province, city FROM school_master "
            "WHERE province IS NOT NULL AND province != '' "
            "AND city IS NOT NULL AND city != '' "
            "AND province NOT IN ('军校') AND city NOT IN ('军校') "
            "ORDER BY province, city"
        ).fetchall()
    mapping: dict[str, list[str]] = {}
    for province, city in rows:
        mapping.setdefault(province, []).append(city)
    return mapping


def _fmt_req(req_json: str | None) -> str:
    try:
        req = json.loads(req_json or "{}")
    except Exception:
        return "不限"
    t = req.get("type", "NONE")
    subs = req.get("subjects", [])
    if t == "NONE":      return "不限"
    if t == "UNKNOWN":   return "❓"
    if t == "ALL_REQUIRED": return " + ".join(subs) + "（均须）"
    if t == "ANY_ONE":   return " / ".join(subs) + "（任一）"
    return " / ".join(subs) or "自定义"


def _to_df(programs: list[dict]) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "学校":      p.get("school_name", ""),
            "城市":      resolve_school_city(p.get("school_name", "")),
            "专业":      p.get("major_name", ""),
            "选科要求":  _fmt_req(p.get("subject_requirement_json")),
            "⚠":        "  ".join(p.get("_warnings") or []),
        }
        for p in programs
    ])


def _recommendation_df(programs: list[dict], main_priority: str) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "_idx":      index - 1,  # original list index, hidden in UI
            "序号":      p.get("volunteer_no") or index,
            "层级":      (p.get("gap_info") or {}).get("tier", ""),
            "学校":      p.get("school_name", ""),
            "城市":      p.get("school_city") or resolve_school_city(p.get("school_name", "")),
            "专业":      p.get("major_name", ""),
            "专业匹配":  p.get("_major_tag", ""),
            **history_rank_columns(p),
            "均值位次":  (p.get("gap_info") or {}).get("weighted_avg"),
            "gap":       (p.get("gap_info") or {}).get("gap"),
            "排序理由":  format_sort_reason_for_display(p, main_priority),
            "历史年数":  (p.get("gap_info") or {}).get("data_years"),
            "选科要求":  _fmt_req(p.get("subject_requirement_json")),
            "⚠":        "  ".join(p.get("_warnings") or []),
        }
        for index, p in enumerate(programs, start=1)
    ])


def _filter_df(df: pd.DataFrame, keyword: str) -> pd.DataFrame:
    if not keyword:
        return df
    mask = df["学校"].str.contains(keyword, na=False) | df["专业"].str.contains(keyword, na=False)
    return df[mask]


def _dynamic_text_list(
    label: str,
    key: str,
    placeholder: str,
    defaults: list[str] | None = None,
) -> list[str]:
    count_key = f"{key}_count"
    initial = defaults or [""]
    if count_key not in st.session_state:
        st.session_state[count_key] = max(1, len(initial))
        for index, value in enumerate(initial):
            st.session_state.setdefault(f"{key}_{index}", value)

    st.markdown(f"**{label}**")
    values = []
    for index in range(st.session_state[count_key]):
        values.append(
            st.text_input(
                f"{label}{index + 1}",
                key=f"{key}_{index}",
                placeholder=placeholder,
                label_visibility="collapsed",
            )
        )
    if st.button(f"+ 添加{label}", key=f"{key}_add"):
        st.session_state[count_key] += 1
        st.rerun()
    return normalize_items(values)


# ─── 预加载 + AI填报pending处理（必须在所有widget渲染前执行） ─────────────────

_all_major_options = _load_major_options()
_form_ready = bool(st.session_state.get("_form_ready", False))
# Pop the rerun flag early so it only takes effect for one run
_reco_rerun_pending = st.session_state.pop("_reco_rerun_pending", False)

if "_pending_fill" in st.session_state:
    _pf = st.session_state.pop("_pending_fill")
    for _k, _v in _pf.items():
        st.session_state[_k] = _v

# ─── 侧边栏 ──────────────────────────────────────────────────────────────────

with st.sidebar:
    st.header("🔑 AI 设置")
    _user_api_key = st.text_input(
        "百炼 API Key（必填，AI 对话 / 报告 / 解释均需要）",
        type="password",
        placeholder="sk-...",
        help="填入后才能使用 AI 解释功能。Key 仅在本次会话中使用，不会保存。",
    )
    _effective_api_key = _user_api_key.strip() or None

    st.caption(
        "⚠️ **免责声明**：本工具及 AI 建议仅供参考，不构成正式志愿填报指导。"
        "AI 可能出错，请用户结合官方政策自行核实，最终填报结果由用户本人负责，"
        "本工具概不承担任何责任。"
    )

    if _form_ready:
        st.divider()
        st.header("📋 考生信息")
        rank = st.number_input("全省位次", 1, 400_000, value=36_500, step=100, key="w_rank")
        total_score = st.number_input("总分", 200, 750, value=None, step=1, placeholder="选填", key="w_total_score")
        selected_subjects = st.multiselect(
            "选考科目（选 3 门）", SUBJECT_ORDER,
            default=[], max_selections=3,
            key="w_subjects",
        )

        st.divider()
        with st.container(border=True):
            st.markdown("**🎯 主排序**")
            main_priority = st.selectbox(
                "排序方式",
                ["请选择…", "专业优先", "学校优先", "城市优先"],
                index=0,
                key="w_main_priority",
                label_visibility="collapsed",
            )
            st.caption(
                "专业优先：先看专业匹配；未指定目标专业时，按对应学科评估排序。"
                "学校优先：先看学校排名/层次。城市优先：先看偏好城市。"
            )

        with st.container(border=True):
            st.markdown("**偏好设置**")
            selected_majors_from_list = st.multiselect(
                "偏好专业（排序优先）",
                options=_all_major_options,
                default=[],
                placeholder='搜索专业名，如"计算机"…',
                key="w_majors_list",
            )
            preferred_major_input = selected_majors_from_list + _dynamic_text_list(
                "手动补充关键词",
                "preferred_majors",
                "例如：人工智能",
            )
            limit_to_preferred_majors = bool(preferred_major_input) and st.checkbox(
                "只看这些专业",
                value=False,
                key="w_limit_majors",
                disabled=not bool(preferred_major_input),
            )
            if main_priority == "专业优先" and not preferred_major_input:
                st.caption("未填写偏好专业时，专业优先不会猜你的专业喜好，而是按各专业对应的学科评估排序。")
            elif preferred_major_input and not limit_to_preferred_majors:
                st.caption("当前这些专业只影响排序；勾选“只看这些专业”后才会过滤候选池。")

            _province_city_map = _load_province_city_map()
            _all_cities_flat = sorted({c for cs in _province_city_map.values() for c in cs})
            preferred_cities_sort_input = st.multiselect(
                "偏好城市（排序优先）",
                options=_all_cities_flat,
                default=[],
                placeholder="搜索城市…",
                key="w_preferred_cities_sort",
            )
            limit_to_preferred_cities = bool(preferred_cities_sort_input) and st.checkbox(
                "只看这些城市",
                value=False,
                key="w_limit_cities",
                disabled=not bool(preferred_cities_sort_input),
            )

            preferred_schools_raw = st.text_input(
                "偏好学校（排序优先）",
                value="", placeholder="浙江大学, 上海交通大学",
            )
            _has_pref_schools = bool(preferred_schools_raw.strip())
            limit_to_preferred_schools = _has_pref_schools and st.checkbox(
                "只看这些学校",
                value=False,
                key="w_limit_schools",
                disabled=not _has_pref_schools,
            )
            risk_preference = st.selectbox("风险偏好", ["激进", "均衡", "保守"], index=1, key="w_risk")
            volunteer_total = st.number_input("志愿数量", 1, 80, 80, step=1)

            excluded_major_input = _dynamic_text_list(
                "排除专业",
                "excluded_majors",
                "例如：土木工程",
            )

        st.divider()
        st.header("🔧 筛选条件")
        school_levels = st.multiselect(
            "学校层次（不选 = 不限）",
            ["985", "211", "双一流"],
            default=[],
        )
        _all_provinces = sorted(_province_city_map.keys())
        _selected_provinces = st.multiselect(
            "只看这些省份",
            options=_all_provinces,
            default=[],
            placeholder="搜索省份…",
            key="w_provinces",
        )
        _city_options = sorted({
            city
            for prov in (_selected_provinces or _all_provinces)
            for city in _province_city_map.get(prov, [])
        })
        preferred_cities_input = st.multiselect(
            "进一步指定城市（不选则包含所选省份全部城市）",
            options=_city_options,
            default=[],
            placeholder="搜索城市…" if _selected_provinces else "请先选省份，或直接搜索全部城市",
            key="w_cities",
        )
        _ALL_PROVINCES = [
            "北京", "天津", "上海", "重庆",
            "河北", "山西", "内蒙古", "辽宁", "吉林", "黑龙江",
            "江苏", "浙江", "安徽", "福建", "江西", "山东",
            "河南", "湖北", "湖南", "广东", "广西", "海南",
            "四川", "贵州", "云南", "西藏", "陕西", "甘肃", "青海", "宁夏", "新疆",
        ]
        excluded_regions = st.multiselect("排除省份", _ALL_PROVINCES)
        accept_sino_foreign = st.checkbox("接受中外合作专业", value=False)
        accept_private = st.checkbox("接受民办学校", value=True)
        excluded_schools_raw = st.text_input("排除学校（精确名）", value="")
    else:
        st.divider()
        st.info("完成右侧 AI 对话后，参数将在此处显示供你核对和修改。")

# 表单未就绪时提供后备变量，供对话框的 _profile_ctx 使用
if not _form_ready:
    rank = st.session_state.get("w_rank", 1)
    total_score = st.session_state.get("w_total_score") or 600
    selected_subjects = st.session_state.get("w_subjects", [])
    main_priority = st.session_state.get("w_main_priority", "请选择…")
    risk_preference = st.session_state.get("w_risk", "均衡")
    preferred_majors: list[str] = []
    preferred_cities: list[str] = []
    preferred_cities_sort_input: list[str] = []
    preferred_cities_input: list[str] = []
    preferred_major_input: list[str] = []
    excluded_major_input: list[str] = []
    limit_to_preferred_majors = False
    limit_to_preferred_cities = False
    limit_to_preferred_schools = False
    preferred_schools_raw = ""

# ─── AI 对话顾问 ──────────────────────────────────────────────────────────────

def _parse_json_from_text(text: str) -> dict | None:
    match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except Exception:
            return None
    return None


_XIAOYU_WELCOME = (
    "你好！我是**小明**，你的志愿填报助手 👋\n\n"
    "告诉我以下信息，我来帮你生成推荐志愿：\n\n"
    "**必填**\n"
    "- 全省位次（如：8000）\n"
    "- 选考科目，3门（如：物理、化学、生物）\n"
    "- 主排序偏好：**专业优先 / 学校优先 / 城市优先**（三选一）\n\n"
    "**选填**（填了推荐更精准）\n"
    "- 偏好专业（如：计算机、金融）→ 选了会优先推这些专业\n"
    "- 偏好城市/地区（如：北京、长三角）→ 选了会优先推这些地区\n"
    "- 风险偏好（激进 / 均衡 / 保守）\n\n"
    "直接说就行，例如：位次8000，物理化学生物，专业优先，想学计算机，偏好长三角\n\n"
    "*⚠️ AI 建议仅供参考，最终填报请以官方规定为准，本工具概不负责。*"
)

if "ai_chat" not in st.session_state:
    st.session_state["ai_chat"] = [{"role": "assistant", "content": _XIAOYU_WELCOME}]

_has_inject = "_ai_inject" in st.session_state
_has_fn_inject = "_ai_fn_inject" in st.session_state
with st.expander("💬 AI 对话顾问", expanded=True):
    _chat_container = st.container(height=340)
    with _chat_container:
        for _m in st.session_state["ai_chat"]:
            with st.chat_message(_m["role"]):
                st.write(_m["content"])

    _input_n = st.session_state.get("_ai_input_n", 0)
    _c1, _c2, _c3 = st.columns([6, 1, 1])
    with _c1:
        _user_msg = st.text_input(
            "输入",
            key=f"_ai_msg_{_input_n}",
            placeholder="直接输入你的情况或问题…",
            label_visibility="collapsed",
        )
    with _c2:
        _send = st.button("发送", use_container_width=True, key="ai_send")
    with _c3:
        _clear_chat = st.button("清除", use_container_width=True, key="ai_clear")

    if _clear_chat:
        st.session_state["ai_chat"] = [{"role": "assistant", "content": _XIAOYU_WELCOME}]
        st.session_state.pop("ai_parsed", None)
        st.session_state.pop("_advisor_ctx", None)
        st.session_state.pop("_advisor_intro_sent", None)
        st.session_state.pop("_form_ready", None)
        st.session_state["_ai_input_n"] = _input_n + 1
        st.rerun()

    # fn_inject: buttons that call dedicated functions (explain / report)
    if _send and queue_ai_message(st.session_state, _user_msg, _input_n):
        st.rerun()

    _msg_to_send = None
    _fn_inject = None
    if _has_fn_inject:
        _fn_inject = st.session_state.pop("_ai_fn_inject")
    elif _has_inject:
        _msg_to_send = st.session_state.pop("_ai_inject")
    elif "_ai_pending_msg" in st.session_state:
        _msg_to_send = st.session_state.pop("_ai_pending_msg")

    if _fn_inject:
        if not _effective_api_key:
            st.warning("请在左侧填入百炼 API Key 才能使用 AI 功能")
        else:
            _fn_type = _fn_inject["fn"]
            _fn_label = _fn_inject["label"]
            st.session_state["ai_chat"].append({"role": "user", "content": _fn_label})
            with _chat_container:
                with st.chat_message("user"):
                    st.write(_fn_label)
            _search_priority = _fn_inject.get("main_priority", "学校优先")
            if _fn_type == "explain_volunteer":
                _major = _fn_inject["volunteer"].get("major_name", "")
                _city = _fn_inject["volunteer"].get("school_city", "")
                if _search_priority == "城市优先" and _city:
                    _query = f"{_city} {_major} 就业 薪资 产业 2025".strip()
                    with st.spinner(f"🔍 搜索 {_city} {_major} 城市就业数据…"):
                        _fn_search = search_web(_query)
                else:
                    with st.spinner(f"🔍 搜索 {_major} 就业数据…"):
                        _fn_search = search_web(f"{_major} 就业去向 薪资 2025")
            else:
                from collections import Counter as _Counter
                _top_major = (_Counter(
                    v.get("major_name", "") for v in _fn_inject["volunteers"]
                ).most_common(1) or [("", 0)])[0][0]
                if _search_priority == "城市优先":
                    _top_city = (_Counter(
                        v.get("school_city", "") for v in _fn_inject["volunteers"] if v.get("school_city")
                    ).most_common(1) or [("", 0)])[0][0]
                    _query = f"{_top_city} {_top_major} 就业 薪资 产业 2025".strip()
                    with st.spinner(f"🔍 搜索 {_top_city} {_top_major} 就业数据…"):
                        _fn_search = search_web(_query) if _top_city else []
                else:
                    with st.spinner(f"🔍 搜索 {_top_major} 就业数据…"):
                        _fn_search = search_web(f"{_top_major} 就业去向 薪资 2025") if _top_major else []
            try:
                with _chat_container:
                    with st.chat_message("assistant"):
                        if _fn_type == "explain_volunteer":
                            _response = st.write_stream(
                                explain_volunteer(
                                    _fn_inject["volunteer"],
                                    _fn_inject["profile"],
                                    search_results=_fn_search,
                                    main_priority=_fn_inject.get("main_priority", "学校优先"),
                                    api_key=_effective_api_key,
                                )
                            )
                        else:  # generate_report
                            _response = st.write_stream(
                                generate_overall_report(
                                    _fn_inject["volunteers"],
                                    _fn_inject["stats"],
                                    _fn_inject["profile"],
                                    search_results=_fn_search,
                                    main_priority=_fn_inject.get("main_priority", "学校优先"),
                                    api_key=_effective_api_key,
                                )
                            )
                if not _response:
                    _response = "⚠️ AI 未返回内容，请稍后重试或检查 API Key 是否有效。"
                    with _chat_container:
                        with st.chat_message("assistant"):
                            st.write(_response)
            except Exception as _e:
                _response = f"⚠️ 生成失败：{_e}"
                with _chat_container:
                    with st.chat_message("assistant"):
                        st.write(_response)
            st.session_state["ai_chat"].append({"role": "assistant", "content": _response})
            st.rerun()

    if _msg_to_send:
        if not _effective_api_key:
            st.warning("请在左侧填入百炼 API Key 才能使用 AI 功能")
        else:
            _profile_ctx = {
                "rank": int(rank),
                "selected_subjects": selected_subjects,
                "preferred_majors": preferred_major_input,
                "preferred_cities": preferred_cities_sort_input,
                "main_priority": main_priority,
                "risk_preference": risk_preference,
            }
            st.session_state["ai_chat"].append({"role": "user", "content": _msg_to_send})
            with _chat_container:
                with st.chat_message("user"):
                    st.write(_msg_to_send)
            _search_results = None
            if should_search(_msg_to_send) and st.session_state.get("_advisor_ctx"):
                with st.spinner("🔍 查询最新数据…"):
                    _search_results = search_web(_msg_to_send)
            with _chat_container:
                with st.chat_message("assistant"):
                    _response = st.write_stream(
                        chat_with_advisor(
                            st.session_state["ai_chat"],
                            profile_ctx=_profile_ctx,
                            recommendation_ctx=st.session_state.get("_advisor_ctx"),
                            search_results=_search_results,
                            api_key=_effective_api_key,
                        )
                    )
            st.session_state["ai_chat"].append({"role": "assistant", "content": _response})
            _parsed = _parse_json_from_text(_response)
            if _parsed:
                st.session_state["ai_parsed"] = _parsed
            st.rerun()

    if "ai_parsed" in st.session_state:
        _p = st.session_state["ai_parsed"]
        st.divider()
        st.markdown("**小明提取到的参数，确认后填入表单：**")
        _pc1, _pc2 = st.columns(2)
        with _pc1:
            st.write(f"位次：**{_p.get('rank', '—')}**")
            st.write(f"选考科目：**{', '.join(_p.get('selected_subjects', []))}**")
            st.write(f"主排序：**{_p.get('main_priority', '—')}**")
            st.write(f"风险偏好：**{_p.get('risk_preference', '—')}**")
        with _pc2:
            st.write(f"偏好专业：**{', '.join(_p.get('preferred_majors', [])) or '未指定'}**")
            st.write(f"偏好城市：**{', '.join(_p.get('preferred_cities', [])) or '未指定'}**")

        if st.button("确认填入表单", type="primary", key="ai_confirm"):
            _fill: dict = {}
            if _p.get("rank"):
                _fill["w_rank"] = max(1, min(400_000, int(_p["rank"])))
            if _p.get("total_score"):
                _fill["w_total_score"] = max(200, min(750, int(_p["total_score"])))
            if _p.get("selected_subjects"):
                _normalized = [_SUBJECT_ALIAS.get(s, s) for s in _p["selected_subjects"]]
                _fill["w_subjects"] = [s for s in _normalized if s in SUBJECT_ORDER][:3]
            _mp = _p.get("main_priority")
            if _mp in ["专业优先", "学校优先", "城市优先"]:
                _fill["w_main_priority"] = _mp
            if _p.get("risk_preference") in ["激进", "均衡", "保守"]:
                _fill["w_risk"] = _p["risk_preference"]
            if _p.get("preferred_majors"):
                _matched: list[str] = []
                for _kw in _p["preferred_majors"]:
                    if _kw in _all_major_options:
                        _matched.append(_kw)
                    else:
                        _matched.extend(o for o in _all_major_options if _kw in o)
                _fill["w_majors_list"] = list(dict.fromkeys(_matched))
                if _fill["w_majors_list"]:
                    if "w_main_priority" not in _fill:
                        _fill["w_main_priority"] = "专业优先"
            if _p.get("preferred_cities"):
                _REGION_EXPAND: dict[str, list[str]] = {
                    "长三角": ["上海", "杭州", "南京", "苏州", "宁波", "合肥"],
                    "珠三角": ["广州", "深圳", "佛山", "东莞", "珠海"],
                    "京津冀": ["北京", "天津"],
                    "成渝":   ["成都", "重庆"],
                    "中部":   ["武汉", "长沙", "南昌", "郑州"],
                    "长江中游": ["武汉", "长沙", "南昌", "郑州"],
                    "西部":   ["成都", "重庆", "西安"],
                    "东北":   ["沈阳", "大连", "哈尔滨", "长春"],
                }
                _expanded: list[str] = []
                for _c in _p["preferred_cities"]:
                    _expanded.extend(_REGION_EXPAND.get(_c, [_c]))
                _prov_map = _load_province_city_map()
                _all_cities = {c for cs in _prov_map.values() for c in cs}
                _valid_cities = list(dict.fromkeys(c for c in _expanded if c in _all_cities))
                if _valid_cities:
                    _fill["w_preferred_cities_sort"] = _valid_cities
            st.session_state["_pending_fill"] = _fill
            st.session_state["_form_ready"] = True
            st.session_state.pop("ai_parsed", None)
            # Auto-post user confirmation + 小志 handoff
            st.session_state["ai_chat"].append({"role": "user", "content": "同意，参数已确认"})
            st.session_state["ai_chat"].append({
                "role": "assistant",
                "content": (
                    "收到！参数已填入左侧表单，推荐志愿正在生成 🎯\n\n"
                    "你也可以在**左侧表单**自由修改考生信息（位次、选科、偏好城市等），修改后志愿会自动重新生成。"
                    "如果不想手动改，也可以直接在对话框里说（例如「我想留在江浙沪」「换成学校优先」），我来帮你更新参数重新生成。\n\n"
                    "志愿生成后，在这个对话框下方会出现两个按钮：\n"
                    "- **生成总体报告** — 分析整体冲稳保方案\n"
                    "- **解释此条志愿** — 选一条志愿深度解析\n\n"
                    "也可以直接问我任何问题！"
                ),
            })
            st.session_state["_advisor_intro_sent"] = True
            st.rerun()

    # Quick actions — only visible after recommendations are generated
    _ctx = st.session_state.get("_advisor_ctx")
    if _ctx and _ctx.get("volunteers"):
        _qa_vols = _ctx["volunteers"]
        _qa_labels = [
            f"{v.get('volunteer_no')}. {v.get('school_name')} · {v.get('major_name')}"
            for v in _qa_vols
        ]
        st.divider()
        _fn_profile = {
            "rank": int(rank),
            "selected_subjects": selected_subjects,
            "preferred_majors": preferred_major_input,
            "preferred_cities": preferred_cities_sort_input,
            "main_priority": main_priority,
            "risk_preference": risk_preference,
        }
        if st.button("📊 生成总体报告", use_container_width=True, key="btn_report"):
            if not _effective_api_key:
                st.warning("请先填入左侧百炼 API Key")
            else:
                st.session_state["_ai_fn_inject"] = {
                    "fn": "generate_report",
                    "label": "📊 生成总体分析报告",
                    "volunteers": _ctx["volunteers"],
                    "stats": _ctx["stats"],
                    "profile": _fn_profile,
                    "main_priority": main_priority,
                }
                st.rerun()
        _qa_sel_label = st.selectbox(
            "选择要解释的志愿",
            options=_qa_labels,
            label_visibility="collapsed",
            key="chat_vol_select",
        )
        _qa_idx = _qa_labels.index(_qa_sel_label) if _qa_sel_label in _qa_labels else 0
        if st.button("💬 解释此条志愿", use_container_width=True, key="btn_explain"):
            if not _effective_api_key:
                st.warning("请先填入左侧百炼 API Key")
            else:
                _qv = _qa_vols[_qa_idx]
                st.session_state["_ai_fn_inject"] = {
                    "fn": "explain_volunteer",
                    "label": f"💬 解释第{_qv.get('volunteer_no')}条：{_qv.get('school_name')}·{_qv.get('major_name')}",
                    "volunteer": _qv,
                    "profile": _fn_profile,
                    "main_priority": main_priority,
                }
                st.rerun()

# 表单未就绪时在此停止，不渲染后续推荐内容
if not _form_ready:
    st.stop()

# ─── 校验 ────────────────────────────────────────────────────────────────────

if len(selected_subjects) != 3:
    st.info(
        f"请选择恰好 3 门选考科目（当前 {len(selected_subjects)} 门）\n\n"
        "可以在上方 **AI 对话顾问** 中用自然语言告诉小明你的情况，他会帮你自动填写。"
    )
    st.stop()

if main_priority not in ("专业优先", "学校优先", "城市优先"):
    st.info("请在左侧选择主排序方式（专业优先 / 学校优先 / 城市优先），或在上方 AI 对话中告诉小明你的偏好。")
    st.stop()

preferred_cities = preferred_cities_sort_input  # soft sort preference only
# Hard pool filter: 只看城市 checkbox > specific cities (hard filter) > province expansion > none
if limit_to_preferred_cities:
    city_filters = preferred_cities_sort_input
elif preferred_cities_input:
    city_filters = preferred_cities_input
elif _selected_provinces:
    city_filters = sorted({
        city
        for prov in _selected_provinces
        for city in _province_city_map.get(prov, [])
    })
else:
    city_filters = []
preferred_majors, preferred_categories = split_major_preferences(preferred_major_input)
major_kws = preferred_major_input if limit_to_preferred_majors else []
excluded_majors = excluded_major_input
preferred_schools = normalize_items([preferred_schools_raw])
excluded_schools = normalize_items([excluded_schools_raw])

# ─── 核心过滤（选科 + 硬约束） ───────────────────────────────────────────────

try:
    profile = StudentProfile(
        rank=int(rank), total_score=int(total_score or 600),
        selected_subjects=selected_subjects,
        constraints=Constraints(
            accept_private=accept_private,
            accept_sino_foreign=accept_sino_foreign,
        ),
        preferences=Preferences(
            cities=CityPreference(
                preferred=preferred_cities,
                excluded_regions=excluded_regions,
            ),
            majors=MajorPreference(
                preferred_majors=preferred_majors,
                preferred_categories=preferred_categories,
                excluded_majors=excluded_majors,
            ),
            schools=SchoolPreference(
                preferred_schools=preferred_schools,
                excluded_schools=excluded_schools,
            ),
        ),
        priority_mode=main_priority,
        risk_preference=risk_preference,
    )
except Exception as e:
    st.error(f"输入有误：{e}")
    st.stop()

with st.spinner("计算中…"):
    eligible, excl_subj = filter_by_subject(profile, year=2025)
    pool, excl_const    = filter_by_constraints(eligible, profile)

# ─── 叠加 AND 过滤 ────────────────────────────────────────────────────────────

steps: list[tuple[str, int]] = [
    ("选科", len(pool)),
]

if school_levels:
    pool, dropped = filter_by_school_level(pool, school_levels)
    steps.append(("+".join(school_levels), len(pool)))

if city_filters:
    pool, dropped = filter_by_city(pool, city_filters)
    steps.append(("城市", len(pool)))

if major_kws:
    pool, dropped = filter_by_major_keywords(pool, major_kws)
    steps.append(("专业词", len(pool)))

if limit_to_preferred_schools and preferred_schools:
    pool = [p for p in pool if p.get("school_name") in set(preferred_schools)]
    steps.append(("指定学校", len(pool)))

final = pool

with st.spinner("生成推荐志愿…"):
    recommendation = build_recommendations(
        final,
        profile,
        main_priority=main_priority,
        preferred_majors=preferred_majors,
        preferred_categories=preferred_categories,
        preferred_schools=preferred_schools,
        preferred_cities=preferred_cities,
        risk_preference=risk_preference,
        total=int(volunteer_total),
    )

# Store context for AI advisor; rerun once after each new computation so buttons always reflect latest data.
# _reco_rerun_pending was popped at the top of the script; if True we're already in the triggered rerun
# and should NOT rerun again (that would loop forever).
_first_recommendation = "_advisor_ctx" not in st.session_state
st.session_state["_advisor_ctx"] = recommendation

if not _reco_rerun_pending:
    if _first_recommendation and not st.session_state.get("_advisor_intro_sent"):
        st.session_state["_advisor_intro_sent"] = True
        _xm_intro = (
            "志愿表出来了，我来帮你分析 📊\n\n"
            "你可以：\n"
            "- 点下方「**生成总体报告**」，我来分析整体冲稳保方案\n"
            "- 选中某条志愿后点「**解释此条志愿**」，我来深度解析\n"
            "- 直接问我任何问题，比如：这几个学校就业怎么样？能不能更激进一点？\n\n"
            "有问题随时说！"
        )
        st.session_state["ai_chat"].append({"role": "assistant", "content": _xm_intro})
    st.session_state["_reco_rerun_pending"] = True
    st.rerun()  # rerun so quick-action buttons reflect latest volunteers

# ─── 漏斗指标 ────────────────────────────────────────────────────────────────

total_raw = len(eligible) + len(excl_subj)
st.markdown("### 过滤漏斗")
cols = st.columns(2 + len(steps))
cols[0].metric("全量", f"{total_raw:,}")
cols[1].metric("选科后", f"{len(eligible):,}", f"−{len(excl_subj)}")
for i, (label, cnt) in enumerate(steps):
    prev = len(eligible) if i == 0 else steps[i-1][1]
    cols[2 + i].metric(label, f"{cnt:,}", f"−{prev - cnt}" if prev != cnt else "")

# ─── 结果表 ───────────────────────────────────────────────────────────────────

st.divider()
stats = recommendation["stats"]
st.markdown("### 推荐志愿")
st.caption(
    f"当前排序：{main_priority}；"
    f"专业限制：{'只看已选专业' if major_kws else '不限制专业'}；"
    f"城市限制：{'只看已选地区' if city_filters else '不限制城市'}。"
)
stat_cols = st.columns(6)
stat_cols[0].metric("推荐", f"{stats['total']:,}")
stat_cols[1].metric("冲", f"{stats['冲']:,}")
stat_cols[2].metric("稳", f"{stats['稳']:,}")
stat_cols[3].metric("保", f"{stats['保']:,}")
stat_cols[4].metric("垫", f"{stats['垫']:,}")
stat_cols[5].metric("备选池", f"{stats['备选池']:,}")

if major_kws and (stats.get("冲", 0) + stats.get("稳", 0)) < 25:
    st.info(
        "当前专业限制较严格，符合条件的冲/稳志愿不足，已用保底志愿补齐。"
        "如需更多冲稳选项，可适当放宽偏好专业或取消专业限制。"
    )
if not preferred_majors and not preferred_categories:
    st.warning(
        "💡 你没有填写专业偏好，志愿按城市档+学校排名排序，结果可能较分散。"
        "建议在左侧「偏好专业」填写目标方向（如「计算机」），让推荐更聚焦。"
    )

search = st.text_input("🔍 搜索", placeholder="搜索学校或专业…")
recommend_df = _recommendation_df(recommendation["volunteers"], main_priority)
candidate_df = _to_df(final)
reserve_df = _recommendation_df(recommendation["reserve"], main_priority)
if search.strip():
    keyword = search.strip()
    recommend_df = _filter_df(recommend_df, keyword)
    candidate_df = _filter_df(candidate_df, keyword)
    reserve_df = _filter_df(reserve_df, keyword)

tab_recommend, tab_candidates, tab_reserve = st.tabs(["推荐志愿", "候选池", "备选池"])

with tab_recommend:
    st.dataframe(
        recommend_df.drop(columns=["_idx"]), width="stretch", hide_index=True, height=580,
        column_config={
            "序号":      st.column_config.NumberColumn(width="small"),
            "层级":      st.column_config.TextColumn(width="small"),
            "学校":      st.column_config.TextColumn(width="medium"),
            "城市":      st.column_config.TextColumn(width="small"),
            "专业":      st.column_config.TextColumn(width="large"),
            "专业匹配":  st.column_config.TextColumn(width="small"),
            "2025位次":  st.column_config.TextColumn(width="small"),
            "2024位次":  st.column_config.TextColumn(width="small"),
            "2023位次":  st.column_config.TextColumn(width="small"),
            "均值位次":  st.column_config.NumberColumn(width="small"),
            "gap":       st.column_config.NumberColumn(width="small"),
            "历史年数":  st.column_config.NumberColumn(width="small"),
            "选科要求":  st.column_config.TextColumn(width="medium"),
            "⚠":        st.column_config.TextColumn(width="medium"),
        },
    )
    _rec_vols = recommendation["volunteers"]
    _rec_options = ["— 选择一条志愿查看详情 —"] + [
        f"{p.get('volunteer_no')}. {p.get('school_name')}  ·  {p.get('major_name', '')}"
        for p in _rec_vols
    ]
    _rec_sel = st.selectbox("查看详情", _rec_options, key="rec_detail_sel", label_visibility="collapsed")
    if _rec_sel != _rec_options[0]:
        _rec_idx = _rec_options.index(_rec_sel) - 1
        with st.container(border=True):
            _show_program_detail(_rec_vols[_rec_idx])

with tab_candidates:
    warn_cnt = sum(1 for p in final if p.get("_warnings"))
    st.caption(f"候选池 {len(final):,} 条" + (f"，其中 {warn_cnt} 条需核对" if warn_cnt else ""))
    st.dataframe(
        candidate_df, width="stretch", hide_index=True, height=620,
        column_config={
            "学校":      st.column_config.TextColumn(width="medium"),
            "城市":      st.column_config.TextColumn(width="small"),
            "专业":      st.column_config.TextColumn(width="large"),
            "选科要求":  st.column_config.TextColumn(width="medium"),
            "⚠":        st.column_config.TextColumn(width="medium"),
        },
    )

with tab_reserve:
    st.dataframe(
        reserve_df.drop(columns=["_idx"]), width="stretch", hide_index=True, height=580,
        column_config={
            "序号":      st.column_config.NumberColumn(width="small"),
            "层级":      st.column_config.TextColumn(width="small"),
            "学校":      st.column_config.TextColumn(width="medium"),
            "城市":      st.column_config.TextColumn(width="small"),
            "专业":      st.column_config.TextColumn(width="large"),
            "专业匹配":  st.column_config.TextColumn(width="small"),
            "2025位次":  st.column_config.TextColumn(width="small"),
            "2024位次":  st.column_config.TextColumn(width="small"),
            "2023位次":  st.column_config.TextColumn(width="small"),
            "均值位次":  st.column_config.NumberColumn(width="small"),
            "gap":       st.column_config.NumberColumn(width="small"),
            "历史年数":  st.column_config.NumberColumn(width="small"),
            "选科要求":  st.column_config.TextColumn(width="medium"),
            "⚠":        st.column_config.TextColumn(width="medium"),
        },
    )
    _rsv_vols = recommendation["reserve"]
    _rsv_options = ["— 选择一条志愿查看详情 —"] + [
        f"{p.get('volunteer_no')}. {p.get('school_name')}  ·  {p.get('major_name', '')}"
        for p in _rsv_vols
    ]
    _rsv_sel = st.selectbox("查看详情", _rsv_options, key="rsv_detail_sel", label_visibility="collapsed")
    if _rsv_sel != _rsv_options[0]:
        _rsv_idx = _rsv_options.index(_rsv_sel) - 1
        with st.container(border=True):
            _show_program_detail(_rsv_vols[_rsv_idx])
