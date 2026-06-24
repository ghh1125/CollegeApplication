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
_SCHOOL_LINK_MARKER = "__school_name__="
_SCHOOL_LINK_DISPLAY_RE = r".*__school_name__=([^&]+)$"


def _school_name_link(url: str | None, school_name: str) -> str:
    """Return a URL that Streamlit can display as the school name."""

    clean_url = (url or "").strip()
    if not clean_url:
        return school_name
    separator = "&" if "#" in clean_url else "#"
    return f"{clean_url}{separator}{_SCHOOL_LINK_MARKER}{school_name}"


def _with_linked_school_names(df):
    """Make the school-name column clickable while keeping the source URL column."""

    if "院校名称" not in df.columns or "招生官网" not in df.columns:
        return df
    linked = df.copy()
    linked["院校名称"] = [
        _school_name_link(row.get("招生官网"), str(row.get("院校名称") or ""))
        for _, row in linked.iterrows()
    ]
    return linked



def _reset() -> None:
    for k in list(st.session_state.keys()):
        if k.startswith("zj_") or k.startswith("_zj_"):
            del st.session_state[k]


def _collect_form() -> dict:
    return {
        "rank": st.session_state.get("zj_rank", 8000),
        "selected_subjects": st.session_state.get("zj_subjects", []),
        "main_priority": st.session_state.get("zj_priority", "请选择…"),
        "preferred_majors": [s.strip() for s in st.session_state.get("zj_majors", "").split(",") if s.strip()],
        "preferred_cities": [c.strip() for c in st.session_state.get("zj_cities", "").split(",") if c.strip()],
    }


def render(province: str = "zhejiang") -> None:
    st.title("高考志愿推荐系统 · 浙江")
    if st.button("← 切换省份", key="zj_back"):
        _reset()
        st.session_state.pop("_province", None)
        st.rerun()

    with st.expander("📎 数据来源与官方参考链接"):
        st.markdown(
            "本系统所有数据均来自公开官方渠道，可点击以下链接在原始平台核实：\n\n"
            "| 数据内容 | 官方来源 |\n"
            "|---------|----------|\n"
            "| 历史录取位次 / 招生计划 | [阳光高考（教育部主管）](https://gaokao.chsi.com.cn) |\n"
            "| 院校库（学校详情/学费/层次） | [阳光高考 · 院校库](https://gaokao.chsi.com.cn/sch/) |\n"
            "| 专业库（专业介绍/就业方向） | [阳光高考 · 专业库](https://gaokao.chsi.com.cn/zyk/) |\n"
            "| 位次分段表 / 选考科目要求 | [浙江省教育考试院](https://www.zjzs.net) |\n"
            "| 本科专业目录（2026版） | [教育部普通高等学校本科专业目录](https://www.moe.gov.cn) |\n"
            "| 教育部学科评估（A+/A/B…） | [全国第四轮学科评估结果](https://www.moe.gov.cn/srcsite/A22/s7065/202112/t20211231_579326.html) |\n"
            "| 大学排名 | [软科中国大学排名](https://www.shanghairanking.cn/rankings/bcur/202611) |\n\n"
            "> 最终填报请以[浙江省教育考试院](https://www.zjzs.net)官方公布数据为准，本工具结果仅供参考。"
        )

    with st.expander("📋 2026年官方政策文件"):
        st.markdown("""
以下为浙江省及教育部2026年高考招生相关官方政策原文，本系统推荐逻辑以这些政策为依据：

| 文件 | 发布机构 | 日期 |
|-----|---------|------|
| [关于做好2026年普通高校招生工作的通知](https://gaokao.chsi.com.cn/gkxx/zc/ss/202605/20260521/2293484159.html) | 中共浙江省委教育工作领导小组秘书组 | 2026-05-19 |
| [2026年浙江省普通高校招生工作实施方案](https://github.com/ghh1125/CollegeApplication/raw/main/data/zhejiang/raw/2293484160.docx)（附件，.docx） | 浙江省教育考试院 | 2026-05-19 |

> 政策原文要点（与本工具直接相关）：
> - **80志愿上限**（实施方案第30条）：「考生每次可填报不超过80个志愿」——本工具最终推荐恰为80个
> - **专业平行志愿**（第30条）：「以1所学校的1个专业（类）作为1个志愿单位」——每条志愿=一所院校+一个专业
> - **位次排序**（第28条）：同分时依次比较文化总分→语数总分→语/数单科→外语→选考科目
> - **智能辅助**（通知）：「优化高考志愿填报智能辅助服务，加强和完善考生志愿填报各环节管理」
> - **防诈提示**（通知）：「提醒考生谨防"高价志愿填报指导"诈骗陷阱」
> - **禁止代填**（通知）：「不得以任何形式代替或干预考生填报志愿」
""")

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

    def _on_cat_change():
        val = st.session_state.get("zj_cat_select", [])
        if "全部" in val and len(val) > 1:
            st.session_state["zj_cat_select"] = ["全部"]

    categories = st.multiselect("学科门类（一级，可多选；选「全部」或不选=不限）",
                                ["全部"] + _CAT_OPTIONS,
                                help="选整个门类，如 工学，囊括其下所有专业。",
                                key="zj_cat_select",
                                on_change=_on_cat_change)

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

    st.markdown(r"**体检结果** \* — 色觉用于过滤体检受限专业（国家标准）")
    m1, m2, m3 = st.columns(3)
    with m1:
        height = st.number_input("身高 cm", min_value=0, max_value=250, value=0, step=1)
    with m2:
        color_vision = st.selectbox("色觉 *", _COLOR_VISION, index=0)
    with m3:
        vision = st.number_input("裸眼视力（较差眼，如 4.8）", min_value=0.0, max_value=5.3,
                                 value=0.0, step=0.1)

    st.markdown(r"**单科成绩 \*** — 用于过滤有单科最低分要求的学校")
    s1, s2, s3 = st.columns(3)
    with s1:
        chinese = st.number_input("语文 *", min_value=0, max_value=150, value=0, step=1)
    with s2:
        math = st.number_input("数学 *", min_value=0, max_value=150, value=0, step=1)
    with s3:
        foreign = st.number_input("外语 *", min_value=0, max_value=150, value=0, step=1)

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
        _render_screening(saved)
        _render_final(saved)



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


def _build_hierarchy(rows: list[dict]) -> dict[str, dict[str, list[str]]]:
    """Build {门类名: {专业类名: [专业名]}} from step1 rows, preserving order."""
    _SKIP = {"—", "专科(高职)", "专科", ""}
    h: dict[str, dict[str, list[str]]] = {}
    seen: dict[str, dict[str, set[str]]] = {}
    for r in rows:
        cat = r.get("类别") or ""
        cls = r.get("二级学科") or ""
        maj = r.get("专业名称") or ""
        if cat in _SKIP or cls in _SKIP:
            continue
        h.setdefault(cat, {})
        seen.setdefault(cat, {})
        h[cat].setdefault(cls, [])
        seen[cat].setdefault(cls, set())
        if maj and maj not in seen[cat][cls]:
            seen[cat][cls].add(maj)
            h[cat][cls].append(maj)
    return h


def _cascading_filter_ui(
    prefix: str,
    hierarchy: dict[str, dict[str, list[str]]],
) -> list[str]:
    """三列真联级：门类 radio → 专业类 radio → 具体专业 multiselect（含「全部」按钮）。
    选择结果全部落在具体专业层，返回 sel_majs。
    """
    cat_names = list(hierarchy.keys())
    if not cat_names:
        return []

    col_cat, col_cls, col_maj = st.columns([2, 3, 4])

    with col_cat:
        st.caption("学科门类")
        cur_cat = st.radio(
            "门类", cat_names,
            key=f"zj_{prefix}_nav_cat",
            label_visibility="collapsed",
        )

    cls_names = list(hierarchy.get(cur_cat, {}).keys())

    with col_cls:
        st.caption(cur_cat)
        cur_cls = st.radio(
            "专业类", cls_names,
            key=f"zj_{prefix}_nav_cls_{cur_cat}",
            label_visibility="collapsed",
        ) if cls_names else None

    majors = hierarchy.get(cur_cat, {}).get(cur_cls, []) if cur_cls else []
    maj_key = f"zj_{prefix}_maj_{cur_cls}" if cur_cls else None

    with col_maj:
        if cur_cls and majors and maj_key:
            st.caption(cur_cls)
            if st.button("全部", key=f"zj_{prefix}_allbtn_{cur_cls}"):
                st.session_state[maj_key] = list(majors)
            st.multiselect(
                cur_cls, majors, key=maj_key,
                label_visibility="collapsed",
                placeholder="选具体专业（不选 = 不限）",
            )

    # 从所有专业类的 session_state 中收集（切换不会丢失）
    sel_majs: list[str] = []
    for cat in cat_names:
        for cls_name in hierarchy.get(cat, {}):
            sel_majs.extend(st.session_state.get(f"zj_{prefix}_maj_{cls_name}", []))

    return sel_majs


def _render_screening(s: StudentInput) -> None:
    import pandas as pd
    from src.zhejiang.step1_screen import screen

    st.divider()
    st.subheader("第一步 · 初步筛选（按省份排，浙江最前）")
    st.caption("已用：选科要求、学科门类、地域偏好、体检色觉（国家标准）、经济预算（23441条精确到专业）、"
               "单科成绩（有要求的学校参与过滤）。不按位次过滤——冲稳保在第三步生成时处理。"
               "展示：学制（23456条）、学费、体检/外语要求原文。")
    with st.spinner("筛选中…"):
        rows = screen(s)
    st.session_state["zj_screen_rows"] = rows

    if not rows:
        st.warning("没有符合条件的学校专业，试着放宽学科门类或地域偏好。")
        return
    st.success(f"共筛出 {len(rows)} 条")
    df = pd.DataFrame(rows)[[
        "排序", "专业名称", "专业代码", "二级学科", "学科评估", "软科专业排名", "软科专业评级", "院校名称",
        "招生官网", "院校代码", "层次", "城市", "办学类型", "学制", "学费/年",
        "2025最低位次", "2024最低位次", "2023最低位次",
    ]].rename(columns={"二级学科": "专业类", "学科评估": "学科评估结果", "层次": "院校级别"})
    st.dataframe(
        _with_linked_school_names(df), use_container_width=True, hide_index=True, height=600,
        column_config={
            "排序": st.column_config.NumberColumn(width="small"),
            "院校名称": st.column_config.LinkColumn(
                "院校名称", display_text=_SCHOOL_LINK_DISPLAY_RE, width="medium"
            ),
            "学科评估结果": st.column_config.TextColumn(width="small"),
            "软科专业排名": st.column_config.TextColumn(width="small"),
            "软科专业评级": st.column_config.TextColumn(width="small"),
            "招生官网": None,
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
    st.caption("剔除和偏好互斥：选一种模式即可。")

    filter_mode = st.radio(
        "过滤模式",
        ["不过滤", "剔除不想要的专业", "只保留偏好专业"],
        horizontal=True,
        key="zj_filter_mode",
        label_visibility="collapsed",
    )

    hierarchy = _build_hierarchy(rows)
    excl_majs: list[str] = []
    pref_majs: list[str] = []

    if filter_mode == "剔除不想要的专业":
        st.caption("命中的专业从候选池中删除")
        excl_majs = _cascading_filter_ui("excl", hierarchy)
    elif filter_mode == "只保留偏好专业":
        st.caption("只保留命中的专业，其余全部删除")
        pref_majs = _cascading_filter_ui("pref", hierarchy)

    moe_warn = st.toggle(
        "过滤预警专业",
        value=False,
        help="2020-2024年全国普通本科撤销布点数量 Top30（教育部数据）；开启后剔除这些专业，关闭时仅展示⚠️标记。"
             "数据来源：教育部 moe.gov.cn 历年普通高等学校本科专业备案和审批结果。",
    )
    st.session_state["zj_intent_filter"] = {
        "excl_majs": excl_majs,
        "pref_majs": pref_majs,
        "moe_warn": moe_warn,
    }

    if st.button("开始二轮筛选", type="primary", use_container_width=True):
        from src.zhejiang.step2_filter import apply_intent_filter
        filtered = apply_intent_filter(rows, excl_majs, pref_majs, moe_warn)
        st.session_state["zj_filtered_rows"] = [
            {**r, "排序": i, "预警状态": "⚠️预警" if r.get("预警") else "—"}
            for i, r in enumerate(filtered, 1)
        ]

    filtered_rows: list[dict] = st.session_state.get("zj_filtered_rows", [])
    if not filtered_rows:
        return

    st.divider()
    st.subheader("二轮筛选")
    removed = len(rows) - len(filtered_rows)
    msg = f"共 {len(filtered_rows)} 条"
    if removed:
        msg += f"（已过滤 {removed} 条）"
    st.success(msg)

    df2 = pd.DataFrame(filtered_rows)[[
        "排序", "专业名称", "专业代码", "二级学科", "学科评估", "软科专业排名", "软科专业评级", "院校名称",
        "招生官网", "预警状态", "院校代码", "层次", "城市", "办学类型", "学制", "学费/年",
        "2025最低位次", "2024最低位次", "2023最低位次",
    ]].rename(columns={"二级学科": "专业类", "学科评估": "学科评估结果", "层次": "院校级别"})
    st.dataframe(
        _with_linked_school_names(df2), use_container_width=True, hide_index=True, height=600,
        column_config={
            "排序": st.column_config.NumberColumn(width="small"),
            "院校名称": st.column_config.LinkColumn(
                "院校名称", display_text=_SCHOOL_LINK_DISPLAY_RE, width="medium"
            ),
            "学科评估结果": st.column_config.TextColumn(width="small"),
            "软科专业排名": st.column_config.TextColumn(width="small"),
            "软科专业评级": st.column_config.TextColumn(width="small"),
            "预警状态": st.column_config.TextColumn(width="small"),
            "招生官网": None,
            "院校级别": st.column_config.TextColumn(width="small"),
            "城市": st.column_config.TextColumn(width="small"),
            "2025最低位次": st.column_config.NumberColumn(width="small"),
            "2024最低位次": st.column_config.NumberColumn(width="small"),
            "2023最低位次": st.column_config.NumberColumn(width="small"),
        },
    )


def _to_excel(
    df_final: "pd.DataFrame",
    chong_t: list[dict],
    wen_t: list[dict],
    bao_t: list[dict],
    cols: list[str],
) -> bytes:
    """生成多 Sheet Excel：最终80志愿 + 冲/稳/保候选池。"""
    import io
    import pandas as pd
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df_final.to_excel(writer, sheet_name="最终80志愿", index=False)
        for label, data in [("冲候选池", chong_t), ("稳候选池", wen_t), ("保候选池", bao_t)]:
            pd.DataFrame(data).reindex(columns=cols).to_excel(writer, sheet_name=label, index=False)
    return buf.getvalue()


def _render_final(s: StudentInput) -> None:
    """第三步：两步走——先展示冲/稳/保候选池，确认后生成最终 80 志愿。"""
    import pandas as pd
    from collections import Counter
    from src.zhejiang.step3_generate import generate

    filtered_rows = st.session_state.get("zj_filtered_rows", [])
    if not filtered_rows:
        return

    # 若二轮筛选结果变化，清除第三步缓存
    rows_key = len(filtered_rows)
    if st.session_state.get("zj_step3_rows_key") != rows_key:
        st.session_state.pop("zj_step3_pools", None)
        st.session_state.pop("zj_step3_final", None)
        st.session_state["zj_step3_rows_key"] = rows_key

    st.divider()
    st.subheader("第三步 · 三轮分档（冲 / 稳 / 保）")
    st.caption("从二轮候选池中按历年最低位次分档，生成冲/稳/保三个候选池；最终参考 80 志愿从这三个池中按比例选出。")

    COLS = [
        "序号", "冲稳保", "专业名称", "专业代码", "二级学科", "学科评估", "软科专业排名", "软科专业评级",
        "保研率", "专业发展路径",
        "院校名称", "招生官网", "院校代码", "层次", "学制", "学费/年", "预警",
        "2025最低位次", "2024最低位次", "2023最低位次", "三年平均位次",
    ]
    COL_CFG = {
        "序号":         st.column_config.NumberColumn(width="small"),
        "冲稳保":       st.column_config.TextColumn(width="small"),
        "学科评估":     st.column_config.TextColumn(width="small"),
        "软科专业排名": st.column_config.TextColumn(width="small"),
        "软科专业评级": st.column_config.TextColumn(width="small"),
        "保研率":       st.column_config.NumberColumn(format="%.1f%%", width="small"),
        "专业发展路径": st.column_config.TextColumn(width="large"),
        "院校名称":     st.column_config.LinkColumn(
            "院校名称", display_text=_SCHOOL_LINK_DISPLAY_RE, width="medium"
        ),
        "招生官网":     None,
        "层次":         st.column_config.TextColumn(width="small"),
        "预警":         st.column_config.TextColumn(width="small"),
        "2025最低位次": st.column_config.NumberColumn(width="small"),
        "2024最低位次": st.column_config.NumberColumn(width="small"),
        "2023最低位次": st.column_config.NumberColumn(width="small"),
        "三年平均位次": st.column_config.NumberColumn(width="small"),
    }

    def _df(rows: list[dict]) -> "pd.DataFrame":
        return pd.DataFrame(rows).reindex(columns=COLS)

    # ── 3a：按位次范围分档，展示三个完整候选池（无数量限制）──────────────
    if "zj_step3_pools" not in st.session_state:
        if st.button("开始三轮分档", type="primary", use_container_width=True):
            with st.spinner("按位次分档中…"):
                chong_t, wen_t, bao_t, final = generate(s, filtered_rows)
            st.session_state["zj_step3_pools"] = (chong_t, wen_t, bao_t)
            st.session_state["zj_step3_final"] = final
            st.rerun()
        return

    chong_t, wen_t, bao_t = st.session_state["zj_step3_pools"]

    st.success(f"三轮分档完成：冲 {len(chong_t)} 条 / 稳 {len(wen_t)} 条 / 保 {len(bao_t)} 条（以下为各档全部候选，无数量限制）")
    with st.expander(f"冲 · 候选池（{len(chong_t)} 条）", expanded=True):
        st.dataframe(_with_linked_school_names(_df(chong_t)), hide_index=True, height=400, column_config=COL_CFG) if chong_t else st.info("无冲的候选")
    with st.expander(f"稳 · 候选池（{len(wen_t)} 条）", expanded=True):
        st.dataframe(_with_linked_school_names(_df(wen_t)), hide_index=True, height=400, column_config=COL_CFG) if wen_t else st.info("无稳的候选")
    with st.expander(f"保 · 候选池（{len(bao_t)} 条）", expanded=True):
        st.dataframe(_with_linked_school_names(_df(bao_t)), hide_index=True, height=400, column_config=COL_CFG) if bao_t else st.info("无保的候选")

    all_pool = chong_t + wen_t + bao_t
    if any(r.get("_baoyan_fallback") for r in all_pool):
        st.caption(
            "ℹ️ 保研率说明：异地校区（如「哈工大(威海)」「北航杭州国际校园」）"
            "无独立统计数据，以主校保研率作为参考，实际校区可能偏低，请自行核实。"
        )

    # ── 3b：从三轮候选池按数量规则选出参考 80 志愿 ────────────────────────
    st.divider()
    st.subheader("最终 · 参考 80 志愿")
    st.caption("从三轮冲/稳/保候选池中按数量规则（位次段决定冲/稳/保比例）选出 80 个志愿。")

    if "zj_step3_final" not in st.session_state or not st.session_state["zj_step3_final"]:
        st.warning("三轮候选池数量不足，请放宽筛选条件后重新进行二轮筛选。")
        return

    if st.button("从三轮候选池生成参考 80 志愿", type="primary", use_container_width=True,
                 key="btn_confirm_final"):
        st.session_state["zj_step3_show_final"] = True

    if not st.session_state.get("zj_step3_show_final"):
        return

    final = st.session_state["zj_step3_final"]
    cwb = Counter(r["冲稳保"] for r in final)
    if len(final) < 80:
        st.warning(
            f"当前只有 **{len(final)}** 个志愿（冲 {cwb.get('冲',0)} / 稳 {cwb.get('稳',0)} / 保 {cwb.get('保',0)}），"
            "不足 80 个。建议返回放宽筛选条件（减少排除项、扩大地域/专业范围）后重新生成。"
        )
    else:
        st.success(
            f"共 {len(final)} 个志愿 · 冲 {cwb.get('冲',0)} / 稳 {cwb.get('稳',0)} / 保 {cwb.get('保',0)}"
        )

    st.markdown("#### 参考 80 志愿")
    st.info(
        "💡 **仅供参考，最终志愿请自行斟酌决定。**\n\n"
        "高考填志愿是人生中的重要决策，本工具基于历年位次数据和你填写的偏好进行系统性筛选与排序，"
        "旨在帮你快速缩小范围、发现可能忽略的选项。但每个人的情况不同，"
        "建议结合学校官方招生章程、专业培养方案、个人兴趣与职业规划综合考量，"
        "必要时咨询老师、家长或专业人士。**志愿最终由你自己填报，结果由你自己负责。**"
    )
    df_final = _df(final)
    st.dataframe(_with_linked_school_names(df_final), hide_index=True, height=600, column_config=COL_CFG)

    if any(r.get("_baoyan_fallback") for r in final):
        st.caption(
            "ℹ️ 保研率说明：异地校区（如「哈工大(威海)」「北航杭州国际校园」）"
            "无独立统计数据，以主校保研率作为参考，实际校区可能偏低，请自行核实。"
        )

    st.markdown("#### 导出志愿方案")
    ec1, ec2 = st.columns(2)
    with ec1:
        st.download_button(
            "⬇️ 下载 Excel（含冲稳保分档）",
            data=_to_excel(df_final, chong_t, wen_t, bao_t, COLS),
            file_name="志愿方案.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )
    with ec2:
        st.download_button(
            "⬇️ 下载 CSV（可用 Excel 打开）",
            data=df_final.to_csv(index=False).encode("utf-8-sig"),
            file_name="志愿方案.csv",
            mime="text/csv",
            use_container_width=True,
        )
