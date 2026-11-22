"""Theme-independent clipboard content shared by Markdown editors and readers."""
from __future__ import annotations

from dataclasses import dataclass, field
from html import escape
from html.parser import HTMLParser
import re
from urllib.parse import urlsplit

from PySide6.QtCore import QMimeData
from PySide6.QtGui import QTextDocument
import markdown as md

from sp.markdown_html import (
    decode_vault_link_href, EditorEmphasisExtension, _normalize_markdown_lists,
    _rewrite_zim_links, _rewrite_highlight, _rewrite_strikethrough,
)

INTERNAL_MARKDOWN = "application/x-stillpoint-markdown"


def clean_text(text: str) -> str:
    text = text.replace("\u2029", "\n").replace("\u2028", "\n")
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u200b-\u200f\u202a-\u202e\ufeff\ufffc\ue000-\uf8ff]", "", text)


@dataclass
class _Node:
    tag: str
    attrs: dict = field(default_factory=dict)
    children: list = field(default_factory=list)


class _FragmentParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("root")
        self.stack = [self.root]
        self.ignored = []
        self.qt_fragment = False

    def handle_starttag(self, tag, attrs):
        if tag == "meta" and dict(attrs).get("name") == "qrichtext":
            self.qt_fragment = True
        if tag in {"head", "script", "style", "iframe", "object"} or self.ignored:
            self.ignored.append(tag)
            return
        node = _Node(tag, dict(attrs))
        self.stack[-1].children.append(node)
        if tag not in {"br", "hr", "img", "meta", "link", "input", "wbr"}:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if self.stack[-1].tag == tag:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self.ignored:
            if tag in self.ignored:
                self.ignored = self.ignored[:self.ignored.index(tag)]
            return
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                self.stack = self.stack[:index]
                break

    def handle_data(self, data):
        if not self.ignored:
            if data.isspace() and self.stack[-1].tag in {"root", "html", "body", "table", "thead", "tbody", "tfoot", "tr", "ul", "ol"}:
                return
            self.stack[-1].children.append(clean_text(data))


_ALLOWED = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "strong", "em", "u", "s", "sub", "sup", "mark",
            "a", "ul", "ol", "li", "blockquote", "pre", "code", "table", "thead", "tbody", "tfoot", "tr", "th", "td", "br", "hr", "img"}
_ALIASES = {"b": "strong", "i": "em", "strike": "s", "del": "s", "div": "p"}


def _safe_url(value):
    if not value or re.search(r"[\x00-\x20]", value):
        return ""
    try:
        scheme = urlsplit(value).scheme.lower()
    except ValueError:
        return ""
    return value if scheme in {"", "http", "https", "mailto", "tel", "file", "stillpoint-page"} else ""


def _semantic_html(node, *, in_pre=False, in_header=False, qt_fragment=False):
    if isinstance(node, str):
        return escape(node)
    tag = _ALIASES.get(node.tag, node.tag)
    qt_fragment = qt_fragment or bool(node.attrs.get("qt_fragment"))
    children = "".join(_semantic_html(child, in_pre=in_pre or tag == "pre", in_header=in_header or tag == "thead", qt_fragment=qt_fragment) for child in node.children)
    if qt_fragment and tag in {"root", "html", "body"}:
        children = re.sub(r"</pre>\s*<pre>", "\n", children)
    if node.tag == "span":
        style = dict(re.findall(r"([\w-]+)\s*:\s*([^;]+)", node.attrs.get("style", "")))
        weight = style.get("font-weight", "").strip()
        if weight in {"bold", "bolder"} or (weight.isdigit() and int(weight) >= 600):
            children = f"<strong>{children}</strong>"
        if style.get("font-style", "").strip() in {"italic", "oblique"}:
            children = f"<em>{children}</em>"
        decoration = style.get("text-decoration", "")
        if "line-through" in decoration:
            children = f"<s>{children}</s>"
        if "underline" in decoration:
            children = f"<u>{children}</u>"
        family = style.get("font-family", "").lower()
        if not in_pre and any(name in family for name in ("monospace", "menlo", "monaco", "courier", "consolas")):
            children = f"<code>{children}</code>"
        return children
    if tag not in _ALLOWED:
        return children
    attrs = {}
    if tag == "a":
        href = _safe_url(node.attrs.get("href", ""))
        if not href:
            return children  # Source-position anchors never leave the app.
        attrs["href"] = href
    elif tag == "img":
        src = _safe_url(node.attrs.get("src", ""))
        if not src:
            return escape(node.attrs.get("alt", ""))
        attrs = {"src": src, "alt": node.attrs.get("alt", "")}
    elif tag == "table":
        # Neutral structure survives email pasting without a reader theme.
        attrs = {"border": "1", "cellpadding": "6", "cellspacing": "0"}
    elif tag in {"td", "th"}:
        if in_header:
            tag = "th"
        for name in ("colspan", "rowspan"):
            value = node.attrs.get(name, "")
            if value.isdigit() and 0 < int(value) <= 1000:
                attrs[name] = value
        align = node.attrs.get("align", "").lower()
        if align in {"left", "center", "right"}:
            attrs["align"] = align
    elif tag == "ol":
        start = node.attrs.get("start", "")
        if start.isdigit():
            attrs["start"] = start
    serialized = "".join(f' {name}="{escape(value, quote=True)}"' for name, value in attrs.items())
    if tag in {"br", "hr", "img"}:
        return f"<{tag}{serialized}>"
    return f"<{tag}{serialized}>{children}</{tag}>"


def _descendants(node, tag):
    for child in node.children:
        if isinstance(child, _Node):
            if child.tag == tag:
                yield child
            else:
                yield from _descendants(child, tag)


def _plain(node, *, in_pre=False, depth=0):
    if isinstance(node, str):
        return node if in_pre else re.sub(r"\s+", " ", node)
    tag = node.tag
    if tag in {"ul", "ol"}:
        rows = []
        number = int(node.attrs.get("start", "1"))
        for child in node.children:
            if isinstance(child, _Node) and child.tag == "li":
                marker = f"{number}. " if tag == "ol" else "• "
                content = _plain(child, depth=depth + 1).strip()
                rows.append("  " * depth + marker + content)
                number += 1
        return ("\n" if depth else "") + "\n".join(rows) + "\n"
    if tag == "table":
        rows = []
        for row in _descendants(node, "tr"):
            cells = [_plain(cell).strip() for cell in row.children
                     if isinstance(cell, _Node) and cell.tag in {"td", "th"}]
            rows.append("\t".join(cells))
        return "\n".join(rows) + "\n\n"
    if tag == "br":
        return "\n"
    if tag == "hr":
        return "\n"
    if tag == "img":
        return node.attrs.get("alt", "") or "[Image]"
    content = "".join(_plain(child, in_pre=in_pre or tag == "pre", depth=depth) for child in node.children)
    if tag == "pre":
        return content + "\n\n"
    if tag in {"p", "div", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6"}:
        return content.strip() + "\n\n"
    return content


def _parse(html):
    parser = _FragmentParser()
    parser.feed(html)
    parser.close()
    parser.root.attrs["qt_fragment"] = parser.qt_fragment
    return parser.root


def selection_markdown(html):
    """Recover semantic Markdown from a rendered selection, without its theme."""
    document = QTextDocument()
    document.setHtml(html)
    markdown = clean_text(document.toMarkdown()).strip()
    # Preserve app page targets in the editor's storage-link representation.
    markdown = re.sub(r"\[([^\]]*)\]\((stillpoint-page:[^\s)]+)\)",
                      lambda match: f"[{decode_vault_link_href(match[2])}|{match[1]}]", markdown)
    return markdown


def clipboard_from_html(html, *, markdown=None, mode="formatted"):
    root = _parse(html)
    fragment = _semantic_html(root)
    # Parse the clean fragment for plain text so that discarded head/style
    # content, source anchors, and editor-only glyphs cannot leak into it.
    plain = _plain(_parse(fragment)).strip()
    plain = re.sub(r"\n[ \t]+\n", "\n\n", plain)
    plain = re.sub(r"\n{3,}", "\n\n", plain)
    mime = QMimeData()
    if mode == "plain":
        mime.setText(plain)
        return mime
    if markdown is None:
        markdown = selection_markdown(fragment)
    if mode == "markdown":
        mime.setText(markdown)
        mime.setData("text/markdown", markdown.encode("utf-8"))
        return mime
    mime.setText(plain)
    mime.setHtml('<html><head><meta charset="utf-8"></head><body><!--StartFragment-->'
                 + fragment + '<!--EndFragment--></body></html>')
    mime.setData(INTERNAL_MARKDOWN, markdown.encode("utf-8"))
    return mime


def clipboard_from_markdown(markdown, *, mode="formatted"):
    # Reading View intentionally draws some dash markers literally. Clipboard
    # HTML uses semantic lists so recipient applications can edit their items.
    normalized = _rewrite_strikethrough(_rewrite_highlight(_rewrite_zim_links(markdown)))
    lines = []
    fence = ""
    for line in normalized.splitlines():
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if marker:
            value = marker[1]
            if not fence:
                fence = value
            elif value[0] == fence[0] and len(value) >= len(fence):
                fence = ""
        elif not fence and not re.fullmatch(r"\s*(?:-\s*){3,}", line):
            line = re.sub(r"^([ \t]*)-([ \t]+)", r"\1*\2", line)
        lines.append(line)
    normalized = "\n".join(lines)
    normalized = _normalize_markdown_lists(normalized)
    html = md.markdown(normalized, extensions=["fenced_code", "tables", "nl2br", EditorEmphasisExtension()])
    return clipboard_from_html(html, markdown=markdown, mode=mode)
