"""Build the Jiangsu SQLite DB from raw 掌上高考 JSON + shared common tables.

Pipeline:
  1. apply data/jiangsu/schema.sql  (historical_cutoff + admission_plan, group-aware)
  2. copy national tables from data/common/common.db
     (school_master / discipline_evaluation / major_description / major_subject_requirement)
  3. ingest data/jiangsu/raw/score/*  → historical_cutoff (per-major 录取位次)
  4. ingest data/jiangsu/raw/plan/*   → admission_plan (计划数/学费, + parsed 选科要求)

Run:  python -m src.jiangsu.input.ingest
"""

from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
RAW_DIR = PROJECT_ROOT / "data" / "jiangsu" / "raw"
DB_PATH = PROJECT_ROOT / "data" / "jiangsu" / "college.db"
SCHEMA_PATH = PROJECT_ROOT / "data" / "jiangsu" / "schema.sql"
COMMON_DB = PROJECT_ROOT / "data" / "common" / "common.db"
OFFICIAL_DIR = RAW_DIR / "official"
PLAN_DETAIL_DIR = RAW_DIR / "plan_details"

NATIONAL_TABLES = (
    "school_master",
    "discipline_evaluation",
    "major_description",
    "major_subject_requirement",
    "school_profile",
    "major_profile",
    "city_profile",
)


def _parse_int(value) -> int | None:
    try:
        s = str(value).strip().replace(",", "")
        return int(float(s)) if s and s not in ("-", "--", "—") else None
    except (ValueError, TypeError):
        return None


def parse_subject_requirement(sg_info: str | None) -> dict:
    """Parse 江苏 group 选科要求 text into a structured dict.

    Examples:
      "首选物理，再选化学"        → first=物理, reselect_type=ALL, reselect=[化学]
      "首选物理，再选化学和生物"  → ALL, [化学,生物]
      "首选历史，再选化学或生物"  → ANY, [化学,生物]
      "首选物理，再选不限"        → NONE, []
    """
    text = (sg_info or "").strip()
    first = "物理" if "首选物理" in text else ("历史" if "首选历史" in text else "")
    reselect_part = text.split("再选", 1)[1] if "再选" in text else ""
    subjects = [s for s in ("物理", "化学", "生物", "思想政治", "政治", "地理", "历史")
                if s in reselect_part]
    subjects = ["思想政治" if s == "政治" else s for s in subjects]
    if not reselect_part or "不限" in reselect_part:
        rtype = "NONE"
    elif "或" in reselect_part:
        rtype = "ANY"
    else:
        rtype = "ALL"
    return {"first_choice": first, "reselect_type": rtype, "reselect": subjects}


def init_db() -> sqlite3.Connection:
    """Create the Jiangsu DB: province tables from schema + national tables copied in."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))

    # copy national tables from common.db
    src = sqlite3.connect(COMMON_DB)
    for t in NATIONAL_TABLES:
        ddl = src.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (t,)
        ).fetchone()
        if not ddl:
            continue
        conn.execute(f"DROP TABLE IF EXISTS {t}")
        conn.execute(ddl[0])
        rows = src.execute(f"SELECT * FROM {t}").fetchall()
        if rows:
            ph = ",".join("?" * len(rows[0]))
            conn.executemany(f"INSERT INTO {t} VALUES ({ph})", rows)
    src.close()
    conn.commit()
    return conn


def _iter_raw(kind: str):
    """Yield (school, year, item) for every row in data/jiangsu/raw/{kind}/*/*.json."""
    base = RAW_DIR / kind
    if not base.exists():
        return
    for f in sorted(base.rglob("*.json")):
        doc = json.loads(f.read_text(encoding="utf-8"))
        school, year = doc.get("school", {}), doc.get("year")
        for item in doc.get("items", []):
            yield school, year, item


def _iter_official_cutoffs():
    """Yield normalized official cutoff rows from data/jiangsu/raw/official."""
    if not OFFICIAL_DIR.exists():
        return
    for f in sorted(OFFICIAL_DIR.glob("cutoff_*.csv")):
        with f.open(encoding="utf-8", newline="") as fp:
            for row in csv.DictReader(fp):
                if row.get("school_code") and row.get("special_group"):
                    yield row


def _iter_plan_details():
    """Yield normalized per-major plan rows from data/jiangsu/raw/plan_details."""
    if not PLAN_DETAIL_DIR.exists():
        return
    for f in sorted(PLAN_DETAIL_DIR.glob("plan_details_*.csv")):
        with f.open(encoding="utf-8", newline="") as fp:
            for row in csv.DictReader(fp):
                if row.get("school_code") and row.get("special_group") and row.get("major_name"):
                    yield row


def _group_major_name(row: dict) -> str:
    school = row.get("school_name") or ""
    sg_name = row.get("sg_name") or ""
    sg_info = (row.get("sg_info") or "").replace("，", "").replace(",", "")
    base = f"{school}{sg_name}专业组" if sg_name else f"{school}院校专业组"
    return f"{base}-{sg_info}" if sg_info else base


def ingest_scores(conn: sqlite3.Connection) -> int:
    """score/special rows → historical_cutoff (per-major 录取位次)."""
    sql = """
        INSERT OR IGNORE INTO historical_cutoff (
            year, subject_category, school_code, school_name,
            special_group, sg_name, sg_info, major_code, major_name,
            min_score, min_rank, plan_count
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
    """
    n = 0
    for school, year, it in _iter_raw("score"):
        cat = it.get("local_type_name")  # 物理类 / 历史类
        if cat not in ("物理类", "历史类"):
            continue
        conn.execute(sql, (
            int(it.get("year") or year),
            cat,
            str(it.get("school_id") or school.get("school_id") or ""),
            it.get("name") or school.get("name") or "",
            str(it.get("special_group") or ""),
            str(it.get("sg_name") or "").strip("（）()"),
            it.get("sg_info") or "",
            str(it.get("spcode") or ""),
            it.get("spname") or "",
            _parse_int(it.get("min")),
            _parse_int(it.get("min_section")),
            _parse_int(it.get("num")),
        ))
        n += 1
    conn.commit()
    return n


def ingest_official_cutoffs(conn: sqlite3.Connection) -> int:
    """Official 投档线 CSV → historical_cutoff.

    Official files are 院校专业组-level. They are stored as a synthetic member row
    with major_code='__GROUP__' so Jiangsu group aggregation can carry the group
    historical threshold even when group-inner majors are unavailable.
    """
    sql = """
        INSERT OR IGNORE INTO historical_cutoff (
            year, subject_category, school_code, school_name,
            special_group, sg_name, sg_info, major_code, major_name,
            min_score, min_rank, plan_count
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
    """
    n = 0
    for row in _iter_official_cutoffs():
        conn.execute(sql, (
            _parse_int(row.get("year")),
            row.get("subject_category") or "",
            str(row.get("school_code") or ""),
            row.get("school_name") or "",
            str(row.get("special_group") or ""),
            str(row.get("sg_name") or ""),
            row.get("sg_info") or "",
            "__GROUP__",
            _group_major_name(row),
            _parse_int(row.get("min_score")),
            _parse_int(row.get("min_rank")),
            None,
        ))
        n += 1
    conn.commit()
    return n


def ingest_plans(conn: sqlite3.Connection) -> int:
    """plan/school rows → admission_plan (计划数/学费 + parsed 选科要求)."""
    sql = """
        INSERT OR IGNORE INTO admission_plan (
            year, subject_category, school_code, school_name,
            special_group, sg_name, sg_info, major_code, major_name,
            plan_count, subject_requirement, subject_requirement_json,
            tuition, duration
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """
    n = 0
    for school, year, it in _iter_raw("plan"):
        cat = it.get("local_type_name")
        if cat not in ("物理类", "历史类"):
            continue
        sg_info = it.get("sg_info") or ""
        conn.execute(sql, (
            int(it.get("year") or year),
            cat,
            str(it.get("school_id") or school.get("school_id") or ""),
            it.get("name") or school.get("name") or "",
            str(it.get("special_group") or ""),
            str(it.get("sg_name") or "").strip("（）()"),
            sg_info,
            str(it.get("spcode") or ""),
            it.get("spname") or "",
            _parse_int(it.get("num")),
            sg_info,
            json.dumps(parse_subject_requirement(sg_info), ensure_ascii=False),
            _parse_int(it.get("tuition")),
            it.get("length") or "",
        ))
        n += 1
    conn.commit()
    return n


def _official_thresholds(conn: sqlite3.Connection) -> dict[tuple, tuple[int | None, int | None]]:
    rows = conn.execute(
        """
        SELECT year, subject_category, school_code, special_group, min_score, min_rank
        FROM historical_cutoff
        WHERE major_code = '__GROUP__'
        """
    ).fetchall()
    return {
        (int(year), str(cat), str(code), str(group)): (min_score, min_rank)
        for year, cat, code, group, min_score, min_rank in rows
    }


def ingest_plan_details(conn: sqlite3.Connection) -> int:
    """Normalized plan_details CSV → admission_plan + per-major historical_cutoff.

    Official Jiangsu cutoff is group-level. To make the downstream experience
    match Zhejiang's per-major shape, each group-member major receives the
    group's official min_score/min_rank for the same year.
    """
    plan_sql = """
        INSERT OR IGNORE INTO admission_plan (
            year, subject_category, school_code, school_name,
            special_group, sg_name, sg_info, major_code, major_name,
            plan_count, subject_requirement, subject_requirement_json,
            tuition, duration
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """
    cutoff_sql = """
        INSERT OR IGNORE INTO historical_cutoff (
            year, subject_category, school_code, school_name,
            special_group, sg_name, sg_info, major_code, major_name,
            min_score, min_rank, plan_count
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
    """
    thresholds = _official_thresholds(conn)
    n = 0
    for row in _iter_plan_details():
        year = _parse_int(row.get("year"))
        if year is None:
            continue
        subject_category = row.get("subject_category") or ""
        school_code = str(row.get("school_code") or "")
        special_group = str(row.get("special_group") or "")
        sg_info = row.get("sg_info") or ""
        major_code = str(row.get("major_code") or "")
        major_name = row.get("major_name") or ""
        # 跳过汇总/合计行（如"普通计划—历史类本科批合计(534)"）——非真实专业
        if not major_name or "合计" in major_name or "小计" in major_name:
            continue
        plan_count = _parse_int(row.get("plan_count"))
        threshold_key = (year, subject_category, school_code, special_group)
        if threshold_key not in thresholds:
            continue
        min_score, min_rank = thresholds[threshold_key]
        params = (
            year,
            subject_category,
            school_code,
            row.get("school_name") or "",
            special_group,
            str(row.get("sg_name") or ""),
            sg_info,
            major_code,
            major_name,
            plan_count,
            sg_info,
            json.dumps(parse_subject_requirement(sg_info), ensure_ascii=False),
            _parse_int(row.get("tuition")),
            row.get("duration") or "",
        )
        conn.execute(plan_sql, params)
        conn.execute(cutoff_sql, (
            year,
            subject_category,
            school_code,
            row.get("school_name") or "",
            special_group,
            str(row.get("sg_name") or ""),
            sg_info,
            major_code,
            major_name,
            min_score,
            min_rank,
            plan_count,
        ))
        n += 1
    conn.commit()
    return n


def ingest_official_group_plans(
    conn: sqlite3.Connection,
    years: set[int] | None = None,
) -> int:
    """Create fallback admission_plan rows from official group cutoffs.

    This keeps Jiangsu recommendations usable from official data alone. These
    synthetic rows are inserted only when a group has no per-major plan detail,
    so they preserve the recommendation entry without masking real majors.
    """
    sql = """
        INSERT OR IGNORE INTO admission_plan (
            year, subject_category, school_code, school_name,
            special_group, sg_name, sg_info, major_code, major_name,
            plan_count, subject_requirement, subject_requirement_json,
            tuition, duration
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """
    n = 0
    for row in _iter_official_cutoffs():
        row_year = _parse_int(row.get("year"))
        if row_year is None or (years is not None and row_year not in years):
            continue
        existing = conn.execute(
            """
            SELECT 1 FROM admission_plan
            WHERE year = ? AND subject_category = ? AND school_code = ?
              AND special_group = ? AND major_code != '__GROUP__'
            LIMIT 1
            """,
            (
                row_year,
                row.get("subject_category") or "",
                str(row.get("school_code") or ""),
                str(row.get("special_group") or ""),
            ),
        ).fetchone()
        if existing:
            continue
        sg_info = row.get("sg_info") or ""
        conn.execute(sql, (
            row_year,
            row.get("subject_category") or "",
            str(row.get("school_code") or ""),
            row.get("school_name") or "",
            str(row.get("special_group") or ""),
            str(row.get("sg_name") or ""),
            sg_info,
            "__GROUP__",
            _group_major_name(row),
            None,
            sg_info,
            json.dumps(parse_subject_requirement(sg_info), ensure_ascii=False),
            None,
            "",
        ))
        n += 1
    conn.commit()
    return n


def main() -> None:
    conn = init_db()
    s_official = ingest_official_cutoffs(conn)
    p_details = ingest_plan_details(conn)
    s = ingest_scores(conn)
    p = ingest_plans(conn)
    p_official = ingest_official_group_plans(conn, years={2025, 2024, 2023})
    # quick verification
    cats = conn.execute(
        "SELECT subject_category, COUNT(*) FROM historical_cutoff GROUP BY subject_category"
    ).fetchall()
    groups = conn.execute(
        "SELECT COUNT(DISTINCT school_code||'-'||special_group) FROM admission_plan"
    ).fetchone()[0]
    conn.close()
    print(f"historical_cutoff: {s + s_official + p_details} 行  {dict(cats)}")
    print(f"  - official group cutoff: {s_official} 行")
    print(f"  - normalized plan detail: {p_details} 行")
    print(f"  - zsgk per-major score:  {s} 行")
    print(f"admission_plan:    {p + p_details + p_official} 行  专业组数: {groups}")
    print(f"  - zsgk per-major plan:   {p} 行")
    print(f"  - official group plan:   {p_official} 行")
    print(f"DB → {DB_PATH}")


if __name__ == "__main__":
    main()
