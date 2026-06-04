"""Shanghai student profile (3+3 model, 院校专业组 volunteer unit).

Differs from Jiangsu (3+1+2):
  - 6 选 3：从 物理/化学/生物/政治/历史/地理 任选 3 门（无"技术"，无首选科目，不分文理）
  - 单一投档位次池：subject_category 恒为「综合」
Differs from Zhejiang (7选3, 学校+专业): 志愿单位是院校专业组（见 allocation）。

别名归一：上海官方常用「生命科学」=生物、「思想政治」=政治。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, field_validator, model_validator

from src.shanghai.config import SHANGHAI_CATEGORY

# 上海选考 6 选 3（规范名）
SELECT_SUBJECTS = {"物理", "化学", "生物", "思想政治", "历史", "地理"}

# 上海口径别名 → 规范名
_SUBJECT_ALIASES: dict[str, str] = {
    "生命科学": "生物",
    "政治": "思想政治",
}

PRIORITY_MODES: dict[str, dict[str, float]] = {
    "专业优先": {"major": 0.50, "school": 0.25, "city": 0.25},
    "学校优先": {"major": 0.25, "school": 0.50, "city": 0.25},
    "城市优先": {"major": 0.25, "school": 0.25, "city": 0.50},
    "均衡模式": {"major": 0.34, "school": 0.33, "city": 0.33},
}


def normalize_subject(s: str) -> str:
    """生命科学→生物、政治→思想政治。"""
    return _SUBJECT_ALIASES.get(s.strip(), s.strip())


class CityPreference(BaseModel):
    preferred: list[str] = []
    excluded_regions: list[str] = []
    accept_outside_shanghai: bool = True


class MajorPreference(BaseModel):
    preferred_categories: list[str] = []
    preferred_majors: list[str] = []
    excluded_majors: list[str] = []


class SchoolPreference(BaseModel):
    preferred_schools: list[str] = []
    preferred_levels: list[str] = []  # 双一流/985/211
    excluded_schools: list[str] = []


class Preferences(BaseModel):
    cities: CityPreference = CityPreference()
    majors: MajorPreference = MajorPreference()
    schools: SchoolPreference = SchoolPreference()


class Constraints(BaseModel):
    accept_private: bool = True
    accept_sino_foreign: bool = False
    max_tuition: int | None = None
    only_public_undergraduate: bool = False
    foreign_language: str = "英语"
    has_physical_exam_limit: bool = False


class StudentProfile(BaseModel):
    rank: int                                   # 全市位次（单一池）
    total_score: int | None = None
    selected_subjects: list[str]                # 选考 3 门

    preferences: Preferences = Preferences()
    constraints: Constraints = Constraints()

    priority_mode: Literal["专业优先", "学校优先", "城市优先", "均衡模式"] = "均衡模式"
    priority_weights: dict[str, float] | None = None
    risk_preference: Literal["激进", "均衡", "保守"] = "均衡"

    @property
    def subject_category(self) -> str:
        """上海单一投档池，恒为「综合」。"""
        return SHANGHAI_CATEGORY

    @field_validator("rank")
    @classmethod
    def rank_valid(cls, v: int) -> int:
        assert 1 <= v <= 200000, f"位次 {v} 不合理"
        return v

    @field_validator("selected_subjects")
    @classmethod
    def subjects_valid(cls, v: list[str]) -> list[str]:
        normalized = [normalize_subject(s) for s in v]
        assert len(normalized) == 3, "选考科目必须恰好 3 门"
        assert len(set(normalized)) == 3, "选考科目不能重复"
        invalid = set(normalized) - SELECT_SUBJECTS
        assert not invalid, f"选考科目只能来自 物理/化学/生物/思想政治/历史/地理，非法：{invalid}"
        return normalized

    @model_validator(mode="after")
    def resolve_weights(self) -> "StudentProfile":
        if self.priority_weights is not None:
            total = sum(self.priority_weights.values())
            assert abs(total - 1.0) < 0.01, "自定义权重之和必须等于1"
        else:
            self.priority_weights = PRIORITY_MODES[self.priority_mode]
        return self

    def get_weights(self) -> dict[str, float]:
        return self.priority_weights  # type: ignore[return-value]
