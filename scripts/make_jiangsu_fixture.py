"""Generate a small realistic Jiangsu raw-data fixture for pipeline validation.

The 掌上高考 API throttles aggressive scraping, so for validating the Jiangsu
pipeline (ingest → filter → recommend → 专业组 output) we synthesize a handful of
schools in the EXACT raw JSON shape the scraper produces. Values mirror real
observed field formats (sg_name "（08）", sg_info "首选物理，再选化学",
min_section = 录取位次). School names exist in common.db so enrichment works.

This is a VALIDATION fixture, not real admission data. Run:
    python scripts/make_jiangsu_fixture.py
then ingest with:  python -m src.jiangsu.input.ingest
"""

from __future__ import annotations

import json
from pathlib import Path

RAW = Path(__file__).resolve().parents[1] / "data" / "jiangsu" / "raw"
YEARS = (2025, 2024, 2023)

# (school_id, school_name, category, group_no, special_group_id, sg_info, [(major, base_rank), ...])
# base_rank ≈ 2025 录取位次；2024/2023 自动加微扰，构造三年数据
GROUPS = [
    # ── 物理类 ──
    ("111", "南京大学", "物理类", "07", "1781", "首选物理，再选化学",
     [("人工智能", 200), ("计算机科学与技术", 440), ("软件工程", 460)]),
    ("113", "东南大学", "物理类", "03", "1791", "首选物理，再选化学",
     [("计算机类", 2400), ("电子信息类", 3100), ("自动化类", 4600)]),
    ("114", "南京航空航天大学", "物理类", "06", "1881", "首选物理，再选化学",
     [("计算机科学与技术", 6200), ("人工智能", 6500), ("软件工程", 6800)]),
    ("115", "扬州大学", "物理类", "02", "1891", "首选物理，再选不限",
     [("计算机科学与技术", 7900), ("电气工程及其自动化", 8200), ("通信工程", 8400)]),
    ("112", "南京理工大学", "物理类", "05", "1801", "首选物理，再选化学",
     [("计算机科学与技术", 7800), ("软件工程", 8600), ("网络工程", 9200)]),
    ("280", "河海大学", "物理类", "04", "1811", "首选物理，再选不限",
     [("计算机科学与技术", 9000), ("水利水电工程", 12000), ("土木工程", 14000)]),
    ("281", "南京工业大学", "物理类", "02", "1821", "首选物理，再选化学",
     [("计算机科学与技术", 15000), ("化学工程与工艺", 19000), ("安全工程", 24000)]),
    ("282", "江苏大学", "物理类", "01", "1831", "首选物理，再选不限",
     [("机械工程", 20000), ("车辆工程", 22000), ("能源与动力工程", 26000)]),
    ("283", "南通大学", "物理类", "03", "1841", "首选物理，再选化学",
     [("临床医学", 28000), ("电气工程及其自动化", 41000), ("计算机科学与技术", 43000)]),
    # ── 历史类 ──
    ("111", "南京大学", "历史类", "01", "1851", "首选历史，再选不限",
     [("汉语言文学", 1500), ("法学", 1800), ("新闻学", 2400)]),
    ("184", "苏州大学", "历史类", "02", "1861", "首选历史，再选不限",
     [("汉语言文学", 3500), ("英语", 4000), ("历史学", 4300)]),
    ("287", "扬州大学", "历史类", "03", "1901", "首选历史，再选不限",
     [("汉语言文学", 4800), ("法学", 5200), ("英语", 5400)]),
    ("285", "南京师范大学", "历史类", "01", "1871", "首选历史，再选不限",
     [("汉语言文学", 6000), ("教育学", 8500), ("地理科学", 11000)]),
    ("286", "江苏师范大学", "历史类", "02", "1881", "首选历史，再选不限",
     [("汉语言文学", 25000), ("英语", 30000), ("旅游管理", 38000)]),
]

# 院校代号在 score/plan 里其实是 school_id；major spcode 随意编号
def _rank_for_year(base: int, year: int) -> int:
    # 2025=base, 2024=base*1.04, 2023=base*1.08（早年位次略高=更难，构造波动）
    factor = {2025: 1.0, 2024: 1.04, 2023: 1.08}[year]
    return int(base * factor)


def build() -> None:
    for kind in ("plan", "score"):
        for year in YEARS:
            (RAW / kind / str(year)).mkdir(parents=True, exist_ok=True)

    # group rows by (school_id, year) → one file per school per year (both 物理/历史 if同校)
    by_school: dict[tuple[str, int], dict] = {}
    for sid, sname, cat, gno, sgid, sginfo, majors in GROUPS:
        for year in YEARS:
            key = (sid, year)
            by_school.setdefault(key, {"name": sname, "score": [], "plan": []})
            for idx, (mname, base) in enumerate(majors):
                rank = _rank_for_year(base, year)
                score_min = 700 - rank // 200  # 粗略分数，仅占位
                common = {
                    "school_id": sid, "name": sname, "year": year,
                    "local_type_name": cat, "local_batch_name": "本科批",
                    "sg_name": f"（{gno}）", "special_group": sgid, "sg_info": sginfo,
                    "spcode": f"{gno}{idx:02d}", "spname": mname,
                }
                by_school[key]["score"].append({
                    **common, "min": score_min, "min_section": rank,
                })
                by_school[key]["plan"].append({
                    **common, "num": 30 + idx * 5, "tuition": 6800, "length": "四年",
                })

    for (sid, year), payload in by_school.items():
        for kind in ("plan", "score"):
            f = RAW / kind / str(year) / f"{sid}.json"
            # merge if file exists (same school both 物理/历史 already handled in one payload)
            f.write_text(json.dumps(
                {"school": {"school_id": sid, "name": payload["name"]},
                 "year": year, "items": payload[kind]},
                ensure_ascii=False), encoding="utf-8")

    n_files = len(list(RAW.rglob("*.json")))
    print(f"fixture 生成完毕：{n_files} 个 raw 文件（{len(GROUPS)} 组，物理类+历史类，3年）")


if __name__ == "__main__":
    build()
