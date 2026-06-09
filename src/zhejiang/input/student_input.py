"""浙江考生输入模型（重构版 · 仅输入层）。

只负责「用户输入」这一层：定义字段、可选项、取值范围、校验。
不含筛选 / 排序 / 推荐逻辑——那些后续步骤再接。页面只读这里的选项常量渲染表单。

字段（按产品需求）：
  1. 位次（省内排名）
  2. 高考分数
  3. 选科：7 选 3（政治/历史/地理/物理/化学/生物/技术）
  4. 一级学科：支持多选（12 门类 + 交叉学科）
  5. 经济预算：不考虑 / <1万 / 1~5万 / >5万（每年学费）
  6. 地域偏好：无偏好 / 有偏好（全国省级，按优先级多选）
  7. 体检结果：身高、色觉、视力
  8. 单科成绩：语文、数学、外语

体检 / 单科目前只收集，硬约束校验数据待补（见 docstring 末尾 TODO）。
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from src.zhejiang.input.disciplines import MAJOR_CLASS_NAMES, classes_grouped

# ─── 选项常量（页面渲染表单直接用，逻辑留在 src）──────────────────────────────

# 选科 7 选 3（浙江）。"政治" 规范名为 "思想政治"，此处沿用产品口径"政治"。
SUBJECTS_7 = ["政治", "历史", "地理", "物理", "化学", "生物", "技术"]
_SUBJECT_ALIASES = {"思想政治": "政治", "思政": "政治", "生物学": "生物", "信息技术": "技术", "通用技术": "技术"}

# 学科多选：选到「专业类」（二级，4 位代码），按门类分组供页面级联多选。
# {门类名: [(专业类码, 专业类名), ...]}
MAJOR_CLASSES_GROUPED = classes_grouped()

# 全国省级行政区（地域偏好按优先级多选用）
PROVINCES = [
    "北京", "天津", "河北", "山西", "内蒙古", "辽宁", "吉林", "黑龙江",
    "上海", "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南",
    "湖北", "湖南", "广东", "广西", "海南", "重庆", "四川", "贵州",
    "云南", "西藏", "陕西", "甘肃", "青海", "宁夏", "新疆",
    "香港", "澳门", "台湾",
]

COLOR_VISION_OPTIONS = ["正常", "色弱", "色盲"]


class Budget(str, Enum):
    """经济预算（每年学费）。"""
    ANY = "不考虑"
    UNDER_1W = "<1万/年"
    W1_TO_5 = "1~5万/年"
    OVER_5W = ">5万/年"


# ─── 子结构 ──────────────────────────────────────────────────────────────────

class RegionPreference(BaseModel):
    """地域偏好：无偏好，或有偏好 + 按优先级排序的省份列表。"""
    has_preference: bool = False
    provinces: list[str] = Field(default_factory=list)  # 顺序即优先级（前 > 后）

    @field_validator("provinces")
    @classmethod
    def _valid_provinces(cls, v: list[str]) -> list[str]:
        # 去重保序 + 必须是合法省份
        seen: set[str] = set()
        out: list[str] = []
        for p in v:
            p = p.strip()
            if p and p in PROVINCES and p not in seen:
                seen.add(p)
                out.append(p)
        return out

    @model_validator(mode="after")
    def _check(self) -> "RegionPreference":
        if self.has_preference and not self.provinces:
            raise ValueError("选择「有地域偏好」时，至少要按优先级选 1 个省份")
        if not self.has_preference:
            self.provinces = []
        return self


class MedicalExam(BaseModel):
    """体检结果（用于体检受限专业的一票否决，数据待补）。"""
    height_cm: int | None = None                                  # 身高 cm
    color_vision: Literal["正常", "色弱", "色盲"] = "正常"          # 色觉
    naked_eye_vision: float | None = None                         # 裸眼视力（较差眼，如 4.8 / 0.6）

    @field_validator("height_cm")
    @classmethod
    def _height(cls, v: int | None) -> int | None:
        if v is not None:
            assert 100 <= v <= 250, "身高需在 100~250cm"
        return v

    @field_validator("naked_eye_vision")
    @classmethod
    def _vision(cls, v: float | None) -> float | None:
        if v is not None:
            assert 0 <= v <= 5.3, "裸眼视力数值不合理"
        return v


class SubjectScores(BaseModel):
    """单科成绩（用于单科要求的一票否决，数据待补）。浙江主科满分 150。"""
    chinese: int | None = None    # 语文
    math: int | None = None       # 数学
    foreign: int | None = None    # 外语

    @field_validator("chinese", "math", "foreign")
    @classmethod
    def _score(cls, v: int | None) -> int | None:
        if v is not None:
            assert 0 <= v <= 150, "单科成绩需在 0~150"
        return v


# ─── 主输入模型 ──────────────────────────────────────────────────────────────

class StudentInput(BaseModel):
    """浙江考生完整输入。"""
    rank: int                                       # 1. 位次（省内排名）
    total_score: int                                # 2. 高考分数（浙江满分 750）
    selected_subjects: list[str]                    # 3. 选科 7选3
    major_classes: list[str] = Field(default_factory=list)  # 4. 学科多选（专业类 4 位码）
    budget: Budget = Budget.ANY                     # 5. 经济预算
    region: RegionPreference = RegionPreference()   # 6. 地域偏好
    medical: MedicalExam = MedicalExam()            # 7. 体检
    subject_scores: SubjectScores = SubjectScores() # 8. 单科成绩

    @field_validator("rank")
    @classmethod
    def _rank(cls, v: int) -> int:
        assert 1 <= v <= 400000, f"位次 {v} 不合理（1~400000）"
        return v

    @field_validator("total_score")
    @classmethod
    def _score(cls, v: int) -> int:
        assert 0 <= v <= 750, f"高考分数 {v} 不合理（0~750）"
        return v

    @field_validator("selected_subjects")
    @classmethod
    def _subjects(cls, v: list[str]) -> list[str]:
        norm = [_SUBJECT_ALIASES.get(s.strip(), s.strip()) for s in v]
        assert len(norm) == 3, "选科必须恰好 3 门"
        assert len(set(norm)) == 3, "选科不能重复"
        invalid = set(norm) - set(SUBJECTS_7)
        assert not invalid, f"非法选科：{invalid}"
        return norm

    @field_validator("major_classes")
    @classmethod
    def _major_classes(cls, v: list[str]) -> list[str]:
        # 只保留合法的 4 位专业类码，去重保序
        seen: set[str] = set()
        out: list[str] = []
        for code in v:
            code = str(code).strip()
            if code in MAJOR_CLASS_NAMES and code not in seen:
                seen.add(code)
                out.append(code)
        return out


# TODO（数据待补后启用硬约束）：
#   - 体检受限：各专业对 色觉/视力/身高 的限制（《普通高校招生体检工作指导意见》+ 院校章程）
#   - 单科要求：部分专业/学校对 语文/数学/外语 单科最低分要求
#   目前 medical / subject_scores 仅收集存储，不参与筛选。
