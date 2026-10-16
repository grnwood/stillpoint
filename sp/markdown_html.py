"""Shared Markdown-to-HTML rendering for print and in-app reading views."""
from __future__ import annotations

import html
import re

import markdown as md


_IMAGE_MD_SIZE_RE = re.compile(
    r'!\[(?P<alt>[^\]]*)\]\((?P<path>[^)\s]+)(?:\s+"(?P<title>[^"]*)")?\)\{width=(?P<width>\d+)\}',
    re.MULTILINE,
)
_IMAGE_MD_ANY_RE = re.compile(
    r'!\[[^\]]*\]\([^)\s]+(?:\s+"[^"]*")?\)(?:\{width=\d+\})?',
    re.MULTILINE,
)
_ZIM_LINK_RE = re.compile(r"\[(?P<target>[^\]|]+)\|(?P<label>[^\]]*)\]")
_SOURCE_ANCHOR_BLOCK_RE = re.compile(
    r'<p><span id="(?P<anchor>sp-source-\d+)">&#8203;</span></p>\s*'
)


def source_block_lines(text: str) -> list[int]:
    """Return one-based source lines that begin a readable Markdown block."""
    starts: list[int] = []
    previous_blank = True
    in_fence = False
    fence_marker = ""
    for index, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            previous_blank = True
            continue
        if stripped.startswith(("```", "~~~")):
            marker = stripped[:3]
            if not in_fence:
                in_fence = True
                fence_marker = marker
            elif marker == fence_marker:
                in_fence = False
                fence_marker = ""
        if previous_blank:
            starts.append(index)
        previous_blank = False
    return starts or ([1] if text else [])


def nearest_source_anchor(text: str, source_line: int) -> str:
    """Return the block anchor at or immediately before ``source_line``."""
    starts = source_block_lines(text)
    if not starts:
        return ""
    target = max(1, int(source_line))
    nearest = starts[0]
    for line in starts:
        if line > target:
            break
        nearest = line
    return f"sp-source-{nearest}"


def _add_source_anchors(text: str) -> str:
    starts = set(source_block_lines(text))
    if not starts:
        return text
    output: list[str] = []
    for index, line in enumerate(text.splitlines(), start=1):
        if index in starts:
            # A zero-width character keeps the anchor alive in QTextDocument.
            # Qt can discard a truly empty span, notably before image blocks.
            output.extend((f'<span id="sp-source-{index}">&#8203;</span>', ""))
        output.append(line)
    return "\n".join(output)


def render_markdown_html(text: str, *, source_anchors: bool = False) -> str:
    """Render Stillpoint Markdown consistently for print and reading views."""
    normalized = _add_source_anchors(text) if source_anchors else text
    normalized = _rewrite_zim_links(normalized)
    normalized = _normalize_markdown_image_blocks(normalized)
    normalized = _rewrite_markdown_image_sizes(normalized)
    normalized = _rewrite_task_and_dash_markers(normalized)
    normalized = _normalize_markdown_lists(normalized)
    normalized = _rewrite_highlight(normalized)
    normalized = _rewrite_strikethrough(normalized)
    renderer = md.Markdown(extensions=["fenced_code", "tables", "nl2br"])
    rendered = renderer.convert(normalized)
    if source_anchors:
        rendered = _inline_source_anchors(rendered)
    return _rewrite_task_checkboxes(rendered)


def _inline_source_anchors(rendered: str) -> str:
    """Attach source anchors to content without creating empty layout rows."""
    output: list[str] = []
    cursor = 0
    while True:
        marker = _SOURCE_ANCHOR_BLOCK_RE.search(rendered, cursor)
        if marker is None:
            output.append(rendered[cursor:])
            break
        output.append(rendered[cursor:marker.start()])
        content_start = marker.end()
        opening = re.match(r"<(?P<tag>[a-zA-Z0-9]+)\b[^>]*>", rendered[content_start:])
        if opening is None:
            output.append(rendered[marker.start():marker.end()])
            cursor = marker.end()
            continue
        tag = opening.group("tag").lower()
        opening_text = opening.group(0)
        if opening_text.rstrip().endswith("/>"):
            anchored_opening = re.sub(
                r"\s*/>$",
                f' id="{marker.group("anchor")}" />',
                opening_text,
            )
            output.append(anchored_opening)
            cursor = content_start + opening.end()
            continue
        relative_insertion = opening.end()
        if tag == "table":
            target = re.search(r"<(?:th|td)\b[^>]*>", rendered[content_start:], re.DOTALL)
            if target is not None:
                relative_insertion = target.end()
        elif tag in {"ul", "ol"}:
            target = re.search(r"<li\b[^>]*>", rendered[content_start:], re.DOTALL)
            if target is not None:
                relative_insertion = target.end()
        elif tag == "blockquote":
            target = re.search(r"<(?:p|h[1-6])\b[^>]*>", rendered[content_start:], re.DOTALL)
            if target is not None:
                relative_insertion = target.end()
        elif tag == "pre":
            target = re.search(r"<code\b[^>]*>", rendered[content_start:], re.DOTALL)
            if target is not None:
                relative_insertion = target.end()
        insertion = content_start + relative_insertion
        output.append(rendered[content_start:insertion])
        output.append(f'<span id="{marker.group("anchor")}">&#8203;</span>')
        cursor = insertion
    return "".join(output)


def _rewrite_task_and_dash_markers(text: str) -> str:
    lines = text.splitlines()
    normalized: list[str] = []
    in_fence = False
    fence_marker = ""
    checkbox_re = re.compile(
        r"^(?P<indent>[ \t]*)(?:(?:[-+*]|\d+\.)[ \t]+)?"
        r"\[(?P<state>[ xX])\][ \t]+(?P<rest>.*)$"
    )
    dash_re = re.compile(r"^(?P<indent>[ \t]*)-[ \t]+(?P<rest>.*)$")
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            marker = stripped[:3]
            if not in_fence:
                in_fence = True
                fence_marker = marker
            elif marker == fence_marker:
                in_fence = False
                fence_marker = ""
            normalized.append(line)
            continue
        if in_fence:
            normalized.append(line)
            continue
        checkbox_match = checkbox_re.match(line)
        if checkbox_match:
            checked = (checkbox_match.group("state") or " ").lower() == "x"
            cls = "md-checkbox md-checkbox--checked" if checked else "md-checkbox md-checkbox--unchecked"
            normalized.append(
                f'{checkbox_match.group("indent") or ""}<span class="{cls}" '
                f'aria-hidden="true"></span>{checkbox_match.group("rest") or ""}'
            )
            continue
        dash_match = dash_re.match(line)
        if dash_match:
            normalized.append(
                f'{dash_match.group("indent") or ""}\\- {dash_match.group("rest") or ""}'
            )
            continue
        normalized.append(line)
    return "\n".join(normalized)


def _rewrite_task_checkboxes(html_text: str) -> str:
    pattern = re.compile(r"(<li>\s*)\[(?P<state>[ xX])\]\s+", re.IGNORECASE)

    def replace(match: re.Match) -> str:
        checked = (match.group("state") or " ").lower() == "x"
        cls = "md-checkbox md-checkbox--checked" if checked else "md-checkbox md-checkbox--unchecked"
        return f'{match.group(1)}<span class="{cls}" aria-hidden="true"></span>'

    return pattern.sub(replace, html_text)


def _normalize_markdown_image_blocks(text: str) -> str:
    lines = text.splitlines()
    normalized: list[str] = []
    in_fence = False
    fence_marker = ""
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            marker = stripped[:3]
            if not in_fence:
                in_fence = True
                fence_marker = marker
            elif marker == fence_marker:
                in_fence = False
                fence_marker = ""
            normalized.append(line)
            continue
        if in_fence:
            normalized.append(line)
            continue
        image_only = bool(stripped) and not _IMAGE_MD_ANY_RE.sub("", stripped).strip()
        normalized.append(stripped if image_only else line)
    return "\n".join(normalized)


def _normalize_markdown_lists(text: str) -> str:
    def is_list_line(value: str) -> bool:
        trimmed = value.lstrip()
        if trimmed.startswith(("* ", "- ", "+ ")):
            return True
        # Stillpoint's editor permits italicized list rows such as
        # ``    *- item text*``.  Treat their dash as the list marker when
        # removing accidental code-block indentation for HTML rendering.
        if trimmed.startswith(("*- ", "_- ")):
            return True
        digits = ""
        for character in trimmed:
            if not character.isdigit():
                break
            digits += character
        return bool(digits) and trimmed[len(digits):].startswith(". ")

    def indent_columns(value: str) -> int:
        columns = 0
        for character in value:
            if character == " ":
                columns += 1
            elif character == "\t":
                columns += 4
            else:
                break
        return columns

    def strip_indent(value: str, columns: int) -> str:
        remaining = columns
        index = 0
        while index < len(value) and remaining > 0:
            if value[index] == " ":
                remaining -= 1
            elif value[index] == "\t":
                remaining -= 4
            else:
                break
            index += 1
        return value[index:]

    lines = text.splitlines()
    normalized: list[str] = []
    in_fence = False
    fence_marker = ""
    deindent_active = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            marker = stripped[:3]
            if not in_fence:
                in_fence = True
                fence_marker = marker
            elif marker == fence_marker:
                in_fence = False
                fence_marker = ""
            normalized.append(line)
            continue
        if in_fence or not stripped:
            normalized.append(line)
            continue
        if not deindent_active and is_list_line(line) and indent_columns(line) >= 4:
            deindent_active = True
        if is_list_line(line):
            if normalized and normalized[-1].strip() and not is_list_line(normalized[-1].strip()):
                normalized.append("")
            normalized.append(strip_indent(line, 4) if deindent_active else line)
            continue
        deindent_active = False
        normalized.append(line)
    return "\n".join(normalized)


def _rewrite_outside_code(text: str, pattern: str, replacement: str) -> str:
    normalized: list[str] = []
    in_fence = False
    fence_marker = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            marker = stripped[:3]
            if not in_fence:
                in_fence = True
                fence_marker = marker
            elif marker == fence_marker:
                in_fence = False
                fence_marker = ""
            normalized.append(line)
            continue
        if in_fence:
            normalized.append(line)
            continue
        parts = line.split("`")
        for index in range(0, len(parts), 2):
            parts[index] = re.sub(pattern, replacement, parts[index])
        normalized.append("`".join(parts))
    return "\n".join(normalized)


def _rewrite_strikethrough(text: str) -> str:
    return _rewrite_outside_code(text, r"~~(.*?)~~", r"<del>\1</del>")


def _rewrite_highlight(text: str) -> str:
    return _rewrite_outside_code(text, r"==(.+?)==", r"<mark>\1</mark>")


def _rewrite_markdown_image_sizes(text: str) -> str:
    def replace(match: re.Match) -> str:
        alt = html.escape(match.group("alt") or "", quote=True)
        path = html.escape(match.group("path") or "", quote=True)
        title = match.group("title")
        title_attr = f' title="{html.escape(title, quote=True)}"' if title else ""
        width = match.group("width")
        return f'<img src="{path}" alt="{alt}" style="width: {width}px;"{title_attr} />'

    return _IMAGE_MD_SIZE_RE.sub(replace, text)


def _rewrite_zim_links(text: str) -> str:
    def replace(match: re.Match) -> str:
        target = (match.group("target") or "").strip()
        label = (match.group("label") or "").strip()
        if target.startswith(("http://", "https://")):
            return f"[{label or target}]({target})"
        return label or target.lstrip(":")

    return _ZIM_LINK_RE.sub(replace, text)
