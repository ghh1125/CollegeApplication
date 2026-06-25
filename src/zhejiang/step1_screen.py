"""浙江第一步：初步筛选（铺出候选池）。

用用户已填、且**有数据**的维度筛选「学校+专业」，组装成表。
**不按位次过滤**——选科/学科匹配的全部列出，冲稳保留到第二步。
按 省份排序（浙江最前）→ 同省按大学 → 同校按 2025 位次。

参与筛选：选科(7选3) / 学科门类(一级) / 地域偏好 / 体检色觉(国家标准) / 经济预算(学费) / 单科最低分。

输出键（UI 显示名见 ui 层）：
  排序 | 专业名称 | 专业代码 | 二级学科(=专业类) | 学科评估 | 类别(门类) |
  院校名称 | 院校代码 | 层次(=院校级别) | 城市 | 办学类型 | 学制 | 学费/年 |
  2026计划数 | 选科要求 | 单科要求 |
  培养安排备注(校区/外语门槛，从专业名称拆出，避免污染历史位次匹配) |
  省份 | 2025/2024/2023最低分 | 2025/2024/2023最低位次
"""

from __future__ import annotations

import json
import re
from typing import Any

from db import get_conn
from src.zhejiang.reference import SCHOOL_LEVEL_MAP
from src.zhejiang.rank_utils import _lookup_discipline_code, normalize_major_name
from src.zhejiang.input.disciplines import CATEGORY_NAMES, MAJOR_CLASS_NAMES
from src.zhejiang.input.medical_rules import conditions_for, restricted_classes, restricted_majors
from src.zhejiang.input.student_input import Budget

YEAR = 2026
REF_YEARS = (2025, 2024, 2023)

# 2020-2024全国普通本科撤销布点数量 Top30（数据来源：教育部）
# 撤销多 = 就业市场需求萎缩信号；展示预警标记，二轮可选过滤。
WARN_MAJORS_2020_2024: frozenset[str] = frozenset([
    "信息管理与信息系统", "公共事业管理", "信息与计算科学", "市场营销",
    "产品设计", "电子信息科学与技术", "服装与服饰设计", "工业设计",
    "网络工程", "广告学", "动画", "生物技术", "测控技术与仪器",
    "社会工作", "电子商务", "教育技术学", "自然地理与资源环境",
    "旅游管理", "汽车服务工程", "生物工程", "行政管理", "应用化学",
    "环境科学", "工业工程", "广播电视学", "汉语国际教育",
    "酒店管理", "应用统计学", "秘书学", "材料化学",
])
# 候选位次窗口：只保留 2025位次 落在 [考生位次×reach, 考生位次×safe] 内的专业，
# 两头太离谱的都砍掉（默认 ±20%，倍率随画像在 persona.py 调）。
# 选科：用户输入用「政治」，库里用「思想政治」
_SUBJECT_ALIASES = {"政治": "思想政治", "思政": "思想政治", "生物学": "生物", "信息技术": "技术", "通用技术": "技术"}
# 专业类名 → 4 位码（大类招生如「计算机类」直接按类名映射）
_CLASSNAME_TO_CODE = {name: code for code, name in MAJOR_CLASS_NAMES.items()}


def _parse_tuition_amounts(text: str) -> list[int]:
    """Extract all parseable tuition numbers (元/年) from charter text."""
    if not text:
        return []
    # Pattern A: "N元/学年"  "N元/年"  "N元/生/学年"
    pats = [
        r'(\d[\d,]*)\s*元\s*[/／]\s*(?:学年|年|生)',
        # Pattern B: "每学年N元"  "每生每学年N元"  "每年N元"
        r'每\S{0,4}?(?:学年|年)\s*(\d[\d,]*)\s*元',
        # Pattern C: "X元（每生每学年）"
        r'(\d[\d,]*)\s*元[^，。；\n]{0,10}?(?:每生每学年|每学年|每年)',
    ]
    result = []
    for pat in pats:
        for n in re.findall(pat, text):
            try:
                v = int(n.replace(',', ''))
                if 1000 <= v <= 300000:
                    result.append(v)
            except ValueError:
                pass
    return list(set(result))


def _subject_score_ok(major_name: str, score_req: dict, scores: Any) -> bool:
    """Check student's single-subject scores against school's charter requirements."""
    if not score_req:
        return True
    has_any = scores.chinese is not None or scores.math is not None or scores.foreign is not None
    if not has_any:
        return True

    def _get(subj: str) -> int | None:
        return {"chinese": scores.chinese, "math": scores.math, "foreign": scores.foreign}.get(subj)

    for subj, min_score in score_req.get("school_wide", {}).items():
        s = _get(subj)
        if s is not None and s < min_score:
            return False
    for pat in score_req.get("by_pattern", []):
        # pattern 是专业名关键词(如"中外合作")，需要检查它是否出现在(完整)专业名里，
        # 不是反过来检查专业名是否是这个短关键词的子串(那样几乎永远不成立)。
        if pat["pattern"] in major_name or pat["pattern"] in _norm(major_name):
            s = _get(pat["subject"])
            if s is not None and s < pat["min"]:
                return False
    return True


def _direct_subject_score_ok(score_req: dict, scores: Any) -> bool:
    """Check row-level single-subject score requirements."""

    if not score_req:
        return True
    has_any = scores.chinese is not None or scores.math is not None or scores.foreign is not None
    if not has_any:
        return True
    values = {"chinese": scores.chinese, "math": scores.math, "foreign": scores.foreign}
    for subject, min_score in score_req.items():
        score = values.get(subject)
        if score is not None and min_score is not None and score < int(min_score):
            return False
    return True


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


def _load_json_dict(value: str | None) -> dict:
    if not value:
        return {}
    try:
        loaded = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _tuition_display(tuition: Any, tuition_text: str | None, fallback_text: str | None = None) -> str:
    text = str(tuition_text or "").strip()
    if text and not re.fullmatch(r"\d+", text):
        return text
    if tuition is not None:
        return f"{int(tuition):,}元/年"
    if text:
        return f"{int(text):,}元/年"
    return fallback_text or "—"


def _table_has_rows(conn: Any, table: str, year: int) -> bool:
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    if not exists:
        return False
    row = conn.execute(f"SELECT 1 FROM {table} WHERE year=? LIMIT 1", (year,)).fetchone()
    return row is not None


def _plan_source_for_year(conn: Any, year: int) -> tuple[str, int]:
    """Return the table/year used for current-year admission plans.

    2026 plans live in a dedicated table so the 2025-derived admission_plan stays
    unchanged. If the 2026 table has not been populated yet, fall back to 2025.
    """
    if year == 2026 and _table_has_rows(conn, "admission_plan_2026", 2026):
        return "admission_plan_2026", 2026
    if year == 2026:
        return "admission_plan", 2025
    return "admission_plan", year


def _light_norm_major(name: str) -> str:
    """轻度归一化：统一全半角括号/空格，但**保留**括号内容。

    与 normalize_major_name 不同——"数字媒体技术" 和 "数字媒体技术(中外合作办学)"
    是两个完全不同的专业，不能因为去掉括号而被合并成同一个 key。
    """
    text = re.sub(r"\s+", "", str(name or "").strip())
    return text.replace("（", "(").replace("）", ")")


def _history_for_program(
    school_code: str,
    major_code: str,
    school_name: str,
    major_name: str,
    code_hist: dict[tuple[str, str], dict[int, int]],
    name_hist: dict[tuple[str, str], dict[int, int]],
    name_hist_loose: dict[tuple[str, str], dict[int, int]] | None = None,
) -> dict[int, int]:
    """Resolve historical ranks by code first, then by school-major name.

    专业名匹配分两层：
    1. 精确层（保留括号）：区分"数字媒体技术"和"数字媒体技术(中外合作办学)"等不同子方向。
    2. 模糊层（去括号兜底）：仅用于 2026 新专业措辞与历年不完全一致时的兜底，且仅在
       该模糊 key 对应的历年数据**没有歧义**（同年所有变体位次一致）时才使用，
       避免把不同子方向的位次互相覆盖。
    """
    result: dict[int, int] = {}
    exact_key = (school_name, _light_norm_major(major_name))
    if exact_key in name_hist:
        result.update(name_hist[exact_key])
    elif name_hist_loose is not None:
        loose_key = (school_name, normalize_major_name(major_name))
        result.update(name_hist_loose.get(loose_key, {}))
    result.update(code_hist.get((school_code, major_code), {}))
    return result


def _display_major_code(
    raw_major_code: str,
    province_major_code: str = "",
) -> str:
    """Return the user-facing 浙江专业代号 (NOT the MOE national_code).

    `ENR2026-*` is only an internal stable key generated from the 2026 scraped
    plan row. It is not Zhejiang's application major code, so never show it to
    users as "专业代码". Six-digit national catalog codes such as 080901 are not
    Zhejiang application codes either.
    """
    def _valid(code: str) -> bool:
        code = str(code or "").strip().upper()
        if not code or code.startswith("ENR2026"):
            return False
        if re.fullmatch(r"\d{6}", code):
            return False
        return bool(re.fullmatch(r"[0-9A-Z]{1,4}", code))

    province_code = str(province_major_code or "").strip().upper()
    if _valid(province_code):
        return province_code

    raw_code = str(raw_major_code or "").strip().upper()
    if _valid(raw_code):
        return raw_code
    return "—"


def _level_label(school_name: str) -> str:
    import re as _re

    def _norm(s: str) -> str:
        return s.replace("（", "(").replace("）", ")")

    n985 = {_norm(x) for x in SCHOOL_LEVEL_MAP["985"]}
    n211 = {_norm(x) for x in SCHOOL_LEVEL_MAP["211"]}
    n双  = {_norm(x) for x in SCHOOL_LEVEL_MAP["双一流"]}

    def _candidates(name: str) -> list[str]:
        """返回该校所有需要查表的候选名（自身 + 主校）。"""
        result = [_norm(name)]
        # 去尾部括号（校区/城市限定）
        stripped = _re.sub(r"[（(][^）)]*[）)]$", "", name).strip()
        if stripped != name:
            result.append(_norm(stripped))
        # 无括号分校/校区：「XX大学YY分校/校区」→「XX大学」
        m = _re.match(r"^(.+?大学).+(分校|校区)$", name)
        if m:
            result.append(_norm(m.group(1)))
        # 同时保留中间的城市限定括号：「XX大学(城市)YY校区」→「XX大学(城市)」
        # 部分学校(如中国石油大学)的211/双一流身份是按校区分别认定的，
        # 裸名"中国石油大学"不在名单里，只有"中国石油大学（北京）"在。
        m3 = _re.match(r"^(.+?大学[（(][^）)]+[）)]).+(分校|校区)$", name)
        if m3:
            result.append(_norm(m3.group(1)))
        # 医学院/医学部：「XX大学医学院/部」→「XX大学」
        m2 = _re.match(r"^(.+?大学)医学[院部]$", name)
        if m2:
            result.append(_norm(m2.group(1)))
        return result

    cands = _candidates(school_name)
    is985 = any(c in n985 for c in cands)
    is211 = any(c in n211 for c in cands)
    is双  = any(c in n双  for c in cands)

    # 数据验证：985 ⊆ 211 ⊆ 双一流，全部成立，故按全部命中档次复合展示。
    if is985:
        return "985/211/双一流"
    if is211:
        return "211/双一流"
    if is双:
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
    # (school_name, 轻度归一化但保留括号的专业名) → {year: min_rank}; 精确层，
    # 用于区分"数字媒体技术"和"数字媒体技术(中外合作办学)"等不同子方向。
    hist_name: dict[tuple[str, str], dict[int, int]] = {}
    # (school_name, 完全去括号归一化专业名) → {(year, rank), ...}；用于检测模糊层歧义
    _loose_raw: dict[tuple[str, str], dict[int, set[int]]] = {}
    for sc, sn, mc, mn, yr, rank in conn.execute(
        """
        SELECT school_code, school_name, major_code, major_name, year, min_rank
        FROM historical_cutoff
        WHERE year IN (2025,2024,2023)
        """
    ):
        if rank:
            hist.setdefault((str(sc), str(mc)), {})[int(yr)] = int(rank)
            hist_name.setdefault((str(sn), _light_norm_major(mn)), {})[int(yr)] = int(rank)
            loose_key = (str(sn), normalize_major_name(mn))
            _loose_raw.setdefault(loose_key, {}).setdefault(int(yr), set()).add(int(rank))
    # 模糊层兜底：仅当某年所有同名（去括号后）变体的位次完全一致（即没有歧义）时才采用，
    # 否则该年宁可留空，也不要把不同子方向的位次互相覆盖展示错误数据。
    hist_name_loose: dict[tuple[str, str], dict[int, int]] = {}
    for key, year_ranks in _loose_raw.items():
        safe = {yr: next(iter(ranks)) for yr, ranks in year_ranks.items() if len(ranks) == 1}
        if safe:
            hist_name_loose[key] = safe
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
    # school_name → 办学类型（school_profile.school_nature）; 软科排名；招生官网
    ruanke: dict[str, int | None] = {}
    nature: dict[str, str] = {}
    admission_url: dict[str, str] = {}
    for sn, nat, rk, url in conn.execute(
        "SELECT school_name, school_nature, ruanke_rank, undergraduate_admission_url FROM school_profile"
    ):
        nature[sn] = nat or ""
        ruanke[sn] = rk  # None if unranked
        admission_url[sn] = url or ""
    # school_name → charter fields (2026)
    charter: dict[str, dict] = {}
    for sn, tuition, physical, language, rules in conn.execute(
        """SELECT school_name, tuition_text, physical_requirement_text,
                  language_requirement_text, admission_rules_text
           FROM admission_charter WHERE year=2026 AND fetch_status='ok'"""
    ):
        amounts = _parse_tuition_amounts(tuition or "")
        charter[sn] = {
            "tuition_text": tuition or "",
            "physical_text": physical or "",
            "language_text": language or "",
            "rules_text": rules or "",
            "tuition_amounts": amounts,  # parsed numbers for budget filter
        }
    # school_name → subject_min_scores_json (from school_profile)
    subj_scores: dict[str, dict] = {}
    for sn, js in conn.execute(
        "SELECT school_name, subject_min_scores_json FROM school_profile"
        " WHERE subject_min_scores_json IS NOT NULL AND subject_min_scores_json != ''"
    ):
        try:
            subj_scores[sn] = json.loads(js)
        except (json.JSONDecodeError, TypeError):
            pass
    # (school_name, major_name) → {ranking, grade}（软科专业排名 2026）
    ruanke_major: dict[tuple[str, str], dict] = {}
    for sn, mn, rk, gd in conn.execute(
        "SELECT school_name, major_name, ranking, grade FROM ruanke_major_rank WHERE year=2026"
    ):
        ruanke_major[(sn, mn)] = {"ranking": rk or "—", "grade": gd or "—"}
    return {"name2code": name2code, "hist": hist, "hist_name": hist_name, "hist_name_loose": hist_name_loose,
            "disc": disc, "prov": prov, "city": city,
            "nature": nature, "ruanke": ruanke, "charter": charter, "subj_scores": subj_scores,
            "ruanke_major": ruanke_major, "admission_url": admission_url}


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
        plan_table, plan_year = _plan_source_for_year(conn, year)
        plan_columns = {row[1] for row in conn.execute(f"PRAGMA table_info({plan_table})")}
        province_code_expr = "province_major_code" if "province_major_code" in plan_columns else "''"
        plan_count_expr = "plan_count" if "plan_count" in plan_columns else "NULL"
        subject_text_expr = (
            "subject_requirement_text"
            if "subject_requirement_text" in plan_columns
            else "subject_requirement"
            if "subject_requirement" in plan_columns
            else "''"
        )
        note_expr = "training_note" if "training_note" in plan_columns else "''"
        tuition_text_expr = "tuition_text" if "tuition_text" in plan_columns else "''"
        single_subject_text_expr = (
            "single_subject_requirement_text"
            if "single_subject_requirement_text" in plan_columns
            else "''"
        )
        single_subject_json_expr = (
            "single_subject_requirement_json"
            if "single_subject_requirement_json" in plan_columns
            else "''"
        )
        history_exprs = []
        for hist_year in REF_YEARS:
            for suffix in ("score", "rank"):
                column = f"hist_{hist_year}_min_{suffix}"
                history_exprs.append(column if column in plan_columns else "NULL")
        rows_raw = conn.execute(
            f"""SELECT school_code, school_name, major_code, {province_code_expr}, major_name,
                       subject_requirement_json, tuition, {tuition_text_expr}, duration,
                       {plan_count_expr}, {subject_text_expr}, {note_expr},
                       {single_subject_text_expr}, {single_subject_json_expr},
                       {", ".join(history_exprs)}
                FROM {plan_table} WHERE year=?""", (plan_year,)
        ).fetchall()

    name2code, hist, hist_name, disc = lk["name2code"], lk["hist"], lk["hist_name"], lk["disc"]
    hist_name_loose = lk["hist_name_loose"]
    prov, city, nature, ruanke, charter = lk["prov"], lk["city"], lk["nature"], lk["ruanke"], lk["charter"]
    admission_url = lk["admission_url"]
    subj_scores = lk["subj_scores"]
    budget = student.budget
    out: list[dict] = []
    for row in rows_raw:
        (
            sc, sn, mc, province_mc, mn, req, tuition, tuition_text, duration,
            plan_count, subject_text, training_note, single_subject_text, single_subject_json,
            h2025_score, h2025_rank, h2024_score, h2024_rank, h2023_score, h2023_rank,
        ) = row
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
        # 5. 经济预算：≤8000 时剔除高学费；>8000 表示不差钱（含所有学费），不做过滤
        if budget == Budget.LE_8000:
            if tuition is not None:
                if tuition > 8000:
                    continue
            else:
                amounts = charter.get(sn, {}).get("tuition_amounts", [])
                if amounts and min(amounts) > 8000:
                    continue
        # 6. 单科成绩：有要求数据的参与筛选，无数据的通过
        row_score_req = _load_json_dict(single_subject_json)
        if row_score_req:
            if not _direct_subject_score_ok(row_score_req, student.subject_scores):
                continue
        else:
            score_req = subj_scores.get(sn, {})
            if score_req and not _subject_score_ok(mn, score_req, student.subject_scores):
                continue

        ranks = _history_for_program(sc, mc, sn, mn, hist, hist_name, hist_name_loose)
        table_scores = {
            2025: h2025_score,
            2024: h2024_score,
            2023: h2023_score,
        }
        table_ranks = {
            2025: h2025_rank,
            2024: h2024_rank,
            2023: h2023_rank,
        }
        for hist_year, table_rank in table_ranks.items():
            if table_rank:
                ranks[hist_year] = int(table_rank)
        # 学科评估：本科专业 → 研究生学科码 → 等级
        disc_code = _lookup_discipline_code(normalize_major_name(mn), raw_name=mn)
        grade = disc.get((sn, disc_code or ""), "")
        # 专科段（前2位 41-59）：直接剔除，只保留本科
        is_zhuanke = code6[:2].isdigit() and int(code6[:2]) >= 40 if code6 else False
        if is_zhuanke:
            continue
        ch = charter.get(sn, {})
        rmj = lk["ruanke_major"].get((sn, mn), {})
        # `mc` 可能是 ENR2026-* 内部键；code6 是教育部目录码。这两者都不是
        # 浙江2026志愿填报专业代号。只有 province_major_code 才能作为用户可见
        # 的“专业代码”；没有就留空，避免错误代码进入志愿表。
        display_code = _display_major_code(mc, province_mc)
        out.append({
            "专业名称": mn, "专业代码": display_code,
            "二级学科": MAJOR_CLASS_NAMES.get(class4, "—"),
            "学科评估": grade or "—",
            "软科专业排名": rmj.get("ranking", "—"),
            "软科专业评级": rmj.get("grade", "—"),
            "类别": CATEGORY_NAMES.get(men2, "—"),
            "院校名称": sn, "院校代码": sc,
            "招生官网": admission_url.get(sn, ""),
            "层次": _level_label(sn),
            "城市": city.get(sn) or "—",
            "办学类型": nature.get(sn) or "—",
            "学制": duration or "—",
            "学费/年": _tuition_display(tuition, tuition_text, ch.get("tuition_text")),
            "2026计划数": plan_count,
            "选科要求": subject_text or "—",
            "单科要求": single_subject_text or "—",
            "体检要求": ch.get("physical_text") or "—",
            "外语要求": ch.get("language_text") or "—",
            "培养安排备注": training_note or "—",
            "章程": "有" if ch else "—",
            "预警": any(w in mn for w in WARN_MAJORS_2020_2024),
            "省份": prov.get(sn, ""),
            "_ruanke_rank": ruanke.get(sn),
            "2025最低分": table_scores.get(2025),
            "2025最低位次": ranks.get(2025),
            "2024最低分": table_scores.get(2024),
            "2024最低位次": ranks.get(2024),
            "2023最低分": table_scores.get(2023),
            "2023最低位次": ranks.get(2023),
        })

    # 排序：省份(浙江最前) → 软科排名(有排名优先，小=好) → 无排名按校名 → 2025位次
    def _key(r: dict) -> tuple:
        p = r["省份"]
        rk = r["_ruanke_rank"]
        r25 = r["2025最低位次"]
        return (p != HOME_PROVINCE, p, rk is None, rk or 0, r["院校名称"], r25 is None, r25 or 0)

    out.sort(key=_key)
    return out


def screen(student: Any, year: int = YEAR) -> list[dict]:
    """第一步初步筛选：选科 / 学科门类 / 地域 / 体检 / 预算 / 单科成绩。
    不做位次过滤——冲/稳/保分档在第三步生成志愿时处理。
    按 省份(浙江最前) → 软科排名 → 2025位次 排序并编号。
    """
    out = list(_full_pool(student, year))
    for i, r in enumerate(out, 1):
        r["排序"] = i
    return out
