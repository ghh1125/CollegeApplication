"""体检受限规则（依据《普通高等学校招生体检工作指导意见》教学〔2003〕3号）。

这是**全国统一的固定文件**，不需要逐校抓取。把其中与色觉/视力相关、按专业类可落地的
条款，编码成「体检条件 → 受限专业类（4 位码）」的规则表，分两档：
  - 不予录取（hard）：学校不予录取，等于硬约束直接剔除。
  - 不宜就读（soft）：学校可不予录取/建议谨慎，作提示不强制剔除。

注意：意见原文有的按「专业」、有的按「专业类」、有的措辞为"专业类"或举例，
这里按专业类（4 位码）做**保守落地**——能明确对应到专业类的才编码，宁缺毋滥；
个别专业级差异留待后续按 national_code 细化。所有结论最终以院校招生章程为准。
"""

from __future__ import annotations

# 色盲（不能辨别红/黄/绿/蓝/紫各种颜色）→ 不予录取 的专业类（第二条之相关款）
_COLOR_BLIND_FORBID = {
    "0703": "化学类", "0813": "化工与制药类", "0827": "食品科学与工程类",
    "0826": "生物医学工程类", "0830": "生物工程类", "0710": "生物科学类",
    "0901": "植物生产类", "0902": "自然保护与环境生态类", "0903": "动物生产类",
    "0904": "动物医学类", "0905": "林学类", "0906": "水产类", "0907": "草学类",
    "1001": "基础医学类", "1002": "临床医学类", "1003": "口腔医学类",
    "1004": "公共卫生与预防医学类", "1005": "中医学类", "1006": "中西医结合类",
    "1007": "药学类", "1008": "中药学类", "1009": "法医学类",
    "1010": "医学技术类", "1011": "护理学类",
    "0709": "地质学类", "0814": "地质类", "0815": "矿业类",
    "0705": "地理科学类", "0707": "海洋科学类",
}

# 色弱 → 不予录取 的专业类（范围比色盲略小，但医、化、生、农林医技仍受限）
_COLOR_WEAK_FORBID = {
    "0703": "化学类", "0813": "化工与制药类",
    "0710": "生物科学类", "0826": "生物医学工程类", "0830": "生物工程类",
    "1001": "基础医学类", "1002": "临床医学类", "1003": "口腔医学类",
    "1004": "公共卫生与预防医学类", "1005": "中医学类", "1006": "中西医结合类",
    "1007": "药学类", "1008": "中药学类", "1009": "法医学类", "1010": "医学技术类",
    "0901": "植物生产类", "0903": "动物生产类", "0904": "动物医学类",
}

# 色弱/色盲 → 不宜就读（建议谨慎；学校可不予录取）的专业类
_COLOR_NOT_ADVISED = {
    "0705": "地理科学类", "0706": "大气科学类", "0708": "地球物理学类",
    "0828": "建筑类", "1305": "设计学类", "1304": "美术学类",
    "0818": "交通运输类",
}


def color_vision_restrictions(color_vision: str) -> dict[str, list[str]]:
    """按色觉返回受限专业类。

    返回 {"forbid": [code4...], "not_advised": [code4...]}。
    color_vision: 正常 / 色弱 / 色盲。
    """
    if color_vision == "色盲":
        forbid = dict(_COLOR_BLIND_FORBID)
    elif color_vision == "色弱":
        forbid = dict(_COLOR_WEAK_FORBID)
    else:
        return {"forbid": [], "not_advised": []}
    not_advised = {c: n for c, n in _COLOR_NOT_ADVISED.items() if c not in forbid}
    return {"forbid": sorted(forbid), "not_advised": sorted(not_advised)}


def restricted_class_names(color_vision: str) -> dict[str, list[str]]:
    """同上，但返回专业类名（便于展示提示）。"""
    src = (_COLOR_BLIND_FORBID if color_vision == "色盲"
           else _COLOR_WEAK_FORBID if color_vision == "色弱" else {})
    forbid_names = sorted(src.values())
    advised_names = sorted(n for c, n in _COLOR_NOT_ADVISED.items() if c not in src)
    return {"forbid": forbid_names, "not_advised": advised_names}
