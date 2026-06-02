"""Student profile data model used throughout the filtering and ranking pipeline."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, field_validator, model_validator

VALID_SUBJECTS = {"物理", "化学", "生物", "思想政治", "历史", "地理", "技术"}

# 接受 "政治" 作为 "思想政治" 的简写（与 DB 里的规范名统一）
_SUBJECT_ALIASES: dict[str, str] = {"政治": "思想政治"}

PRIORITY_MODES: dict[str, dict[str, float]] = {
    "专业优先": {"major": 0.50, "school": 0.25, "city": 0.25},
    "学校优先": {"major": 0.25, "school": 0.50, "city": 0.25},
    "城市优先": {"major": 0.25, "school": 0.25, "city": 0.50},
    "均衡模式": {"major": 0.34, "school": 0.33, "city": 0.33},
}


class ScoreDetail(BaseModel):
    语文: int
    数学: int
    外语: int
    electives: dict[str, int]  # key 必须在 VALID_SUBJECTS 里

    @field_validator("语文", "数学", "外语")
    @classmethod
    def score_range(cls, v: int) -> int:
        assert 0 <= v <= 150, "主科分数必须在 0-150 之间"
        return v

    @field_validator("electives")
    @classmethod
    def electives_valid(cls, v: dict[str, int]) -> dict[str, int]:
        normalized = {_SUBJECT_ALIASES.get(k, k): score for k, score in v.items()}
        invalid = set(normalized) - VALID_SUBJECTS
        assert not invalid, f"electives 包含无效科目：{invalid}"
        for subject, score in normalized.items():
            assert 0 <= score <= 100, f"{subject} 分数必须在 0-100 之间"
        return normalized


class CityPreference(BaseModel):
    preferred: list[str] = []
    excluded_regions: list[str] = []
    accept_outside_zhejiang: bool = True


class MajorPreference(BaseModel):
    preferred_categories: list[str] = []
    preferred_majors: list[str] = []
    excluded_majors: list[str] = []


class SchoolPreference(BaseModel):
    preferred_schools: list[str] = []
    preferred_levels: list[str] = []  # 双一流/985/211/省重点/公办本科
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
    rank: int
    total_score: int | None = None
    selected_subjects: list[str]
    scores: ScoreDetail | None = None

    preferences: Preferences = Preferences()
    constraints: Constraints = Constraints()

    priority_mode: Literal["专业优先", "学校优先", "城市优先", "均衡模式"] = "均衡模式"
    priority_weights: dict[str, float] | None = None

    risk_preference: Literal["激进", "均衡", "保守"] = "均衡"

    @field_validator("rank")
    @classmethod
    def rank_valid(cls, v: int) -> int:
        assert 1 <= v <= 400000, f"位次 {v} 不合理"
        return v

    @field_validator("selected_subjects")
    @classmethod
    def subjects_valid(cls, v: list[str]) -> list[str]:
        normalized = [_SUBJECT_ALIASES.get(s, s) for s in v]
        assert len(normalized) == 3, "必须选择恰好3门选考科目"
        assert len(set(normalized)) == 3, "选考科目不能重复"
        invalid = set(normalized) - VALID_SUBJECTS
        assert not invalid, f"包含无效科目：{invalid}"
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
