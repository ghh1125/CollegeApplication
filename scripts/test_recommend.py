"""Run the full recommendation pipeline for a sample Zhejiang candidate."""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.input.profile import (
    CityPreference,
    Constraints,
    MajorPreference,
    Preferences,
    StudentProfile,
)
from src.input.filter import filter_by_constraints, filter_by_subject
from src.allocation.recommend import build_recommendations


def main() -> None:
    """Run filter -> history gap -> ranking -> volunteer-list assembly."""

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
                preferred_categories=["计算机类", "电子信息类"],
                excluded_majors=["土木工程", "护理学"],
            ),
            cities=CityPreference(
                preferred=["北京", "上海", "广州", "深圳", "杭州", "南京", "宁波", "苏州"],
                excluded_regions=["东北", "西北"],
            ),
        ),
        priority_mode="专业优先",
        risk_preference="均衡",
    )

    eligible, excluded_subject = filter_by_subject(profile, year=2025)
    final, excluded_constraints = filter_by_constraints(eligible, profile)

    result = build_recommendations(
        final,
        profile,
        main_priority="专业优先",
        preferred_majors=profile.preferences.majors.preferred_majors,
        preferred_categories=profile.preferences.majors.preferred_categories,
        preferred_schools=profile.preferences.schools.preferred_schools,
        preferred_cities=profile.preferences.cities.preferred,
        risk_preference=profile.risk_preference,
    )

    print(f"选科过滤后：{len(eligible)} 条（剔除 {len(excluded_subject)} 条）")
    print(f"硬约束过滤后：{len(final)} 条（剔除 {len(excluded_constraints)} 条）")
    print(f"志愿统计：{result['stats']}")
    for volunteer in result["volunteers"][:10]:
        gap_info = volunteer["gap_info"]
        print(
            f"#{volunteer['volunteer_no']} [{gap_info['tier']}] "
            f"{volunteer['school_name']} {volunteer['major_name']} "
            f"均值位次:{gap_info['weighted_avg']} gap:{gap_info['gap']}"
        )


if __name__ == "__main__":
    main()
