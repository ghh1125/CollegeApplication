"""Zhejiang-specific province configuration."""

from src.common.input.llm import ProvinceConfig

PROVINCE_CONFIG = ProvinceConfig(
    volunteer_system="平行志愿，最多可填 80 个专业（含学校）",
    subject_system="7 选 3（物理/化学/生物/历史/地理/思想政治/技术）",
    total_volunteers=80,
    risk_allocation={
        "激进": {"冲": 30, "稳": 30, "保": 15, "垫": 5},
        "均衡": {"冲": 20, "稳": 30, "保": 20, "垫": 10},
        "保守": {"冲": 10, "稳": 25, "保": 30, "垫": 15},
    },
)
