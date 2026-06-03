"""Audit Jiangsu group-inner major coverage against official cutoff groups."""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = PROJECT_ROOT / "data" / "jiangsu" / "raw"
OFFICIAL_DIR = RAW_DIR / "official"
DETAIL_DIR = RAW_DIR / "plan_details"
DEFAULT_YEARS = (2025, 2024, 2023)
SUBJECTS = {"physics": "物理类", "history": "历史类"}


def load_official_groups(year: int, subject_key: str) -> dict[tuple[str, str], dict]:
    path = OFFICIAL_DIR / f"cutoff_{year}_{subject_key}.csv"
    groups: dict[tuple[str, str], dict] = {}
    if not path.exists():
        return groups
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            groups[(row["school_code"], row["special_group"])] = row
    return groups


def load_detail_groups(year: int, subject_key: str) -> dict[tuple[str, str], int]:
    path = DETAIL_DIR / f"plan_details_{year}_{subject_key}.csv"
    groups: dict[tuple[str, str], int] = defaultdict(int)
    if not path.exists():
        return groups
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            groups[(row["school_code"], row["special_group"])] += 1
    return dict(groups)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fail-under", type=float, default=None, help="专业组覆盖率低于该百分比时退出 1")
    args = parser.parse_args()

    total_official = 0
    total_matched = 0
    total_majors = 0
    for year in DEFAULT_YEARS:
        for subject_key, subject_category in SUBJECTS.items():
            official = load_official_groups(year, subject_key)
            details = load_detail_groups(year, subject_key)
            matched = set(official) & set(details)
            missing = [official[key] for key in sorted(set(official) - set(details))]
            major_count = sum(details.values())
            total_official += len(official)
            total_matched += len(matched)
            total_majors += major_count
            print(
                f"{year} {subject_category}: 官方组 {len(official)}，"
                f"已补专业组 {len(matched)}，专业明细 {major_count}，"
                f"缺组 {len(missing)}"
            )
            if missing:
                examples = [
                    f"{row['school_code']} {row['school_name']} {row['sg_name']}({row['sg_info']})"
                    for row in missing[:8]
                ]
                print("  缺口示例：" + "；".join(examples))

    coverage = (total_matched / total_official * 100) if total_official else 0.0
    print(
        f"=== 江苏计划明细覆盖率 === 官方组 {total_official}，"
        f"已补 {total_matched}，专业明细 {total_majors}，覆盖率 {coverage:.2f}%"
    )
    if args.fail_under is not None and coverage < args.fail_under:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
