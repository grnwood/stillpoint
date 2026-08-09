from __future__ import annotations

import re
from typing import Optional


QUICK_CAPTURE_SECTION_TITLE = "## QuickCaptures"
QUICK_CAPTURE_ATTACHMENT_PLACEHOLDER_RE = re.compile(
    r"<(?:clipboard-Image|file-(?:Image|Attachment))-[^>]+>"
)


def format_attachment_link(name: str, width: Optional[int] = None, *, is_image: bool = True) -> str:
    if is_image:
        if width and width > 600:
            return f"![](./{name}){{width=600}}"
        return f"![](./{name})"
    return f"[{name}](./{name})"


def resolve_attachment_placeholders(
    text: str,
    attachments: Optional[list[dict]] = None,
) -> tuple[str, list[str]]:
    resolved = text
    appended: list[str] = []
    for entry in attachments or []:
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        link = format_attachment_link(
            name,
            entry.get("width"),
            is_image=bool(entry.get("is_image", entry.get("width") is not None)),
        )
        placeholder = str(entry.get("placeholder") or "").strip()
        if placeholder and placeholder in resolved:
            resolved = resolved.replace(placeholder, link)
        else:
            appended.append(f"  {link}")
    resolved = QUICK_CAPTURE_ATTACHMENT_PLACEHOLDER_RE.sub("", resolved)
    return resolved, appended
