"""Zhejiang-specific province configuration for LLM prompts."""

from src.common.input.llm import ProvinceConfig

PROVINCE_CONFIG = ProvinceConfig(
    volunteer_system="平行志愿，最多可填 80 个专业（含学校）",
    subject_system="7 选 3（物理/化学/生物/历史/地理/思想政治/技术）",
    region_expansions={
        "长三角": ["上海", "杭州", "南京", "苏州", "宁波", "合肥"],
        "珠三角": ["广州", "深圳", "佛山", "东莞"],
        "京津冀": ["北京", "天津"],
        "成渝":   ["成都", "重庆"],
        "中部/长江中游": ["武汉", "长沙", "南昌", "郑州"],
    },
)
