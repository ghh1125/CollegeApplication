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

    st.markdown("**体检结果** \* — 色觉用于过滤体检受限专业（国家标准）")
    m1, m2, m3 = st.columns(3)
    with m1:
        height = st.number_input("身高 cm", min_value=0, max_value=250, value=0, step=1)
    with m2:
        color_vision = st.selectbox("色觉 *", _COLOR_VISION, index=0)
    with m3:
        vision = st.number_input("裸眼视力（较差眼，如 4.8）", min_value=0.0, max_value=5.3,
                                 value=0.0, step=0.1)

    st.markdown("**单科成绩 \*** — 用于过滤有单科最低分要求的学校（69所有数据）")
    s1, s2, s3 = st.columns(3)
    with s1:
        chinese = st.number_input("语文 *", min_value=0, max_value=150, value=0, step=1)
    with s2:
        math = st.number_input("数学 *", min_value=0, max_value=150, value=0, step=1)
    with s3:
        foreign = st.number_input("外语 *", min_value=0, max_value=150, value=0, step=1)

    submitted = st.button("保存信息", type="primary", use_container_width=True)

    if submitted:
        if not chinese or not math or not foreign:
            st.error("请填写语文、数学、外语单科成绩（必填，不能为 0）")
            return
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
                    "chinese": chinese,
                    "math": math,
                    "foreign": foreign,
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


def _build_filter_opts_by_level(rows: list[dict]) -> tuple[list[str], list[str], list[str]]:
    """从第一步筛选结果提取三级独立选项，顺序与表格一致（去重）。
    返回 (门类列表, 专业大类列表, 具体专业列表)。
    """
    _SKIP = {"—", "专科(高职)", "专科", ""}
    cat_seen: set[str] = set()
    cls_seen: set[str] = set()
    maj_seen: set[str] = set()
    cats: list[str] = []
    clss: list[str] = []
    majs: list[str] = []
    for r in rows:
        cat = r.get("类别") or ""
        cls = r.get("二级学科") or ""
        maj = r.get("专业名称") or ""
        if cat and cat not in _SKIP and cat not in cat_seen:
            cat_seen.add(cat)
            cats.append(cat)
        if cls and cls not in _SKIP and cls not in cls_seen:
            cls_seen.add(cls)
            clss.append(cls)
        if maj and maj not in maj_seen:
            maj_seen.add(maj)
            majs.append(maj)
    return cats, clss, majs


def _render_screening(s: StudentInput) -> None:
    import pandas as pd
    from src.zhejiang.step1_screen import screen

    st.divider()
    st.subheader("第一步 · 初步筛选（按省份排，浙江最前）")
    st.caption("已用：选科要求、学科门类、地域偏好、体检色觉（国家标准）、经济预算（23441条精确到专业）、"
               "单科成绩（69所学校有最低分要求）。不按位次过滤——冲稳保在第三步生成时处理。"
               "展示：学制（23456条）、学费、体检/外语要求原文。")
    with st.spinner("筛选中…"):
        rows = screen(s)
    st.session_state["zj_screen_rows"] = rows

    if not rows:
        st.warning("没有符合条件的学校专业，试着放宽学科门类或地域偏好。")
        return
    st.success(f"共筛出 {len(rows)} 条")
    df = pd.DataFrame(rows)[[
        "排序", "专业名称", "专业代码", "二级学科", "学科评估", "院校名称",
        "院校代码", "层次", "城市", "办学类型", "学制", "学费/年",
        "2025最低位次", "2024最低位次", "2023最低位次",
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
    st.caption("选项来源于上方初步筛选结果；三列分别对应三个粒度：学科门类 / 专业大类 / 具体专业。改选后过滤结果实时刷新。")

    cats, clss, majs = _build_filter_opts_by_level(rows)

    st.markdown("**非意向专业剔除**（命中任一层 → 剔除）")
    ec1, ec2, ec3 = st.columns(3)
    excl_cats = ec1.multiselect("学科门类", cats, key="zj_excl_cat")
    excl_cls  = ec2.multiselect("专业大类", clss, key="zj_excl_cls")
    excl_majs = ec3.multiselect("具体专业", majs, key="zj_excl_maj")

    st.markdown("**专业偏好**（若有选择，只保留命中行；无选择 = 不限）")
    pc1, pc2, pc3 = st.columns(3)
    pref_cats = pc1.multiselect("学科门类", cats, key="zj_pref_cat")
    pref_cls  = pc2.multiselect("专业大类", clss, key="zj_pref_cls")
    pref_majs = pc3.multiselect("具体专业", majs, key="zj_pref_maj")

    moe_warn = st.toggle(
        "过滤预警专业",
        value=False,
        help="2020-2024年全国普通本科撤销布点数量 Top30（教育部数据）；开启后剔除这些专业，关闭时仅展示⚠️标记。"
             "数据来源：教育部 moe.gov.cn 历年普通高等学校本科专业备案和审批结果。",
    )
    st.session_state["zj_intent_filter"] = {
        "excl_cats": excl_cats, "excl_cls": excl_cls, "excl_majs": excl_majs,
        "pref_cats": pref_cats, "pref_cls": pref_cls, "pref_majs": pref_majs,
        "moe_warn": moe_warn,
    }

    if st.button("开始二轮筛选", type="primary", use_container_width=True):
        from src.zhejiang.step2_filter import apply_intent_filter
        from src.zhejiang.step3_generate import classify_rows
        filtered = apply_intent_filter(rows, excl_cats, excl_cls, excl_majs,
                                       pref_cats, pref_cls, pref_majs, moe_warn)
        # 打上冲/稳/保标签（用于列展示和后续第三步）
        classify_rows(filtered, int(s.rank))
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
        "排序", "冲稳保", "专业名称", "专业代码", "二级学科", "学科评估", "院校名称",
        "预警状态", "院校代码", "层次", "城市", "办学类型", "学制", "学费/年",
        "2025最低位次", "2024最低位次", "2023最低位次",
    ]].rename(columns={"二级学科": "专业类", "学科评估": "学科评估结果", "层次": "院校级别"})
    st.dataframe(
        df2, width="stretch", hide_index=True, height=600,
        column_config={
            "排序": st.column_config.NumberColumn(width="small"),
            "冲稳保": st.column_config.TextColumn(width="small"),
            "学科评估结果": st.column_config.TextColumn(width="small"),
            "预警状态": st.column_config.TextColumn(width="small"),
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
    st.subheader("第三步 · 生成参考 80 志愿")

    COLS = [
        "序号", "冲稳保", "专业名称", "专业代码", "二级学科", "学科评估", "保研率", "专业发展路径",
        "院校名称", "院校代码", "层次", "学制", "学费/年", "预警",
        "2025最低位次", "2024最低位次", "2023最低位次", "三年平均位次",
    ]
    COL_CFG = {
        "序号":         st.column_config.NumberColumn(width="small"),
        "冲稳保":       st.column_config.TextColumn(width="small"),
        "学科评估":     st.column_config.TextColumn(width="small"),
        "保研率":       st.column_config.NumberColumn(format="%.1f%%", width="small"),
        "专业发展路径": st.column_config.TextColumn(width="large"),
        "层次":         st.column_config.TextColumn(width="small"),
        "预警":         st.column_config.TextColumn(width="small"),
        "2025最低位次": st.column_config.NumberColumn(width="small"),
        "2024最低位次": st.column_config.NumberColumn(width="small"),
        "2023最低位次": st.column_config.NumberColumn(width="small"),
        "三年平均位次": st.column_config.NumberColumn(width="small"),
    }

    def _df(rows: list[dict]) -> "pd.DataFrame":
        return pd.DataFrame(rows).reindex(columns=COLS)

    # ── 阶段一：生成并展示冲/稳/保候选池 ──────────────────────────────────
    if "zj_step3_pools" not in st.session_state:
        if st.button("生成参考候选池（冲/稳/保三档）", type="primary", use_container_width=True):
            with st.spinner("计算候选池…"):
                chong_t, wen_t, bao_t, final = generate(s, filtered_rows)
            st.session_state["zj_step3_pools"] = (chong_t, wen_t, bao_t)
            st.session_state["zj_step3_final"] = final   # 一并缓存，避免重复计算
            st.rerun()
        return

    chong_t, wen_t, bao_t = st.session_state["zj_step3_pools"]

    st.info(
        f"候选池已生成：冲 {len(chong_t)} 条 / 稳 {len(wen_t)} 条 / 保 {len(bao_t)} 条。"
        "查看下方三档后，点击「确认生成最终 80 志愿」。"
    )
    with st.expander(f"冲 · 候选池（{len(chong_t)} 条）", expanded=True):
        st.dataframe(_df(chong_t), hide_index=True, height=400, column_config=COL_CFG) if chong_t else st.info("无冲的候选")
    with st.expander(f"稳 · 候选池（{len(wen_t)} 条）", expanded=True):
        st.dataframe(_df(wen_t), hide_index=True, height=400, column_config=COL_CFG) if wen_t else st.info("无稳的候选")
    with st.expander(f"保 · 候选池（{len(bao_t)} 条）", expanded=True):
        st.dataframe(_df(bao_t), hide_index=True, height=400, column_config=COL_CFG) if bao_t else st.info("无保的候选")

    # ── 阶段二：确认后展示最终 80 志愿 ────────────────────────────────────
    if "zj_step3_final" not in st.session_state or not st.session_state["zj_step3_final"]:
        st.warning("二轮筛选结果不足，请放宽筛选条件后重新进行二轮筛选。")
        return

    st.divider()
    if st.button("确认，生成参考 80 志愿 ✓", type="primary", use_container_width=True,
                 key="btn_confirm_final"):
        st.session_state["zj_step3_show_final"] = True

    if not st.session_state.get("zj_step3_show_final"):
        return

    final = st.session_state["zj_step3_final"]
    cwb = Counter(r["冲稳保"] for r in final)
    st.success(
        f"共 {len(final)} 个志愿 · 冲 {cwb.get('冲',0)} / 稳 {cwb.get('稳',0)} / 保 {cwb.get('保',0)}"
    )

    st.markdown("#### 参考 80 志愿")
    df_final = _df(final)
    st.dataframe(df_final, hide_index=True, height=600, column_config=COL_CFG)

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
