from __future__ import annotations

import re

_NON_WORD = re.compile(r"[^\w\s-]")
_SPACES = re.compile(r"[\s_]+")


def heading_slug(text: str) -> str:
    """Return a stable anchor slug for a heading title."""
    cleaned = _NON_WORD.sub("", (text or "").strip().lower())
    slug = _SPACES.sub("-", cleaned).strip("-")
    return slug or "heading"
