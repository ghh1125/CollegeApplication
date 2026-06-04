"""Shanghai-specific province configuration.

上海 = 江苏的「院校专业组」模型 + 浙江的「3+3 单一位次池」：
  - 志愿单位：院校专业组（本科普通批 24 个平行志愿，每组 4 个专业 + 是否服从调剂）
  - 选科：3+3，从 物理/化学/生物/政治/历史/地理 任选 3 门，**不分物理类/历史类、无首选科目**
  - 单一投档位次池（subject_category 恒为「综合」）
"""

from src.common.input.llm import ProvinceConfig

# 上海近三年；与浙江/江苏一致，但定档只用最新一年（专业组逐年重排，跨年不可比）
SHANGHAI_YEAR_WEIGHTS = {2025: 0.5, 2024: 0.3, 2023: 0.2}

# 上海单一投档池的科类标识（占位，便于复用按科类过滤的通用逻辑）
SHANGHAI_CATEGORY = "综合"

PROVINCE_CONFIG = ProvinceConfig(
    volunteer_system="平行志愿，本科普通批设 24 个院校专业组，每组 4 个专业志愿 + 是否服从调剂",
    subject_system="3+3（语文/数学/外语 + 从 物理/化学/生物/政治/历史/地理 任选 3 门，不分文理）",
    total_volunteers=24,
    # 上海 24 个院校专业组的冲稳保分配
    risk_allocation={
        "激进": {"冲": 10, "稳": 8, "保": 5, "垫": 1},
        "均衡": {"冲": 6, "稳": 9, "保": 6, "垫": 3},
        "保守": {"冲": 4, "稳": 7, "保": 9, "垫": 4},
    },
    volunteer_unit="院校专业组",
    subject_collect_hint="选考 3 门（从 物理/化学/生物/政治/历史/地理 中选 3 门，不分文理）",
    json_example=(
        '{"rank":..., "total_score":..., "selected_subjects":[选考3门], '
        '"preferred_majors":[...], "preferred_cities":[...], '
        '"main_priority":"专业优先/学校优先/城市优先", "risk_preference":"激进/均衡/保守"}'
    ),
)
