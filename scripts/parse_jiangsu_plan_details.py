"""Normalize Jiangsu admission-plan sources into per-major plan detail CSV.

Place downloaded source files under:

    data/jiangsu/raw/plan_sources/{year}/

Supported inputs: CSV, XLS, XLSX, HTML, TXT, and text-extractable PDF. The
parser is deliberately source-agnostic: 阳光高考 exports, university admission
tables, and manually downloaded files can all flow into the same output shape.

Output:

    data/jiangsu/raw/plan_details/plan_details_{year}_{physics|history}.csv

Run:

    python scripts/parse_jiangsu_plan_details.py
"""

from __future__ import annotations

import argparse
import csv
import re
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Iterable

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = PROJECT_ROOT / "data" / "jiangsu" / "raw"
SOURCE_DIR = RAW_DIR / "plan_sources"
DETAIL_DIR = RAW_DIR / "plan_details"
OFFICIAL_DIR = RAW_DIR / "official"
DEFAULT_YEARS = (2025, 2024, 2023)

SUBJECT_TO_FILE = {"物理类": "physics", "历史类": "history"}
FILE_TO_SUBJECT = {v: k for k, v in SUBJECT_TO_FILE.items()}
SUBJECT_REQUIREMENT_TERMS = ("不限", "化学", "生物", "思想政治", "政治", "地理")

FIELDNAMES = [
    "year",
    "subject_category",
    "school_code",
    "school_name",
    "special_group",
    "sg_name",
    "sg_info",
    "major_code",
    "major_name",
    "plan_count",
    "tuition",
    "duration",
    "source_url",
    "source_file",
    "matched_official_group",
]


def clean_text(value: object) -> str:
    text = str(value or "").strip()
    text = text.replace("\u3000", " ").replace("（", "(").replace("）", ")")
    return re.sub(r"\s+", " ", text)


def parse_int(value: object) -> int | None:
    text = clean_text(value).replace(",", "")
    if not text or text.lower() == "nan" or text in {"-", "--", "—"}:
        return None
    match = re.search(r"\d+", text)
    return int(match.group(0)) if match else None


def normalize_sg_name(value: object) -> str:
    text = clean_text(value)
    match = re.search(r"(\d{2,3})", text)
    return match.group(1) if match else ""


def infer_subject_category(*values: object) -> str:
    text = " ".join(clean_text(v) for v in values)
    if "物理" in text:
        return "物理类"
    if "历史" in text:
        return "历史类"
    return ""


def looks_like_subject_requirement(text: str) -> bool:
    req = clean_text(text)
    return any(term in req for term in SUBJECT_REQUIREMENT_TERMS) or "再选" in req


def infer_requirement_text(*values: object) -> str:
    """Infer 江苏 group requirement from headings like 普通类（物理+化学）."""
    text = " ".join(clean_text(v) for v in values)
    first = "物理" if "物理" in text else ("历史" if "历史" in text else "")
    if not first:
        return ""
    if "不限" in text:
        reselect = "不限"
    else:
        found = [s for s in ("化学", "生物", "思想政治", "政治", "地理") if s in text]
        if not found:
            return f"首选{first}"
        found = ["思想政治" if s == "政治" else s for s in found]
        reselect = "和".join(dict.fromkeys(found))
    return f"首选{first}，再选{reselect}"


def parse_group_text(value: object) -> tuple[str, str, str]:
    """Return (school_name, sg_name, sg_info) from combined group text."""
    text = clean_text(value)
    match = re.search(r"(.+?)(\d{2,3})\s*专业组(?:\(([^)]*)\))?", text)
    if not match:
        return "", normalize_sg_name(text), ""
    school = match.group(1).strip()
    sg_name = match.group(2)
    req = (match.group(3) or "").strip()
    return school, sg_name, req


def canonical_group_info(subject_category: str, text: str) -> str:
    req = clean_text(text)
    if not req:
        return ""
    inferred = infer_requirement_text(req)
    if inferred:
        return inferred
    if "首选" in req or "再选" in req:
        return req
    first = "物理" if subject_category == "物理类" else "历史"
    return f"首选{first}，再选{req}"


def load_official_group_index(years: Iterable[int] = DEFAULT_YEARS) -> dict[tuple, dict]:
    """Official cutoff groups keyed by (year, subject_category, school_code, sg_name)."""
    index: dict[tuple, dict] = {}
    for year in years:
        for file_subject, subject_category in FILE_TO_SUBJECT.items():
            path = OFFICIAL_DIR / f"cutoff_{year}_{file_subject}.csv"
            if not path.exists():
                continue
            with path.open(encoding="utf-8", newline="") as f:
                for row in csv.DictReader(f):
                    key = (
                        int(row["year"]),
                        row["subject_category"],
                        str(row["school_code"]),
                        normalize_sg_name(row.get("sg_name")),
                    )
                    index[key] = row
    return index


def display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def source_url_for(path: Path) -> str:
    marker = path.with_suffix(path.suffix + ".url")
    if marker.exists():
        return marker.read_text(encoding="utf-8").strip()
    return ""


def find_column(columns: list[str], *keyword_sets: tuple[str, ...]) -> str | None:
    for keywords in keyword_sets:
        for column in columns:
            if all(keyword in column for keyword in keywords):
                return column
    return None


def cell(row: pd.Series, column: str | None) -> str:
    return clean_text(row.get(column)) if column else ""


def promote_header_row(frame: pd.DataFrame) -> pd.DataFrame:
    """Use the first table row containing plan headers as dataframe columns."""
    for idx, row in frame.iterrows():
        values = [clean_text(v) for v in row.tolist()]
        joined = " ".join(values)
        if "代号" in joined and ("专业名称" in joined or "专业组" in joined) and "计划" in joined:
            new_frame = frame.iloc[idx + 1 :].copy()
            new_frame.columns = values
            return new_frame
    return frame


def normalize_frame(
    frame: pd.DataFrame,
    *,
    year: int,
    source_file: Path,
    official_groups: dict[tuple, dict],
) -> list[dict]:
    if frame.empty:
        return []

    frame = promote_header_row(frame).copy()
    frame.columns = [clean_text(c) for c in frame.columns]
    columns = list(frame.columns)

    code_col = find_column(columns, ("院校", "代号"), ("院校", "代码"), ("学校", "代码"), ("代号",))
    school_col = find_column(columns, ("院校", "名称"), ("学校", "名称"), ("院校",), ("学校",))
    group_col = find_column(columns, ("专业组",), ("院校专业组",), ("组号",))
    subject_col = find_column(columns, ("科类",), ("首选",), ("选考", "科目"))
    req_col = find_column(columns, ("再选",), ("选考", "要求"), ("科目", "要求"))
    major_code_col = find_column(columns, ("专业", "代号"), ("专业", "代码"))
    major_col = find_column(columns, ("专业", "名称"), ("招生", "专业"), ("专业",))
    if school_col == group_col:
        school_col = None
    plan_col = find_column(columns, ("计划", "数"), ("招生", "人数"), ("人数",))
    tuition_col = find_column(columns, ("学费",),)
    duration_col = find_column(columns, ("学制",), ("年限",))

    records: list[dict] = []
    current_school_code = ""
    current_school_name = ""
    current_subject = ""
    current_sg_name = ""
    current_sg_info = ""

    for _, row in frame.iterrows():
        raw_values = [clean_text(v) for v in row.tolist()]
        if not any(raw_values):
            continue

        raw_code = cell(row, code_col)
        school_code = raw_code if re.fullmatch(r"\d{4}", raw_code or "") else current_school_code
        school_name = cell(row, school_col) or current_school_name
        group_text = cell(row, group_col)
        parsed_school, parsed_sg, parsed_req = parse_group_text(group_text)
        combo_match = re.fullmatch(r"(\d{4})(\d{2,3})", raw_code or "")
        if combo_match and ("专业组" in group_text or parsed_sg):
            school_code = combo_match.group(1)
            parsed_sg = parsed_sg or combo_match.group(2)
        if parsed_school and (not school_name or "专业组" in school_name):
            school_name = parsed_school
        sg_name = parsed_sg or current_sg_name
        subject_category = infer_subject_category(cell(row, subject_col), group_text, " ".join(raw_values)) or current_subject
        req_value = cell(row, req_col)
        if req_value and not looks_like_subject_requirement(req_value):
            req_value = ""
        sg_info = req_value or parsed_req or infer_requirement_text(group_text, " ".join(raw_values)) or current_sg_info

        if combo_match and parsed_sg:
            current_school_code = school_code
            current_school_name = school_name
            current_sg_name = sg_name
            current_subject = subject_category
            current_sg_info = sg_info
            continue

        major_name = cell(row, major_col)
        major_code = cell(row, major_code_col) or (raw_code if raw_code and raw_code != school_code else "")
        plan_count = parse_int(cell(row, plan_col))
        tuition = parse_int(cell(row, tuition_col))
        duration = cell(row, duration_col)

        # Some plan tables use merged cells; carry group context forward.
        if re.fullmatch(r"\d{4}", school_code or ""):
            current_school_code = school_code
        if school_name and "专业" not in school_name and (parsed_sg or re.fullmatch(r"\d{4}", raw_code or "")):
            current_school_name = school_name
        if sg_name:
            current_sg_name = sg_name
        if subject_category:
            current_subject = subject_category
        if sg_info:
            current_sg_info = sg_info

        if not major_name or "专业名称" in major_name or "院校" in major_name:
            continue
        if not re.fullmatch(r"\d{4}", school_code or "") or not school_name or not sg_name:
            continue
        if subject_category not in SUBJECT_TO_FILE:
            continue

        official = official_groups.get((year, subject_category, school_code, sg_name))
        if official and not sg_info:
            sg_info = official.get("sg_info") or ""
        sg_info = canonical_group_info(subject_category, sg_info)
        special_group = f"{school_code}-{sg_name}"

        records.append(
            {
                "year": year,
                "subject_category": subject_category,
                "school_code": school_code,
                "school_name": school_name,
                "special_group": special_group,
                "sg_name": sg_name,
                "sg_info": sg_info,
                "major_code": major_code,
                "major_name": major_name,
                "plan_count": plan_count if plan_count is not None else "",
                "tuition": tuition if tuition is not None else "",
                "duration": duration,
                "source_url": source_url_for(source_file),
                "source_file": display_path(source_file),
                "matched_official_group": 1 if official else 0,
            }
        )
    return records


def extract_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".html", ".htm", ".txt", ".csv"}:
        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return path.read_text(encoding="gb18030", errors="ignore")
    if suffix == ".pdf":
        pdftotext = shutil.which("pdftotext")
        if not pdftotext:
            return ""
        out = path.with_suffix(".txt")
        subprocess.run(
            [pdftotext, "-layout", str(path), str(out)],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return out.read_text(encoding="utf-8", errors="ignore") if out.exists() else ""
    return ""


def parse_text_lines(
    text: str,
    *,
    year: int,
    source_file: Path,
    official_groups: dict[tuple, dict],
) -> list[dict]:
    """Best-effort fallback for whitespace/text extracted admission plans.

    Handles both compact rows and the common Jiangsu layout:

        138122 扬州大学22专业组(化学)51
        65 数学与应用数学(师范) 2 5500
        66 物理学(师范) 2 5500
    """
    source_url = source_url_for(source_file)
    rows: list[dict] = []
    current: dict[str, str] = {}

    def official_matches(school_code: str, sg_name: str, subject_category: str = "") -> list[tuple[str, dict]]:
        if subject_category:
            row = official_groups.get((year, subject_category, school_code, sg_name))
            return [(subject_category, row)] if row else []
        found: list[tuple[str, dict]] = []
        for candidate in SUBJECT_TO_FILE:
            row = official_groups.get((year, candidate, school_code, sg_name))
            if row:
                found.append((candidate, row))
        return found

    def emit_major(
        *,
        major_code: str,
        major_name: str,
        plan_count: int | None = None,
        tuition: int | None = None,
        duration: str = "",
    ) -> None:
        if not current or not major_name:
            return
        school_code = current["school_code"]
        sg_name = current["sg_name"]
        subject_hint = current.get("subject_category", "")
        matches = official_matches(school_code, sg_name, subject_hint)
        if not matches and subject_hint:
            matches = [(subject_hint, {})]
        for subject_category, official in matches:
            sg_info = canonical_group_info(
                subject_category,
                current.get("sg_info") or (official.get("sg_info") if official else "") or "",
            )
            rows.append(
                {
                    "year": year,
                    "subject_category": subject_category,
                    "school_code": school_code,
                    "school_name": current.get("school_name") or (official.get("school_name") if official else ""),
                    "special_group": f"{school_code}-{sg_name}",
                    "sg_name": sg_name,
                    "sg_info": sg_info,
                    "major_code": major_code,
                    "major_name": major_name,
                    "plan_count": plan_count if plan_count is not None else "",
                    "tuition": tuition if tuition is not None else "",
                    "duration": duration,
                    "source_url": source_url,
                    "source_file": display_path(source_file),
                    "matched_official_group": 1 if official else 0,
                }
            )

    def update_group(line: str, subject_category: str, requirement_context: str) -> bool:
        patterns = [
            # 138122 扬州大学22专业组(化学)51  → school_code=1381, sg_name=22
            r"^(?P<combo>\d{6,7})\s+(?P<school>.+?)(?P<sg>\d{2,3})\s*专业组(?:\((?P<req>[^)]*)\))?(?:\s*\d{1,4})?$",
            r"^(?P<code>\d{4})\s+(?P<school>.+?)(?P<sg>\d{2,3})\s*专业组(?:\((?P<req>[^)]*)\))?(?:\s*\d{1,4})?$",
            r"^(?P<code>\d{4})\s+(?P<school>.+?)\s+(?P<sg>\d{2,3})\s*专业组(?:\((?P<req>[^)]*)\))?(?:\s*\d{1,4})?$",
        ]
        for pattern in patterns:
            match = re.match(pattern, line)
            if not match:
                continue
            gd = match.groupdict()
            school_code = gd.get("code") or (gd.get("combo") or "")[:4]
            sg_name = gd.get("sg") or ""
            if not re.fullmatch(r"\d{4}", school_code) or not sg_name:
                continue
            current.clear()
            current.update(
                {
                    "school_code": school_code,
                    "school_name": clean_text(gd.get("school") or ""),
                    "sg_name": sg_name,
                    "sg_info": clean_text(gd.get("req") or "") if looks_like_subject_requirement(gd.get("req") or "") else requirement_context,
                    "subject_category": subject_category,
                }
            )
            return True
        return False

    def parse_major_line(line: str) -> tuple[str, str, int | None, int | None, str] | None:
        if "专业组" in line or "院校" in line or "专业名称" in line:
            return None
        match = re.match(r"^(?P<code>[A-Za-z]?\d{1,3}|[A-Z]{1,3})\s*(?P<rest>[\u4e00-\u9fa5].+)$", line)
        if not match:
            return None
        major_code = match.group("code")
        rest = clean_text(match.group("rest"))
        parts = rest.split()
        duration = ""
        if parts and re.fullmatch(r"[三四五六七八0-9]+年", parts[-1]):
            duration = parts.pop()

        numeric_tail: list[str] = []
        while parts and (re.fullmatch(r"\d{1,6}", parts[-1]) or parts[-1] == "免费"):
            numeric_tail.insert(0, parts.pop())
        major_name = clean_text(" ".join(parts))
        if not major_name or len(major_name) > 80:
            return None

        plan_count: int | None = None
        tuition: int | None = None
        numeric_values = [parse_int(v) for v in numeric_tail if v != "免费"]
        numeric_values = [v for v in numeric_values if v is not None]
        if len(numeric_values) >= 2:
            plan_count = numeric_values[0]
            tuition = numeric_values[-1] if numeric_values[-1] and numeric_values[-1] > 1000 else None
        elif len(numeric_values) == 1:
            value = numeric_values[0]
            if value > 1000:
                tuition = value
            else:
                plan_count = value
        return major_code, major_name, plan_count, tuition, duration

    subject_context = ""
    requirement_context = ""
    for line in text.splitlines():
        line = clean_text(line)
        if not line:
            continue
        subject_context = infer_subject_category(subject_context, line) or subject_context
        requirement_context = infer_requirement_text(requirement_context, line) or requirement_context
        if update_group(line, subject_context, requirement_context):
            continue

        match = re.match(
            r"^(?P<code>\d{4})\s+(?P<school>.+?)\s+(?P<sg>\d{2,3})\s*专业组"
            r"(?:\((?P<req>[^)]*)\))?\s+(?P<major>[\u4e00-\u9fa5A-Za-z0-9（）()·+、-]+)"
            r"(?:\s+(?P<num>\d{1,4}))?(?:\s+(?P<tuition>\d{3,6}))?",
            line,
        )
        if not match:
            parsed = parse_major_line(line)
            if parsed:
                major_code, major_name, plan_count, tuition, duration = parsed
                emit_major(
                    major_code=major_code,
                    major_name=major_name,
                    plan_count=plan_count,
                    tuition=tuition,
                    duration=duration,
                )
            continue
        gd = match.groupdict()
        current.clear()
        current.update(
            {
                "school_code": gd["code"],
                "school_name": gd["school"],
                "sg_name": gd["sg"],
                "sg_info": gd.get("req") or "",
                "subject_category": subject_context,
            }
        )
        for subject_category, official in official_matches(gd["code"], gd["sg"], subject_context):
            sg_info = canonical_group_info(subject_category, gd.get("req") or official.get("sg_info") or "")
            rows.append(
                {
                    "year": year,
                    "subject_category": subject_category,
                    "school_code": gd["code"],
                    "school_name": gd["school"],
                    "special_group": f"{gd['code']}-{gd['sg']}",
                    "sg_name": gd["sg"],
                    "sg_info": sg_info,
                    "major_code": "",
                    "major_name": gd["major"],
                    "plan_count": gd.get("num") or "",
                    "tuition": gd.get("tuition") or "",
                    "duration": "",
                    "source_url": source_url,
                    "source_file": display_path(source_file),
                    "matched_official_group": 1,
                }
            )
    return rows


def read_frames(path: Path) -> list[pd.DataFrame]:
    suffix = path.suffix.lower()
    if suffix in {".xls", ".xlsx"}:
        sheets = pd.read_excel(path, sheet_name=None)
        return [frame for frame in sheets.values() if not frame.empty]
    if suffix == ".csv":
        return [pd.read_csv(path)]
    if suffix in {".html", ".htm"}:
        try:
            return pd.read_html(path, flavor="lxml")
        except (ImportError, ValueError):
            return []
    return []


def parse_source_file(path: Path, year: int, official_groups: dict[tuple, dict]) -> list[dict]:
    records: list[dict] = []
    for frame in read_frames(path):
        records.extend(
            normalize_frame(
                frame,
                year=year,
                source_file=path,
                official_groups=official_groups,
            )
        )
    if records:
        return records
    return parse_text_lines(
        extract_text(path),
        year=year,
        source_file=path,
        official_groups=official_groups,
    )


def dedupe(records: list[dict]) -> list[dict]:
    best: dict[tuple, dict] = {}
    for record in records:
        key = (
            record["year"],
            record["subject_category"],
            record["school_code"],
            record["special_group"],
            record.get("major_code") or record["major_name"],
        )
        if key not in best or int(record.get("matched_official_group") or 0) > int(best[key].get("matched_official_group") or 0):
            best[key] = record
    return list(best.values())


def write_outputs(records: list[dict]) -> None:
    grouped: dict[tuple[int, str], list[dict]] = defaultdict(list)
    for record in records:
        grouped[(int(record["year"]), record["subject_category"])].append(record)

    DETAIL_DIR.mkdir(parents=True, exist_ok=True)
    for (year, subject_category), rows in sorted(grouped.items()):
        suffix = SUBJECT_TO_FILE[subject_category]
        path = DETAIL_DIR / f"plan_details_{year}_{suffix}.csv"
        with path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            writer.writeheader()
            writer.writerows(rows)
        matched = sum(1 for row in rows if int(row.get("matched_official_group") or 0))
        print(f"{path.name}: {len(rows)} 行，官方组匹配 {matched}/{len(rows)}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=int, nargs="+", default=list(DEFAULT_YEARS))
    args = parser.parse_args()

    official_groups = load_official_group_index(args.years)
    all_records: list[dict] = []
    for year in args.years:
        year_dir = SOURCE_DIR / str(year)
        if not year_dir.exists():
            print(f"未找到计划源目录：{year_dir}")
            continue
        for path in sorted(year_dir.rglob("*")):
            if path.suffix.lower() not in {".csv", ".xls", ".xlsx", ".html", ".htm", ".txt", ".pdf"}:
                continue
            rows = parse_source_file(path, year, official_groups)
            if rows:
                print(f"{path.relative_to(PROJECT_ROOT)}: 解析 {len(rows)} 行")
                all_records.extend(rows)

    all_records = dedupe(all_records)
    write_outputs(all_records)
    print(f"完成：标准化专业计划 {len(all_records)} 行")


if __name__ == "__main__":
    main()
