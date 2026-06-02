"""Quick smoke-test for the subject + constraint filter pipeline."""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.input.profile import (
    Constraints,
    CityPreference,
    MajorPreference,
    Preferences,
    StudentProfile,
)
from src.input.filter import filter_by_constraints, filter_by_subject


def main() -> None:
    profile = StudentProfile(
        rank=36500,
        total_score=626,
        selected_subjects=["物理", "化学", "生物"],
        constraints=Constraints(
            accept_private=False,
            accept_sino_foreign=False,
        ),
        preferences=Preferences(
            majors=MajorPreference(
                preferred_majors=["计算机科学与技术", "软件工程", "人工智能"],
                excluded_majors=["土木工程", "护理学"],
            ),
            cities=CityPreference(
                preferred=["杭州", "上海", "南京"],
                excluded_regions=["东北", "西北"],
            ),
        ),
        priority_mode="专业优先",
        risk_preference="均衡",
    )

    # ── Step 1: subject filter ──────────────────────────────────────────────
    eligible, excluded_subject = filter_by_subject(profile, year=2025)

    # ── Step 2: constraint filter ───────────────────────────────────────────
    final, excluded_constraint = filter_by_constraints(eligible, profile)

    total = len(eligible) + len(excluded_subject)
    print(f"初始候选池：       {total:>6} 条")
    print(f"选科过滤后：       {len(eligible):>6} 条（剔除 {len(excluded_subject)} 条）")
    print(f"硬约束过滤后：     {len(final):>6} 条（剔除 {len(excluded_constraint)} 条）")

    # ── Excluded-reason breakdown ───────────────────────────────────────────
    subject_reasons = Counter(e["detail"] for e in excluded_subject)
    constraint_reasons = Counter(e["reason"] for e in excluded_constraint)

    print("\n剔除原因统计（选科）：")
    for reason, count in subject_reasons.most_common():
        print(f"  {reason}: {count} 条")

    print("\n剔除原因统计（硬约束）：")
    for reason, count in constraint_reasons.most_common():
        print(f"  {reason}: {count} 条")

    # ── Sanity checks ───────────────────────────────────────────────────────
    sino_in_final = [p for p in final if "中外合作" in (p.get("major_name") or "")]
    private_in_final = [p for p in final if "民办" in (p.get("school_name") or "")]
    excluded_major_in_final = [
        p for p in final
        if any(ex in (p.get("major_name") or "") for ex in ["土木工程", "护理学"])
    ]

    print("\n合理性校验：")
    print(f"  中外合作专业漏入：{len(sino_in_final)} 条（预期 0）")
    print(f"  民办学校漏入：    {len(private_in_final)} 条（预期 0）")
    print(f"  排除专业漏入：    {len(excluded_major_in_final)} 条（预期 0）")

    # ── Sample output ────────────────────────────────────────────────────────
    print("\n最终候选池前10条：")
    for p in final[:10]:
        warn = f" ⚠ {', '.join(p['_warnings'])}" if p.get("_warnings") else ""
        print(f"  {p['school_name']} | {p['major_name']}{warn}")


if __name__ == "__main__":
    main()
