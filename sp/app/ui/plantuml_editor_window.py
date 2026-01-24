"""Standalone PlantUML editor window with split view and rendering."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal, QSize, QMimeData, QRect, QUrl, QByteArray, QBuffer, QIODevice
from PySide6.QtGui import QKeySequence, QShortcut, QPixmap, QImage, QTextCursor, QFont, QDesktopServices
from PySide6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPlainTextEdit,
    QLabel,
    QScrollArea,
    QSplitter,
    QToolButton,
    QMenu,
    QApplication,
    QMessageBox,
    QFileDialog,
    QComboBox,
    QLineEdit,
    QPlainTextDocumentLayout,
    QDialog,
    QPushButton,
    QTextEdit,
)
from PySide6.QtGui import QPainter, QColor, QTextFormat

from sp.app.plantuml_renderer import PlantUMLRenderer, RenderResult
from .ai_chat_panel import ApiWorker, ServerManager
from sp.app import config

_LOGGING = os.getenv("ZIMX_PLANTUML_DEBUG", "0") not in ("0", "false", "False", "", None)


class LineNumberArea(QWidget):
    """Line number area for editor."""
    
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self):
        return QSize(self.editor.lineNumberAreaWidth(), 0)

    def paintEvent(self, event):
        painter = QPainter(self)
        # Slightly lighter than editor background (editor is ~30, line numbers ~40)
        painter.fillRect(event.rect(), QColor(40, 40, 40))
        
        block = self.editor.firstVisibleBlock()
        blockNumber = block.blockNumber()
        top = self.editor.blockBoundingGeometry(block).translated(self.editor.contentOffset()).top()
        bottom = top + self.editor.blockBoundingRect(block).height()
        
        # Line number text color: light gray
        painter.setPen(QColor(128, 128, 128))
        
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                lineNum = blockNumber + 1
                painter.drawText(5, int(top), self.editor.lineNumberAreaWidth() - 10, 
                                int(self.editor.blockBoundingRect(block).height()),
                                Qt.AlignRight, str(lineNum))
            block = block.next()
            top = bottom
            bottom = top + self.editor.blockBoundingRect(block).height()
            blockNumber += 1


class PlainTextEditWithLineNumbers(QPlainTextEdit):
    """PlainTextEdit with integrated line number area."""
    
    def __init__(self):
        super().__init__()
        self.line_number_area = LineNumberArea(self)
        self.blockCountChanged.connect(self._update_line_number_area_width)
        self.updateRequest.connect(self._update_line_numbers)
        self._update_line_number_area_width(0)

    def lineNumberAreaWidth(self):
        # Always allocate space for 3 digits (999 lines max before wrapping display)
        digits = 3
        space = 3 + self.fontMetrics().horizontalAdvance('9') * digits
        return space

    def _update_line_number_area_width(self, _):
        self.setViewportMargins(self.lineNumberAreaWidth(), 0, 0, 0)

    def _update_line_numbers(self, rect, dy):
        if dy:
            self.line_number_area.scroll(0, dy)
        else:
            self.line_number_area.update(0, rect.y(), self.line_number_area.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_line_number_area_width(0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cr = self.contentsRect()
        self.line_number_area.setGeometry(QRect(cr.left(), cr.top(), self.lineNumberAreaWidth(), cr.height()))


class ViPlainTextEdit(PlainTextEditWithLineNumbers):
    """PlainTextEdit with a lightweight vi-style navigation mode."""

    viInsertModeChanged = Signal(bool)

    def __init__(self):
        super().__init__()
        self._vi_feature_enabled: bool = False
        self._vi_mode_active: bool = False
        self._vi_insert_mode: bool = False
        self._vi_block_cursor_enabled: bool = False
        self._pending_d: bool = False
        self._pending_y: bool = False
        self._pending_g: bool = False
        self._vi_clipboard: str = ""
        width = max(1, self.cursorWidth())
        self._default_cursor_width: int = width
        self._auto_indent_enabled: bool = True

    def set_vi_mode_enabled(self, enabled: bool) -> None:
        self._vi_feature_enabled = bool(enabled)
        self._pending_d = False
        self._pending_y = False
        self._pending_g = False
        if self._vi_feature_enabled:
            self._enter_vi_navigation_mode(force_emit=True)
        else:
            self._vi_insert_mode = False
            self._vi_mode_active = False
            self._apply_cursor_style()
            self.viInsertModeChanged.emit(False)

    def set_vi_block_cursor_enabled(self, enabled: bool) -> None:
        self._vi_block_cursor_enabled = bool(enabled)
        self._apply_cursor_style()

    def _apply_cursor_style(self) -> None:
        if self._vi_feature_enabled and self._vi_mode_active and self._vi_block_cursor_enabled:
            try:
                block_width = max(2, self.fontMetrics().horizontalAdvance("M"))
            except Exception:
                block_width = 8
            self.setCursorWidth(block_width)
        else:
            self.setCursorWidth(self._default_cursor_width)

    def _enter_vi_navigation_mode(self, force_emit: bool = False) -> None:
        if not self._vi_feature_enabled:
            return
        emit_needed = force_emit or self._vi_insert_mode
        self._vi_mode_active = True
        self._vi_insert_mode = False
        self._pending_d = False
        self._pending_y = False
        self._pending_g = False
        self._apply_cursor_style()
        if emit_needed:
            self.viInsertModeChanged.emit(False)

    def _enter_vi_insert_mode(self) -> None:
        if not self._vi_feature_enabled:
            return
        self._vi_mode_active = False
        self._vi_insert_mode = True
        self._pending_d = False
        self._pending_y = False
        self._pending_g = False
        self._apply_cursor_style()
        self.viInsertModeChanged.emit(True)

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        key = event.key()
        mods = event.modifiers()

        # Handle Enter key for auto-indentation (works in both vi and normal mode)
        if key in (Qt.Key_Return, Qt.Key_Enter) and self._auto_indent_enabled:
            cursor = self.textCursor()
            block = cursor.block()
            # Extract leading whitespace from current line
            line_text = block.text()
            indent = line_text[:len(line_text) - len(line_text.lstrip(" \t"))]
            # Insert newline + indent
            cursor.beginEditBlock()
            cursor.insertText("\n" + indent)
            cursor.endEditBlock()
            self.setTextCursor(cursor)
            event.accept()
            return

        if not self._vi_feature_enabled:
            return super().keyPressEvent(event)

        # Ctrl+Shift+J/K -> Page Down / Page Up
        if mods == (Qt.ControlModifier | Qt.ShiftModifier) and key in (Qt.Key_J, Qt.Key_K):
            bar = self.verticalScrollBar()
            step = max(1, bar.pageStep())
            bar.setValue(bar.value() + (step if key == Qt.Key_J else -step))
            event.accept()
            return

        # Insert mode: exit on Esc, otherwise behave normally
        if self._vi_insert_mode:
            if key == Qt.Key_Escape and mods in (Qt.NoModifier, Qt.ShiftModifier):
                event.accept()
                self._enter_vi_navigation_mode()
                return
            self._pending_d = False
            self._pending_y = False
            self._pending_g = False
            return super().keyPressEvent(event)

        # Navigation mode
        if mods & Qt.ControlModifier:
            if mods == Qt.ControlModifier and key == Qt.Key_R:
                self.redo()
                event.accept()
                return
            # Pass through other Ctrl shortcuts (copy/paste, etc.)
            self._pending_d = False
            self._pending_y = False
            self._pending_g = False
            return super().keyPressEvent(event)

        # g + g -> start of document
        if key == Qt.Key_G and self._pending_g:
            self._pending_g = False
            self._move_cursor(QTextCursor.Start)
            event.accept()
            return
        if key == Qt.Key_G and (mods & Qt.ShiftModifier):
            self._pending_g = False
            self._move_cursor(QTextCursor.End)
            event.accept()
            return
        if key == Qt.Key_G:
            self._pending_g = True
            event.accept()
            return
        self._pending_g = False

        # dd / yy combos, or delete selection if one exists
        if key == Qt.Key_D:
            cursor = self.textCursor()
            if cursor.hasSelection():
                event.accept()
                self._pending_d = False
                self._pending_y = False
                self._pending_g = False
                self._cut_selection()
                return
            if self._pending_d:
                self._pending_d = False
                self._delete_current_line()
            else:
                self._pending_d = True
            event.accept()
            return
        if key == Qt.Key_Y:
            if self._pending_y:
                self._pending_y = False
                self._yank_current_line()
            else:
                self._pending_y = True
            event.accept()
            return
        self._pending_d = False
        self._pending_y = False

        if key == Qt.Key_Escape:
            event.accept()
            return
        if key == Qt.Key_I:
            event.accept()
            self._enter_vi_insert_mode()
            return
        if key == Qt.Key_A:
            event.accept()
            cursor = self.textCursor()
            if not cursor.atEnd():
                cursor.movePosition(QTextCursor.Right)
                self.setTextCursor(cursor)
            self._enter_vi_insert_mode()
            return
        if key == Qt.Key_O and (mods & Qt.ShiftModifier):
            # Shift+O comes through as Key_O with Shift modifier
            event.accept()
            self._open_line_above()
            return
        if key == Qt.Key_O:
            event.accept()
            self._open_line_below()
            return
        if key == Qt.Key_H:
            event.accept()
            self._move_cursor(QTextCursor.Left)
            return
        if key == Qt.Key_L:
            event.accept()
            self._move_cursor(QTextCursor.Right)
            return
        if key == Qt.Key_J:
            event.accept()
            self._move_cursor(QTextCursor.Down)
            return
        if key == Qt.Key_K:
            event.accept()
            self._move_cursor(QTextCursor.Up)
            return
        if key == Qt.Key_0:
            event.accept()
            self._move_cursor(QTextCursor.StartOfLine)
            return
        if key == Qt.Key_Q and not (mods & Qt.ShiftModifier):
            event.accept()
            self._move_cursor(QTextCursor.StartOfLine)
            return
        if key == Qt.Key_Dollar:
            event.accept()
            self._move_cursor(QTextCursor.EndOfLine)
            return
        if key == Qt.Key_Semicolon:
            event.accept()
            self._move_cursor(QTextCursor.EndOfLine)
            return
        if (mods & Qt.ShiftModifier) and key == Qt.Key_N:
            event.accept()
            # Select downward one line; if at last line, select to document end
            cursor = self.textCursor()
            block = cursor.block()
            if not block.isValid() or not block.next().isValid() or cursor.atEnd():
                cursor.movePosition(QTextCursor.End, QTextCursor.KeepAnchor)
                self.setTextCursor(cursor)
            else:
                cursor.movePosition(QTextCursor.Down, QTextCursor.KeepAnchor)
                self.setTextCursor(cursor)
            return
        if (mods & Qt.ShiftModifier) and key == Qt.Key_U:
            event.accept()
            # Select upward one line; if at first line, select to start
            cursor = self.textCursor()
            block = cursor.block()
            if not block.isValid() or not block.previous().isValid() or cursor.atStart():
                cursor.movePosition(QTextCursor.Start, QTextCursor.KeepAnchor)
                self.setTextCursor(cursor)
            else:
                cursor.movePosition(QTextCursor.Up, QTextCursor.KeepAnchor)
                self.setTextCursor(cursor)
            return
        if key == Qt.Key_W:
            event.accept()
            self._move_cursor(QTextCursor.WordRight)
            return
        if key == Qt.Key_B:
            event.accept()
            self._move_cursor(QTextCursor.WordLeft)
            return
        if key == Qt.Key_X:
            cursor = self.textCursor()
            event.accept()
            if cursor.hasSelection():
                self._cut_selection()
            else:
                self._delete_char()
            return
        if key == Qt.Key_P:
            event.accept()
            self._paste_after()
            return
        if key == Qt.Key_U:
            event.accept()
            self.undo()
            return

        # Allow default navigation keys (arrows, page up/down, home/end)
        if key in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down, Qt.Key_PageUp, Qt.Key_PageDown, Qt.Key_Home, Qt.Key_End):
            return super().keyPressEvent(event)

        # Ignore other printable keys while in navigation mode
        event.accept()

    def _move_cursor(self, move: QTextCursor.MoveOperation) -> None:
        cursor = self.textCursor()
        cursor.movePosition(move)
        self.setTextCursor(cursor)

    def _delete_char(self) -> None:
        cursor = self.textCursor()
        if cursor.atEnd():
            return
        cursor.beginEditBlock()
        cursor.deleteChar()
        cursor.endEditBlock()
        self.setTextCursor(cursor)

    def _cut_selection(self) -> None:
        cursor = self.textCursor()
        if not cursor.hasSelection():
            return
        start = cursor.selectionStart()
        end = cursor.selectionEnd()
        try:
            full_text = self.document().toPlainText()
            selected = full_text[start:end]
        except Exception:
            selected = cursor.selectedText()
            # Qt inserts U+2029 for paragraph separators; normalize to newlines
            selected = selected.replace("\u2029", "\n")
        self._vi_clipboard = selected if selected.endswith("\n") else selected + "\n"
        cursor.beginEditBlock()
        cursor.removeSelectedText()
        cursor.endEditBlock()
        self.setTextCursor(cursor)

    def _delete_current_line(self) -> None:
        cursor = self.textCursor()
        block = cursor.block()
        line_text = block.text()
        self._vi_clipboard = f"{line_text}\n"
        start_pos = block.position()
        cursor.beginEditBlock()
        cursor.setPosition(start_pos)
        cursor.movePosition(QTextCursor.EndOfBlock, QTextCursor.KeepAnchor)
        cursor.removeSelectedText()
        if block.next().isValid():
            cursor.setPosition(start_pos)
            cursor.deleteChar()
        cursor.endEditBlock()
        self.setTextCursor(cursor)

    def _yank_current_line(self) -> None:
        cursor = self.textCursor()
        block = cursor.block()
        self._vi_clipboard = f"{block.text()}\n"

    def _paste_after(self) -> None:
        if not self._vi_clipboard:
            return
        cursor = self.textCursor()
        cursor.beginEditBlock()
        cursor.movePosition(QTextCursor.EndOfBlock)
        cursor.insertBlock()
        cursor.insertText(self._vi_clipboard.rstrip("\n"))
        cursor.endEditBlock()
        self.setTextCursor(cursor)

    def _open_line_below(self) -> None:
        cursor = self.textCursor()
        block = cursor.block()
        indent = block.text()[: len(block.text()) - len(block.text().lstrip(" \t"))]
        cursor.beginEditBlock()
        cursor.movePosition(QTextCursor.EndOfBlock)
        cursor.insertBlock()
        cursor.insertText(indent)
        cursor.endEditBlock()
        self.setTextCursor(cursor)
        self._enter_vi_insert_mode()

    def _open_line_above(self) -> None:
        cursor = self.textCursor()
        block = cursor.block()
        indent = block.text()[: len(block.text()) - len(block.text().lstrip(" \t"))]
        cursor.beginEditBlock()
        cursor.movePosition(QTextCursor.StartOfBlock)
        cursor.insertBlock()
        cursor.movePosition(QTextCursor.Up)
        cursor.movePosition(QTextCursor.StartOfLine)
        cursor.insertText(indent)
        cursor.endEditBlock()
        self.setTextCursor(cursor)
        self._enter_vi_insert_mode()


class ChatLineEdit(QLineEdit):
    """Line edit with history navigation and Ctrl+Enter send."""

    sendRequested = Signal()

    def __init__(self, history_ref: list[str]):
        super().__init__()
        self._history = history_ref
        self._history_index: int | None = None

    def keyPressEvent(self, event) -> None:
        key = event.key()
        mods = event.modifiers()

        # Ctrl+Shift+Enter/Return triggers send (original behavior)
        if key in (Qt.Key_Return, Qt.Key_Enter):
            if (mods & Qt.ControlModifier) and (mods & Qt.ShiftModifier):
                event.accept()
                self.sendRequested.emit()
                return

        # History navigation
        if key == Qt.Key_Up:
            if self._history:
                if self._history_index is None:
                    self._history_index = len(self._history) - 1
                else:
                    self._history_index = max(0, self._history_index - 1)
                self._apply_history()
            event.accept()
            return

        if key == Qt.Key_Down:
            if self._history:
                if self._history_index is None:
                    # nothing selected
                    pass
                elif self._history_index < len(self._history) - 1:
                    self._history_index += 1
                    self._apply_history()
                else:
                    # Move past last to clear
                    self._history_index = None
                    self.clear()
            event.accept()
            return

        super().keyPressEvent(event)

    def _apply_history(self) -> None:
        if self._history_index is None:
            return
        try:
            self.setText(self._history[self._history_index])
            # Move cursor to end for convenience
            self.setCursorPosition(len(self.text()))
        except Exception:
            pass


class ZoomablePreviewLabel(QLabel):
    """Custom label that handles Ctrl+MouseWheel for zooming and left-click drag for panning."""
    
    zoomRequested = Signal(int)  # delta (positive = zoom in, negative = zoom out)
    
    def __init__(self):
        super().__init__()
        self.pan_start_pos = None
        self.is_panning = False
    
    def wheelEvent(self, event) -> None:
        """Handle mouse wheel - zoom on Ctrl modifier."""
        if event.modifiers() & Qt.ControlModifier:
            delta = event.angleDelta().y()
            if delta > 0:
                self.zoomRequested.emit(1)  # Zoom in
            elif delta < 0:
                self.zoomRequested.emit(-1)  # Zoom out
            event.accept()
        else:
            super().wheelEvent(event)
    
    def mousePressEvent(self, event) -> None:
        """Start pan operation on left mouse button."""
        pixmap = self.pixmap()
        if event.button() == Qt.LeftButton and pixmap:
            # Allow panning if image is larger than viewport in either dimension
            if pixmap.width() > self.width() or pixmap.height() > self.height():
                self.is_panning = True
                self.pan_start_pos = event.globalPos()
                self.setCursor(Qt.ClosedHandCursor)
                event.accept()
                return
        super().mousePressEvent(event)
    
    def mouseMoveEvent(self, event) -> None:
        """Handle panning when dragging."""
        if self.is_panning and self.pan_start_pos:
            delta = event.globalPos() - self.pan_start_pos
            # Find the scroll area parent and adjust scroll bars
            parent = self.parent()
            while parent:
                if isinstance(parent, QScrollArea):
                    h_bar = parent.horizontalScrollBar()
                    v_bar = parent.verticalScrollBar()
                    h_bar.setValue(h_bar.value() - delta.x())
                    v_bar.setValue(v_bar.value() - delta.y())
                    self.pan_start_pos = event.globalPos()
                    event.accept()
                    return
                parent = parent.parent()
            event.accept()
        else:
            super().mouseMoveEvent(event)
    
    def mouseReleaseEvent(self, event) -> None:
        """End pan operation."""
        if event.button() == Qt.LeftButton and self.is_panning:
            self.is_panning = False
            self.pan_start_pos = None
            self.setCursor(Qt.ArrowCursor)
            event.accept()
        else:
            super().mouseReleaseEvent(event)


def _generate_error_svg(error_msg: str, line_num: int = 0) -> str:
    """Generate a PlantUML-style error diagram SVG."""
    # Escape HTML entities in error message
    error_display = error_msg.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    
    line_info = f" (line {line_num})" if line_num > 0 else ""
    
    svg = f"""<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" contentScriptType="application/ecmascript" contentStyleType="text/css" height="400px" preserveAspectRatio="none" style="width:800px;height:400px;background:#ffeeee" version="1.1" viewBox="0 0 800 400" width="800px" zoomAndPan="magnify">
    <defs>
        <style type="text/css"><![CDATA[
            * {{ margin: 0; padding: 0; }}
            .error-title {{ font-size: 24px; font-weight: bold; fill: #cc0000; font-family: monospace; }}
            .error-message {{ font-size: 14px; fill: #333333; font-family: monospace; word-wrap: break-word; }}
            .error-line {{ font-size: 12px; fill: #666666; font-family: monospace; }}
            .error-box {{ fill: #ffe6e6; stroke: #ff9999; stroke-width: 2; }}
        ]]></style>
    </defs>
    <rect class="error-box" x="20" y="20" width="760" height="360" rx="5" ry="5"/>
    <text class="error-title" x="40" y="60">⚠ PlantUML Render Error{line_info}</text>
    <foreignObject x="40" y="90" width="720" height="280">
        <div xmlns="http://www.w3.org/1999/xhtml" style="font-family: monospace; font-size: 13px; color: #333; white-space: pre-wrap; word-break: break-word; line-height: 1.4;">
            {error_display}
        </div>
    </foreignObject>
</svg>"""
    return svg


class PlantUMLEditorWindow(QMainWindow):
    """Non-modal editor window for PlantUML diagrams with split editor/preview."""

    def __init__(self, file_path: str, parent=None, on_save=None) -> None:
        super().__init__(parent)
        
        # Set window icon explicitly (especially important on Windows)
        from sp.app.main import get_app_icon
        self.setWindowIcon(get_app_icon())
        
        self.file_path = Path(file_path)
        self._on_save = on_save
        self.renderer = PlantUMLRenderer()
        self._ai_prompt_history: list[str] = []
        self._vi_enabled: bool = config.load_vi_mode_enabled()
        self._vi_insert_active: bool = False
        # Check if AI chat is enabled
        self._ai_chat_enabled: bool = config.load_enable_ai_chats()
        # Load auto-render setting (default: False)
        self._auto_render_enabled: bool = config.load_puml_auto_render(default=False)
        self._editor_dirty: bool = False
        self.setWindowTitle(f"PlantUML Editor - {self.file_path.name}")
        self.setGeometry(100, 100, 1400, 800)
        
        # Create main widget and layout
        main_widget = QWidget()
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        
        # Create toolbar with zoom buttons and export
        toolbar_layout = QHBoxLayout()
        toolbar_layout.setContentsMargins(5, 5, 5, 5)
        
        # Editor section with Save button
        editor_section = QHBoxLayout()
        
        self.save_btn = QToolButton()
        self.save_btn.setText("💾 Save")
        self.save_btn.setToolTip("Save file (Ctrl+S or Ctrl+Enter)")
        self.save_btn.clicked.connect(self._save_file)
        editor_section.addWidget(self.save_btn)
        
        # Editor zoom controls
        editor_zoom_label = QLabel("Zoom:")
        editor_section.addWidget(editor_zoom_label)
        
        self.editor_zoom_out_btn = QToolButton()
        self.editor_zoom_out_btn.setText("−")
        self.editor_zoom_out_btn.setToolTip("Zoom out editor")
        self.editor_zoom_out_btn.clicked.connect(self._zoom_out_editor)
        editor_section.addWidget(self.editor_zoom_out_btn)
        
        self.editor_zoom_in_btn = QToolButton()
        self.editor_zoom_in_btn.setText("+")
        self.editor_zoom_in_btn.setToolTip("Zoom in editor")
        self.editor_zoom_in_btn.clicked.connect(self._zoom_in_editor)
        editor_section.addWidget(self.editor_zoom_in_btn)
        
        editor_section.addSpacing(15)
        
        # Auto-render checkbox
        from PySide6.QtWidgets import QCheckBox
        self.auto_render_checkbox = QCheckBox("Auto render?")
        self.auto_render_checkbox.setToolTip("Automatically render diagram on text changes")
        self.auto_render_checkbox.setChecked(self._auto_render_enabled)
        self.auto_render_checkbox.toggled.connect(self._on_auto_render_toggled)
        editor_section.addWidget(self.auto_render_checkbox)
        
        # Manual render status label
        self.render_status_label = QLabel()
        self.render_status_label.setStyleSheet("color: #ffa500; font-style: italic; margin-left: 10px;")
        editor_section.addWidget(self.render_status_label)
        self._update_render_status_label()
        
        toolbar_layout.addLayout(editor_section)
        toolbar_layout.addStretch()
        
        # Preview section with zoom and export (right-aligned)
        preview_section = QHBoxLayout()
        
        # Render button
        self.render_btn = QToolButton()
        self.render_btn.setText("⟲ Render")
        self.render_btn.setToolTip("Render diagram (Ctrl+S)")
        self.render_btn.clicked.connect(self._render)
        preview_section.addWidget(self.render_btn)
        
        preview_section.addSpacing(10)
        
        # Preview zoom controls
        preview_zoom_label = QLabel("Zoom:")
        preview_section.addWidget(preview_zoom_label)
        
        self.preview_zoom_out_btn = QToolButton()
        self.preview_zoom_out_btn.setText("−")
        self.preview_zoom_out_btn.setToolTip("Zoom out preview")
        self.preview_zoom_out_btn.clicked.connect(self._zoom_out_preview)
        preview_section.addWidget(self.preview_zoom_out_btn)
        
        self.preview_zoom_in_btn = QToolButton()
        self.preview_zoom_in_btn.setText("+")
        self.preview_zoom_in_btn.setToolTip("Zoom in preview")
        self.preview_zoom_in_btn.clicked.connect(self._zoom_in_preview)
        preview_section.addWidget(self.preview_zoom_in_btn)
        
        preview_section.addSpacing(10)
        
        # Export button
        self.export_btn = QToolButton()
        self.export_btn.setText("↓ Export")
        self.export_btn.setToolTip("Export diagram as SVG or PNG")
        self.export_btn.clicked.connect(self._show_export_menu)
        preview_section.addWidget(self.export_btn)
        
        toolbar_layout.addLayout(preview_section)
        
        main_layout.addLayout(toolbar_layout)
        
        # Create center widget with shortcuts dropdown above split view
        center_widget = QWidget()
        center_layout = QVBoxLayout()
        center_layout.setContentsMargins(0, 0, 0, 0)
        
        # Shortcuts and AI server/model selection row
        shortcuts_layout = QHBoxLayout()
        shortcuts_layout.setContentsMargins(5, 5, 5, 5)
        
        shortcuts_label = QLabel("Shortcuts:")
        self.shortcuts_category_combo = QComboBox()
        self.shortcuts_variant_combo = QComboBox()
        self.shortcuts_help_btn = QToolButton()
        self.shortcuts_help_btn.setText("?")
        self.shortcuts_help_btn.setToolTip("Open documentation for selected diagram type")
        self.shortcuts_help_btn.setEnabled(False)
        
        self._shortcuts_data: list[dict] = []
        self._load_shortcuts()
        
        self.shortcuts_category_combo.currentIndexChanged.connect(self._on_shortcuts_category_changed)
        self.shortcuts_variant_combo.currentIndexChanged.connect(self._on_shortcut_variant_selected)
        self.shortcuts_help_btn.clicked.connect(self._on_shortcuts_help_clicked)
        
        shortcuts_layout.addWidget(shortcuts_label)
        shortcuts_layout.addWidget(self.shortcuts_category_combo)
        shortcuts_layout.addWidget(self.shortcuts_variant_combo)
        shortcuts_layout.addWidget(self.shortcuts_help_btn)
        
        # Add separator
        shortcuts_layout.addSpacing(20)
        
        # Server dropdown (only if AI chat enabled)
        server_label = QLabel("Server:")
        self.ai_server_combo = QComboBox()
        self.ai_server_combo.setMaximumWidth(150)
        self.ai_server_combo.currentTextChanged.connect(self._on_ai_server_changed)
        server_label.setVisible(self._ai_chat_enabled)
        self.ai_server_combo.setVisible(self._ai_chat_enabled)
        shortcuts_layout.addWidget(server_label)
        shortcuts_layout.addWidget(self.ai_server_combo)
        
        # Model dropdown (only if AI chat enabled)
        model_label = QLabel("Model:")
        self.ai_model_combo = QComboBox()
        self.ai_model_combo.setMaximumWidth(150)
        model_label.setVisible(self._ai_chat_enabled)
        self.ai_model_combo.setVisible(self._ai_chat_enabled)
        shortcuts_layout.addWidget(model_label)
        shortcuts_layout.addWidget(self.ai_model_combo)
        
        shortcuts_layout.addStretch()
        
        # Load servers and models only if AI chat enabled
        if self._ai_chat_enabled:
            self._load_ai_servers_models()
        
        center_layout.addLayout(shortcuts_layout)
        
        # Main horizontal splitter: Editor (left) | Preview & Chat (right)
        main_h_splitter = QSplitter(Qt.Horizontal)
        
        # LEFT: PlantUML code editor
        self.editor = ViPlainTextEdit()
        self.editor.setPlaceholderText("Enter PlantUML diagram code here...")
        self.editor.setFont(self._get_monospace_font())
        # Style editor to look more like a code editor
        self.editor.setStyleSheet("""
            QPlainTextEdit {
                background-color: #1e1e1e;
                color: #d4d4d4;
                border: 1px solid #3e3e3e;
                padding: 8px;
                selection-background-color: #264f78;
                selection-color: #ffffff;
            }
        """)
        self.editor.setTabStopDistance(self.editor.fontMetrics().horizontalAdvance(' ') * 2)
        main_h_splitter.addWidget(self.editor)
        
        # RIGHT: Vertical splitter with Preview (top) and Chat (bottom)
        right_v_splitter = QSplitter(Qt.Vertical)
        
        # Preview container
        preview_container = QWidget()
        preview_layout = QVBoxLayout()
        preview_layout.setContentsMargins(0, 0, 0, 0)
        
        self.preview_scroll = QScrollArea()
        self.preview_scroll.setWidgetResizable(True)
        self.preview_scroll.setStyleSheet("QScrollArea { background-color: #f0f0f0; }")
        
        self.preview_label = ZoomablePreviewLabel()
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setStyleSheet("QLabel { background-color: white; border: 1px solid #ccc; }")
        self.preview_label.zoomRequested.connect(self._on_preview_wheel_zoom)
        self.preview_label.setContextMenuPolicy(Qt.CustomContextMenu)
        self.preview_label.customContextMenuRequested.connect(self._show_preview_context_menu)
        self.preview_scroll.setWidget(self.preview_label)
        
        preview_layout.addWidget(self.preview_scroll)
        
        # Loading overlay
        self.loading_overlay = self._create_loading_overlay(preview_container)
        self.loading_overlay.hide()
        
        preview_container.setLayout(preview_layout)
        right_v_splitter.addWidget(preview_container)
        
        # AI chat panel (only if enabled)
        if self._ai_chat_enabled:
            self.ai_panel = self._create_ai_chat_panel()
            right_v_splitter.addWidget(self.ai_panel)
            # Set right splitter (preview/chat) sizes: 70% preview, 30% chat
            right_v_splitter.setSizes([490, 210])
        else:
            self.ai_panel = None
            # No chat panel, just use preview at full size
            right_v_splitter.addWidget(preview_container)
            right_v_splitter.setSizes([700])
        right_v_splitter.setCollapsible(0, False)
        right_v_splitter.setCollapsible(1, True)  # Chat can be collapsed
        
        # Add right splitter to main horizontal splitter
        main_h_splitter.addWidget(right_v_splitter)
        
        # Set main splitter sizes: 40% editor, 60% preview+chat
        main_h_splitter.setSizes([400, 600])
        main_h_splitter.setCollapsible(0, False)
        main_h_splitter.setCollapsible(1, False)
        
        self.editor_preview_splitter = main_h_splitter
        main_h_splitter.splitterMoved.connect(self._on_splitter_moved)
        
        center_layout.addWidget(main_h_splitter)
        center_widget.setLayout(center_layout)
        main_layout.addWidget(center_widget)
        main_widget.setLayout(main_layout)
        self.setCentralWidget(main_widget)

        # Debounced geometry/save timer
        self._geom_timer = QTimer(self)
        self._geom_timer.setSingleShot(True)
        self._geom_timer.setInterval(400)
        self._geom_timer.timeout.connect(self._save_geometry_prefs)

        # Wire splitters to save state
        self._vertical_splitter = right_v_splitter
        if self._ai_chat_enabled:
            self._vertical_splitter.splitterMoved.connect(lambda *_: self._geom_timer.start())
        self.editor_preview_splitter.splitterMoved.connect(lambda *_: self._geom_timer.start())

        # Restore geometry and splitter states
        self._restore_geometry_prefs()

        # Status badge for vi insert mode (INS)
        self._badge_base_style = "border: 1px solid #666; padding: 2px 6px; border-radius: 3px;"
        self._vi_badge_base_style = self._badge_base_style
        self._vi_status_label = QLabel("INS")
        self._vi_status_label.setObjectName("viStatusLabel")
        self._vi_status_label.setToolTip("Vi insert mode indicator")
        self.statusBar().addPermanentWidget(self._vi_status_label, 0)
        self.editor.set_vi_block_cursor_enabled(config.load_vi_block_cursor_enabled())
        self.editor.viInsertModeChanged.connect(self._on_vi_insert_state_changed)
        self.editor.set_vi_mode_enabled(self._vi_enabled)
        self._update_vi_badge_visibility()
        
        # Load file content
        self._load_file()
        
        # Setup debounce timer for rendering
        self.render_timer = QTimer()
        self.render_timer.setSingleShot(True)
        self.render_timer.setInterval(1000)  # 1 second debounce
        self.render_timer.timeout.connect(self._render)
        
        # Now connect editor changes (after timer is created)
        self.editor.textChanged.connect(self._on_editor_changed)
        
        # Setup Ctrl+S shortcut for save
        QShortcut(QKeySequence.Save, self, self._save_file)
        
        # Setup Ctrl+Enter for manual render (works when editor has focus)
        ctrl_enter_shortcut = QShortcut(QKeySequence(Qt.CTRL | Qt.Key_Return), self.editor)
        ctrl_enter_shortcut.activated.connect(self._render)
        
        # Setup Ctrl+Shift+Space to toggle focus between editor and AI chat
        QShortcut(QKeySequence(Qt.CTRL | Qt.SHIFT | Qt.Key_Space), self, self._toggle_focus_editor_ai)
        
        # Zoom levels (load persisted)
        try:
            self.editor_zoom_level = int(config.load_puml_editor_zoom(0))
        except Exception:
            self.editor_zoom_level = 0
        try:
            self.preview_zoom_level = int(config.load_puml_preview_zoom(0))
        except Exception:
            self.preview_zoom_level = 0
        self.preview_pixmap: Optional[QPixmap] = None
        # Apply editor zoom immediately
        try:
            base_pt = 11
            font = self.editor.font()
            font.setPointSize(max(6, base_pt + self.editor_zoom_level))
            self.editor.setFont(font)
        except Exception:
            pass
        
        self._render()

    def _on_vi_insert_state_changed(self, insert_active: bool) -> None:
        self._vi_insert_active = bool(insert_active)
        self._update_vi_badge_style(insert_active)

    def _update_vi_badge_visibility(self) -> None:
        if not hasattr(self, "_vi_status_label"):
            return
        if self._vi_enabled:
            self._vi_status_label.show()
            self._update_vi_badge_style(self._vi_insert_active)
        else:
            self._vi_status_label.hide()

    def _update_vi_badge_style(self, insert_active: bool) -> None:
        if not hasattr(self, "_vi_status_label"):
            return
        style = self._vi_badge_base_style
        if self._vi_enabled:
            if insert_active:
                style += " background-color: #ffd54d; color: #000;"
            else:
                style += " background-color: transparent; color: #e0e0e0;"
        else:
            style += " background-color: transparent; color: #e0e0e0;"
        self._vi_status_label.setStyleSheet(style)

    def _on_auto_render_toggled(self, checked: bool) -> None:
        """Handle auto-render checkbox toggle."""
        self._auto_render_enabled = checked
        config.save_puml_auto_render(checked)
        self._update_render_status_label()
        # If auto-render is enabled and editor is dirty, trigger render
        if checked and self._editor_dirty:
            self.render_timer.start()

    def _update_render_status_label(self) -> None:
        """Update the render status label based on auto-render and dirty state."""
        if not hasattr(self, 'render_status_label'):
            return
        if not self._auto_render_enabled and self._editor_dirty:
            self.render_status_label.setText("Ctrl+Enter to render...")
        else:
            self.render_status_label.setText("")

    def _create_loading_overlay(self, parent: QWidget) -> QWidget:
        """Create a loading overlay widget with spinner."""
        overlay = QWidget(parent)
        overlay.setObjectName("loadingOverlay")
        overlay.setStyleSheet("""
            QWidget#loadingOverlay {
                background-color: rgba(0, 0, 0, 220);
            }
            QLabel {
                color: white;
                font-size: 16px;
                font-weight: bold;
            }
        """)
        
        layout = QVBoxLayout(overlay)
        layout.setAlignment(Qt.AlignCenter)
        
        # Spinner label (using Unicode spinner character)
        self.spinner_label = QLabel("⟳")
        self.spinner_label.setStyleSheet("font-size: 80px; color: #4CAF50;")
        self.spinner_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.spinner_label)
        
        # Loading text
        loading_text = QLabel("Rendering diagram...")
        loading_text.setAlignment(Qt.AlignCenter)
        layout.addWidget(loading_text)
        
        # Animation timer for spinner
        self.spinner_timer = QTimer()
        self.spinner_timer.setInterval(100)
        self.spinner_angle = 0
        self.spinner_timer.timeout.connect(self._animate_spinner)
        
        return overlay

    def _animate_spinner(self) -> None:
        """Animate the spinner by rotating through Unicode spinner characters."""
        spinners = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']
        if hasattr(self, 'spinner_label'):
            self.spinner_angle = (self.spinner_angle + 1) % len(spinners)
            self.spinner_label.setText(spinners[self.spinner_angle])

    def _show_loading(self) -> None:
        """Show the loading overlay with animation."""
        if not hasattr(self, 'loading_overlay'):
            return
        try:
            # Position overlay to cover the entire parent container
            parent = self.loading_overlay.parent()
            if parent:
                self.loading_overlay.setGeometry(0, 0, parent.width(), parent.height())
            self.loading_overlay.raise_()
            self.loading_overlay.show()
            self.spinner_timer.start()
        except Exception:
            pass

    def _hide_loading(self) -> None:
        """Hide the loading overlay and restore preview."""
        if not hasattr(self, 'loading_overlay'):
            return
        try:
            self.spinner_timer.stop()
            self.loading_overlay.hide()
        except Exception:
            pass

    # --- Geometry persistence -------------------------------------------------
    def _restore_geometry_prefs(self) -> None:
        try:
            geom64 = config.load_puml_window_geometry()
            if geom64:
                self.restoreGeometry(QByteArray.fromBase64(geom64.encode("utf-8")))
        except Exception:
            pass
        try:
            hstate64 = config.load_puml_hsplit_state()
            if hstate64:
                self.editor_preview_splitter.restoreState(QByteArray.fromBase64(hstate64.encode("utf-8")))
        except Exception:
            pass
        try:
            vstate64 = config.load_puml_vsplit_state()
            if vstate64:
                self._vertical_splitter.restoreState(QByteArray.fromBase64(vstate64.encode("utf-8")))
        except Exception:
            pass

    def _save_geometry_prefs(self) -> None:
        try:
            g = self.saveGeometry().toBase64().data().decode("utf-8")
            config.save_puml_window_geometry(g)
            size = self.geometry().size()
            print(f"[PlantUML] Saved geometry: {size.width()}x{size.height()} (base64 {len(g)} chars)")
        except Exception as exc:
            print(f"[PlantUML] Save geometry failed: {exc}")
        try:
            h = self.editor_preview_splitter.saveState().toBase64().data().decode("utf-8")
            config.save_puml_hsplit_state(h)
            sizes = self.editor_preview_splitter.sizes()
            print(f"[PlantUML] Saved HSplit sizes: {sizes}")
        except Exception as exc:
            print(f"[PlantUML] Save HSplit failed: {exc}")
        try:
            v = self._vertical_splitter.saveState().toBase64().data().decode("utf-8")
            config.save_puml_vsplit_state(v)
            sizes = self._vertical_splitter.sizes()
            print(f"[PlantUML] Saved VSplit sizes: {sizes}")
        except Exception as exc:
            print(f"[PlantUML] Save VSplit failed: {exc}")

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        if hasattr(self, "_geom_timer"):
            self._geom_timer.start()
        # Update loading overlay position if visible
        if hasattr(self, 'loading_overlay') and self.loading_overlay.isVisible():
            parent = self.loading_overlay.parent()
            if parent:
                self.loading_overlay.setGeometry(0, 0, parent.width(), parent.height())

    def moveEvent(self, event) -> None:  # type: ignore[override]
        super().moveEvent(event)
        if hasattr(self, "_geom_timer"):
            self._geom_timer.start()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        try:
            if hasattr(self, "_geom_timer"):
                self._geom_timer.stop()
        except Exception:
            pass
        self._save_geometry_prefs()
        return super().closeEvent(event)

    def _get_monospace_font(self):
        """Get a monospace font suitable for code editing."""
        from PySide6.QtGui import QFont
        font = QFont()
        font.setFamily("Courier New" if os.name == "nt" else "Courier")
        font.setPointSize(11)
        font.setFixedPitch(True)
        return font

    # --- AI prompt handling ----------------------------------------------------

    def _load_shortcuts(self) -> None:
        """Load PlantUML diagram templates from puml_shortcuts.json into two-level combos."""
        # Reset
        self.shortcuts_category_combo.clear()
        self.shortcuts_variant_combo.clear()
        self.shortcuts_category_combo.addItem("-- Pick a type --", None)
        self.shortcuts_variant_combo.addItem("-- Pick variant --", None)
        self.shortcuts_variant_combo.setEnabled(False)
        self.shortcuts_help_btn.setEnabled(False)
        self._shortcuts_data = []
        try:
            shortcuts = None
            loaded_from = None
            # Candidate locations
            candidates = [
                Path(__file__).resolve().parents[1] / "puml_shortcuts.json",  # sp/app/puml_shortcuts.json
                Path(__file__).resolve().parents[0] / "puml_shortcuts.json",  # sp/app/ui/puml_shortcuts.json (fallback)
                Path.cwd() / "sp" / "app" / "puml_shortcuts.json",
            ]
            for p in candidates:
                try:
                    if p.exists():
                        with open(p, 'r', encoding='utf-8') as f:
                            shortcuts = json.load(f)
                        loaded_from = str(p)
                        print(f"[PlantUML] Loaded shortcuts from {p} ({len(shortcuts) if isinstance(shortcuts, list) else 'invalid'} items)")
                        break
                except Exception as exc:
                    print(f"[PlantUML] Failed reading {p}: {exc}")
            # Try importlib.resources as a last resort
            if shortcuts is None:
                try:
                    import importlib.resources as ilr
                    data = ilr.files("sp.app").joinpath("puml_shortcuts.json").read_text(encoding="utf-8")
                    shortcuts = json.loads(data)
                    loaded_from = "package:sp.app/puml_shortcuts.json"
                    print("[PlantUML] Loaded shortcuts via importlib.resources")
                except Exception as exc:
                    print(f"[PlantUML] resources load failed: {exc}")

            if isinstance(shortcuts, list):
                self._shortcuts_data = shortcuts
                for item in shortcuts:
                    name = item.get("name", "")
                    if name:
                        self.shortcuts_category_combo.addItem(name, name)
                print(f"[PlantUML] Shortcuts loaded: {len(shortcuts)} categories from {loaded_from}")
            else:
                print("[PlantUML] No shortcuts loaded (not a list)")
        except Exception as exc:
            print(f"[PlantUML] Failed to load shortcuts: {exc}")

    def _on_shortcuts_category_changed(self, index: int) -> None:
        """When the category changes, offer Simple/Advanced variants and enable help."""
        # Reset variants
        self.shortcuts_variant_combo.blockSignals(True)
        self.shortcuts_variant_combo.clear()
        self.shortcuts_variant_combo.addItem("-- Pick variant --", None)
        self.shortcuts_variant_combo.blockSignals(False)
        self.shortcuts_variant_combo.setEnabled(False)
        self.shortcuts_help_btn.setEnabled(False)

        if index <= 0:
            return
        name = self.shortcuts_category_combo.itemData(index)
        item = next((it for it in self._shortcuts_data if it.get("name") == name), None)
        if not item:
            return
        # Add variants present in the JSON
        variants = []
        if item.get("simple_puml"):
            variants.append(("Simple", "simple_puml"))
        if item.get("advanced_puml"):
            variants.append(("Advanced", "advanced_puml"))
        for label, key in variants:
            self.shortcuts_variant_combo.addItem(label, key)
        self.shortcuts_variant_combo.setEnabled(bool(variants))
        self.shortcuts_help_btn.setEnabled(bool(item.get("help")))

    def _on_shortcut_variant_selected(self, index: int) -> None:
        """Insert the selected variant (Simple/Advanced) for the current category."""
        if index <= 0:
            return
        cat_index = self.shortcuts_category_combo.currentIndex()
        if cat_index <= 0:
            return
        name = self.shortcuts_category_combo.itemData(cat_index)
        item = next((it for it in self._shortcuts_data if it.get("name") == name), None)
        if not item:
            return
        key = self.shortcuts_variant_combo.itemData(index)
        code = item.get(key or "", "")
        if not code:
            return
        # Replace !!BR!! markers with actual newlines
        code = code.replace("!!BR!!", "\n")
        cursor = self.editor.textCursor()
        pos = cursor.position()
        text = self.editor.toPlainText()
        if pos > 0 and text and text[pos - 1] != '\n':
            cursor.insertText('\n')
        cursor.insertText(code)
        cursor.insertText('\n')
        # Reset variant to placeholder after insert so user can choose again
        try:
            self.shortcuts_variant_combo.blockSignals(True)
            self.shortcuts_variant_combo.setCurrentIndex(0)
        finally:
            self.shortcuts_variant_combo.blockSignals(False)

    def _on_shortcuts_help_clicked(self) -> None:
        cat_index = self.shortcuts_category_combo.currentIndex()
        if cat_index <= 0:
            return
        name = self.shortcuts_category_combo.itemData(cat_index)
        item = next((it for it in self._shortcuts_data if it.get("name") == name), None)
        if not item:
            return
        url = item.get("help")
        if not url:
            return
        try:
            QDesktopServices.openUrl(QUrl(url))
        except Exception as exc:
            if _LOGGING:
                print(f"[PlantUML] Failed to open help URL: {exc}")

    def _toggle_focus_editor_ai(self) -> None:
        """Toggle focus between editor and AI chat input (Ctrl+Shift+Space)."""
        if self.editor.hasFocus():
            self.ai_input.setFocus()
            self.ai_input.selectAll()
        else:
            self.editor.setFocus()

    def _on_splitter_moved(self, pos: int, index: int) -> None:
        """Handle splitter moves - update loading overlay position if visible."""
        if hasattr(self, 'loading_overlay') and self.loading_overlay.isVisible():
            # Use QTimer to defer geometry update until splitter animation completes
            def update_overlay():
                if hasattr(self, 'loading_overlay'):
                    parent = self.loading_overlay.parent()
                    if parent:
                        self.loading_overlay.setGeometry(0, 0, parent.width(), parent.height())
            QTimer.singleShot(10, update_overlay)

    def _create_ai_chat_panel(self) -> QWidget:
        """Create AI chat panel with message input and send button."""
        panel = QWidget()
        panel.setStyleSheet("QWidget { background-color: #1e1e1e; border-top: 1px solid #444; } QLineEdit { background-color: #2d2d2d; color: #e0e0e0; border: 1px solid #444; }")
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # Chat input field with history and Ctrl+Enter
        self.ai_input = ChatLineEdit(self._ai_prompt_history)
        self.ai_input.setPlaceholderText("Describe the diagram you want to generate or update...")
        self.ai_input.sendRequested.connect(self._on_ai_send)
        # Set height to approximately 3 lines
        font_metrics = self.ai_input.fontMetrics()
        line_height = font_metrics.lineSpacing()
        self.ai_input.setFixedHeight(line_height * 3)
        layout.addWidget(self.ai_input)
        
        # Send button
        self.ai_send_btn = QToolButton()
        self.ai_send_btn.setText("📤 Send")
        self.ai_send_btn.setToolTip("Send message to AI (Ctrl+Enter)")
        self.ai_send_btn.clicked.connect(self._on_ai_send)
        self.ai_send_btn.setFixedHeight(line_height * 3)
        layout.addWidget(self.ai_send_btn)
        
        panel.setLayout(layout)
        return panel

    def _load_ai_servers_models(self) -> None:
        """Load available servers and models from configuration."""
        try:
            from sp.app.ui.ai_chat_panel import ServerManager, get_available_models
            
            mgr = ServerManager()
            servers = mgr.load_servers()
            server_names = [srv["name"] for srv in servers]
            
            self.ai_server_combo.clear()
            self.ai_server_combo.addItems(server_names)
            
            # Set to current default or first available
            try:
                default_server = config.load_default_ai_server()
                if default_server and default_server in server_names:
                    self.ai_server_combo.setCurrentText(default_server)
                elif server_names:
                    self.ai_server_combo.setCurrentIndex(0)
            except Exception:
                if server_names:
                    self.ai_server_combo.setCurrentIndex(0)
            
            # Load models for selected server
            self._refresh_ai_models()
        except Exception as exc:
            if _LOGGING:
                print(f"[PlantUML] Failed to load AI servers: {exc}")

    def _on_ai_server_changed(self, server_name: str) -> None:
        """Refresh models when server selection changes."""
        self._refresh_ai_models()

    def _refresh_ai_models(self) -> None:
        """Refresh available models for the selected server."""
        try:
            from sp.app.ui.ai_chat_panel import ServerManager, get_available_models
            
            mgr = ServerManager()
            server_name = self.ai_server_combo.currentText()
            if not server_name:
                self.ai_model_combo.clear()
                return
            
            server = mgr.get_server(server_name)
            if not server:
                self.ai_model_combo.clear()
                return
            
            models = get_available_models(server)
            self.ai_model_combo.clear()
            self.ai_model_combo.addItems(models)
            
            # Set to current default if available
            try:
                default_model = config.load_default_ai_model()
                if default_model and default_model in models:
                    self.ai_model_combo.setCurrentText(default_model)
                elif models:
                    self.ai_model_combo.setCurrentIndex(0)
            except Exception:
                if models:
                    self.ai_model_combo.setCurrentIndex(0)
        except Exception as exc:
            if _LOGGING:
                print(f"[PlantUML] Failed to refresh AI models: {exc}")

    def _on_ai_send(self) -> None:
        """Send chat message to AI and display response."""
        user_message = self.ai_input.text().strip()
        if not user_message:
            return
        
        self.ai_input.clear()
        self.ai_input.setEnabled(False)
        self.ai_send_btn.setEnabled(False)
        
        # Show toast message and busy cursor
        self.statusBar().showMessage("🤔 AI is thinking...", 0)
        QApplication.setOverrideCursor(Qt.BusyCursor)
        
        # Save to history (no duplicates back-to-back)
        try:
            if not self._ai_prompt_history or self._ai_prompt_history[-1] != user_message:
                self._ai_prompt_history.append(user_message)
            # Reset history cursor in input
            if hasattr(self.ai_input, "_history_index"):
                self.ai_input._history_index = None
        except Exception:
            pass
        
        try:
            # Get selected server and model from dropdowns
            server_name = self.ai_server_combo.currentText()
            model_name = self.ai_model_combo.currentText()
            
            if not server_name or not model_name:
                QMessageBox.warning(self, "Error", "Please select a server and model")
                self.ai_input.setEnabled(True)
                self.ai_send_btn.setEnabled(True)
                return
            
            # Get server config
            try:
                from sp.app.ui.ai_chat_panel import ServerManager
                mgr = ServerManager()
                server_config = mgr.get_server(server_name)
                if not server_config:
                    QMessageBox.warning(self, "Error", f"Server '{server_name}' not found")
                    self.ai_input.setEnabled(True)
                    self.ai_send_btn.setEnabled(True)
                    return
            except Exception as exc:
                QMessageBox.warning(self, "Error", f"Failed to get server config: {exc}")
                self.ai_input.setEnabled(True)
                self.ai_send_btn.setEnabled(True)
                return
            
            # Load system prompt
            try:
                prompt_path = Path(__file__).resolve().parents[1] / "puml_prompt.txt"
                system_prompt = prompt_path.read_text(encoding="utf-8")
            except Exception:
                system_prompt = "You are a helpful assistant. You generate PlantUML diagrams."
            
            # Get current editor content
            editor_content = self.editor.toPlainText()
            
            # Construct messages
            user_content = f"Current diagram:\n```\n{editor_content}\n```\n\nUser request: {user_message}"
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ]
            
            # Launch streaming worker with selected model (NOT persisted)
            worker = ApiWorker(server_config, messages, model_name, stream=True, parent=self)
            self._ai_worker = worker
            self._ai_response_buffer = ""
            worker.chunk.connect(self._on_ai_response_chunk)
            worker.finished.connect(self._on_ai_response_finished)
            worker.failed.connect(self._on_ai_response_failed)
            worker.start()
            
        except Exception as exc:
            if _LOGGING:
                print(f"[PlantUML] AI send error: {exc}")
            QMessageBox.critical(self, "Error", f"AI request failed: {exc}")
            self.ai_input.setEnabled(True)
            self.ai_send_btn.setEnabled(True)

    def _on_ai_response_chunk(self, chunk: str) -> None:
        """Accumulate AI response chunks."""
        try:
            if not hasattr(self, '_ai_response_buffer'):
                self._ai_response_buffer = ""
            self._ai_response_buffer += chunk or ""
            if _LOGGING and chunk:
                print(f"[PlantUML] Received chunk: {len(chunk)} chars")
        except Exception as exc:
            if _LOGGING:
                print(f"[PlantUML] Chunk error: {exc}")

    def _on_ai_response_finished(self, content: str) -> None:
        """Display AI response with accept/decline buttons."""
        QApplication.restoreOverrideCursor()
        self.ai_input.setEnabled(True)
        self.ai_send_btn.setEnabled(True)
        self.statusBar().showMessage("✓ AI response received", 3000)  # Show for 3 seconds
        
        try:
            response = self._ai_response_buffer or content or ""
            if _LOGGING:
                print(f"[PlantUML] Response finished. Buffer len: {len(self._ai_response_buffer)}, Content len: {len(content)}, Final response len: {len(response)}")
            
            if not response:
                if _LOGGING:
                    print("[PlantUML] No response received")
                return
            
            # Extract PlantUML code from response (remove markdown code blocks if present)
            if "```plantuml" in response.lower():
                start = response.lower().find("```plantuml") + 11
                end = response.find("```", start)
                if end > start:
                    response = response[start:end].strip()
            elif "```" in response:
                start = response.find("```") + 3
                end = response.find("```", start)
                if end > start:
                    response = response[start:end].strip()
            
            if _LOGGING:
                print(f"[PlantUML] Extracted response len: {len(response)}")
                print(f"[PlantUML] First 100 chars: {response[:100]}")
            
            # Show response dialog with accept/decline
            self._show_ai_response_dialog(response)
            
        except Exception as exc:
            if _LOGGING:
                print(f"[PlantUML] Response finish error: {exc}")
            import traceback
            traceback.print_exc()
        finally:
            try:
                self._ai_worker = None
            except Exception:
                pass

    def _on_ai_response_failed(self, message: str) -> None:
        """Handle AI error."""
        QApplication.restoreOverrideCursor()
        self.ai_input.setEnabled(True)
        self.ai_send_btn.setEnabled(True)
        self.statusBar().showMessage("✗ AI request failed", 3000)  # Show for 3 seconds
        QMessageBox.warning(self, "AI Error", f"Failed to get AI response: {message}")
        try:
            self._ai_worker = None
        except Exception:
            pass

    def _show_ai_response_dialog(self, ai_text: str) -> None:
        """Show side-by-side diff of original vs AI-generated PlantUML."""
        if _LOGGING:
            print(f"[PlantUML] Showing response dialog with {len(ai_text)} chars")
        
        dialog = QDialog(self)
        dialog.setWindowTitle("Review AI Generated Diagram - Accept or Decline")
        dialog.setGeometry(50, 50, 1400, 800)
        dialog.setStyleSheet("""
            QDialog {
                background-color: #1e1e1e;
            }
            QLabel {
                color: #e0e0e0;
            }
            QPushButton {
                background-color: #2d2d2d;
                color: #e0e0e0;
                border: 1px solid #444;
                padding: 8px 16px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #3d3d3d;
            }
        """)
        
        layout = QVBoxLayout()
        
        # Title
        title = QLabel("Review Changes - Left: Current | Right: AI Generated")
        title.setStyleSheet("color: #e0e0e0; font-weight: bold; font-size: 12px; padding: 5px;")
        layout.addWidget(title)
        
        # Side-by-side diff comparison
        diff_layout = QHBoxLayout()
        
        # Left panel: Original
        left_panel = QWidget()
        left_layout = QVBoxLayout()
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_label = QLabel("Current PlantUML")
        left_label.setStyleSheet("color: #e0e0e0; font-weight: bold;")
        left_layout.addWidget(left_label)
        
        original_text = self.editor.toPlainText()
        left_display = self._create_diff_display(original_text, ai_text, is_original=True)
        left_layout.addWidget(left_display)
        left_panel.setLayout(left_layout)
        
        # Right panel: AI Generated
        right_panel = QWidget()
        right_layout = QVBoxLayout()
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_label = QLabel("AI Generated PlantUML")
        right_label.setStyleSheet("color: #e0e0e0; font-weight: bold;")
        right_layout.addWidget(right_label)
        
        right_display = self._create_diff_display(original_text, ai_text, is_original=False)
        right_layout.addWidget(right_display)
        right_panel.setLayout(right_layout)
        
        # Add both panels
        diff_layout.addWidget(left_panel, stretch=1)
        diff_layout.addWidget(right_panel, stretch=1)
        layout.addLayout(diff_layout, stretch=1)
        
        # Accept/Decline buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        accept_btn = QPushButton("✓ Accept Changes")
        accept_btn.setMinimumWidth(150)
        accept_btn.setStyleSheet("""
            QPushButton {
                background-color: #1e5c1e;
                color: #90ee90;
                border: 1px solid #4caf50;
                padding: 8px 16px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #2d7a2d;
            }
        """)
        accept_btn.clicked.connect(lambda: self._accept_ai_response(ai_text, dialog))
        button_layout.addWidget(accept_btn)
        
        decline_btn = QPushButton("✗ Decline")
        decline_btn.setMinimumWidth(150)
        decline_btn.setStyleSheet("""
            QPushButton {
                background-color: #5c1e1e;
                color: #ff6b6b;
                border: 1px solid #f44336;
                padding: 8px 16px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #7a2d2d;
            }
        """)
        decline_btn.clicked.connect(dialog.reject)
        button_layout.addWidget(decline_btn)
        
        layout.addLayout(button_layout)
        dialog.setLayout(layout)
        
        if _LOGGING:
            print("[PlantUML] Displaying dialog...")
        
        # Keep a reference to prevent garbage collection
        self._active_diff_dialog = dialog
        
        # Show the dialog
        dialog.exec()

    def _create_diff_display(self, original: str, modified: str, is_original: bool) -> QTextEdit:
        """Create a text display with diff highlighting."""
        display = QTextEdit()
        display.setReadOnly(True)
        display.setStyleSheet("""
            QTextEdit {
                background-color: #2d2d2d;
                color: #e0e0e0;
                border: 1px solid #444;
                padding: 5px;
                font-family: Courier, monospace;
                font-size: 10px;
            }
        """)
        
        if is_original:
            # Show original text with removed lines highlighted in red
            display.setPlainText(original)
            self._highlight_diff_lines(display, original, modified, is_added=False)
        else:
            # Show modified text with added lines highlighted in green
            display.setPlainText(modified)
            self._highlight_diff_lines(display, original, modified, is_added=True)
        
        return display

    def _highlight_diff_lines(self, text_edit: QTextEdit, original: str, modified: str, is_added: bool) -> None:
        """Highlight added/removed/changed lines in the text edit."""
        from PySide6.QtGui import QTextCharFormat, QTextBlockFormat, QBrush
        from PySide6.QtGui import QTextCursor as QtgQTextCursor
        
        original_lines = original.splitlines()
        modified_lines = modified.splitlines()
        
        cursor = text_edit.textCursor()
        cursor.movePosition(QtgQTextCursor.MoveOperation.Start)
        
        if is_added:
            # Show modified version with additions highlighted
            for i, line in enumerate(modified_lines):
                if i < len(original_lines):
                    if line != original_lines[i]:
                        # Changed line - orange/yellow
                        fmt = QTextCharFormat()
                        fmt.setBackground(QBrush(QColor(100, 80, 0)))  # Dark orange
                        cursor.select(QtgQTextCursor.SelectionType.LineUnderCursor)
                        cursor.setCharFormat(fmt)
                else:
                    # New line - green
                    fmt = QTextCharFormat()
                    fmt.setBackground(QBrush(QColor(0, 80, 0)))  # Dark green
                    cursor.select(QtgQTextCursor.SelectionType.LineUnderCursor)
                    cursor.setCharFormat(fmt)
                
                # Move to next line
                if not cursor.movePosition(QtgQTextCursor.MoveOperation.Down):
                    break
        else:
            # Show original version with removals highlighted
            for i, line in enumerate(original_lines):
                if i < len(modified_lines):
                    if line != modified_lines[i]:
                        # Changed line - orange/yellow
                        fmt = QTextCharFormat()
                        fmt.setBackground(QBrush(QColor(100, 80, 0)))  # Dark orange
                        cursor.select(QtgQTextCursor.SelectionType.LineUnderCursor)
                        cursor.setCharFormat(fmt)
                else:
                    # Removed line - red
                    fmt = QTextCharFormat()
                    fmt.setBackground(QBrush(QColor(80, 0, 0)))  # Dark red
                    cursor.select(QtgQTextCursor.SelectionType.LineUnderCursor)
                    cursor.setCharFormat(fmt)
                
                # Move to next line
                if not cursor.movePosition(QtgQTextCursor.MoveOperation.Down):
                    break

    def _accept_ai_response(self, ai_text: str, dialog: QDialog) -> None:
        """Accept AI response: replace buffer, save, render."""
        try:
            # Replace entire editor buffer
            self.editor.setPlainText(ai_text)
            
            # Save file
            self._save_file()
            
            # Render diagram
            self._render()
            
            dialog.accept()
        except Exception as exc:
            if _LOGGING:
                print(f"[PlantUML] Accept error: {exc}")
            QMessageBox.critical(self, "Error", f"Failed to accept response: {exc}")

    def _resolve_ai_server_and_model(self) -> Optional[tuple[dict, str]]:
        """Resolve AI server and model configuration."""
        try:
            server_mgr = ServerManager()
        except Exception:
            return None
        server_config: dict = {}
        try:
            default_server_name = config.load_default_ai_server()
        except Exception:
            default_server_name = None
        if default_server_name:
            try:
                server_config = server_mgr.get_server(default_server_name) or {}
            except Exception:
                server_config = {}
        if not server_config:
            try:
                active = server_mgr.get_active_server_name()
                if active:
                    server_config = server_mgr.get_server(active) or {}
            except Exception:
                server_config = {}
        if not server_config:
            try:
                servers = server_mgr.load_servers()
                if servers:
                    server_config = servers[0]
            except Exception:
                server_config = {}
        if not server_config:
            return None
        try:
            model = config.load_default_ai_model()
        except Exception:
            model = None
        if not model:
            model = server_config.get("default_model") or "gpt-3.5-turbo"
        return server_config, model

    def _load_file(self) -> None:
        """Load PlantUML file content into editor."""
        if self.file_path.exists():
            try:
                content = self.file_path.read_text(encoding="utf-8")
                if content.strip():
                    self.editor.setPlainText(content)
                    return
            except Exception as exc:
                QMessageBox.warning(self, "Error", f"Failed to load file: {exc}")
        # New or empty file
        template = self._default_puml_template()
        if not template:
            template = """@startuml
' PlantUML diagram
' https://plantuml.com/

' Add your diagram here
A -> B: message
B --> A: response
@enduml
"""
        self.editor.setPlainText(template)

    def _default_puml_template(self) -> str:
        """Return the first simple_puml template from shortcuts, if available."""
        if self._shortcuts_data:
            first_item = self._shortcuts_data[0]
            if isinstance(first_item, dict):
                template = first_item.get("simple_puml", "")
                if template:
                    return template
        try:
            shortcuts_path = Path(__file__).resolve().parents[1] / "puml_shortcuts.json"
            data = shortcuts_path.read_text(encoding="utf-8")
            shortcuts = json.loads(data)
            if isinstance(shortcuts, list) and shortcuts:
                first_item = shortcuts[0]
                if isinstance(first_item, dict):
                    return first_item.get("simple_puml", "") or ""
        except Exception:
            return ""
        return ""

    def _save_file(self) -> None:
        """Save editor content to file."""
        try:
            content = self.editor.toPlainText()
            if self._on_save is not None:
                ok, message = self._on_save(content)
                if not ok:
                    QMessageBox.critical(self, "Error", message or "Failed to save file.")
                    return
            self.file_path.write_text(content, encoding="utf-8")
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to save file: {exc}")

    def _on_editor_changed(self) -> None:
        """Debounce rendering when editor text changes."""
        self._editor_dirty = True
        self._update_render_status_label()
        if self._auto_render_enabled:
            self.render_timer.start()

    def _render(self) -> None:
        """Render the PlantUML diagram."""
        self.render_timer.stop()
        self._editor_dirty = False
        self._update_render_status_label()
        
        # Check if window still exists
        try:
            if not self or not hasattr(self, 'editor'):
                return
            test_attr = self.editor  # Test if object is still valid
        except RuntimeError:
            return
        
        # Show loading overlay
        self._show_loading()
        
        puml_text = self.editor.toPlainText()
        
        try:
            result = self.renderer.render_svg(puml_text)
        except Exception as exc:
            print(f"[PlantUML Editor] Render exception: {exc}", file=__import__('sys').stdout, flush=True)
            self._hide_loading()
            return
        
        # Check again after async operation
        try:
            if not self or not hasattr(self, 'preview_label'):
                return
            test_attr = self.preview_label  # Test if object is still valid
        except RuntimeError:
            return
        
        try:
            if result.success and result.svg_content:
                # Store SVG for export/copy
                self._last_svg = result.svg_content
                
                # Convert SVG to image for display
                try:
                    self.preview_pixmap = self._svg_to_pixmap(result.svg_content)
                    if self.preview_pixmap:
                        self._update_preview_display()
                        self.render_btn.setText("✓ Render")
                    else:
                        # Show error diagram instead of text
                        error_svg = _generate_error_svg("Failed to convert SVG to image")
                        self._last_svg = error_svg
                        self.preview_pixmap = self._svg_to_pixmap(error_svg)
                        if self.preview_pixmap:
                            self._update_preview_display()
                        self.render_btn.setText("✗ Render")
                    self._hide_loading()
                except Exception as svg_exc:
                    print(f"[PlantUML Editor] SVG error: {svg_exc}", file=__import__('sys').stdout, flush=True)
                    # Show error diagram
                    error_svg = _generate_error_svg(f"SVG rendering error:\n{str(svg_exc)}")
                    self._last_svg = error_svg
                    try:
                        self.preview_pixmap = self._svg_to_pixmap(error_svg)
                        if self.preview_pixmap:
                            self._update_preview_display()
                    except RuntimeError:
                        pass
                    self.render_btn.setText("✗ Render")
                    self._hide_loading()
            else:
                error_msg = result.error_message or result.stderr or "Unknown error"
                # Extract line number if present (e.g., "line 5: Error description")
                line_num = 0
                try:
                    if "line" in error_msg.lower():
                        import re
                        match = re.search(r'line\s+(\d+)', error_msg.lower())
                        if match:
                            line_num = int(match.group(1))
                except Exception:
                    pass
                
                # Show error diagram with stderr content
                error_display = f"PlantUML Error\n\n{error_msg}"
                if result.stderr and result.stderr != error_msg:
                    error_display += f"\n\nDetails:\n{result.stderr[:500]}"
                
                error_svg = _generate_error_svg(error_display, line_num)
                self._last_svg = error_svg
                try:
                    self.preview_pixmap = self._svg_to_pixmap(error_svg)
                    if self.preview_pixmap:
                        self._update_preview_display()
                except RuntimeError:
                    pass
                self.render_btn.setText("✗ Render")
                self._hide_loading()
        except Exception as exc:
            print(f"[PlantUML Editor] Render error: {exc}", file=__import__('sys').stdout, flush=True)
            error_svg = _generate_error_svg(f"Internal error:\n{str(exc)}")
            self._last_svg = error_svg
            try:
                self.preview_pixmap = self._svg_to_pixmap(error_svg)
                if self.preview_pixmap:
                    self._update_preview_display()
            except RuntimeError:
                pass
            self.render_btn.setText("✗ Render")
            self._hide_loading()

    def _svg_to_pixmap(self, svg_content: str) -> Optional[QPixmap]:
        """Convert SVG string to QPixmap."""
        try:
            from PySide6.QtSvg import QSvgRenderer
            from PySide6.QtCore import QByteArray
            
            svg_bytes = QByteArray(svg_content.encode("utf-8"))
            renderer = QSvgRenderer(svg_bytes)
            
            if not renderer.isValid():
                return None
            
            size = renderer.defaultSize()
            if not size.isValid():
                size = QSize(800, 600)
            
            pixmap = QPixmap(size)
            pixmap.fill(Qt.white)
            
            from PySide6.QtGui import QPainter
            painter = QPainter(pixmap)
            renderer.render(painter)
            painter.end()
            
            return pixmap
        except Exception:
            return None

    def _update_preview_display(self) -> None:
        """Update the preview label with current pixmap and zoom level."""
        # Check if object still exists
        try:
            if not self or not hasattr(self, 'preview_label') or not self.preview_pixmap:
                return
            test_attr = self.preview_label  # Test if object is still valid
        except RuntimeError:
            return
        
        # Apply zoom
        zoom_factor = 1.0 + (self.preview_zoom_level * 0.1)
        size = self.preview_pixmap.size()
        new_size = QSize(int(size.width() * zoom_factor), int(size.height() * zoom_factor))
        
        scaled_pixmap = self.preview_pixmap.scaledToWidth(
            new_size.width(),
            Qt.SmoothTransformation
        )
        
        try:
            self.preview_label.setPixmap(scaled_pixmap)
        except RuntimeError:
            pass  # Widget was deleted, ignore

    def _zoom_in_editor(self) -> None:
        """Zoom in editor text."""
        self.editor_zoom_level += 1
        font = self.editor.font()
        font.setPointSize(11 + self.editor_zoom_level)
        self.editor.setFont(font)
        try:
            config.save_puml_editor_zoom(self.editor_zoom_level)
        except Exception:
            pass
        if hasattr(self, "_geom_timer"):
            self._geom_timer.start()

    def _zoom_out_editor(self) -> None:
        """Zoom out editor text."""
        if self.editor_zoom_level > -5:  # Min size 6pt
            self.editor_zoom_level -= 1
            font = self.editor.font()
            font.setPointSize(11 + self.editor_zoom_level)
            self.editor.setFont(font)
            try:
                config.save_puml_editor_zoom(self.editor_zoom_level)
            except Exception:
                pass
            if hasattr(self, "_geom_timer"):
                self._geom_timer.start()

    def _zoom_in_preview(self) -> None:
        """Zoom in preview image."""
        self.preview_zoom_level += 1
        try:
            self._update_preview_display()
        except RuntimeError:
            pass
        try:
            config.save_puml_preview_zoom(self.preview_zoom_level)
        except Exception:
            pass
        if hasattr(self, "_geom_timer"):
            self._geom_timer.start()

    def _zoom_out_preview(self) -> None:
        """Zoom out preview image."""
        if self.preview_zoom_level > -10:  # Min zoom
            self.preview_zoom_level -= 1
            try:
                self._update_preview_display()
            except RuntimeError:
                pass
            try:
                config.save_puml_preview_zoom(self.preview_zoom_level)
            except Exception:
                pass
            if hasattr(self, "_geom_timer"):
                self._geom_timer.start()

    def _on_preview_wheel_zoom(self, delta: int) -> None:
        """Handle Ctrl+MouseWheel zoom on preview."""
        if delta > 0:
            self._zoom_in_preview()
        else:
            self._zoom_out_preview()

    def _show_export_menu(self) -> None:
        """Show export options menu."""
        menu = QMenu(self)
        
        export_svg = menu.addAction("Export as SVG...")
        export_svg.triggered.connect(self._export_svg)
        
        export_png = menu.addAction("Export as PNG...")
        export_png.triggered.connect(self._export_png)
        
        menu.addSeparator()
        
        copy_svg = menu.addAction("Copy SVG")
        copy_svg.triggered.connect(self._copy_svg)
        
        copy_png = menu.addAction("Copy PNG")
        copy_png.triggered.connect(self._copy_png)
        
        menu.exec(self.export_btn.mapToGlobal(self.export_btn.rect().bottomLeft()))

    def _show_preview_context_menu(self, pos) -> None:
        """Show right-click menu on preview with copy options."""
        menu = QMenu(self)
        copy_svg = menu.addAction("Copy SVG")
        copy_svg.triggered.connect(self._copy_svg)
        copy_png = menu.addAction("Copy PNG")
        copy_png.triggered.connect(self._copy_png)
        menu.exec(self.preview_label.mapToGlobal(pos))

    def _export_svg(self) -> None:
        """Export diagram as SVG file."""
        if not hasattr(self, '_last_svg'):
            QMessageBox.warning(self, "No Diagram", "Render the diagram first.")
            return
        
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export as SVG",
            str(self.file_path.with_suffix(".svg")),
            "SVG Files (*.svg)"
        )
        
        if file_path:
            try:
                Path(file_path).write_text(self._last_svg, encoding="utf-8")
                QMessageBox.information(self, "Exported", f"Saved to {file_path}")
            except Exception as exc:
                QMessageBox.critical(self, "Error", f"Failed to export: {exc}")

    def _export_png(self) -> None:
        """Export diagram as PNG file."""
        if not self.preview_pixmap:
            QMessageBox.warning(self, "No Diagram", "Render the diagram first.")
            return
        
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export as PNG",
            str(self.file_path.with_suffix(".png")),
            "PNG Files (*.png)"
        )
        
        if file_path:
            try:
                self.preview_pixmap.save(file_path, "PNG")
                QMessageBox.information(self, "Exported", f"Saved to {file_path}")
            except Exception as exc:
                QMessageBox.critical(self, "Error", f"Failed to export: {exc}")

    def _copy_svg(self) -> None:
        """Copy SVG to clipboard."""
        if not hasattr(self, '_last_svg'):
            QMessageBox.warning(self, "No Diagram", "Render the diagram first.")
            return
        
        clipboard = QApplication.clipboard()
        clipboard.setText(self._last_svg)
        self.statusBar().showMessage("SVG copied to clipboard", 2000)

    def _copy_png(self) -> None:
        """Copy PNG to clipboard."""
        if not self.preview_pixmap:
            QMessageBox.warning(self, "No Diagram", "Render the diagram first.")
            return
        
        clipboard = QApplication.clipboard()
        buffer = QBuffer()
        buffer.open(QIODevice.WriteOnly)
        self.preview_pixmap.save(buffer, "PNG")
        png_bytes = bytes(buffer.data())
        mime = QMimeData()
        mime.setData("image/png", png_bytes)
        mime.setImageData(self.preview_pixmap.toImage())
        clipboard.setMimeData(mime)
        self.statusBar().showMessage("PNG copied to clipboard", 2000)

    def closeEvent(self, event) -> None:
        """Save file before closing."""
        self._save_file()
        super().closeEvent(event)
