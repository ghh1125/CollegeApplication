"""浙江第三步：从二轮筛选结果生成最终 80 个志愿（冲稳保分档）。

分段配置（按考生位次）：
  位次段          冲间距          稳范围       保范围      冲/稳/保数量
  0 – 5000        每 50 名        +1000 内     +5000 内    20/40/20
  5001 – 10000    位次÷70        +2000 内     +10000 内   20/40/20
  10001 – 50000   位次÷100       +3000 内     +15000 内   20/30/30
  50001+          每 500 名       +3000 内     +15000 内   20/30/30

冲/稳/保判定（以 2025 最低位次为准，无则用 2024/2023）：
  冲：ref_rank < student_rank            （往年录取比考生靠前，需冲）
  稳：student_rank ≤ ref_rank ≤ rank+稳  （接近或略低于考生）
  保：rank+稳 < ref_rank ≤ rank+保       （明显低于考生）

冲：从「最近」往「最远」采样，相邻志愿间距 ≥ interval。
稳/保：从最近往最远排列（min_rank 升序）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from db import get_conn


@dataclass(frozen=True)
class _Cfg:
    interval:    int
    stable:      int   # 稳的后延上限
    safety:      int   # 保的后延上限（从 rank 算起，保从 stable 末尾接续）
    n_chong:     int
    n_wen:       int
    n_bao:       int
    rush_start:  int   # 冲区间左端 = max(1, rank - interval * n_chong)


def _config(rank: int) -> _Cfg:
    """
    三段连续区间（以考生位次 R 为基准）：
      冲：[rush_start, R)         rush_start = max(1, R - interval * 20)
      稳：[R, R + stable]
      保：(R + stable, R + safety]
    """
    if rank <= 5000:
        iv = 50
        return _Cfg(iv, 1_000, 5_000, 20, 40, 20,
                    max(1, rank - iv * 20))
    if rank <= 10000:
        iv = max(1, rank // 70)
        return _Cfg(iv, 2_000, 10_000, 20, 40, 20,
                    max(1, rank - iv * 20))
    if rank <= 50000:
        iv = max(1, rank // 100)
        return _Cfg(iv, 3_000, 15_000, 20, 30, 30,
                    max(1, rank - iv * 20))
    iv = 500
    return _Cfg(iv, 3_000, 15_000, 20, 30, 30,
                max(1, rank - iv * 20))


def _ref_rank(r: dict) -> int | None:
    """2025 → 2024 → 2023 优先取一个有效位次。"""
    for y in (2025, 2024, 2023):
        v = r.get(f"{y}最低位次")
        if v:
            return int(v)
    return None


def _label(r: dict, rank: int, cfg: _Cfg) -> str:
    rr = _ref_rank(r)
    if rr is None:
        return "—"
    if cfg.rush_start <= rr < rank:
        return "冲"
    if rank <= rr <= rank + cfg.stable:
        return "稳"
    if rank + cfg.stable < rr <= rank + cfg.safety:
        return "保"
    return "—"


def classify_rows(rows: list[dict], rank: int) -> list[dict]:
    """给每行打上 冲/稳/保/— 标签（原地修改并返回）。用于二轮表格展示。"""
    cfg = _config(rank)
    for r in rows:
        r["冲稳保"] = _label(r, rank, cfg)
    return rows


def _select_chong(pool: list[dict], count: int, interval: int) -> list[dict]:
    """冲池：按 ref_rank 降序（最近先），间距 ≥ interval，取 count 个。"""
    sorted_pool = sorted(
        [r for r in pool if _ref_rank(r) is not None],
        key=lambda r: _ref_rank(r),      # type: ignore[arg-type]
        reverse=True,
    )
    result: list[dict] = []
    last: int | None = None
    for r in sorted_pool:
        rr = _ref_rank(r)
        if last is None or (last - rr) >= interval:  # type: ignore[operator]
            result.append(r)
            last = rr
            if len(result) >= count:
                break
    # 如果间距过滤后数量不足，从剩余里补（不再要求间距）
    if len(result) < count:
        picked_ids = {id(x) for x in result}
        for r in sorted_pool:
            if id(r) not in picked_ids:
                result.append(r)
                if len(result) >= count:
                    break
    return result


def _select_front(pool: list[dict], count: int) -> list[dict]:
    """稳/保池：按 ref_rank 升序（最近先），取 count 个。"""
    sorted_pool = sorted(
        [r for r in pool if _ref_rank(r) is not None],
        key=lambda r: _ref_rank(r),  # type: ignore[arg-type]
    )
    return sorted_pool[:count]


def split_pools(
    rows: list[dict], rank: int, cfg: _Cfg
) -> tuple[list[dict], list[dict], list[dict]]:
    """将候选行按冲/稳/保分成三个连续区间（无有效位次的行丢弃）：
      冲：[rush_start, rank)
      稳：[rank, rank + stable]
      保：(rank + stable, rank + safety]
    """
    chong, wen, bao = [], [], []
    for r in rows:
        rr = _ref_rank(r)
        if rr is None:
            continue
        if cfg.rush_start <= rr < rank:
            chong.append(r)
        elif rr <= rank + cfg.stable:
            wen.append(r)
        elif rr <= rank + cfg.safety:
            bao.append(r)
    return chong, wen, bao


def _load_baoyan() -> dict[str, float | None]:
    """学校名 → 保研率（%），无数据返回 None。"""
    with get_conn("zhejiang") as conn:
        return {
            row[0]: row[1]
            for row in conn.execute(
                "SELECT school_name, recommend_master_rate FROM school_profile"
                " WHERE recommend_master_rate IS NOT NULL"
            )
        }


def generate(
    student: Any,
    rows: list[dict],
) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    """从二轮筛选结果生成最终志愿。

    返回 (chong_table, wen_table, bao_table, final_80)。
    前三个是分档完整候选（不限数量），final_80 是按规则选出的最终志愿。
    """
    rank = int(student.rank)
    cfg = _config(rank)
    baoyan = _load_baoyan()

    chong_pool, wen_pool, bao_pool = split_pools(rows, rank, cfg)

    # 按规则选取
    sel_c = _select_chong(chong_pool, cfg.n_chong, cfg.interval)
    sel_w = _select_front(wen_pool,   cfg.n_wen)
    sel_b = _select_front(bao_pool,   cfg.n_bao)

    # 标记
    for r in sel_c: r["_cwb"] = "冲"
    for r in sel_w: r["_cwb"] = "稳"
    for r in sel_b: r["_cwb"] = "保"

    picked = sel_c + sel_w + sel_b

    # 不足 80 时从全部二轮结果里按「离考生位次最近」补（有位次数据的优先）
    if len(picked) < 80:
        picked_ids = {id(x) for x in picked}
        rest = sorted(
            [r for r in rows if id(r) not in picked_ids and _ref_rank(r) is not None],
            key=lambda r: abs(_ref_rank(r) - rank),  # type: ignore[operator]
        )
        for r in rest:
            r["_cwb"] = _label(r, rank, cfg) or "稳"
            picked.append(r)
            picked_ids.add(id(r))
            if len(picked) >= 80:
                break
    picked = picked[:80]

    # 最终排序：冲按 ref_rank 升序（难→易），稳/保按 ref_rank 升序
    picked.sort(key=lambda r: (_ref_rank(r) or 999999))

    def _build_table(src: list[dict], label: str) -> list[dict]:
        out = []
        for i, r in enumerate(src, 1):
            yrs = [r.get(f"{y}最低位次") for y in (2025, 2024, 2023)]
            have = [y for y in yrs if y]
            avg = round(sum(have) / len(have)) if have else None
            out.append({
                "序号": i,
                "冲稳保": label,
                "专业名称": r["专业名称"], "专业代码": r["专业代码"],
                "二级学科": r["二级学科"],
                "学科评估": r["学科评估"],
                "保研率": baoyan.get(r["院校名称"]),
                "类别": r["类别"],
                "院校名称": r["院校名称"], "院校代码": r["院校代码"],
                "层次": r["层次"],
                "学制": r.get("学制", "—"),
                "学费/年": r.get("学费/年", "—"),
                "2025最低位次": r.get("2025最低位次"),
                "2024最低位次": r.get("2024最低位次"),
                "2023最低位次": r.get("2023最低位次"),
                "三年平均位次": avg,
                "预警": "⚠️" if r.get("预警") else "",
            })
        return out

    # 三档完整候选表（显示用）
    chong_table = _build_table(
        sorted(chong_pool, key=lambda r: _ref_rank(r) or 999999), "冲"
    )
    wen_table = _build_table(
        sorted(wen_pool, key=lambda r: _ref_rank(r) or 999999), "稳"
    )
    bao_table = _build_table(
        sorted(bao_pool, key=lambda r: _ref_rank(r) or 999999), "保"
    )

    # 最终 80
    final = []
    for i, r in enumerate(picked, 1):
        yrs = [r.get(f"{y}最低位次") for y in (2025, 2024, 2023)]
        have = [y for y in yrs if y]
        avg = round(sum(have) / len(have)) if have else None
        final.append({
            "序号": i,
            "冲稳保": r.get("_cwb", "稳"),
            "专业名称": r["专业名称"], "专业代码": r["专业代码"],
            "二级学科": r["二级学科"],
            "学科评估": r["学科评估"],
            "保研率": baoyan.get(r["院校名称"]),
            "类别": r["类别"],
            "院校名称": r["院校名称"], "院校代码": r["院校代码"],
            "层次": r["层次"],
            "学制": r.get("学制", "—"),
            "学费/年": r.get("学费/年", "—"),
            "2025最低位次": r.get("2025最低位次"),
            "2024最低位次": r.get("2024最低位次"),
            "2023最低位次": r.get("2023最低位次"),
            "三年平均位次": avg,
            "预警": "⚠️" if r.get("预警") else "",
        })
    return chong_table, wen_table, bao_table, final
