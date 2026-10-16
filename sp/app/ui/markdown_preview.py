"""Token-free, in-process reading view for Markdown editors."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import (
    QColor,
    QKeyEvent,
    QKeySequence,
    QMouseEvent,
    QPalette,
    QShortcut,
    QTextCursor,
    QTextDocument,
    QTextFormat,
)
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextBrowser,
    QToolButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from sp.app.ui.theme import theme_value
from sp.markdown_html import nearest_source_anchor, render_markdown_html


class _ReadingBrowser(QTextBrowser):
    """Selectable HTML reader with optional vi-style scrolling."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.vi_enabled = False
        self.vi_cursor_style = "line"
        self._pending_g = False
        self._cursor_sync_pending = False
        self._reading_cursor = QTextCursor()
        self._scroll_animation = QPropertyAnimation(
            self.verticalScrollBar(), b"value", self
        )
        self._scroll_animation.setDuration(120)
        self._scroll_animation.setEasingCurve(QEasingCurve.OutCubic)
        self._scroll_animation.finished.connect(self._schedule_cursor_sync)
        self.verticalScrollBar().valueChanged.connect(self._schedule_cursor_sync)

    def _schedule_cursor_sync(self, *_args) -> None:
        if self._cursor_sync_pending:
            return
        self._cursor_sync_pending = True
        QTimer.singleShot(0, self, self._run_scheduled_cursor_sync)

    def _run_scheduled_cursor_sync(self) -> None:
        self._cursor_sync_pending = False
        self._sync_cursor_to_viewport()

    def set_vi_state(self, enabled: bool, cursor_style: str = "line") -> None:
        self.vi_enabled = bool(enabled)
        self.setCursorWidth(0 if self.vi_enabled else 1)
        normalized = str(cursor_style or "").strip().lower()
        self.vi_cursor_style = normalized if normalized in {"block", "line"} else "line"
        self._sync_cursor_to_viewport()

    def refresh_reading_cursor(self) -> None:
        if not self.vi_enabled or self.document().isEmpty():
            self.setExtraSelections([])
            return
        cursor = QTextCursor(self._reading_cursor)
        if cursor.isNull():
            cursor = self.cursorForPosition(
                QPoint(max(0, self.viewport().width() // 3), self.viewport().height() // 2)
            )
        cursor = self._nearest_content_cursor(cursor)
        cursor.clearSelection()
        self._reading_cursor = QTextCursor(cursor)
        selection = QTextEdit.ExtraSelection()
        selection.cursor = QTextCursor(cursor)
        if self.vi_cursor_style == "block" and not selection.cursor.atEnd():
            selection.cursor.movePosition(QTextCursor.Right, QTextCursor.KeepAnchor)
            selection.format.setProperty(QTextFormat.FullWidthSelection, False)
            selection.format.setBackground(
                QColor(str(theme_value("markdown_preview.cursor.block_bg", "#b259ff")))
            )
            selection.format.setForeground(
                QColor(str(theme_value("markdown_preview.cursor.block_text", "#111111")))
            )
        else:
            selection.format.setProperty(QTextFormat.FullWidthSelection, True)
            selection.format.setBackground(
                QColor(str(theme_value("markdown_preview.cursor.line_bg", "#b259ff")))
            )
            selection.format.setForeground(
                QColor(str(theme_value("markdown_preview.cursor.line_text", "#111111")))
            )
        self.setExtraSelections([selection])

    @staticmethod
    def _nearest_content_cursor(cursor: QTextCursor) -> QTextCursor:
        """Skip the empty blocks emitted for source-position HTML anchors."""
        origin = cursor.block()
        if origin.isValid() and origin.text().strip():
            return cursor
        block = origin.next()
        while block.isValid():
            if block.text().strip():
                return QTextCursor(block)
            block = block.next()
        block = origin.previous()
        while block.isValid():
            if block.text().strip():
                return QTextCursor(block)
            block = block.previous()
        return cursor

    def _sync_cursor_to_viewport(self) -> None:
        if not self.vi_enabled or self.document().isEmpty():
            self.refresh_reading_cursor()
            return
        self._reading_cursor = self._nearest_content_cursor(self.cursorForPosition(
            QPoint(max(0, self.viewport().width() // 3), self.viewport().height() // 2)
        ))
        self.refresh_reading_cursor()

    def page_scroll(self, direction: int, *, fraction: float = 0.85) -> None:
        scrollbar = self.verticalScrollBar()
        step = max(1, round(scrollbar.pageStep() * fraction))
        target = max(
            scrollbar.minimum(),
            min(scrollbar.maximum(), scrollbar.value() + (1 if direction > 0 else -1) * step),
        )
        self._scroll_animation.stop()
        self._scroll_animation.setDuration(120)
        self._scroll_animation.setStartValue(scrollbar.value())
        self._scroll_animation.setEndValue(target)
        self._scroll_animation.start()

    def line_scroll(self, direction: int) -> None:
        scrollbar = self.verticalScrollBar()
        step = max(24, scrollbar.singleStep() * 3)
        target = max(
            scrollbar.minimum(),
            min(scrollbar.maximum(), scrollbar.value() + (1 if direction > 0 else -1) * step),
        )
        self._scroll_animation.stop()
        self._scroll_animation.setDuration(80)
        self._scroll_animation.setStartValue(scrollbar.value())
        self._scroll_animation.setEndValue(target)
        self._scroll_animation.start()

    def jump_to_edge(self, *, end: bool) -> None:
        self._scroll_animation.stop()
        scrollbar = self.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum() if end else scrollbar.minimum())
        self._sync_cursor_to_viewport()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        super().mousePressEvent(event)
        if self.vi_enabled:
            self._reading_cursor = self._nearest_content_cursor(
                self.cursorForPosition(event.position().toPoint())
            )
            self.refresh_reading_cursor()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        modifiers = event.modifiers() & ~Qt.KeypadModifier
        if (
            self.vi_enabled
            and modifiers == Qt.ShiftModifier
            and event.key() == Qt.Key_G
        ):
            self.jump_to_edge(end=True)
            self._pending_g = False
            event.accept()
            return
        if self.vi_enabled and modifiers == Qt.NoModifier:
            scrollbar = self.verticalScrollBar()
            key = event.key()
            if key in (Qt.Key_J, Qt.Key_K):
                direction = 1 if key == Qt.Key_J else -1
                self.line_scroll(direction)
                self._pending_g = False
                event.accept()
                return
            if key == Qt.Key_G:
                if self._pending_g:
                    self.jump_to_edge(end=False)
                    self._pending_g = False
                else:
                    self._pending_g = True
                event.accept()
                return
            self._pending_g = False
        if self.vi_enabled and modifiers == Qt.ControlModifier and event.key() in (Qt.Key_D, Qt.Key_U):
            direction = 1 if event.key() == Qt.Key_D else -1
            self.page_scroll(direction, fraction=0.5)
            event.accept()
            return
        if modifiers == Qt.NoModifier and event.key() in (Qt.Key_PageDown, Qt.Key_PageUp):
            self.page_scroll(1 if event.key() == Qt.Key_PageDown else -1)
            event.accept()
            return
        self._pending_g = False
        super().keyPressEvent(event)


class MarkdownReadingView(QWidget):
    """A shared read-only Markdown surface used by both desktop editors."""

    editRequested = Signal()
    linkActivated = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.last_markdown = ""
        self.last_html = ""
        self.base_directory: Path | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QFrame(self)
        header.setObjectName("markdownReadingHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(10, 4, 8, 4)
        title = QLabel("Reading View", header)
        title.setAccessibleName("Reading View")
        header_layout.addWidget(title)
        header_layout.addStretch()
        self.find_button = QToolButton(header)
        self.find_button.setText("Find")
        self.find_button.setToolTip("Find in Reading View")
        self.find_button.clicked.connect(self.show_find)
        header_layout.addWidget(self.find_button)
        self.edit_button = QPushButton("Edit", header)
        self.edit_button.setToolTip("Return to the Markdown editor")
        self.edit_button.clicked.connect(self.editRequested)
        header_layout.addWidget(self.edit_button)
        layout.addWidget(header)

        self.find_bar = QFrame(self)
        find_layout = QHBoxLayout(self.find_bar)
        find_layout.setContentsMargins(8, 4, 8, 4)
        self.find_query = QLineEdit(self.find_bar)
        self.find_query.setPlaceholderText("Find in preview")
        self.find_query.returnPressed.connect(self.find_next)
        find_layout.addWidget(self.find_query, 1)
        previous = QToolButton(self.find_bar)
        previous.setText("Previous")
        previous.clicked.connect(self.find_previous)
        find_layout.addWidget(previous)
        following = QToolButton(self.find_bar)
        following.setText("Next")
        following.clicked.connect(self.find_next)
        find_layout.addWidget(following)
        close = QToolButton(self.find_bar)
        close.setText("Close")
        close.clicked.connect(self.hide_find)
        find_layout.addWidget(close)
        self.find_bar.hide()
        layout.addWidget(self.find_bar)

        self.browser = _ReadingBrowser(self)
        self.browser.setObjectName("markdownReadingBrowser")
        self.browser.setAccessibleName("Rendered Markdown reading view")
        self.browser.setOpenLinks(False)
        self.browser.setOpenExternalLinks(False)
        self.browser.setTextInteractionFlags(
            Qt.TextSelectableByMouse
            | Qt.TextSelectableByKeyboard
            | Qt.LinksAccessibleByMouse
            | Qt.LinksAccessibleByKeyboard
        )
        self.browser.anchorClicked.connect(
            lambda url: self.linkActivated.emit(url.toString())
        )
        layout.addWidget(self.browser, 1)
        self.setFocusProxy(self.browser)

        find_shortcut = QShortcut(QKeySequence.Find, self)
        find_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        find_shortcut.activated.connect(self.show_find)
        self._find_shortcut = find_shortcut
        close_find = QShortcut(QKeySequence(Qt.Key_Escape), self.find_bar)
        close_find.setContext(Qt.WidgetWithChildrenShortcut)
        close_find.activated.connect(self.hide_find)
        self._close_find_shortcut = close_find
        self._page_shortcuts = []
        page_sequences = [
            ("Ctrl+Shift+J", 1),
            ("Ctrl+Shift+K", -1),
        ]
        if sys.platform == "darwin":
            page_sequences.extend((("Meta+Shift+J", 1), ("Meta+Shift+K", -1)))
        for sequence, direction in page_sequences:
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            shortcut.activated.connect(
                lambda step=direction: self.page_scroll(step)
            )
            self._page_shortcuts.append(shortcut)
        self._apply_palette()

    def _apply_palette(self) -> None:
        palette = self.palette()
        paper = str(theme_value("markdown_preview.paper_bg", "#ffffff"))
        text = str(theme_value("markdown_preview.text", "#111111"))
        muted = str(theme_value("markdown_preview.muted", "#666666"))
        border = str(theme_value("markdown_preview.border", "#c6cbd1"))
        table_header = str(theme_value("markdown_preview.table_header_bg", "#e9edf2"))
        code_bg = str(theme_value("markdown_preview.code_bg", "#f6f8fa"))
        highlight = str(theme_value("markdown_preview.link", "#1a5fb4"))
        self._css = f"""
            html, body {{ color: {text}; background: {paper}; }}
            body {{ font-family: sans-serif; font-size: 12pt; line-height: 1.5;
                    margin: 30px 36px 42px 36px; }}
            h1, h2, h3, h4, h5, h6 {{ color: {text}; margin-top: 1.25em;
                   margin-bottom: 0.45em; font-weight: 700; }}
            h1 {{ font-size: 28pt; border-bottom: 2px solid {border}; padding-bottom: 6px; }}
            h2 {{ font-size: 22pt; border-bottom: 1px solid {border}; padding-bottom: 4px; }}
            h3 {{ font-size: 18pt; }} h4 {{ font-size: 15pt; }}
            h5 {{ font-size: 13pt; }} h6 {{ font-size: 12pt; color: {muted}; }}
            p {{ margin-top: 0.55em; margin-bottom: 0.55em; }}
            table {{ border-collapse: collapse; width: 100%; margin-top: 1em;
                     margin-bottom: 1em; border: 2px solid {border}; }}
            th, td {{ border: 1px solid {border}; padding: 9px 12px; text-align: left; }}
            th {{ background: {table_header}; color: {text}; font-weight: 700; }}
            blockquote {{ color: {muted}; border-left: 4px solid {border};
                          margin-left: 0; padding-left: 1em; }}
            code {{ background: {code_bg}; font-family: monospace; }}
            pre {{ background: {code_bg}; border: 1px solid {border}; padding: 10px; }}
            a {{ color: {highlight}; text-decoration: none; }}
            img {{ max-width: 100%; margin-top: 1em; margin-bottom: 1em; }}
            mark {{ color: {text}; background: transparent; text-decoration: underline; }}
            .md-checkbox {{ border: 1px solid {text}; padding-left: 0.55em;
                            padding-right: 0.15em; margin-right: 0.35em; }}
            .md-checkbox--checked {{ background: {highlight}; }}
        """
        self.setStyleSheet(
            "QFrame#markdownReadingHeader {"
            f"background: {palette.color(QPalette.AlternateBase).name()}; "
            f"color: {palette.color(QPalette.Text).name()}; "
            f"border-bottom: 1px solid {palette.color(QPalette.Mid).name()};"
            "} QTextBrowser#markdownReadingBrowser {"
            f"background: {paper}; color: {text}; border: none;"
            "}"
        )
        self.browser.refresh_reading_cursor()

    def set_vi_enabled(self, enabled: bool) -> None:
        self.browser.set_vi_state(enabled, self.browser.vi_cursor_style)

    def set_vi_cursor_style(self, style: str) -> None:
        self.browser.set_vi_state(self.browser.vi_enabled, style)

    def page_scroll(self, direction: int) -> None:
        self.browser.page_scroll(direction)

    def set_markdown(
        self,
        markdown_text: str,
        *,
        base_directory: str | Path | None = None,
        source_line: int = 1,
    ) -> None:
        self.last_markdown = markdown_text or ""
        self.base_directory = Path(base_directory).resolve() if base_directory else None
        if self.base_directory is not None:
            base_url = QUrl.fromLocalFile(str(self.base_directory) + os.sep)
            self.browser.document().setBaseUrl(base_url)
        else:
            self.browser.document().setBaseUrl(QUrl())
        body = render_markdown_html(self.last_markdown, source_anchors=True)
        self.last_html = f"<html><head><style>{self._css}</style></head><body>{body}</body></html>"
        self.browser.setHtml(self.last_html)
        self.scroll_to_source_line(source_line)

    def scroll_to_source_line(self, source_line: int) -> None:
        anchor = nearest_source_anchor(self.last_markdown, source_line)
        if anchor:
            def scroll_and_mark(value=anchor):
                self.browser.scrollToAnchor(value)
                self.browser._sync_cursor_to_viewport()

            QTimer.singleShot(0, self.browser, scroll_and_mark)

    def show_find(self) -> None:
        self.find_bar.show()
        self.find_query.setFocus(Qt.ShortcutFocusReason)
        self.find_query.selectAll()

    def hide_find(self) -> None:
        self.find_bar.hide()
        self.browser.setFocus(Qt.ShortcutFocusReason)

    def _find(self, *, backward: bool) -> None:
        query = self.find_query.text()
        if not query:
            return
        flags = QTextDocument.FindBackward if backward else QTextDocument.FindFlag(0)
        if not self.browser.find(query, flags):
            cursor = self.browser.textCursor()
            cursor.movePosition(QTextCursor.End if backward else QTextCursor.Start)
            self.browser.setTextCursor(cursor)
            self.browser.find(query, flags)

    def find_next(self) -> None:
        self._find(backward=False)

    def find_previous(self) -> None:
        self._find(backward=True)

    def focus_reader(self) -> None:
        self.browser.setFocus(Qt.ShortcutFocusReason)

    def zoom_in(self, steps: int = 1) -> None:
        self.browser.zoomIn(max(1, int(steps)))

    def zoom_out(self, steps: int = 1) -> None:
        self.browser.zoomOut(max(1, int(steps)))
