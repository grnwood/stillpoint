from __future__ import annotations

import logging
import sys
import os
import re
import itertools
from pathlib import Path
from typing import Optional, Callable
import httpx
from html.parser import HTMLParser

from PySide6.QtCore import (
    QBuffer,
    QByteArray,
    QEvent,
    QMimeData,
    Qt,
    QRegularExpression,
    Signal,
    QUrl,
    QPoint,
    QTimer,
    QSignalBlocker,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QImage,
    QTextCharFormat,
    QTextCursor,
    QSyntaxHighlighter,
    QTextImageFormat,
    QTextFormat,
    QDesktopServices,
    QGuiApplication,
    QPainter,
    QPen,
    QKeyEvent,
    QKeySequence,
    QTextDocument,
    QShortcut,
    QAction,
)
from PySide6.QtWidgets import (
    QTextEdit,
    QMenu,
    QInputDialog,
    QDialog,
    QApplication,
    QWidget,
    QVBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QLabel,
)
from shiboken6 import Shiboken
from markdown import markdown as render_markdown
from .path_utils import path_to_colon, colon_to_path, ensure_root_colon_link
from .heading_utils import heading_slug
from .page_load_logger import PageLoadLogger
from .ai_actions_data import AI_ACTION_GROUPS
from .jump_dialog import JumpToPageDialog
from sp.app import config


logger = logging.getLogger(__name__)


class SearchEngine:
    """Lightweight search/replace helper bound to a MarkdownEditor."""

    def __init__(self, editor: "MarkdownEditor") -> None:
        self.editor = editor
        self.last_query: str = ""
        self.last_replacement: str = ""
        self.last_forward: bool = True
        self.last_case_sensitive: bool = False

    @staticmethod
    def _normalize_text(text: str) -> str:
        return text.replace("\u2029", "\n")

    def find_next(
        self,
        query: str,
        *,
        backwards: bool = False,
        wrap: bool = True,
        case_sensitive: bool = False,
    ) -> tuple[bool, bool]:
        query = query or self.last_query
        if not query:
            return False, False
        doc = self.editor.document()
        cursor = QTextCursor(self.editor.textCursor())
        flags = QTextDocument.FindFlag(0)
        if backwards:
            flags |= QTextDocument.FindBackward
        if case_sensitive:
            flags |= QTextDocument.FindCaseSensitively
        match = doc.find(query, cursor, flags)
        wrapped = False
        if match.isNull() and wrap:
            restart = QTextCursor(doc.end() if backwards else doc.begin())
            match = doc.find(query, restart, flags)
            wrapped = not match.isNull()
        if match.isNull():
            return False, False
        self.editor.setTextCursor(match)
        self.editor.ensureCursorVisible()
        self.last_query = query
        self.last_forward = not backwards
        self.last_case_sensitive = case_sensitive
        return True, wrapped

    def repeat_last(self, reverse: bool = False) -> tuple[bool, bool, bool]:
        if not self.last_query:
            return False, False, False
        backwards = not self.last_forward
        if reverse:
            backwards = not backwards
        found, wrapped = self.find_next(
            self.last_query,
            backwards=backwards,
            wrap=True,
            case_sensitive=self.last_case_sensitive,
        )
        return found, wrapped, backwards

    def replace_current(self, replacement: str) -> bool:
        cursor = self.editor.textCursor()
        if not cursor.hasSelection():
            return False
        selected = self._normalize_text(cursor.selectedText())
        if self.last_query and selected != self.last_query:
            return False
        cursor.beginEditBlock()
        cursor.insertText(replacement)
        cursor.endEditBlock()
        self.last_replacement = replacement
        return True

    def replace_all(self, query: str, replacement: str, *, case_sensitive: bool = False) -> int:
        query = query or self.last_query
        if not query:
            return 0
        doc = self.editor.document()
        edit_cursor = QTextCursor(doc)
        cursor = QTextCursor(doc)
        original_cursor = QTextCursor(self.editor.textCursor())
        flags = QTextDocument.FindFlag(0)
        if case_sensitive:
            flags |= QTextDocument.FindCaseSensitively
        count = 0
        edit_cursor.beginEditBlock()
        while True:
            match = doc.find(query, cursor, flags)
            if match.isNull():
                break
            match.insertText(replacement)
            # Continue searching after the replacement to avoid re-visiting the same span
            cursor = QTextCursor(doc)
            cursor.setPosition(match.selectionStart() + len(replacement))
            count += 1
        edit_cursor.endEditBlock()
        if count:
            self.last_query = query
            self.last_replacement = replacement
            self.last_forward = True
            self.last_case_sensitive = case_sensitive
            try:
                self.editor.setTextCursor(original_cursor)
            except Exception:
                pass
        return count


TAG_PATTERN = QRegularExpression(r"(?<![\w.+-])@([A-Za-z0-9_]+)")
TASK_PATTERN = QRegularExpression(
    r"^(?P<indent>\s*)"
    r"(?:[-*]\s*\[(?P<state>[ xX])\])"
    r"(?P<body>\s+.*)$"
)
TASK_LINE_PATTERN = re.compile(
    r"^(\s*)([-*])\s*\[(?P<state>[ xX])\](\s+)(?P<body>.*)$",
    re.MULTILINE,
)
SYMBOL_TASK_LINE_PATTERN = re.compile(
    r"^(\s*)([☐☑])(\s+)(?P<body>.*)$",
    re.MULTILINE,
)
# Bullet patterns for storage and display
BULLET_STORAGE_PATTERN = re.compile(r"^(\s*)\* ", re.MULTILINE)
BULLET_DISPLAY_PATTERN = re.compile(r"^(\s*)• ", re.MULTILINE)
# Plus-prefixed link pattern: +PageName or +Projects (CamelCase style), requires surrounding whitespace
CAMEL_LINK_PATTERN = QRegularExpression(r"(?<!\\S)\\+(?P<link>[A-Za-z][\\w]*)(?=\\s|$)")

# CamelCase link pattern: +PageName (deprecated but kept for compatibility)
CAMEL_LINK_PATTERN = QRegularExpression(r"(?<!\\S)\\+(?P<link>[A-Za-z][\\w]*)(?=\\s|$)")

# File link pattern for attachments: [text](./file.ext) or [text](file.ext)
WIKI_FILE_LINK_PATTERN = QRegularExpression(
    r"\[(?P<text>[^\]]+)\]\s*\((?P<file>(?:\./)?[^)\n]+\.[A-Za-z0-9]{1,8})\)"
)

# Unified wiki-style link format: [link|label]
# Works for both HTTP URLs and page links (colon notation)
# Plain HTTP URL pattern (for highlighting plain URLs without labels)
HTTP_URL_PATTERN = QRegularExpression(r"(?P<url>https?://[^\s<>\"{}|\\^`\[\]]+)")
# Plain colon link pattern (for highlighting plain colon links without labels)
# Require start-of-line or whitespace so times like 08:19am don't register as links.
COLON_LINK_PATTERN = QRegularExpression(r"(?<!\S)(?P<link>:[^\s\[\]]+(?:#[^\s\[\]]+)?)")

# Unified wiki-style link storage format: [link|label]
# Matches both HTTP and page links (label can be empty)
WIKI_LINK_STORAGE_PATTERN = re.compile(
    r"\[(?P<link>[^\]|]+)\|(?P<label>[^\]]*)\]",
    re.MULTILINE
)

# Handles a rare duplication bug where a wiki link tail is re-appended after decoding,
# e.g. [link|label]tail|label] where tail is a suffix of link.
WIKI_LINK_DUPLICATE_TAIL_PATTERN = re.compile(
    r"\[(?P<link>[^\]|]+)\|(?P<label>[^\]]*)\](?P<tail>[^\s\]]+)\|\s*(?P=label)\]"
)

LINK_SENTINEL = "\uE100"
# Display pattern for rendered links (sentinel + link + sentinel + label + sentinel)
WIKI_LINK_DISPLAY_PATTERN = re.compile(
    rf"{LINK_SENTINEL}(?P<link>[^{LINK_SENTINEL}\n]+){LINK_SENTINEL}(?P<label>[^{LINK_SENTINEL}\n]*){LINK_SENTINEL}"
)

TABLE_ROW_PATTERN = re.compile(r"^\s*\|.*\|\s*$")
TABLE_SEP_PATTERN = re.compile(r"^\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*$")

HEADING_MAX_LEVEL = 5


class AIActionOverlay(QWidget):
    """Overlay widget showing AI action categories and actions."""

    actionTriggered = Signal(str, str)
    startChat = Signal()
    loadChat = Signal()
    sendSelection = Signal()
    closed = Signal()

    class Entry:
        def __init__(self, label: str, kind: str, group=None, action=None):
            self.label = label
            self.kind = kind
            self.group = group
            self.action = action

    def __init__(self, parent=None) -> None:
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self._text = ""
        self._current_group = None
        self._entries: list[AIActionOverlay.Entry] = []
        self._groups = AI_ACTION_GROUPS
        self._has_chat = False
        self._chat_active = False
        self.setStyleSheet("background: #000000; color: white; border-radius: 10px; border: 1px solid #222222;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)
        self._selection_label = QLabel()
        self._selection_label.setStyleSheet("color: #dfe6fa; font-size: 16px;")
        self._selection_label.setVisible(False)
        layout.addWidget(self._selection_label)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Send Selection to AI Chat or type action…")
        self._search.setStyleSheet(
            "font-size: 18px; color: white; background: rgba(255, 255, 255, 0.08); border: 1px solid rgba(255, 255, 255, 0.5); padding: 8px; border-radius: 6px;"
        )
        self._search.textChanged.connect(self._refresh_list)
        layout.addWidget(self._search)
        self._list = QListWidget()
        self._list.setUniformItemSizes(True)
        self._list.setStyleSheet("font-size: 18px; color: white; background: transparent; padding: 4px;")
        self._list.itemActivated.connect(self._activate_current_item)
        self._list.itemClicked.connect(lambda *_: self._activate_current_item())
        layout.addWidget(self._list)
        self._list.setMinimumHeight(220)
        self._search.installEventFilter(self)
        self._update_header()

    def text(self) -> str:
        return self._text

    def set_chat_state(self, *, has_chat: bool, chat_active: bool) -> None:
        self._has_chat = bool(has_chat)
        self._chat_active = bool(chat_active)

    def open(self, text: str, *, has_chat: Optional[bool] = None, chat_active: Optional[bool] = None, anchor: Optional[QPoint] = None) -> None:
        if not text:
            return
        if has_chat is not None:
            self._has_chat = bool(has_chat)
        if chat_active is not None:
            self._chat_active = bool(chat_active)
        self._text = text
        self._current_group = None
        self._update_entries()
        self._search.clear()
        self._search.setFocus()
        parent = self.parent()
        if parent:
            geo = parent.rect()
            width = min(max(420, geo.width() - 80), geo.width())
            height = min(280, max(200, geo.height() - 100))
            if anchor:
                screen_geo = QGuiApplication.primaryScreen().availableGeometry()
                left = max(screen_geo.left(), min(anchor.x() - width // 2, screen_geo.right() - width))
                top = max(screen_geo.top(), min(anchor.y() - height // 2, screen_geo.bottom() - height))
            else:
                center = parent.mapToGlobal(geo.center())
                left = center.x() - width // 2
                top = center.y() - height // 2
            self.setGeometry(left, top, width, height)
        self._update_header()
        self.show()
        self.raise_()
        self._refresh_list()
        if self._list.count():
            self._list.setCurrentRow(0)

    def _update_entries(self) -> None:
        self._entries = []
        if not self._current_group:
            if not self._has_chat:
                self._entries.append(AIActionOverlay.Entry("Send selection to Global Chat", "send_global"))
                self._entries.append(AIActionOverlay.Entry("Chat: Start Chat with this Page", "start_chat"))
                self._entries.append(AIActionOverlay.Entry("Chat: with Global", "load_global"))
            else:
                self._entries.append(AIActionOverlay.Entry("Send selection to Page Chat", "send_page"))
                self._entries.append(AIActionOverlay.Entry("Send selection to Global Chat", "send_global"))
                self._entries.append(AIActionOverlay.Entry("Chat: with Page", "load_chat"))
                self._entries.append(AIActionOverlay.Entry("Chat: With Global", "load_global"))
            # One-shot prompt: send selected text directly to the configured model
            # and replace the selection with the model response (does not add to chat history).
            self._entries.append(AIActionOverlay.Entry("One-Shot Prompt Selection", "one_shot"))
            for group in self._groups:
                label = f"{group.title}..."
                self._entries.append(AIActionOverlay.Entry(label, "group", group=group))
        else:
            for action in self._current_group.actions:
                self._entries.append(AIActionOverlay.Entry(action.title, "action", action=action))
        self._update_header()

    def _update_header(self) -> None:
        if self._current_group:
            self._selection_label.setText(f"{self._current_group.title}")
            self._selection_label.setVisible(True)
            self._search.setPlaceholderText(f"Type an action in {self._current_group.title}…")
        else:
            self._selection_label.setVisible(False)
            self._search.setPlaceholderText("Send selection, choose an action, or type a custom prompt…")

    def _refresh_list(self) -> None:
        raw_text = self._search.text()
        search_text = raw_text.lower().strip()
        custom_prompt = raw_text.strip()
        self._list.clear()
        matches = 0
        for entry in self._entries:
            if search_text and search_text not in entry.label.lower():
                continue
            item = QListWidgetItem(entry.label)
            item.setData(Qt.UserRole, entry)
            self._list.addItem(item)
            matches += 1
        if custom_prompt:
            custom_entry = AIActionOverlay.Entry(custom_prompt, "custom_prompt")
            custom_item = QListWidgetItem(f"Use custom prompt: {custom_prompt}")
            custom_item.setData(Qt.UserRole, custom_entry)
            self._list.addItem(custom_item)
        count = self._list.count()
        if count:
            self._list.setCurrentRow(0 if matches else count - 1)

    def eventFilter(self, obj, event):  # type: ignore[override]
        if obj == self._search and event.type() == QEvent.KeyPress:
            if event.key() in (Qt.Key_Down, Qt.Key_Up):
                delta = 1 if event.key() == Qt.Key_Down else -1
                self._move_selection(delta)
                return True
            if event.modifiers() == (Qt.ControlModifier | Qt.ShiftModifier):
                if event.key() == Qt.Key_J:
                    self._move_selection(1)
                    return True
                if event.key() == Qt.Key_K:
                    self._move_selection(-1)
                    return True
            if event.key() == Qt.Key_Right:
                current = self._list.currentItem()
                if current:
                    entry = current.data(Qt.UserRole)
                    if entry and entry.kind == "group":
                        self._current_group = entry.group
                        self._update_entries()
                        self._refresh_list()
                        return True
            if event.key() in (Qt.Key_Left, Qt.Key_Backspace) and not self._search.text():
                if self._current_group:
                    self._current_group = None
                    self._update_entries()
                    self._refresh_list()
                    return True
            if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Tab, Qt.Key_Space):
                if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                    self._activate_current_item()
                    return True
        return super().eventFilter(obj, event)

    def _move_selection(self, delta: int) -> None:
        count = self._list.count()
        if not count:
            return
        row = self._list.currentRow()
        row = max(0, min(count - 1, row + delta))
        self._list.setCurrentRow(row)

    def _activate_current_item(self) -> None:
        item = self._list.currentItem()
        if not item:
            return
        entry = item.data(Qt.UserRole)
        if not entry:
            return
        if entry.kind == "send_page":
            self.actionTriggered.emit("Send selection to Page Chat", "")
            self.hide()
        elif entry.kind == "send_global":
            self.actionTriggered.emit("Send selection to Global Chat", "")
            self.hide()
        elif entry.kind == "start_chat":
            self.startChat.emit()
            self.hide()
        elif entry.kind == "load_chat":
            self.loadChat.emit()
            self.hide()
        elif entry.kind == "load_global":
            self.actionTriggered.emit("Load Global Chat", "")
            self.hide()
        elif entry.kind == "group":
            self._current_group = entry.group
            self._update_entries()
            self._refresh_list()
        elif entry.kind == "action":
            self.actionTriggered.emit(entry.action.title, entry.action.prompt)
            self.hide()
        elif entry.kind == "one_shot":
            # Signal a one-shot action; main window will perform a direct API call
            self.actionTriggered.emit("One-Shot Prompt Selection", "")
            self.hide()
        elif entry.kind == "custom_prompt":
            self.actionTriggered.emit("Custom Prompt", entry.label)
            self.hide()

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._activate_current_item()
            return
        if event.key() == Qt.Key_Escape:
            self.hide()
            return
        if event.key() == Qt.Key_Backspace and not self._search.text() and self._current_group:
            self._current_group = None
            self._update_entries()
            self._refresh_list()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event) -> None:
        self.hide()
        super().focusOutEvent(event)

    def hide(self) -> None:  # type: ignore[override]
        super().hide()
        self.closed.emit()

    def is_visible(self) -> bool:
        """Helper so parents can detect when the overlay is open."""
        return self.isVisible()
HEADING_SENTINEL_BASE = 0xE000
HEADING_MARK_PATTERN = re.compile(r"^(\s*)(#{1,5})(\s+)(.+)$", re.MULTILINE)
HEADING_SENTINEL_CHARS = "".join(chr(HEADING_SENTINEL_BASE + lvl) for lvl in range(1, HEADING_MAX_LEVEL + 1))
HEADING_DISPLAY_PATTERN = re.compile(rf"^(\s*)([{HEADING_SENTINEL_CHARS}])(.*)$", re.MULTILINE)
IMAGE_PATTERN = re.compile(
    r"!\[(?P<alt>[^\]]*)\]\((?P<path>[^)\s]+)\)(?:\{width=(?P<width>\d+)\})?", re.MULTILINE
)

IMAGE_PROP_ALT = int(QTextFormat.UserProperty)
IMAGE_PROP_ORIGINAL = IMAGE_PROP_ALT + 1
IMAGE_PROP_WIDTH = IMAGE_PROP_ALT + 2
IMAGE_PROP_NATURAL_WIDTH = IMAGE_PROP_ALT + 3
IMAGE_PROP_NATURAL_HEIGHT = IMAGE_PROP_ALT + 4

_DETAILED_LOGGING = os.getenv("SP_DETAILED_LOGGING", "0") not in ("0", "false", "False", "", None)

def _utf16_positions(text: str) -> list[int]:
    positions = [0]
    for ch in text:
        positions.append(positions[-1] + (2 if ord(ch) > 0xFFFF else 1))
    return positions


def heading_sentinel(level: int) -> str:
    level = max(1, min(HEADING_MAX_LEVEL, level))
    return chr(HEADING_SENTINEL_BASE + level)


def heading_level_from_char(char: str) -> int:
    if not char:
        return 0
    code = ord(char) - HEADING_SENTINEL_BASE
    if 1 <= code <= HEADING_MAX_LEVEL:
        return code
    return 0

class MarkdownHighlighter(QSyntaxHighlighter):
    CODE_BLOCK_STATE = 1

    def __init__(self, parent) -> None:  # type: ignore[override]
        super().__init__(parent)
        # Precompile regex patterns (avoid per-block construction)
        self._code_pattern = QRegularExpression(r"`[^`]+`")
        self._bold_italic_pattern = QRegularExpression(r"\*\*\*([^*]+)\*\*\*")
        self._bold_pattern = QRegularExpression(r"\*\*([^*]+)\*\*")
        self._italic_pattern = QRegularExpression(r"(?<!\*)\*([^*]+)\*(?!\*)")
        self._strikethrough_pattern = QRegularExpression(r"~~([^~]+)~~")
        self._highlight_pattern = QRegularExpression(r"==([^=]+)==")
        # Timing instrumentation
        self._timing_enabled = False
        self._timing_total = 0.0
        self._timing_blocks = 0
        self.heading_format = QTextCharFormat()
        self.heading_format.setForeground(QColor("#6cb4ff"))
        self.heading_format.setFontWeight(QFont.Weight.DemiBold)
        self.hidden_format = QTextCharFormat()
        transparent = QColor(0, 0, 0, 0)
        self.hidden_format.setForeground(transparent)
        # Tiny size so hidden sentinels don't create visible gaps
        self.hidden_format.setFontPointSize(0.01)

        self._heading_multipliers = (1.9, 1.6, 1.35, 1.2, 1.08)
        self.heading_styles: list[QTextCharFormat] = []
        try:
            base_font = parent.defaultFont()
            base_pt = base_font.pointSizeF() or base_font.pointSize() or 14.0
        except Exception:
            base_pt = 14.0
        self._apply_heading_sizes(base_pt)

        self.bold_format = QTextCharFormat()
        self.bold_format.setForeground(QColor("#ffd479"))

        self.italic_format = QTextCharFormat()
        self.italic_format.setForeground(QColor("#ffa7c4"))

        mono_font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        if mono_font.family():
            mono_family = mono_font.family()
        else:
            mono_family = "Courier New"
        self.code_format = QTextCharFormat()
        self.code_format.setForeground(QColor("#a3ffab"))
        self.code_format.setBackground(QColor("#2a2a2a"))
        self.code_format.setFontFamily(mono_family)
        self.code_format.setFontFixedPitch(True)
        self.code_format.setFontStyleHint(QFont.StyleHint.Monospace)

        self.quote_format = QTextCharFormat()
        self.quote_format.setForeground(QColor("#7fdbff"))
        self.quote_format.setFontItalic(True)

        self.list_format = QTextCharFormat()
        self.list_format.setForeground(QColor("#ffffff"))

        self.code_block = QTextCharFormat()
        self.code_block.setBackground(QColor("#2a2a2a"))
        self.code_block.setForeground(QColor("#a3ffab"))
        self.code_block.setFontFamily(mono_family)
        self.code_block.setFontFixedPitch(True)
        self.code_block.setFontStyleHint(QFont.StyleHint.Monospace)
        
        self.code_fence_format = QTextCharFormat()
        self.code_fence_format.setForeground(QColor("#555555"))

        self.tag_format = QTextCharFormat()
        self.tag_format.setForeground(QColor("#ffa657"))

        self.checkbox_format = QTextCharFormat()
        self.checkbox_format.setForeground(QColor("#c8c8c8"))
        self.checkbox_format.setFontFamily("Segoe UI Symbol")

        self.hr_format = QTextCharFormat()
        self.hr_format.setForeground(QColor("#555555"))
        self.hr_format.setBackground(QColor("#333333"))

        self.table_format = QTextCharFormat()
        self.table_format.setFontFamily(mono_family)
        self.table_format.setFontFixedPitch(True)
        self.table_format.setFontStyleHint(QFont.StyleHint.Monospace)
        self._base_font_size = base_pt
        self._table_font_size = max(6.0, base_pt - 2.0)
        self.table_format.setFontPointSize(self._table_font_size)
        
        # Strikethrough format
        self.strikethrough_format = QTextCharFormat()
        self.strikethrough_format.setForeground(QColor("#888888"))
        self.strikethrough_format.setFontStrikeOut(True)
        
        # Highlight format
        self.highlight_format = QTextCharFormat()
        self.highlight_format.setBackground(QColor("#ffff00"))
        self.highlight_format.setForeground(QColor("#000000"))
        
        # Bold+Italic combined format
        self.bold_italic_format = QTextCharFormat()
        self.bold_italic_format.setForeground(QColor("#ffb8d1"))
        self.bold_italic_format.setFontWeight(QFont.Weight.Bold)
        self.bold_italic_format.setFontItalic(True)

        self._reset_code_block_cache()
        self._init_pygments(config.load_pygments_style("monokai"))
        
        # Block-level highlighting cache for performance
        self._block_cache: dict[int, tuple[int, list[tuple[int, int, QTextCharFormat]]]] = {}
        self._cache_access_order: list[int] = []
        self._cache_max_size: int = 1000
        self._cache_hits: int = 0
        self._cache_misses: int = 0

    def _apply_heading_sizes(self, base_pt: float) -> None:
        """Recompute heading sizes from the current base font size."""
        safe_base = max(6.0, base_pt or 14.0)
        self.heading_styles = []
        for mult in self._heading_multipliers:
            fmt = QTextCharFormat(self.heading_format)
            fmt.setFontPointSize(safe_base * mult)
            self.heading_styles.append(fmt)

    def set_base_font_size(self, base_pt: float) -> None:
        """Update heading/table sizes when the editor zoom changes."""
        self._base_font_size = max(6.0, base_pt or 14.0)
        self._apply_heading_sizes(self._base_font_size)
        self._table_font_size = max(6.0, self._base_font_size - 2.0)
        self.table_format.setFontPointSize(self._table_font_size)
        self.rehighlight()

    def _reset_code_block_cache(self) -> None:
        self._code_block_spans: dict[int, list[tuple[int, int, QTextCharFormat]]] = {}
        self._active_code_lang: Optional[str] = None

    def _init_pygments(self, style_name: Optional[str] = None) -> None:
        self._pygments_enabled = False
        try:
            from pygments.formatters.html import HtmlFormatter
            from pygments.lexers import get_lexer_by_name, TextLexer
            from pygments import lex
        except Exception as exc:
            logger.warning("Pygments unavailable; code fences stay monospace only: %s", exc)
            return

        self._pygments_enabled = True
        chosen_style = style_name or "monokai"
        self._pygments_style_name = chosen_style
        self._pygments_lex = lex
        try:
            self._pygments_formatter = HtmlFormatter(style=chosen_style)
        except Exception:
            self._pygments_formatter = HtmlFormatter(style="monokai")
            self._pygments_style_name = "monokai"
        self._pygments_get_lexer = get_lexer_by_name
        self._pygments_text_lexer = TextLexer
        self._pygments_format_cache: dict[str, QTextCharFormat] = {}
        self._pygments_lexer_cache: dict[str, object] = {}

    def set_pygments_style(self, style_name: str) -> None:
        """Update the Pygments style and rehighlight."""
        self._init_pygments(style_name)
        self._reset_code_block_cache()
        try:
            self.rehighlight()
        except Exception:
            pass

    def _extract_fence_language(self, text: str) -> Optional[str]:
        if not text.startswith("```"):
            return None
        lang = text[3:].strip()
        return lang or None

    def _lexer_for_language(self, lang: Optional[str]):
        if not self._pygments_enabled:
            return None
        cache_key = (lang or "").lower()
        if cache_key in self._pygments_lexer_cache:
            return self._pygments_lexer_cache[cache_key]
        try:
            lexer = self._pygments_get_lexer(cache_key) if cache_key else self._pygments_text_lexer()
        except Exception:
            lexer = self._pygments_text_lexer()
        self._pygments_lexer_cache[cache_key] = lexer
        return lexer

    def _format_for_token(self, token) -> QTextCharFormat:
        if not self._pygments_enabled:
            return self.code_block

        key = str(token)
        fmt = self._pygments_format_cache.get(key)
        if fmt:
            return fmt

        style = self._pygments_formatter.style.style_for_token(token)
        fmt = QTextCharFormat(self.code_block)
        if style.get("color"):
            fmt.setForeground(QColor(f"#{style['color']}"))
        if style.get("bgcolor"):
            fmt.setBackground(QColor(f"#{style['bgcolor']}"))
        if style.get("bold"):
            fmt.setFontWeight(QFont.Weight.Bold)
        if style.get("italic"):
            fmt.setFontItalic(True)
        if style.get("underline"):
            fmt.setFontUnderline(True)
        self._pygments_format_cache[key] = fmt
        return fmt

    def _cache_code_block_spans(self, start_block, lang: Optional[str]) -> None:
        if not self._pygments_enabled:
            return
        lexer = self._lexer_for_language(lang)
        if lexer is None:
            return

        blocks = []
        lines = []
        block = start_block
        while block.isValid():
            text = block.text()
            if text.startswith("```"):
                break
            blocks.append(block)
            lines.append(text)
            block = block.next()

        if not blocks:
            return

        code = "\n".join(lines)
        try:
            tokens = self._pygments_lex(code, lexer)
        except Exception as exc:
            logger.debug("Pygments lexing failed for %s: %s", lang or "plain", exc)
            return

        line_idx = 0
        col = 0
        for token_type, value in tokens:
            remaining = value
            while remaining:
                newline = remaining.find("\n")
                if newline == -1:
                    part = remaining
                    remaining = ""
                else:
                    part = remaining[:newline]
                    remaining = remaining[newline + 1 :]
                if line_idx >= len(blocks):
                    break
                if part:
                    fmt = self._format_for_token(token_type)
                    block_number = blocks[line_idx].blockNumber()
                    self._code_block_spans.setdefault(block_number, []).append((col, len(part), fmt))
                    col += len(part)
                if newline != -1:
                    line_idx += 1
                    col = 0

    def _ensure_code_block_cache(self, block) -> None:
        """If a rehighlight starts mid-block, rebuild cached spans by scanning backward to the fence."""
        if not self._pygments_enabled or block.blockNumber() in self._code_block_spans:
            return
        fence = block.previous()
        while fence.isValid():
            text = fence.text()
            if text.startswith("```"):
                lang = self._extract_fence_language(text)
                self._cache_code_block_spans(fence.next(), lang)
                break
            fence = fence.previous()

    def _scan_inline_patterns(self, text: str) -> list[tuple[int, int, str]]:
        """Single-pass scanner for all inline markdown patterns.
        
        Returns list of (start, length, pattern_name) tuples.
        Uses combined regex for efficiency - one pass instead of 10+.
        """
        # Combined pattern with named groups - matches all inline markdown in priority order
        combined = re.compile(
            r'(?P<code>`[^`]+`)|'
            r'(?P<bold_italic>\*\*\*[^*]+\*\*\*)|'
            r'(?P<bold>\*\*[^*]+\*\*)|'
            r'(?P<italic>(?<!\*)\*[^*]+\*(?!\*))|'
            r'(?P<strike>~~[^~]+~~)|'
            r'(?P<highlight>==[^=]+==)|'
            r'(?P<tag>(?<![\w.+-])@[A-Za-z0-9_]+)|'
            r'(?P<wiki_link>\[[^\]|]+\|[^\]]*\])|'
            r'(?P<camel_link>(?<!\S)\+[A-Za-z][\w]*(?=\s|$))|'
            r'(?P<colon_link>(?<!\S):[^\s\[\]]+(?:#[^\s\[\]]+)?)|'
            r'(?P<file_link>\[[^\]]+\]\s*\((?:\./)?[^)\n]+\.[A-Za-z0-9]{1,8}\))|'
            r'(?P<http_url>https?://[^\s<>"{}|\\^`\[\]]+)'
        )
        
        tokens = []
        for match in combined.finditer(text):
            # Find which named group matched
            for name, value in match.groupdict().items():
                if value is not None:
                    tokens.append((match.start(), match.end() - match.start(), name))
                    break
        
        return tokens
    
    def _apply_inline_code_formatting(self, text: str) -> None:
        """Hide backticks and apply inline code formatting for a line."""
        iterator = self._code_pattern.globalMatch(text)
        while iterator.hasNext():
            match = iterator.next()
            start = match.capturedStart()
            length = match.capturedLength()

            # Pattern: `content`
            if length >= 2:  # At least `x`
                content_start = start + 1  # Skip opening `
                content_length = length - 2  # Exclude both ` markers

                # Hide opening `
                self.setFormat(start, 1, self.hidden_format)

                # Apply code format to content
                if content_length > 0:
                    self.setFormat(content_start, content_length, self.code_format)

                # Hide closing `
                self.setFormat(start + length - 1, 1, self.hidden_format)

    def highlightBlock(self, text: str) -> None:  # type: ignore[override]
        import time
        t0 = time.perf_counter() if self._timing_enabled else 0.0

        block = self.currentBlock()
        block_num = block.blockNumber()
        block_revision = block.revision()
        
        # Check cache for this block
        if block_num in self._block_cache:
            cached_revision, cached_spans = self._block_cache[block_num]
            if cached_revision == block_revision:
                # Cache hit - apply cached formats and return
                for start, length, fmt in cached_spans:
                    self.setFormat(start, length, fmt)
                # Update LRU access order
                if block_num in self._cache_access_order:
                    self._cache_access_order.remove(block_num)
                self._cache_access_order.append(block_num)
                self._cache_hits += 1
                if self._timing_enabled:
                    self._timing_blocks += 1
                    self._timing_total += time.perf_counter() - t0
                return
        
        self._cache_misses += 1
        # Track all format operations for caching
        self._current_format_spans: list[tuple[int, int, QTextCharFormat]] = []
        self._capturing_formats = True
        if not block.previous().isValid():
            self._reset_code_block_cache()

        prev_state = self.previousBlockState()
        in_code_block = (prev_state == self.CODE_BLOCK_STATE)
        
        # Check if this line starts or ends a code block
        if text.startswith("```"):
            if in_code_block:
                self._active_code_lang = None
            else:
                self._active_code_lang = self._extract_fence_language(text)
                self._cache_code_block_spans(block.next(), self._active_code_lang)
            in_code_block = not in_code_block
            self.setCurrentBlockState(self.CODE_BLOCK_STATE if in_code_block else 0)
            self.setFormat(0, len(text), self.code_fence_format)
            if self._timing_enabled:
                self._timing_blocks += 1
                self._timing_total += time.perf_counter() - t0
            return
        elif in_code_block:
            self.setCurrentBlockState(self.CODE_BLOCK_STATE)
            self.setFormat(0, len(text), self.code_block)
            self._ensure_code_block_cache(block)
            spans = self._code_block_spans.get(block.blockNumber())
            if spans:
                for start, length, fmt in spans:
                    if length > 0 and start < len(text):
                        self.setFormat(start, min(length, len(text) - start), fmt)
            if self._timing_enabled:
                self._timing_blocks += 1
                self._timing_total += time.perf_counter() - t0
            return
        else:
            self.setCurrentBlockState(0)

        stripped = text.lstrip()
        indent = len(text) - len(stripped)
        level = heading_level_from_char(stripped[0]) if stripped else 0
        heading_applied = False
        if level:
            fmt = self.heading_styles[min(level, len(self.heading_styles)) - 1]
            self.setFormat(indent + 1, max(0, len(stripped) - 1), fmt)
            self.setFormat(indent, 1, self.hidden_format)
            heading_applied = True
        elif stripped.startswith("#"):
            hashes = len(stripped) - len(stripped.lstrip("#"))
            if 1 <= hashes <= HEADING_MAX_LEVEL and stripped[hashes:hashes + 1] == " ":
                fmt = self.heading_styles[min(hashes, len(self.heading_styles)) - 1]
                self.setFormat(indent + hashes + 1, len(stripped) - hashes - 1, fmt)
                heading_applied = True

        # If we styled a heading, stop here so later rules (links, tags, etc.) don't override
        # the heading font size/color and leave trailing characters unstyled.
        if heading_applied:
            self._apply_inline_code_formatting(text)
            if self._timing_enabled:
                self._timing_blocks += 1
                self._timing_total += time.perf_counter() - t0
            return

        is_table = bool(TABLE_ROW_PATTERN.match(text) or TABLE_SEP_PATTERN.match(text))

        if text.strip().startswith(("- ", "* ", "+ ", "• ")):
            self.setFormat(0, len(text), self.list_format)
        
        # Blockquotes - handle > and >> (nested)
        stripped_for_quote = text.lstrip()
        if stripped_for_quote.startswith(">"):
            # Count the number of > markers
            idx = 0
            while idx < len(stripped_for_quote) and stripped_for_quote[idx] == '>':
                idx += 1
            # Skip optional space after >
            if idx < len(stripped_for_quote) and stripped_for_quote[idx] == ' ':
                idx += 1

            # Hide the > markers and optional spaces
            quote_start = len(text) - len(stripped_for_quote)
            self.setFormat(quote_start, idx, self.hidden_format)

            # Style the remaining text as quote
            remaining_length = len(text) - quote_start - idx
            if remaining_length > 0:
                self.setFormat(quote_start + idx, remaining_length, self.quote_format)
        
        stripped_hr = text.strip()
        if stripped_hr == "---" or stripped_hr == "***" or stripped_hr == "___":
            self.setFormat(0, len(text), self.hr_format)
        
        # Apply monospace + compact font to table rows last so pipes align
        if is_table:
            self.setFormat(0, len(text), self.table_format)
        
        # === PHASE 2: Single-pass inline pattern scanner ===
        # Scan all patterns once instead of 10+ separate passes
        tokens = self._scan_inline_patterns(text)
        
        # Track bold_italic ranges to avoid double-formatting
        bold_italic_ranges = []
        
        # Build display link exclusion zones (sentinel-delimited links)
        display_link_spans: list[tuple[int, int]] = []
        idx = 0
        while idx < len(text):
            if text[idx] == LINK_SENTINEL:
                link_start = idx + 1
                link_end = text.find(LINK_SENTINEL, link_start)
                if link_end > link_start:
                    label_start = link_end + 1
                    label_end = text.find(LINK_SENTINEL, label_start)
                    if label_end >= label_start:
                        link_fmt = QTextCharFormat()
                        link_fmt.setForeground(QColor("#4fa3ff"))
                        link_fmt.setFontUnderline(True)
                        # Hide opening sentinel and link
                        self.setFormat(idx, label_start - idx, self.hidden_format)
                        # If label is empty, show the link; otherwise show the label
                        if label_end == label_start:
                            visible_end = link_end
                            if link_end > link_start and text[link_end - 1] == "|":
                                visible_end -= 1
                            if visible_end > link_start:
                                self.setFormat(link_start, visible_end - link_start, link_fmt)
                        else:
                            self.setFormat(label_start, label_end - label_start, link_fmt)
                        self.setFormat(label_end, 1, self.hidden_format)
                        display_link_spans.append((idx, label_end + 1))
                        idx = label_end + 1
                        continue
            idx += 1
        
        # Build wiki link exclusion zones
        wiki_spans: list[tuple[int, int]] = []
        
        # Apply token formats
        for start, length, token_type in tokens:
            end = start + length
            
            # Check if inside display link
            inside_display = any(ds <= start and end <= de for (ds, de) in display_link_spans)
            
            if token_type == 'code':
                # Inline code: `content`
                if length >= 2:
                    self.setFormat(start, 1, self.hidden_format)  # opening `
                    self.setFormat(start + 1, length - 2, self.code_format)  # content
                    self.setFormat(start + length - 1, 1, self.hidden_format)  # closing `
                
            elif token_type == 'bold_italic':
                # ***content***
                bold_italic_ranges.append((start, end))
                if length >= 6:
                    self.setFormat(start, 3, self.hidden_format)
                    fmt = QTextCharFormat()
                    fmt.setFontWeight(QFont.Weight.Bold)
                    fmt.setFontItalic(True)
                    fmt.setForeground(QColor("#ffb8d1"))
                    self.setFormat(start + 3, length - 6, fmt)
                    self.setFormat(start + length - 3, 3, self.hidden_format)
                
            elif token_type == 'bold':
                # **content** - skip if inside bold_italic
                overlaps = any(bi_start <= start < bi_end or bi_start < end <= bi_end 
                              for bi_start, bi_end in bold_italic_ranges)
                if not overlaps and length >= 4:
                    self.setFormat(start, 2, self.hidden_format)
                    fmt = QTextCharFormat()
                    fmt.setFontWeight(QFont.Weight.Bold)
                    fmt.setForeground(QColor("#ffd479"))
                    self.setFormat(start + 2, length - 4, fmt)
                    self.setFormat(start + length - 2, 2, self.hidden_format)
                    
            elif token_type == 'italic':
                # *content* - skip if inside bold_italic
                overlaps = any(bi_start <= start < bi_end or bi_start < end <= bi_end 
                              for bi_start, bi_end in bold_italic_ranges)
                if not overlaps and length >= 2:
                    self.setFormat(start, 1, self.hidden_format)
                    fmt = QTextCharFormat()
                    fmt.setFontItalic(True)
                    fmt.setForeground(QColor("#ffa7c4"))
                    self.setFormat(start + 1, length - 2, fmt)
                    self.setFormat(start + length - 1, 1, self.hidden_format)
                    
            elif token_type == 'strike':
                # ~~content~~
                if length >= 4:
                    self.setFormat(start, 2, self.hidden_format)
                    self.setFormat(start + 2, length - 4, self.strikethrough_format)
                    self.setFormat(start + length - 2, 2, self.hidden_format)
                
            elif token_type == 'highlight':
                # ==content==
                if length >= 4:
                    self.setFormat(start, 2, self.hidden_format)
                    self.setFormat(start + 2, length - 4, self.highlight_format)
                    self.setFormat(start + length - 2, 2, self.hidden_format)
                
            elif token_type == 'tag':
                # @tag
                self.setFormat(start, length, self.tag_format)
                
            elif token_type == 'wiki_link':
                # [link|label] - track span and format label
                wiki_spans.append((start, end))
                pipe_pos = text.find('|', start, end)
                if pipe_pos != -1:
                    label_start = pipe_pos + 1
                    label_length = end - label_start - 1  # -1 for closing ]
                    link_fmt = QTextCharFormat()
                    link_fmt.setForeground(QColor("#4fa3ff"))
                    link_fmt.setFontUnderline(True)
                    self.setFormat(label_start, label_length, link_fmt)
                    
            elif token_type == 'camel_link':
                # +PageName - skip if inside wiki or display link
                inside_wiki = any(ws <= start and end <= we for (ws, we) in wiki_spans)
                if not inside_wiki and not inside_display:
                    link_fmt = QTextCharFormat()
                    link_fmt.setForeground(QColor("#4fa3ff"))
                    link_fmt.setFontUnderline(True)
                    self.setFormat(start, length, link_fmt)
                    
            elif token_type == 'colon_link':
                # :Page:Name - skip if inside wiki or display link
                inside_wiki = any(ws <= start and end <= we for (ws, we) in wiki_spans)
                if not inside_wiki and not inside_display:
                    link_fmt = QTextCharFormat()
                    link_fmt.setForeground(QColor("#4fa3ff"))
                    link_fmt.setFontUnderline(True)
                    self.setFormat(start, length, link_fmt)
                    
            elif token_type == 'file_link':
                # [text](./file.ext) - skip if overlaps wiki link
                inside_wiki = any(ws <= start and end <= we for (ws, we) in wiki_spans)
                if not inside_wiki:
                    bracket_end = text.find(']', start)
                    if bracket_end != -1 and bracket_end < end:
                        label_start = start + 1
                        label_length = bracket_end - label_start
                        link_fmt = QTextCharFormat()
                        link_fmt.setForeground(QColor("#4fa3ff"))
                        link_fmt.setFontUnderline(True)
                        self.setFormat(start, 1, self.hidden_format)  # [
                        self.setFormat(label_start, label_length, link_fmt)
                        self.setFormat(bracket_end, end - bracket_end, self.hidden_format)  # ](...)
                        
            elif token_type == 'http_url':
                # https://... - skip if inside wiki or display link
                inside_wiki = any(ws <= start and end <= we for (ws, we) in wiki_spans)
                if not inside_display and not inside_display:
                    link_fmt = QTextCharFormat()
                    link_fmt.setForeground(QColor("#4fa3ff"))
                    link_fmt.setFontUnderline(True)
                    self.setFormat(start, length, link_fmt)
        
        # Checkbox symbols (☐☑) - not in scanner, handled separately
        stripped2 = text.lstrip()
        if stripped2.startswith("☐") or stripped2.startswith("☑"):
            offset = len(text) - len(stripped2)
            self.setFormat(offset, 1, self.checkbox_format)



        # Store computed formats in cache
        self._capturing_formats = False
        self._block_cache[block_num] = (block_revision, self._current_format_spans)
        self._current_format_spans = []
        # Update LRU access order
        if block_num in self._cache_access_order:
            self._cache_access_order.remove(block_num)
        self._cache_access_order.append(block_num)
        # Evict oldest if cache exceeds limit
        if len(self._block_cache) > self._cache_max_size:
            oldest = self._cache_access_order.pop(0)
            self._block_cache.pop(oldest, None)
        
        if self._timing_enabled:
            self._timing_total += (time.perf_counter() - t0)
            self._timing_blocks += 1

    def setFormat(self, start: int, count: int, format: QTextCharFormat) -> None:  # type: ignore[override]
        """Override setFormat to capture formatting spans for cache."""
        super().setFormat(start, count, format)
        if getattr(self, '_capturing_formats', False) and count > 0:
            # Make a copy of the format to avoid reference issues
            fmt_copy = QTextCharFormat(format)
            self._current_format_spans.append((start, count, fmt_copy))
    
    def reset_timing(self):
        self._timing_total = 0.0
        self._timing_blocks = 0

    def enable_timing(self, enabled: bool):
        self._timing_enabled = enabled


class MarkdownEditor(QTextEdit):
    def _convert_camelcase_links(self, text: str) -> str:
        """Convert +CamelCase links to colon-style links [:Path:Path:Page|+CamelCase] using current page context, but only if not already inside a [link|label]."""
        import re
        from pathlib import Path
        from sp.server.adapters.files import PAGE_SUFFIX, PAGE_SUFFIXES
        from .path_utils import path_to_colon
        current_path = self.current_relative_path() if hasattr(self, "current_relative_path") else None
        base_dir: Optional[Path] = None
        if current_path:
            try:
                current = Path(current_path)
                # When pointed at /Page/Page.md, use the containing folder as the base
                base_dir = current.parent if current.suffix in PAGE_SUFFIXES else current
            except Exception:
                base_dir = None
        # Find all [link|label] spans so we can skip +CamelCase in the label part
        link_spans = []
        for m in re.finditer(r'\[([^\]|]+)\|([^\]]*)\]', text):
            # Mark the label part (after the |)
            link_start = m.start()
            pipe = text.find('|', link_start, m.end())
            if pipe != -1:
                label_start = pipe + 1
                link_spans.append((label_start, m.end() - 1))  # exclude the closing ]
        def is_in_label(pos):
            return any(start <= pos < end for start, end in link_spans)
        allowed_prefixes = {"(", "[", "{", "<", "'", '"'}
        def replacer(match):
            start = match.start()
            if is_in_label(start):
                return match.group(0)  # Don't replace if in label part
            if start > 0:
                prev = text[start - 1]
                # Only convert when +CamelCase appears after whitespace or opening punctuation.
                if not prev.isspace() and prev not in allowed_prefixes:
                    return match.group(0)
            link = match.group('link')
            label = link  # Just the page name, no plus
            if base_dir:
                target_path = (base_dir / link / f"{link}{PAGE_SUFFIX}").as_posix()
                if not target_path.startswith("/"):
                    target_path = f"/{target_path}"
                colon_path = path_to_colon(target_path)
            else:
                colon_path = path_to_colon(f"/{link}/{link}{PAGE_SUFFIX}")
            return f"[:{colon_path}|{label}]"
        # Replace +CamelCase only if not in the label part of a [link|label]
        return re.sub(r'(?<!\S)\+(?P<link>[A-Za-z][\w]*)(?=\s|$)', replacer, text)
    imageSaved = Signal(str)
    focusLost = Signal()
    cursorMoved = Signal(int)
    linkActivated = Signal(str)
    linkHovered = Signal(str)  # Emits link path when hovering/cursor over a link
    linkCopied = Signal(str)  # Emits link text when a link is copied via context menu
    headingsChanged = Signal(list)
    viewportResized = Signal()
    editPageSourceRequested = Signal(str)  # Emits file path when user wants to edit page source
    openFileLocationRequested = Signal(str)  # Emits file path when user wants to open file location
    locateInNavigatorRequested = Signal(str)  # Emits current page path when locator is requested
    insertDateRequested = Signal()
    attachmentDropped = Signal(str)  # Emits filename when a file is dropped into the editor
    backlinksRequested = Signal(str)  # Emits current page path when backlinks are requested
    aiChatRequested = Signal(str)  # Emits current page path when AI Chat is requested
    aiChatSendRequested = Signal(str)  # Send selected/whole text to the open chat
    aiChatPageFocusRequested = Signal(str)  # Request the chat tab focused on this page
    aiInlinePromptRequested = Signal(QPoint, int)  # QPoint(global), insert_pos
    aiActionRequested = Signal(str, str, str)  # title, prompt, text
    deletePageRequested = Signal(QPoint)  # Request deleting the current page (with global position)
    printPageRequested = Signal(str)  # Request printing the current page
    findBarRequested = Signal(bool, bool, str)  # replace_mode, backwards_first, seed_query
    viInsertModeChanged = Signal(bool)  # Emits True when editor is in insert mode
    headingPickerRequested = Signal(object, bool)  # QPoint(global), prefer_above
    LIST_INDENT_UNIT = "  "
    _VI_EXTRA_KEY = QTextFormat.UserProperty + 1
    _FLASH_EXTRA_KEY = QTextFormat.UserProperty + 2
    _HR_EXTRA_KEY = QTextFormat.UserProperty + 3
    _LOAD_GUARD_DEPTH = 0  # class-level: block cursor/link work during any markdown load
    _block_count_guard: bool = False

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._current_path: Optional[str] = None
        self._last_context_menu_global_pos: Optional[QPoint] = None
        self._context_menu_selection: Optional[tuple[int, int]] = None
        # Context menus temporarily take focus (QMenu is a popup widget). That can trigger
        # focusOutEvent and upstream autosave handlers; suppress that specific focus-loss.
        self._suppress_focus_lost_once: bool = False
        # General-purpose focus loss suppression used by popup overlays/dialogs that
        # should not trigger autosave writes (e.g., one-shot prompt overlay).
        self._focus_lost_suppression_depth: int = 0
        self._vault_root: Optional[Path] = None
        self._remote_mode = False
        self._remote_cache_root: Optional[Path] = None
        self._api_base: Optional[str] = None
        self._http_client: Optional[httpx.Client] = None
        self._auth_prompt: Optional[Callable[[], bool]] = None
        self._vi_mode_active: bool = False
        self._vi_block_cursor_enabled: bool = True  # default on, controlled by preferences
        self._vi_saved_flash_time: Optional[int] = None
        self._vi_last_cursor_pos: int = -1
        self._vi_feature_enabled: bool = False
        self._vi_insert_mode: bool = False
        self._vi_replace_pending: bool = False
        self._vi_last_edit: Optional[Callable[[], None]] = None
        self._vi_clipboard: str = ""
        self._vi_pending_activation: bool = False
        self._vi_has_painted: bool = False
        self._vi_paint_in_progress: bool = False
        self._vi_activation_timer: Optional[QTimer] = None
        self._heading_outline: list[dict] = []
        self._dialog_block_input: bool = False
        self._read_only_mode: bool = False
        self._ai_actions_enabled: bool = True
        self._ai_chat_available: bool = False
        self._ai_chat_active: bool = False
        self._page_load_logger: Optional[PageLoadLogger] = None
        self._open_in_window_callback: Optional[Callable[[str], None]] = None
        self._filter_nav_callback: Optional[Callable[[str], None]] = None
        self._move_text_callback: Optional[Callable[[str, str], bool]] = None
        self._suppress_link_scan: bool = False
        self._suppress_vi_cursor: bool = False
        self._overlay_transition: bool = False  # True while a mode overlay is spinning up/down
        self._overlay_active: bool = False  # True while inside a ModeWindow
        self._cursor_events_blocked: bool = False
        self._enforce_display_guard: bool = False
        self._last_heading_block_num: Optional[int] = None
        self._pending_heading_block_num: Optional[int] = None
        self._pending_heading_level: Optional[int] = None
        self._search_engine = SearchEngine(self)
        self.setPlaceholderText("Open a Markdown file to begin editing…")
        self.setAcceptRichText(True)
        self.setTabStopDistance(4 * self.fontMetrics().horizontalAdvance(" "))
        self._indent_unit = " " * 4
        self.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.highlighter = MarkdownHighlighter(self.document())
        self.cursorPositionChanged.connect(self._emit_cursor)
        self.cursorPositionChanged.connect(self._maybe_update_vi_cursor)
        self._cursor_signals_connected = True
        self.cursorPositionChanged.connect(self._ensure_cursor_margin)
        self._display_guard = False
        self._hanging_indent_guard = False
        self._last_block_count: int = 0
        self.textChanged.connect(self._enforce_display_symbols)
        self.textChanged.connect(self._check_block_count_change)
        self.viewport().installEventFilter(self)
        self._heading_timer = QTimer(self)
        self._heading_timer.setInterval(250)
        self._heading_timer.setSingleShot(True)
        self._heading_timer.timeout.connect(self._emit_heading_outline)
        self.textChanged.connect(self._schedule_heading_outline)
        self._hr_timer = QTimer(self)
        self._hr_timer.setInterval(120)
        self._hr_timer.setSingleShot(True)
        self._hr_timer.timeout.connect(self._refresh_hr_selections)
        self.textChanged.connect(self._schedule_hr_selections)
        # Timer for CamelCase link conversion; explicitly started on key triggers
        self._camel_refresh_timer = QTimer(self)
        self._camel_refresh_timer.setInterval(120)
        self._camel_refresh_timer.setSingleShot(True)
        self._camel_refresh_timer.timeout.connect(self._refresh_camel_links)
        self._last_camel_trigger: Optional[str] = None
        self._last_camel_cursor_pos: Optional[int] = None
        # Guard flag for inline trigger processing
        self._processing_inline_trigger: bool = False
        # Enable mouse tracking for hover cursor changes
        self.viewport().setMouseTracking(True)
        # Enable drag and drop for file attachments
        self.setAcceptDrops(True)
        # Configure scroll-past-end margin initially
        QTimer.singleShot(0, self._apply_scroll_past_end_margin)
        QTimer.singleShot(0, self._refresh_hr_selections)

        self._ai_send_shortcut = QShortcut(QKeySequence("Ctrl+Shift+P"), self)
        try:
            self._ai_send_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        except Exception:
            pass
        self._ai_send_shortcut.activated.connect(self._show_ai_action_overlay)
        self._ai_focus_shortcut = QShortcut(QKeySequence("Ctrl+Shift+["), self)
        self._ai_focus_shortcut.activated.connect(self._emit_ai_chat_focus)

        self._ai_action_overlay = AIActionOverlay(self)
        self._ai_action_overlay.actionTriggered.connect(self._handle_ai_action_overlay)
        self._ai_action_overlay.sendSelection.connect(self._emit_ai_chat_send)
        self._ai_action_overlay.startChat.connect(self._emit_ai_chat_start)
        self._ai_action_overlay.loadChat.connect(self._emit_ai_chat_focus)
        self._ai_action_overlay.closed.connect(self._restore_vi_after_overlay)
        self._overlay_vi_mode_before: Optional[bool] = None
        self._document_alive = True
        self._editor_alive = True
        self._layout_alive = True
        self._viewport_alive = True
        self._suppress_paint = False
        self._suppress_paint_depth = 0
        self._saved_updates_enabled: Optional[bool] = None
        self.destroyed.connect(self._on_editor_destroyed)
        self._connect_document_signals(self.document())
        viewport = self.viewport()
        if viewport is not None:
            viewport.destroyed.connect(self._on_viewport_destroyed)

    def _status_message(self, msg: str, duration: int = 2000) -> None:
        window = self.window()
        try:
            if window and hasattr(window, "statusBar"):
                window.statusBar().showMessage(msg, duration)
        except Exception:
            pass

    def _search_seed_query(self) -> str:
        cursor = self.textCursor()
        if cursor.hasSelection():
            return cursor.selectedText().replace("\u2029", "\n")
        return ""

    def set_read_only_mode(self, read_only: bool) -> None:
        """Enable or disable user edits while keeping navigation intact."""
        self._read_only_mode = bool(read_only)
        self.setReadOnly(self._read_only_mode)
        if self._read_only_mode and self._vi_feature_enabled:
            # Exit insert/replace states so vi navigation keys keep working
            self._vi_insert_mode = False
            self._vi_replace_pending = False
            try:
                self._enter_vi_navigation_mode()
            except Exception:
                pass

    def search_find_next(self, query: str, *, backwards: bool = False, wrap: bool = True, case_sensitive: bool = False) -> bool:
        found, wrapped = self._search_engine.find_next(
            query,
            backwards=backwards,
            wrap=wrap,
            case_sensitive=case_sensitive,
        )
        if not found:
            self._status_message("No match found.")
            return False
        if wrapped:
            self._status_message(f"Wrapped to {'end' if backwards else 'beginning'} of document.")
        return True

    def search_replace_current(self, replacement: str) -> bool:
        if not self._search_engine.last_query:
            self._status_message("Find text before replacing.")
            return False
        replaced = self._search_engine.replace_current(replacement)
        if not replaced:
            self._status_message("No current match to replace.")
        return replaced

    def search_replace_all(self, query: str, replacement: str, *, case_sensitive: bool = False) -> int:
        count = self._search_engine.replace_all(query, replacement, case_sensitive=case_sensitive)
        if count == 0:
            self._status_message("No matches replaced.")
        else:
            plural = "occurrence" if count == 1 else "occurrences"
            self._status_message(f"Replaced {count} {plural}.", 2500)
        return count

    def search_repeat_last(self, reverse: bool = False) -> bool:
        found, wrapped, backwards = self._search_engine.repeat_last(reverse=reverse)
        if not found:
            self._status_message("No previous search.")
            return False
        if wrapped:
            self._status_message(f"Wrapped to {'end' if backwards else 'beginning'} of document.")
        return True

    def search_word_under_cursor(self, *, backwards: bool = False) -> bool:
        cursor = self.textCursor()
        cursor.select(QTextCursor.WordUnderCursor)
        word = cursor.selectedText().replace("\u2029", "\n").strip()
        if not word:
            self._status_message("No word under cursor.")
            return False
        return self.search_find_next(word, backwards=backwards, wrap=True, case_sensitive=False)

    def last_search_query(self) -> str:
        return self._search_engine.last_query

    def request_find_bar(self, *, replace: bool, backwards: bool = False, seed: Optional[str] = None) -> None:
        query = seed if seed is not None else self._search_seed_query()
        self.findBarRequested.emit(replace, backwards, query)

    def set_pygments_style(self, style: str) -> None:
        """Update the code-fence highlighting style."""
        try:
            self.highlighter.set_pygments_style(style)
        except Exception:
            pass

    def is_ai_overlay_visible(self) -> bool:
        return self._ai_action_overlay.is_visible()

    def _connect_document_signals(self, document: Optional[QTextDocument]) -> None:
        if document is None:
            self._document_alive = False
            return
        if not self._is_alive(document):
            self._document_alive = False
            return
        document.destroyed.connect(self._on_document_destroyed)
        self._document_alive = True
        layout = document.documentLayout()
        if layout is not None:
            try:
                layout.destroyed.connect(self._on_layout_destroyed)
                self._layout_alive = True
            except Exception:
                pass

    def _on_layout_destroyed(self) -> None:
        self._layout_alive = False

    def _on_document_destroyed(self) -> None:
        self._document_alive = False
        self._push_paint_block()

    def _on_editor_destroyed(self) -> None:
        self._editor_alive = False

    def _on_viewport_destroyed(self) -> None:
        self._viewport_alive = False

    def _is_alive(self, obj) -> bool:
        return bool(obj) and Shiboken.isValid(obj)

    def _push_paint_block(self) -> None:
        """Disable painting (and updates) with nesting support."""
        self._suppress_paint_depth += 1
        if self._suppress_paint_depth == 1:
            self._saved_updates_enabled = self.updatesEnabled()
            try:
                self.setUpdatesEnabled(False)
            except Exception:
                pass
        self._suppress_paint = True

    def _pop_paint_block(self) -> None:
        if self._suppress_paint_depth == 0:
            self._suppress_paint = False
            return
        self._suppress_paint_depth -= 1
        if self._suppress_paint_depth == 0:
            self._suppress_paint = False
            if self._saved_updates_enabled is not None:
                try:
                    self.setUpdatesEnabled(self._saved_updates_enabled)
                except Exception:
                    pass
                self._saved_updates_enabled = None

    def paintEvent(self, event):  # type: ignore[override]
        """Custom paint to draw horizontal rules as visual lines."""
        if os.getenv("SP_DISABLE_HR_OVERLAY", "0") not in ("0", "false", "False", ""):
            super().paintEvent(event)
            return
        if self._suppress_paint or self._suppress_paint_depth:
            return
        if self._vi_paint_in_progress:
            return
        self._vi_paint_in_progress = True
        painter: Optional[QPainter] = None
        try:
            # Guard against paint events firing while the underlying Qt objects are
            # being torn down (e.g., during vault switches). Hitting an invalid
            # document/layout inside QTextEdit's paintEvent can segfault, so we bail
            # out early if anything looks dead.
            if not self._editor_alive or not self._document_alive or not self._is_alive(self):
                return
            document = self.document()
            if not self._is_alive(document):
                return
            layout = document.documentLayout()
            if not self._layout_alive or not self._is_alive(layout):
                return
            viewport = self.viewport()
            if not self._is_alive(viewport):
                return

            super().paintEvent(event)
            if not self._vi_has_painted:
                self._vi_has_painted = True

            try:
                if not self._viewport_alive:
                    try:
                        viewport.destroyed.connect(self._on_viewport_destroyed)
                        self._viewport_alive = True
                    except Exception:
                        return
                painter = QPainter(viewport)
                pen = QPen(QColor("#555555"))
                pen.setWidth(2)
                painter.setPen(pen)
                scroll_bar = self.verticalScrollBar()
                vsb = scroll_bar.value() if self._is_alive(scroll_bar) else 0
                block = document.begin()
                while block.isValid():
                    if not self._document_alive or not self._editor_alive:
                        break
                    if not self._is_alive(document) or not self._is_alive(layout):
                        break
                    if block.text().strip() in ("---", "***", "___"):
                        try:
                            br = layout.blockBoundingRect(block)
                        except RuntimeError as exc:
                            logger.warning("Aborting markdown rule overlay; layout gone: %s", exc)
                            break
                        viewport_height = viewport.height() if self._is_alive(viewport) else 0
                        y = int(br.top() - vsb + br.height() / 2)
                        if 0 <= y <= viewport_height:
                            painter.drawLine(0, y, viewport.width(), y)
                    block = block.next()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Skipping Markdown paint overlay due to error: %s", exc, exc_info=True)
            finally:
                if painter is not None and painter.isActive():
                    painter.end()
        finally:
            self._vi_paint_in_progress = False

    def set_context(self, vault_root: Optional[str], relative_path: Optional[str]) -> None:
        self._vault_root = Path(vault_root) if vault_root else None
        self._current_path = relative_path

    def set_remote_context(
        self,
        remote_mode: bool,
        api_base: Optional[str],
        cache_root: Optional[Path],
        http_client: Optional[httpx.Client],
        auth_prompt: Optional[Callable[[], bool]],
    ) -> None:
        self._remote_mode = bool(remote_mode)
        self._api_base = api_base.rstrip("/") if api_base else None
        self._remote_cache_root = cache_root
        self._http_client = http_client
        self._auth_prompt = auth_prompt

    def setDocument(self, document: QTextDocument) -> None:  # type: ignore[override]
        self._push_paint_block()
        old_document = self.document()
        if old_document is not None:
            try:
                old_document.destroyed.disconnect(self._on_document_destroyed)
            except (TypeError, RuntimeError):
                pass
            old_layout = old_document.documentLayout()
            if old_layout is not None:
                try:
                    old_layout.destroyed.disconnect(self._on_layout_destroyed)
                except (TypeError, RuntimeError):
                    pass
        try:
            super().setDocument(document)
            self._connect_document_signals(document)
        finally:
            self._pop_paint_block()

    def current_relative_path(self) -> Optional[str]:
        return self._current_path

    def set_page_load_logger(self, logger: Optional[PageLoadLogger]) -> None:
        """Attach a page load logger for the next render cycle."""
        # The logger itself knows whether logging is enabled.
        self._page_load_logger = logger

    def set_open_in_window_callback(self, callback: Optional[Callable[[str], None]]) -> None:
        """Provide a handler to open the current page in a separate window (main editor only)."""
        self._open_in_window_callback = callback

    def set_filter_nav_callback(self, callback: Optional[Callable[[str], None]]) -> None:
        """Provide a handler to filter the navigation tree by the current page's subtree."""
        self._filter_nav_callback = callback

    def set_move_text_callback(self, callback: Optional[Callable[[str, str], bool]]) -> None:
        """Provide a handler that appends markdown text to a target page path."""
        self._move_text_callback = callback

    def _selected_display_text(self) -> str:
        cursor = self.textCursor()
        if not cursor.hasSelection():
            return ""
        return cursor.selectedText().replace("\u2029", "\n")

    def _move_selected_text_to_page(self, dest_page_path: str) -> bool:
        """Append selection to dest page (via callback) and replace selection with a link."""
        if not dest_page_path:
            return False
        if self._current_path and dest_page_path == self._current_path:
            return False
        if not self._move_text_callback:
            return False
        display_text = self._selected_display_text()
        if not display_text.strip():
            return False
        markdown_text = self._from_display(display_text)
        try:
            ok = bool(self._move_text_callback(dest_page_path, markdown_text))
        except Exception:
            ok = False
        if not ok:
            return False
        colon = path_to_colon(dest_page_path) or ""
        if not colon:
            return False
        self.insert_link(colon)
        return True

    def _move_text_via_jump_dialog(self) -> None:
        cursor = self.textCursor()
        if not cursor.hasSelection():
            return
        anchor = self._last_context_menu_global_pos
        dialog = JumpToPageDialog(self, compact=True, geometry_key=None, anchor_global_pos=anchor)
        # Opening a modal dialog steals focus from the editor; suppress focusLost so
        # upstream autosave handlers don't write just for showing the dialog.
        self._suppress_focus_lost_once = True
        try:
            result = dialog.exec()
        finally:
            self._suppress_focus_lost_once = False
        if result != QDialog.Accepted:
            return
        dest = dialog.selected_path()
        if not dest:
            return
        self._move_selected_text_to_page(dest)

    def _mark_page_load(self, label: str) -> None:
        if self._page_load_logger:
            self._page_load_logger.mark(label)

    def _complete_page_load_logging(self, label: str) -> None:
        if self._page_load_logger:
            self._page_load_logger.end(label)
            self._page_load_logger = None

    def set_markdown(self, content: str) -> None:
        self._push_paint_block()
        try:
            import time
            from os import getenv
            t0 = time.perf_counter()
            self._mark_page_load("render start")
            if content.endswith('\\n'):
                stripped = content.rstrip('\\n')
                trailing_count = len(content) - len(stripped)
                if trailing_count > 10:
                    content = stripped + '\\n' * 10
            normalized = self._normalize_markdown_images(content)
            t1 = time.perf_counter()
            self._mark_page_load("normalize images")
            display = self._to_display(normalized)
            if display and not display.endswith("\n"):
                display += "\n"
            t2 = time.perf_counter()
            self._mark_page_load("convert to display text")
            self.highlighter.enable_timing(True)
            self.highlighter.reset_timing()
            type(self)._LOAD_GUARD_DEPTH += 1
            self._display_guard = True
            self.setUpdatesEnabled(False)
            self._suppress_vi_cursor = True
            self._suppress_link_scan = True
            self._cursor_events_blocked = True
            cursor_signals_were_connected = getattr(self, "_cursor_signals_connected", False)
            if cursor_signals_were_connected:
                try:
                    self.cursorPositionChanged.disconnect(self._emit_cursor)
                    self.cursorPositionChanged.disconnect(self._maybe_update_vi_cursor)
                    self._cursor_signals_connected = False
                except Exception:
                    pass
            # Remember cursor position so we can restore it after repopulating
            cur = self.textCursor()
            orig_block_num = cur.blockNumber()
            orig_col = cur.position() - cur.block().position()
            def restore_cursor_after_load(display_len: int) -> None:
                try:
                    block_count = self.document().blockCount()
                    if block_count <= 0:
                        return
                    restore_block_num = min(orig_block_num, max(0, block_count - 1))
                    restored_block = self.document().findBlockByNumber(restore_block_num)
                    if not restored_block.isValid():
                        restored_block = self.document().lastBlock()
                    target_pos = restored_block.position() + min(orig_col, max(0, restored_block.length() - 1))
                    new_cursor = self.textCursor()
                    new_cursor.setPosition(min(max(0, target_pos), max(0, display_len)))
                    self.setTextCursor(new_cursor)
                except Exception:
                    pass
            blocker = QSignalBlocker(self)
            try:
                self.document().clear()
                self.textChanged.disconnect(self._enforce_display_symbols)
                self.textChanged.disconnect(self._schedule_heading_outline)
                # Always defer highlighting during load for better performance
                highlighter_disabled = True
                self.highlighter.setDocument(None)
                incremental = False
                batch_ms_total = 0.0
                batches = 0
                if getenv("SP_INCREMENTAL_LOAD") == "1":
                    incremental = True
                    lines = display.splitlines(keepends=True)
                    batch_size = 50
                    buf = []
                    import time as _time
                    for i, line in enumerate(lines):
                        buf.append(line)
                        if len(buf) >= batch_size or i == len(lines) - 1:
                            b0 = _time.perf_counter()
                            self.insertPlainText("".join(buf))
                            b1 = _time.perf_counter()
                            batch_ms_total += (b1 - b0) * 1000.0
                            batches += 1
                            buf = []
                    t3 = time.perf_counter()
                    # Restore cursor after incremental insertion
                    restore_cursor_after_load(len(display))
                else:
                    self.setPlainText(display)
                    t3 = time.perf_counter()
                    # Restore cursor after full setPlainText
                    restore_cursor_after_load(len(display))
                self._mark_page_load("document populated")
                self.textChanged.connect(self._enforce_display_symbols)
                self.textChanged.connect(self._schedule_heading_outline)
                if highlighter_disabled:
                    self.highlighter.setDocument(self.document())
                self.setUpdatesEnabled(True)
                self._render_images(display, time.perf_counter())
                t4 = time.perf_counter()
                self._mark_page_load("render images")
                self._display_guard = False
                self._schedule_heading_outline()
                self._refresh_hr_selections()
                self._apply_scroll_past_end_margin()
            finally:
                del blocker
                self._suppress_vi_cursor = False
                self._suppress_link_scan = False
                QTimer.singleShot(0, lambda: setattr(self, "_cursor_events_blocked", False))
                type(self)._LOAD_GUARD_DEPTH = max(0, type(self)._LOAD_GUARD_DEPTH - 1)
                if cursor_signals_were_connected and not getattr(self, "_cursor_signals_connected", False):
                    try:
                        self.cursorPositionChanged.connect(self._emit_cursor)
                        self.cursorPositionChanged.connect(self._maybe_update_vi_cursor)
                        self._cursor_signals_connected = True
                    except Exception:
                        pass
                self._pop_paint_block()
            t5 = time.perf_counter()
            self._mark_page_load("outline + margin scheduled")
            if _DETAILED_LOGGING:
                print(f"[MD_TIMING] set_markdown breakdown:")
                print(f"  normalize_images: {(t1-t0)*1000:.1f}ms")
                print(f"  to_display: {(t2-t1)*1000:.1f}ms")
                print(f"  setPlainText: {(t3-t2)*1000:.1f}ms")
                print(f"  render_images: {(t4-t3)*1000:.1f}ms (lazy - deferred)")
                print(f"  schedule_outline+margin: {(t5-t4)*1000:.1f}ms")
                print(f"  TOTAL: {(t5-t0)*1000:.1f}ms")
                setPlainText_time_ms = (t3-t2)*1000
                if setPlainText_time_ms > 1000:
                    print(f"[MD_PERF_WARNING] setPlainText took {setPlainText_time_ms:.1f}ms - unusually slow!")
                    print(f"[MD_PERF_WARNING] This may indicate signal cascade issues (highlighter is deferred by default).")
                if incremental:
                    print(
                        f"[MD_TIMING] Incremental batches={batches} cumulative_insert={batch_ms_total:.1f}ms "
                        f"avg_batch={(batch_ms_total/max(batches,1)):.1f}ms"
                    )
                if self.highlighter._timing_blocks:
                    avg = (self.highlighter._timing_total / self.highlighter._timing_blocks) * 1000.0
                    total = self.highlighter._timing_total * 1000.0
                    hit_rate = (self.highlighter._cache_hits / max(1, self.highlighter._cache_hits + self.highlighter._cache_misses)) * 100
                    print(f"[MD_TIMING] Highlighter: blocks={self.highlighter._timing_blocks} total={total:.1f}ms avg={avg:.2f}ms")
                    print(f"[MD_CACHE] hits={self.highlighter._cache_hits} misses={self.highlighter._cache_misses} hit_rate={hit_rate:.1f}% size={len(self.highlighter._block_cache)}")
            self.highlighter.enable_timing(False)
            self._mark_page_load("editor focus ready")
            self.setFocus()
        finally:
            if self._suppress_paint_depth:
                self._pop_paint_block()

    def replace_markdown_in_place(self, content: str) -> None:
        """Replace the full document from markdown without clearing undo history."""
        # Clear highlighter cache when replacing content
        if hasattr(self.highlighter, '_block_cache'):
            self.highlighter._block_cache.clear()
            self.highlighter._cache_access_order.clear()
        if content.endswith("\n"):
            stripped = content.rstrip("\n")
            trailing_count = len(content) - len(stripped)
            if trailing_count > 10:
                content = stripped + "\n" * 10
        normalized = self._normalize_markdown_images(content)
        display = self._to_display(normalized)
        if display and not display.endswith("\n"):
            display += "\n"
        cur = self.textCursor()
        orig_block_num = cur.blockNumber()
        orig_col = cur.position() - cur.block().position()
        self._display_guard = True
        try:
            doc = self.document()
            replace_cursor = QTextCursor(doc)
            replace_cursor.beginEditBlock()
            replace_cursor.select(QTextCursor.Document)
            replace_cursor.insertText(display)
            replace_cursor.endEditBlock()
            try:
                restored_block = self.document().findBlockByNumber(
                    min(orig_block_num, max(0, self.document().blockCount() - 1))
                )
                if restored_block.isValid():
                    target_pos = restored_block.position() + min(
                        orig_col, max(0, restored_block.length() - 1)
                    )
                    new_cursor = self.textCursor()
                    new_cursor.setPosition(min(max(0, target_pos), max(0, len(display))))
                    self.setTextCursor(new_cursor)
            except Exception:
                pass
            self._render_images(display)
        finally:
            self._display_guard = False
        self._schedule_heading_outline()
        self._refresh_hr_selections()
        self._apply_scroll_past_end_margin()

    def apply_title_heading(self, heading_text: str) -> None:
        """Insert or replace the first heading line using display formatting."""
        if not heading_text:
            return
        display_heading = self._to_display(f"# {heading_text}").rstrip("\n")
        doc = self.document()
        block = doc.firstBlock()
        while block.isValid() and not block.text().strip():
            block = block.next()
        self._display_guard = True
        try:
            cursor = QTextCursor(doc)
            cursor.beginEditBlock()
            if not block.isValid():
                cursor.setPosition(0)
                cursor.insertText(f"{display_heading}\n")
            else:
                stripped = block.text().lstrip()
                if stripped and heading_level_from_char(stripped[0]):
                    start = block.position()
                    end = start + max(0, block.length() - 1)
                    cursor.setPosition(start)
                    cursor.setPosition(end, QTextCursor.KeepAnchor)
                    cursor.insertText(display_heading)
                else:
                    cursor.setPosition(block.position())
                    cursor.insertText(f"{display_heading}\n")
            cursor.endEditBlock()
        finally:
            self._display_guard = False
        self._schedule_heading_outline()

    def unload_for_delete(self) -> None:
        """Clear the document safely when the current page is being deleted."""
        blocker = QSignalBlocker(self)
        doc_blocker = QSignalBlocker(self.document())
        try:
            self.setUpdatesEnabled(False)
            try:
                self.textChanged.disconnect(self._enforce_display_symbols)
                self.textChanged.disconnect(self._schedule_heading_outline)
            except Exception:
                pass
            self.document().clear()
            self.clear()
        finally:
            self.setUpdatesEnabled(True)
            try:
                self.textChanged.connect(self._enforce_display_symbols)
                self.textChanged.connect(self._schedule_heading_outline)
            except Exception:
                pass
            del doc_blocker
            del blocker

    def to_markdown(self) -> str:
        markdown = self._doc_to_markdown()
        markdown = self._normalize_markdown_images(markdown)
        # Convert +CamelCase links to colon-style links before saving
        markdown = self._convert_camelcase_links(markdown)
        result = self._from_display(markdown)
        # Guard against QTextDocument fragment drift: if the doc-derived markdown
        # doesn't match the display-derived markdown, prefer the display path,
        # but never drop image markdown that only exists in the document.
        baseline = self._from_display(self.toPlainText())
        if result != baseline:
            result_images = len(IMAGE_PATTERN.findall(result))
            baseline_images = len(IMAGE_PATTERN.findall(baseline))
            if result_images < baseline_images:
                result = baseline
            elif result_images == baseline_images and len(result) < len(baseline):
                result = baseline
        if sys.platform == "win32" and os.getenv("SP_WIN_TRUNC_DEBUG", "0") not in ("0", "false", "False", ""):
            display_text = self.toPlainText()
            def _tail(text: str) -> str:
                tail = text.replace("\u2029", "\n")
                return tail[-200:] if len(tail) > 200 else tail
            mismatch = None
            for idx, (a, b) in enumerate(zip(baseline, result)):
                if a != b:
                    mismatch = idx
                    break
            if mismatch is None and len(baseline) != len(result):
                mismatch = min(len(baseline), len(result))
            print(
                "[StillPoint][WIN_TRUNC_DEBUG] "
                f"display_len={len(display_text)} baseline_len={len(baseline)} result_len={len(result)} "
                f"mismatch_at={mismatch}"
            )
            print(f"[StillPoint][WIN_TRUNC_DEBUG] baseline_tail={_tail(baseline)!r}")
            print(f"[StillPoint][WIN_TRUNC_DEBUG] result_tail={_tail(result)!r}")
        if sys.platform == "win32" and os.getenv("SP_WIN_IMAGE_SAVE_DEBUG", "0") not in ("0", "false", "False", ""):
            doc = self.document()
            cursor = QTextCursor(doc)
            cursor.setPosition(0)
            doc_end = max(0, doc.characterCount() - 1)
            doc_images: list[str] = []
            while cursor.position() < doc_end:
                cursor.setPosition(cursor.position() + 1, QTextCursor.KeepAnchor)
                fmt = cursor.charFormat()
                if fmt.isImageFormat():
                    img_fmt = fmt.toImageFormat()
                    name = img_fmt.property(IMAGE_PROP_ORIGINAL) or img_fmt.name()
                    doc_images.append(str(name))
                cursor.setPosition(cursor.position())
            result_images = IMAGE_PATTERN.findall(result)
            baseline_images = IMAGE_PATTERN.findall(baseline)
            def _img_sample(images: list[tuple[str, str, str]]) -> list[str]:
                sample: list[str] = []
                for alt, path, width in images[:5]:
                    suffix = f"{{width={width}}}" if width else ""
                    sample.append(f"![{alt}]({path}){suffix}")
                return sample
            print(
                "[StillPoint][WIN_IMAGE_SAVE_DEBUG] "
                f"doc_images={len(doc_images)} result_images={len(result_images)} baseline_images={len(baseline_images)}"
            )
            if doc_images:
                print(f"[StillPoint][WIN_IMAGE_SAVE_DEBUG] doc_image_sample={doc_images[:5]}")
            if result_images:
                print(f"[StillPoint][WIN_IMAGE_SAVE_DEBUG] result_image_sample={_img_sample(result_images)}")
            if baseline_images:
                print(f"[StillPoint][WIN_IMAGE_SAVE_DEBUG] baseline_image_sample={_img_sample(baseline_images)}")
        return result

    def _schedule_camel_refresh(self) -> None:
        """DEPRECATED: +CamelCase auto-conversion disabled in Phase 3."""
        # No-op: Phase 3 removed auto-conversion
        pass
    
    def _check_inline_link_trigger(self) -> None:
        """Check if user typed // trigger and show quick link picker."""
        if self._processing_inline_trigger or self._display_guard:
            return
        cursor = self.textCursor()
        pos = cursor.position()

        # Look back 3 characters to check for '//'
        if pos < 3:
            return

        trigger_cursor = QTextCursor(cursor)
        trigger_cursor.setPosition(pos - 3)
        trigger_cursor.setPosition(pos, QTextCursor.KeepAnchor)
        trigger_text = trigger_cursor.selectedText()
        if trigger_text == "[[ ":
            if not config.load_enable_ai_chats():
                return
            self._processing_inline_trigger = True
            try:
                prev_display_guard = self._display_guard
                self._display_guard = True
                trigger_cursor.removeSelectedText()
                self.setTextCursor(trigger_cursor)
                cursor_rect = self.cursorRect()
                anchor_pos = self.mapToGlobal(cursor_rect.bottomLeft())
                self.aiInlinePromptRequested.emit(anchor_pos, trigger_cursor.position())
            finally:
                self._display_guard = prev_display_guard
                self._processing_inline_trigger = False
            return

        # Check if we're inside a [link|label] construct - skip trigger if so
        line_start = cursor.block().position()
        line_text = cursor.block().text()
        pos_in_line = pos - line_start
        
        # Don't trigger if we just completed a link (] followed by space)
        if pos_in_line >= 2 and line_text[pos_in_line - 2:pos_in_line] == "] ":
            return
        
        # Look for unclosed [ before cursor position
        open_bracket = line_text.rfind('[', 0, pos_in_line)
        if open_bracket != -1:
            # Check if there's a matching ] after the [
            close_bracket = line_text.find(']', open_bracket, pos_in_line)
            if close_bracket == -1:
                # We're inside an unclosed [link|label] - don't trigger
                return
        
        cursor.setPosition(pos - 3)
        cursor.setPosition(pos, QTextCursor.KeepAnchor)
        trigger_text = cursor.selectedText()
        
        # Check for // trigger (quick link)
        if trigger_text == "// ":
            # Set guard flag to prevent duplicate processing
            self._processing_inline_trigger = True
            try:
                prev_display_guard = self._display_guard
                self._display_guard = True
                # Remove the trigger and update cursor
                cursor.removeSelectedText()
                self.setTextCursor(cursor)
                # Get cursor position for dialog placement (after removal)
                cursor_rect = self.cursorRect()
                anchor_pos = self.mapToGlobal(cursor_rect.bottomLeft())
                # Show quick link dialog near cursor
                self._trigger_inline_link_insert(anchor_pos)
            finally:
                self._display_guard = prev_display_guard
                # Always clear guard flag
                self._processing_inline_trigger = False
            return
    
    def _trigger_inline_link_insert(self, anchor_pos: QPoint) -> None:
        """Show compact jump dialog near cursor and insert link in [:Page|label] format."""
        # Create and show jump dialog near cursor
        try:
            from .jump_dialog import JumpToPageDialog
            dialog = JumpToPageDialog(
                self, 
                compact=True, 
                geometry_key=None,
                anchor_global_pos=anchor_pos,
                launch_mode="insert_link",
                current_page_path=self._current_path
            )
            if dialog.exec() == QDialog.Accepted:
                selected = dialog.selected_path()
                if selected:
                    # Convert to colon path
                    from .path_utils import path_to_colon
                    colon_path = path_to_colon(selected)
                    # Match Ctrl+L behavior: use target as label unless user provides one.
                    self.insert_link(colon_path)
        except Exception as e:
            logger.warning(f"Failed to show inline link dialog: {e}")

    def _refresh_camel_links(self) -> None:
        """Convert any +CamelCase links in the document and re-render display."""
        if self._display_guard:
            return
        current_text = self.toPlainText()
        # Remember current cursor location (block + column) to restore precisely
        cur = self.textCursor()
        orig_block_num = cur.blockNumber()
        orig_col = cur.position() - cur.block().position()
        storage_text = self._from_display(current_text)
        converted = self._convert_camelcase_links(storage_text)
        if converted == storage_text:
            self._last_camel_trigger = None
            self._last_camel_cursor_pos = None
            return
        # Re-render only the changed blocks instead of resetting the whole document
        display_text = self._to_display(converted)
        current_lines = current_text.splitlines()
        new_lines = display_text.splitlines()
        # If line counts diverge, fall back to full render as a safety net
        if len(current_lines) != len(new_lines):
            self._display_guard = True
            doc = self.document()
            replace_cursor = QTextCursor(doc)
            replace_cursor.beginEditBlock()
            replace_cursor.select(QTextCursor.Document)
            replace_cursor.insertText(display_text)
            replace_cursor.endEditBlock()
            restored_block = self.document().findBlockByNumber(orig_block_num)
            target_pos = restored_block.position() + min(orig_col, max(0, restored_block.length() - 1))
            new_cursor = self.textCursor()
            new_cursor.setPosition(min(target_pos, len(display_text)))
            self.setTextCursor(new_cursor)
            self._position_cursor_after_camel(new_cursor, display_text, orig_block_num, orig_col)
            self._render_images(display_text)
            self._display_guard = False
            self._schedule_heading_outline()
            self._apply_scroll_past_end_margin()
            return

        # Replace only the blocks that changed
        changed_indices = [i for i, (old, new) in enumerate(zip(current_lines, new_lines)) if old != new]
        if not changed_indices:
            self._last_camel_trigger = None
            self._last_camel_cursor_pos = None
            return

        self._display_guard = True
        # Collect blocks once to avoid repeated iteration
        blocks = []
        block = self.document().begin()
        while block.isValid():
            blocks.append(block)
            block = block.next()

        for idx in changed_indices:
            if idx >= len(blocks):
                continue
            block = blocks[idx]
            start = block.position()
            text_len = len(block.text())
            tc = QTextCursor(self.document())
            tc.setPosition(start)
            tc.setPosition(start + text_len, QTextCursor.KeepAnchor)
            tc.insertText(new_lines[idx])

        # Restore cursor to original line/column, clamped to new line length
        restored_block = self.document().findBlockByNumber(orig_block_num)
        target_pos = restored_block.position() + min(orig_col, max(0, restored_block.length() - 1))
        new_cursor = self.textCursor()
        new_cursor.setPosition(min(target_pos, len(display_text)))
        self.setTextCursor(new_cursor)
        self._position_cursor_after_camel(new_cursor, display_text, orig_block_num, orig_col)
        self._render_images(display_text)
        self._display_guard = False
        self._schedule_heading_outline()
        self._apply_scroll_past_end_margin()

    def _position_cursor_after_camel(
        self,
        cursor: QTextCursor,
        display_text: str,
        orig_block_num: int | None = None,
        orig_col: int | None = None,
    ) -> None:
        """Place cursor after the converted link, keeping typing flow natural."""
        trigger = self._last_camel_trigger
        origin_pos = self._last_camel_cursor_pos
        self._last_camel_trigger = None
        self._last_camel_cursor_pos = None

        # Resolve an absolute origin position
        abs_origin = None
        if orig_block_num is not None and orig_col is not None:
            block = self.document().findBlockByNumber(orig_block_num)
            abs_origin = block.position() + max(0, min(orig_col, len(block.text())))
        elif origin_pos is not None:
            abs_origin = origin_pos
        if abs_origin is None:
            return

        # Pick the appropriate link based on trigger type
        chosen = None
        if trigger == "enter":
            # For Enter key: cursor is on new line, find the link ending just before the newline
            # (the link on the previous line that was just typed)
            best_before = None
            for m in WIKI_LINK_DISPLAY_PATTERN.finditer(display_text):
                if m.end() < abs_origin:
                    best_before = m
                elif m.start() >= abs_origin:
                    break
            chosen = best_before
        else:
            # For Space key: find the link at/after the origin
            fallback_before = None
            for m in WIKI_LINK_DISPLAY_PATTERN.finditer(display_text):
                if m.start() <= abs_origin <= m.end():
                    chosen = m
                    break
                if m.start() > abs_origin and chosen is None:
                    chosen = m
                    break
                fallback_before = m
            if chosen is None and fallback_before is not None:
                chosen = fallback_before
        if not chosen:
            return

        target_pos = chosen.end()
        if trigger == "space":
            if target_pos < len(display_text) and display_text[target_pos] == " ":
                target_pos += 1
            else:
                cursor.beginEditBlock()
                cursor.setPosition(target_pos)
                cursor.insertText(" ")
                cursor.endEditBlock()
                target_pos += 1
        elif trigger == "enter":
            if target_pos < len(display_text) and display_text[target_pos] == "\n":
                target_pos += 1
            else:
                cursor.beginEditBlock()
                cursor.setPosition(target_pos)
                cursor.insertText("\n")
                cursor.endEditBlock()
                target_pos += 1
            
            # When Enter is pressed after +CamelCase, activate the link immediately
            # The chosen match has 'link' and 'label' groups from WIKI_LINK_DISPLAY_PATTERN
            link_target = chosen.group('link')
            if link_target:
                print(f"[CamelCase] Activating link after Enter: {link_target}")
                # Schedule link activation after the cursor is positioned
                QTimer.singleShot(0, lambda target=link_target: self.linkActivated.emit(target))

        cursor.setPosition(min(target_pos, len(display_text)))
        self.setTextCursor(cursor)
        # Ensure we are not left inside hidden link sentinels for vi/arrow navigation
        try:
            self._handle_link_boundary_navigation(Qt.Key_Right)
        except Exception:
            pass

    def set_font_point_size(self, size: int) -> None:
        # Clamp to a sensible, positive point size to avoid Qt warnings
        try:
            safe_size = max(6, int(size))
        except Exception:
            safe_size = 13
        font = self.font()
        font.setPointSize(safe_size)
        self.setFont(font)
        try:
            self.setTabStopDistance(4 * self.fontMetrics().horizontalAdvance(" "))
        except Exception:
            pass
        try:
            if hasattr(self, "highlighter"):
                self.highlighter.set_base_font_size(float(safe_size))
        except Exception:
            pass

    def begin_dialog_block(self) -> None:
        """Completely freeze editor input and interaction while a modal dialog is open."""
        self._dialog_block_input = True
        self.setReadOnly(True)
        self.setEnabled(False)
        self.setContextMenuPolicy(Qt.NoContextMenu)
        self.viewport().setCursor(Qt.ArrowCursor)
        # Optionally, clear selection to avoid visual confusion
        cursor = self.textCursor()
        cursor.clearSelection()
        self.setTextCursor(cursor)

    def end_dialog_block(self) -> None:
        """Re-enable editor input and interaction after a modal dialog closes."""
        self._dialog_block_input = False
        self.setReadOnly(self._read_only_mode)
        self.setEnabled(True)
        self.setContextMenuPolicy(Qt.DefaultContextMenu)
        self.viewport().unsetCursor()

    def insert_link(self, colon_path: str, link_name: str | None = None, *, surround_with_spaces: bool = False) -> None:
        """Insert a link at the current cursor position in display format.
        
        Inserts directly as sentinel+target+sentinel+label+sentinel (display format).
        When surround_with_spaces is True, adds missing spaces around the link to avoid
        embedding sentinels in the middle of a word.
        """
        if not colon_path:
            return
        
        is_http_url = colon_path.startswith(("http://", "https://"))
        target = self._normalize_external_link(colon_path) if is_http_url else ensure_root_colon_link(colon_path)
        
        # Determine the display label
        label = ""
        if link_name and link_name.strip():
            candidate = link_name.strip()
            match_left = candidate.lstrip(":/")
            target_left = target.lstrip(":/")
            if match_left != target_left:
                label = candidate
        
        # If no custom label, use target or a short label based on preference.
        if not label:
            if (not is_http_url) and config.load_prefer_short_links():
                trimmed = target.lstrip(":")
                label = (trimmed.split(":")[-1] if trimmed else target)
            else:
                label = target
        
        # Insert directly in display format: sentinel + target + sentinel + label + sentinel
        display_link = f"{LINK_SENTINEL}{target}{LINK_SENTINEL}{label}{LINK_SENTINEL}"
        
        cursor = self.textCursor()
        prefix = ""
        suffix = ""
        if surround_with_spaces and not cursor.hasSelection():
            text = self.toPlainText()
            pos = cursor.position()
            prev_char = text[pos - 1] if pos > 0 else ""
            next_char = text[pos] if pos < len(text) else ""
            if prev_char and not prev_char.isspace():
                prefix = " "
            if next_char and not next_char.isspace() and next_char not in ".,;:!?)]}":
                suffix = " "

        cursor.beginEditBlock()
        cursor.insertText(prefix + display_link + suffix)
        cursor.endEditBlock()
        
        # Cursor is now positioned right after the link (after the closing sentinel)
        # No need to reposition - insertText leaves cursor at the right place
        
        # Mark document as modified and trigger rehighlighting
        self.document().setModified(True)
        
        # Force rehighlight of the affected block to apply sentinel formatting
        if self.highlighter:
            block = cursor.block()
            if block.isValid():
                self.highlighter.rehighlightBlock(block)

    def _is_on_checkbox(self, cursor) -> bool:
        """Check if the cursor is positioned on a checkbox character."""
        block = cursor.block()
        text = block.text()
        rel_pos = cursor.position() - block.position()
        
        # Check for symbol checkboxes (☐ ☑)
        symbol_match = re.match(r"^(?P<indent>\s*)(?P<box>[☐☑])", text)
        if symbol_match:
            indent_len = len(symbol_match.group("indent") or "")
            # Cursor must be on or immediately after the checkbox symbol
            return rel_pos <= indent_len + 1
        
        # Check for markdown checkboxes - [ ] or - [x]
        md_match = re.match(r"^(?P<indent>\s*)(?P<bullet>[-*])\s*\[(?P<state>[ xX])\]", text)
        if md_match:
            # Cursor must be within the checkbox portion: [ ] or [x]
            return md_match.start("state") <= rel_pos <= md_match.end("state")
        
        return False

    def _toggle_task_marker(self, block, rel_pos: int | None = None) -> bool:
        text = block.text()
        symbol_match = re.match(r"^(?P<indent>\s*)(?P<box>[☐☑])", text)
        if symbol_match:
            indent = symbol_match.group("indent") or ""
            if rel_pos is not None and rel_pos > len(indent) + 1:
                return False
            pos = block.position() + len(indent)
            new_symbol = "☑" if symbol_match.group("box") == "☐" else "☐"
            line_cursor = QTextCursor(self.document())
            line_cursor.setPosition(pos)
            line_cursor.setPosition(pos + 1, QTextCursor.KeepAnchor)
            line_cursor.insertText(new_symbol)
            return True

        md_match = re.match(r"^(?P<indent>\s*)(?P<bullet>[-*])\s*\[(?P<state>[ xX])\]", text)
        if not md_match:
            return False
        if rel_pos is not None and rel_pos > md_match.end():
            return False
        state = md_match.group("state") or " "
        new_state = "x" if state.strip().lower() != "x" else " "
        pos = block.position() + md_match.start("state")
        line_cursor = QTextCursor(self.document())
        line_cursor.setPosition(pos)
        line_cursor.setPosition(pos + 1, QTextCursor.KeepAnchor)
        line_cursor.insertText(new_state)
        return True

    def toggle_task_state(self) -> None:
        cursor = self.textCursor()
        initial_position = cursor.position()
        block = cursor.block()
        if self._toggle_task_marker(block):
            self._restore_cursor_position(initial_position)

    def insertFromMimeData(self, source: QMimeData) -> None:  # type: ignore[override]
        # 1) Images → save and embed
        if source.hasImage() and self._vault_root and self._current_path:
            image = source.imageData()
            if isinstance(image, QImage):
                if self._remote_mode:
                    filename = self._next_remote_paste_image_name()
                    payload = self._encode_image_png(image)
                    if filename and payload and self._upload_remote_bytes(filename, payload):
                        self._cache_remote_bytes(filename, payload)
                        self._insert_image_from_path(filename, alt=Path(filename).stem)
                        self.imageSaved.emit(filename)
                        return
                saved = self._save_image(image)
                if saved:
                    self._insert_image_from_path(saved.name, alt=saved.stem)
                    self.imageSaved.emit(saved.name)
                    return

        # 2) Rich HTML → prefer plain text if available to avoid style noise
        if source.hasHtml() and source.hasText():
            plain = source.text()
            if plain:
                self.textCursor().insertText(plain)
                return

        # 3) Rich HTML → markdown (avoid pasting styled fragments)
        if source.hasHtml():
            html = source.html()
            plain_from_html = self._html_to_plaintext_with_links(html)
            if not plain_from_html and source.hasText():
                plain_from_html = source.text()
            if plain_from_html:
                self.textCursor().insertText(plain_from_html)
                if "[" in plain_from_html and "|" in plain_from_html:
                    self._refresh_display()
                return

        # 4) Default paste without auto-link munging
        super().insertFromMimeData(source)

    def _html_to_plaintext_with_links(self, html: str) -> str:
        """Strip HTML to plain text, converting anchors to [url|label] links."""
        if not html:
            return ""

        class _PlainLinkParser(HTMLParser):
            block_tags = {
                "p", "div", "section", "article", "header", "footer",
                "blockquote", "pre", "li", "ul", "ol",
                "table", "tr", "td", "th",
                "h1", "h2", "h3", "h4", "h5", "h6",
            }

            def __init__(self) -> None:
                super().__init__()
                self.parts: list[str] = []
                self._link_href: Optional[str] = None
                self._link_text: list[str] = []

            def _last_char(self) -> str:
                return self.parts[-1][-1] if self.parts else ""

            def _ensure_newline(self) -> None:
                if self._last_char() != "\n":
                    self.parts.append("\n")

            def handle_starttag(self, tag: str, attrs) -> None:
                tag = tag.lower()
                if tag == "a":
                    self._link_href = dict(attrs).get("href", "")
                    self._link_text = []
                    return
                if tag == "br":
                    self._ensure_newline()
                    return
                if tag in self.block_tags:
                    self._ensure_newline()
                    if tag == "li":
                        self.parts.append("- ")

            def handle_endtag(self, tag: str) -> None:
                tag = tag.lower()
                if tag == "a":
                    label = "".join(self._link_text).strip()
                    href = self._link_href or ""
                    if href:
                        link_target = self._normalize_external_link(href)
                        display = label or ""
                        self.parts.append(f"[{link_target}|{display}]")
                    else:
                        self.parts.append(label)
                    self._link_href = None
                    self._link_text = []
                    return
                if tag in self.block_tags:
                    self._ensure_newline()

            def handle_data(self, data: str) -> None:
                if self._link_href is not None:
                    self._link_text.append(data)
                else:
                    self.parts.append(data)

        parser = _PlainLinkParser()
        try:
            parser.feed(html)
            parser.close()
        except Exception:
            return ""

        text = "".join(parser.parts)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip("\n")

    def _normalize_external_link(self, link: str) -> str:
        """Preserve full external links while stripping whitespace and hidden sentinels."""
        text = (link or "").strip()
        if LINK_SENTINEL in text:
            text = text.split(LINK_SENTINEL, 1)[0]
        return text

    def _wrap_plain_http_links(self, text: str) -> str:
        """Convert bare HTTP(S) URLs into wiki format [url|] to enable sentinel rendering."""
        if not text or "http" not in text:
            return text

        pattern = re.compile(rf"(?<!\[)(?<!\()(https?://[^\s<>\[\]\(\){LINK_SENTINEL}]+)")

        def repl(match: re.Match[str]) -> str:
            url = match.group(1)
            normalized = self._normalize_external_link(url)
            return f"[{normalized}|]"

        return pattern.sub(repl, text)

    def _has_markdown_syntax(self, text: str) -> bool:
        """Heuristic to decide if text is markdown (headings, lists, code, etc.)."""
        if not text:
            return False
        patterns = [
            r"^\s{0,3}#{1,6}\s+\S",           # headings
            r"^\s{0,3}[-*+]\s+\S",            # bullets
            r"^\s{0,3}\d+\.\s+\S",            # ordered list
            r"^\s{0,3}>\s?\S",                # blockquote
            r"```",                           # fenced code
            r"~~",                            # strikethrough markers
            r"`[^`]+`",                       # inline code
            r"\*\*[^*]+\*\*",                 # bold
            r"\*[^\s][^*]*\*",                # italic
            r"\[([^\]]+)\]\([^)]+\)",         # link
            r"^\s{0,3}[-*_]{3,}\s*$",         # horizontal rule
        ]
        for pat in patterns:
            if re.search(pat, text, re.MULTILINE):
                return True
        return False

    def _save_image(self, image: QImage) -> Optional[Path]:
        """Save a pasted image next to the current page and return its absolute Path.

        Images are stored in the same folder as the current page
        using sequential names like paste_image_001.png, paste_image_002.png, etc.
        """
        if not (self._vault_root and self._current_path):
            return None
        rel_file_path = self._current_path.lstrip("/")
        folder = (self._vault_root / rel_file_path).resolve().parent
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            return None
        index = 1
        while True:
            candidate = folder / f"paste_image_{index:03d}.png"
            if not candidate.exists():
                break
            index += 1
        if image.save(str(candidate), "PNG"):
            return candidate
        return None

    def _encode_image_png(self, image: QImage) -> Optional[bytes]:
        data = QByteArray()
        buffer = QBuffer(data)
        if not buffer.open(QBuffer.WriteOnly):
            return None
        try:
            if not image.save(buffer, "PNG"):
                return None
        finally:
            buffer.close()
        return bytes(data)

    def _remote_page_key(self) -> Optional[str]:
        if not self._current_path:
            return None
        if self._current_path.startswith("/"):
            return self._current_path
        return f"/{self._current_path}"

    def _list_remote_attachment_names(self) -> set[str]:
        if not self._http_client:
            return set()
        page_key = self._remote_page_key()
        if not page_key:
            return set()
        try:
            resp = self._http_client.get("/files/", params={"page_path": page_key})
            if resp.status_code == 401 and self._auth_prompt:
                if self._auth_prompt():
                    resp = self._http_client.get("/files/", params={"page_path": page_key})
            resp.raise_for_status()
            payload = resp.json()
            attachments = payload.get("attachments", [])
        except httpx.HTTPError:
            return set()
        names: set[str] = set()
        if isinstance(attachments, list):
            for entry in attachments:
                if not isinstance(entry, dict):
                    continue
                attachment_path = entry.get("attachment_path") or entry.get("stored_path")
                if attachment_path:
                    names.add(Path(str(attachment_path)).name)
        return names

    def _unique_remote_name(self, desired: str) -> str:
        existing = self._list_remote_attachment_names()
        if desired not in existing:
            return desired
        base = Path(desired)
        for idx in itertools.count(1):
            candidate = f"{base.stem} ({idx}){base.suffix}"
            if candidate not in existing:
                return candidate
        return desired

    def _next_remote_paste_image_name(self) -> Optional[str]:
        existing = self._list_remote_attachment_names()
        pattern = re.compile(r"^paste_image_(\d{3})\.png$", re.IGNORECASE)
        highest = 0
        for name in existing:
            match = pattern.match(name)
            if match:
                try:
                    highest = max(highest, int(match.group(1)))
                except ValueError:
                    continue
        return f"paste_image_{highest + 1:03d}.png"

    def _upload_remote_bytes(self, filename: str, payload: bytes) -> bool:
        if not self._http_client:
            return False
        page_key = self._remote_page_key()
        if not page_key:
            return False
        try:
            resp = self._http_client.post(
                "/files/attach",
                data={"page_path": page_key},
                files={"files": (filename, payload, "application/octet-stream")},
            )
            if resp.status_code == 401 and self._auth_prompt:
                if self._auth_prompt():
                    resp = self._http_client.post(
                        "/files/attach",
                        data={"page_path": page_key},
                        files={"files": (filename, payload, "application/octet-stream")},
                    )
            resp.raise_for_status()
            return True
        except httpx.HTTPError:
            return False

    def _upload_remote_file(self, filename: str, file_path: Path) -> bool:
        if not self._http_client:
            return False
        page_key = self._remote_page_key()
        if not page_key:
            return False
        try:
            with file_path.open("rb") as handle:
                resp = self._http_client.post(
                    "/files/attach",
                    data={"page_path": page_key},
                    files={"files": (filename, handle, "application/octet-stream")},
                )
                if resp.status_code == 401 and self._auth_prompt:
                    if self._auth_prompt():
                        try:
                            handle.seek(0)
                        except OSError:
                            pass
                        resp = self._http_client.post(
                            "/files/attach",
                            data={"page_path": page_key},
                            files={"files": (filename, handle, "application/octet-stream")},
                        )
                resp.raise_for_status()
            return True
        except httpx.HTTPError:
            return False
        except OSError:
            return False

    def _cache_remote_bytes(self, raw_path: str, payload: bytes) -> None:
        if not self._remote_cache_root:
            return
        virtual_path = self._virtual_image_path(raw_path)
        if not virtual_path:
            return
        cache_path = (self._remote_cache_root / "attachments" / virtual_path.lstrip("/")).resolve()
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_bytes(payload)
        except OSError:
            return
    
    
    # (Removed old _copy_link_to_location; newer implementation exists later in file)

    def style_operations(self) -> list[tuple[str, QKeySequence, Callable[[], None], str]]:
        """Return available inline style operations and their handlers."""
        return [
            ("Bold", QKeySequence("Ctrl+B"), self._toggle_bold, "Toggle bold (**text**)"),
            ("Italic", QKeySequence("Ctrl+I"), self._toggle_italic, "Toggle italic (*text*)"),
            ("Strikethrough", QKeySequence("Ctrl+K"), self._toggle_strikethrough, "Toggle strikethrough (~~text~~)"),
            ("Highlight", QKeySequence("Ctrl+U"), self._toggle_highlight, "Toggle highlight (==text==)"),
            ("Verbatim", QKeySequence("Ctrl+T"), self._toggle_verbatim, "Wrap selection in backticks"),
            ("Heading 1", QKeySequence("Ctrl+1"), lambda: self._apply_heading_level(1), "Convert line to H1 (#)"),
            ("Heading 2", QKeySequence("Ctrl+2"), lambda: self._apply_heading_level(2), "Convert line to H2 (##)"),
            ("Heading 3", QKeySequence("Ctrl+3"), lambda: self._apply_heading_level(3), "Convert line to H3 (###)"),
            ("Heading 4", QKeySequence("Ctrl+4"), lambda: self._apply_heading_level(4), "Convert line to H4 (####)"),
            ("Heading 5", QKeySequence("Ctrl+5"), lambda: self._apply_heading_level(5), "Convert line to H5 (#####)"),
            ("Remove Heading", QKeySequence("Ctrl+7"), self._remove_heading, "Strip heading markers on this line"),
            ("Bullet List", QKeySequence(), lambda: self._apply_list_style("bullet"), "Turn selection into bullet list"),
            ("Dash List", QKeySequence(), lambda: self._apply_list_style("dash"), "Turn selection into dash list"),
            ("Checkbox List", QKeySequence(), lambda: self._apply_list_style("task"), "Turn selection into checkbox list"),
            ("Clear List", QKeySequence(), lambda: self._apply_list_style("clear"), "Remove list markers from selection"),
            ("Clear Formatting", QKeySequence("Ctrl+9"), self._clear_inline_formatting, "Restore selection to plain text"),
        ]

    def _toggle_markdown_format(self, prefix: str, suffix: str = None) -> None:
        """Toggle markdown formatting around selected text or word at cursor.
        
        Args:
            prefix: The markdown prefix (e.g., '**' for bold)
            suffix: The markdown suffix (defaults to prefix if None)
        """
        if suffix is None:
            suffix = prefix
        
        cursor = self.textCursor()
        
        # If no selection, select the word under cursor
        if not cursor.hasSelection():
            cursor.select(QTextCursor.WordUnderCursor)
        
        selected_text = cursor.selectedText()
        if not selected_text:
            return
        selected = selected_text.replace("\u2029", "\n")
        prefix_len = len(prefix)
        suffix_len = len(suffix)

        # Multi-line selection: apply/remove per line
        lines = selected.split("\n")
        if len(lines) > 1:
            def line_wrapped(line: str) -> bool:
                return len(line) >= prefix_len + suffix_len and line.startswith(prefix) and line.endswith(suffix)

            non_empty_lines = [ln for ln in lines if ln]
            all_wrapped = bool(non_empty_lines) and all(line_wrapped(line) for line in non_empty_lines)
            new_lines: list[str] = []
            if all_wrapped:
                for line in lines:
                    if line and line_wrapped(line):
                        new_lines.append(line[prefix_len : len(line) - suffix_len])
                    else:
                        new_lines.append(line)
            else:
                for line in lines:
                    if not line:
                        new_lines.append(line)
                    else:
                        new_lines.append(f"{prefix}{line}{suffix}")
            new_text = "\n".join(new_lines)
            start = cursor.selectionStart()
            cursor.beginEditBlock()
            cursor.insertText(new_text)
            cursor.endEditBlock()
            new_cursor = self.textCursor()
            new_cursor.setPosition(start)
            new_cursor.setPosition(start + len(new_text), QTextCursor.KeepAnchor)
            self.setTextCursor(new_cursor)
            return

        start = cursor.selectionStart()
        end = cursor.selectionEnd()
        doc_text = self.toPlainText()

        already_wrapped = False
        if start >= prefix_len and end + suffix_len <= len(doc_text):
            before = doc_text[start - prefix_len : start]
            after = doc_text[end : end + suffix_len]
            if before == prefix and after == suffix:
                already_wrapped = True

        cursor.beginEditBlock()

        if already_wrapped:
            cursor.setPosition(end)
            cursor.movePosition(QTextCursor.Right, QTextCursor.KeepAnchor, suffix_len)
            cursor.removeSelectedText()

            cursor.setPosition(start - prefix_len)
            cursor.movePosition(QTextCursor.Right, QTextCursor.KeepAnchor, prefix_len)
            cursor.removeSelectedText()

            cursor.setPosition(start - prefix_len)
            cursor.setPosition(start - prefix_len + len(selected_text), QTextCursor.KeepAnchor)
        else:
            cursor.setPosition(start)
            cursor.setPosition(end, QTextCursor.KeepAnchor)
            wrapped_text = f"{prefix}{selected_text}{suffix}"
            cursor.insertText(wrapped_text)

            cursor.setPosition(start + prefix_len)
            cursor.setPosition(start + prefix_len + len(selected_text), QTextCursor.KeepAnchor)

        cursor.endEditBlock()
        self.setTextCursor(cursor)
    
    def _toggle_bold(self) -> None:
        """Toggle bold formatting (**text**)."""
        cursor = self.textCursor()
        selected = cursor.selectedText()
        
        # Check if already italic with * - if so, upgrade to bold+italic
        if selected and len(selected) >= 2:
            if selected[0] == '*' and selected[-1] == '*' and not (selected.startswith('**') or selected.startswith('***')):
                # Already italic with *, upgrade to ***
                cursor.beginEditBlock()
                new_text = f"**{selected}*"
                cursor.insertText(new_text)
                cursor.endEditBlock()
                return
        
        self._toggle_markdown_format('**')
    
    def _toggle_italic(self) -> None:
        """Toggle italic formatting (*text*)."""
        cursor = self.textCursor()
        selected = cursor.selectedText()
        
        # Check if already bold with ** - if so, upgrade to bold+italic
        if selected and len(selected) >= 4:
            if selected.startswith('**') and selected.endswith('**'):
                # Already bold, upgrade to ***
                cursor.beginEditBlock()
                new_text = f"*{selected}*"
                cursor.insertText(new_text)
                cursor.endEditBlock()
                return
        
        self._toggle_markdown_format('*')
    
    def _toggle_strikethrough(self) -> None:
        """Toggle strikethrough formatting (~~text~~)."""
        self._toggle_markdown_format('~~')
    
    def _toggle_highlight(self) -> None:
        """Toggle highlight formatting (==text==)."""
        self._toggle_markdown_format('==')

    def _toggle_verbatim(self) -> None:
        """Wrap selection in backticks for inline code."""
        self._toggle_markdown_format('`')

    def _strip_heading(self, text: str) -> tuple[str, str]:
        """Return (indent, content) without any heading markers/sentinels."""
        stripped = text.lstrip(" \t")
        indent = text[: len(text) - len(stripped)]
        if not stripped:
            return indent, ""
        level = heading_level_from_char(stripped[0])
        if level:
            return indent, stripped[1:].lstrip()
        if stripped.startswith("#"):
            hashes = len(stripped) - len(stripped.lstrip("#"))
            return indent, stripped[hashes:].lstrip()
        return indent, stripped

    def _apply_heading_level(self, level: int) -> None:
        """Set current line to heading level (1-5)."""
        level = max(1, min(level, HEADING_MAX_LEVEL))
        cursor = self.textCursor()
        block = cursor.block()
        if not block.isValid():
            return
        text = block.text()
        indent, content = self._strip_heading(text)
        sentinel = heading_sentinel(level)
        new_line = f"{indent}{sentinel}{content}"
        cursor.beginEditBlock()
        line_cursor = QTextCursor(block)
        line_cursor.select(QTextCursor.LineUnderCursor)
        line_cursor.insertText(new_line)
        new_pos = block.position() + len(indent) + len(sentinel)
        cursor.setPosition(min(new_pos, block.position() + len(new_line)))
        cursor.endEditBlock()
        self.setTextCursor(cursor)
        self._pending_heading_block_num = None
        self._pending_heading_level = None
        self._schedule_heading_outline()

    def _remove_heading(self) -> None:
        """Remove heading markers from the current line."""
        cursor = self.textCursor()
        block = cursor.block()
        if not block.isValid():
            return
        text = block.text()
        indent, content = self._strip_heading(text)
        new_line = indent + content
        cursor.beginEditBlock()
        line_cursor = QTextCursor(block)
        line_cursor.select(QTextCursor.LineUnderCursor)
        line_cursor.insertText(new_line)
        cursor.setPosition(block.position() + len(indent))
        cursor.endEditBlock()
        self.setTextCursor(cursor)
        self._pending_heading_block_num = None
        self._pending_heading_level = None
        self._schedule_heading_outline()

    def _prepare_heading_edit_on_input(self, cursor: QTextCursor) -> bool:
        """Strip heading sentinel for editing and remember prior heading level."""
        block = cursor.block()
        if not block.isValid():
            return False
        text = block.text()
        stripped = text.lstrip(" \t")
        if not stripped:
            return False
        indent = text[: len(text) - len(stripped)]
        level = heading_level_from_char(stripped[0])
        if not level:
            return False
        post_sentinel = stripped[1:]
        space_count = len(post_sentinel) - len(post_sentinel.lstrip())
        content = post_sentinel.lstrip()
        new_line = indent + content
        if new_line == text:
            return False

        line_start = block.position()

        def _adjust_rel(rel: int) -> int:
            if rel <= len(indent):
                return rel
            content_start = len(indent) + 1 + space_count
            if rel > content_start:
                return max(len(indent), rel - (1 + space_count))
            return len(indent)

        if cursor.hasSelection():
            sel_start = cursor.selectionStart()
            sel_end = cursor.selectionEnd()
            rel_start = max(0, sel_start - line_start)
            rel_end = max(0, sel_end - line_start)
            new_rel_start = _adjust_rel(rel_start)
            new_rel_end = _adjust_rel(rel_end)
        else:
            rel_pos = max(0, cursor.position() - line_start)
            new_rel_pos = _adjust_rel(rel_pos)

        cursor.beginEditBlock()
        line_cursor = QTextCursor(block)
        line_cursor.select(QTextCursor.LineUnderCursor)
        line_cursor.insertText(new_line)
        new_block = self.document().findBlock(line_start)
        new_len = max(0, new_block.length() - 1)
        new_cursor = self.textCursor()
        if cursor.hasSelection():
            new_abs_start = line_start + min(new_rel_start, new_len)
            new_abs_end = line_start + min(new_rel_end, new_len)
            new_cursor.setPosition(new_abs_start)
            new_cursor.setPosition(new_abs_end, QTextCursor.KeepAnchor)
        else:
            new_abs = line_start + min(new_rel_pos, new_len)
            new_cursor.setPosition(new_abs)
        self.setTextCursor(new_cursor)
        self._pending_heading_block_num = new_block.blockNumber()
        self._pending_heading_level = level
        cursor.endEditBlock()
        self._schedule_heading_outline()
        return True

    def _selected_blocks(self) -> list:
        """Return all blocks touched by the selection (or current line)."""
        cursor = self.textCursor()
        if not cursor.hasSelection():
            block = cursor.block()
            return [block] if block.isValid() else []
        start = cursor.selectionStart()
        end = cursor.selectionEnd()
        doc = self.document()
        start_block = doc.findBlock(start)
        end_block = doc.findBlock(max(start, end - 1))
        if not start_block.isValid() or not end_block.isValid():
            return []
        blocks = []
        block = start_block
        while block.isValid():
            blocks.append(block)
            if block == end_block:
                break
            block = block.next()
        return blocks

    def _split_list_content(self, text: str) -> tuple[str, str]:
        """Return indent and content stripped of list markers."""
        indent = text[: len(text) - len(text.lstrip(" \t"))]
        kind, _, remainder = self._list_line_info(text)
        content = remainder
        if kind == "bullet":
            if content.startswith(("• ", "* ")):
                content = content[2:]
        elif kind == "dash":
            if content.startswith("- "):
                content = content[2:]
        elif kind == "task":
            if content.startswith(("☐ ", "☑ ")):
                content = content[2:]
            else:
                m = re.match(r"[-*]\s*\[[ xX]\]\s*(.*)", content)
                if m:
                    content = m.group(1)
        content = content.lstrip()
        return indent, content

    def _apply_list_style(self, list_type: str) -> None:
        """Apply list style to selection: bullet, dash, task, or clear."""
        blocks = self._selected_blocks()
        if not blocks:
            return
        cursor = self.textCursor()
        first_pos = blocks[0].position()
        last_block = blocks[-1]
        cursor.beginEditBlock()
        for block in blocks:
            text = block.text()
            indent, content = self._split_list_content(text)
            if list_type == "clear":
                new_line = indent + content
            elif list_type == "bullet":
                new_line = f"{indent}• {content}"
            elif list_type == "dash":
                new_line = f"{indent}- {content}"
            else:
                new_line = f"{indent}☐ {content}"
            line_cursor = QTextCursor(block)
            line_cursor.select(QTextCursor.LineUnderCursor)
            line_cursor.insertText(new_line)
            self._refresh_hanging_indent(block, new_line)
        cursor.endEditBlock()
        end_pos = last_block.position() + max(0, last_block.length() - 1)
        new_cursor = self.textCursor()
        new_cursor.setPosition(first_pos)
        new_cursor.setPosition(end_pos, QTextCursor.KeepAnchor)
        self.setTextCursor(new_cursor)
        self._schedule_heading_outline()

    def _strip_task_tags(self, text: str) -> str:
        """Remove @tags from the given text."""
        cleaned = re.sub(r"(?<![\\w.+-])@[A-Za-z0-9_]+", "", text)
        return re.sub(r"\\s{2,}", " ", cleaned).strip()

    def _apply_escape_selection_transform(self) -> bool:
        """Apply Esc behavior for a selection in vi mode."""
        blocks = self._selected_blocks()
        if not blocks:
            return False
        cursor = self.textCursor()
        first_pos = blocks[0].position()
        last_block = blocks[-1]
        has_task = False
        has_bullet = False
        has_dash = False
        for block in blocks:
            text = block.text()
            kind, _, _ = self._list_line_info(text)
            if kind == "task":
                has_task = True
            elif kind == "bullet":
                has_bullet = True
            elif kind == "dash":
                has_dash = True
        if not (has_task or has_bullet or has_dash):
            return False

        if has_task:
            target = "bullet"
            strip_tags = True
        else:
            target = "clear"
            strip_tags = False

        changed = False
        cursor.beginEditBlock()
        for block in blocks:
            text = block.text()
            indent, content = self._split_list_content(text)
            if strip_tags:
                content = self._strip_task_tags(content)
            if target == "bullet":
                new_line = f"{indent}• {content}" if content else f"{indent}• "
            else:
                new_line = indent + content
            line_cursor = QTextCursor(block)
            line_cursor.select(QTextCursor.LineUnderCursor)
            line_cursor.insertText(new_line)
            changed = True
        cursor.endEditBlock()
        if not changed:
            return False
        end_pos = last_block.position() + max(0, last_block.length() - 1)
        new_cursor = self.textCursor()
        new_cursor.setPosition(first_pos)
        new_cursor.setPosition(end_pos, QTextCursor.KeepAnchor)
        self.setTextCursor(new_cursor)
        self._schedule_heading_outline()
        return True

    def _clear_inline_formatting(self) -> None:
        """Remove common markdown markers from the selection."""
        cursor = self.textCursor()
        if not cursor.hasSelection():
            cursor.select(QTextCursor.WordUnderCursor)
        if not cursor.hasSelection():
            return
        text = cursor.selectedText().replace("\u2029", "\n")
        # Strip common wrappers
        patterns = [
            (r"\*\*\*(.+?)\*\*\*", r"\1"),
            (r"\*\*(.+?)\*\*", r"\1"),
            (r"\*(.+?)\*", r"\1"),
            (r"~~(.+?)~~", r"\1"),
            (r"==(.+?)==", r"\1"),
            (r"`(.+?)`", r"\1"),
        ]
        for pat, repl in patterns:
            text = re.sub(pat, repl, text, flags=re.DOTALL)
        cursor.beginEditBlock()
        cursor.insertText(text)
        cursor.endEditBlock()
        self.setTextCursor(cursor)

    def focusOutEvent(self, event):  # type: ignore[override]
        super().focusOutEvent(event)
        if self._suppress_focus_lost_once or self._focus_lost_suppression_depth > 0:
            self._suppress_focus_lost_once = False
            return
        self.focusLost.emit()

    def push_focus_lost_suppression(self) -> None:
        self._focus_lost_suppression_depth += 1

    def pop_focus_lost_suppression(self) -> None:
        self._focus_lost_suppression_depth = max(0, self._focus_lost_suppression_depth - 1)

    def _handle_read_only_keypress(self, event: QKeyEvent) -> bool:
        """Allow navigation/copy but block mutations when read-only."""
        mods = event.modifiers() & ~Qt.KeypadModifier
        key = event.key()
        # Allow common non-mutating shortcuts (copy/find/select-all)
        for seq in (
            QKeySequence.Copy,
            QKeySequence.Find,
            QKeySequence.FindNext,
            QKeySequence.FindPrevious,
            QKeySequence.SelectAll,
        ):
            if event.matches(seq):
                super().keyPressEvent(event)
                return True
        # Let vi navigation continue to work; editing commands are guarded inside handler
        if self._vi_feature_enabled and self._handle_vi_keypress(event):
            return True
        nav_keys = {
            Qt.Key_Left,
            Qt.Key_Right,
            Qt.Key_Up,
            Qt.Key_Down,
            Qt.Key_Home,
            Qt.Key_End,
            Qt.Key_PageUp,
            Qt.Key_PageDown,
        }
        if key in nav_keys or (mods & Qt.ShiftModifier and key in nav_keys):
            super().keyPressEvent(event)
            return True
        if key in (Qt.Key_Return, Qt.Key_Enter):
            cursor = self.textCursor()
            if self._is_cursor_at_link_activation_point(cursor):
                link = self._link_under_cursor(cursor)
                if link:
                    self.linkActivated.emit(link)
            return True
        # Allow other modifier combos (e.g., Ctrl+something) to propagate for non-editing shortcuts
        if mods & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier):
            super().keyPressEvent(event)
            return True
        # Swallow everything else (typing) to keep buffer unchanged
        if event.text():
            self._status_message("Vault is read-only; editing is disabled.")
        return True

    def keyPressEvent(self, event):  # type: ignore[override]
        if self._dialog_block_input:
            event.ignore()
            return
        if self._read_only_mode:
            if self._handle_read_only_keypress(event):
                return
        # Reserve Ctrl+\ and Ctrl+Backslash for application shortcuts (task panel focus)
        if event.modifiers() == Qt.ControlModifier and event.key() in (Qt.Key_Backslash, 0x5C):
            event.ignore()
            return
        # Markdown formatting shortcuts and undo/redo (Ctrl+Z/Ctrl+Y)
        if event.modifiers() == Qt.ControlModifier:
            if event.key() == Qt.Key_B:
                self._toggle_bold()
                event.accept()
                return
            elif event.key() == Qt.Key_I:
                self._toggle_italic()
                event.accept()
                return
            elif event.key() == Qt.Key_K:
                self._toggle_strikethrough()
                event.accept()
                return
            elif event.key() == Qt.Key_U:
                self._toggle_highlight()
                event.accept()
                return
            elif event.key() == Qt.Key_T:
                self._toggle_verbatim()
                event.accept()
                return
            elif event.key() in (Qt.Key_1, Qt.Key_2, Qt.Key_3, Qt.Key_4, Qt.Key_5):
                self._apply_heading_level(event.key() - Qt.Key_0)
                event.accept()
                return
            elif event.key() == Qt.Key_7:
                self._remove_heading()
                event.accept()
                return
            elif event.key() == Qt.Key_9:
                self._clear_inline_formatting()
                event.accept()
                return
            elif event.key() == Qt.Key_Z:
                self._undo_or_status()
                event.accept()
                return
            elif event.key() == Qt.Key_Y:
                self._redo_or_status()
                event.accept()
                return
        if self._vi_feature_enabled and self._handle_vi_keypress(event):
            event.accept()
            return

        if (event.modifiers() & Qt.ControlModifier) and (event.modifiers() & Qt.ShiftModifier):
            if event.key() == Qt.Key_K:
                self._vi_page_up()
                event.accept()
                return
            if event.key() == Qt.Key_J:
                self._vi_page_down()
                event.accept()
                return
        # Check for meaningful modifiers (ignore KeypadModifier which Qt may add on some platforms)
        # This is used throughout the keyPressEvent for cross-platform compatibility
        meaningful_modifiers = event.modifiers() & ~Qt.KeypadModifier
        
        # Bullet/task mode key handling
        cursor = self.textCursor()
        block = cursor.block()
        text = block.text()
        is_bullet, indent, content = self._is_bullet_line(text)
        is_dash, dash_indent, dash_content = self._is_dash_line(text)
        is_task, task_indent, task_state, task_content = self._is_task_line(text)
        if not (meaningful_modifiers & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier)) and (
            event.text() or event.key() in (Qt.Key_Backspace, Qt.Key_Delete)
        ):
            self._prepare_heading_edit_on_input(cursor)
        # Ctrl+E: edit link under cursor
        if event.key() == Qt.Key_E and event.modifiers() == Qt.ControlModifier:
            self._edit_link_at_cursor(cursor)
            event.accept()
            return
        # Tab: indent current line or selection
        if event.key() == Qt.Key_Tab and not meaningful_modifiers:
            if cursor.hasSelection() and self._apply_indent_to_selection(dedent=False):
                event.accept()
                return
            if is_bullet and self._handle_bullet_indent():
                event.accept()
                return
            if is_dash and self._handle_dash_indent():
                event.accept()
                return
            if is_task and self._handle_task_indent(task_indent):
                event.accept()
                return
        # Shift-Tab / Ctrl+Shift+Tab: dedent
        if event.key() == Qt.Key_Backtab and not (meaningful_modifiers & ~(Qt.ControlModifier | Qt.ShiftModifier)):
            if cursor.hasSelection() and self._apply_indent_to_selection(dedent=True):
                event.accept()
                return
            if meaningful_modifiers & Qt.ControlModifier:
                # Ctrl+Shift+Tab without a selection is reserved for navigation elsewhere
                pass
            else:
                if is_bullet and self._handle_bullet_dedent():
                    event.accept()
                    return
                if is_dash and self._handle_dash_dedent():
                    event.accept()
                    return
                if is_task and self._handle_task_dedent(task_indent):
                    event.accept()
                    return
                if self._handle_generic_dedent():
                    event.accept()
                    return
        # Enter: continue bullet or terminate if empty
        if is_bullet and event.key() in (Qt.Key_Return, Qt.Key_Enter) and not meaningful_modifiers:
            if self._handle_bullet_enter():
                event.accept()
                return
        # Enter: continue dash list or terminate if empty
        if is_dash and event.key() in (Qt.Key_Return, Qt.Key_Enter) and not meaningful_modifiers:
            if self._handle_dash_enter():
                event.accept()
                return
        # Enter: continue task checkbox lines
        if is_task and event.key() in (Qt.Key_Return, Qt.Key_Enter) and not meaningful_modifiers:
            # If the caret sits inside a link on a task line, activate the link instead of inserting a new task
            if self._is_cursor_at_link_activation_point(cursor):
                link = self._link_under_cursor(cursor)
                if link:
                    self.linkActivated.emit(link)
                    event.accept()
                    return
            if self._handle_task_enter(task_indent, task_content):
                event.accept()
                return
        # Esc: vi-mode exit or terminate bullet mode when vi is disabled
        if event.key() == Qt.Key_Escape:
            if self._handle_vi_escape():
                event.accept()
                return
            if self._handle_escape_clear_empty_line():
                event.accept()
                return
            super().keyPressEvent(event)
            return
        # ...existing code...
        # When at end-of-buffer, Down should still scroll the viewport
        if event.key() == Qt.Key_Down and not meaningful_modifiers:
            if self.textCursor().atEnd():
                self._scroll_one_line_down()
                event.accept()
                return
        # Handle Left/Right arrow keys for proper link boundary navigation
        if event.key() in (Qt.Key_Left, Qt.Key_Right) and not meaningful_modifiers:
            if self._handle_link_boundary_navigation(event.key()):
                event.accept()
                return
        
        # Protect link sentinels from deletion
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace) and not meaningful_modifiers:
            if self._protect_sentinel_deletion(event.key()):
                event.accept()
                return
        
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not meaningful_modifiers:
            # Check if cursor is within a link - if so, just insert newline, don't activate
            cursor = self.textCursor()
            if not self._is_cursor_at_link_activation_point(cursor):
                # Handle bullet list continuation (non-bullet lines)
                if self._handle_bullet_enter():
                    event.accept()
                    return
                # Handle dash list continuation (non-dash lines)
                if self._handle_dash_enter():
                    event.accept()
                    return
                # For non-bullets: carry over leading indentation on new line
                if self._handle_enter_indent_same_level():
                    event.accept()
                    return
            else:
                # Cursor is at a link activation point - activate the link
                link = self._link_under_cursor()
                if link:
                    self.linkActivated.emit(link)
                    return
        # ...existing code...
        super().keyPressEvent(event)

    def _vi_page_up(self):
        # Simulate PageUp: move cursor up by visible lines
        lines = max(1, self.viewport().height() // self.fontMetrics().lineSpacing())
        c = self.textCursor()
        c.movePosition(QTextCursor.Up, QTextCursor.MoveAnchor, lines)
        self.setTextCursor(c)

    def _vi_page_down(self):
        # Simulate PageDown: move cursor down by visible lines
        lines = max(1, self.viewport().height() // self.fontMetrics().lineSpacing())
        c = self.textCursor()
        c.movePosition(QTextCursor.Down, QTextCursor.MoveAnchor, lines)
        self.setTextCursor(c)

    def keyReleaseEvent(self, event):  # type: ignore[override]
        super().keyReleaseEvent(event)
        # Phase 3: +CamelCase auto-conversion removed - use [[ or // triggers instead
        # Check for inline link triggers: [[ or //
        if event.key() == Qt.Key_Space and not (event.modifiers() & ~Qt.KeypadModifier):
            # Only trigger in insert mode (or when vi mode is disabled)
            if not self._vi_feature_enabled or self._vi_insert_mode:
                # Prevent duplicate processing
                if not self._processing_inline_trigger:
                    self._check_inline_link_trigger()
        
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            block = self.textCursor().block().previous()
            if block.isValid():
                self._finalize_heading_block(block)

    def contextMenuEvent(self, event):  # type: ignore[override]
        self._last_context_menu_global_pos = event.globalPos()
        if self._context_menu_selection:
            try:
                sel_start, sel_end = self._context_menu_selection
                restore = QTextCursor(self.document())
                restore.setPosition(sel_start)
                restore.setPosition(sel_end, QTextCursor.MoveMode.KeepAnchor)
                self.setTextCursor(restore)
            except Exception:
                pass
        if _DETAILED_LOGGING:
            print(
                f"[AI Menu Debug] contextMenuEvent path={self._current_path!r} "
                f"ai_actions_enabled={self._ai_actions_enabled} "
                f"ai_chat_available={self._ai_chat_available} "
                f"text_len={len(self.toPlainText())}"
            )

        click_cursor = self.cursorForPosition(event.pos())
        heading_text = self._heading_text_for_cursor(click_cursor)
        heading_action = None
        if heading_text:
            heading_action = QAction("Copy link to clipboard", self)
            heading_action.setEnabled(bool(self._current_path))
            heading_action.triggered.connect(
                lambda checked=False, text=heading_text: self._copy_link_to_location(
                    link_text=None,
                    anchor_text=text,
                )
            )

        image_hit = self._image_at_position(event.pos())
        if image_hit:
            _, fmt = image_hit
            image_name = fmt.name()
            menu = QMenu(self)
            self._add_ai_actions_entry(menu, as_submenu=True)
            self._add_view_mode_actions(menu)
            for width in (300, 600, 900):
                action = menu.addAction(f"{width}px")
                action.triggered.connect(
                    lambda checked=False, w=width, name=image_name: self._resize_image_by_name(name, w)
                )
            menu.addSeparator()
            reset_action = menu.addAction("Original Size")
            reset_action.triggered.connect(
                lambda checked=False, name=image_name: self._resize_image_by_name(name, None)
            )
            menu.addSeparator()
            custom_action = menu.addAction("Custom…")
            custom_action.triggered.connect(
                lambda checked=False, name=image_name: self._prompt_image_width_by_name(name)
            )
            self._suppress_focus_lost_once = True
            menu.exec(event.globalPos())
            self._suppress_focus_lost_once = False
            self._context_menu_selection = None
            return

        md_link = self._markdown_link_at_cursor(click_cursor)
        plain_link = self._link_under_cursor(click_cursor)
        if md_link or plain_link:
            menu = QMenu(self)
            if heading_action:
                menu.addAction(heading_action)
            self._add_ai_actions_entry(menu, as_submenu=True)
            self._add_view_mode_actions(menu)

            base_menu = self.createStandardContextMenu()
            try:
                self._install_copy_actions(base_menu)
            except Exception:
                pass
            edit_sub = menu.addMenu("Edit")
            for act in base_menu.actions():
                edit_sub.addAction(act)
            insert_link_action = menu.addAction("Insert Link…")
            insert_link_action.triggered.connect(self._request_insert_link)

            link_sub = menu.addMenu("Link")
            edit_action = link_sub.addAction("Edit Link…")
            edit_action.triggered.connect(lambda: self._edit_link_at_cursor(click_cursor))
            remove_action = link_sub.addAction("Remove Link")
            remove_action.triggered.connect(lambda: self._remove_link_at_cursor(click_cursor))
            link_sub.addSeparator()
            link_for_copy = md_link[3] if md_link else plain_link
            copy_action = link_sub.addAction("Copy Link Target")
            copy_action.triggered.connect(lambda: self._copy_link_to_location(link_for_copy))

            if self._current_path:
                page_sub = menu.addMenu("Page")
                copy_page_action = page_sub.addAction("Copy link to this Page")
                copy_page_action.triggered.connect(
                    lambda: self._copy_link_to_location(link_text=None, anchor_text=None)
                )
                print_page_action = page_sub.addAction("Print Page…")
                print_page_action.triggered.connect(
                    lambda: self.printPageRequested.emit(self._current_path or "")
                )
                edit_src_action = page_sub.addAction("Edit Page Source")
                edit_src_action.triggered.connect(
                    lambda: self.editPageSourceRequested.emit(self._current_path or "")
                )
                open_loc_action = page_sub.addAction("Open Page Location")
                open_loc_action.triggered.connect(
                    lambda: self.openFileLocationRequested.emit(self._current_path or "")
                )
                open_popup_action = page_sub.addAction("Open Page in New Editor")
                open_popup_action.setEnabled(bool(self._open_in_window_callback))
                if self._open_in_window_callback:
                    open_popup_action.triggered.connect(
                        lambda: self._open_in_window_callback(self._current_path or "")
                    )
                delete_page_action = page_sub.addAction("Delete Page")
                delete_page_action.triggered.connect(
                    lambda pos=event.globalPos(): self.deletePageRequested.emit(pos)
                )

                nav_sub = menu.addMenu("Navigate")
                filter_action = nav_sub.addAction("Filter Nav From Here")
                filter_action.setEnabled(bool(self._filter_nav_callback))
                if self._filter_nav_callback:
                    filter_action.triggered.connect(
                        lambda: self._filter_nav_callback(self._current_path or "")
                    )
                locate_tree_action = nav_sub.addAction("Locate in Page Tree")
                locate_tree_action.triggered.connect(
                    lambda: self.locateInNavigatorRequested.emit(self._current_path or "")
                )
                backlinks_action = nav_sub.addAction("Link Graph / Navigator")
                backlinks_action.triggered.connect(
                    lambda: self.backlinksRequested.emit(self._current_path or "")
                )

                move_action = menu.addAction("Move Text…")
                move_action.setEnabled(self.textCursor().hasSelection())
                move_action.triggered.connect(self._move_text_via_jump_dialog)
            self._suppress_focus_lost_once = True
            menu.exec(event.globalPos())
            self._suppress_focus_lost_once = False
            self._context_menu_selection = None
            return

        if self._current_path:
            base_menu = self.createStandardContextMenu()
            try:
                self._install_copy_actions(base_menu)
            except Exception:
                pass
            menu = QMenu(self)
            if heading_action:
                menu.addAction(heading_action)
            self._add_ai_actions_entry(menu, as_submenu=True)
            self._add_view_mode_actions(menu)

            edit_sub = menu.addMenu("Edit")
            for act in base_menu.actions():
                edit_sub.addAction(act)
            insert_link_action = menu.addAction("Insert Link…")
            insert_link_action.triggered.connect(self._request_insert_link)

            page_sub = menu.addMenu("Page")
            new_page_action = page_sub.addAction("New Page...")
            new_page_action.triggered.connect(self._request_new_page)
            page_sub.addSeparator()
            copy_page_action = page_sub.addAction("Copy link to this Page")
            copy_page_action.triggered.connect(
                lambda: self._copy_link_to_location(link_text=None, anchor_text=None)
            )
            print_page_action = page_sub.addAction("Print Page…")
            print_page_action.triggered.connect(
                lambda: self.printPageRequested.emit(self._current_path or "")
            )
            edit_src_action = page_sub.addAction("Edit Page Source")
            edit_src_action.triggered.connect(
                lambda: self.editPageSourceRequested.emit(self._current_path or "")
            )
            open_loc_action = page_sub.addAction("Open Page Location")
            open_loc_action.triggered.connect(
                lambda: self.openFileLocationRequested.emit(self._current_path or "")
            )
            open_popup_action = page_sub.addAction("Open Page in New Editor")
            open_popup_action.setEnabled(bool(self._open_in_window_callback))
            if self._open_in_window_callback:
                open_popup_action.triggered.connect(
                    lambda: self._open_in_window_callback(self._current_path or "")
                )
            delete_page_action = page_sub.addAction("Delete Page")
            delete_page_action.triggered.connect(lambda pos=event.globalPos(): self.deletePageRequested.emit(pos))

            nav_sub = menu.addMenu("Navigate")
            filter_action = nav_sub.addAction("Filter Nav From Here")
            filter_action.setEnabled(bool(self._filter_nav_callback))
            if self._filter_nav_callback:
                filter_action.triggered.connect(lambda: self._filter_nav_callback(self._current_path or ""))
            locate_tree_action = nav_sub.addAction("Locate in Page Tree")
            locate_tree_action.triggered.connect(
                lambda: self.locateInNavigatorRequested.emit(self._current_path or "")
            )
            backlinks_action = nav_sub.addAction("Link Graph / Navigator")
            backlinks_action.triggered.connect(lambda: self.backlinksRequested.emit(self._current_path or ""))

            move_action = menu.addAction("Move Text…")
            move_action.setEnabled(self.textCursor().hasSelection())
            move_action.triggered.connect(self._move_text_via_jump_dialog)

            self._suppress_focus_lost_once = True
            menu.exec(event.globalPos())
            self._suppress_focus_lost_once = False
            self._context_menu_selection = None
            return
       
        self._context_menu_selection = None
        super().contextMenuEvent(event)

    def _request_insert_link(self) -> None:
        window = self.window()
        if window and hasattr(window, "_insert_link"):
            try:
                window._insert_link()
            except Exception:
                pass

    def _request_new_page(self) -> None:
        window = self.window()
        if window and hasattr(window, "_show_new_page_dialog"):
            try:
                window._show_new_page_dialog()
            except Exception:
                pass

    def _request_mode_overlay(self, mode: str) -> None:
        window = self.window()
        if window and hasattr(window, "_toggle_mode_overlay"):
            try:
                window._toggle_mode_overlay(mode)
            except Exception:
                pass

    def _add_view_mode_actions(self, menu: QMenu) -> None:
        if not menu:
            return
        view_sub = menu.addMenu("View...")
        focus_action = view_sub.addAction("Focus Mode")
        focus_action.setToolTip("Open in Focus Mode")
        focus_action.triggered.connect(lambda checked=False: self._request_mode_overlay("focus"))
        audience_action = view_sub.addAction("Audience Mode")
        audience_action.setToolTip("Open in Audience Mode")
        audience_action.triggered.connect(lambda checked=False: self._request_mode_overlay("audience"))

    def _ai_chat_payload_text(self) -> str:
        cursor = self.textCursor()
        selected = cursor.selection().toPlainText()
        text = selected if selected.strip() else self.toPlainText()
        normalized = text.replace("\u2029", "\n").strip()
        return normalized

    def _sanitize_for_clipboard(self, text: str) -> str:
        """Normalize text for copying to the system clipboard.

        - Replace Unicode paragraph separators with newlines.
        - Remove non-printable control characters except tab/newline/carriage return.
        - Trim trailing/leading whitespace.
        """
        if text is None:
            return ""
        text = text.replace("\u2029", "\n")
        # Remove low-control characters but keep common whitespace (\n,\r,\t)
        text = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]+", "", text)
        # Remove BOM, object-replacement, zero-width and bidi control characters,
        # and private-use-area glyphs that can appear as weird symbols when pasted.
        try:
            text = re.sub(
                r"[\u200B-\u200F\u2028\u202A-\u202E\uFEFF\uFFFC\uE000-\uF8FF]",
                "",
                text,
            )
        except Exception:
            pass
        return text.strip()

    def _copy_selection_as_html(self) -> None:
        """Convert selected markdown text to HTML and copy to clipboard."""
        cursor = self.textCursor()
        if not cursor.hasSelection():
            return
        
        # Get selected text and normalize it
        selected_text = cursor.selectedText()
        # Convert Qt's paragraph separator to newlines
        markdown_text = selected_text.replace("\u2029", "\n")
        # Convert storage format links [target|label] to markdown [label](target)
        markdown_text = self._from_display(markdown_text)
        
        try:
            # Convert markdown to HTML with common extensions
            html = render_markdown(
                markdown_text,
                extensions=["extra", "sane_lists", "tables", "fenced_code", "nl2br"]
            )
            
            # Copy to clipboard
            clipboard = QGuiApplication.clipboard()
            mime_data = QMimeData()
            mime_data.setHtml(html)
            mime_data.setText(markdown_text)  # Also provide plain text fallback
            clipboard.setMimeData(mime_data)
            
        except Exception as e:
            logger.error(f"Failed to convert selection to HTML: {e}")
            # Fallback to plain text
            clipboard = QGuiApplication.clipboard()
            clipboard.setText(markdown_text)

    def _install_copy_actions(self, menu: QMenu) -> None:
        """Replace the default Copy action with sanitized versions and add 'Copy As Markdown'."""
        if not menu:
            return
        actions = menu.actions()
        copy_act = None
        for a in actions:
            t = (a.text() or "").lower()
            if t.startswith("copy"):
                copy_act = a
                break
        # Create sanitized copy action
        def do_copy():
            cursor = self.textCursor()
            if cursor.hasSelection():
                txt = cursor.selection().toPlainText()
            else:
                txt = self.toPlainText()
            QApplication.clipboard().setText(self._sanitize_for_clipboard(txt))

        # Create 'Copy As Markdown' action
        def do_copy_md():
            cursor = self.textCursor()
            if cursor.hasSelection():
                # Convert the selected display text back to storage markdown
                display_text = cursor.selection().toPlainText()
                try:
                    txt = self._from_display(display_text)
                except Exception:
                    txt = display_text
            else:
                # Use full-document conversion which handles images and link conversions
                txt = self.to_markdown()
            # For markdown, keep markup characters but still normalize control chars
            QApplication.clipboard().setText(self._sanitize_for_clipboard(txt))

        new_copy = menu.addAction("Copy")
        new_copy.triggered.connect(lambda checked=False: do_copy())
        md_action = menu.addAction("Copy As Markdown")
        md_action.triggered.connect(lambda checked=False: do_copy_md())
        # Remove original copy action if present
        if copy_act:
            menu.removeAction(copy_act)

    def _emit_ai_chat_send(self) -> None:
        payload = self._ai_chat_payload_text()
        if not payload:
            return
        self.aiChatSendRequested.emit(payload)

    def _emit_ai_chat_start(self) -> None:
        if not self._current_path:
            return
        self.aiChatRequested.emit(self._current_path)

    def _emit_ai_chat_focus(self) -> None:
        if not self._current_path:
            return
        self.aiChatPageFocusRequested.emit(self._current_path)

    def _emit_one_shot_selection(self) -> None:
        """Trigger a one-shot prompt using the current selection (or document)."""
        text = self._ai_chat_payload_text()
        if not text:
            return
        self.aiActionRequested.emit("One-Shot Prompt Selection", "", text)

    def _show_ai_action_overlay(
        self,
        *,
        anchor: Optional[QPoint] = None,
        text_override: Optional[str] = None,
        has_chat: Optional[bool] = None,
        chat_active: Optional[bool] = None,
    ) -> None:
        if not self._ai_actions_enabled or not config.load_enable_ai_chats():
            return
        # Showing the overlay steals focus from the editor (Qt.Popup). Upstream focusLost
        # handlers may autosave; suppress that focus-loss for this interaction.
        self._suppress_focus_lost_once = True
        cursor = QTextCursor(self.textCursor())
        if cursor.hasSelection():
            sel_start = cursor.selectionStart()
            sel_end = cursor.selectionEnd()
        else:
            sel_start = sel_end = cursor.position()
        text = text_override if text_override is not None else self._ai_chat_payload_text()
        if not text:
            self._suppress_focus_lost_once = False
            return
        self._suspend_vi_for_overlay()
        self._ai_action_overlay.open(
            text,
            has_chat=self._ai_chat_available if has_chat is None else has_chat,
            chat_active=getattr(self, "_ai_chat_active", False) if chat_active is None else chat_active,
            anchor=anchor,
        )
        self._suppress_focus_lost_once = False
        try:
            restore = QTextCursor(self.document())
            restore.setPosition(sel_start)
            restore.setPosition(sel_end, QTextCursor.KeepAnchor)
            self.setTextCursor(restore)
        except Exception:
            pass

    def show_ai_overlay_with_text(
        self, text: str, *, anchor: Optional[QPoint] = None, has_chat: bool = True, chat_active: bool = True
    ) -> None:
        """Expose overlay for external callers (e.g., AI chat panel)."""
        self._show_ai_action_overlay(anchor=anchor, text_override=text, has_chat=has_chat, chat_active=chat_active)

    def _handle_ai_action_overlay(self, title: str, prompt: str) -> None:
        text = self._ai_action_overlay.text()
        self.aiActionRequested.emit(title, prompt, text)

    def _suspend_vi_for_overlay(self) -> None:
        if self._overlay_vi_mode_before is not None:
            return
        self._overlay_vi_mode_before = self._vi_mode_active
        if self._vi_mode_active:
            self.set_vi_mode(False)

    def _restore_vi_after_overlay(self) -> None:
        if self._overlay_vi_mode_before is None:
            return
        self.set_vi_mode(self._overlay_vi_mode_before)
        self._overlay_vi_mode_before = None

    def _add_ai_actions_entry(self, menu: QMenu, *, as_submenu: bool = False) -> None:
        """Insert a single AI Actions entry that opens the AI actions panel.

        `as_submenu` is ignored (kept for call-site compatibility).
        """
        if not (self._ai_actions_enabled and config.load_enable_ai_chats()):
            return None
        ai_action = QAction("AI Actions...", self)
        ai_action.triggered.connect(
            lambda checked=False: self._show_ai_action_overlay(anchor=self._last_context_menu_global_pos)
        )
        existing = menu.actions()
        if existing:
            first = existing[0]
            menu.insertAction(first, ai_action)
            menu.insertSeparator(first)
        else:
            menu.addAction(ai_action)
            menu.addSeparator()
        return None
    
    def dragEnterEvent(self, event):  # type: ignore[override]
        """Accept drag events with file URLs."""
        mime = event.mimeData()
        print(f"[DragEnter] hasUrls: {mime.hasUrls()}, hasText: {mime.hasText()}")
        # Check for custom stillpoint-path format first (from tree drag)
        if mime.hasFormat("application/x-stillpoint-path"):
            print(f"[DragEnter] Has stillpoint-path format")
            event.acceptProposedAction()
        elif mime.hasUrls():
            print(f"[DragEnter] URLs: {[url.toLocalFile() for url in mime.urls()]}")
            event.acceptProposedAction()
        elif mime.hasText():
            print(f"[DragEnter] Text: {mime.text()}")
            event.acceptProposedAction()
        else:
            print(f"[DragEnter] Formats: {mime.formats()}")
            super().dragEnterEvent(event)
    
    def dragMoveEvent(self, event):  # type: ignore[override]
        """Accept drag move events with file URLs."""
        mime = event.mimeData()
        if mime.hasFormat("application/x-stillpoint-path") or mime.hasUrls() or mime.hasText():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)
    
    def dropEvent(self, event):  # type: ignore[override]
        """Handle dropped files - insert as image or file link."""
        from pathlib import Path
        
        mime = event.mimeData()
        print(f"[Drop] hasUrls: {mime.hasUrls()}, hasText: {mime.hasText()}")
        print(f"[Drop] Formats: {mime.formats()}")
        print(f"[Drop] Has stillpoint-path: {mime.hasFormat('application/x-stillpoint-path')}")
        
        file_path = None
        dropped_path_text: str | None = None
        
        if mime.hasUrls():
            urls = mime.urls()
            print(f"[Drop] URLs: {[url.toLocalFile() for url in urls]}")
            if urls:
                file_path = Path(urls[0].toLocalFile())
        elif mime.hasText():
            # Try to parse text as file path
            text = mime.text().strip()
            print(f"[Drop] Text: {text}")
            dropped_path_text = text
            if text.startswith('file://'):
                file_path = Path(text[7:])
            else:
                file_path = Path(text)
        # Tree drop to create vault link (use custom mime to avoid clobbering file drops)
        if mime.hasFormat("application/x-stillpoint-path"):
            try:
                raw = mime.data("application/x-stillpoint-path")
                dropped_path_text = bytes(raw).decode("utf-8")
            except Exception:
                dropped_path_text = None
        if dropped_path_text and dropped_path_text.startswith("/"):
            try:
                from .path_utils import path_to_colon, ensure_root_colon_link
            except Exception:
                return super().dropEvent(event)
            
            # Check if dropped path is a child of the current page
            current_path = self.current_relative_path() if hasattr(self, "current_relative_path") else None
            link_text = None
            
            if current_path:
                try:
                    from pathlib import Path
                    from sp.server.adapters.files import PAGE_SUFFIXES
                    
                    # Get the parent directory of the current page
                    current = Path(current_path)
                    if current.suffix in PAGE_SUFFIXES:
                        parent_dir = current.parent
                    else:
                        parent_dir = current
                    
                    # Check if dropped path is a direct child
                    dropped = Path(dropped_path_text.lstrip("/"))
                    
                    # If dropped path is under parent_dir, use +CamelCase format
                    if str(dropped.parent) == str(parent_dir) and dropped.suffix in PAGE_SUFFIXES:
                        # Extract the page name (without .md extension)
                        page_name = dropped.stem
                        link_text = f"+{page_name}"
                except Exception:
                    pass
            
            # Fall back to colon link if not a child
            if not link_text:
                colon = path_to_colon(dropped_path_text) or dropped_path_text
                link_target = ensure_root_colon_link(colon)
                label = Path(dropped_path_text).stem or link_target
                link_text = f"[{link_target}|{label}]"
            
            cursor = self.cursorForPosition(event.pos())
            self.setTextCursor(cursor)
            cursor.insertText(link_text)
            self._refresh_display()
            event.acceptProposedAction()
            return
        
        if file_path and file_path.exists() and file_path.is_file():
            print(f"[Drop] Processing file: {file_path}")
            if self._remote_mode:
                upload_name = self._unique_remote_name(file_path.name)
                if file_path.suffix.lower() in ['.png', '.jpg', '.jpeg', '.gif', '.bmp', '.svg']:
                    payload = file_path.read_bytes()
                    if self._upload_remote_bytes(upload_name, payload):
                        self._cache_remote_bytes(upload_name, payload)
                        self._insert_image_from_path(upload_name, alt=Path(upload_name).stem)
                        event.acceptProposedAction()
                        if not dropped_path_text:
                            self.attachmentDropped.emit(upload_name)
                        return
                else:
                    if self._upload_remote_file(upload_name, file_path):
                        cursor = self.cursorForPosition(event.pos())
                        self.setTextCursor(cursor)
                        link_text = f"[{upload_name}](./{upload_name})"
                        cursor.insertText(link_text)
                        event.acceptProposedAction()
                        if not dropped_path_text:
                            self.attachmentDropped.emit(upload_name)
                        return
            # Check if it's an image
            if file_path.suffix.lower() in ['.png', '.jpg', '.jpeg', '.gif', '.bmp', '.svg']:
                # Insert as image
                print(f"[Drop] Inserting as image")
                self._insert_image_from_path(file_path.name, alt=file_path.stem)
            else:
                # Insert as file link
                print(f"[Drop] Inserting as file link")
                cursor = self.cursorForPosition(event.pos())
                self.setTextCursor(cursor)
                link_text = f"[{file_path.name}](./{file_path.name})"
                cursor.insertText(link_text)
            
            event.acceptProposedAction()
            # For vault link drops (text path), do not emit attachmentDropped to avoid duplicate indexing
            try:
                if not dropped_path_text:
                    self.attachmentDropped.emit(file_path.name)
            except Exception:
                pass
            return
        else:
            print(f"[Drop] File not found or not valid: {file_path}")
        
        super().dropEvent(event)

    def mouseMoveEvent(self, event):  # type: ignore[override]
        # Show pointing hand cursor when hovering over a link
        cursor = self.cursorForPosition(event.pos())
        link = self._link_under_cursor(cursor)
        if link:
            self.viewport().setCursor(Qt.PointingHandCursor)
            self.linkHovered.emit(link)
        else:
            self.viewport().setCursor(Qt.IBeamCursor)
            self.linkHovered.emit("")  # Empty string to clear status bar
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event):  # type: ignore[override]
        # Preserve selection on right-click (opening context menu should not unselect buffer).
        if event.button() == Qt.RightButton:
            cursor = self.textCursor()
            if cursor.hasSelection():
                self._context_menu_selection = (cursor.selectionStart(), cursor.selectionEnd())
                event.accept()
                return
            self._context_menu_selection = None

        # Single click on links to activate them
        if event.button() == Qt.LeftButton:
            cursor = self.cursorForPosition(event.pos())
            md_info = self._markdown_link_at_cursor(cursor)
            link = md_info[3] if md_info else self._link_under_cursor(cursor)
            if link:
                self.linkActivated.emit(link)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):  # type: ignore[override]
        hit = self._image_at_position(event.pos())
        if hit:
            _, fmt = hit
            original = fmt.property(IMAGE_PROP_ORIGINAL) or fmt.name()
            resolved = self._resolve_image_path(str(original))
            if resolved and resolved.exists():
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(resolved)))
            return
        
        # Check if double-clicking on a checkbox and toggle it
        cursor = self.cursorForPosition(event.pos())
        if self._is_on_checkbox(cursor):
            self._toggle_task_at_cursor(event.pos())
            return
        
        # Double-click on links also activates them
        md_info = self._markdown_link_at_cursor(cursor)
        link = md_info[3] if md_info else self._link_under_cursor(cursor)
        if link:
            self.linkActivated.emit(link)
            return
        super().mouseDoubleClickEvent(event)

    def _toggle_task_at_cursor(self, pos=None) -> bool:
        cursor = self.cursorForPosition(pos) if pos is not None else self.textCursor()
        block = cursor.block()
        rel = cursor.position() - block.position()
        return self._toggle_task_marker(block, rel_pos=rel)

    def _handle_link_boundary_navigation(self, key: int) -> bool:
        """Handle Left/Right arrow navigation over link boundaries. Returns True if handled."""
        cursor = self.textCursor()
        block = cursor.block()
        rel_pos = cursor.position() - block.position()
        text = block.text()
        
        # Find link boundaries in display format: sentinel + link + sentinel + label + sentinel
        idx = 0
        while idx < len(text):
            if text[idx] == LINK_SENTINEL:
                link_start = idx + 1
                link_end = text.find(LINK_SENTINEL, link_start)
                if link_end > link_start:
                    label_start = link_end + 1
                    label_end = text.find(LINK_SENTINEL, label_start)
                    if label_end >= label_start:  # >= to handle empty labels
                        # Determine visible region: if label empty, show link; otherwise show label
                        if label_end == label_start:  # Empty label - link is visible
                            visible_start = link_start
                            visible_end = link_end
                        else:  # Non-empty label - label is visible
                            visible_start = label_start
                            visible_end = label_end
                        
                        if key == Qt.Key_Right:
                            # Moving right: if cursor is in the hidden part, jump to visible start
                            if idx <= rel_pos < visible_start:
                                new_cursor = QTextCursor(cursor)
                                new_cursor.setPosition(block.position() + visible_start)
                                self.setTextCursor(new_cursor)
                                return True
                            # If at the end of visible part, move past the closing sentinel
                            elif rel_pos == visible_end:
                                new_cursor = QTextCursor(cursor)
                                new_cursor.setPosition(block.position() + label_end + 1)
                                self.setTextCursor(new_cursor)
                                return True
                        
                        elif key == Qt.Key_Left:
                            # Moving left: if cursor is in the visible part, jump to before the link
                            if visible_start < rel_pos <= visible_end:
                                new_cursor = QTextCursor(cursor)
                                new_cursor.setPosition(block.position() + idx)
                                self.setTextCursor(new_cursor)
                                return True
                            # If at start of visible part, jump before the link
                            elif rel_pos == visible_start:
                                new_cursor = QTextCursor(cursor)
                                new_cursor.setPosition(block.position() + idx)
                                self.setTextCursor(new_cursor)
                                return True
                        
                        idx = label_end + 1
                        continue
            idx += 1
        
        return False

    def _protect_sentinel_deletion(self, key: int) -> bool:
        """Prevent deletion of link sentinels that would corrupt link display. Returns True if deletion should be blocked."""
        cursor = self.textCursor()
        
        # If there's a selection, check if it contains any sentinels
        if cursor.hasSelection():
            selected_text = cursor.selectedText()
            if LINK_SENTINEL in selected_text:
                # Delete the entire link(s) instead of partial sentinels
                self._delete_selection_with_links(cursor)
                return True
        else:
            # Single character deletion
            block = cursor.block()
            rel_pos = cursor.position() - block.position()
            text = block.text()
            
            if key == Qt.Key_Delete:
                # Check if we're about to delete a sentinel
                if rel_pos < len(text) and text[rel_pos] == LINK_SENTINEL:
                    # Find the complete link structure and delete it
                    self._delete_link_at_position(cursor, rel_pos)
                    return True
                # Check if we're right after a link's closing sentinel
                if rel_pos > 0 and text[rel_pos - 1] == LINK_SENTINEL:
                    self._delete_link_at_position(cursor, rel_pos - 1)
                    return True
            elif key == Qt.Key_Backspace:
                # Check if we're about to delete a sentinel
                if rel_pos > 0 and text[rel_pos - 1] == LINK_SENTINEL:
                    # Find the complete link structure and delete it
                    self._delete_link_at_position(cursor, rel_pos - 1)
                    return True
                # Also check if we're at the start of a visible part of a link
                # by checking if there's a sentinel anywhere behind us
                if rel_pos > 0:
                    idx = 0
                    while idx < len(text):
                        if text[idx] == LINK_SENTINEL:
                            link_start_pos = idx
                            link_content_start = idx + 1
                            link_content_end = text.find(LINK_SENTINEL, link_content_start)
                            if link_content_end > link_content_start:
                                label_start = link_content_end + 1
                                label_end = text.find(LINK_SENTINEL, label_start)
                                if label_end >= label_start:
                                    link_end_pos = label_end + 1
                                    # Check if cursor is inside this link's visible region
                                    if label_end == label_start:  # Empty label - link is visible
                                        visible_start = link_content_start
                                        visible_end = link_content_end
                                    else:  # Non-empty label - label is visible
                                        visible_start = label_start
                                        visible_end = label_end
                                    
                                    # If cursor is at start of visible part, backspace would hit sentinel
                                    if rel_pos == visible_start:
                                        self._delete_link_at_position(cursor, link_start_pos)
                                        return True
                                    
                                    idx = link_end_pos
                                    continue
                        idx += 1
        
        return False

    def _delete_selection_with_links(self, cursor: QTextCursor) -> None:
        """Delete selection, removing complete links if any sentinels are selected."""
        start = cursor.selectionStart()
        end = cursor.selectionEnd()
        
        # Get the full text to analyze
        full_text = self.toPlainText()
        
        # Find all link boundaries
        link_ranges = []
        for match in WIKI_LINK_DISPLAY_PATTERN.finditer(full_text):
            link_ranges.append((match.start(), match.end()))
        
        # Determine which links overlap with the selection
        adjusted_start = start
        adjusted_end = end
        
        for link_start, link_end in link_ranges:
            # If selection overlaps with link, expand to include the entire link
            if not (end <= link_start or start >= link_end):
                adjusted_start = min(adjusted_start, link_start)
                adjusted_end = max(adjusted_end, link_end)
        
        # Delete the adjusted range
        new_cursor = QTextCursor(self.document())
        new_cursor.setPosition(adjusted_start)
        new_cursor.setPosition(adjusted_end, QTextCursor.KeepAnchor)
        new_cursor.removeSelectedText()
        self.setTextCursor(new_cursor)

    def _delete_link_at_position(self, cursor: QTextCursor, sentinel_pos: int) -> None:
        """Delete the complete link structure at the given sentinel position."""
        block = cursor.block()
        text = block.text()
        link_range = self._link_range_in_block(text, sentinel_pos)
        if not link_range:
            return
        link_start_pos, link_end_pos = link_range
        new_cursor = QTextCursor(block)
        new_cursor.setPosition(block.position() + link_start_pos)
        new_cursor.setPosition(block.position() + link_end_pos, QTextCursor.KeepAnchor)
        new_cursor.removeSelectedText()
        self.setTextCursor(new_cursor)

    def _link_range_in_block(self, text: str, sentinel_pos: int) -> Optional[tuple[int, int]]:
        """Return (start, end) range for the link containing sentinel_pos in a block."""
        idx = 0
        while idx < len(text):
            if text[idx] == LINK_SENTINEL:
                link_start_pos = idx
                link_content_start = idx + 1
                link_content_end = text.find(LINK_SENTINEL, link_content_start)
                if link_content_end > link_content_start:
                    label_start = link_content_end + 1
                    label_end = text.find(LINK_SENTINEL, label_start)
                    if label_end >= label_start:
                        link_end_pos = label_end + 1
                        if link_start_pos <= sentinel_pos < link_end_pos:
                            return (link_start_pos, link_end_pos)
                        idx = link_end_pos
                        continue
            idx += 1
        return None

    def _trigger_history_navigation(self, qt_key: int) -> None:
        """Simulate Alt+Left/Right to leverage MainWindow history shortcuts."""
        window = self.window()
        if not window:
            return
        # Prefer calling the navigation helpers directly to avoid focus edge cases
        try:
            if qt_key == Qt.Key_Left and hasattr(window, "_navigate_history_back"):
                window._navigate_history_back()
                return
            if qt_key == Qt.Key_Right and hasattr(window, "_navigate_history_forward"):
                window._navigate_history_forward()
                return
            if qt_key == Qt.Key_Up and hasattr(window, "_navigate_history_back"):
                window._navigate_history_back()
                return
            if qt_key == Qt.Key_Down and hasattr(window, "_navigate_history_forward"):
                window._navigate_history_forward()
                return
        except Exception:
            pass
        press = QKeyEvent(QEvent.KeyPress, qt_key, Qt.AltModifier)
        release = QKeyEvent(QEvent.KeyRelease, qt_key, Qt.AltModifier)
        QApplication.sendEvent(window, press)
        QApplication.sendEvent(window, release)
    
    def _link_region_at_cursor(self, cursor: QTextCursor | None = None) -> Optional[tuple[str, int, int]]:
        """Return (link_text, start_pos, end_pos) for the link under the cursor, if any."""
        if self._cursor_events_blocked or self._display_guard or self._suppress_link_scan or self._overlay_transition:
            return None
        if self._in_mode_window_transition():
            return None
        cursor = cursor or self.textCursor()
        block = cursor.block()
        try:
            if not block.isValid():
                return None
            doc = block.document()
            if doc is None or doc.isEmpty():
                return None
        except Exception:
            return None
        rel = cursor.position() - block.position()
        try:
            text = block.text()
        except Exception:
            return None
        if not text:
            return None
        # Prefer markdown-style links first (handles display + storage formats)
        md_link = self._markdown_link_at_cursor(cursor)
        if md_link:
            start, end, _label, link_target = md_link
            return link_target, start, end

        # Plain HTTP URLs
        http_iter = HTTP_URL_PATTERN.globalMatch(text)
        while http_iter.hasNext():
            match = http_iter.next()
            start = match.capturedStart()
            end = start + match.capturedLength()
            if start <= rel < end:
                return match.captured("url"), start, end

        # Colon links (e.g., :Page:Child or :Page#anchor)
        colon_iter = COLON_LINK_PATTERN.globalMatch(text)
        while colon_iter.hasNext():
            match = colon_iter.next()
            start = match.capturedStart()
            end = start + match.capturedLength()
            if start <= rel < end:
                link = ensure_root_colon_link(match.captured("link"))
                return link, start, end

        # CamelCase links with + prefix
        camel_iter = CAMEL_LINK_PATTERN.globalMatch(text)
        while camel_iter.hasNext():
            match = camel_iter.next()
            start = match.capturedStart()
            end = start + match.capturedLength()
            if start <= rel < end:
                return match.captured("link"), start, end
        return None

    def _is_cursor_at_link_activation_point(self, cursor: QTextCursor) -> bool:
        """Check if cursor is positioned where Enter should activate a link vs insert newline."""
        region = self._link_region_at_cursor(cursor)
        if not region:
            return False
        _, start, end = region
        rel_pos = cursor.position() - cursor.block().position()
        # Only treat as activation when cursor is strictly inside the link (not touching the ends)
        return start < rel_pos < end

    def _link_under_cursor(self, cursor: QTextCursor | None = None) -> Optional[str]:
        if self._cursor_events_blocked or self._display_guard or self._suppress_link_scan or self._overlay_transition:
            return None
        if self._in_mode_window_transition():
            return None
        region = self._link_region_at_cursor(cursor)
        if not region:
            return None
        link, _, _ = region
        if not link:
            return None
        link = link.strip()
        if link.startswith(("http://", "https://")):
            return self._normalize_external_link(link)
        if ":" in link or link.startswith(":"):
            return ensure_root_colon_link(link)
        return link

    def _markdown_link_at_cursor(self, cursor: QTextCursor) -> Optional[tuple[int,int,str,str]]:
        """Return (start, end, text, link) for a wiki-style link under cursor, or None."""
        block = cursor.block()
        rel = cursor.position() - block.position()
        text = block.text()
        
        # Check display-format: sentinel + link + sentinel + label + sentinel
        idx = 0
        while idx < len(text):
            if text[idx] == LINK_SENTINEL:
                link_start = idx + 1
                link_end = text.find(LINK_SENTINEL, link_start)
                if link_end > link_start:
                    label_start = link_end + 1
                    label_end = text.find(LINK_SENTINEL, label_start)
                    if label_end >= label_start:  # >= to handle empty labels
                        raw_link = text[link_start:link_end]
                        visible_label = text[label_start:label_end]
                        if label_end == label_start:  # Empty label - link is visible
                            visible_start = link_start
                            visible_end = link_end
                            if visible_end > visible_start and text[visible_end - 1] == "|":
                                visible_end -= 1
                            visible_text = text[link_start:visible_end]
                            clean_link = raw_link[:-1] if raw_link.endswith("|") else raw_link
                        else:  # Non-empty label - label is visible
                            visible_start = label_start
                            visible_end = label_end
                            visible_text = visible_label
                            clean_link = raw_link

                        # Check if cursor is in the visible portion
                        if visible_start <= rel < visible_end:
                            display_text = visible_text or clean_link
                            return (idx, label_end + 1, display_text, clean_link)

                        idx = label_end + 1
                        continue
            idx += 1
        
        # Check storage-format wiki-style links: [link|label]
        wiki_pattern = r"\[([^\]|]+)\|([^\]]*)\]"
        import re as regex_module
        for match in regex_module.finditer(wiki_pattern, text):
            start = match.start()
            end = match.end()
            link = match.group(1)
            label = match.group(2)
            # Determine visible part (label if non-empty, otherwise link)
            if label:
                visible_start = start + 1 + len(link) + 1  # After '[link|'
                visible_end = visible_start + len(label)
                visible_text = label
            else:
                visible_start = start + 1  # After '['
                visible_end = start + 1 + len(link)
                visible_text = link
            
            if visible_start <= rel < visible_end:
                return (start, end, visible_text, link)
        
        # Check file links: [text](./file.ext) or [text](file.ext)
        fit = WIKI_FILE_LINK_PATTERN.globalMatch(text)
        while fit.hasNext():
            fm = fit.next()
            start = fm.capturedStart()
            end = start + fm.capturedLength()
            text_val = fm.captured("text")
            text_start = start + 1
            text_end = text_start + len(text_val)
            if text_start <= rel < text_end:
                return (start, end, text_val, fm.captured("file"))
        return None

    def _remove_link_at_cursor(self, cursor: QTextCursor) -> None:
        """Remove a link at the cursor position (remove the + prefix or convert colon notation to plain text)."""
        block = cursor.block()
        rel = cursor.position() - block.position()
        block_text = block.text()
        # If a markdown link, unwrap to just the display text
        md = self._markdown_link_at_cursor(cursor)
        if md:
            start, end, text_val, _ = md
            tc = QTextCursor(block)
            tc.setPosition(block.position() + start)
            tc.setPosition(block.position() + end, QTextCursor.KeepAnchor)
            tc.insertText(text_val)
            return
        
        # Check for CamelCase/plus-prefixed links (+PageName or +Projects)
        iterator = CAMEL_LINK_PATTERN.globalMatch(block_text)
        while iterator.hasNext():
            match = iterator.next()
            start = match.capturedStart()
            end = start + match.capturedLength()
            if start <= rel < end:
                # Remove the + prefix
                text_cursor = QTextCursor(block)
                text_cursor.setPosition(block.position() + start)
                text_cursor.setPosition(block.position() + end, QTextCursor.KeepAnchor)
                # Replace with just the link text (without +)
                text_cursor.insertText(match.captured("link"))
                return
        
        # Check for colon notation links (PageA:PageB:PageC)
        colon_iterator = COLON_LINK_PATTERN.globalMatch(block_text)
        while colon_iterator.hasNext():
            match = colon_iterator.next()
            start = match.capturedStart()
            end = start + match.capturedLength()
            if start <= rel < end:
                # Convert colon notation to forward slash notation to break the link pattern
                text_cursor = QTextCursor(block)
                text_cursor.setPosition(block.position() + start)
                text_cursor.setPosition(block.position() + end, QTextCursor.KeepAnchor)
                link_text = match.captured("link")
                text_cursor.insertText(link_text.replace(":", "/"))
                return

    def _edit_link_at_cursor(self, cursor: QTextCursor) -> None:
        """Open edit link dialog (reuse insert link UI) and replace link under cursor."""
        from .insert_link_dialog import InsertLinkDialog
        from PySide6.QtWidgets import QApplication
        # Find the main window to use as parent
        main_window = None
        widget = self
        while widget is not None:
            if widget.metaObject().className().endswith("MainWindow"):
                main_window = widget
                break
            widget = widget.parent()
        parent = main_window if main_window is not None else self.window()

        # Suspend Vi mode while dialog is open
        vi_was_active = self._vi_mode_active
        if vi_was_active:
            self.set_vi_mode(False)

        block = cursor.block()
        md = self._markdown_link_at_cursor(cursor)
        if md:
            start, end, text_val, link_val = md
        else:
            # Fallback: plain colon, CamelCase, or HTTP link
            link_val = self._link_under_cursor(cursor)
            if not link_val:
                return
            text_val = link_val
            # determine start/end of the match to replace
            rel = cursor.position() - block.position()
            # Check HTTP URL first
            http_it = HTTP_URL_PATTERN.globalMatch(block.text())
            rng = None
            while http_it.hasNext():
                m = http_it.next()
                s = m.capturedStart(); e = s + m.capturedLength()
                if s <= rel < e:
                    rng = (s,e)
                    break
            # Check colon notation
            if rng is None:
                it = COLON_LINK_PATTERN.globalMatch(block.text())
                while it.hasNext():
                    m = it.next()
                    s = m.capturedStart(); e = s + m.capturedLength()
                    if s <= rel < e:
                        rng = (s,e)
                        break
            # Check CamelCase
            if rng is None:
                it = CAMEL_LINK_PATTERN.globalMatch(block.text())
                while it.hasNext():
                    m = it.next()
                    s = m.capturedStart(); e = s + m.capturedLength()
                    if s <= rel < e:
                        rng = (s,e)
                        break
            if rng is None:
                return
            start, end = rng
        dlg = InsertLinkDialog(
            parent=parent,
            selected_text="",
            editing=True,
            initial_link_target=link_val,
            initial_link_label=text_val,
        )
        dlg.setWindowModality(Qt.ApplicationModal)
        dlg.activateWindow()
        dlg.raise_()
        dlg.search.setFocus()
        self.begin_dialog_block()
        try:
            self._suppress_focus_lost_once = True
            try:
                result = dlg.exec()
            finally:
                self._suppress_focus_lost_once = False
            if result == QDialog.Accepted:
                new_to = dlg.selected_colon_path() or link_val
                raw_label = dlg.selected_link_name() or ""
                if new_to and new_to.startswith(("http://", "https://")):
                    new_to = self._normalize_external_link(new_to)
                elif new_to:
                    new_to = ensure_root_colon_link(new_to)

                link_label = ""
                if raw_label:
                    match_left = raw_label.lstrip(":/")
                    target_left = new_to.lstrip(":/") if new_to else ""
                    if match_left != target_left:
                        link_label = raw_label
                tc = QTextCursor(block)
                tc.setPosition(block.position() + start)
                tc.setPosition(block.position() + end, QTextCursor.KeepAnchor)
                tc.removeSelectedText()
                self.setTextCursor(tc)
                self.insert_link(new_to, link_label)
        finally:
            self.end_dialog_block()
            # Always restore focus to the editor after dialog closes
            QTimer.singleShot(0, self.setFocus)
            # Restore Vi mode if it was active
            if vi_was_active:
                self.set_vi_mode(True)
    
    def _copy_link_to_location(self, link_text: str | None = None, anchor_text: Optional[str] = None) -> Optional[str]:
        """Copy a link location as colon-notation to clipboard.

        Args:
            link_text: The link text (e.g., 'PageName' from +PageName, or 'PageA:PageB:PageC' from colon link).
                      If None, copies the current page's location.
            anchor_text: Optional heading text to append as anchor when copying current page.
        """
        if link_text:
            # If it's a colon notation link, use it as-is
            if ":" in link_text:
                colon_path = link_text
            else:
                # It's a relative link (+PageName), resolve it relative to current page
                if not self._current_path:
                    return None
                # Get current page's colon path
                current_colon = path_to_colon(self._current_path)
                if not current_colon:
                    # We're at root, just use the link text
                    colon_path = link_text
                else:
                    # Append the link to current page's path
                    colon_path = f"{current_colon}:{link_text}"
        else:
            # Copy current page's location
            if not self._current_path:
                return None
            colon_path = path_to_colon(self._current_path)

        if colon_path:
            colon_path = ensure_root_colon_link(colon_path)
            if anchor_text and "#" not in colon_path:
                # Slugify the anchor text (convert spaces to dashes, lowercase)
                slugified_anchor = heading_slug(anchor_text)
                colon_path = f"{colon_path}#{slugified_anchor}"
            clipboard = QGuiApplication.clipboard()
            clipboard.setText(colon_path)
            self.linkCopied.emit(colon_path)
            # Keep vi clipboard in sync so 'p' in vi mode pastes this link
            self._vi_clipboard = colon_path
            return colon_path
        return None

    def _copy_link_or_heading(self) -> Optional[str]:
        """Copy link under cursor, otherwise current heading slugged link."""
        cursor = self.textCursor()
        # Prefer the raw link under cursor (preserves anchors)
        plain_link = self._link_under_cursor(cursor)
        if plain_link:
            return self._copy_link_to_location(link_text=plain_link)
        md_link = self._markdown_link_at_cursor(cursor)
        if md_link:
            # md_link = (start, end, text, link_target)
            target = md_link[3]
            return self._copy_link_to_location(link_text=target)
        heading_text = self.current_heading_text()
        if heading_text:
            return self._copy_link_to_location(link_text=None, anchor_text=heading_text)
        return None

    def copy_current_page_link(self) -> Optional[str]:
        """Copy current page (or heading) link and return the copied text."""
        heading_text = self.current_heading_text()
        return self._copy_link_to_location(link_text=None, anchor_text=heading_text)

    def _is_code_block_line(self, block) -> bool:
        """Return True if the block is a fenced code line or inside a code block."""
        try:
            if block.blockState() == MarkdownHighlighter.CODE_BLOCK_STATE:
                return True
        except Exception:
            pass
        try:
            if block.text().lstrip().startswith("```"):
                return True
        except Exception:
            return False
        return False

    def _should_finalize_heading_block(self, block) -> bool:
        if not block or not block.isValid():
            return False
        if self._is_code_block_line(block):
            return False
        text = block.text()
        stripped = text.lstrip()
        if stripped and heading_level_from_char(stripped[0]):
            return False
        if (
            self._pending_heading_block_num is not None
            and self._pending_heading_level is not None
            and block.blockNumber() == self._pending_heading_block_num
        ):
            return True
        if HEADING_MARK_PATTERN.match(text):
            return True
        return False

    def _emit_cursor(self) -> None:
        if (
            self._cursor_events_blocked
            or self._display_guard
            or self._suppress_link_scan
            or self._overlay_transition
            or type(self)._LOAD_GUARD_DEPTH > 0
            or not getattr(self, "_cursor_signals_connected", True)
        ):
            return
        if self._in_mode_window_transition():
            return
        cursor = self.textCursor()
        current_block_num = cursor.blockNumber()
        prev_block_num = self._last_heading_block_num
        if prev_block_num is None:
            self._last_heading_block_num = current_block_num
        elif prev_block_num != current_block_num:
            prev_block = self.document().findBlockByNumber(prev_block_num)
            if prev_block.isValid() and self._should_finalize_heading_block(prev_block):
                self._finalize_heading_block(prev_block)
            self._last_heading_block_num = current_block_num
        self.cursorMoved.emit(cursor.position())
        # Check if cursor is over a link and emit link path
        link = self._link_under_cursor(cursor)
        if link:
            self.linkHovered.emit(link)
        else:
            self.linkHovered.emit("")  # Empty string to clear status bar

    # --- Vi-mode cursor -------------------------------------------------
    def set_vi_block_cursor_enabled(self, enabled: bool) -> None:
        """Set whether vi-mode should show a block cursor. Does not affect vi-mode navigation."""
        self._vi_block_cursor_enabled = enabled
        # Refresh cursor display if currently in vi-mode
        if self._vi_mode_active:
            self._update_vi_cursor()

    def set_vi_mode_enabled(self, enabled: bool) -> None:
        """Globally enable or disable vi-style navigation."""
        if self._vi_feature_enabled == enabled:
            return
        self._vi_feature_enabled = enabled
        self._vi_replace_pending = False
        self._vi_pending_activation = False
        if enabled:
            if self.isVisible() and self._vi_has_painted:
                self._enter_vi_navigation_mode(force_emit=True)
            else:
                self._vi_pending_activation = True
                self._schedule_vi_activation()
        else:
            self._vi_insert_mode = False
            self.set_vi_mode(False)
            self.viInsertModeChanged.emit(False)

    def _schedule_vi_activation(self) -> None:
        if not self._vi_pending_activation or not self._vi_feature_enabled:
            return
        if self._vi_activation_timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(self._activate_pending_vi)
            self._vi_activation_timer = timer
        if not self._vi_activation_timer.isActive():
            self._vi_activation_timer.start(0)

    def _activate_pending_vi(self) -> None:
        if not self._vi_feature_enabled or not self._vi_pending_activation:
            return
        if self._vi_paint_in_progress:
            if self._vi_activation_timer is not None:
                self._vi_activation_timer.start(10)
            return
        if not self._vi_has_painted or not self.isVisible():
            if self._vi_activation_timer is not None:
                self._vi_activation_timer.start(10)
            return
        self._vi_pending_activation = False
        self._enter_vi_navigation_mode(force_emit=True)

    def _enter_vi_navigation_mode(self, force_emit: bool = False) -> None:
        if not self._vi_feature_enabled:
            return
        emit_needed = force_emit or self._vi_insert_mode
        self._vi_insert_mode = False
        self._vi_replace_pending = False
        self.set_vi_mode(True)
        if emit_needed:
            self.viInsertModeChanged.emit(False)

    def _enter_vi_insert_mode(self) -> None:
        if not self._vi_feature_enabled:
            return
        if self._vi_insert_mode and not self._vi_replace_pending:
            return
        self._vi_insert_mode = True
        self.set_vi_mode(False)
        self.viInsertModeChanged.emit(True)

    def _handle_vi_escape(self) -> bool:
        if not self._vi_feature_enabled:
            return False
        if self._vi_replace_pending:
            self._vi_replace_pending = False
            self._enter_vi_navigation_mode()
            return True
        if self._vi_insert_mode:
            self._enter_vi_navigation_mode()
            return True
        return False

    def _handle_escape_clear_empty_line(self) -> bool:
        if self._read_only_mode:
            return False
        cursor = self.textCursor()
        block = cursor.block()
        if not block.isValid():
            return False
        text = block.text()
        if text.strip():
            is_task, _, _, task_content = self._is_task_line(text)
            if is_task and not task_content.strip():
                pass
            else:
                is_bullet, _, bullet_content = self._is_bullet_line(text)
                if is_bullet and not bullet_content.strip():
                    pass
                else:
                    is_dash, _, dash_content = self._is_dash_line(text)
                    if is_dash and not dash_content.strip():
                        pass
                    else:
                        return False
        cursor.beginEditBlock()
        cursor.select(QTextCursor.LineUnderCursor)
        cursor.removeSelectedText()
        cursor.insertText("")
        cursor.setPosition(block.position())
        cursor.endEditBlock()
        self.setTextCursor(cursor)
        return True

    def _handle_vi_keypress(self, event: QKeyEvent) -> bool:
        if not self._vi_feature_enabled:
            return False
        mods = event.modifiers() & ~Qt.KeypadModifier
        # Copy link/heading (Ctrl+Shift+L) even in vi navigation mode
        key = event.key()
        text_char = event.text() or ""
        read_only = self._read_only_mode
        cursor = self.textCursor()

        def _block_vi_edit() -> bool:
            self._status_message("Vault is read-only; editing is disabled.")
            return True
        if mods == (Qt.ControlModifier | Qt.ShiftModifier) and key == Qt.Key_L:
            copied = self._copy_link_or_heading()
            window = self.window()
            try:
                if copied and window and hasattr(window, "statusBar"):
                    window.statusBar().showMessage(f"Copied link: {copied}", 2000)
            except Exception:
                pass
            if copied:
                self._vi_clipboard = copied
            return True
        # Allow Alt+H/J/K/L navigation even in vi mode
        if mods == Qt.AltModifier:
            if key == Qt.Key_H:
                self._trigger_history_navigation(Qt.Key_Left)
                return True
            if key == Qt.Key_L:
                self._trigger_history_navigation(Qt.Key_Right)
                return True
            if key == Qt.Key_J:
                self._trigger_history_navigation(Qt.Key_Down)
                return True
            if key == Qt.Key_K:
                self._trigger_history_navigation(Qt.Key_Up)
                return True
            return False
        if mods & Qt.ControlModifier:
            return False
        shift = bool(mods & Qt.ShiftModifier)
        other_mods = mods & ~(Qt.ShiftModifier)
        if other_mods:
            return False

        if self._vi_replace_pending:
            if key == Qt.Key_Escape:
                self._vi_replace_pending = False
                self._enter_vi_navigation_mode()
                return True
            if read_only:
                self._vi_replace_pending = False
                return _block_vi_edit()
            text = event.text()
            if text:
                char = text[0]
                self._vi_replace_char(char)
                self._vi_last_edit = lambda ch=char: self._vi_replace_char(ch)
            self._vi_replace_pending = False
            self._enter_vi_navigation_mode()
            return True

        if self._vi_insert_mode:
            if key == Qt.Key_Escape and not shift:
                self._enter_vi_navigation_mode()
                return True
            if read_only:
                return _block_vi_edit()
            return False

        if cursor.hasSelection() and text_char in ("-", "*"):
            if read_only:
                return _block_vi_edit()
            if text_char == "-":
                self._apply_list_style("dash")
                self._vi_last_edit = lambda: self._apply_list_style("dash")
            else:
                self._apply_list_style("bullet")
                self._vi_last_edit = lambda: self._apply_list_style("bullet")
            return True

        # Navigation mode commands
        if key == Qt.Key_Escape:
            if cursor.hasSelection():
                if read_only:
                    return _block_vi_edit()
                if self._apply_escape_selection_transform():
                    self._vi_last_edit = self._apply_escape_selection_transform
                    return True
            if self._handle_escape_clear_empty_line():
                return True
            return True
        if key == Qt.Key_G:
            if shift:
                self._vi_move_to_file_end()
            else:
                self._vi_move_to_file_start()
            return True

        if shift and key == Qt.Key_N:
            # If we're at the last line of the document, move the selection
            # to the end of the current line instead of attempting to move down.
            cursor = self.textCursor()
            block = cursor.block()
            if not block.isValid() or not block.next().isValid() or cursor.atEnd():
                # We're at the last line: select to the absolute end of document
                self._vi_move_cursor(QTextCursor.End, select=True)
            else:
                self._vi_move_cursor(QTextCursor.Down, select=True)
            return True

        if key == Qt.Key_Slash:
            self.request_find_bar(replace=False, backwards=False, seed=self._search_seed_query())
            return True
        if key == Qt.Key_Question:
            self.request_find_bar(replace=False, backwards=True, seed=self._search_seed_query())
            return True
        if key == Qt.Key_T and not shift:
            cursor_rect = self.cursorRect()
            viewport = self.viewport()
            prefer_above = False
            try:
                prefer_above = cursor_rect.center().y() > (viewport.height() // 2)
            except Exception:
                prefer_above = False
            global_point = viewport.mapToGlobal(cursor_rect.bottomLeft())
            self.headingPickerRequested.emit(global_point, prefer_above)
            return True
        if key == Qt.Key_N:
            self.search_repeat_last(reverse=False)
            return True
        if key == Qt.Key_Asterisk:
            self.search_word_under_cursor(backwards=False)
            return True
        if key == Qt.Key_NumberSign:
            self.search_word_under_cursor(backwards=True)
            return True

        if shift and key == Qt.Key_U:
            # If we're at the first line of the document, select to the absolute
            # start of the document (match Shift+Up behavior at top-of-file).
            cursor = self.textCursor()
            block = cursor.block()
            if not block.isValid() or not block.previous().isValid() or cursor.atStart():
                self._vi_move_cursor(QTextCursor.Start, select=True)
            else:
                self._vi_move_cursor(QTextCursor.Up, select=True)
            return True

        if key in (Qt.Key_Semicolon, Qt.Key_Colon):
            # ';' moves to EOL; with Shift (':') we select to EOL.
            self._vi_move_to_line_end(select=shift or key == Qt.Key_Colon)
            return True

        if key == Qt.Key_J and not shift:
            self._vi_move_cursor(QTextCursor.Down)
            return True
        if key == Qt.Key_K and not shift:
            self._vi_move_cursor(QTextCursor.Up)
            return True
        if key == Qt.Key_H:
            self._vi_move_cursor(QTextCursor.Left, select=shift)
            return True
        if key == Qt.Key_L:
            self._vi_move_cursor(QTextCursor.Right, select=shift)
            return True

        if key == Qt.Key_0 and not shift:
            self._vi_move_to_line_start()
            return True
        if key == Qt.Key_Q and not shift:
            self._vi_move_to_line_start()
            return True
        if key == Qt.Key_AsciiCircum and not shift:
            self._vi_move_to_first_nonblank()
            return True
        if key == Qt.Key_Dollar:
            self._vi_move_to_line_end(select=False)
            return True

        if key == Qt.Key_W and not shift:
            self._vi_move_word(forward=True)
            return True
        if key == Qt.Key_B and not shift:
            self._vi_move_word(forward=False)
            return True
        if key == Qt.Key_C and not shift:
            self._vi_copy_to_buffer()
            return True

        if key == Qt.Key_I and not shift:
            if read_only:
                return _block_vi_edit()
            self._vi_insert_before_cursor()
            return True
        if key == Qt.Key_A and not shift:
            if read_only:
                return _block_vi_edit()
            self._vi_insert_after_cursor()
            return True
        if key == Qt.Key_O:
            if read_only:
                return _block_vi_edit()
            if shift:
                self._vi_open_line_above()
            else:
                self._vi_open_line_below()
            return True

        if (key == Qt.Key_P or text_char.lower() == "p") and not shift:
            if read_only:
                return _block_vi_edit()
            inserted = self._vi_paste_buffer()
            if inserted:
                self._vi_last_edit = lambda text=inserted: self._vi_insert_text(text)
            return True

        if key == Qt.Key_X and not shift:
            if read_only:
                return _block_vi_edit()
            self._vi_cut_selection_or_char()
            self._vi_last_edit = self._vi_cut_selection_or_char
            return True
        if key == Qt.Key_D and not shift:
            if read_only:
                return _block_vi_edit()
            self._vi_delete_selection_or_line()
            self._vi_last_edit = self._vi_delete_selection_or_line
            return True
        if key == Qt.Key_R and not shift:
            if read_only:
                return _block_vi_edit()
            self._vi_replace_pending = True
            self._enter_vi_insert_mode()
            return True
        if key == Qt.Key_U and not shift:
            if read_only:
                return _block_vi_edit()
            self._undo_or_status()
            return True
        if key == Qt.Key_Y and not shift:
            if read_only:
                return _block_vi_edit()
            self._redo_or_status()
            return True
        if key == Qt.Key_Period and not shift:
            if read_only:
                return _block_vi_edit()
            self._vi_repeat_last_edit()
            return True

        if key in (Qt.Key_Backspace, Qt.Key_Delete, Qt.Key_Return, Qt.Key_Enter):
            if key in (Qt.Key_Return, Qt.Key_Enter):
                cursor = self.textCursor()
                if self._is_cursor_at_link_activation_point(cursor):
                    link = self._link_under_cursor(cursor)
                    if link:
                        self.linkActivated.emit(link)
            if read_only and key in (Qt.Key_Backspace, Qt.Key_Delete):
                return _block_vi_edit()
            return True

        if key in (Qt.Key_Tab, Qt.Key_Backtab):
            return False

        text = event.text()
        if text:
            if read_only:
                return _block_vi_edit()
            return True
        return False

    def _vi_move_cursor(self, op: QTextCursor.MoveOperation, select: bool = False, count: int = 1) -> None:
        cursor = self.textCursor()
        mode = QTextCursor.KeepAnchor if select else QTextCursor.MoveAnchor
        cursor.movePosition(op, mode, max(1, count))
        cursor = self._clamp_cursor(cursor)
        self.setTextCursor(cursor)
        if not select and op in (QTextCursor.Left, QTextCursor.Right):
            # Skip hidden link sentinels so vi-mode can enter/exit links cleanly
            self._handle_link_boundary_navigation(Qt.Key_Left if op == QTextCursor.Left else Qt.Key_Right)

    def _vi_move_to_line_start(self) -> None:
        self._vi_move_cursor(QTextCursor.StartOfLine)

    def _vi_move_to_line_end(self, select: bool) -> None:
        self._vi_move_cursor(QTextCursor.EndOfLine, select=select)

    def _vi_move_to_first_nonblank(self) -> None:
        cursor = self.textCursor()
        block = cursor.block()
        text = block.text()
        stripped = text.lstrip(" \t")
        offset = len(text) - len(stripped)
        cursor.setPosition(block.position() + offset)
        cursor = self._clamp_cursor(cursor)
        self.setTextCursor(cursor)

    def _vi_move_to_file_start(self) -> None:
        self._vi_move_cursor(QTextCursor.Start)

    def _vi_move_to_file_end(self) -> None:
        self._vi_move_cursor(QTextCursor.End)

    def _open_vi_command_prompt(self) -> None:
        """Handle minimal vi-style command input (:%s/old/new/g)."""
        try:
            cmd, ok = QInputDialog.getText(self, "Command", ":")
        except Exception:
            return
        if not ok:
            return
        cmd_str = (cmd or "").strip()
        if cmd_str.startswith("%s/") and cmd_str.endswith("/g"):
            body = cmd_str[3:-2]
            if "/" not in body:
                self._status_message("Invalid substitution command.")
                return
            old, new = body.split("/", 1)
            if not old:
                self._status_message("Empty search pattern.")
                return
            self.search_replace_all(old, new)
            return
        self._status_message("Unknown command.")

    def _vi_move_word(self, forward: bool) -> None:
        op = QTextCursor.WordRight if forward else QTextCursor.WordLeft
        self._vi_move_cursor(op)

    def _system_clipboard_text(self) -> str:
        try:
            return QGuiApplication.clipboard().text() or ""
        except Exception:
            return ""

    def _system_clipboard_set(self, text: str) -> None:
        try:
            QGuiApplication.clipboard().setText(text)
        except Exception:
            pass

    def _undo_or_status(self) -> None:
        try:
            doc = self.document()
            if not doc or not doc.isUndoAvailable():
                self._status_message("Nothing to undo.")
                return
            cursor_before = QTextCursor(self.textCursor())
            # Perform the normal undo first
            self.undo()
            # If a one-shot placeholder remains (it was inserted outside the undo stack),
            # replace it with the original prompt so Undo fully restores the prior state.
            try:
                ph_start = getattr(self, "_one_shot_placeholder_start", None)
                ph_len = getattr(self, "_one_shot_placeholder_len", None)
                orig = getattr(self, "_one_shot_original_text", None)
                if ph_start is not None and ph_len is not None:
                    full = self.toPlainText()
                    # Ensure indices are within range
                    if ph_start >= 0 and ph_start + ph_len <= len(full):
                        snippet = full[ph_start: ph_start + ph_len]
                        if snippet == "Executing one shot prompt...":
                            # Replace placeholder with original text without adding to undo stack
                            try:
                                doc.setUndoRedoEnabled(False)
                            except Exception:
                                pass
                            try:
                                sel = QTextCursor(doc)
                                sel.setPosition(ph_start)
                                sel.setPosition(ph_start + ph_len, QTextCursor.KeepAnchor)
                                sel.beginEditBlock()
                                sel.removeSelectedText()
                                if orig:
                                    sel.insertText(orig)
                                sel.endEditBlock()
                            except Exception:
                                pass
                            try:
                                # restore undo state
                                doc.setUndoRedoEnabled(True)
                            except Exception:
                                pass
                            # Clear one-shot markers
                            try:
                                del self._one_shot_placeholder_start
                            except Exception:
                                pass
                            try:
                                del self._one_shot_placeholder_len
                            except Exception:
                                pass
                            try:
                                del self._one_shot_original_text
                            except Exception:
                                pass
                            return
            except Exception:
                pass
            if not doc.isUndoAvailable():
                # Reached the start of the stack; keep cursor stable to avoid jumps.
                self.setTextCursor(cursor_before)
        except Exception:
            pass

    def _redo_or_status(self) -> None:
        try:
            doc = self.document()
            if not doc or not doc.isRedoAvailable():
                self._status_message("Nothing to redo.")
                return
            cursor_before = QTextCursor(self.textCursor())
            self.redo()
            if not doc.isRedoAvailable():
                # Reached the end of the stack; keep cursor stable to avoid jumps.
                self.setTextCursor(cursor_before)
        except Exception:
            pass

    def _vi_copy_to_buffer(self) -> bool:
        text = self._vi_selected_text_or_line()
        if text is None:
            return False
        self._vi_clipboard = text
        self._system_clipboard_set(text)
        return True

    def _vi_selected_text_or_line(self) -> Optional[str]:
        cursor = QTextCursor(self.textCursor())
        if cursor.hasSelection():
            text = self._vi_selection_as_markdown(cursor)
        else:
            block = cursor.block()
            if not block.isValid():
                return None
            cursor.select(QTextCursor.LineUnderCursor)
            text = self._vi_selection_as_markdown(cursor)
        if text is None:
            return None
        normalized = text.replace("\u2029", "\n")
        return normalized if normalized else None

    def _vi_selection_as_markdown(self, cursor: QTextCursor) -> Optional[str]:
        """Return selected content as markdown, preserving images."""
        if not cursor.hasSelection():
            return None
        start = cursor.selectionStart()
        end = cursor.selectionEnd()
        doc_cursor = QTextCursor(self.document())
        doc_cursor.setPosition(start)
        parts: list[str] = []
        while doc_cursor.position() < end:
            doc_cursor.setPosition(doc_cursor.position() + 1, QTextCursor.KeepAnchor)
            fmt = doc_cursor.charFormat()
            if fmt.isImageFormat():
                parts.append(self._markdown_from_image_format(fmt.toImageFormat()))
            else:
                part = doc_cursor.selectedText()
                if part:
                    parts.append(part)
            doc_cursor.setPosition(doc_cursor.position())
        if not parts:
            return None
        return "".join(parts)

    def _vi_selection_text(self, cursor: QTextCursor) -> Optional[str]:
        text = self._vi_selection_as_markdown(cursor)
        if text is None:
            if not cursor.hasSelection():
                return None
            text = cursor.selectedText()
        if not text:
            return None
        normalized = text.replace("\u2029", "\n")
        if LINK_SENTINEL in normalized:
            try:
                normalized = self._from_display(normalized)
            except Exception:
                pass
        return normalized if normalized else None

    def _vi_cut_selection_or_char(self) -> None:
        cursor = self.textCursor()
        
        # Check for sentinel deletion before proceeding
        if cursor.hasSelection():
            selected_text = cursor.selectedText()
            if LINK_SENTINEL in selected_text:
                start = cursor.selectionStart()
                end = cursor.selectionEnd()
                full_text = self.toPlainText()
                link_ranges = [(m.start(), m.end()) for m in WIKI_LINK_DISPLAY_PATTERN.finditer(full_text)]
                adjusted_start = start
                adjusted_end = end
                for link_start, link_end in link_ranges:
                    if not (end <= link_start or start >= link_end):
                        adjusted_start = min(adjusted_start, link_start)
                        adjusted_end = max(adjusted_end, link_end)
                yank_cursor = QTextCursor(self.document())
                yank_cursor.setPosition(adjusted_start)
                yank_cursor.setPosition(adjusted_end, QTextCursor.KeepAnchor)
                normalized = self._vi_selection_text(yank_cursor)
                # Use the safe deletion method
                self._delete_selection_with_links(cursor)
                if normalized:
                    self._vi_clipboard = normalized
                    self._system_clipboard_set(normalized)
                return
        else:
            # Check if next character is a sentinel, or if cursor is right after a link's closing sentinel
            block = cursor.block()
            rel_pos = cursor.position() - block.position()
            text = block.text()
            if rel_pos < len(text) and text[rel_pos] == LINK_SENTINEL:
                link_range = self._link_range_in_block(text, rel_pos)
                if link_range:
                    link_start, link_end = link_range
                    yank_cursor = QTextCursor(block)
                    yank_cursor.setPosition(block.position() + link_start)
                    yank_cursor.setPosition(block.position() + link_end, QTextCursor.KeepAnchor)
                    normalized = self._vi_selection_text(yank_cursor)
                    yank_cursor.removeSelectedText()
                    self.setTextCursor(yank_cursor)
                    if normalized:
                        self._vi_clipboard = normalized
                        self._system_clipboard_set(normalized)
                return
            # Check if we're right after a link's closing sentinel
            if rel_pos > 0 and (rel_pos - 1) < len(text) and text[rel_pos - 1] == LINK_SENTINEL:
                # Find if this is a closing sentinel of a link
                link_range = self._link_range_in_block(text, rel_pos - 1)
                if link_range:
                    link_start, link_end = link_range
                    yank_cursor = QTextCursor(block)
                    yank_cursor.setPosition(block.position() + link_start)
                    yank_cursor.setPosition(block.position() + link_end, QTextCursor.KeepAnchor)
                    normalized = self._vi_selection_text(yank_cursor)
                    yank_cursor.removeSelectedText()
                    self.setTextCursor(yank_cursor)
                    if normalized:
                        self._vi_clipboard = normalized
                        self._system_clipboard_set(normalized)
                return
        
        cursor.beginEditBlock()
        text = ""
        if cursor.hasSelection():
            text = self._vi_selection_as_markdown(cursor) or ""
            cursor.removeSelectedText()
        else:
            cursor.movePosition(QTextCursor.Right, QTextCursor.KeepAnchor)
            sel_text = self._vi_selection_as_markdown(cursor)
            text = sel_text if sel_text is not None else cursor.selectedText()
            if text:
                cursor.removeSelectedText()
        cursor.endEditBlock()
        self.setTextCursor(cursor)
        normalized = text.replace("\u2029", "\n") if text else ""
        if normalized:
            self._vi_clipboard = normalized
            self._system_clipboard_set(normalized)

    def _vi_insert_text(self, text: str) -> None:
        if not text:
            return
        cursor = self.textCursor()
        cursor.beginEditBlock()
        if cursor.hasSelection():
            cursor.removeSelectedText()
        cursor.insertText(text)
        cursor.endEditBlock()
        self.setTextCursor(cursor)
        try:
            has_wiki = bool(WIKI_LINK_STORAGE_PATTERN.search(text))
            has_colon = any(COLON_LINK_PATTERN.match(line).hasMatch() for line in text.splitlines())
        except Exception:
            has_wiki = False
            has_colon = False
        if has_wiki or has_colon:
            self._refresh_display()
        if "![" in text:
            self._render_images(self.toPlainText())

    def _vi_paste_buffer(self) -> Optional[str]:
        sys_clip = self._system_clipboard_text()
        if sys_clip:
            self._vi_clipboard = sys_clip
        if not self._vi_clipboard:
            return None
        self._vi_insert_text(self._vi_clipboard)
        return self._vi_clipboard

    def _vi_delete_line(self) -> None:
        cursor = self.textCursor()
        cursor.beginEditBlock()
        cursor.select(QTextCursor.LineUnderCursor)
        cursor.removeSelectedText()
        if not cursor.atEnd():
            cursor.deleteChar()
        cursor.endEditBlock()
        self.setTextCursor(cursor)
    
    def _vi_delete_selection_or_line(self) -> None:
        """Delete current selection; if none, delete the current line. Does not yank."""
        cursor = self.textCursor()
        cursor.beginEditBlock()
        if cursor.hasSelection():
            cursor.removeSelectedText()
        else:
            cursor.select(QTextCursor.LineUnderCursor)
            cursor.removeSelectedText()
            if not cursor.atEnd():
                cursor.deleteChar()
        cursor.endEditBlock()
        self.setTextCursor(cursor)

    def _vi_replace_char(self, char: str) -> None:
        if not char:
            return
        cursor = self.textCursor()
        if cursor.atEnd():
            return
        cursor.beginEditBlock()
        cursor.deleteChar()
        cursor.insertText(char)
        cursor.endEditBlock()
        self.setTextCursor(cursor)

    def _vi_open_line_below(self) -> None:
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.EndOfLine)
        self.setTextCursor(cursor)
        if not self._handle_enter_indent_same_level():
            cursor = self.textCursor()
            cursor.insertBlock()
            self.setTextCursor(cursor)
        self._enter_vi_insert_mode()

    def _vi_open_line_above(self) -> None:
        cursor = self.textCursor()
        block = cursor.block()
        text = block.text()
        indent = text[: len(text) - len(text.lstrip(" \t"))]
        cursor.beginEditBlock()
        cursor.movePosition(QTextCursor.StartOfLine)
        cursor.insertText(f"{indent}\n")
        cursor.movePosition(QTextCursor.Up)
        cursor.movePosition(QTextCursor.StartOfLine)
        cursor.movePosition(QTextCursor.Right, QTextCursor.MoveAnchor, len(indent))
        cursor.endEditBlock()
        self.setTextCursor(cursor)
        self._enter_vi_insert_mode()

    def _vi_insert_before_cursor(self) -> None:
        self._enter_vi_insert_mode()

    def _vi_insert_after_cursor(self) -> None:
        cursor = self.textCursor()
        if not cursor.atEnd():
            cursor.movePosition(QTextCursor.Right)
            self.setTextCursor(cursor)
        self._enter_vi_insert_mode()

    def _vi_repeat_last_edit(self) -> None:
        if self._vi_last_edit:
            self._vi_last_edit()

    def set_vi_mode(self, active: bool) -> None:
        """Enable or disable vi-mode cursor styling (pink block)."""
        if active and not self._vi_feature_enabled:
            active = False
        if self._vi_mode_active == active:
            return
        self._vi_mode_active = active
        # Disable cursor blinking while in vi-mode to avoid flicker with overlay (only if block cursor enabled)
        if active and self._vi_block_cursor_enabled:
            if self._vi_saved_flash_time is None:
                try:
                    self._vi_saved_flash_time = QGuiApplication.cursorFlashTime()
                except Exception:
                    self._vi_saved_flash_time = 1000
            try:
                QGuiApplication.setCursorFlashTime(0)
            except Exception:
                pass
        else:
            if self._vi_saved_flash_time is not None:
                try:
                    QGuiApplication.setCursorFlashTime(self._vi_saved_flash_time)
                except Exception:
                    pass
            self._vi_saved_flash_time = None
            self._vi_last_cursor_pos = -1
        self._update_vi_cursor()
        # Ensure editor focus when vi mode is toggled and no dialog is open
        if not self._dialog_block_input:
            self.setFocus()

    def _maybe_update_vi_cursor(self) -> None:
        if (
            self._cursor_events_blocked
            or self._display_guard
            or self._suppress_vi_cursor
            or type(self)._LOAD_GUARD_DEPTH > 0
            or self._in_mode_window_transition()
        ):
            return
        if not self._vi_mode_active or not self._vi_block_cursor_enabled:
            return
        pos = self.textCursor().position()
        if pos == self._vi_last_cursor_pos:
            return
        self._vi_last_cursor_pos = pos
        self._update_vi_cursor()

    def _clamp_cursor(self, cursor: QTextCursor) -> QTextCursor:
        """Clamp cursor position to the document length to avoid out-of-range warnings."""
        try:
            length = len(self.toPlainText())
        except Exception:
            length = cursor.document().characterCount()
        safe = max(0, min(cursor.position(), max(0, length)))
        if safe != cursor.position():
            cursor.setPosition(safe)
        return cursor

    def _ensure_cursor_margin(self) -> None:
        """Keep a small margin at the bottom of the viewport for visibility while editing."""
        sb = self.verticalScrollBar()
        if not sb:
            return
        rect = self.cursorRect()
        viewport = self.viewport()
        if not viewport:
            return
        view_h = viewport.height()
        margin = 48
        overshoot = rect.bottom() - (view_h - margin)
        if overshoot > 0:
            sb.setValue(min(sb.maximum(), sb.value() + overshoot))

    def _in_mode_window_transition(self) -> bool:
        """Return True if the editor lives in a ModeWindow that isn't settled yet."""
        if getattr(self, "_suppress_link_scan", False):
            return True
        if getattr(self, "_overlay_transition", False):
            return True
        try:
            win = self.window()
        except Exception:
            return False
        if not win:
            return False
        try:
            if getattr(win.__class__, "__name__", "") == "ModeWindow":
                if not getattr(win, "_ready", True) or getattr(win, "_pending_close", False):
                    return True
            if hasattr(win, "_mode_window_pending") and getattr(win, "_mode_window_pending"):
                return True
        except Exception:
            return False
        return False

    def _update_vi_cursor(self) -> None:
        if (
            self._cursor_events_blocked
            or self._display_guard
            or self._suppress_vi_cursor
            or type(self)._LOAD_GUARD_DEPTH > 0
            or self._in_mode_window_transition()
        ):
            return
        if not self._vi_mode_active or not self._vi_block_cursor_enabled:
            # Clear any vi-mode selection overlay but preserve other selections (e.g., flashes)
            remaining = [s for s in self.extraSelections() if s.format.property(self._VI_EXTRA_KEY) is None]
            self.setExtraSelections(remaining)
            return
        doc = self.document()
        if doc is None or doc.isEmpty():
            return
        cursor = self.textCursor()
        # Don't draw block cursor overlay while there's an active selection
        if cursor.hasSelection():
            remaining = [s for s in self.extraSelections() if s.format.property(self._VI_EXTRA_KEY) is None]
            self.setExtraSelections(remaining)
            return
        block_cursor = QTextCursor(cursor)
        if not block_cursor.atEnd():
            # Select the character under the caret to form a block
            block_cursor.movePosition(QTextCursor.Right, QTextCursor.KeepAnchor)
        extra = QTextEdit.ExtraSelection()
        extra.cursor = block_cursor
        fmt = extra.format
        fmt.setBackground(QColor("#b259ff"))  # purple block
        fmt.setForeground(QColor("#111"))     # dark text for contrast
        fmt.setProperty(QTextFormat.FullWidthSelection, False)
        fmt.setProperty(self._VI_EXTRA_KEY, True)
        existing = [s for s in self.extraSelections() if s.format.property(self._VI_EXTRA_KEY) is None]
        existing.append(extra)
        self.setExtraSelections(existing)

    def eventFilter(self, obj, event):  # type: ignore[override]
        if obj is self.viewport():
            if self._cursor_events_blocked or self._display_guard or self._suppress_vi_cursor or self._in_mode_window_transition():
                return super().eventFilter(obj, event)
            if event.type() in (QEvent.Paint, QEvent.UpdateRequest, QEvent.FocusIn, QEvent.FocusOut):
                if self._vi_mode_active:
                    self._update_vi_cursor()
        return super().eventFilter(obj, event)

    def resizeEvent(self, event):  # type: ignore[override]
        super().resizeEvent(event)
        self.viewportResized.emit()
        # Reapply scroll-past-end margin on resize
        self._apply_scroll_past_end_margin()

    def _to_display(self, text: str) -> str:
        def _symbol_for(state: str) -> str:
            return "☑" if state.strip().lower() == "x" else "☐"

        def _format_task_symbol(indent: str, symbol: str, body: str) -> str:
            body = (body or "").lstrip()
            spacer = "  " if not body else " "
            return f"{indent}{symbol}{spacer}{body}"

        def _md_repl(match: re.Match[str]) -> str:
            symbol = _symbol_for(match.group("state"))
            return _format_task_symbol(match.group(1), symbol, match.group("body"))

        def _symbol_repl(match: re.Match[str]) -> str:
            symbol = _symbol_for("x" if match.group(2) == "☑" else " ")
            return _format_task_symbol(match.group(1), symbol, match.group("body"))

        converted = TASK_LINE_PATTERN.sub(_md_repl, text)
        converted = SYMBOL_TASK_LINE_PATTERN.sub(_symbol_repl, converted)
        converted = HEADING_MARK_PATTERN.sub(self._encode_heading, converted)
        # Transform wiki-style links: [link|label] → sentinel + link + sentinel + label + sentinel
        converted = WIKI_LINK_STORAGE_PATTERN.sub(self._encode_wiki_link, converted)
        # Transform bullets: * → •
        converted = BULLET_STORAGE_PATTERN.sub(r"\1• ", converted)
        return converted

    def _from_display(self, text: str) -> str:
        # Restore wiki-style links: sentinel + link + sentinel + label + sentinel → [link|label]
        restored = WIKI_LINK_DISPLAY_PATTERN.sub(self._decode_wiki_link, text)
        # Drop duplicated link tails that sometimes get re-appended after decoding.
        def _dedupe_tail(m: re.Match[str]) -> str:
            link = m.group("link")
            label = m.group("label")
            tail = m.group("tail")
            if tail and link.endswith(tail):
                return f"[{link}|{label}]"
            return m.group(0)

        restored = WIKI_LINK_DUPLICATE_TAIL_PATTERN.sub(_dedupe_tail, restored)
        restored = HEADING_DISPLAY_PATTERN.sub(self._decode_heading, restored)
        restored = SYMBOL_TASK_LINE_PATTERN.sub(
            lambda m: f"{m.group(1)}- [{'x' if m.group(2) == '☑' else ' '}] {m.group('body')}",
            restored,
        )
        # Restore bullets: • → *
        restored = BULLET_DISPLAY_PATTERN.sub(r"\1* ", restored)
        return restored

    def _encode_heading(self, match: re.Match[str]) -> str:
        indent, hashes, _, body = match.groups()
        level = min(len(hashes), HEADING_MAX_LEVEL)
        sentinel = heading_sentinel(level)
        return f"{indent}{sentinel}{body}"

    def _decode_heading(self, match: re.Match[str]) -> str:
        indent, marker, body = match.groups()
        level = heading_level_from_char(marker)
        if not level:
            return match.group(0)
        clean_body = body.lstrip()
        spacer = " " if clean_body else ""
        hashes = "#" * level
        return f"{indent}{hashes}{spacer}{clean_body}"

    def _encode_wiki_link(self, match: re.Match[str]) -> str:
        """Convert [link|label] to hidden format, preserving empty-label marker."""
        link = match.group("link")
        label = match.group("label")
        if not label:
            # Inject a trailing pipe so plain-text round trips keep the wiki delimiter.
            return f"{LINK_SENTINEL}{link}|{LINK_SENTINEL}{LINK_SENTINEL}"
        return f"{LINK_SENTINEL}{link}{LINK_SENTINEL}{label}{LINK_SENTINEL}"

    def _decode_wiki_link(self, match: re.Match[str]) -> str:
        """Convert hidden format sentinel+link+sentinel+label+sentinel back to [link|label]"""
        link = match.group("link")
        label = match.group("label")
        if not label and link.endswith("|"):
            link = link[:-1]
        return f"[{link}|{label}]"

    def refresh_heading_outline(self) -> None:
        """Force computation of heading outline immediately."""
        self._emit_heading_outline()

    def _schedule_heading_outline(self) -> None:
        if self._display_guard:
            return
        self._heading_timer.start()

    def _schedule_hr_selections(self) -> None:
        if self._display_guard:
            return
        if self._hr_timer.isActive():
            return
        self._hr_timer.start()

    def _refresh_hr_selections(self) -> None:
        doc = self.document()
        if doc is None:
            return
        selections: list[QTextEdit.ExtraSelection] = []
        block = doc.begin()
        while block.isValid():
            if block.text().strip() in ("---", "***", "___"):
                cursor = QTextCursor(block)
                cursor.select(QTextCursor.LineUnderCursor)
                sel = QTextEdit.ExtraSelection()
                sel.cursor = cursor
                fmt = sel.format
                fmt.setBackground(QColor("#333333"))
                fmt.setForeground(QColor("#333333"))
                fmt.setProperty(QTextFormat.FullWidthSelection, True)
                fmt.setProperty(self._HR_EXTRA_KEY, True)
                selections.append(sel)
            block = block.next()
        existing = [s for s in self.extraSelections() if s.format.property(self._HR_EXTRA_KEY) is None]
        existing.extend(selections)
        self.setExtraSelections(existing)

    def _emit_heading_outline(self) -> None:
        outline: list[dict] = []
        block = self.document().firstBlock()
        while block.isValid():
            text = block.text()
            stripped = text.lstrip()
            if stripped.strip() in ("---", "***", "___"):
                outline.append(
                    {
                        "level": 1,
                        "title": "---",
                        "line": block.blockNumber() + 1,
                        "position": block.position(),
                        "type": "hr",
                    }
                )
                block = block.next()
                continue
            if stripped:
                level = heading_level_from_char(stripped[0])
                title = None
                if not level:
                    match = HEADING_MARK_PATTERN.match(text)
                    if match:
                        hashes = match.group(2)
                        title = match.group(4).strip()
                        level = min(len(hashes), HEADING_MAX_LEVEL)
                else:
                    title = stripped[1:].strip()
                if level:
                    cursor = QTextCursor(block)
                    outline.append(
                        {
                            "level": level,
                            "title": title,
                            "line": block.blockNumber() + 1,
                            "position": cursor.position(),
                        }
                    )
            block = block.next()
        self._heading_outline = outline
        self.headingsChanged.emit(outline)

    def jump_to_anchor(self, anchor: str) -> bool:
        slug = heading_slug(anchor)
        if not slug:
            return False
        for entry in self._heading_outline:
            if heading_slug(entry.get("title", "")) == slug:
                cursor = self.textCursor()
                cursor.setPosition(int(entry.get("position", 0)))
                self.setTextCursor(cursor)
                self.ensureCursorVisible()
                return True
        return False

    def current_heading_slug(self) -> Optional[str]:
        """Return slug of heading on current line, if any."""
        if not self._heading_outline:
            return None
        line_no = self.textCursor().blockNumber() + 1
        for entry in self._heading_outline:
            if int(entry.get("line", 0)) == line_no:
                slug = heading_slug(entry.get("title", ""))
                return slug or None
        return None

    def current_heading_text(self) -> Optional[str]:
        """Return original text of heading on current line, if any."""
        if not self._heading_outline:
            return None
        line_no = self.textCursor().blockNumber() + 1
        for entry in self._heading_outline:
            if int(entry.get("line", 0)) == line_no:
                return entry.get("title", "") or None
        return None

    def _heading_text_for_cursor(self, cursor: QTextCursor) -> Optional[str]:
        if not cursor:
            return None
        block = cursor.block()
        if not block.isValid():
            return None
        text = block.text()
        stripped = text.lstrip()
        if not stripped:
            return None
        if stripped.strip() in ("---", "***", "___"):
            return None
        level = heading_level_from_char(stripped[0])
        title = None
        if not level:
            match = HEADING_MARK_PATTERN.match(text)
            if match:
                hashes = match.group(2)
                title = match.group(4).strip()
                level = min(len(hashes), HEADING_MAX_LEVEL)
        else:
            title = stripped[1:].strip()
        if level and title:
            return title
        return None

    def _refresh_display(self) -> None:
        """Force full document re-render to apply display transformations.
        
        This converts storage format (markdown syntax) to display format (with hidden syntax).
        Used after inserting/editing links or pasting content that may contain links.
        """
        self._display_guard = True
        current_text = self.toPlainText()
        # First convert FROM display back to storage (in case text is already partially in display format)
        storage_text = self._from_display(current_text)
        # Convert +CamelCase links immediately so display is updated without waiting for save
        storage_text = self._convert_camelcase_links(storage_text)
        storage_text = self._wrap_plain_http_links(storage_text)
        # Then convert to display format
        display_text = self._to_display(storage_text)
        old_cursor_pos = self.textCursor().position()
        doc = self.document()
        replace_cursor = QTextCursor(doc)
        replace_cursor.beginEditBlock()
        replace_cursor.select(QTextCursor.Document)
        replace_cursor.insertText(display_text)
        replace_cursor.endEditBlock()
        # Restore cursor position (approximately)
        new_cursor = self.textCursor()
        new_cursor.setPosition(min(old_cursor_pos, len(display_text)))
        self.setTextCursor(new_cursor)
        self._render_images(display_text)
        self._display_guard = False
        self._schedule_heading_outline()
        self._apply_scroll_past_end_margin()
        self._apply_scroll_past_end_margin()

    def _enforce_display_symbols(self) -> None:
        """Normalize display formatting on the current line only.

        Avoid full-document rewrites to preserve inline image fragments and
        prevent cursor jumps or spurious newlines when typing.
        """
        if self._display_guard or self._enforce_display_guard:
            return
        self._enforce_display_guard = True
        try:
            cursor = self.textCursor()
            block = cursor.block()
            if not block.isValid():
                return

            original = block.text()

            # 1) Normalize checkbox syntax to display symbols.
            def _symbol_for(state: str) -> str:
                return "☑" if state.strip().lower() == "x" else "☐"

            def _format_task_symbol(indent: str, symbol: str, body: str) -> str:
                body = (body or "").lstrip()
                spacer = "  " if not body else " "
                return f"{indent}{symbol}{spacer}{body}"

            def md_repl(match: re.Match[str]) -> str:
                symbol = _symbol_for(match.group("state") or " ")
                return _format_task_symbol(match.group(1), symbol, match.group("body"))

            def symbol_repl(match: re.Match[str]) -> str:
                symbol = _symbol_for("x" if match.group(2) == "☑" else " ")
                return _format_task_symbol(match.group(1), symbol, match.group("body"))

            line = original
            shortcut_match = re.match(r"^(\s*)\((?P<state>[xX]?)\)\s*(?P<body>.*)$", line)
            if shortcut_match:
                symbol = _symbol_for(shortcut_match.group("state") or " ")
                line = _format_task_symbol(
                    shortcut_match.group(1) or "",
                    symbol,
                    shortcut_match.group("body") or "",
                )

            line = TASK_LINE_PATTERN.sub(md_repl, line)
            line = SYMBOL_TASK_LINE_PATTERN.sub(symbol_repl, line)

            # 2) Heading marks: #'s → sentinel on this line only (unless actively editing)
            delay_heading_render = False
            if HEADING_MARK_PATTERN.match(line):
                if self._is_code_block_line(block):
                    delay_heading_render = True
                else:
                    stripped = line.lstrip()
                    # Keep hashes visible while the cursor is on this line; render after Enter.
                    if not (stripped and heading_level_from_char(stripped[0])):
                        delay_heading_render = True
            if not delay_heading_render:
                line = HEADING_MARK_PATTERN.sub(self._encode_heading, line)

            # 3) Wiki-style links: [link|label] → sentinel + link + sentinel + label + sentinel
            line = WIKI_LINK_STORAGE_PATTERN.sub(self._encode_wiki_link, line)
            
            # 4) Bullet conversion: Convert "* " at start of line (after whitespace) to bullet
            # Only convert when user types "* " followed by space
            stripped = line.lstrip()
            if stripped.startswith("* ") and len(stripped) > 2:
                # Check if this is a new bullet being typed (cursor should be after "* ")
                abs_pos = cursor.position()
                line_start = block.position()
                rel_pos = abs_pos - line_start
                indent = line[:len(line) - len(stripped)]
                bullet_pos = len(indent) + 2  # Position after "* "
                
                # Only convert if cursor is near the bullet marker position
                # This prevents conversion when just navigating through existing bullets
                if abs(rel_pos - bullet_pos) <= 2:
                    # Convert the * to a bullet point (•)
                    line = indent + "• " + stripped[2:]

            if line == original:
                # Even if text is unchanged, ensure hanging indent matches current line type
                is_bullet, bullet_indent, _ = self._is_bullet_line(line)
                is_dash, dash_indent, _ = self._is_dash_line(line)
                is_task, task_indent, _, _ = self._is_task_line(line)
                if is_bullet:
                    self._apply_hanging_indent(block, bullet_indent, "• ")
                elif is_dash:
                    self._apply_hanging_indent(block, dash_indent, "- ")
                elif is_task:
                    marker = "☑ " if line.strip().startswith("☑") else "☐ "
                    self._apply_hanging_indent(block, task_indent, marker)
                else:
                    self._clear_hanging_indent(block)
                
                # Force rehighlight to ensure sentinels stay hidden
                # This is especially important after deletions where formatting may be lost
                if LINK_SENTINEL in line:
                    self.highlighter.rehighlightBlock(block)
                return
        finally:
            self._enforce_display_guard = False
    
    def _check_block_count_change(self) -> None:
        """Clear highlighter cache when block count changes (lines added/deleted)."""
        if self._block_count_guard or self._processing_inline_trigger or self._display_guard:
            return
        self._block_count_guard = True
        current_count = self.document().blockCount()
        if hasattr(self, '_last_block_count') and self._last_block_count != current_count:
            # Block structure changed - invalidate highlighter cache
            if hasattr(self.highlighter, '_block_cache'):
                self.highlighter._block_cache.clear()
                self.highlighter._cache_access_order.clear()
            self.highlighter.rehighlight()
        self._last_block_count = current_count
        self._block_count_guard = False
        # All caret/line/block logic removed; only cache invalidation and block count update remain

    def _restore_cursor_position(self, position: int) -> None:
        cursor = self.textCursor()
        cursor.setPosition(min(position, len(self.toPlainText())))
        self.setTextCursor(cursor)

    def _apply_hanging_indent(self, block, indent: str, marker: str) -> None:
        """Indent wrapped lines to start after bullet/task markers."""
        if not block or not block.isValid():
            return
        # Prevent recursion - setBlockFormat can trigger events on Windows
        # that may call back into formatting code
        if self._display_guard or self._hanging_indent_guard:
            return
        fm = self.fontMetrics()
        # Calculate tab width properly for hanging indent
        # Replace tabs with equivalent spaces for consistent rendering
        tab_width = self.tabStopDistance()
        char_width = fm.horizontalAdvance(" ")
        spaces_per_tab = int(tab_width / char_width) if char_width > 0 else 4
        indent_visual = indent.replace("\t", " " * spaces_per_tab)
        
        left_margin = fm.horizontalAdvance(indent_visual + marker)
        text_indent = -left_margin
        fmt = block.blockFormat()
        if fmt.leftMargin() == left_margin and fmt.textIndent() == text_indent:
            return
        fmt.setLeftMargin(left_margin)
        fmt.setTextIndent(text_indent)
        self._hanging_indent_guard = True
        try:
            cursor = QTextCursor(block)
            cursor.setBlockFormat(fmt)
        finally:
            self._hanging_indent_guard = False

    def _clear_hanging_indent(self, block) -> None:
        if not block or not block.isValid():
            return
        # Prevent recursion - setBlockFormat can trigger events on Windows
        # that may call back into formatting code
        if self._display_guard or self._hanging_indent_guard:
            return
        fmt = block.blockFormat()
        if fmt.leftMargin() == 0 and fmt.textIndent() == 0:
            return
        fmt.setLeftMargin(0)
        fmt.setTextIndent(0)
        self._hanging_indent_guard = True
        try:
            cursor = QTextCursor(block)
            cursor.setBlockFormat(fmt)
        finally:
            self._hanging_indent_guard = False

    def _finalize_heading_block(self, block) -> None:
        """Render a plain '# ' heading line into display form once editing is done."""
        if self._display_guard or not block or not block.isValid():
            return
        if self._is_code_block_line(block):
            self._pending_heading_block_num = None
            self._pending_heading_level = None
            return
        text = block.text()
        if not text:
            return
        stripped = text.lstrip()
        # Skip if already rendered (sentinel present)
        if stripped and heading_level_from_char(stripped[0]):
            self._pending_heading_block_num = None
            self._pending_heading_level = None
            return
        converted = HEADING_MARK_PATTERN.sub(self._encode_heading, text)
        if converted == text:
            if (
                self._pending_heading_block_num is None
                or self._pending_heading_level is None
                or block.blockNumber() != self._pending_heading_block_num
            ):
                return
            if not stripped:
                self._pending_heading_block_num = None
                self._pending_heading_level = None
                return
            if stripped.startswith("```"):
                self._pending_heading_block_num = None
                self._pending_heading_level = None
                return
            indent = text[: len(text) - len(stripped)]
            converted = f"{indent}{heading_sentinel(self._pending_heading_level)}{stripped}"

        current = self.textCursor()
        current_pos = current.position()
        block_start = block.position()
        delta = len(converted) - len(text)

        self._display_guard = True
        try:
            line_cursor = QTextCursor(block)
            line_cursor.select(QTextCursor.LineUnderCursor)
            line_cursor.insertText(converted)
        finally:
            self._display_guard = False
            self._pending_heading_block_num = None
            self._pending_heading_level = None

        if delta and current_pos > block_start + len(text):
            new_cursor = self.textCursor()
            new_cursor.setPosition(max(block_start, current_pos + delta))
            self.setTextCursor(new_cursor)
        self._schedule_heading_outline()
    
    # --- Bullet list handling ---
    
    def _is_bullet_line(self, text: str) -> tuple[bool, str, str]:
        """Check if line is a bullet and return (is_bullet, indent, content_after_bullet).
        
        Returns:
            (True, indent_str, content) if bullet line
            (False, "", "") otherwise
        """
        stripped = text.lstrip()
        indent = text[:len(text) - len(stripped)]
        
        # Check for bullet patterns: "• " or "* " only (exclude -/+ to avoid false bullets)
        if stripped.startswith(("• ", "* ")):
            content = stripped[2:]
            return (True, indent, content)
        
        return (False, "", "")

    def _is_dash_line(self, text: str) -> tuple[bool, str, str]:
        """Check if line is a dash list entry: leading '-' plus a space."""
        stripped = text.lstrip()
        indent = text[:len(text) - len(stripped)]
        if stripped.startswith("- ") and not stripped.startswith("--") and not stripped.startswith("- ["):
            return True, indent, stripped[2:]
        return False, "", ""

    def _list_line_info(self, text: str) -> tuple[str, str, str]:
        """Return (kind, indent, remainder) where kind is bullet/dash/task/other."""
        is_task, indent, _, _ = self._is_task_line(text)
        if is_task:
            return "task", indent, text[len(indent):]
        is_bullet, indent, _ = self._is_bullet_line(text)
        if is_bullet:
            return "bullet", indent, text[len(indent):]
        is_dash, indent, _ = self._is_dash_line(text)
        if is_dash:
            return "dash", indent, text[len(indent):]
        return "other", "", text

    def _list_children_blocks(self, block, current_indent_len: int):
        """Collect immediately following list blocks that are more indented than current.

        Non-list lines with greater indent are skipped but allow traversal to continue,
        so grandchildren separated by blank/indented lines still move with the parent.
        """
        children = []
        next_block = block.next()
        while next_block.isValid():
            text = next_block.text()
            leading_ws = len(text) - len(text.lstrip(" \t"))
            kind, next_indent, remainder = self._list_line_info(text)
            if kind == "other":
                if leading_ws > current_indent_len:
                    next_block = next_block.next()
                    continue
                break
            if len(next_indent) > current_indent_len:
                children.append((next_block, kind, next_indent, remainder))
                next_block = next_block.next()
                continue
            break
        return children

    def _rewrite_block_indent(self, block, new_indent: str, remainder: Optional[str] = None) -> str:
        """Replace block text with new_indent + remainder (defaults to existing remainder)."""
        text = block.text()
        if remainder is None:
            remainder = text[len(text) - len(text.lstrip(" \t")):]
        new_line = new_indent + remainder
        line_cursor = QTextCursor(block)
        line_cursor.select(QTextCursor.LineUnderCursor)
        line_cursor.insertText(new_line)
        self._refresh_hanging_indent(block, new_line)
        return new_line

    def _refresh_hanging_indent(self, block, line: str) -> None:
        """Reapply hanging indent based on the updated line text."""
        is_bullet, bullet_indent, _ = self._is_bullet_line(line)
        if is_bullet:
            self._apply_hanging_indent(block, bullet_indent, "• ")
            return
        is_dash, dash_indent, _ = self._is_dash_line(line)
        if is_dash:
            self._apply_hanging_indent(block, dash_indent, "- ")
            return
        is_task, task_indent, task_state, _ = self._is_task_line(line)
        if is_task:
            stripped = line.lstrip()
            if stripped.startswith(("☐", "☑")):
                marker = "☑ " if stripped.startswith("☑") else "☐ "
            else:
                m = re.match(r"^(\s*)([-*])\s*\[(?P<state>[ xX])\]\s*", line)
                if m:
                    marker = f"{m.group(2)} [{'x' if m.group('state').lower() == 'x' else ' '}] "
                else:
                    marker = "☑ " if task_state.lower() == "x" else "☐ "
            self._apply_hanging_indent(block, task_indent, marker)
            return
        self._clear_hanging_indent(block)
    
    def _handle_bullet_enter(self) -> bool:
        """Handle Enter key in bullet mode. Returns True if handled."""
        cursor = self.textCursor()
        block = cursor.block()
        text = block.text()
        
        is_bullet, indent, content = self._is_bullet_line(text)
        if not is_bullet:
            return False
        
        # If bullet line is empty (just the bullet), exit bullet mode
        if not content.strip():
            cursor.beginEditBlock()
            cursor.select(QTextCursor.LineUnderCursor)
            cursor.removeSelectedText()
            if indent:
                new_indent, removed = self._dedent_line_text(indent, self.LIST_INDENT_UNIT)
                if removed == 0:
                    new_indent = ""
                cursor.insertText(new_indent + "• ")
                cursor.setPosition(cursor.block().position() + len(new_indent) + 2)
            else:
                cursor.insertText("")
                cursor.setPosition(cursor.block().position())
            cursor.endEditBlock()
            self.setTextCursor(cursor)
            return True
        
        # Insert new line with bullet (use • for visual consistency)
        cursor.beginEditBlock()
        cursor.insertText("\n" + indent + "• ")
        cursor.endEditBlock()
        return True
    
    def _handle_dash_enter(self) -> bool:
        """Handle Enter key in dash-list mode. Returns True if handled."""
        cursor = self.textCursor()
        block = cursor.block()
        text = block.text()

        is_dash, indent, content = self._is_dash_line(text)
        if not is_dash:
            return False

        if not content.strip():
            cursor.beginEditBlock()
            cursor.select(QTextCursor.LineUnderCursor)
            cursor.removeSelectedText()
            if indent:
                new_indent, removed = self._dedent_line_text(indent, self.LIST_INDENT_UNIT)
                if removed == 0:
                    new_indent = ""
                cursor.insertText(new_indent + "- ")
                cursor.setPosition(cursor.block().position() + len(new_indent) + 2)
            else:
                cursor.insertText("")
                cursor.setPosition(cursor.block().position())
            cursor.endEditBlock()
            self.setTextCursor(cursor)
            return True

        cursor.beginEditBlock()
        cursor.insertText("\n" + indent + "- ")
        cursor.endEditBlock()
        return True

    def _is_task_line(self, text: str) -> tuple[bool, str, str, str]:
        """Return whether the line is a task and its components (indent, state, content)."""
        m = re.match(r"^(\s*)([☐☑])\s*(.*)$", text)
        if m:
            indent = m.group(1) or ""
            state = "x" if m.group(2) == "☑" else " "
            content = m.group(3) or ""
            return True, indent, state, content
        m = re.match(r"^(\s*)([-*])\s*\[(?P<state>[ xX])\]\s*(.*)$", text)
        if m:
            indent = m.group(1) or ""
            state = m.group("state") or " "
            content = m.group(4) or ""
            return True, indent, state, content
        return False, "", "", ""

    def _handle_task_enter(self, indent: str, content: str) -> bool:
        cursor = self.textCursor()
        cursor.beginEditBlock()
        if not content.strip():
            cursor.select(QTextCursor.LineUnderCursor)
            cursor.removeSelectedText()
            if indent:
                new_indent, removed = self._dedent_line_text(indent, self.LIST_INDENT_UNIT)
                if removed == 0:
                    new_indent = ""
                marker = "☐ "
                cursor.insertText(new_indent + marker)
                cursor.setPosition(cursor.block().position() + len(new_indent) + len(marker))
            else:
                cursor.insertText("")
                cursor.setPosition(cursor.block().position())
            cursor.endEditBlock()
            self.setTextCursor(cursor)
            return True
        cursor.movePosition(QTextCursor.EndOfBlock)
        cursor.insertBlock()
        marker = "☐ "
        cursor.insertText(indent + marker)
        cursor.endEditBlock()
        self.setTextCursor(cursor)
        return True

    def _maybe_expand_checkbox(self) -> bool:
        return False

    def _handle_task_indent(self, indent: str) -> bool:
        cursor = self.textCursor()
        block = cursor.block()
        text = block.text()

        # Collect child list items with greater indent
        current_indent_len = len(indent)
        children_blocks = self._list_children_blocks(block, current_indent_len)

        rel_from_end = len(text) - (cursor.position() - block.position())

        cursor.beginEditBlock()
        new_indent = indent + self.LIST_INDENT_UNIT
        remainder = text[len(indent):]
        new_line = self._rewrite_block_indent(block, new_indent, remainder)
        # Indent children (any list type)
        for child_block, _, child_indent, child_remainder in children_blocks:
            new_child_indent = child_indent + self.LIST_INDENT_UNIT
            self._rewrite_block_indent(child_block, new_child_indent, child_remainder)

        marker_len = 3 if remainder.startswith(("☐  ", "☑  ")) else 2
        new_pos = block.position() + len(new_line) - rel_from_end
        cursor.setPosition(max(block.position() + len(new_indent) + marker_len, new_pos))
        self.setTextCursor(cursor)
        cursor.endEditBlock()
        return True

    def _handle_task_dedent(self, indent: str) -> bool:
        if len(indent) < len(self.LIST_INDENT_UNIT):
            return False
        cursor = self.textCursor()
        block = cursor.block()
        text = block.text()

        current_indent_len = len(indent)
        indent_unit_len = len(self.LIST_INDENT_UNIT)
        children_blocks = self._list_children_blocks(block, current_indent_len)

        rel_from_end = len(text) - (cursor.position() - block.position())

        cursor.beginEditBlock()
        dedent = indent[:-indent_unit_len]
        remainder = text[len(indent):]
        new_line = self._rewrite_block_indent(block, dedent, remainder)

        for child_block, _, child_indent, child_remainder in children_blocks:
            if len(child_indent) >= indent_unit_len:
                new_child_indent = child_indent[:-indent_unit_len]
                self._rewrite_block_indent(child_block, new_child_indent, child_remainder)

        marker_len = 3 if remainder.startswith(("☐  ", "☑  ")) else 2
        new_pos = block.position() + len(new_line) - rel_from_end
        cursor.setPosition(max(block.position() + len(dedent) + marker_len, new_pos))
        self.setTextCursor(cursor)
        cursor.endEditBlock()
        return True
    
    def _handle_bullet_indent(self) -> bool:
        """Handle Tab key for bullet indentation. Returns True if handled.
        
        If the bullet has child bullets (more indented bullets following it),
        they will be indented along with the parent.
        """
        cursor = self.textCursor()
        block = cursor.block()
        text = block.text()
        
        is_bullet, indent, content = self._is_bullet_line(text)
        if not is_bullet:
            return False
        
        # Get current indent level
        current_indent_len = len(indent)
        
        # Find all child list items (bullets/dashes/tasks) that are more indented
        children_blocks = self._list_children_blocks(block, current_indent_len)
        
        # Save cursor position relative to end of line
        rel_from_end = len(text) - (cursor.position() - block.position())
        
        # Begin edit block to make all changes atomic
        cursor.beginEditBlock()
        
        # Indent the current line
        new_indent = indent + self.LIST_INDENT_UNIT
        remainder = text[len(indent):]
        new_line = self._rewrite_block_indent(block, new_indent, remainder)
        
        # Indent all child list items
        for child_block, _, child_indent, child_remainder in children_blocks:
            new_child_indent = child_indent + self.LIST_INDENT_UNIT
            self._rewrite_block_indent(child_block, new_child_indent, child_remainder)
        
        # Restore cursor position (adjusted for new indent)
        new_pos = block.position() + len(new_line) - rel_from_end
        cursor.setPosition(max(block.position() + len(new_indent) + 2, new_pos))
        self.setTextCursor(cursor)
        cursor.endEditBlock()
        return True
    
    def _handle_bullet_dedent(self) -> bool:
        """Handle Shift+Tab key for bullet dedentation. Returns True if handled.
        
        If the bullet has child bullets (more indented bullets following it),
        they will be dedented along with the parent.
        """
        cursor = self.textCursor()
        block = cursor.block()
        text = block.text()
        
        is_bullet, indent, content = self._is_bullet_line(text)
        if not is_bullet:
            return False
        
        # Can't dedent if already at zero indent
        if len(indent) == 0:
            return True  # Still consume the event
        
        # Get current indent level
        current_indent_len = len(indent)
        
        # Find all child list items (bullets/dashes/tasks) that are more indented
        children_blocks = self._list_children_blocks(block, current_indent_len)
        
        # Remove up to one indent unit from indent
        indent_unit_len = len(self.LIST_INDENT_UNIT)
        if len(indent) >= indent_unit_len:
            new_indent = indent[:-indent_unit_len]
        else:
            new_indent = ""
        
        # Save cursor position relative to end of line
        rel_from_end = len(text) - (cursor.position() - block.position())
        
        # Begin edit block to make all changes atomic
        cursor.beginEditBlock()
        
        # Dedent the current line
        remainder = text[len(indent):]
        new_line = self._rewrite_block_indent(block, new_indent, remainder)
        
        # Dedent all child list items (if they have enough indent)
        for child_block, _, child_indent, child_remainder in children_blocks:
            if len(child_indent) >= indent_unit_len:
                new_child_indent = child_indent[:-indent_unit_len]
                self._rewrite_block_indent(child_block, new_child_indent, child_remainder)
        
        # Restore cursor position (adjusted for new indent)
        new_pos = block.position() + len(new_line) - rel_from_end
        cursor.setPosition(max(block.position() + len(new_indent) + 2, new_pos))
        self.setTextCursor(cursor)
        cursor.endEditBlock()
        return True

    def _handle_dash_indent(self) -> bool:
        """Handle Tab key for dash lists, carrying child dashes along."""
        cursor = self.textCursor()
        block = cursor.block()
        text = block.text()

        is_dash, indent, content = self._is_dash_line(text)
        if not is_dash:
            return False

        current_indent_len = len(indent)
        children_blocks = self._list_children_blocks(block, current_indent_len)

        rel_from_end = len(text) - (cursor.position() - block.position())

        cursor.beginEditBlock()
        new_indent = indent + self.LIST_INDENT_UNIT
        remainder = text[len(indent):]
        new_line = self._rewrite_block_indent(block, new_indent, remainder)

        for child_block, _, child_indent, child_remainder in children_blocks:
            new_child_indent = child_indent + self.LIST_INDENT_UNIT
            self._rewrite_block_indent(child_block, new_child_indent, child_remainder)

        new_pos = block.position() + len(new_line) - rel_from_end
        cursor.setPosition(max(block.position() + len(new_indent) + 2, new_pos))
        self.setTextCursor(cursor)
        cursor.endEditBlock()
        return True

    def _handle_dash_dedent(self) -> bool:
        """Handle Shift+Tab for dash lists, carrying child dashes along."""
        cursor = self.textCursor()
        block = cursor.block()
        text = block.text()

        is_dash, indent, content = self._is_dash_line(text)
        if not is_dash:
            return False
        indent_unit_len = len(self.LIST_INDENT_UNIT)
        if len(indent) == 0:
            return True

        current_indent_len = len(indent)
        children_blocks = self._list_children_blocks(block, current_indent_len)

        rel_from_end = len(text) - (cursor.position() - block.position())

        cursor.beginEditBlock()
        new_indent = indent[:-indent_unit_len] if len(indent) >= indent_unit_len else ""
        remainder = text[len(indent):]
        new_line = self._rewrite_block_indent(block, new_indent, remainder)

        for child_block, _, child_indent, child_remainder in children_blocks:
            if len(child_indent) >= indent_unit_len:
                new_child_indent = child_indent[:-indent_unit_len]
                self._rewrite_block_indent(child_block, new_child_indent, child_remainder)

        new_pos = block.position() + len(new_line) - rel_from_end
        cursor.setPosition(max(block.position() + len(new_indent) + 2, new_pos))
        self.setTextCursor(cursor)
        cursor.endEditBlock()
        return True

    # --- Generic indentation helpers (non-bullet) ---

    def _handle_enter_indent_same_level(self) -> bool:
        """On Enter, continue the next line at the same leading indentation as the current line.

        Applies only when not in a bullet context. Returns True if handled.
        """
        cursor = self.textCursor()
        block = cursor.block()
        if not block.isValid():
            return False
        text = block.text()
        # Determine leading whitespace (tabs and/or spaces)
        stripped = text.lstrip(" \t")
        indent = text[: len(text) - len(stripped)]
        # Simply insert a newline plus the indent
        cursor.beginEditBlock()
        cursor.insertText("\n" + indent)
        cursor.endEditBlock()
        return True

    def _dedent_line_text(self, text: str, indent_unit: Optional[str] = None) -> tuple[str, int]:
        """Return (new_text, removed_chars) after removing one indent unit from the start."""
        indent_unit = indent_unit or self._indent_unit or "    "
        if not text:
            return text, 0
        if text.startswith("\t"):
            return text[1:], 1
        if indent_unit and text.startswith(indent_unit):
            return text[len(indent_unit) :], len(indent_unit)
        removed = 0
        max_remove = len(indent_unit) if indent_unit else 4
        while removed < max_remove and removed < len(text) and text[removed] == " ":
            removed += 1
        if removed:
            return text[removed:], removed
        return text, 0

    def _apply_indent_to_selection(self, dedent: bool) -> bool:
        """Indent or dedent all blocks touched by the current selection."""
        cursor = self.textCursor()
        if not cursor.hasSelection():
            return False
        start = cursor.selectionStart()
        end = cursor.selectionEnd()
        doc = self.document()
        start_block = doc.findBlock(start)
        end_block = doc.findBlock(max(start, end - 1))
        if not start_block.isValid() or not end_block.isValid():
            return False
        blocks: list = []
        block = start_block
        while block.isValid():
            blocks.append(block)
            if block == end_block:
                break
            block = block.next()
        if not blocks:
            return False
        indent_unit = self._indent_unit or "    "
        cursor.beginEditBlock()
        start_delta = 0
        end_delta = 0
        modified = False
        for idx, blk in enumerate(blocks):
            line_cursor = QTextCursor(self.document())
            line_start = blk.position()
            text = blk.text()
            if dedent:
                new_line, removed = self._dedent_line_text(text, indent_unit)
                if removed == 0:
                    continue
                line_cursor.setPosition(line_start)
                line_cursor.setPosition(line_start + removed, QTextCursor.KeepAnchor)
                line_cursor.removeSelectedText()
                modified = True
                end_delta -= removed
                if idx == 0:
                    start_delta -= removed
            else:
                line_cursor.setPosition(line_start)
                line_cursor.insertText(indent_unit)
                modified = True
                end_delta += len(indent_unit)
                if idx == 0:
                    start_delta += len(indent_unit)
        cursor.endEditBlock()
        if not modified:
            return True
        new_start = max(0, start + start_delta)
        new_end = max(new_start, end + end_delta)
        new_cursor = self.textCursor()
        new_cursor.setPosition(new_start)
        new_cursor.setPosition(new_end, QTextCursor.KeepAnchor)
        self.setTextCursor(new_cursor)
        return True

    def _handle_generic_dedent(self) -> bool:
        """Handle Shift+Tab on non-bullet lines by removing one leading indent unit.

        Dedent strategy:
        - If line starts with a tab (\t), remove one tab.
        - Else if starts with two spaces, remove two spaces.
        - Else if starts with one space, remove that single space.
        Always consumes the event even if nothing to dedent to prevent focus shifts.
        Returns True when the event should be consumed.
        """
        cursor = self.textCursor()
        block = cursor.block()
        if not block.isValid():
            return True  # consume to avoid focus change

        text = block.text()
        # If it's a bullet, let bullet handler manage it
        is_bullet, _, _ = self._is_bullet_line(text)
        if is_bullet:
            return False

        new_line, removed = self._dedent_line_text(text)
        if removed == 0:
            return True

        # Preserve caret relative position
        rel = cursor.position() - block.position()
        cursor.beginEditBlock()
        line_cursor = QTextCursor(block)
        line_cursor.select(QTextCursor.LineUnderCursor)
        line_cursor.insertText(new_line)
        # Restore cursor position moved left by 'removed', not going before line start
        new_block = self.document().findBlock(block.position())
        new_pos = max(new_block.position(), block.position() + rel - removed)
        c = self.textCursor()
        c.setPosition(new_pos)
        self.setTextCursor(c)
        cursor.endEditBlock()
        return True

    # --- Scrolling helpers ---

    def _apply_scroll_past_end_margin(self) -> None:
        """Add bottom margin to the document so the view can scroll past the last line."""
        if (
            self._cursor_events_blocked
            or self._display_guard
            or self._suppress_vi_cursor
            or type(self)._LOAD_GUARD_DEPTH > 0
            or self._in_mode_window_transition()
        ):
            return
        try:
            root = self.document().rootFrame()
            fmt = root.frameFormat()
            # Use a fraction of the viewport height for a comfortable cushion
            margin = max(0, int(self.viewport().height() * 0.4))
            if fmt.bottomMargin() != margin:
                fmt.setBottomMargin(margin)
                root.setFrameFormat(fmt)
        except Exception:
            # Be defensive—failure to set margin shouldn't break editing
            pass

    def _scroll_one_line_down(self) -> None:
        """Scroll the viewport down by roughly one line height."""
        sb = self.verticalScrollBar()
        step = max(1, int(self.fontMetrics().lineSpacing()))
        sb.setValue(sb.value() + step)

    def _doc_to_markdown(self) -> str:
        """Convert document to markdown.

        Uses QTextDocument blocks/fragments so inline images can be round-tripped
        back into markdown. Trailing newlines are normalized to avoid file bloat
        from accidental or repeated saves.
        """
        parts: list[str] = []
        doc = self.document()
        text = doc.toPlainText()
        end = len(text)
        cursor = QTextCursor(doc)
        pos = 0
        while pos < end:
            ch = text[pos]
            if ch == "\ufffc":
                cursor.setPosition(pos)
                fmt = cursor.charFormat()
                if fmt.isImageFormat():
                    img_md = self._markdown_from_image_format(fmt.toImageFormat())
                    # If the markdown tag is still in the document right after the image
                    # object, skip the duplicate text to keep saves stable.
                    if text.startswith(img_md, pos + 1):
                        parts.append(img_md)
                        pos += 1 + len(img_md)
                        continue
                    parts.append(img_md)
                pos += 1
                continue
            parts.append(ch)
            pos += 1

        result = "".join(parts).replace("\u2029", "\n")

        # Limit trailing newlines to keep saves stable and prevent runaway growth.
        max_trailing = 3
        stripped = result.rstrip("\n")
        trailing = len(result) - len(stripped)
        if trailing > max_trailing:
            result = stripped + ("\n" * max_trailing)

        # Ensure a single trailing newline for non-empty documents.
        if result and not result.endswith("\n"):
            result += "\n"

        return result

    def _markdown_from_image_format(self, img_fmt: QTextImageFormat) -> str:
        """Return markdown representation for an inline image fragment.

        Reconstructs the original (possibly relative) path, preserving an
        optional stored width attribute as `{width=NNN}` so round-trips from
        markdown → display → markdown are stable.
        """
        alt = img_fmt.property(IMAGE_PROP_ALT) or ""
        original = img_fmt.property(IMAGE_PROP_ORIGINAL) or img_fmt.name()
        if not isinstance(original, str):  # defensive
            original = str(original)
        original = self._normalize_image_path(original)
        width_prop = int(img_fmt.property(IMAGE_PROP_WIDTH) or 0)
        suffix = f"{{width={width_prop}}}" if width_prop else ""
        return f"![{alt}]({original}){suffix}"

    def _render_images(self, display_text: str, scheduled_at: Optional[float] = None) -> None:
        """Replace markdown image patterns in the given display text with inline images.

        This operates on the current document by selecting each pattern range
        and inserting a QTextImageFormat created from the resolved path.
        """
        import time
        delay_ms = (time.perf_counter() - scheduled_at) * 1000.0 if scheduled_at else 0.0
        matches: list[tuple[int, int, str, str, Optional[str], str, str]] = []
        qt_pattern = QRegularExpression(r"!\[[^\]]*\]\([^\)\s]+\)(?:\{width=\d+\})?")
        doc = self.document()
        cursor = QTextCursor(doc)
        cursor.movePosition(QTextCursor.Start)
        while True:
            cursor = doc.find(qt_pattern, cursor)
            if cursor.isNull():
                break
            selected = cursor.selectedText().replace("\u2029", "\n")
            match = IMAGE_PATTERN.search(selected)
            if match:
                utf16_sel = _utf16_positions(selected)
                start_pos = cursor.selectionStart() + utf16_sel[match.start()]
                end_pos = cursor.selectionStart() + utf16_sel[match.end()]
                matches.append(
                    (
                        start_pos,
                        end_pos,
                        match.group("path"),
                        match.group("alt") or "",
                        match.group("width"),
                        selected,
                        match.group(0),
                    )
                )
            cursor.setPosition(cursor.selectionEnd())
        if sys.platform == "win32" and os.getenv("SP_WIN_IMAGE_DEBUG", "0") not in ("0", "false", "False", ""):
            sample = matches[:5]
            print(f"[StillPoint][WIN_IMAGE_DEBUG] matches={len(matches)} sample_count={len(sample)}")
            for idx, (start_pos, end_pos, path, alt, width, selected, match_text) in enumerate(sample, start=1):
                snippet = selected.replace("\u2029", "\\n")
                if len(snippet) > 120:
                    snippet = snippet[:120] + "..."
                print(
                    f"[StillPoint][WIN_IMAGE_DEBUG] #{idx} range=({start_pos},{end_pos}) "
                    f"path={path} alt_len={len(alt)} width={width or ''} snippet={snippet!r} match={match_text!r}"
                )
        if not matches:
            self._mark_page_load(f"render images skipped (0) delay={delay_ms:.1f}ms")
            QTimer.singleShot(
                0,
                lambda: self._complete_page_load_logging(
                    f"qt idle after images delay={(time.perf_counter() - (scheduled_at or time.perf_counter()))*1000:.1f}ms"
                ),
            )
            return
        if _DETAILED_LOGGING:
            print(f"[TIMING] Rendering {len(matches)} images...")
        self._mark_page_load(f"render images start count={len(matches)} delay={delay_ms:.1f}ms")
        cursor = self.textCursor()
        cursor.beginEditBlock()
        try:
            for idx, (start_pos, end_pos, path, alt, width, selected, match_text) in enumerate(reversed(matches)):
                t_img_start = time.perf_counter()
                cursor.setPosition(start_pos)
                cursor.setPosition(end_pos, QTextCursor.KeepAnchor)
                if os.getenv("SP_IMAGE_RENDER_DEBUG", "0") not in ("0", "false", "False", ""):
                    picked = cursor.selectedText().replace("\u2029", "\n")
                    print(
                        f"[StillPoint][IMAGE_RENDER_DEBUG] idx={idx+1}/{len(matches)} "
                        f"range=({start_pos},{end_pos}) picked={picked!r} match={match_text!r}"
                    )
                picked = cursor.selectedText().replace("\u2029", "\n")
                if picked != match_text:
                    if os.getenv("SP_IMAGE_RENDER_DEBUG", "0") not in ("0", "false", "False", ""):
                        print(
                            f"[StillPoint][IMAGE_RENDER_DEBUG] skip idx={idx+1} "
                            f"range=({start_pos},{end_pos}) picked_mismatch"
                        )
                    continue

                fmt = self._create_image_format(
                    path,
                    alt,
                    width,
                )
                t_img_end = time.perf_counter()

                if fmt is None:
                    # If the image can't be resolved, leave the markdown text as-is
                    print(f"  Image {idx+1}/{len(matches)} ({path}): FAILED")
                    continue

                cursor.removeSelectedText()
                cursor.insertImage(fmt)
                print(f"  Image {idx+1}/{len(matches)} ({path}): {(t_img_end - t_img_start)*1000:.1f}ms")
        finally:
            cursor.endEditBlock()
        self._mark_page_load(f"render images done count={len(matches)}")
        end_at = time.perf_counter()
        QTimer.singleShot(
            0,
            lambda: self._complete_page_load_logging(
                f"qt idle after images delay={(time.perf_counter() - end_at)*1000:.1f}ms"
            ),
        )

    def _insert_image_from_path(self, raw_path: str, alt: str = "", width: Optional[int] = None) -> None:
        fmt = self._create_image_format(raw_path, alt, str(width) if width else None)
        if fmt is None:
            self.insertPlainText(f"![{alt}]({raw_path})")
            return
        cursor = self.textCursor()
        cursor.insertImage(fmt)

    def _create_image_format(self, raw_path: str, alt: str, width: Optional[str]) -> Optional[QTextImageFormat]:
        resolved = self._resolve_image_path(raw_path)
        if resolved is None or not resolved.exists():
            return None
        image = QImage(str(resolved))
        if image.isNull():
            return None
        fmt = QTextImageFormat()
        fmt.setName(str(resolved))
        fmt.setProperty(IMAGE_PROP_ALT, alt)
        fmt.setProperty(IMAGE_PROP_ORIGINAL, raw_path)
        fmt.setProperty(IMAGE_PROP_NATURAL_WIDTH, image.width())
        fmt.setProperty(IMAGE_PROP_NATURAL_HEIGHT, image.height())
        width_val = int(width) if width else 0
        if width_val:
            fmt.setWidth(width_val)
            if image.width():
                ratio = image.height() / image.width()
                fmt.setHeight(width_val * ratio)
            fmt.setProperty(IMAGE_PROP_WIDTH, width_val)
        else:
            fmt.setProperty(IMAGE_PROP_WIDTH, 0)
        return fmt

    def _resolve_image_path(self, raw_path: str) -> Optional[Path]:
        if not raw_path:
            return None
        raw_path = raw_path.strip()
        raw_path = raw_path.replace("\\", "/")
        while "/./" in raw_path:
            raw_path = raw_path.replace("/./", "/")
        if raw_path.startswith("http://") or raw_path.startswith("https://"):
            return None
        if self._remote_mode:
            return self._resolve_remote_image_path(raw_path)
        base_dir: Optional[Path] = None
        if self._vault_root and self._current_path:
            base_dir = (self._vault_root / self._current_path.lstrip("/")).parent
        elif self._vault_root:
            base_dir = self._vault_root
        path_obj = Path(raw_path)
        if path_obj.is_absolute():
            return path_obj.resolve()
        if raw_path.startswith("/"):
            if self._vault_root:
                return (self._vault_root / raw_path.lstrip("/")).resolve()
            return Path(raw_path).resolve()
        if raw_path.startswith("./"):
            raw_path = raw_path[2:]
        if base_dir:
            return (base_dir / raw_path).resolve()
        return Path(raw_path).resolve()

    def _resolve_remote_image_path(self, raw_path: str) -> Optional[Path]:
        if not self._current_path or not self._api_base or not self._remote_cache_root:
            return None
        virtual_path = self._virtual_image_path(raw_path)
        if not virtual_path:
            return None
        cache_path = (self._remote_cache_root / "attachments" / virtual_path.lstrip("/")).resolve()
        if cache_path.exists():
            return cache_path
        if not self._http_client:
            return None
        try:
            resp = self._http_client.get("/api/file/raw", params={"path": virtual_path})
            if resp.status_code == 401 and self._auth_prompt:
                if self._auth_prompt():
                    resp = self._http_client.get("/api/file/raw", params={"path": virtual_path})
            resp.raise_for_status()
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_bytes(resp.content)
            return cache_path
        except httpx.HTTPError:
            return None

    def _virtual_image_path(self, raw_path: str) -> Optional[str]:
        raw_path = raw_path.strip()
        raw_path = raw_path.replace("\\", "/")
        while "/./" in raw_path:
            raw_path = raw_path.replace("/./", "/")
        if not raw_path or raw_path.startswith("http://") or raw_path.startswith("https://"):
            return None
        if raw_path.startswith("/"):
            return raw_path
        base_dir = Path(self._current_path).parent if self._current_path else Path("/")
        rel_path = raw_path
        if rel_path.startswith("./"):
            rel_path = rel_path[2:]
        return f"/{(base_dir / rel_path).as_posix()}"

    def _normalize_image_path(self, path: str) -> str:
        path = (path or "").strip()
        path = path.replace("\\", "/")
        while "/./" in path:
            path = path.replace("/./", "/")
        if not path or path.startswith(("http://", "https://", "/", "./", "../")):
            return path
        return f"./{path}"

    def _normalize_markdown_images(self, markdown: str) -> str:
        if not markdown:
            return markdown

        def repl(match: re.Match[str]) -> str:
            alt = match.group("alt") or ""
            path = self._normalize_image_path(match.group("path") or "")
            width = match.group("width") or ""
            suffix = f"{{width={width}}}" if width else ""
            return f"![{alt}]({path}){suffix}"

        normalized = IMAGE_PATTERN.sub(repl, markdown)
        return normalized

    def _image_at_position(self, pos: Optional[QPoint]) -> Optional[tuple[QTextCursor, QTextImageFormat]]:
        cursor = self.cursorForPosition(pos) if pos is not None else self.textCursor()
        fmt = cursor.charFormat()
        if fmt.isImageFormat():
            return cursor, fmt.toImageFormat()
        if cursor.position() > 0:
            probe = QTextCursor(cursor)
            probe.movePosition(QTextCursor.Left, QTextCursor.MoveAnchor, 1)
            fmt = probe.charFormat()
            if fmt.isImageFormat():
                return probe, fmt.toImageFormat()
        return None

    def _find_image_by_name(self, image_name: str) -> Optional[tuple[QTextCursor, QTextImageFormat]]:
        """Find an image in the document by its filename."""
        block = self.document().begin()
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                fragment = it.fragment()
                if fragment.isValid():
                    fmt = fragment.charFormat()
                    if fmt.isImageFormat():
                        img_fmt = fmt.toImageFormat()
                        if img_fmt.name() == image_name:
                            # Create cursor at this fragment
                            cursor = QTextCursor(self.document())
                            cursor.setPosition(fragment.position())
                            return cursor, img_fmt
                it += 1
            block = block.next()
        return None

    def _resize_image_by_name(self, image_name: str, width: Optional[int]) -> None:
        """Find and resize an image by its filename."""
        result = self._find_image_by_name(image_name)
        if not result:
            return
        
        cursor, img_fmt = result
        
        # Compute new size properties
        natural_w = float(img_fmt.property(IMAGE_PROP_NATURAL_WIDTH) or 0)
        natural_h = float(img_fmt.property(IMAGE_PROP_NATURAL_HEIGHT) or 0)
        if width:
            img_fmt.setProperty(IMAGE_PROP_WIDTH, int(width))
            img_fmt.setWidth(int(width))
            if natural_w:
                ratio = natural_h / natural_w if natural_w else 0
                img_fmt.setHeight(int(width) * ratio if ratio else int(width))
        else:
            # Reset to natural size - clear the width property and use natural dimensions
            img_fmt.setProperty(IMAGE_PROP_WIDTH, 0)
            # For display, use the natural dimensions
            if natural_w and natural_h:
                img_fmt.setWidth(int(natural_w))
                img_fmt.setHeight(int(natural_h))
            else:
                img_fmt.setWidth(0)
                img_fmt.setHeight(0)
        
        # Replace the image at cursor position
        img_pos = cursor.position()
        cursor.beginEditBlock()
        cursor.setPosition(img_pos)
        cursor.setPosition(img_pos + 1, QTextCursor.KeepAnchor)
        cursor.insertImage(img_fmt)
        cursor.endEditBlock()

    def _prompt_image_width_by_name(self, image_name: str) -> None:
        """Prompt for custom width for an image identified by name."""
        result = self._find_image_by_name(image_name)
        if not result:
            return
        _, fmt = result
        current = int(fmt.property(IMAGE_PROP_WIDTH) or 0)
        if not current:
            current = int(fmt.property(IMAGE_PROP_NATURAL_WIDTH) or 300)
        width, ok = QInputDialog.getInt(self, "Image Width", "Width (px):", current, 50, 4096)
        if not ok:
            return
        self._resize_image_by_name(image_name, width)

    def set_ai_actions_enabled(self, enabled: bool) -> None:
        """Enable/disable AI actions menu entries."""
        self._ai_actions_enabled = bool(enabled)

    def set_ai_chat_available(self, available: bool, *, active: Optional[bool] = None) -> None:
        """Toggle whether a chat already exists for the current page."""
        self._ai_chat_available = bool(available)
        if active is not None:
            self._ai_chat_active = bool(active)

    def _prompt_custom_translation(self, text: str) -> None:
        lang, ok = QInputDialog.getText(self, "Custom Language", "Translate to which language?")
        if not ok or not lang.strip():
            return
        self.aiActionRequested.emit("Custom Translation", f"Translate to {lang.strip()}.", text)

    def _prompt_compare_note(self, text: str) -> None:
        other, ok = QInputDialog.getText(self, "Compare Against", "Paste or type the other note to compare against:")
        if not ok or not other.strip():
            return
        combined = f"Compare this note to the following note:\n\nOTHER NOTE:\n{other.strip()}\n\nORIGINAL NOTE:\n{text}"
        self.aiActionRequested.emit("Compare Against Another Note", "Compare against the provided note.", combined)

    def _looks_like_code(self, text: str) -> bool:
        """Heuristic to decide if text is code-like."""
        if not text:
            return False
        indicators = ("def ", "class ", "import ", "{", "}", ";", "=>", "#include", "function ")
        if any(tok in text for tok in indicators):
            return True
        # many short lines with symbols
        lines = text.splitlines()
        symbol_lines = sum(1 for ln in lines if any(ch in ln for ch in "{}();<>"))
        return symbol_lines >= max(3, len(lines) // 4)
