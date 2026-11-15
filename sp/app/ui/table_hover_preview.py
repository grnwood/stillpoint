"""Optional table preview controller; never edits the attached document."""
from PySide6.QtCore import QEvent, QObject, QPoint, Qt, QTimer
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QToolButton, QVBoxLayout

from .reading_options import reading_source_blocks
from .screen_positioning import clamp_popup_top_left, popup_available_geometry


class TableHoverPreview(QObject):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor
        self.popup = None
        self._source = None
        self._code_revision = None
        self._code_ranges = []
        self.button = QToolButton(editor.viewport())
        self.button.setText("Preview table")
        self.button.setAccessibleName("Preview table")
        self.button.setToolTip("Open a read-only table preview")
        self.button.setFocusPolicy(Qt.NoFocus)
        self.button.setCursor(Qt.PointingHandCursor)
        self.button.setStyleSheet(
            "QToolButton { background: #245a81; color: white; border: 1px solid #8bb7d8;"
            " border-radius: 5px; padding: 4px 8px; }"
            "QToolButton:hover { background: #326f9c; }"
        )
        self.button.adjustSize()
        self.button.hide()
        self.button.clicked.connect(self.open_preview)
        self._leave_timer = QTimer(self)
        self._leave_timer.setSingleShot(True)
        self._leave_timer.setInterval(150)
        self._leave_timer.timeout.connect(self._hide_unless_hovered)
        editor.viewport().setMouseTracking(True)
        editor.viewport().installEventFilter(self)
        editor.verticalScrollBar().valueChanged.connect(self.hide)
        editor.horizontalScrollBar().valueChanged.connect(self.hide)
        editor.textChanged.connect(self._document_changed)

    def hide(self, *_args):
        self.button.hide()
        self._source = None

    def _hide_unless_hovered(self):
        if not self.button.underMouse():
            self.hide()

    def _document_changed(self):
        self.hide()
        if self.popup is not None:
            self.popup.close()

    def source_at(self, block):
        if not block.isValid() or '|' not in block.text():
            return None
        document = self.editor.document()
        if document.revision() != self._code_revision:
            self._code_ranges = [(source.line, source.end_line)
                                 for source in reading_source_blocks(document.toPlainText())
                                 if source.kind == 'code']
            self._code_revision = document.revision()
        if any(start <= block.blockNumber() + 1 <= end for start, end in self._code_ranges):
            return None
        first = block
        # Bound hover work even for unusually large documents.
        for _ in range(500):
            previous = first.previous()
            if not previous.isValid() or '|' not in previous.text() or not previous.text().strip():
                break
            first = previous
        lines = []
        current = first
        for _ in range(500):
            if not current.isValid() or '|' not in current.text() or not current.text().strip():
                break
            lines.append(current.text())
            current = current.next()
        relative_line = block.blockNumber() - first.blockNumber() + 1
        for source in reading_source_blocks('\n'.join(lines)):
            if source.kind == 'table' and source.line <= relative_line <= source.end_line:
                return source.markdown, first.blockNumber() + source.line - 1
        return None

    def show_at(self, point):
        if self.editor.textCursor().hasSelection():
            self.hide()
            return
        block = self.editor.cursorForPosition(point).block()
        source = self.source_at(block)
        if source is None:
            self.hide()
            return
        self._source, first_line = source
        header = self.editor.document().findBlockByNumber(first_line)
        rect = self.editor.cursorRect(QTextCursor(header))
        viewport = self.editor.viewport()
        x = max(0, viewport.width() - self.button.width() - 12)
        y = max(4, min(rect.top() - self.button.height() - 4,
                       viewport.height() - self.button.height() - 4))
        self.button.move(x, y)
        self.button.show()
        self.button.raise_()

    def eventFilter(self, obj, event):
        if event.type() == QEvent.MouseMove:
            if not event.buttons():
                self._leave_timer.stop()
                if (self.button.isVisible()
                        and self.button.geometry().adjusted(-12, -12, 12, 12).contains(event.position().toPoint())):
                    return False
                self.show_at(event.position().toPoint())
            else:
                self.hide()
        elif event.type() == QEvent.Leave:
            self._leave_timer.start()
        elif event.type() in (QEvent.Hide, QEvent.Resize, QEvent.MouseButtonPress):
            self.hide()
        return False

    def open_preview(self):
        if not self._source:
            return
        from .markdown_preview import MarkdownReadingView

        if self.popup is not None:
            self.popup.close()
        popup = QDialog(self.editor, Qt.Popup)
        popup.setAttribute(Qt.WA_DeleteOnClose, True)
        popup.setWindowTitle("Table preview")
        layout = QVBoxLayout(popup)
        header = QHBoxLayout()
        header.addWidget(QLabel("Table preview"))
        header.addStretch()
        close = QToolButton(popup)
        close.setText("Close")
        close.clicked.connect(popup.close)
        header.addWidget(close)
        layout.addLayout(header)
        reader = MarkdownReadingView(popup)
        reader.set_header_visible(False)
        reader.set_theme_palette(self.editor.palette())
        reader.set_markdown(self._source)
        layout.addWidget(reader)
        bounds = popup_available_geometry(parent=self.editor)
        popup.resize(min(900, bounds.width() - 40), min(500, bounds.height() - 40))
        center = self.editor.viewport().mapToGlobal(self.editor.viewport().rect().center())
        popup.move(clamp_popup_top_left(center - QPoint(popup.width() // 2, popup.height() // 2), popup.size(), bounds))
        def finished(*_args):
            if self.popup is popup:
                self.popup = None
            self.editor.setFocus(Qt.OtherFocusReason)

        popup.finished.connect(finished)
        self.popup = popup
        popup.show()
        reader.browser.setFocus()
