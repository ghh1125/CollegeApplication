"""Application list builder stage."""

from __future__ import annotations

from collections import Counter
from typing import Any


# Zhejiang defaults — callers can pass province-specific values instead.
_DEFAULT_RISK_ALLOCATION: dict[str, dict[str, int]] = {
    "激进": {"冲": 30, "稳": 30, "保": 15, "垫": 5},
    "均衡": {"冲": 20, "稳": 30, "保": 20, "垫": 10},
    "保守": {"冲": 10, "稳": 25, "保": 30, "垫": 15},
}
_DEFAULT_TOTAL = 80

PRIMARY_TIERS = ["冲", "稳", "保", "垫"]
RESERVE_TIERS = ["高危冲", "数据不足"]


def build_volunteer_list(
    candidates: list[dict],
    risk_preference: str,
    total: int | None = None,
    risk_allocation: dict[str, dict[str, int]] | None = None,
) -> dict:
    """
    Build a volunteer list from already-sorted candidates.

    高危冲和数据不足默认只进备选池。若某层数量不足，优先按相邻层补齐；
    因当前 gap 计算不会直接产生"垫"，最后会用未使用的更安全/相近层补足。

    Pass `total` and `risk_allocation` from ProvinceConfig for non-Zhejiang provinces.
    """
    if risk_allocation is None:
        risk_allocation = _DEFAULT_RISK_ALLOCATION
    if total is None:
        total = _DEFAULT_TOTAL

    allocation = risk_allocation[risk_preference]
    pools = {tier: [] for tier in PRIMARY_TIERS + RESERVE_TIERS}
    for program in candidates:
        tier = program.get("gap_info", {}).get("tier", "数据不足")
        pools.setdefault(tier, []).append(program)

    reserve = pools["高危冲"] + pools["数据不足"]
    result: list[dict] = []
    used: set[int] = set()
    shortfall: dict[str, int] = {}

    for tier in PRIMARY_TIERS:
        target = allocation.get(tier, 0)
        selected = _take_unused(pools[tier], target, used)
        result.extend(selected)
        shortfall[tier] = target - len(selected)

    backup_order = [("冲", "稳"), ("稳", "保"), ("保", "垫"), ("垫", "保")]
    for from_tier, backup_tier in backup_order:
        missing = shortfall.get(from_tier, 0)
        if missing <= 0:
            continue
        supplement = _take_unused(pools[backup_tier], missing, used)
        result.extend(supplement)
        shortfall[from_tier] -= len(supplement)

    if len(result) < total:
        for tier in ["保", "稳", "冲", "垫"]:
            supplement = _take_unused(pools[tier], total - len(result), used)
            result.extend(supplement)
            if len(result) >= total:
                break

    result = _order_by_risk_tier(result)[:total]

    volunteers = [dict(program) for program in result]
    for index, program in enumerate(volunteers, start=1):
        program["volunteer_no"] = index

    tier_counts = Counter(program.get("gap_info", {}).get("tier") for program in volunteers)

    return {
        "volunteers": volunteers,
        "reserve": reserve,
        "stats": {
            "total": len(volunteers),
            "冲": tier_counts.get("冲", 0),
            "稳": tier_counts.get("稳", 0),
            "保": tier_counts.get("保", 0),
            "垫": tier_counts.get("垫", 0),
            "备选池": len(reserve),
        },
    }


def _take_unused(pool: list[dict], count: int, used: set[int]) -> list[dict]:
    if count <= 0:
        return []

    selected: list[dict] = []
    for program in pool:
        marker = id(program)
        if marker in used:
            continue
        selected.append(program)
        used.add(marker)
        if len(selected) >= count:
            break
    return selected


def _order_by_risk_tier(programs: list[dict]) -> list[dict]:
    ordered: list[dict] = []
    for tier in PRIMARY_TIERS:
        ordered.extend(
            program
            for program in programs
            if program.get("gap_info", {}).get("tier") == tier
        )
    return ordered
