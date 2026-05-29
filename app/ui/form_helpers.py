"""Pure helpers for Streamlit form inputs."""

from __future__ import annotations

import re
from collections.abc import MutableMapping
from typing import Any


def normalize_items(values: list[str]) -> list[str]:
    """Clean user-entered list values, splitting accidental comma input."""

    items: list[str] = []
    seen: set[str] = set()
    for value in values:
        for part in re.split(r"[,，]", str(value or "")):
            item = re.sub(r"\s+", " ", part.strip())
            if not item or item in seen:
                continue
            items.append(item)
            seen.add(item)
    return items


def split_major_preferences(values: list[str]) -> tuple[list[str], list[str]]:
    """Split preferred majors into concrete majors and broad major categories."""

    items = normalize_items(values)
    categories = [item for item in items if item.endswith("类")]
    majors = [item for item in items if item not in categories]
    return majors, categories


def queue_ai_message(
    state: MutableMapping[str, Any],
    message: str,
    input_index: int,
) -> bool:
    """Queue a chat message and advance the dynamic input key so the box clears."""

    text = str(message or "").strip()
    if not text:
        return False
    state["_ai_pending_msg"] = text
    state["_ai_input_n"] = input_index + 1
    return True


def format_sort_reason_for_display(program: dict, main_priority: str) -> str:
    """Return the backend sort reason, or build a compact UI fallback."""

    reason = str(program.get("sort_reason") or "").strip()
    if reason:
        return reason

    gap_info = program.get("gap_info") or {}
    tier = gap_info.get("tier") or "数据不足"
    details: list[str] = []

    discipline_grade = program.get("discipline_grade") or ""
    school_best_grade = program.get("school_best_grade") or ""
    if discipline_grade:
        details.append(f"学科评估{discipline_grade}")
    elif main_priority == "专业优先" and school_best_grade:
        details.append(f"学校最佳学科{school_best_grade}")

    ruanke_rank = program.get("ruanke_rank")
    if ruanke_rank:
        details.append(f"软科第{ruanke_rank}")
    elif program.get("is_985"):
        details.append("985")
    elif program.get("is_211"):
        details.append("211")
    elif program.get("is_double_first_class"):
        details.append("双一流")

    city = program.get("school_city") or ""
    if main_priority == "城市优先" and city:
        details.append(city)

    gap = gap_info.get("gap")
    if gap is not None:
        details.append(f"gap {gap}")
    else:
        details.append("历史位次不足")

    return f"{tier}；{main_priority}：" + "；".join(details)
