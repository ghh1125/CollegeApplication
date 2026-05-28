"""Pure helpers for Streamlit form inputs."""

from __future__ import annotations

import re


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
