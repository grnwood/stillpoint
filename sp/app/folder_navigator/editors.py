"""Text editors used by the stand-alone folder navigator.

The generic source editor is deliberately self contained. Importing an editor
from a larger tool window makes opening a file depend on all of that window's
optional UI dependencies being importable.
"""

from __future__ import annotations

from pathlib import Path

from pygments import lex
from pygments.lexers import TextLexer, get_lexer_for_filename
from pygments.styles import get_style_by_name
from pygments.util import ClassNotFound
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import (QColor, QFont, QKeyEvent, QSyntaxHighlighter,
                           QTextCharFormat, QTextCursor, QTextFormat)
from PySide6.QtWidgets import QApplication, QPlainTextEdit, QTextEdit

from sp.app import config
from sp.app.ui.markdown_editor import MarkdownEditor
from sp.app.ui.keyboard_shortcuts import is_vi_navigation_chord
from sp.app.ui.theme import theme_color


class PygmentsHighlighter(QSyntaxHighlighter):
    """Small Pygments-to-Qt adapter that highlights one text block at a time."""

    def __init__(self, document, filename: str = "") -> None:
        super().__init__(document)
        try:
            self.lexer = get_lexer_for_filename(filename) if filename else TextLexer()
        except ClassNotFound:
            self.lexer = TextLexer()
        try:
            self.style = get_style_by_name(config.load_pygments_style())
        except (ClassNotFound, ValueError):
            self.style = get_style_by_name("default")

    def highlightBlock(self, text: str) -> None:  # noqa: N802 - Qt API
        offset = 0
        for token, value in lex(text, self.lexer):
            length = len(value)
            if length:
                style = self.style.style_for_token(token)
                char_format = QTextCharFormat()
                if style.get("color"):
                    char_format.setForeground(QColor("#" + style["color"]))
                if style.get("bgcolor"):
                    char_format.setBackground(QColor("#" + style["bgcolor"]))
                if style.get("bold"):
                    char_format.setFontWeight(QFont.Weight.Bold)
                if style.get("italic"):
                    char_format.setFontItalic(True)
                if style.get("underline"):
                    char_format.setFontUnderline(True)
                self.setFormat(offset, length, char_format)
            offset += length


class SourceEditor(QPlainTextEdit):
    """A lightweight syntax-highlighted editor with optional Vim navigation."""

    viInsertModeChanged = Signal(bool)
    viNavigationEscapePressed = Signal()
    findRequested = Signal()
    _VI_BLOCK_EXTRA_KEY = int(QTextFormat.UserProperty) + 4200
    _VI_LINE_EXTRA_KEY = int(QTextFormat.UserProperty) + 4201

    def __init__(self, filename: str | Path = "", parent=None) -> None:
        super().__init__(parent)
        font = QFont("monospace")
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.setFont(font)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._vi_feature_enabled = False
        self._vi_insert_mode = True
        self._vi_cursor_style = "block"
        self._pending_g = False
        self._default_cursor_width = max(1, self.cursorWidth())
        self._block_cursor_width = max(
            2, self.fontMetrics().horizontalAdvance("M")
        )
        self.syntax_highlighter = PygmentsHighlighter(self.document(), str(filename))
        # Keep the shorter name for callers that used the initial implementation.
        self.highlighter = self.syntax_highlighter
        self.cursorPositionChanged.connect(self._update_vi_cursor_highlight)
        self.textChanged.connect(self._update_vi_cursor_highlight)

    def set_vi_mode_enabled(self, enabled: bool) -> None:
        self._vi_feature_enabled = bool(enabled)
        self._set_vi_insert_mode(not self._vi_feature_enabled)

    def set_vi_cursor_style(self, style: str) -> None:
        normalized = str(style or "").strip().lower()
        self._vi_cursor_style = normalized if normalized in {"block", "line"} else "line"
        self._update_cursor_width()
        self._update_vi_cursor_highlight()

    def _set_vi_insert_mode(self, enabled: bool) -> None:
        self._vi_insert_mode = bool(enabled)
        self._pending_g = False
        self._update_cursor_width()
        self._update_vi_cursor_highlight()
        self.viInsertModeChanged.emit(self._vi_insert_mode)

    def _update_cursor_width(self) -> None:
        if not self._vi_feature_enabled or self._vi_insert_mode:
            self.setCursorWidth(self._default_cursor_width)
            return
        width = 2 if self._vi_cursor_style == "line" else self._block_cursor_width
        self.setCursorWidth(width)

    def _update_vi_cursor_highlight(self) -> None:
        """Render the configured StillPoint vi navigation cursor treatment."""
        existing = [
            selection for selection in self.extraSelections()
            if selection.format.property(self._VI_BLOCK_EXTRA_KEY) is None
            and selection.format.property(self._VI_LINE_EXTRA_KEY) is None
        ]
        if (not self._vi_feature_enabled or self._vi_insert_mode
                or self.textCursor().hasSelection()):
            self.setExtraSelections(existing)
            return

        cursor = self.textCursor()
        accent, foreground = self._vi_cursor_colors()
        extra = QTextEdit.ExtraSelection()
        if self._vi_cursor_style == "block":
            block_cursor = QTextCursor(cursor)
            if not block_cursor.atEnd():
                block_cursor.movePosition(
                    QTextCursor.MoveOperation.Right,
                    QTextCursor.MoveMode.KeepAnchor,
                )
            extra.cursor = block_cursor
            extra.format.setProperty(QTextFormat.FullWidthSelection, False)
            extra.format.setProperty(self._VI_BLOCK_EXTRA_KEY, True)
        else:
            extra.cursor = cursor
            extra.format.setProperty(QTextFormat.FullWidthSelection, True)
            extra.format.setProperty(self._VI_LINE_EXTRA_KEY, True)
        extra.format.setBackground(accent)
        extra.format.setForeground(foreground)
        self.setExtraSelections(existing + [extra])

    @staticmethod
    def _vi_cursor_colors() -> tuple[QColor, QColor]:
        accent_value = (
            config.load_vault_accent_color()
            or theme_color("vi_navigation_line.bg", "#2a3950").name()
        )
        accent = QColor(accent_value)
        if not accent.isValid():
            accent = QColor("#2a3950")
        luminance = (
            0.299 * accent.red()
            + 0.587 * accent.green()
            + 0.114 * accent.blue()
        )
        foreground = QColor("#111111" if luminance >= 160 else "#ffffff")
        return accent, foreground

    def _move(self, operation: QTextCursor.MoveOperation, *, select: bool = False) -> None:
        cursor = self.textCursor()
        cursor.movePosition(
            operation,
            QTextCursor.MoveMode.KeepAnchor if select else QTextCursor.MoveMode.MoveAnchor,
        )
        self.setTextCursor(cursor)

    def _vi_copy(self) -> None:
        cursor = QTextCursor(self.textCursor())
        if not cursor.hasSelection():
            cursor.select(QTextCursor.SelectionType.LineUnderCursor)
        QApplication.clipboard().setText(cursor.selectedText().replace("\u2029", "\n"))

    def _vi_cut(self) -> None:
        if self.isReadOnly():
            return
        cursor = self.textCursor()
        if not cursor.hasSelection():
            cursor.movePosition(
                QTextCursor.MoveOperation.NextCharacter,
                QTextCursor.MoveMode.KeepAnchor,
            )
            self.setTextCursor(cursor)
        self.cut()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt API
        if not self._vi_feature_enabled:
            super().keyPressEvent(event)
            return

        key = event.key()
        modifiers = event.modifiers()
        if key in (Qt.Key.Key_PageUp, Qt.Key.Key_PageDown):
            super().keyPressEvent(event)
            return
        if (self._vi_feature_enabled and is_vi_navigation_chord(modifiers)
                and key in (Qt.Key.Key_J, Qt.Key.Key_K)):
            page_key = Qt.Key.Key_PageDown if key == Qt.Key.Key_J else Qt.Key.Key_PageUp
            page_event = QKeyEvent(event.type(), page_key, Qt.KeyboardModifier.NoModifier)
            super().keyPressEvent(page_event)
            return
        if self._vi_insert_mode:
            if key == Qt.Key.Key_Escape:
                self._set_vi_insert_mode(False)
                return
            super().keyPressEvent(event)
            return

        # Keep application shortcuts such as Ctrl+S and Ctrl+Shift+P working.
        if modifiers & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier):
            super().keyPressEvent(event)
            return

        shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        if shift and key in (Qt.Key.Key_N, Qt.Key.Key_U):
            self._move(
                QTextCursor.MoveOperation.Down if key == Qt.Key.Key_N
                else QTextCursor.MoveOperation.Up,
                select=True,
            )
            self._pending_g = False
            return
        moves = {
            Qt.Key.Key_H: QTextCursor.MoveOperation.Left,
            Qt.Key.Key_Left: QTextCursor.MoveOperation.Left,
            Qt.Key.Key_L: QTextCursor.MoveOperation.Right,
            Qt.Key.Key_Right: QTextCursor.MoveOperation.Right,
            Qt.Key.Key_J: QTextCursor.MoveOperation.Down,
            Qt.Key.Key_Down: QTextCursor.MoveOperation.Down,
            Qt.Key.Key_K: QTextCursor.MoveOperation.Up,
            Qt.Key.Key_Up: QTextCursor.MoveOperation.Up,
            Qt.Key.Key_0: QTextCursor.MoveOperation.StartOfLine,
            Qt.Key.Key_Dollar: QTextCursor.MoveOperation.EndOfLine,
            Qt.Key.Key_W: QTextCursor.MoveOperation.NextWord,
            Qt.Key.Key_B: QTextCursor.MoveOperation.PreviousWord,
        }
        if key in moves:
            self._move(moves[key], select=shift)
            self._pending_g = False
            return
        if key == Qt.Key.Key_G:
            if self._pending_g:
                self._move(QTextCursor.MoveOperation.Start)
                self._pending_g = False
            else:
                self._pending_g = True
            return
        self._pending_g = False
        if (key == Qt.Key.Key_Escape
                and modifiers & ~Qt.KeyboardModifier.KeypadModifier
                == Qt.KeyboardModifier.NoModifier):
            self.viNavigationEscapePressed.emit()
        elif key == Qt.Key.Key_Slash:
            self.findRequested.emit()
        elif key == Qt.Key.Key_I:
            self._set_vi_insert_mode(True)
        elif key == Qt.Key.Key_A:
            self._move(QTextCursor.MoveOperation.Right)
            self._set_vi_insert_mode(True)
        elif key == Qt.Key.Key_O:
            cursor = self.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.EndOfLine)
            cursor.insertBlock()
            self.setTextCursor(cursor)
            self._set_vi_insert_mode(True)
        elif key == Qt.Key.Key_C and not shift:
            self._vi_copy()
        elif key == Qt.Key.Key_X and not shift:
            self._vi_cut()
        elif key == Qt.Key.Key_P and not shift and not self.isReadOnly():
            self.paste()


def configure_markdown_editor(editor: MarkdownEditor, path: Path) -> None:
    """Apply settings shared with StillPoint's primary Markdown editor."""
    editor.set_context(str(path), str(path.parent))
    editor.set_pygments_style(config.load_pygments_style())
    editor.set_vi_cursor_style(config.load_vi_cursor_style())
    editor.set_vi_mode_enabled(config.load_vi_mode_enabled())


def configure_source_editor(editor: SourceEditor) -> None:
    """Apply global editor preferences to a generic source editor."""
    editor.set_vi_cursor_style(config.load_vi_cursor_style())
    editor.set_vi_mode_enabled(config.load_vi_mode_enabled())
