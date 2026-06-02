"""
Build major_subject_requirement and update admission_plan with subject requirements.

Flow:
  Step 0  Parse subject_requirement.pdf → data/raw/subject_req_parsed.csv (skip if exists)
  Step 1  Aggregate CSV by normalized_major_name → major_subject_requirement (source=STANDARD)
  Step 2  3-tier match on admission_plan:
            EXACT      school_name + major_name direct hit in CSV
            MAJOR_NAME normalized major_name hit in major_subject_requirement
            CATEGORY   CATEGORY_DEFAULTS fallback
            UNKNOWN    need_review = 1
  Step 3  Print match report
"""

from __future__ import annotations

import csv
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

RAW_DIR = PROJECT_ROOT / "data" / "raw"
PDF_PATH = RAW_DIR / "subject_requirement.pdf"
CSV_PATH = RAW_DIR / "subject_req_parsed.csv"

SUBJECT_NAMES = ("物理", "化学", "生物", "思想政治", "历史", "地理", "技术")

CATEGORY_DEFAULTS: dict[str, dict] = {
    "理学":   {"type": "ANY_ONE", "subjects": ["物理", "化学", "生物"]},
    "工学":   {"type": "ANY_ONE", "subjects": ["物理", "化学"]},
    "医学":   {"type": "ALL_REQUIRED", "subjects": ["化学", "生物"]},
    "农学":   {"type": "ANY_ONE", "subjects": ["化学", "生物"]},
    "经济学": {"type": "NONE", "subjects": []},
    "管理学": {"type": "NONE", "subjects": []},
    "法学":   {"type": "NONE", "subjects": []},
    "文学":   {"type": "NONE", "subjects": []},
    "历史学": {"type": "NONE", "subjects": []},
    "教育学": {"type": "NONE", "subjects": []},
    "艺术学": {"type": "NONE", "subjects": []},
}

# Ordered rules: first keyword match wins per major name
_CATEGORY_RULES: list[tuple[str, list[str]]] = [
    ("医学",  ["医学", "护理", "临床", "口腔", "预防医学", "中医", "中药", "药学", "检验", "麻醉"]),
    ("农学",  ["农学", "农业", "农林", "林学", "园艺", "植物保护", "动物医学", "动物科学", "食品科学"]),
    ("理学",  ["物理学", "数学", "化学类", "天文学", "地质学", "大气科学", "地球物理",
               "地理信息", "心理学", "力学", "生物科学", "生态学", "统计学", "信息与计算", "理科"]),
    ("工学",  ["工程", "计算机", "软件工程", "电子信息", "机械", "材料科学", "建筑",
               "土木", "化工", "电气", "自动化", "通信", "人工智能", "光电", "微电子",
               "机器人", "航空", "航天", "数字媒体技术", "工业设计", "测绘", "工科", "能源", "车辆"]),
    ("历史学", ["历史学", "考古", "文物", "博物馆"]),
    ("教育学", ["教育学", "学前教育", "特殊教育", "体育", "运动", "武术"]),
    ("艺术学", ["艺术学", "音乐", "美术", "舞蹈", "戏剧", "影视", "动画", "绘画", "摄影"]),
    ("经济学", ["经济学", "金融", "财政", "贸易", "保险", "经济"]),
    ("管理学", ["管理学", "工商管理", "会计", "财务", "市场营销", "物流", "行政管理",
               "土地资源", "信息管理", "管理"]),
    ("法学",  ["法学", "政治学", "社会学", "马克思主义", "公安", "国际政治", "社会工作"]),
    ("文学",  ["语言文学", "新闻", "传播", "出版", "英语", "俄语", "德语", "法语",
               "日语", "阿拉伯", "西班牙语", "汉语言", "文学", "翻译", "人文", "文科"]),
]

# Requirement texts that carry no subject information
_SKIP_REQ = frozenset({"本科", "高职（专科）", "专科", "高职(专科)"})


# ─── helpers ────────────────────────────────────────────────────────────────

def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _normalize_school(name: str) -> str:
    return re.sub(r"[\s　]+", "", _clean(name))


def _normalize_major_for_lookup(name: str) -> str:
    """Strip parenthesized qualifiers for MAJOR_NAME fallback matching only."""
    name = re.sub(r"[（(][^)）]*[)）]", "", _clean(name))
    return re.sub(r"[\s　]+", "", name.strip())


def _exact_key(school: str, major: str) -> tuple[str, str]:
    """Key for EXACT matching: normalise whitespace/brackets but keep content."""
    def _norm(s: str) -> str:
        s = _clean(s).replace("（", "(").replace("）", ")")
        return re.sub(r"[\s　]+", "", s)
    return _norm(school), _norm(major)


def _parse_requirement(text: str) -> tuple[str, list[str]]:
    """
    Parse a 选考科目要求 cell into (requirement_type, subjects).

    PDF uses only these patterns:
      不提科目要求               → NONE, []
      X(1门科目考生必须选考…)    → ALL_REQUIRED, [X]
      X,Y(2门科目考生均须选考…)  → ALL_REQUIRED, [X, Y]
      X,Y,Z(3门…均须选考…)      → ALL_REQUIRED, [X, Y, Z]
    Cells containing only level text (本科/专科) are treated as NONE.
    """
    text = _clean(text)
    if not text or text in _SKIP_REQ or "不提科目要求" in text or "不限" in text:
        return "NONE", []
    subjects = [s for s in SUBJECT_NAMES if s in text]
    if subjects and ("均须选考" in text or "必须选考" in text):
        return "ALL_REQUIRED", subjects
    if subjects:
        return "CUSTOM", subjects
    return "NONE", []


def _detect_category(major_name: str) -> str | None:
    for category, keywords in _CATEGORY_RULES:
        if any(kw in major_name for kw in keywords):
            return category
    return None


# ─── Step 0: PDF → CSV ──────────────────────────────────────────────────────

def parse_pdf_to_csv(pdf_path: Path = PDF_PATH, csv_path: Path = CSV_PATH) -> int:
    """Extract all subject requirement rows from PDF and write CSV. Returns row count."""
    try:
        import pdfplumber
    except ImportError:
        raise RuntimeError("pdfplumber not installed: pip install pdfplumber")

    fieldnames = [
        "province", "school_name", "major_name", "normalized_major_name",
        "level", "requirement_type", "requirement_subjects", "requirement_text",
    ]
    rows: list[dict] = []

    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            for table in page.find_tables():
                for row in table.extract():
                    if not row or len(row) < 6:
                        continue
                    province, school_name, major_name, _sub_majors, level, req_text = row[:6]
                    school_name = _clean(school_name)
                    major_name = _clean(major_name)
                    if not school_name or not major_name or school_name == "院校名称":
                        continue
                    req_type, req_subjects = _parse_requirement(req_text)
                    rows.append({
                        "province": _clean(province),
                        "school_name": school_name,
                        "major_name": major_name,
                        "normalized_major_name": _normalize_major_for_lookup(major_name),
                        "level": _clean(level),
                        "requirement_type": req_type,
                        "requirement_subjects": json.dumps(req_subjects, ensure_ascii=False),
                        "requirement_text": _clean(req_text),
                    })

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    return len(rows)


# ─── Step 1: CSV → major_subject_requirement ────────────────────────────────

def build_major_req_table(csv_path: Path = CSV_PATH, conn: Any = None) -> int:
    """
    Aggregate CSV by normalized_major_name.
    When same normalized name has conflicting requirements, take the most frequent.
    """
    from db import get_cursor

    records: list[dict] = []
    with csv_path.open(encoding="utf-8-sig", newline="") as fh:
        records = list(csv.DictReader(fh))

    grouped: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        key = r["normalized_major_name"]
        if key:
            grouped[key].append(r)

    upsert_sql = """
        INSERT INTO major_subject_requirement
            (normalized_major_name, major_category, requirement_type,
             requirement_subjects, requirement_text, source)
        VALUES (?, ?, ?, ?, ?, 'STANDARD')
        ON CONFLICT (normalized_major_name, source) DO UPDATE SET
            requirement_type     = EXCLUDED.requirement_type,
            requirement_subjects = EXCLUDED.requirement_subjects,
            requirement_text     = EXCLUDED.requirement_text,
            major_category       = EXCLUDED.major_category
    """

    inserted = 0
    with get_cursor(conn) as cursor:
        for norm_name, group in grouped.items():
            counter = Counter(
                (r["requirement_type"], r["requirement_subjects"]) for r in group
            )
            (best_type, best_subjects_json), _ = counter.most_common(1)[0]
            category = _detect_category(norm_name) or _detect_category(group[0]["major_name"])
            sample_text = group[0]["requirement_text"]
            cursor.execute(
                upsert_sql,
                (norm_name, category, best_type, best_subjects_json, sample_text),
            )
            inserted += 1

    return inserted


# ─── Step 2: 3-tier match → update admission_plan ───────────────────────────

def update_admission_plan(csv_path: Path = CSV_PATH, conn: Any = None) -> dict[str, int]:
    """
    Update admission_plan.subject_requirement_json / subject_req_source / need_review
    using 3-tier priority matching.
    """
    from db import get_cursor

    # Build EXACT index: (norm_school, norm_major) → (req_type, subjects, req_text)
    exact_index: dict[tuple[str, str], tuple[str, list, str]] = {}
    with csv_path.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            key = _exact_key(row["school_name"], row["major_name"])
            req_type = row["requirement_type"]
            subjects = json.loads(row["requirement_subjects"] or "[]")
            exact_index.setdefault(key, (req_type, subjects, row["requirement_text"]))

    # Build MAJOR_NAME index from major_subject_requirement table
    major_name_index: dict[str, tuple[str, list]] = {}
    with get_cursor(conn) as cursor:
        cursor.execute(
            "SELECT normalized_major_name, requirement_type, requirement_subjects "
            "FROM major_subject_requirement WHERE source = 'STANDARD'"
        )
        for norm_name, req_type, subj_json in cursor.fetchall():
            major_name_index[norm_name] = (req_type, json.loads(subj_json or "[]"))

    # Fetch all admission_plan rows
    with get_cursor(conn) as cursor:
        cursor.execute("SELECT id, school_name, major_name FROM admission_plan")
        plan_rows = cursor.fetchall()

    counts: dict[str, int] = {"EXACT": 0, "MAJOR_NAME": 0, "CATEGORY": 0, "UNKNOWN": 0}
    update_params: list[tuple] = []

    for plan_id, school_name, major_name in plan_rows:
        req_type: str | None = None
        req_subjects: list = []
        req_text = ""
        source: str | None = None

        # Priority 1 – EXACT: original school + original major name
        exact = exact_index.get(_exact_key(school_name, major_name))
        if exact:
            req_type, req_subjects, req_text = exact
            source = "EXACT"

        # Priority 2 – MAJOR_NAME: strip parenthesised qualifiers
        if not source:
            norm_key = _normalize_major_for_lookup(major_name)
            match = major_name_index.get(norm_key)
            if match:
                req_type, req_subjects = match
                source = "MAJOR_NAME"

        # Priority 3 – CATEGORY: discipline defaults
        if not source:
            category = _detect_category(major_name)
            default = CATEGORY_DEFAULTS.get(category) if category else None
            if default:
                req_type = default["type"]
                req_subjects = default["subjects"]
                source = "CATEGORY"

        # UNKNOWN
        if not source:
            counts["UNKNOWN"] += 1
            update_params.append((
                json.dumps({"type": "UNKNOWN"}, ensure_ascii=False),
                "", "UNKNOWN", 1, plan_id,
            ))
            continue

        counts[source] += 1
        req_json = json.dumps({"type": req_type, "subjects": req_subjects}, ensure_ascii=False)
        update_params.append((req_json, req_text, source, 0, plan_id))

    update_sql = """
        UPDATE admission_plan
        SET subject_requirement_json = ?,
            subject_requirement_text = ?,
            subject_req_source       = ?,
            need_review              = ?
        WHERE id = ?
    """
    with get_cursor(conn) as cursor:
        cursor.executemany(update_sql, update_params)

    return counts


# ─── Step 3: report ──────────────────────────────────────────────────────────

def _print_report(counts: dict[str, int], major_req_count: int) -> None:
    total = sum(counts.values())
    unknown = counts.get("UNKNOWN", 0)
    pct = f"{unknown / total * 100:.1f}%" if total else "0%"
    print("\n=== 选考要求匹配报告 ===")
    print(f"精确匹配（学校+专业）：  {counts.get('EXACT', 0):>6} 条")
    print(f"专业名匹配：             {counts.get('MAJOR_NAME', 0):>6} 条")
    print(f"大类默认值：             {counts.get('CATEGORY', 0):>6} 条")
    print(f"UNKNOWN 需人工核对：     {unknown:>6} 条（占比 {pct}）")
    print(f"\nmajor_subject_requirement 表共 {major_req_count} 个专业标准要求")


# ─── main ────────────────────────────────────────────────────────────────────

def main() -> None:
    from db import get_conn
    from scripts.init_db import initialize_database

    # Ensure schema is up to date (adds new columns if table already exists)
    initialize_database()

    # Step 0
    if CSV_PATH.exists():
        print(f"subject_req_parsed.csv 已存在，跳过 PDF 解析")
    else:
        if not PDF_PATH.exists():
            print(f"错误：找不到 {PDF_PATH}")
            sys.exit(1)
        print("Step 0  解析 PDF → subject_req_parsed.csv ...")
        n = parse_pdf_to_csv()
        print(f"        写出 {n} 条记录")

    with get_conn() as conn:
        # Step 1
        print("Step 1  构建 major_subject_requirement 表 ...")
        major_count = build_major_req_table(CSV_PATH, conn)
        print(f"        写入 {major_count} 个专业标准要求")

        # Step 2
        print("Step 2  更新 admission_plan 选考要求 ...")
        counts = update_admission_plan(CSV_PATH, conn)

    # Step 3
    _print_report(counts, major_count)


if __name__ == "__main__":
    main()
