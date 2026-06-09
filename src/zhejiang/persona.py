"""用户画像 / 使用场景分档（浙江）。

按考生「全省位次」把人分到三类画像，决定后续推荐策略的侧重点。
分档边界：
  - 顶尖精英：前 5,000        —— 目标清北华五
  - 高分优选：5,000 – 50,000  —— 985/211 与省内顶尖双非博弈
  - 中坚核心：50,000 – 一段线 —— 人数最多，求稳求实用
  - 一段线下：> 一段线        —— 一段线之外（二段/专科为主，本工具非核心场景）

一段线位次来自浙江省考试院一分一段表（普通类）：
  2025 普通类一段线 = 490 分，对应位次 184,372 名。
策略文案逐字采用产品需求文档「用户画像与使用场景」表，未自行杜撰。
"""

from __future__ import annotations

from dataclasses import dataclass

# 浙江普通类一段线对应位次（按年份；来源：省考试院一分一段表）
FIRST_SEGMENT_RANK = {
    2025: 184_372,  # 一段线 490 分
}
LATEST_FIRST_SEGMENT_RANK = FIRST_SEGMENT_RANK[2025]

ELITE_MAX = 5_000        # 顶尖精英上界（含）
PREMIUM_MAX = 50_000     # 高分优选上界（含）


@dataclass(frozen=True)
class Persona:
    key: str          # 机器标识
    name: str         # 画像名
    rank_desc: str    # 位次区间（人话）
    feature: str      # 核心特征
    pain: str         # 典型痛点
    value: str        # 智能体核心价值


PERSONAS: dict[str, Persona] = {
    "elite": Persona(
        key="elite", name="顶尖精英", rank_desc="全省前 5,000",
        feature="目标清北华五，专业志向极强",
        pain="冲名校怕丢专业，锁专业怕亏分",
        value="极小梯度冲刺 + 王牌专业锁定",
    ),
    "premium": Persona(
        key="premium", name="高分优选", rank_desc="5,000 – 50,000",
        feature="985/211 与省内顶尖双非博弈",
        pain="选校还是选专业难以权衡",
        value="行业强校准入校验 + 地域折价计算",
    ),
    "core": Persona(
        key="core", name="中坚核心", rank_desc="50,000 – 一段线",
        feature="人数最多，求稳求实用",
        pain="怕滑档、怕天坑、怕就业差",
        value="负面清单拦截 + 就业/考公路径加权",
    ),
    "below": Persona(
        key="below", name="一段线下", rank_desc="一段线之外",
        feature="一段线以下，以二段/专科批为主",
        pain="可填院校有限，重在稳妥录取",
        value="保底兜底 + 专科优质专业筛选",
    ),
}


def classify(rank: int, year: int = 2025) -> Persona:
    """按全省位次返回对应画像。"""
    seg = FIRST_SEGMENT_RANK.get(year, LATEST_FIRST_SEGMENT_RANK)
    if rank <= ELITE_MAX:
        return PERSONAS["elite"]
    if rank <= PREMIUM_MAX:
        return PERSONAS["premium"]
    if rank <= seg:
        return PERSONAS["core"]
    return PERSONAS["below"]
