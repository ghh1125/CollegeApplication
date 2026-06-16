"""浙江第二步：意向过滤（在第一步候选池上二次筛选）。

用户可在第一步结果上进一步收窄：
  - 排除不想要的具体专业（前端"全部"会展开为该专业类所有专业名）
  - 只保留偏好的具体专业（非空时仅保留命中行）
  - 开启教育部预警过滤（2020–2024 撤销布点 Top30）

输入：第一步 screen() 返回的 rows（list[dict]）
输出：过滤后的 rows（保留顺序）
"""

from __future__ import annotations


def apply_intent_filter(
    rows: list[dict],
    excl_majs: list[str],
    pref_majs: list[str],
    moe_warn: bool = False,
) -> list[dict]:
    """非意向剔除 + 专业偏好过滤 + 预警过滤。

    参数：
        rows      : step1_screen.screen() 的输出
        excl_majs : 排除的具体专业名列表
        pref_majs : 偏好的具体专业名列表（非空时仅保留命中行）
        moe_warn  : True 时过滤掉教育部预警专业
    """
    em = set(excl_majs)
    pm = set(pref_majs)

    def _excluded(name: str) -> bool:
        if name in em:
            return True
        return any(name.startswith(base + "(") for base in em)

    out = [r for r in rows if not _excluded(r.get("专业名称", ""))]
    if moe_warn:
        out = [r for r in out if not r.get("预警")]
    if pm:
        out = [r for r in out if r.get("专业名称") in pm]
    return out
