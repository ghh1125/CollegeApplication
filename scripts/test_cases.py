"""Multi-profile sanity check: run 6 representative scenarios and print top-15 each."""

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


CASES = [
    # ── Case 1: 顶尖理科生，专业优先，CS偏好，偏好北上广深 ──────────────────
    dict(
        label="Case1 顶尖CS 专业优先",
        profile=StudentProfile(
            rank=5000,
            total_score=680,
            selected_subjects=["物理", "化学", "生物"],
            constraints=Constraints(accept_private=False, accept_sino_foreign=False),
            preferences=Preferences(
                majors=MajorPreference(
                    preferred_majors=["计算机", "软件工程", "人工智能"],
                    preferred_categories=["计算机类"],
                    excluded_majors=[],
                ),
                cities=CityPreference(preferred=["北京", "上海", "深圳", "广州", "杭州"]),
            ),
            priority_mode="专业优先",
            risk_preference="均衡",
        ),
        priority="专业优先",
    ),
    # ── Case 2: 中等生，学校优先，无强专业偏好 ─────────────────────────────
    dict(
        label="Case2 中等生 学校优先",
        profile=StudentProfile(
            rank=50000,
            total_score=598,
            selected_subjects=["物理", "化学", "历史"],
            constraints=Constraints(accept_private=False, accept_sino_foreign=False),
            preferences=Preferences(
                majors=MajorPreference(
                    preferred_majors=[],
                    preferred_categories=["计算机类", "自动化类"],
                    excluded_majors=["护理学", "土木工程"],
                ),
                cities=CityPreference(preferred=["北京", "上海", "杭州", "南京", "苏州"]),
            ),
            priority_mode="学校优先",
            risk_preference="均衡",
        ),
        priority="学校优先",
    ),
    # ── Case 3: 偏低分理科生，城市优先，留浙 ──────────────────────────────
    dict(
        label="Case3 低分 城市优先 留浙江",
        profile=StudentProfile(
            rank=120000,
            total_score=555,
            selected_subjects=["物理", "化学", "地理"],
            constraints=Constraints(accept_private=False, accept_sino_foreign=False),
            preferences=Preferences(
                majors=MajorPreference(
                    preferred_majors=["计算机", "电子信息"],
                    preferred_categories=["计算机类", "电子信息类"],
                    excluded_majors=[],
                ),
                cities=CityPreference(preferred=["杭州", "宁波", "温州", "金华"]),
            ),
            priority_mode="城市优先",
            risk_preference="保守",
        ),
        priority="城市优先",
    ),
    # ── Case 4: 中高分，专业优先，电子信息偏好 ─────────────────────────────
    dict(
        label="Case4 中高分 电子信息 专业优先",
        profile=StudentProfile(
            rank=20000,
            total_score=644,
            selected_subjects=["物理", "化学", "生物"],
            constraints=Constraints(accept_private=False, accept_sino_foreign=False),
            preferences=Preferences(
                majors=MajorPreference(
                    preferred_majors=["电子信息", "通信工程", "集成电路"],
                    preferred_categories=["电子信息类", "自动化类"],
                    excluded_majors=[],
                ),
                cities=CityPreference(preferred=["北京", "上海", "深圳", "广州", "武汉"]),
            ),
            priority_mode="专业优先",
            risk_preference="激进",
        ),
        priority="专业优先",
    ),
    # ── Case 5: 高分，城市优先，偏好北京 ──────────────────────────────────
    dict(
        label="Case5 高分 城市优先 北京",
        profile=StudentProfile(
            rank=15000,
            total_score=650,
            selected_subjects=["物理", "化学", "生物"],
            constraints=Constraints(accept_private=False, accept_sino_foreign=False),
            preferences=Preferences(
                majors=MajorPreference(
                    preferred_majors=["计算机", "软件工程"],
                    preferred_categories=["计算机类"],
                    excluded_majors=[],
                ),
                cities=CityPreference(preferred=["北京"]),
            ),
            priority_mode="城市优先",
            risk_preference="均衡",
        ),
        priority="城市优先",
    ),
    # ── Case 6: 中等，学校优先，经济金融偏好 ──────────────────────────────
    dict(
        label="Case6 中等 学校优先 经济金融",
        profile=StudentProfile(
            rank=36000,
            total_score=626,
            selected_subjects=["物理", "化学", "历史"],
            constraints=Constraints(accept_private=False, accept_sino_foreign=False),
            preferences=Preferences(
                majors=MajorPreference(
                    preferred_majors=["金融", "经济", "统计"],
                    preferred_categories=["经济学类", "金融学类"],
                    excluded_majors=[],
                ),
                cities=CityPreference(preferred=["北京", "上海", "深圳"]),
            ),
            priority_mode="学校优先",
            risk_preference="均衡",
        ),
        priority="学校优先",
    ),
]


def run_case(case: dict) -> None:
    profile = case["profile"]
    label = case["label"]
    priority = case["priority"]

    eligible, ex_subj = filter_by_subject(profile, year=2025)
    final, ex_constr = filter_by_constraints(eligible, profile)

    result = build_recommendations(
        final,
        profile,
        main_priority=priority,
        preferred_majors=profile.preferences.majors.preferred_majors,
        preferred_categories=profile.preferences.majors.preferred_categories,
        preferred_schools=profile.preferences.schools.preferred_schools,
        preferred_cities=profile.preferences.cities.preferred,
        risk_preference=profile.risk_preference,
        total=80,
    )

    stats = result["stats"]
    vols = result["volunteers"]

    print(f"\n{'='*70}")
    print(f"  {label}  rank={profile.rank}  risk={profile.risk_preference}")
    print(f"  过滤后 {len(final)} 条  |  志愿: {stats}")
    print(f"{'='*70}")
    print(f"{'#':>3} {'tier':^5} {'学校':^16} {'城市':^6} {'专业':^22} {'评估':^5} {'软科':>5} {'均值位次':>8} {'gap':>7}")
    print("-"*80)

    for v in vols[:15]:
        gi = v.get("gap_info") or {}
        disc = v.get("discipline_grade") or "-"
        ruanke = v.get("ruanke_rank") or "-"
        school = (v.get("school_name") or "")[:16]
        major = (v.get("major_name") or "")[:22]
        city = (v.get("school_city") or "")[:6]
        tier = gi.get("tier", "?")
        wavg = gi.get("weighted_avg") or "-"
        gap = gi.get("gap")
        gap_str = f"{gap:+d}" if gap is not None else "-"
        print(f"{v['volunteer_no']:>3} {tier:^5} {school:^16} {city:^6} {major:^22} {disc:^5} {str(ruanke):>5} {str(wavg):>8} {gap_str:>7}")


def main() -> None:
    for case in CASES:
        try:
            run_case(case)
        except Exception as exc:
            print(f"\n[ERROR] {case['label']}: {exc}")
            import traceback; traceback.print_exc()

    print("\n\nDone.")


if __name__ == "__main__":
    main()
