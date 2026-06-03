"""Jiangsu-specific province configuration (3+1+2 院校专业组)."""

from src.common.input.llm import ProvinceConfig

# 江苏新高考近三年，权重偏向近年（与浙江一致）。
JIANGSU_YEAR_WEIGHTS = {2025: 0.5, 2024: 0.3, 2023: 0.2}

PROVINCE_CONFIG = ProvinceConfig(
    volunteer_system="平行志愿，本科批最多填 40 个院校专业组，每组最多 6 个专业 + 服从调剂",
    subject_system="3+1+2（首选物理或历史；再选从化学/生物/思想政治/地理中选 2 门）",
    total_volunteers=40,
    # 江苏 40 个院校专业组的冲稳保分配
    risk_allocation={
        "激进": {"冲": 16, "稳": 14, "保": 8, "垫": 2},
        "均衡": {"冲": 10, "稳": 15, "保": 10, "垫": 5},
        "保守": {"冲": 6, "稳": 12, "保": 15, "垫": 7},
    },
)
