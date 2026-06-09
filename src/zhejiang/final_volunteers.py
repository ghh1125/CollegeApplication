"""浙江第二步：从候选池收敛到 80 个志愿（冲稳保分档 + 梯度 + 排序主键）。

输入：第一步 screen() 的候选池（已按选科/学科/地域/体检/位次窗口筛过）。
输出：固定 80 个志愿，分冲/稳/保，按 2025 位次排序，附最终表所有列。

分段梯度配置（按考生位次落段）：
  ┌ 分数段 ┬ 梯度间距 ┬ 冲/稳/保比例 ┬ 排序主键 ┐
  │ 前5,000      │ 15名/志愿     │ 3:3:2 │ 学科评估A类 > 保研率        │
  │ 5,000-50,000 │ 位次/100/志愿 │ 2:4:2 │ 行业特色 > 地域折价         │
  │ 50,000-一段线│ 500位次/志愿  │ 2:3:5 │ 公办属性 > 录取概率(位次余量)│
  └──────────────┴───────────────┴───────┴──────────────────────────────┘

冲稳保按「该专业2025录取位次 / 考生位次」划分：
  冲 < 0.95（录取线比你高，需冲）｜稳 0.95~1.05｜保 > 1.05（比你低，较稳）

数量固定凑满 80：各档按比例分配名额；某档候选不足时，名额由其余候选补齐。
留空列（暂无数据）：考研路径、天坑/预警/撤销过滤。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from db import get_conn
from src.zhejiang.persona import ELITE_MAX, PREMIUM_MAX, classify
from src.zhejiang.screening import _norm, screen

TARGET_TOTAL = 80
# 冲稳保分界（按 录取位次/考生位次）
CHONG_MAX = 0.95   # < 0.95 = 冲
WEN_MAX = 1.05     # 0.95~1.05 = 稳，> 1.05 = 保
# 保底"录取概率≥99%"的安全余量启发式：录取位次 ≥ 考生位次 ×1.1（比你低 10%+ 算很稳）
SAFE_MARGIN = 1.1

_GRADE_SCORE = {"A+": 9, "A": 8, "A-": 7, "B+": 6, "B": 5, "B-": 4, "C+": 3, "C": 2, "C-": 1}


@dataclass(frozen=True)
class SegmentConfig:
    name: str
    gradient: Callable[[int], int]   # 梯度间距（位次），入参考生位次
    ratio: tuple[int, int, int]      # 冲/稳/保 比例
    keys: tuple[str, ...]            # 排序主键（从高到低优先级）


def _segment(rank: int) -> SegmentConfig:
    if rank <= ELITE_MAX:
        return SegmentConfig("前5000", lambda r: 15, (3, 3, 2), ("grade", "baoyan"))
    if rank <= PREMIUM_MAX:
        return SegmentConfig("5000-50000", lambda r: max(1, r // 100), (2, 4, 2), ("industry", "region"))
    return SegmentConfig("50000-一段线", lambda r: 500, (2, 3, 5), ("public", "admit_prob"))


def _quotas(ratio: tuple[int, int, int], total: int = TARGET_TOTAL) -> tuple[int, int, int]:
    """把比例归一到 total（凑满 80），余数给「稳」。"""
    s = sum(ratio)
    c = round(ratio[0] / s * total)
    b = round(ratio[2] / s * total)
    w = total - c - b
    return c, w, b


def _load_extras(conn: Any) -> dict:
    """排序主键要用的学校/专业附加数据。"""
    sp = {
        r[0]: {"baoyan": r[1] or 0.0, "nature": r[2] or "", "stype": r[3] or ""}
        for r in conn.execute("SELECT school_name, recommend_master_rate, school_nature, school_type FROM school_profile")
    }
    career = {
        _norm(r[0]): (r[1] or "")
        for r in conn.execute("SELECT major_name, career_direction FROM major_profile WHERE career_direction!=''")
    }
    return {"sp": sp, "career": career}


# 有「行业特色」的学校类型（用于 5000-50000 段排序主键）
_INDUSTRY_TYPES = {"财经类", "政法类", "师范类", "医药类", "理工类", "农林类", "语言类", "艺术类", "体育类"}
_LEVEL_SCORE = {"985": 4, "211": 3, "双一流": 2, "其他": 1}


def _score_fn(seg: SegmentConfig, rank: int, sp: dict) -> Callable[[dict], tuple]:
    """返回 row → 排序分数元组（越大越优先），按该段排序主键。"""
    def score(r: dict) -> tuple:
        info = sp.get(r["院校名称"], {})
        vals = []
        for k in seg.keys:
            if k == "grade":
                vals.append(_GRADE_SCORE.get(r.get("学科评估", ""), 0))
            elif k == "baoyan":
                vals.append(info.get("baoyan", 0.0))
            elif k == "industry":      # 行业特色：有特色类型优先，再按层次
                vals.append(1 if info.get("stype") in _INDUSTRY_TYPES else 0)
            elif k == "region":        # 地域折价：层次高的优先（折价系数低）
                vals.append(_LEVEL_SCORE.get(r.get("层次", "其他"), 1))
            elif k == "public":        # 公办属性
                vals.append(1 if info.get("nature") == "公办" else 0)
            elif k == "admit_prob":    # 录取概率：过安全门槛后，位次越接近考生(学校越好)越优先
                r25 = r.get("2025最低位次") or rank
                vals.append(-r25)      # -位次：越小(越好/越接近)分越高
        return tuple(vals)
    return score


def _select(cands: list[dict], quota: int, gradient: int, scorefn: Callable[[dict], tuple]) -> list[dict]:
    """在一个档内按梯度间距铺开取 quota 个：每个梯度桶取主键最优者，不足再补。"""
    if not cands or quota <= 0:
        return []
    cands = sorted(cands, key=lambda r: r["2025最低位次"])
    base = cands[0]["2025最低位次"]
    g = max(1, gradient)
    buckets: dict[int, list[dict]] = {}
    for r in cands:
        buckets.setdefault((r["2025最低位次"] - base) // g, []).append(r)
    winners = [max(v, key=scorefn) for v in buckets.values()]
    winners.sort(key=lambda r: r["2025最低位次"])
    picked = winners[:quota]
    if len(picked) < quota:  # 桶不够 → 从剩余里按主键补
        chosen = {id(x) for x in picked}
        rest = sorted((r for r in cands if id(r) not in chosen), key=scorefn, reverse=True)
        picked += rest[: quota - len(picked)]
    return picked


def _pool_at_least(student: Any) -> list[dict]:
    """取候选池；不足 80 时逐步放宽位次窗口（保侧放得更宽），直至够 80 或窗口到顶。"""
    p = classify(int(student.rank))
    reach, safe = p.reach_mult, p.safe_mult
    pool = screen(student)
    while len(pool) < TARGET_TOTAL and (reach > 0.2 or safe < 4.0):
        reach = max(0.2, reach - 0.1)
        safe = min(4.0, safe + 0.3)
        pool = screen(student, reach=reach, safe=safe)
    return pool


def generate(student: Any, exclude_keywords: list[str] | None = None) -> list[dict]:
    """生成最终 80 志愿。exclude_keywords：专业名包含任一关键词则剔除（用户自定义过滤）。"""
    pool = _pool_at_least(student)
    kws = [k.strip() for k in (exclude_keywords or []) if k.strip()]
    if kws:
        pool = [r for r in pool if not any(k in r["专业名称"] for k in kws)]
    rank = int(student.rank)
    seg = _segment(rank)
    with get_conn("zhejiang") as conn:
        extras = _load_extras(conn)
    sp, career = extras["sp"], extras["career"]
    scorefn = _score_fn(seg, rank, sp)

    # 分冲稳保
    chong, wen, bao = [], [], []
    for r in pool:
        ratio = (r["2025最低位次"] or rank) / rank
        (chong if ratio < CHONG_MAX else bao if ratio > WEN_MAX else wen).append(r)

    qc, qw, qb = _quotas(seg.ratio)
    g = seg.gradient(rank)
    # 保底先过"≥99%稳"安全门槛(录取位次≥考生×SAFE_MARGIN)；够名额才用，否则退回全部保底
    bao_safe = [r for r in bao if (r["2025最低位次"] or rank) >= rank * SAFE_MARGIN]
    bao_pool = bao_safe if len(bao_safe) >= qb else bao
    sel_c = _select(chong, qc, g, scorefn)
    sel_w = _select(wen, qw, g, scorefn)
    sel_b = _select(bao_pool, qb, g, scorefn)
    for r in sel_c:
        r["_cwb"] = "冲"
    for r in sel_w:
        r["_cwb"] = "稳"
    for r in sel_b:
        r["_cwb"] = "保"
    picked = sel_c + sel_w + sel_b

    # 固定凑满 80：不足则从未选候选里按「离考生位次最近」补
    if len(picked) < TARGET_TOTAL:
        chosen = {id(x) for x in picked}
        rest = sorted((r for r in pool if id(r) not in chosen),
                      key=lambda r: abs((r["2025最低位次"] or rank) - rank))
        for r in rest[: TARGET_TOTAL - len(picked)]:
            ratio = (r["2025最低位次"] or rank) / rank
            r["_cwb"] = "冲" if ratio < CHONG_MAX else "保" if ratio > WEN_MAX else "稳"
            picked.append(r)
    picked = picked[:TARGET_TOTAL]

    # 组装最终列，按 2025 位次升序编号
    picked.sort(key=lambda r: r["2025最低位次"])
    out: list[dict] = []
    for i, r in enumerate(picked, 1):
        yrs = [r.get(f"{y}最低位次") for y in (2025, 2024, 2023)]
        have = [y for y in yrs if y]
        avg = round(sum(have) / len(have)) if have else None
        out.append({
            "序号": i,
            "冲稳保": r["_cwb"],
            "专业名称": r["专业名称"], "专业代码": r["专业代码"],
            "二级学科": r["二级学科"],
            "学科评估": r["学科评估"],
            "考研路径": "—",  # 暂留空
            "专业发展路径": career.get(_norm(r["专业名称"]), "—"),
            "类别": r["类别"],
            "院校名称": r["院校名称"], "院校代码": r["院校代码"],
            "层次": r["层次"],
            "2025最低位次": r["2025最低位次"],
            "2024最低位次": r["2024最低位次"],
            "2023最低位次": r["2023最低位次"],
            "三年平均位次": avg,
        })
    return out
