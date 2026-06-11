"""浙江第二步：意向过滤（在第一步候选池上二次筛选）。

用户可在第一步结果上进一步收窄：
  - 排除不想要的门类 / 专业类 / 具体专业
  - 只保留偏好的门类 / 专业类 / 具体专业（三级均为可选；命中任一层即生效）
  - 开启教育部预警过滤（2020–2024 撤销布点 Top30）

输入：第一步 screen() 返回的 rows（list[dict]）
输出：过滤后的 rows（保留顺序）
"""

from __future__ import annotations


def apply_intent_filter(
    rows: list[dict],
    excl_cats: list[str],
    excl_cls: list[str],
    excl_majs: list[str],
    pref_cats: list[str],
    pref_cls: list[str],
    pref_majs: list[str],
    moe_warn: bool = False,
) -> list[dict]:
    """非意向剔除 + 专业偏好过滤 + 预警过滤。

    参数：
        rows       : step1_screen.screen() 的输出
        excl_cats  : 排除的门类名（如「工学」）
        excl_cls   : 排除的专业类名（如「计算机类」）
        excl_majs  : 排除的具体专业名
        pref_cats  : 偏好门类（非空时仅保留命中行）
        pref_cls   : 偏好专业类
        pref_majs  : 偏好具体专业
        moe_warn   : True 时过滤掉教育部预警专业
    """
    ec, el, em = set(excl_cats), set(excl_cls), set(excl_majs)
    pc, pl, pm = set(pref_cats), set(pref_cls), set(pref_majs)

    def _maj_excluded(name: str) -> bool:
        # 精确匹配 OR 括号变体（"护理学" 同时排除 "护理学(卓越班)"）
        if name in em:
            return True
        return any(name.startswith(base + "(") for base in em)

    def is_excluded(r: dict) -> bool:
        return (r.get("类别") in ec or
                r.get("二级学科") in el or
                _maj_excluded(r.get("专业名称", "")))

    def is_preferred(r: dict) -> bool:
        return (r.get("类别") in pc or
                r.get("二级学科") in pl or
                r.get("专业名称") in pm)

    out = [r for r in rows if not is_excluded(r)]
    if moe_warn:
        out = [r for r in out if not r.get("预警")]
    if pc or pl or pm:
        out = [r for r in out if is_preferred(r)]
    return out
