"""浙江第一步：初步筛选（铺出候选池）。

用用户已填、且**有数据**的维度筛选「学校+专业」，组装成表。
**不按位次过滤**——选科/学科匹配的全部列出，冲稳保留到第二步。
按 省份排序（浙江最前）→ 同省按大学 → 同校按 2025 位次。

参与筛选：选科(7选3) / 学科门类(一级) / 地域偏好 / 体检色觉。
暂不参与（缺结构化数据）：经济预算(学费) / 单科最低分 / 调剂规则。

输出键（UI 显示名见 ui 层）：
  排序 | 专业名称 | 专业代码 | 二级学科(=专业类) | 学科评估 | 类别(门类) |
  院校名称 | 院校代码 | 层次(=院校级别) | 城市 | 办学类型 | 学制 | 学费/年 |
  省份 | 2025/2024/2023最低位次
"""

from __future__ import annotations

import json
import re
from typing import Any

from db import get_conn
from src.common.reference import SCHOOL_LEVEL_MAP
from src.common.ranking.rank import _lookup_discipline_code, normalize_major_name
from src.zhejiang.input.disciplines import CATEGORY_NAMES, MAJOR_CLASS_NAMES
from src.zhejiang.input.medical_rules import conditions_for, restricted_classes, restricted_majors

YEAR = 2025
REF_YEARS = (2025, 2024, 2023)
# 候选位次窗口：只保留 2025位次 落在 [考生位次×reach, 考生位次×safe] 内的专业，
# 两头太离谱的都砍掉（默认 ±20%，倍率随画像在 persona.py 调）。
# 选科：用户输入用「政治」，库里用「思想政治」
_SUBJECT_ALIASES = {"政治": "思想政治", "思政": "思想政治", "生物学": "生物", "信息技术": "技术", "通用技术": "技术"}
# 专业类名 → 4 位码（大类招生如「计算机类」直接按类名映射）
_CLASSNAME_TO_CODE = {name: code for code, name in MAJOR_CLASS_NAMES.items()}


def _norm(name: str) -> str:
    """专业名归一化：去括号内容 + 去空白（用于匹配 national_code）。"""
    s = re.sub(r"[（(].*?[）)]", "", str(name or ""))
    return re.sub(r"\s+", "", s).strip()


def _subject_ok(req_json: str | None, selected: set[str]) -> bool:
    """学生选科是否满足该专业的选考要求。"""
    if not req_json:
        return True
    try:
        req = json.loads(req_json)
    except (json.JSONDecodeError, TypeError):
        return True
    t = req.get("type", "NONE")
    subs = req.get("subjects", [])
    if t in ("NONE", "UNKNOWN"):
        return True
    if t == "ALL_REQUIRED":
        return all(s in selected for s in subs)
    if t == "ANY_ONE":
        return (not subs) or any(s in selected for s in subs)
    if t == "CUSTOM":
        return all(s in selected for s in subs)
    return True


def _level_label(school_name: str) -> str:
    if school_name in SCHOOL_LEVEL_MAP["985"]:
        return "985"
    if school_name in SCHOOL_LEVEL_MAP["211"]:
        return "211"
    if school_name in SCHOOL_LEVEL_MAP["双一流"]:
        return "双一流"
    return "其他"


def _load_lookups(conn: Any) -> dict:
    """一次性预加载：national_code、3年位次、学科评估、学校省份。"""
    # 专业名(归一化) → national_code。同名（本科+专科）优先取本科（前2位 01-13）。
    _benke = {"01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "11", "12", "13"}
    name2code: dict[str, str] = {}
    for name, code in conn.execute("SELECT name, national_code FROM major_description WHERE national_code!=''"):
        n = _norm(name)
        cur = name2code.get(n)
        if cur and cur[:2] in _benke and code[:2] not in _benke:
            continue  # 已有本科码，不被专科码覆盖
        name2code[n] = code
    # (school_code, major_code) → {year: min_rank}
    hist: dict[tuple[str, str], dict[int, int]] = {}
    for sc, mc, yr, rank in conn.execute(
        "SELECT school_code, major_code, year, min_rank FROM historical_cutoff WHERE year IN (2025,2024,2023)"
    ):
        if rank:
            hist.setdefault((str(sc), str(mc)), {})[int(yr)] = int(rank)
    # (school_name, 研究生学科码) → grade
    disc: dict[tuple[str, str], str] = {
        (str(r[0]), str(r[1])): str(r[2])
        for r in conn.execute("SELECT school_name, discipline_code, grade FROM discipline_evaluation")
    }
    # school_name → province / city（school_master）
    prov: dict[str, str] = {}
    city: dict[str, str] = {}
    for sn, p, ct in conn.execute("SELECT school_name, province, city FROM school_master"):
        prov[sn] = p or ""
        city[sn] = ct or ""
    # school_name → 办学类型（school_profile.school_nature）
    nature = {r[0]: (r[1] or "") for r in conn.execute("SELECT school_name, school_nature FROM school_profile")}
    return {"name2code": name2code, "hist": hist, "disc": disc, "prov": prov, "city": city, "nature": nature}


HOME_PROVINCE = "浙江"


def _full_pool(student: Any, year: int = YEAR) -> list[dict]:
    """全量候选池：按 选科/学科门类/地域/体检 过滤，**不含位次过滤**。

    返回全部匹配的「学校+专业」，按 省份(浙江最前)→大学→2025位次 排序（未编号）。
    第二步冲稳保用这个（需要比考生难的「冲」）。
    """
    selected = {_SUBJECT_ALIASES.get(s, s) for s in (student.selected_subjects or [])}
    want_cats = set(getattr(student, "major_categories", []) or [])  # 一级学科：门类 2 位码
    want_classes = set(student.major_classes or [])                  # 二级学科：专业类 4 位码
    pref_provs = set(student.region.provinces) if student.region.has_preference else set()

    # 体检受限（色觉）：受限专业类 + 受限专业名
    med_conditions = conditions_for(getattr(student.medical, "color_vision", "正常"),
                                    getattr(student.medical, "naked_eye_vision", None))
    forbid_classes: set[str] = set()
    forbid_majors: set[str] = set()
    for cond in med_conditions:
        forbid_classes |= set(restricted_classes(cond))
        forbid_majors |= {_norm(m) for m in restricted_majors(cond)}

    with get_conn("zhejiang") as conn:
        lk = _load_lookups(conn)
        rows_raw = conn.execute(
            """SELECT school_code, school_name, major_code, major_name,
                      subject_requirement_json, tuition, duration
               FROM admission_plan WHERE year=?""", (year,)
        ).fetchall()

    name2code, hist, disc = lk["name2code"], lk["hist"], lk["disc"]
    prov, city, nature = lk["prov"], lk["city"], lk["nature"]
    out: list[dict] = []
    for sc, sn, mc, mn, req, tuition, duration in rows_raw:
        sc, mc = str(sc), str(mc)
        # 1. 选科
        if not _subject_ok(req, selected):
            continue
        nrm = _norm(mn)
        code6 = name2code.get(nrm, "")
        # 专业类码：优先 national_code 前4位；否则若专业名本身是大类名(如「计算机类」)直接映射
        class4 = code6[:4] if code6 else _CLASSNAME_TO_CODE.get(nrm, "")
        men2 = (code6[:2] if code6 else class4[:2])  # 门类码
        # 2. 学科门类 —— 选了一级(门类)或二级(专业类)才筛：命中任一即通过。
        #    无法归类的（试验班/专科）在筛选开启时排除。
        if want_cats or want_classes:
            hit = (class4 and class4 in want_classes) or (men2 and men2 in want_cats)
            if not hit:
                continue
        # 3. 地域偏好
        if pref_provs and prov.get(sn, "") not in pref_provs:
            continue
        # 4. 体检色觉：受限专业类 / 受限专业名 → 剔除
        if class4 and class4 in forbid_classes:
            continue
        if nrm in forbid_majors:
            continue

        ranks = hist.get((sc, mc), {})
        # 学科评估：本科专业 → 研究生学科码 → 等级
        disc_code = _lookup_discipline_code(normalize_major_name(mn), raw_name=mn)
        grade = disc.get((sn, disc_code or ""), "")
        # 专科段（前2位 41-59）：本科专业类表不含，单独标「专科(高职)」
        is_zhuanke = code6[:2].isdigit() and int(code6[:2]) >= 40 if code6 else False
        out.append({
            "专业名称": mn, "专业代码": mc,
            "二级学科": MAJOR_CLASS_NAMES.get(class4, "专科" if is_zhuanke else "—"),
            "学科评估": grade or "—",
            "类别": CATEGORY_NAMES.get(men2, "专科(高职)" if is_zhuanke else "—"),
            "院校名称": sn, "院校代码": sc,
            "层次": _level_label(sn),
            "城市": city.get(sn) or "—",
            "办学类型": nature.get(sn) or "—",
            "学制": duration or "—",          # 数据暂缺，多为 —
            "学费/年": tuition or "—",         # 数据暂缺，多为 —
            "省份": prov.get(sn, ""),
            "2025最低位次": ranks.get(2025),
            "2024最低位次": ranks.get(2024),
            "2023最低位次": ranks.get(2023),
        })

    # 排序：省份(浙江最前) → 大学 → 2025位次(无则排末尾)
    def _key(r: dict) -> tuple:
        p = r["省份"]
        r25 = r["2025最低位次"]
        return (p != HOME_PROVINCE, p, r["院校名称"], r25 is None, r25 or 0)

    out.sort(key=_key)
    return out


def screen(student: Any, year: int = YEAR) -> list[dict]:
    """第一步初步筛选（显示用）：在全量池基础上**只保留考生能上的**——
    即 2025最低位次 ≥ 考生位次（位次比你大=录取门槛比你低=你能上）。
    无 2025 位次的（缺数据）不参与，不显示。按 省份(浙江最前)→大学→位次 排序并编号。
    """
    rank = int(student.rank)
    out = [r for r in _full_pool(student, year)
           if r["2025最低位次"] is not None and r["2025最低位次"] >= rank]
    for i, r in enumerate(out, 1):
        r["排序"] = i
    return out
