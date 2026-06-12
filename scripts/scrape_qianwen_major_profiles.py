#!/usr/bin/env python3
"""Scrape Qianwen major profile data and import it into the Zhejiang DB.

The Qianwen pages are server-side rendered and expose a JSON payload in
``window.__INITIAL_PROPS__``.  This script keeps the raw structured records in
JSONL/CSV and also upserts:

* qianwen_major_profile: full structured Qianwen data
* major_profile: fields already used by the recommendation pipeline
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import re
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "data" / "zhejiang" / "college.db"
RAW_DIR = PROJECT_ROOT / "data" / "zhejiang" / "raw"
MAJOR_LIST_JSON = RAW_DIR / "qianwen_major_list_2026.json"
PROFILE_JSONL = RAW_DIR / "qianwen_major_profiles_2026.jsonl"
PROFILE_CSV = RAW_DIR / "qianwen_major_profiles_2026.csv"
STATUS_JSONL = RAW_DIR / "qianwen_major_profiles_2026.status.jsonl"

SEARCH_URL = "https://p.qianwen.com/gaokaopc-search-tools/search-major"
DETAIL_URL = "https://p.qianwen.com/major/tab"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/136.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://p.qianwen.com/",
}


class ScrapeError(RuntimeError):
    pass


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _extract_initial_props(html: str) -> dict[str, Any]:
    marker = "window.__INITIAL_PROPS__"
    idx = html.find(marker)
    if idx < 0:
        raise ScrapeError("window.__INITIAL_PROPS__ not found")
    eq = html.find("=", idx)
    start = html.find("{", eq)
    if start < 0:
        raise ScrapeError("initial props JSON start not found")
    try:
        payload, _ = json.JSONDecoder().raw_decode(html[start:])
    except json.JSONDecodeError as exc:
        raise ScrapeError(f"failed to decode initial props: {exc}") from exc
    if not isinstance(payload, dict):
        raise ScrapeError("initial props is not an object")
    return payload


def _get_nested(data: dict[str, Any], *keys: str, default: Any = None) -> Any:
    cur: Any = data
    for key in keys:
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


def _detail_map(detail_rows: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    if not isinstance(detail_rows, list):
        return result
    for row in detail_rows:
        if not isinstance(row, dict):
            continue
        key = str(row.get("key") or "").strip()
        value = str(row.get("value") or "").strip()
        if key and value:
            result[key] = value
    return result


def _industry_map(rows: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    if not isinstance(rows, list):
        return result
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = str(row.get("key") or "").strip()
        value = str(row.get("value") or "").strip()
        if key and value:
            result[key] = value
    return result


def _to_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _fetch(session: requests.Session, url: str, timeout: float, retries: int) -> str:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            response = session.get(url, headers=HEADERS, timeout=timeout)
            response.raise_for_status()
            return response.text
        except Exception as exc:  # noqa: BLE001 - crawler should continue
            last_error = exc
            if attempt < retries:
                time.sleep(min(8.0, attempt * 1.5) + random.random())
    raise ScrapeError(str(last_error) if last_error else "unknown fetch error")


def search_major_url() -> str:
    return f"{SEARCH_URL}?{urlencode({'entry': 'tab', 'device': 'pc'})}"


def detail_url(major_name: str) -> str:
    query = {
        "app": "index",
        "collegesTab": "allColleges",
        "webCompassApp": "true",
        "q": f"{major_name}专业",
        "entry": "gaokao_channel",
        "device": "pc",
    }
    return f"{DETAIL_URL}?{urlencode(query)}"


def fetch_major_list(session: requests.Session, timeout: float, retries: int) -> list[dict[str, Any]]:
    html = _fetch(session, search_major_url(), timeout, retries)
    payload = _extract_initial_props(html)
    rows = _get_nested(payload, "initialData", "formattedData", "majorList", default=[])
    if not isinstance(rows, list) or not rows:
        raise ScrapeError("majorList not found or empty")
    result = [row for row in rows if isinstance(row, dict) and row.get("name")]
    MAJOR_LIST_JSON.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def _source_url_from_raw(raw: dict[str, Any], major_name: str) -> str:
    baike = _get_nested(raw, "professional_details_data", "baike", default={}) or {}
    if isinstance(baike, dict) and baike.get("url"):
        return str(baike["url"])
    return detail_url(major_name)


def parse_detail(major_row: dict[str, Any], html: str, url: str) -> dict[str, Any]:
    payload = _extract_initial_props(html)
    init = payload.get("initialData") or {}
    if not isinstance(init, dict):
        raise ScrapeError("initialData missing")

    professional = init.get("professional_details_data") or {}
    employment = init.get("employment_prospects_data") or {}
    related = init.get("related_majors_data") or {}
    colleges = init.get("establishing_colleges_data") or {}
    right_content = init.get("rightContent") or {}

    details = _detail_map(professional.get("detail") if isinstance(professional, dict) else [])
    industry = _industry_map(employment.get("industryDetails") if isinstance(employment, dict) else [])
    baike = professional.get("baike") if isinstance(professional, dict) else {}
    baike = baike if isinstance(baike, dict) else {}

    major_name = str(major_row.get("name") or professional.get("title") or "").strip()
    if not major_name:
        raise ScrapeError("major name missing")

    fresh_salary = _to_int(industry.get("应届平均薪酬"))
    salary_summary = "；".join(f"{key}：{value}" for key, value in industry.items())
    gender_ratio = {
        "men": professional.get("men") if isinstance(professional, dict) else "",
        "women": professional.get("women") if isinstance(professional, dict) else "",
        "right_content_gender": right_content.get("gender") if isinstance(right_content, dict) else {},
        "right_content_genre": right_content.get("genre") if isinstance(right_content, dict) else {},
    }

    return {
        "major_name": major_name,
        "major_code": str(major_row.get("code") or "").strip(),
        "discipline_category": str(major_row.get("majorTab1") or "").strip(),
        "major_category": str(major_row.get("majorTab2") or "").strip(),
        "length": str(major_row.get("length") or "").strip(),
        "degree": str(major_row.get("degree") or "").strip(),
        "popular_value": _to_int(major_row.get("popular_value")),
        "summary": details.get("专业介绍") or str(baike.get("value") or "").strip(),
        "learn_what": details.get("开设课程") or "",
        "career_direction": details.get("就业方向") or "",
        "subject_suggestion": str(professional.get("first_impression_arr") or "").strip()
        if isinstance(professional, dict)
        else "",
        "baike_text": str(baike.get("value") or "").strip(),
        "baike_url": str(baike.get("url") or "").strip(),
        "fresh_salary": fresh_salary,
        "top_city": industry.get("最多就业地区", ""),
        "top_position": industry.get("最多就业岗位", ""),
        "top_industry": industry.get("最多就业行业", ""),
        "gender_ratio": gender_ratio,
        "salary_chart": employment.get("chartTmp") if isinstance(employment, dict) else [],
        "employment_area": init.get("career_area") or [],
        "position_distribution": init.get("handle_svg_data") or [],
        "industry_distribution": init.get("handle_svg_data2") or [],
        "related_majors": related.get("related_majors_data") if isinstance(related, dict) else [],
        "opening_colleges": colleges.get("content") if isinstance(colleges, dict) else [],
        "recommended_majors": right_content.get("rec_major") if isinstance(right_content, dict) else [],
        "source_url": url,
        "baike_source_url": _source_url_from_raw(init, major_name),
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "raw": init,
    }


def existing_done(path: Path) -> set[str]:
    done: set[str] = set()
    if not path.exists():
        return done
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("major_name"):
                done.add(str(row["major_name"]))
    return done


CSV_FIELDS = [
    "major_name",
    "major_code",
    "discipline_category",
    "major_category",
    "length",
    "degree",
    "popular_value",
    "fresh_salary",
    "top_city",
    "top_position",
    "top_industry",
    "subject_suggestion",
    "summary",
    "learn_what",
    "career_direction",
    "source_url",
]


def append_profile(record: dict[str, Any]) -> None:
    with PROFILE_JSONL.open("a", encoding="utf-8") as fh:
        fh.write(_json_dumps(record) + "\n")

    csv_exists = PROFILE_CSV.exists()
    with PROFILE_CSV.open("a", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        if not csv_exists:
            writer.writeheader()
        writer.writerow({field: record.get(field) for field in CSV_FIELDS})


def append_status(row: dict[str, Any]) -> None:
    with STATUS_JSONL.open("a", encoding="utf-8") as fh:
        fh.write(_json_dumps(row) + "\n")


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _add_column(conn: sqlite3.Connection, table: str, name: str, decl: str) -> None:
    if name not in _table_columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def ensure_db_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS qianwen_major_profile (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            major_name TEXT NOT NULL,
            major_code TEXT,
            discipline_category TEXT,
            major_category TEXT,
            length TEXT,
            degree TEXT,
            popular_value INTEGER,
            summary TEXT,
            learn_what TEXT,
            career_direction TEXT,
            subject_suggestion TEXT,
            baike_text TEXT,
            baike_url TEXT,
            fresh_salary INTEGER,
            top_city TEXT,
            top_position TEXT,
            top_industry TEXT,
            gender_ratio_json TEXT,
            salary_chart_json TEXT,
            employment_area_json TEXT,
            position_distribution_json TEXT,
            industry_distribution_json TEXT,
            related_majors_json TEXT,
            opening_colleges_json TEXT,
            recommended_majors_json TEXT,
            raw_json TEXT,
            source_url TEXT,
            fetched_at TEXT DEFAULT (datetime('now')),
            UNIQUE(major_name)
        )
        """
    )
    # Existing deployments have major_profile already. Keep these as nullable
    # optional columns so older tests/fixtures remain valid.
    for name, decl in [
        ("qianwen_code", "TEXT"),
        ("qianwen_discipline_category", "TEXT"),
        ("qianwen_major_category", "TEXT"),
        ("qianwen_length", "TEXT"),
        ("qianwen_degree", "TEXT"),
        ("qianwen_popular_value", "INTEGER"),
        ("qianwen_subject_suggestion", "TEXT"),
        ("qianwen_salary_summary", "TEXT"),
        ("qianwen_gender_ratio_json", "TEXT"),
        ("qianwen_salary_chart_json", "TEXT"),
        ("qianwen_employment_area_json", "TEXT"),
        ("qianwen_position_json", "TEXT"),
        ("qianwen_industry_json", "TEXT"),
        ("qianwen_related_majors_json", "TEXT"),
    ]:
        _add_column(conn, "major_profile", name, decl)


def _blank_to_null(value: Any) -> Any:
    return None if value == "" else value


def _clip(text: str, limit: int = 260) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _norm_major_name(name: str) -> str:
    text = re.sub(r"\s+", "", (name or "").strip())
    text = text.replace("（", "(").replace("）", ")")
    return re.sub(r"\([^)]*\)", "", text)


def _copy_profile_row(conn: sqlite3.Connection, source_name: str) -> sqlite3.Row | None:
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM major_profile WHERE major_name = ?", (source_name,)).fetchone()
    conn.row_factory = None
    return row


def build_category_profiles(conn: sqlite3.Connection) -> int:
    conn.row_factory = sqlite3.Row
    groups: dict[str, list[sqlite3.Row]] = {}
    for row in conn.execute(
        """
        SELECT *
        FROM qianwen_major_profile
        WHERE major_category IS NOT NULL AND major_category != ''
        ORDER BY popular_value DESC
        """
    ):
        groups.setdefault(str(row["major_category"]), []).append(row)

    created = 0
    for category, rows in groups.items():
        if conn.execute("SELECT 1 FROM major_profile WHERE major_name = ?", (category,)).fetchone():
            continue
        top_names = [str(row["major_name"]) for row in rows[:10] if row["major_name"]]
        rep = rows[0]
        discipline = rep["discipline_category"] or ""
        summary = (
            f"{category}是{discipline}下的专业类，千问标准专业库收录的代表专业包括："
            f"{'、'.join(top_names)}。类内不同专业培养方向差异较大，具体以院校招生专业和培养方案为准。"
        )
        career = (
            f"该专业类常见去向可参考代表专业「{rep['major_name']}」："
            f"{_clip(rep['career_direction'] or '', 220)}"
        )
        related = [
            {
                "major_name": row["major_name"],
                "major_code": row["major_code"],
                "popular_value": row["popular_value"],
            }
            for row in rows
        ]
        conn.execute(
            """
            INSERT INTO major_profile (
                major_name, summary, learn_what, career_direction, keywords,
                fallback_from, source_name, source_url,
                qianwen_discipline_category, qianwen_major_category,
                qianwen_popular_value, qianwen_related_majors_json, fetched_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                category,
                summary,
                rep["learn_what"] or "",
                career,
                "",
                "、".join(top_names[:5]),
                "千问高考(专业类汇总)",
                rep["source_url"] or "",
                discipline,
                category,
                sum(int(row["popular_value"] or 0) for row in rows),
                _json_dumps(related),
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        created += 1
    conn.row_factory = None
    return created


def build_admission_major_aliases(conn: sqlite3.Connection) -> int:
    """Create exact major_profile aliases for Zhejiang admission-plan names.

    The recommendation pipeline can normalize bracket suffixes, but DB audits and
    some enrichment paths use exact admission names.  These aliases preserve the
    original source through fallback_from.
    """

    columns = [row[1] for row in conn.execute("PRAGMA table_info(major_profile)")]
    writable_columns = [c for c in columns if c != "id"]
    created = 0
    major_names = [
        str(row[0])
        for row in conn.execute(
            "SELECT DISTINCT major_name FROM admission_plan WHERE province = '浙江' ORDER BY major_name"
        )
        if row[0]
    ]
    conn.row_factory = sqlite3.Row
    for admission_name in major_names:
        if conn.execute("SELECT 1 FROM major_profile WHERE major_name = ?", (admission_name,)).fetchone():
            continue
        norm = _norm_major_name(admission_name)
        if not norm or norm == admission_name:
            continue
        source = conn.execute("SELECT * FROM major_profile WHERE major_name = ?", (norm,)).fetchone()
        if not source:
            continue
        values = []
        for column in writable_columns:
            if column == "major_name":
                values.append(admission_name)
            elif column == "fallback_from":
                values.append(norm)
            elif column == "source_name":
                values.append(f"{source[column] or '千问高考'}(招生名称别名)")
            elif column == "fetched_at":
                values.append(datetime.now().isoformat(timespec="seconds"))
            else:
                values.append(source[column])
        placeholders = ",".join("?" for _ in writable_columns)
        conn.execute(
            f"INSERT INTO major_profile ({','.join(writable_columns)}) VALUES ({placeholders})",
            values,
        )
        created += 1
    conn.row_factory = None
    return created


def import_records(db_path: Path, records: list[dict[str, Any]]) -> None:
    conn = sqlite3.connect(db_path)
    try:
        ensure_db_schema(conn)
        now = datetime.now().isoformat(timespec="seconds")
        for row in records:
            conn.execute(
                """
                INSERT INTO qianwen_major_profile (
                    major_name, major_code, discipline_category, major_category,
                    length, degree, popular_value, summary, learn_what,
                    career_direction, subject_suggestion, baike_text, baike_url,
                    fresh_salary, top_city, top_position, top_industry,
                    gender_ratio_json, salary_chart_json, employment_area_json,
                    position_distribution_json, industry_distribution_json,
                    related_majors_json, opening_colleges_json,
                    recommended_majors_json, raw_json, source_url, fetched_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(major_name) DO UPDATE SET
                    major_code=excluded.major_code,
                    discipline_category=excluded.discipline_category,
                    major_category=excluded.major_category,
                    length=excluded.length,
                    degree=excluded.degree,
                    popular_value=excluded.popular_value,
                    summary=excluded.summary,
                    learn_what=excluded.learn_what,
                    career_direction=excluded.career_direction,
                    subject_suggestion=excluded.subject_suggestion,
                    baike_text=excluded.baike_text,
                    baike_url=excluded.baike_url,
                    fresh_salary=excluded.fresh_salary,
                    top_city=excluded.top_city,
                    top_position=excluded.top_position,
                    top_industry=excluded.top_industry,
                    gender_ratio_json=excluded.gender_ratio_json,
                    salary_chart_json=excluded.salary_chart_json,
                    employment_area_json=excluded.employment_area_json,
                    position_distribution_json=excluded.position_distribution_json,
                    industry_distribution_json=excluded.industry_distribution_json,
                    related_majors_json=excluded.related_majors_json,
                    opening_colleges_json=excluded.opening_colleges_json,
                    recommended_majors_json=excluded.recommended_majors_json,
                    raw_json=excluded.raw_json,
                    source_url=excluded.source_url,
                    fetched_at=excluded.fetched_at
                """,
                (
                    row["major_name"],
                    row.get("major_code"),
                    row.get("discipline_category"),
                    row.get("major_category"),
                    row.get("length"),
                    row.get("degree"),
                    row.get("popular_value"),
                    row.get("summary"),
                    row.get("learn_what"),
                    row.get("career_direction"),
                    row.get("subject_suggestion"),
                    row.get("baike_text"),
                    row.get("baike_url"),
                    row.get("fresh_salary"),
                    row.get("top_city"),
                    row.get("top_position"),
                    row.get("top_industry"),
                    _json_dumps(row.get("gender_ratio") or {}),
                    _json_dumps(row.get("salary_chart") or []),
                    _json_dumps(row.get("employment_area") or []),
                    _json_dumps(row.get("position_distribution") or []),
                    _json_dumps(row.get("industry_distribution") or []),
                    _json_dumps(row.get("related_majors") or []),
                    _json_dumps(row.get("opening_colleges") or []),
                    _json_dumps(row.get("recommended_majors") or []),
                    _json_dumps(row.get("raw") or {}),
                    row.get("source_url"),
                    row.get("fetched_at") or now,
                ),
            )

            salary_summary = "；".join(
                x
                for x in [
                    f"应届平均薪酬：{row.get('fresh_salary')}元" if row.get("fresh_salary") else "",
                    f"最多就业地区：{row.get('top_city')}" if row.get("top_city") else "",
                    f"最多就业岗位：{row.get('top_position')}" if row.get("top_position") else "",
                    f"最多就业行业：{row.get('top_industry')}" if row.get("top_industry") else "",
                ]
                if x
            )
            conn.execute(
                """
                INSERT INTO major_profile (
                    major_name, summary, learn_what, career_direction,
                    keywords, fallback_from, source_name, source_url,
                    qianwen_code, qianwen_discipline_category,
                    qianwen_major_category, qianwen_length, qianwen_degree,
                    qianwen_popular_value, qianwen_subject_suggestion,
                    qianwen_salary_summary, qianwen_gender_ratio_json,
                    qianwen_salary_chart_json, qianwen_employment_area_json,
                    qianwen_position_json, qianwen_industry_json,
                    qianwen_related_majors_json, fetched_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(major_name) DO UPDATE SET
                    summary=COALESCE(NULLIF(major_profile.summary,''), excluded.summary),
                    learn_what=COALESCE(NULLIF(major_profile.learn_what,''), excluded.learn_what),
                    career_direction=COALESCE(NULLIF(major_profile.career_direction,''), excluded.career_direction),
                    source_name=CASE
                        WHEN major_profile.source_name IS NULL OR major_profile.source_name=''
                        THEN excluded.source_name ELSE major_profile.source_name END,
                    source_url=CASE
                        WHEN major_profile.source_url IS NULL OR major_profile.source_url=''
                        THEN excluded.source_url ELSE major_profile.source_url END,
                    qianwen_code=excluded.qianwen_code,
                    qianwen_discipline_category=excluded.qianwen_discipline_category,
                    qianwen_major_category=excluded.qianwen_major_category,
                    qianwen_length=excluded.qianwen_length,
                    qianwen_degree=excluded.qianwen_degree,
                    qianwen_popular_value=excluded.qianwen_popular_value,
                    qianwen_subject_suggestion=excluded.qianwen_subject_suggestion,
                    qianwen_salary_summary=excluded.qianwen_salary_summary,
                    qianwen_gender_ratio_json=excluded.qianwen_gender_ratio_json,
                    qianwen_salary_chart_json=excluded.qianwen_salary_chart_json,
                    qianwen_employment_area_json=excluded.qianwen_employment_area_json,
                    qianwen_position_json=excluded.qianwen_position_json,
                    qianwen_industry_json=excluded.qianwen_industry_json,
                    qianwen_related_majors_json=excluded.qianwen_related_majors_json,
                    fetched_at=excluded.fetched_at
                """,
                (
                    row["major_name"],
                    _clip(row.get("summary") or ""),
                    _blank_to_null(row.get("learn_what")),
                    _blank_to_null(row.get("career_direction")),
                    "",
                    "",
                    "千问高考",
                    row.get("source_url"),
                    row.get("major_code"),
                    row.get("discipline_category"),
                    row.get("major_category"),
                    row.get("length"),
                    row.get("degree"),
                    row.get("popular_value"),
                    row.get("subject_suggestion"),
                    salary_summary,
                    _json_dumps(row.get("gender_ratio") or {}),
                    _json_dumps(row.get("salary_chart") or []),
                    _json_dumps(row.get("employment_area") or []),
                    _json_dumps(row.get("position_distribution") or []),
                    _json_dumps(row.get("industry_distribution") or []),
                    _json_dumps(row.get("related_majors") or []),
                    row.get("fetched_at") or now,
                ),
            )
        category_count = build_category_profiles(conn)
        alias_count = build_admission_major_aliases(conn)
        conn.commit()
        print(f"Built {category_count} category profiles and {alias_count} admission-name aliases")
    finally:
        conn.close()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not path.exists():
        return records
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and row.get("major_name"):
                records.append(row)
    return records


def scrape(args: argparse.Namespace) -> list[dict[str, Any]]:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    major_list = fetch_major_list(session, args.timeout, args.retries)
    if args.limit:
        major_list = major_list[: args.limit]

    done = existing_done(PROFILE_JSONL) if args.resume and not args.force else set()
    records: list[dict[str, Any]] = []
    for idx, major in enumerate(major_list, 1):
        name = str(major.get("name") or "")
        if not name:
            continue
        if name in done:
            continue
        url = detail_url(name)
        try:
            html = _fetch(session, url, args.timeout, args.retries)
            record = parse_detail(major, html, url)
            append_profile(record)
            append_status(
                {
                    "major_name": name,
                    "status": "ok",
                    "idx": idx,
                    "total": len(major_list),
                    "fetched_at": record["fetched_at"],
                }
            )
            records.append(record)
            print(f"[{idx}/{len(major_list)}] ok {name}", flush=True)
        except Exception as exc:  # noqa: BLE001 - keep scraping the rest
            append_status(
                {
                    "major_name": name,
                    "status": "error",
                    "idx": idx,
                    "total": len(major_list),
                    "error": str(exc),
                    "fetched_at": datetime.now().isoformat(timespec="seconds"),
                }
            )
            print(f"[{idx}/{len(major_list)}] error {name}: {exc}", file=sys.stderr, flush=True)
        if args.sleep:
            time.sleep(args.sleep + random.random() * args.jitter)
    return records


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--sleep", type=float, default=0.15)
    parser.add_argument("--jitter", type=float, default=0.15)
    parser.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--no-import-db", action="store_true")
    parser.add_argument("--import-only", action="store_true")
    args = parser.parse_args()

    if args.import_only:
        records = load_jsonl(PROFILE_JSONL)
    else:
        records = scrape(args)
        if args.resume and not args.force:
            # Import the complete local file after a resumed scrape, not only the
            # freshly fetched tail.
            records = load_jsonl(PROFILE_JSONL)

    if not args.no_import_db:
        import_records(args.db, records)
        print(f"Imported {len(records)} records into {args.db}")


if __name__ == "__main__":
    main()
