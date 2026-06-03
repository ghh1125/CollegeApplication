"""Jiangsu student profile (3+1+2 model).

Differs from Zhejiang (7选3):
  - first_choice: 物理 或 历史 (1 门首选，决定录取科类/位次池)
  - selected_subjects: 再选 2 门，来自 {化学, 生物, 思想政治, 地理}（无"技术"）
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, field_validator, model_validator

FIRST_CHOICE_SUBJECTS = {"物理", "历史"}
RESELECT_SUBJECTS = {"化学", "生物", "思想政治", "地理"}

_SUBJECT_ALIASES: dict[str, str] = {"政治": "思想政治"}

# 首选科目 → 录取科类（江苏分两个独立位次池）
FIRST_CHOICE_TO_CATEGORY = {"物理": "物理类", "历史": "历史类"}

PRIORITY_MODES: dict[str, dict[str, float]] = {
    "专业优先": {"major": 0.50, "school": 0.25, "city": 0.25},
    "学校优先": {"major": 0.25, "school": 0.50, "city": 0.25},
    "城市优先": {"major": 0.25, "school": 0.25, "city": 0.50},
    "均衡模式": {"major": 0.34, "school": 0.33, "city": 0.33},
}


class CityPreference(BaseModel):
    preferred: list[str] = []
    excluded_regions: list[str] = []
    accept_outside_jiangsu: bool = True


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
    rank: int                                   # 首选科类内的位次
    total_score: int | None = None
    first_choice: Literal["物理", "历史"]        # 3+1+2 的"1"
    selected_subjects: list[str]                # 再选 2 门

    preferences: Preferences = Preferences()
    constraints: Constraints = Constraints()

    priority_mode: Literal["专业优先", "学校优先", "城市优先", "均衡模式"] = "均衡模式"
    priority_weights: dict[str, float] | None = None
    risk_preference: Literal["激进", "均衡", "保守"] = "均衡"

    @property
    def subject_category(self) -> str:
        """录取科类：物理类 / 历史类（决定查哪个位次池）。"""
        return FIRST_CHOICE_TO_CATEGORY[self.first_choice]

    @field_validator("rank")
    @classmethod
    def rank_valid(cls, v: int) -> int:
        assert 1 <= v <= 400000, f"位次 {v} 不合理"
        return v

    @field_validator("selected_subjects")
    @classmethod
    def subjects_valid(cls, v: list[str]) -> list[str]:
        normalized = [_SUBJECT_ALIASES.get(s, s) for s in v]
        assert len(normalized) == 2, "再选科目必须恰好 2 门"
        assert len(set(normalized)) == 2, "再选科目不能重复"
        invalid = set(normalized) - RESELECT_SUBJECTS
        assert not invalid, f"再选科目只能来自 化学/生物/思想政治/地理，非法：{invalid}"
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
