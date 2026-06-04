"""Generate a small realistic Shanghai raw-data fixture for pipeline validation.

Real Shanghai data is produced separately (official 上海招考热线 + 掌上高考). For
validating the Shanghai pipeline (ingest → filter → recommend → 院校专业组 output)
we synthesize official-format CSVs (subject_category=综合, single pool). School
names exist in common.db so enrichment works.

VALIDATION fixture only, not real admission data. Run:
    python scripts/make_shanghai_fixture.py
    python -m src.shanghai.input.ingest
"""

from __future__ import annotations

import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OFFICIAL = ROOT / "data" / "shanghai" / "raw" / "official"
PLAN = ROOT / "data" / "shanghai" / "raw" / "plan_details"
YEARS = (2025, 2024, 2023)
CAT = "综合"

# (school_code, school_name, group_no, sg_info, base_rank, [majors])
GROUPS = [
    ("145", "复旦大学", "01", "物理、化学(2科必选)", 600, ["人工智能", "计算机科学与技术", "微电子科学与工程"]),
    ("176", "同济大学", "02", "物理", 6200, ["计算机科学与技术", "软件工程", "电子信息类"]),
    ("177", "华东理工大学", "03", "物理、化学(2科必选)", 7200, ["计算机科学与技术", "化学工程与工艺"]),
    ("178", "东华大学", "01", "物理", 8400, ["计算机类", "电子信息工程", "纺织工程"]),
    ("179", "上海大学", "05", "物理", 8800, ["计算机科学与技术", "通信工程", "自动化"]),
    ("180", "上海理工大学", "02", "物理", 13000, ["计算机科学与技术", "机械设计制造及其自动化"]),
    ("181", "上海师范大学", "04", "不限", 22000, ["汉语言文学", "英语", "计算机科学与技术"]),
    ("182", "上海海事大学", "03", "物理", 30000, ["航海技术", "物流管理", "计算机科学与技术"]),
    # 一个历史/不限组，验证单池里 6选3 任意组合可报
    ("183", "华东师范大学", "06", "不限", 4200, ["汉语言文学", "历史学", "工商管理类"]),
]


def _rank(base: int, year: int) -> int:
    return int(base * {2025: 1.0, 2024: 1.04, 2023: 1.08}[year])


def build() -> None:
    OFFICIAL.mkdir(parents=True, exist_ok=True)
    PLAN.mkdir(parents=True, exist_ok=True)
    for year in YEARS:
        cutoff_rows, plan_rows = [], []
        for code, school, gno, sg_info, base, majors in GROUPS:
            sgid = f"{code}-{gno}"
            rank = _rank(base, year)
            score = 660 - rank // 400
            cutoff_rows.append({
                "year": year, "subject_category": CAT, "school_code": code,
                "school_name": school, "special_group": sgid, "sg_name": gno,
                "sg_info": sg_info, "min_score": score, "min_rank": rank,
                "source_url": "fixture",
            })
            for i, mj in enumerate(majors):
                plan_rows.append({
                    "year": year, "subject_category": CAT, "school_code": code,
                    "school_name": school, "special_group": sgid, "sg_name": gno,
                    "sg_info": sg_info, "major_code": f"{gno}{i:02d}", "major_name": mj,
                    "plan_count": 20 + i * 4, "tuition": 6500, "duration": "四年",
                    "source_url": "fixture", "source_file": "fixture", "matched_official_group": sgid,
                })
        with (OFFICIAL / f"cutoff_{year}.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(cutoff_rows[0].keys())); w.writeheader(); w.writerows(cutoff_rows)
        with (PLAN / f"plan_details_{year}.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(plan_rows[0].keys())); w.writeheader(); w.writerows(plan_rows)
    print(f"fixture 生成：{len(GROUPS)} 组 × {len(YEARS)} 年，单池「综合」")


if __name__ == "__main__":
    build()
