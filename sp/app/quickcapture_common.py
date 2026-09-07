from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional


DEFAULT_QUICK_CAPTURE_HEADER = "QuickCaptures"
QUICK_CAPTURE_SECTION_TITLE = f"## {DEFAULT_QUICK_CAPTURE_HEADER}"
QUICK_CAPTURE_ATTACHMENT_PLACEHOLDER_RE = re.compile(
    r"<(?:clipboard-Image|file-(?:Image|Attachment))-[^>]+>"
)


def quick_capture_destination_label(page_ref: str) -> str:
    """Return the compact page label used by every Quick Capture launcher."""
    cleaned = str(page_ref or "").strip()
    if not cleaned:
        return "Custom page"
    if cleaned.startswith(":"):
        return " / ".join(part.replace("_", " ") for part in cleaned.split(":") if part)
    normalized = cleaned.replace("\\", "/").rstrip("/")
    name = Path(normalized).stem
    return name.replace("_", " ") or cleaned


def quick_capture_destination_options(
    page_mode: str,
    page_ref: Optional[str],
    history: list[dict[str, Any]],
    *,
    limit: int = 10,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build the configured and recent destinations shared by capture UIs."""
    selected = {
        "label": (
            "Today's Journal"
            if page_mode != "custom"
            else quick_capture_destination_label(str(page_ref or ""))
        ),
        "page_mode": "custom" if page_mode == "custom" else "today",
        "page_ref": page_ref if page_mode == "custom" else None,
    }
    options: list[dict[str, Any]] = [
        {"label": "Today's Journal", "page_mode": "today", "page_ref": None}
    ]
    seen_custom_refs: set[str] = set()

    configured_ref = str(page_ref or "").strip() if page_mode == "custom" else ""
    if configured_ref:
        seen_custom_refs.add(configured_ref)
        options.append(
            {
                "label": quick_capture_destination_label(configured_ref),
                "page_mode": "custom",
                "page_ref": configured_ref,
            }
        )

    for entry in history:
        recent_ref = str(entry.get("page_ref") or "").strip()
        if entry.get("page_mode") != "custom" or not recent_ref or recent_ref in seen_custom_refs:
            continue
        seen_custom_refs.add(recent_ref)
        options.append(
            {
                "label": quick_capture_destination_label(recent_ref),
                "page_mode": "custom",
                "page_ref": recent_ref,
            }
        )
        if len(options) >= limit:
            break
    return options, selected


def normalize_quick_capture_header(value: Optional[str]) -> str:
    cleaned = re.sub(r"^#{1,6}\s*", "", str(value or "").strip()).strip()
    return cleaned or DEFAULT_QUICK_CAPTURE_HEADER


def quick_capture_section_title(value: Optional[str] = None) -> str:
    if value is None:
        try:
            from sp.app import config

            value = config.load_quick_capture_header()
        except Exception:
            value = DEFAULT_QUICK_CAPTURE_HEADER
    return f"## {normalize_quick_capture_header(value)}"


def _heading_text(line: str) -> Optional[str]:
    """Extract an h1/h2 title, including old headings accidentally made tasks."""
    candidate = str(line or "").strip()
    candidate = re.sub(r"^(?:[-*+]\s*)?\[[ xX]\]\s*", "", candidate)
    candidate = re.sub(r"^[☐☑✓]\s*", "", candidate)
    match = re.match(r"^#{1,2}\s+(.+?)\s*$", candidate)
    return match.group(1).strip() if match else None


def is_quick_capture_heading(line: str, value: Optional[str] = None) -> bool:
    title = _heading_text(line)
    if title is None:
        return False
    configured = normalize_quick_capture_header(
        quick_capture_section_title(value).removeprefix("## ")
    )
    return title.casefold() in {
        configured.casefold(),
        DEFAULT_QUICK_CAPTURE_HEADER.casefold(),
    }


def quick_capture_section_ranges(
    lines: list[str], value: Optional[str] = None
) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    for start, line in enumerate(lines):
        if not is_quick_capture_heading(line, value):
            continue
        end = len(lines)
        for index in range(start + 1, len(lines)):
            if _heading_text(lines[index]) is not None:
                end = index
                break
        ranges.append((start, end))
    return ranges


def append_quick_capture_section(
    content: str,
    entry_lines: list[str],
    value: Optional[str] = None,
) -> str:
    """Append a capture and consolidate duplicate or malformed capture sections."""
    if not entry_lines:
        return content
    section_title = quick_capture_section_title(value)
    lines = content.splitlines()
    ranges = quick_capture_section_ranges(lines, value)
    if not ranges:
        trimmed = content.rstrip("\n")
        spacer = "\n\n" if trimmed else ""
        return f"{trimmed}{spacer}{section_title}\n" + "\n".join(entry_lines) + "\n"

    bodies: list[str] = []
    for start, end in ranges:
        body = lines[start + 1 : end]
        while body and not body[0].strip():
            body.pop(0)
        while body and not body[-1].strip():
            body.pop()
        if body:
            if bodies and bodies[-1].strip() and body[0].strip():
                bodies.append("")
            bodies.extend(body)
    normalized_bodies: list[str] = []
    for line in bodies:
        if line.strip() == "---":
            last_nonblank = next(
                (prior.strip() for prior in reversed(normalized_bodies) if prior.strip()),
                "",
            )
            if last_nonblank == "---":
                continue
        normalized_bodies.append(line)
    bodies = normalized_bodies
    if bodies and bodies[-1].strip():
        bodies.append("")
    bodies.extend(entry_lines)

    first_start = ranges[0][0]
    skipped: set[int] = set()
    for start, end in ranges:
        skipped.update(range(start, end))
    output: list[str] = []
    for index, line in enumerate(lines):
        if index == first_start:
            output.append(section_title)
            output.extend(bodies)
        if index not in skipped:
            output.append(line)
    result = "\n".join(output)
    return result if result.endswith("\n") else result + "\n"


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
