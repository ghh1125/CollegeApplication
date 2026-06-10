"""浙江志愿填报 · 重构版。

第一步：用户自己填写信息（手动表单，不涉及 LLM）。
本页只负责展示与收集；输入的数据模型 / 选项 / 校验都在 src/zhejiang/input/。
收集到的 StudentInput 存在 session_state["zj_input"]，供后续步骤使用。
"""

from __future__ import annotations

import streamlit as st

from src.zhejiang.input.disciplines import CATEGORY_NAMES
from src.zhejiang.input.student_input import (
    Budget,
    MAJOR_CLASSES_GROUPED,
    PROVINCES,
    SUBJECTS_7,
    StudentInput,
)

# 专业类多选：标签「门类·专业类」↔ 4 位代码
_CLASS_LABEL_TO_CODE: dict[str, str] = {}
_CODE_TO_LABEL: dict[str, str] = {}
_CLASS_OPTIONS: list[str] = []
for _cat, _classes in MAJOR_CLASSES_GROUPED.items():
    for _code, _name in _classes:
        _label = f"{_cat}·{_name}"
        _CLASS_LABEL_TO_CODE[_label] = _code
        _CODE_TO_LABEL[_code] = _label
        _CLASS_OPTIONS.append(_label)

# 一级学科（门类）多选：标签「门类名」↔ 2 位码
_CAT_LABEL_TO_CODE = {name: code for code, name in CATEGORY_NAMES.items()}
_CAT_OPTIONS = list(_CAT_LABEL_TO_CODE.keys())

_BUDGET_OPTIONS = [b.value for b in Budget]
_COLOR_VISION = ["正常", "色弱", "色盲"]


def _reset() -> None:
    for k in list(st.session_state.keys()):
        if k.startswith("zj_") or k.startswith("_zj_"):
            del st.session_state[k]


def render(province: str = "zhejiang") -> None:
    st.title("高考志愿推荐系统 · 浙江")
    if st.button("← 切换省份", key="zj_back"):
        _reset()
        st.session_state.pop("_province", None)
        st.rerun()

    st.subheader("第一步 · 填写你的信息")
    st.caption("先把下面信息填好，后面据此筛选和推荐志愿。带 * 为必填。")

    # 不用 st.form：表单内控件提交前不重跑，地域「有/无偏好」联动会失效。
    # 用普通控件 + 保存按钮，地域选项可实时联动。
    c1, c2 = st.columns(2)
    with c1:
        rank = st.number_input("全省位次 *（省内排名）", min_value=1, max_value=400_000,
                               value=8000, step=100)
    with c2:
        total_score = st.number_input("高考分数 *（满分 750）", min_value=0, max_value=750,
                                      value=600, step=1)

    selected = st.multiselect("选考科目 *（7 选 3）", SUBJECTS_7, max_selections=3,
                              help="政治/历史/地理/物理/化学/生物/技术，选 3 门")

    categories = st.multiselect("学科门类（一级，可多选；选「全部」或不选=不限）",
                                ["全部"] + _CAT_OPTIONS,
                                help="选整个门类，如 工学，囊括其下所有专业。")

    budget = st.selectbox("经济预算（每年学费）", _BUDGET_OPTIONS, index=0)

    st.markdown("**地域偏好**")
    has_region = st.radio("是否有地域偏好", ["无偏好", "有偏好"], horizontal=True,
                          label_visibility="collapsed", key="zj_has_region")
    region_provinces: list[str] = []
    if has_region == "有偏好":
        region_provinces = st.multiselect(
            "偏好省份（按选择顺序＝优先级，从高到低）", PROVINCES,
            help="先选的优先级更高",
        )

    st.markdown("**体检结果**（可不填，将来用于体检受限专业的提示）")
    m1, m2, m3 = st.columns(3)
    with m1:
        height = st.number_input("身高 cm", min_value=0, max_value=250, value=0, step=1)
    with m2:
        color_vision = st.selectbox("色觉", _COLOR_VISION, index=0)
    with m3:
        vision = st.number_input("裸眼视力（较差眼，如 4.8）", min_value=0.0, max_value=5.3,
                                 value=0.0, step=0.1)

    st.markdown("**单科成绩**（可不填，将来用于单科要求校验）")
    s1, s2, s3 = st.columns(3)
    with s1:
        chinese = st.number_input("语文", min_value=0, max_value=150, value=0, step=1)
    with s2:
        math = st.number_input("数学", min_value=0, max_value=150, value=0, step=1)
    with s3:
        foreign = st.number_input("外语", min_value=0, max_value=150, value=0, step=1)

    submitted = st.button("保存信息", type="primary", use_container_width=True)

    if submitted:
        try:
            data = StudentInput(
                rank=int(rank),
                total_score=int(total_score),
                selected_subjects=selected,
                # 「全部」或不选 → 不限（空列表）
                major_categories=[_CAT_LABEL_TO_CODE[l] for l in categories if l != "全部"],
                major_classes=[],
                budget=Budget(budget),
                region={
                    "has_preference": has_region == "有偏好",
                    "provinces": region_provinces,
                },
                medical={
                    "height_cm": height or None,
                    "color_vision": color_vision,
                    "naked_eye_vision": vision or None,
                },
                subject_scores={
                    "chinese": chinese or None,
                    "math": math or None,
                    "foreign": foreign or None,
                },
            )
        except Exception as e:  # noqa: BLE001
            st.error(f"信息有误：{e}")
            return
        st.session_state["zj_input"] = data
        st.success("信息已保存 ✓")

    saved: StudentInput | None = st.session_state.get("zj_input")
    if saved:
        _render_summary(saved)
        _render_persona(saved)
        _render_screening(saved)
        _render_final(saved)


def _render_persona(s: StudentInput) -> None:
    from src.zhejiang.persona import classify, LATEST_FIRST_SEGMENT_RANK

    p = classify(int(s.rank))
    st.divider()
    st.markdown(f"**你的画像 · {p.name}**（{p.rank_desc}）")
    st.caption(f"浙江 2025 普通类一段线对应位次约 {LATEST_FIRST_SEGMENT_RANK:,} 名。")
    cols = st.columns(3)
    cols[0].info(f"**核心特征**\n\n{p.feature}")
    cols[1].warning(f"**典型痛点**\n\n{p.pain}")
    cols[2].success(f"**策略重点**\n\n{p.value}")


def _render_summary(s: StudentInput) -> None:
    st.divider()
    st.markdown("**已填写的信息**")
    cats = "、".join(CATEGORY_NAMES.get(c, c) for c in s.major_categories)
    cls = "、".join(_CODE_TO_LABEL.get(c, c) for c in s.major_classes)
    classes = "；".join(x for x in [cats, cls] if x) or "未选（不限）"
    region = ("无偏好" if not s.region.has_preference
              else "、".join(s.region.provinces) + "（按优先级）")
    med = s.medical
    med_str = f"身高 {med.height_cm or '—'}，色觉 {med.color_vision}，裸眼 {med.naked_eye_vision or '—'}"
    sc = s.subject_scores
    sc_str = f"语 {sc.chinese or '—'} / 数 {sc.math or '—'} / 外 {sc.foreign or '—'}"
    for label, val in [
        ("位次", s.rank), ("高考分数", s.total_score),
        ("选科", "、".join(s.selected_subjects)),
        ("意向学科", classes), ("经济预算", s.budget.value),
        ("地域偏好", region), ("体检", med_str), ("单科成绩", sc_str),
    ]:
        st.write(f"- **{label}**：{val}")


def _build_filter_opts(rows: list[dict]) -> tuple[list[str], dict[str, tuple]]:
    """从第一步筛选结果构造三级分层选项：门类 / 门类·专业类 / 门类·专业类·具体专业。
    返回 (option_list, label_map)；label_map: label → (level, cat, cls, maj)。
    """
    cat_seen: set[str] = set()
    cls_seen: set[str] = set()
    maj_seen: set[str] = set()
    cat_list: list[str] = []
    cls_list: list[str] = []
    maj_list: list[str] = []
    label_map: dict[str, tuple] = {}

    for r in rows:
        cat = r.get("类别") or ""
        cls = r.get("二级学科") or ""
        maj = r.get("专业名称") or ""
        if not cat or cat in ("—", "专科(高职)"):
            cat = ""
        if not cls or cls in ("—", "专科"):
            cls = ""

        if cat and cat not in cat_seen:
            cat_seen.add(cat)
            cat_list.append(cat)
            label_map[cat] = ("cat", cat, "", "")

        cls_lbl = f"{cat}·{cls}" if cat and cls else ""
        if cls_lbl and cls_lbl not in cls_seen:
            cls_seen.add(cls_lbl)
            cls_list.append(cls_lbl)
            label_map[cls_lbl] = ("cls", cat, cls, "")

        if maj:
            if cls_lbl:
                maj_lbl = f"{cls_lbl}·{maj}"
            elif cat:
                maj_lbl = f"{cat}··{maj}"
            else:
                maj_lbl = maj
            if maj_lbl not in maj_seen:
                maj_seen.add(maj_lbl)
                maj_list.append(maj_lbl)
                label_map[maj_lbl] = ("maj", cat, cls, maj)

    return cat_list + cls_list + maj_list, label_map


def _render_screening(s: StudentInput) -> None:
    import pandas as pd
    from src.zhejiang.screening import screen

    st.divider()
    st.subheader("第一步 · 初步筛选（按省份排，浙江最前）")
    st.caption("已用：选科、学科门类、地域偏好、体检色觉、2025位次≥你的位次。"
               "未用（缺数据）：学费/学制、单科最低分、调剂规则。")
    with st.spinner("筛选中…"):
        rows = screen(s)
    st.session_state["zj_screen_rows"] = rows
    if not rows:
        st.warning("没有符合条件的学校专业，试着放宽学科门类或地域偏好。")
        return
    st.success(f"共筛出 {len(rows)} 条")
    df = pd.DataFrame(rows)[[
        "排序", "专业名称", "专业代码", "二级学科", "学科评估", "院校名称", "院校代码",
        "层次", "城市", "办学类型", "学制", "学费/年", "2025最低位次", "2024最低位次", "2023最低位次",
    ]].rename(columns={"二级学科": "专业类", "学科评估": "学科评估结果", "层次": "院校级别"})
    st.dataframe(
        df, width="stretch", hide_index=True, height=600,
        column_config={
            "排序": st.column_config.NumberColumn(width="small"),
            "学科评估结果": st.column_config.TextColumn(width="small"),
            "院校级别": st.column_config.TextColumn(width="small"),
            "城市": st.column_config.TextColumn(width="small"),
            "2025最低位次": st.column_config.NumberColumn(width="small"),
            "2024最低位次": st.column_config.NumberColumn(width="small"),
            "2023最低位次": st.column_config.NumberColumn(width="small"),
        },
    )

    # ── 专业意向过滤（出现在第一表格下方，影响第二步志愿生成）──────────────────
    st.divider()
    st.subheader("专业意向过滤")
    st.caption("以下设置将影响第二步志愿生成。选项来源于上方初步筛选结果，支持三级粒度：门类 / 门类·专业类 / 门类·专业类·具体专业。")

    opts, label_map = _build_filter_opts(rows)
    fc, pc = st.columns(2)
    with fc:
        excluded = st.multiselect(
            "非意向专业剔除",
            opts,
            key="zj_filter_exclude",
            help="选中的门类/专业类/具体专业将从志愿里完全排除",
        )
    with pc:
        preferred = st.multiselect(
            "专业偏好",
            opts,
            key="zj_filter_prefer",
            help="选中的门类/专业类/具体专业在生成志愿时优先排列",
        )
    moe_warn = st.toggle(
        "教育部专业预警过滤",
        value=False,
        disabled=True,
        help="教育部发布的就业预警专业；暂无公开结构化数据，功能预留",
    )
    st.session_state["zj_intent_filter"] = {
        "excluded": excluded,
        "preferred": preferred,
        "label_map": label_map,
        "moe_warn": moe_warn,
    }


def _render_final(s: StudentInput) -> None:
    """专业过滤面板 + 生成最终 80 志愿表。"""
    import pandas as pd
    from src.zhejiang.final_volunteers import generate

    st.divider()
    st.subheader("第三步 · 专业过滤 → 生成最终 80 志愿")
    # 排除专业的选项来自第一步初步筛选结果（去重保序）
    screen_rows = st.session_state.get("zj_screen_rows", [])
    exclude_opts = list(dict.fromkeys(r["专业名称"] for r in screen_rows))
    with st.expander("专业过滤（过滤后再生成）", expanded=True):
        excluded = st.multiselect("排除专业（从初步筛选结果里选，可多选）", exclude_opts,
                                  help="选中的专业会从最终志愿里剔除；选项来自上面第一步筛出的专业")
        c1, c2, c3 = st.columns(3)
        c1.checkbox("天坑专业过滤", value=False, disabled=True, help="暂无数据，占位")
        c2.checkbox("教育部预警专业过滤", value=False, disabled=True, help="暂无数据，占位")
        c3.checkbox("教育部撤销专业过滤", value=False, disabled=True, help="暂无数据，占位")
        st.caption("学科范围（一级/二级）请在最上方表单调整后重新保存。天坑/预警/撤销暂留空。")

    if not st.button("生成最终志愿（80个）", type="primary", use_container_width=True):
        return
    with st.spinner("生成中…"):
        rows = generate(s, exclude_keywords=excluded)
    if not rows:
        st.warning("过滤后没有候选了，放宽过滤条件试试。")
        return
    from collections import Counter
    cwb = Counter(r["冲稳保"] for r in rows)
    st.success(f"共 {len(rows)} 个志愿 · 冲 {cwb['冲']} / 稳 {cwb['稳']} / 保 {cwb['保']}")
    df = pd.DataFrame(rows)[[
        "序号", "冲稳保", "专业名称", "专业代码", "二级学科", "学科评估",
        "考研路径", "专业发展路径", "类别", "院校名称", "院校代码", "层次",
        "2025最低位次", "2024最低位次", "2023最低位次", "三年平均位次",
    ]]
    st.dataframe(
        df, width="stretch", hide_index=True, height=600,
        column_config={
            "序号": st.column_config.NumberColumn(width="small"),
            "冲稳保": st.column_config.TextColumn(width="small"),
            "学科评估": st.column_config.TextColumn(width="small"),
            "专业发展路径": st.column_config.TextColumn(width="medium"),
            "层次": st.column_config.TextColumn(width="small"),
        },
    )
