"""浙江筛选 + 组装 + 排序（重构版第二步）。

用用户已填、且**有数据**的维度筛选候选「学校+专业」，组装成结果表，按 2025 最低位次排序。
参与筛选：选科(7选3) / 学科(专业类) / 地域偏好 / 体检色觉。
暂不参与（缺结构化数据）：经济预算(学费) / 单科最低分 / 调剂规则。

输出列：排序 | 专业名称(代码) | 二级学科 | 学科评估结果 | 类别(门类) |
        院校名称(代码) | 985/211/双一流/其他 | 2025/2024/2023 最低位次
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
RANK_HEADROOM = 1000  # 只看 2025位次 ≥ (考生位次 - 1000) 的专业，剔掉够不到的顶尖校
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
    # school_name → province
    prov = {r[0]: (r[1] or "") for r in conn.execute("SELECT school_name, province FROM school_master")}
    return {"name2code": name2code, "hist": hist, "disc": disc, "prov": prov}


def screen(student: Any, year: int = YEAR) -> list[dict]:
    """按学生输入筛选 + 组装 + 按 2025 位次排序，返回结果行。"""
    selected = {_SUBJECT_ALIASES.get(s, s) for s in (student.selected_subjects or [])}
    want_classes = set(student.major_classes or [])              # 专业类 4 位码
    pref_provs = set(student.region.provinces) if student.region.has_preference else set()
    rank_floor = max(1, int(student.rank) - RANK_HEADROOM)       # 2025位次下界

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
            """SELECT school_code, school_name, major_code, major_name, subject_requirement_json
               FROM admission_plan WHERE year=?""", (year,)
        ).fetchall()

    name2code, hist, disc, prov = lk["name2code"], lk["hist"], lk["disc"], lk["prov"]
    out: list[dict] = []
    for sc, sn, mc, mn, req in rows_raw:
        sc, mc = str(sc), str(mc)
        # 1. 选科
        if not _subject_ok(req, selected):
            continue
        nrm = _norm(mn)
        code6 = name2code.get(nrm, "")
        # 专业类码：优先 national_code 前4位；否则若专业名本身是大类名(如「计算机类」)直接映射
        class4 = code6[:4] if code6 else _CLASSNAME_TO_CODE.get(nrm, "")
        men2 = (code6[:2] if code6 else class4[:2])  # 门类码
        # 2. 学科（专业类）—— 用户选了才筛；无法归类的（试验班）在筛选开启时排除
        if want_classes:
            if not class4 or class4 not in want_classes:
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
        # 5. 位次筛选：只保留 2025位次 ≥ (考生位次-1000) 的专业（够不到的顶尖校剔除）
        r2025 = ranks.get(2025)
        if r2025 is None or r2025 < rank_floor:
            continue
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
            "2025最低位次": r2025,
            "2024最低位次": ranks.get(2024),
            "2023最低位次": ranks.get(2023),
        })

    # 排序：按 2025 最低位次升序
    out.sort(key=lambda r: r["2025最低位次"])
    for i, r in enumerate(out, 1):
        r["排序"] = i
    return out
