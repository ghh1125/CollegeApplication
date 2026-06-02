"""Targeted logic checks: city_blend score, major_level, preferred_categories fallback."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.zhejiang.ranking.rank import (
    GRADE_ORDER,
    CITY_TIER,
    _CATEGORY_DISCIPLINE_CODES,
    _lookup_discipline_code,
    _major_level,
    _school_quality_key,
    _city_key,
    sort_candidates,
    calculate_gap,
)
from db import get_conn


# ─── 1. city_blend score：北邮A vs 北外A+ ──────────────────────────────────
def check_city_blend() -> None:
    print("\n=== 1. city_blend: 北邮A vs 北外A+ ===")
    beiyou = {"school_name": "北京邮电大学", "ruanke_rank": 50, "discipline_grade": "A",  "school_city": "北京"}
    beiwei = {"school_name": "北京外国语大学","ruanke_rank": 79, "discipline_grade": "A+", "school_city": "北京"}
    score_beiyou = _school_quality_key(beiyou, [], city_blend=True)
    score_beiwei = _school_quality_key(beiwei, [], city_blend=True)
    print(f"  北邮  A  ruanke=50  score={score_beiyou}")
    print(f"  北外  A+ ruanke=79  score={score_beiwei}")
    print(f"  北邮 > 北外? {'✓' if score_beiyou > score_beiwei else '✗ BUG'}")

    shangjiao = {"school_name": "上海交通大学", "ruanke_rank": 3, "discipline_grade": "A+", "school_city": "上海"}
    score_sj = _school_quality_key(shangjiao, [], city_blend=True)
    print(f"\n  上交  A+ ruanke=3   score={score_sj}")
    print(f"  上交 > 北邮? {'✓' if score_sj > score_beiyou else '✗ BUG'}")


# ─── 2. major_level: preferred_categories fallback via discipline code ──────
def check_major_level() -> None:
    print("\n=== 2. major_level: preferred_categories fallback ===")
    preferred_majors = ["计算机", "软件工程", "人工智能"]
    preferred_categories = ["计算机类"]

    tests = [
        # (major_name, expected_level, note)
        ("计算机科学与技术", 4, "keyword直接命中"),
        ("软件工程",          4, "exact match"),
        ("人工智能",          3, "keyword match 人工智能→计算机"),
        ("电子信息工程",      2, "0810不在计算机类但0810 not in {0812,0835}→应为2? 不，不在计算机类"),
        ("计算机类",          3, "keyword '计算机' in '计算机类'"),
        ("数据科学与大数据技术", 3, "keyword '大数据'? no, '计算机'? no; keyword match via 数据→no; disc=0812→计算机类→2"),
        ("金融工程",          1, "0202不在计算机类→1"),
        ("通信工程",          1, "0810不在计算机类→1"),
        ("自动化",            1, "0811不在计算机类→1"),
    ]

    for major_name, expected, note in tests:
        prog = {"major_name": major_name, "normalized_major_name": major_name, "major_category": ""}
        got = _major_level(prog, preferred_majors, preferred_categories, expanded_major_names=None)
        ok = "✓" if got == expected else f"✗ got={got}"
        print(f"  {ok:10} {major_name:28} expected={expected}  {note}")

    # Check 数据科学 separately: disc code should be 0812 → category 计算机类 → level=2
    prog2 = {"major_name": "数据科学与大数据技术", "normalized_major_name": "数据科学与大数据技术", "major_category": ""}
    disc = _lookup_discipline_code("数据科学与大数据技术")
    got2 = _major_level(prog2, preferred_majors, preferred_categories)
    print(f"\n  数据科学: disc_code={disc}, level={got2} (expected ≥2 since 0812 in 计算机类)")


# ─── 3. city_tier spot checks ────────────────────────────────────────────────
def check_city_tiers() -> None:
    print("\n=== 3. city_tier spot checks ===")
    checks = [
        ("北京", 4), ("上海", 4), ("成都", 4), ("佛山", 4),
        ("济南", 3), ("厦门", 3), ("潍坊", 3),
        ("乌鲁木齐", 2), ("三亚", 2), ("衢州", 2),
        ("拉萨", 1), ("呼伦贝尔", 1),
    ]
    for city, expected in checks:
        got = CITY_TIER.get(city, 1)
        ok = "✓" if got == expected else f"✗ got={got}"
        print(f"  {ok:6} {city:8} tier={got}")


# ─── 4. preferred_categories coverage: all _CATEGORY_DISCIPLINE_CODES keys ──
def check_category_fallback() -> None:
    print("\n=== 4. _CATEGORY_DISCIPLINE_CODES coverage ===")
    representative = {
        "计算机类":       "计算机科学与技术",
        "电子信息类":     "通信工程",
        "自动化类":       "自动化",
        "电气类":         "电气工程及其自动化",
        "机械类":         "机械工程",
        "材料类":         "材料科学与工程",
        "化工类":         "化学工程与工艺",
        "土木类":         "土木工程",
        "建筑类":         "建筑学",
        "数学类":         "数学与应用数学",
        "物理学类":       "物理学",
        "化学类":         "化学",
        "生物科学类":     "生物科学",
        "环境科学类":     "环境科学",
        "经济学类":       "经济学",
        "金融学类":       "金融学",
        "法学类":         "法学",
        "新闻传播学类":   "新闻学",
        "中国语言文学类": "汉语言文学",
        "外国语言文学类": "英语",
        "医学类":         "临床医学",
        "管理科学类":     "管理科学",
        "工商管理类":     "工商管理",
    }
    for cat, major_name in representative.items():
        prog = {"major_name": major_name, "normalized_major_name": major_name, "major_category": ""}
        level = _major_level(prog, [], [cat])
        disc = _lookup_discipline_code(major_name)
        ok = "✓" if level == 2 else f"✗ got={level}"
        print(f"  {ok:6} [{cat}] {major_name:28} disc={disc}")


# ─── 5. DB check: 电子信息 + CS match in real data ──────────────────────────
def check_db_major_match() -> None:
    print("\n=== 5. Real DB: CS-category match for top CS majors ===")
    preferred_categories = ["计算机类"]
    preferred_majors = ["计算机", "软件工程"]

    with get_conn() as conn:
        rows = conn.execute("""
            SELECT DISTINCT major_name FROM major_subject_requirement
            WHERE major_name LIKE '%计算机%' OR major_name LIKE '%软件%' OR major_name LIKE '%人工智能%'
            ORDER BY major_name LIMIT 30
        """).fetchall()

    for (name,) in rows:
        prog = {"major_name": name, "normalized_major_name": name, "major_category": ""}
        level = _major_level(prog, preferred_majors, preferred_categories)
        disc = _lookup_discipline_code(name)
        print(f"  level={level} disc={disc or '?':6} {name}")


# ─── 6. 专业优先：确认 CS程序不会被school_best_grade污染 ────────────────────
def check_no_school_best_pollution() -> None:
    print("\n=== 6. 专业优先 school quality: no school_best_grade pollution ===")
    # 中央财经大学 A+(经济), 计算机专业无disc评估
    # 北邮 A(计算机)
    caijing = {
        "school_name": "中央财经大学",
        "ruanke_rank": 56,
        "discipline_grade": "",    # 计算机无评估
        "school_best_grade": "A+", # 经济A+，不相关
        "school_city": "北京",
    }
    beiyou = {
        "school_name": "北京邮电大学",
        "ruanke_rank": 50,
        "discipline_grade": "A",
        "school_best_grade": "A",
        "school_city": "北京",
    }
    key_caijing = _school_quality_key(caijing, [], major_first=True)
    key_beiyou  = _school_quality_key(beiyou,  [], major_first=True)
    print(f"  中央财经(专业优先) key={key_caijing}  (disc_grade={GRADE_ORDER.get('',0)}, ruanke=-56)")
    print(f"  北京邮电(专业优先) key={key_beiyou}  (disc_grade={GRADE_ORDER.get('A',0)}, ruanke=-50)")
    print(f"  北邮 > 中财? {'✓' if key_beiyou > key_caijing else '✗ BUG'}")


if __name__ == "__main__":
    check_city_blend()
    check_major_level()
    check_city_tiers()
    check_category_fallback()
    check_db_major_match()
    check_no_school_best_pollution()
    print("\n\nAll checks done.")
