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
from PySide6.QtGui import QColor, QFont, QKeyEvent, QSyntaxHighlighter, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QPlainTextEdit

from sp.app import config
from sp.app.ui.markdown_editor import MarkdownEditor
from sp.app.ui.keyboard_shortcuts import is_vi_navigation_chord


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
        self.syntax_highlighter = PygmentsHighlighter(self.document(), str(filename))
        # Keep the shorter name for callers that used the initial implementation.
        self.highlighter = self.syntax_highlighter

    def set_vi_mode_enabled(self, enabled: bool) -> None:
        self._vi_feature_enabled = bool(enabled)
        self._set_vi_insert_mode(not self._vi_feature_enabled)

    def set_vi_cursor_style(self, style: str) -> None:
        self._vi_cursor_style = style
        self._update_cursor_width()

    def _set_vi_insert_mode(self, enabled: bool) -> None:
        self._vi_insert_mode = bool(enabled)
        self._pending_g = False
        self._update_cursor_width()
        self.viInsertModeChanged.emit(self._vi_insert_mode)

    def _update_cursor_width(self) -> None:
        if not self._vi_feature_enabled or self._vi_insert_mode:
            self.setCursorWidth(1)
            return
        self.setCursorWidth(2 if self._vi_cursor_style in {"line", "bar"} else 8)

    def _move(self, operation: QTextCursor.MoveOperation) -> None:
        cursor = self.textCursor()
        cursor.movePosition(operation)
        self.setTextCursor(cursor)

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
            self._move(moves[key])
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
        elif key == Qt.Key.Key_X and not self.isReadOnly():
            cursor = self.textCursor()
            cursor.deleteChar()
            self.setTextCursor(cursor)


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
