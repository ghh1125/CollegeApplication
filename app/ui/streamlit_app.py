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
from app.ui.form_helpers import normalize_items, split_major_preferences
from app.db import get_conn


# ─── 页面配置 ────────────────────────────────────────────────────────────────

st.set_page_config(page_title="高考志愿筛选", page_icon="🎓", layout="wide")
st.title("🎓 高考志愿筛选")

# ─── 辅助 ────────────────────────────────────────────────────────────────────

SUBJECT_ORDER = ["物理", "化学", "生物", "历史", "地理", "思想政治", "技术"]


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
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT province, city FROM school_master "
            "WHERE province IS NOT NULL AND province != '' "
            "AND city IS NOT NULL AND city != '' "
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


def _recommendation_df(programs: list[dict]) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "序号":      p.get("volunteer_no") or index,
            "层级":      (p.get("gap_info") or {}).get("tier", ""),
            "学校":      p.get("school_name", ""),
            "城市":      p.get("school_city") or resolve_school_city(p.get("school_name", "")),
            "专业":      p.get("major_name", ""),
            **history_rank_columns(p),
            "均值位次":  (p.get("gap_info") or {}).get("weighted_avg"),
            "gap":       (p.get("gap_info") or {}).get("gap"),
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

if "_pending_fill" in st.session_state:
    _pf = st.session_state.pop("_pending_fill")
    for _k, _v in _pf.items():
        st.session_state[_k] = _v

# ─── 侧边栏 ──────────────────────────────────────────────────────────────────

with st.sidebar:
    st.header("🔑 AI 设置")
    _user_api_key = st.text_input(
        "百炼 API Key（可选，用于生成解释）",
        type="password",
        placeholder="sk-...",
        help="填入后才能使用 AI 解释功能。Key 仅在本次会话中使用，不会保存。",
    )
    _effective_api_key = _user_api_key.strip() or None

    st.divider()
    st.header("📋 考生信息")
    rank = st.number_input("全省位次", 1, 400_000, value=36_500, step=100, key="w_rank")
    total_score = st.number_input("总分", 200, 750, value=626, step=1, key="w_total_score")
    selected_subjects = st.multiselect(
        "选考科目（选 3 门）", SUBJECT_ORDER,
        default=["物理", "化学", "生物"], max_selections=3,
        key="w_subjects",
    )

    st.divider()
    st.header("推荐策略")
    main_priority = st.selectbox("主排序", ["专业优先", "学校优先"], index=0, key="w_main_priority")

    if main_priority == "专业优先":
        major_options = _all_major_options
        selected_majors_from_list = st.multiselect(
            "想报的专业",
            options=major_options,
            default=[],
            placeholder='搜索专业名，如"计算机"…',
            key="w_majors_list",
        )
        preferred_major_input = selected_majors_from_list + _dynamic_text_list(
            "手动补充关键词",
            "preferred_majors",
            "例如：人工智能",
        )
        limit_to_preferred_majors = st.checkbox(
            "只看这些专业相关",
            value=False,
            disabled=not preferred_major_input,
        )
        excluded_major_input = _dynamic_text_list(
            "不想读的专业",
            "excluded_majors",
            "例如：土木工程",
        )
    else:
        preferred_major_input = []
        limit_to_preferred_majors = False
        excluded_major_input = []

    city_first = st.checkbox("同层级内城市优先", value=True)
    risk_preference = st.selectbox("风险偏好", ["激进", "均衡", "保守"], index=1, key="w_risk")
    volunteer_total = st.number_input("志愿数量", 1, 80, 80, step=1)

    st.divider()
    st.header("🏫 学校层次")
    school_levels = st.multiselect(
        "只看这些层次（不选 = 不限）",
        ["985", "211", "双一流"],
        default=[],
    )
    preferred_schools_raw = st.text_input(
        "偏好学校（推荐排序）",
        value="", placeholder="浙江大学, 上海交通大学",
    )

    st.divider()
    st.header("📍 城市 / 地区")
    _province_city_map = _load_province_city_map()
    _all_provinces = sorted(_province_city_map.keys())
    _selected_provinces = st.multiselect(
        "选省份",
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
        "选城市",
        options=_city_options,
        default=[],
        placeholder="搜索城市…" if _selected_provinces else "请先选省份，或直接搜索全部城市",
        key="w_cities",
    )
    limit_to_preferred_cities = st.checkbox(
        "只看这些城市",
        value=False,
        disabled=not preferred_cities_input,
    )
    st.caption("不勾选时，这些城市只影响推荐排序；勾选后会过滤候选池")
    _ALL_PROVINCES = [
        "北京", "天津", "上海", "重庆",
        "河北", "山西", "内蒙古", "辽宁", "吉林", "黑龙江",
        "江苏", "浙江", "安徽", "福建", "江西", "山东",
        "河南", "湖北", "湖南", "广东", "广西", "海南",
        "四川", "贵州", "云南", "西藏", "陕西", "甘肃", "青海", "宁夏", "新疆",
    ]
    excluded_regions = st.multiselect("排除省份", _ALL_PROVINCES)

    st.divider()
    st.header("🚫 其他约束")
    accept_sino_foreign = st.checkbox("接受中外合作专业", value=False)
    accept_private = st.checkbox("接受民办学校", value=True)
    excluded_schools_raw = st.text_input("排除学校（精确名）", value="")

# ─── AI 智能填报助手 ──────────────────────────────────────────────────────────

def _parse_json_from_text(text: str) -> dict | None:
    match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except Exception:
            return None
    return None


_chat_open = not bool(st.session_state.get("ai_chat"))
with st.expander("🤖 AI 智能填报助手", expanded=_chat_open):
    if "ai_chat" not in st.session_state:
        st.session_state["ai_chat"] = []

    _chat_container = st.container(height=320)
    with _chat_container:
        if not st.session_state["ai_chat"]:
            st.info(
                "**我是小智，你的高考志愿填报助手** 👋\n\n"
                "我会通过几个问题帮你填写左侧参数表单，你只需要用自然语言告诉我：\n"
                "- 你的全省位次是多少？\n"
                "- 选了哪三门选考科目？\n"
                "- 有没有偏好的专业或城市？\n\n"
                "直接在下方输入框开始聊天吧！"
            )
        for _m in st.session_state["ai_chat"]:
            with st.chat_message(_m["role"]):
                st.write(_m["content"])

    _input_n = st.session_state.get("_ai_input_n", 0)
    _c1, _c2, _c3 = st.columns([6, 1, 1])
    with _c1:
        _user_msg = st.text_input(
            "输入",
            key=f"_ai_msg_{_input_n}",
            placeholder="例如：我位次8000，选了物理化学生物，想学计算机，偏好北京上海…",
            label_visibility="collapsed",
        )
    with _c2:
        _send = st.button("发送", use_container_width=True, key="ai_send")
    with _c3:
        _clear_chat = st.button("清除", use_container_width=True, key="ai_clear")

    if _clear_chat:
        st.session_state["ai_chat"] = []
        st.session_state.pop("ai_parsed", None)
        st.session_state["_ai_input_n"] = _input_n + 1
        st.rerun()

    if _send and _user_msg.strip():
        if not _effective_api_key:
            st.warning("请在左侧填入百炼 API Key 才能使用 AI 助手")
        else:
            from app.llm.explain import chat_extract_profile
            _msg_content = _user_msg.strip()
            st.session_state["ai_chat"].append({"role": "user", "content": _msg_content})
            st.session_state["_ai_input_n"] = _input_n + 1
            with _chat_container:
                with st.chat_message("assistant"):
                    _response = st.write_stream(
                        chat_extract_profile(st.session_state["ai_chat"], api_key=_effective_api_key)
                    )
            st.session_state["ai_chat"].append({"role": "assistant", "content": _response})
            _parsed = _parse_json_from_text(_response)
            if _parsed:
                st.session_state["ai_parsed"] = _parsed
            st.rerun()

    if "ai_parsed" in st.session_state:
        _p = st.session_state["ai_parsed"]
        st.divider()
        st.markdown("**提取到的参数：**")
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
                _fill["w_subjects"] = [s for s in _p["selected_subjects"] if s in SUBJECT_ORDER][:3]
            if _p.get("main_priority") in ["专业优先", "学校优先"]:
                _fill["w_main_priority"] = _p["main_priority"]
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
                if _fill["w_majors_list"] and "w_main_priority" not in _fill:
                    _fill["w_main_priority"] = "专业优先"
            if _p.get("preferred_cities"):
                _prov_map = _load_province_city_map()
                _all_cities = {c for cs in _prov_map.values() for c in cs}
                _valid_cities = [c for c in _p["preferred_cities"] if c in _all_cities]
                if _valid_cities:
                    _fill["w_cities"] = _valid_cities
                    _city_to_prov = {c: pv for pv, cs in _prov_map.items() for c in cs}
                    _fill["w_provinces"] = list(dict.fromkeys(
                        _city_to_prov[c] for c in _valid_cities if c in _city_to_prov
                    ))
            st.session_state["_pending_fill"] = _fill
            st.rerun()

# ─── 校验 ────────────────────────────────────────────────────────────────────

if len(selected_subjects) != 3:
    st.warning(f"请选择恰好 3 门选考科目（当前 {len(selected_subjects)} 门）")
    st.stop()

preferred_cities = preferred_cities_input
city_filters = preferred_cities if limit_to_preferred_cities else []
preferred_majors, preferred_categories = split_major_preferences(preferred_major_input)
major_kws = preferred_major_input if limit_to_preferred_majors else []
excluded_majors = excluded_major_input
preferred_schools = normalize_items([preferred_schools_raw])
excluded_schools = normalize_items([excluded_schools_raw])

# ─── 核心过滤（选科 + 硬约束） ───────────────────────────────────────────────

try:
    profile = StudentProfile(
        rank=int(rank), total_score=int(total_score),
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

final = pool

with st.spinner("生成推荐志愿…"):
    recommendation = build_recommendations(
        final,
        profile,
        main_priority=main_priority,
        city_first=city_first,
        preferred_majors=preferred_majors,
        preferred_categories=preferred_categories,
        preferred_schools=preferred_schools,
        preferred_cities=preferred_cities,
        risk_preference=risk_preference,
        total=int(volunteer_total),
    )

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
stat_cols = st.columns(6)
stat_cols[0].metric("推荐", f"{stats['total']:,}")
stat_cols[1].metric("冲", f"{stats['冲']:,}")
stat_cols[2].metric("稳", f"{stats['稳']:,}")
stat_cols[3].metric("保", f"{stats['保']:,}")
stat_cols[4].metric("垫", f"{stats['垫']:,}")
stat_cols[5].metric("备选池", f"{stats['备选池']:,}")

# ─── LLM 总体报告 ────────────────────────────────────────────────────────────

if "overall_report" in st.session_state:
    with st.chat_message("assistant"):
        st.write(st.session_state["overall_report"])
    if st.button("清除报告", key="clear_report"):
        del st.session_state["overall_report"]
        st.rerun()

if st.button("生成总体报告", type="primary"):
    if not _effective_api_key:
        st.warning("请在左侧填入百炼 API Key")
    else:
        try:
            from app.llm.explain import generate_overall_report
            _llm_profile = {
                "rank": int(rank),
                "selected_subjects": selected_subjects,
                "preferred_majors": preferred_majors,
                "preferred_cities": preferred_cities,
                "risk_preference": risk_preference,
            }
            with st.chat_message("assistant"):
                result = st.write_stream(generate_overall_report(
                    recommendation["volunteers"], stats, _llm_profile,
                    api_key=_effective_api_key,
                ))
            st.session_state["overall_report"] = result
            st.rerun()
        except Exception as e:
            st.error(f"生成失败：{e}")

search = st.text_input("🔍 搜索", placeholder="搜索学校或专业…")
recommend_df = _recommendation_df(recommendation["volunteers"])
candidate_df = _to_df(final)
reserve_df = _recommendation_df(recommendation["reserve"])
if search.strip():
    keyword = search.strip()
    recommend_df = _filter_df(recommend_df, keyword)
    candidate_df = _filter_df(candidate_df, keyword)
    reserve_df = _filter_df(reserve_df, keyword)

tab_recommend, tab_candidates, tab_reserve = st.tabs(["推荐志愿", "候选池", "备选池"])

with tab_recommend:
    st.dataframe(
        recommend_df, width="stretch", hide_index=True, height=620,
        column_config={
            "序号":      st.column_config.NumberColumn(width="small"),
            "层级":      st.column_config.TextColumn(width="small"),
            "学校":      st.column_config.TextColumn(width="medium"),
            "城市":      st.column_config.TextColumn(width="small"),
            "专业":      st.column_config.TextColumn(width="large"),
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

    st.divider()
    st.markdown("**单条志愿解释**")
    _volunteers = recommendation["volunteers"]
    _vol_labels = [
        f"{v.get('volunteer_no')}. {v.get('school_name')} · {v.get('major_name')}"
        for v in _volunteers
    ]
    _selected_label = st.selectbox("选择志愿", _vol_labels, index=0, label_visibility="collapsed")
    _selected_idx = _vol_labels.index(_selected_label)
    _explain_key = f"explain_{_selected_idx}"

    # show stored explanation for the currently selected volunteer
    if _explain_key in st.session_state:
        with st.chat_message("assistant"):
            st.write(st.session_state[_explain_key])
        if st.button("清除解释", key="clear_explain"):
            del st.session_state[_explain_key]
            st.rerun()

    if st.button("生成解释", key="explain_single"):
        if not _effective_api_key:
            st.warning("请在左侧填入百炼 API Key")
        else:
            try:
                from app.llm.explain import explain_volunteer
                _llm_profile = {
                    "rank": int(rank),
                    "selected_subjects": selected_subjects,
                    "preferred_majors": preferred_majors,
                    "preferred_cities": preferred_cities,
                }
                with st.chat_message("assistant"):
                    result = st.write_stream(explain_volunteer(
                        _volunteers[_selected_idx], _llm_profile,
                        api_key=_effective_api_key,
                    ))
                st.session_state[_explain_key] = result
                st.rerun()
            except Exception as e:
                st.error(f"生成失败：{e}")

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
        reserve_df, width="stretch", hide_index=True, height=620,
        column_config={
            "序号":      st.column_config.NumberColumn(width="small"),
            "层级":      st.column_config.TextColumn(width="small"),
            "学校":      st.column_config.TextColumn(width="medium"),
            "城市":      st.column_config.TextColumn(width="small"),
            "专业":      st.column_config.TextColumn(width="large"),
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
