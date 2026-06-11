#!/usr/bin/env python3
"""将浙江招录原始 JSONL 的学费/学制导入 admission_plan。

匹配策略（按优先级）：
  S1: major_full_name 精确匹配
  S2: major + major_subtitle 第一个括号 匹配
  S3: 前缀匹配（admission_plan名 startswith major_full_name 或反向）
  多候选但学费学制相同 → 安全填；真歧义（学费不同）→ 跳过

Usage:
    python scripts/import_zhejiang_enrollment.py
    python scripts/import_zhejiang_enrollment.py --jsonl data/zhejiang/raw/custom.jsonl
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "data" / "zhejiang" / "college.db"
DEFAULT_JSONL = PROJECT_ROOT / "data" / "zhejiang" / "raw" / "qianwen_enrollment_2025_zhejiang_undergrad.jsonl"


def norm(s: str) -> str:
    s = re.sub(r'[（]', '(', re.sub(r'[）]', ')', str(s or '')))
    return re.sub(r'\s+', ' ', s).strip()


def first_bracket(subtitle: str) -> str:
    m = re.match(r'(\([^)]*\))', norm(subtitle))
    return m.group(1) if m else ''


def _vals(r: dict) -> tuple[int | None, str | None]:
    t = int(r['tuition']) if r.get('tuition') else None
    d = str(r['major_length']) + '年' if r.get('major_length') else None
    return t, d


def _resolve(candidates: list[dict]) -> tuple[int | None, str | None] | None:
    if len(candidates) == 1:
        return _vals(candidates[0])
    if len(candidates) > 1:
        vals = set(_vals(r) for r in candidates)
        if len(vals) == 1:
            return _vals(candidates[0])
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--jsonl", default=str(DEFAULT_JSONL))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    with open(args.jsonl) as f:
        raw_rows = [json.loads(l) for l in f]

    school_raw: dict[str, list[dict]] = defaultdict(list)
    for r in raw_rows:
        sn = r.get('school_name', '').strip()
        if sn:
            school_raw[sn].append(r)

    conn = sqlite3.connect(DB_PATH)
    unmatched = conn.execute("""
        SELECT id, school_name, major_name FROM admission_plan
        WHERE (tuition IS NULL OR tuition = 0)
    """).fetchall()
    print(f"Unmatched rows (no tuition): {len(unmatched)}")

    to_update: list[tuple] = []
    skipped_ambiguous = skipped_no_match = 0

    for pid, sn, mn in unmatched:
        mn_n = norm(mn)
        candidates = school_raw.get(sn, [])
        if not candidates:
            skipped_no_match += 1
            continue

        result = None

        # S1: exact major_full_name
        s1 = [r for r in candidates if norm(r.get('major_full_name', '')) == mn_n]
        result = _resolve(s1)

        # S2: major + first bracket of subtitle
        if result is None:
            s2 = [r for r in candidates
                  if norm(r.get('major', '')) + first_bracket(r.get('major_subtitle', '')) == mn_n]
            result = _resolve(s2)

        # S3: prefix match
        if result is None:
            s3 = [r for r in candidates if mn_n.startswith(norm(r.get('major_full_name', '')))]
            if not s3:
                s3 = [r for r in candidates if norm(r.get('major_full_name', '')).startswith(mn_n)]
            result = _resolve(s3)

        if result is None:
            # Check if it's a true ambiguity or just no match
            all_s = s1 + (s2 if result is None else []) + (s3 if result is None else [])
            if any(len(x) > 1 for x in [s1, s2 if 's2' in dir() else [], s3 if 's3' in dir() else []]):
                skipped_ambiguous += 1
            else:
                skipped_no_match += 1
            continue

        tuition, duration = result
        if tuition or duration:
            to_update.append((pid, tuition, duration))

    print(f"To update: {len(to_update)}, ambiguous: {skipped_ambiguous}, no match: {skipped_no_match}")

    if args.dry_run:
        print("Dry run — no changes written.")
        return

    with conn:
        for pid, tuition, duration in to_update:
            if tuition and duration:
                conn.execute("UPDATE admission_plan SET tuition=?, duration=? WHERE id=?",
                             (tuition, duration, pid))
            elif tuition:
                conn.execute("UPDATE admission_plan SET tuition=? WHERE id=?", (tuition, pid))
            elif duration:
                conn.execute("UPDATE admission_plan SET duration=? WHERE id=?", (duration, pid))

    t = conn.execute("SELECT COUNT(*) FROM admission_plan WHERE tuition IS NOT NULL AND tuition>0").fetchone()[0]
    d = conn.execute("SELECT COUNT(*) FROM admission_plan WHERE duration IS NOT NULL AND duration!=''").fetchone()[0]
    n = conn.execute("SELECT COUNT(*) FROM admission_plan").fetchone()[0]
    print(f"Done. Tuition: {t}/{n} = {t/n*100:.1f}%  Duration: {d}/{n} = {d/n*100:.1f}%")
    conn.close()


if __name__ == "__main__":
    main()
