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
    from src.ranking.profiles import build_profile_sort_reason

    return build_profile_sort_reason(program, main_priority)
