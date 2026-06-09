"""体检受限规则 —— 依据《普通高等学校招生体检工作指导意见》（教学〔2003〕3号）原文编码。

来源：教育部 moe.gov.cn 原文（深大研究生院等多处转载一致）。全国统一固定文件，无需逐校抓取。
原则（应用户要求，照原文、不臆造）：
  - 专业名**逐字照原文**保留在 `majors`；只有原文写「X 类各专业」的整类才映射到 4 位专业类码 `classes`。
  - 具体专业到专业类的映射交给数据层（按 major_description.national_code 精确匹配），本表不猜。
  - 措辞分两档：不能录取(forbid) / 不宜就读(not_advised)。

注意：原文中「乙肝表面抗原携带者不予录取」相关条款已被 2010 年教育部、卫生部、人社部
《关于进一步规范入学和就业体检项目维护乙肝表面抗原携带者入学和就业权利的通知》**废止**，
故本表不录入乙肝限制。
"""

from __future__ import annotations

# 医学门类（10）全部专业类——原文「医学类各专业」
_MEDICAL_CLASSES = ["1001", "1002", "1003", "1004", "1005", "1006",
                    "1007", "1008", "1009", "1010", "1011"]

# 体检条件 → 受限规则。每条：level(forbid/not_advised) + classes(整类码) + majors(原文专业名)
RULES: dict[str, dict] = {
    # ── 色觉 ──
    "色弱": {
        "level": "forbid",
        "desc": "轻度色觉异常（色弱）不能录取的专业",
        "classes": ["0703", "0813", "1007", "0710", "0831", "0709"] + _MEDICAL_CLASSES,  # 化学/化工与制药/药学/生物科学/公安技术/地质学类 + 医学类各专业
        "majors": [
            "生物工程", "生物医学工程", "动物医学", "动物科学", "野生动物与自然保护区管理",
            "心理学", "应用心理学", "生态学", "侦察学", "特种能源工程与烟火技术", "考古学",
            "海洋科学", "海洋技术", "轮机工程", "食品科学与工程", "轻化工程", "林产化工",
            "农学", "园艺", "植物保护", "茶学", "林学", "园林", "蚕学", "农业资源与环境",
            "水产养殖学", "海洋渔业科学与技术", "材料化学", "环境工程", "高分子材料与工程",
            "过程装备与控制工程", "学前教育", "特殊教育", "体育教育", "运动训练",
            "运动人体科学", "民族传统体育",
        ],
    },
    "色盲": {
        "level": "forbid",
        "desc": "色觉异常 II 度（色盲）不能录取的专业（含色弱全部，另加下列）",
        # 整类：继承色弱整类（医学/化学/生物等）
        "classes": ["0703", "0813", "1007", "0710", "0831", "0709"] + _MEDICAL_CLASSES,
        # 原文在色弱全部专业基础上追加的具体专业：
        "majors_extra": [
            "美术学", "绘画", "艺术设计", "摄影", "动画", "博物馆学", "应用物理学",
            "天文学", "地理科学", "应用气象学", "材料物理", "矿物加工工程",
            "资源勘探工程", "冶金工程", "无机非金属材料工程", "交通运输", "油气储运工程",
        ],
        # majors 在运行时 = 色弱.majors + majors_extra（见 _resolve）
    },
    # ── 裸眼视力 ──
    "视力低于5.0": {
        "level": "forbid",
        "desc": "任何一眼裸眼视力低于 5.0（标准对数视力表）不能录取的专业",
        "classes": [],
        "majors": ["飞行技术", "航海技术", "消防工程", "刑事科学技术", "侦察学"],
    },
    "视力低于4.8": {
        "level": "forbid",
        "desc": "任何一眼裸眼视力低于 4.8 不能录取的专业",
        "classes": [],
        "majors": ["轮机工程", "运动训练", "民族传统体育"],
    },
    # ── 嗅觉迟钝（不宜就读，医学类不能录取）──
    "嗅觉迟钝": {
        "level": "not_advised",
        "desc": "嗅觉迟钝不宜就读的专业（医学类专业不能录取）",
        "classes": ["0401", "0306"],  # 教育学类 / 公安学类
        "majors": ["外交学", "法学", "新闻学", "音乐表演", "表演"],
        "forbid_majors": ["医学类"],  # 原文：医学类专业不能录取
    },
    # ── 听力（不宜就读）──
    "听力受限": {
        "level": "not_advised",
        "desc": "两耳听力受限不宜就读的专业",
        "classes": [],
        "majors": [
            "法学", "外国语言文学", "外交学", "新闻学", "侦察学", "学前教育",
            "音乐学", "录音艺术", "土木工程", "交通运输", "动物科学", "动物医学",
        ],
        "forbid_majors": ["医学类"],
    },
    # ── 口吃（不宜就读）──
    "口吃": {
        "level": "not_advised",
        "desc": "口吃不宜就读的专业",
        "classes": ["0401", "0306"],
        "majors": ["外交学", "法学", "新闻学", "音乐表演", "表演"],
        "forbid_majors": ["医学类"],
    },
}


def _majors_of(condition: str) -> list[str]:
    rule = RULES[condition]
    if condition == "色盲":
        return RULES["色弱"]["majors"] + rule.get("majors_extra", [])
    return list(rule.get("majors", []))


def restricted_classes(condition: str) -> list[str]:
    """该体检条件下「整类受限」的专业类 4 位码（原文写明「X类各专业」的部分）。"""
    return list(RULES.get(condition, {}).get("classes", []))


def restricted_majors(condition: str) -> list[str]:
    """该体检条件下按**具体专业名**受限的清单（原文逐字，交数据层按名/代码匹配）。"""
    return _majors_of(condition)


def conditions_for(color_vision: str = "正常", vision: float | None = None) -> list[str]:
    """根据考生体检结果，返回命中的限制条件 key 列表。"""
    hits: list[str] = []
    if color_vision == "色盲":
        hits.append("色盲")
    elif color_vision == "色弱":
        hits.append("色弱")
    if vision is not None and vision > 0:
        if vision < 4.8:
            hits.append("视力低于5.0")
            hits.append("视力低于4.8")
        elif vision < 5.0:
            hits.append("视力低于5.0")
    return hits
