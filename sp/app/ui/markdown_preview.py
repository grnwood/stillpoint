"""Token-free, in-process reading view for Markdown editors."""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QTimer, Qt, QUrl, Signal
from PySide6.QtGui import (
    QKeyEvent,
    QKeySequence,
    QPalette,
    QShortcut,
    QTextCursor,
    QTextDocument,
)
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from sp.markdown_html import nearest_source_anchor, render_markdown_html


class _ReadingBrowser(QTextBrowser):
    """Selectable HTML reader with optional vi-style scrolling."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.vi_enabled = False
        self._pending_g = False

    def keyPressEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        modifiers = event.modifiers() & ~Qt.KeypadModifier
        if (
            self.vi_enabled
            and modifiers == Qt.ShiftModifier
            and event.key() == Qt.Key_G
        ):
            self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())
            self._pending_g = False
            event.accept()
            return
        if self.vi_enabled and modifiers == Qt.NoModifier:
            scrollbar = self.verticalScrollBar()
            key = event.key()
            if key in (Qt.Key_J, Qt.Key_K):
                direction = 1 if key == Qt.Key_J else -1
                scrollbar.setValue(scrollbar.value() + direction * max(24, scrollbar.singleStep() * 3))
                self._pending_g = False
                event.accept()
                return
            if key == Qt.Key_G:
                if self._pending_g:
                    scrollbar.setValue(scrollbar.minimum())
                    self._pending_g = False
                else:
                    self._pending_g = True
                event.accept()
                return
            self._pending_g = False
        if self.vi_enabled and modifiers == Qt.ControlModifier and event.key() in (Qt.Key_D, Qt.Key_U):
            scrollbar = self.verticalScrollBar()
            direction = 1 if event.key() == Qt.Key_D else -1
            scrollbar.setValue(scrollbar.value() + direction * max(1, scrollbar.pageStep() // 2))
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
        self._apply_palette()

    def _apply_palette(self) -> None:
        palette = self.palette()
        base = palette.color(QPalette.Base).name()
        alternate = palette.color(QPalette.AlternateBase).name()
        text = palette.color(QPalette.Text).name()
        muted = palette.color(QPalette.PlaceholderText).name()
        border = palette.color(QPalette.Mid).name()
        highlight = palette.color(QPalette.Highlight).name()
        self._css = f"""
            body {{ color: {text}; background: {base}; font-family: sans-serif;
                   font-size: 12pt; line-height: 1.45; margin: 24px; }}
            h1, h2, h3, h4, h5, h6 {{ color: {text}; margin-top: 1.25em;
                   margin-bottom: 0.45em; font-weight: 600; }}
            h1 {{ font-size: 2em; }} h2 {{ font-size: 1.6em; }}
            h3 {{ font-size: 1.3em; }} h4 {{ font-size: 1.1em; }}
            p {{ margin-top: 0.55em; margin-bottom: 0.55em; }}
            table {{ border-collapse: collapse; width: 100%; margin-top: 1em;
                     margin-bottom: 1em; }}
            th, td {{ border: 1px solid {border}; padding: 7px 10px; text-align: left; }}
            th {{ background: {alternate}; color: {text}; font-weight: 600; }}
            blockquote {{ color: {muted}; border-left: 4px solid {border};
                          margin-left: 0; padding-left: 1em; }}
            code {{ background: {alternate}; font-family: monospace; }}
            pre {{ background: {alternate}; border: 1px solid {border}; padding: 10px; }}
            a {{ color: {highlight}; text-decoration: none; }}
            img {{ max-width: 100%; margin-top: 1em; margin-bottom: 1em; }}
            mark {{ color: {text}; background: transparent; text-decoration: underline; }}
            .md-checkbox {{ border: 1px solid {text}; padding-left: 0.55em;
                            padding-right: 0.15em; margin-right: 0.35em; }}
            .md-checkbox--checked {{ background: {highlight}; }}
        """
        self.setStyleSheet(
            "QFrame#markdownReadingHeader {"
            f"background: {alternate}; border-bottom: 1px solid {border};"
            "}"
        )

    def set_vi_enabled(self, enabled: bool) -> None:
        self.browser.vi_enabled = bool(enabled)

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
            QTimer.singleShot(0, self.browser, lambda value=anchor: self.browser.scrollToAnchor(value))

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
