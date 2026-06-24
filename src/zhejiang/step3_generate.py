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

import re
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
        elif rank <= rr <= rank + cfg.stable:
            wen.append(r)
        elif rank + cfg.stable < rr <= rank + cfg.safety:
            bao.append(r)
    return chong, wen, bao


def _norm_major(name: str) -> str:
    text = re.sub(r"\s+", "", name.strip())
    text = text.replace("（", "(").replace("）", ")")
    return re.sub(r"\([^)]*\)", "", text)


def _load_career_direction() -> dict[str, str]:
    """标准专业名 → 发展路径文本。优先 major_profile，兜底 major_description。"""
    with get_conn("zhejiang") as conn:
        career: dict[str, str] = {
            row[0]: row[1]
            for row in conn.execute(
                "SELECT major_name, career_direction FROM major_profile"
                " WHERE career_direction IS NOT NULL"
            )
        }
        for row in conn.execute(
            "SELECT name, do_what FROM major_description WHERE do_what IS NOT NULL"
        ):
            if row[0] not in career:
                career[row[0]] = row[1]
    return career


def _lookup_career(name: str, career: dict[str, str]) -> str | None:
    if name in career:
        return career[name]
    norm = _norm_major(name)
    return career.get(norm)


def _load_baoyan() -> tuple[dict[str, float | None], set[str]]:
    """学校名 → 保研率（%），无数据返回 None。同时返回「使用主校数据」的学校名集合。

    匹配顺序：
    1. 精确命中（DB 值 > 0 视为有效数据）
    2. 括号全角↔半角互换后命中同校区记录（DB 值 > 0）
    3. 去掉尾部括号后回落到主校名（校区专属记录不存在或值为 0）
    """
    import re

    def _normalize_brackets(s: str) -> str:
        return s.replace("（", "(").replace("）", ")")

    def _is_campus(name: str) -> bool:
        return bool(re.search(r"[（(][^）)]+[）)]$", name))

    with get_conn("zhejiang") as conn:
        raw = list(conn.execute(
            "SELECT school_name, recommend_master_rate FROM school_profile"
            " WHERE recommend_master_rate IS NOT NULL"
        ))

    base: dict[str, float | None] = {row[0]: row[1] for row in raw}
    norm_base: dict[str, float | None] = {_normalize_brackets(k): v for k, v in base.items()}

    result: dict[str, float | None] = {}
    fallback_schools: set[str] = set()

    with get_conn("zhejiang") as conn:
        cutoff_names = [row[0] for row in conn.execute("SELECT DISTINCT school_name FROM historical_cutoff")]

    all_names = set(base) | set(cutoff_names)

    for sn in all_names:
        # 步骤1：精确命中且有效（>0）
        if sn in base and (base[sn] or 0) > 0:
            result[sn] = base[sn]
            continue

        # 步骤2：括号形式互换后命中且有效
        normed = _normalize_brackets(sn)
        if normed in norm_base and (norm_base[normed] or 0) > 0:
            result[sn] = norm_base[normed]
            # 若原名与归一化后不同，说明只是括号形式差异，不算"参考主校"
            if normed != sn:
                continue
            continue

        # 步骤3：校区变体回落主校（括号内是校区信息，且主校有数据）
        stripped = re.sub(r"[（(][^）)]*[）)]$", "", sn).strip()
        if stripped != sn and stripped in base and (base[stripped] or 0) > 0:
            result[sn] = base[stripped]
            fallback_schools.add(sn)
            continue

        # 步骤4：无括号的分校名（如「山东大学威海分校」）→ 去掉尾部分校/校区词后匹配主校
        stripped2 = re.sub(r"(分校|校区|学院)$", "", sn)
        stripped2 = re.sub(r"[^一-龥a-zA-Z0-9]", "", stripped2[-6:]) if len(stripped2) < len(sn) else ""
        # 直接用常见后缀词切割：「XX大学YY分校」→「XX大学」
        m = re.match(r"^(.+?大学).+(分校|校区)$", sn)
        if m:
            parent = m.group(1)
            if parent in base and (base[parent] or 0) > 0:
                result[sn] = base[parent]
                fallback_schools.add(sn)
                continue

        # 无法匹配
        if sn in base:
            result[sn] = base[sn]  # 保留原值（含0.0）

    return result, fallback_schools


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
    baoyan, baoyan_fallback = _load_baoyan()
    career = _load_career_direction()

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

    # 最终排序：冲按 ref_rank 升序（难→易），稳/保按 ref_rank 升序
    picked.sort(key=lambda r: (_ref_rank(r) or 999999))

    def _build_table(src: list[dict], label: str) -> list[dict]:
        out = []
        for i, r in enumerate(src, 1):
            yrs = [r.get(f"{y}最低位次") for y in (2025, 2024, 2023)]
            have = [y for y in yrs if y]
            avg = round(sum(have) / len(have)) if have else None
            sn = r["院校名称"]
            out.append({
                "序号": i,
                "冲稳保": label,
                "专业名称": r["专业名称"], "专业代码": r["专业代码"],
                "二级学科": r["二级学科"],
                "学科评估": r["学科评估"],
                "软科专业排名": r.get("软科专业排名", "—"),
                "软科专业评级": r.get("软科专业评级", "—"),
                "保研率": baoyan.get(sn),
                "_baoyan_fallback": sn in baoyan_fallback,
                "专业发展路径": _lookup_career(r["专业名称"], career),
                "类别": r["类别"],
                "院校名称": sn, "院校代码": r["院校代码"],
                "招生官网": r.get("招生官网", ""),
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
        sn = r["院校名称"]
        final.append({
            "序号": i,
            "冲稳保": r.get("_cwb", "稳"),
            "专业名称": r["专业名称"], "专业代码": r["专业代码"],
            "二级学科": r["二级学科"],
            "学科评估": r["学科评估"],
            "软科专业排名": r.get("软科专业排名", "—"),
            "软科专业评级": r.get("软科专业评级", "—"),
            "保研率": baoyan.get(sn),
            "_baoyan_fallback": sn in baoyan_fallback,
            "专业发展路径": _lookup_career(r["专业名称"], career),
            "类别": r["类别"],
            "院校名称": sn, "院校代码": r["院校代码"],
            "招生官网": r.get("招生官网", ""),
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
