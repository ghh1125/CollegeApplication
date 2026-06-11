"""从招生章程文本提取结构化字段写回 school_profile:
  - subject_min_scores_json: 单科最低分要求 (JSON)
  - foreign_language_requirement: 外语语种要求 (TEXT)
  - charter_health_requirement: 体检色觉/视力/身高要求 (TEXT，补充 medical_rules)

单科最低分格式 (JSON):
  {
    "school_wide": {"foreign": 90},        // 全校通用
    "by_pattern": [                         // 按专业名模式
      {"pattern": "英语", "subject": "foreign", "min": 120},
      {"pattern": "中外合作", "subject": "foreign", "min": 100}
    ]
  }
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

ZHEJIANG_DB = PROJECT_ROOT / "data" / "zhejiang" / "college.db"
COMMON_DB   = PROJECT_ROOT / "data" / "common" / "common.db"

# ── 单科最低分正则 ─────────────────────────────────────────────────────────────
# 匹配: "X专业的外语单科成绩不低于N分" / "报考X专业要求英语不低于N分"
_SUBJ_MAP = {"外语": "foreign", "英语": "foreign", "日语": "foreign",
             "语文": "chinese", "数学": "math"}

# group1=专业关键词(可选)  group2=科目  group3=分数
_SCORE_RE = re.compile(
    r"(?:报考\s*)?([^，。；\n]{0,20}?专业[^，。；\n]{0,10}?)?"
    r"的?(?:高考)?\s*"
    r"(外语|英语|日语|语文|数学)"
    r"(?:单科|考试)?成绩?\s*"
    r"不(?:得)?低于\s*"
    r"(\d{2,3})\s*分",
    re.S,
)

# 更简单的兜底: "外语/英语 不低于 N 分"
_SIMPLE_RE = re.compile(
    r"(外语|英语|日语|语文|数学)[^\n，。；]{0,20}?不(?:得)?低于\s*(\d{2,3})\s*分",
    re.S,
)

# ── 外语语种要求 ──────────────────────────────────────────────────────────────
_LANG_RE = re.compile(
    r"(?:只(?:招收|录取)|限(?:招)?|仅(?:招)?|须为|要求)[^，。；\n]{0,20}?"
    r"(英语|日语|俄语|德语|法语|西班牙语|朝鲜语)[^，。；\n]{0,10}?(?:语种|考生)",
    re.S,
)


def _extract_score_requirements(text: str) -> dict:
    """从章程文本提取单科最低分，返回结构化 dict。"""
    if not text:
        return {}
    school_wide: dict[str, int] = {}
    by_pattern: list[dict] = []

    for m in _SCORE_RE.finditer(text):
        major_hint = (m.group(1) or "").strip()
        subj_raw = m.group(2)
        score = int(m.group(3))
        subj = _SUBJ_MAP.get(subj_raw, "foreign")
        if score < 50 or score > 150:
            continue
        if major_hint:
            # 从专业关键词里提取有意义的部分（去掉「报考」「的」等）
            hint = re.sub(r"(报考|考生|的|我校|我院)", "", major_hint).strip()
            if hint:
                by_pattern.append({"pattern": hint, "subject": subj, "min": score})
                continue
        # 全校通用
        if subj not in school_wide or school_wide[subj] < score:
            school_wide[subj] = score

    # 兜底：简单模式
    if not school_wide and not by_pattern:
        for m in _SIMPLE_RE.finditer(text):
            subj = _SUBJ_MAP.get(m.group(1), "foreign")
            score = int(m.group(2))
            if 50 <= score <= 150:
                if subj not in school_wide or school_wide[subj] < score:
                    school_wide[subj] = score

    result: dict = {}
    if school_wide:
        result["school_wide"] = school_wide
    if by_pattern:
        # 去重
        seen: set[str] = set()
        deduped = []
        for p in by_pattern:
            key = f"{p['pattern']}|{p['subject']}|{p['min']}"
            if key not in seen:
                seen.add(key)
                deduped.append(p)
        result["by_pattern"] = deduped
    return result


def _extract_foreign_lang(text: str) -> str:
    """提取外语语种限制描述（简短摘要）。"""
    if not text:
        return ""
    matches = _LANG_RE.findall(text)
    if not matches:
        return ""
    langs = list(dict.fromkeys(matches))  # 去重保序
    return "、".join(langs) + "语种限制"


def _run(db_path: Path, verbose: bool = False) -> dict[str, int]:
    updated = 0
    skipped = 0
    with sqlite3.connect(db_path) as conn:
        # 确保 school_profile 有新列
        try:
            conn.execute("ALTER TABLE school_profile ADD COLUMN subject_min_scores_json TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE school_profile ADD COLUMN foreign_language_requirement TEXT")
        except sqlite3.OperationalError:
            pass

        charters = conn.execute("""
            SELECT school_name,
                   COALESCE(admission_rules_text,'') || ' ' ||
                   COALESCE(language_requirement_text,'') || ' ' ||
                   COALESCE(content,'')   AS full_text,
                   physical_requirement_text
            FROM admission_charter
            WHERE year = 2026 AND fetch_status = 'ok'
        """).fetchall()

        for school_name, full_text, phys_text in charters:
            score_req = _extract_score_requirements(full_text)
            lang_req = _extract_foreign_lang(full_text)
            if not score_req and not lang_req:
                skipped += 1
                continue
            score_json = json.dumps(score_req, ensure_ascii=False) if score_req else None
            conn.execute("""
                UPDATE school_profile
                SET subject_min_scores_json = ?,
                    foreign_language_requirement = ?
                WHERE school_name = ?
            """, (score_json, lang_req or None, school_name))
            if conn.total_changes > 0 and verbose:
                print(f"  {school_name}: scores={score_req}, lang={lang_req}")
            updated += conn.total_changes
        conn.commit()
    return {"parsed": len(charters), "updated": updated, "skipped": skipped}


def main() -> None:
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--db", type=Path, default=ZHEJIANG_DB)
    p.add_argument("--verbose", "-v", action="store_true")
    p.add_argument("--both", action="store_true", help="update both zhejiang and common db")
    args = p.parse_args()

    dbs = [args.db]
    if args.both:
        dbs = [ZHEJIANG_DB, COMMON_DB]

    for db in dbs:
        stats = _run(db, verbose=args.verbose)
        print(f"{db.name}: {stats}")


if __name__ == "__main__":
    main()
