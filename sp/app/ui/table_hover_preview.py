"""Optional table preview controller; never edits the attached document."""
from PySide6.QtCore import QEvent, QObject, QPoint, Qt, QTimer
from math import ceil

from PySide6.QtGui import QCursor, QFontMetricsF, QTextBlockFormat, QTextCursor, QTextLength, QTextTable
from PySide6.QtWidgets import QApplication, QDialog, QFrame, QTextBrowser, QVBoxLayout
from shiboken6 import isValid

from .reading_options import reading_source_blocks
from .screen_positioning import clamp_popup_top_left, popup_available_geometry


class TableHoverPreview(QObject):
    def __init__(self, editor, *, source_at_point=None):
        super().__init__(editor)
        self.editor = editor
        self._viewport = editor.viewport()
        self.popup = None
        self._source = None
        self._source_at_point = source_at_point
        self._code_revision = None
        self._code_ranges = []
        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.setInterval(750)
        self._hover_timer.setTimerType(Qt.PreciseTimer)
        self._hover_timer.timeout.connect(self.open_preview)
        editor.viewport().setMouseTracking(True)
        editor.viewport().installEventFilter(self)
        editor.verticalScrollBar().valueChanged.connect(self.hide)
        editor.horizontalScrollBar().valueChanged.connect(self.hide)
        editor.document().contentsChange.connect(self._document_changed)
        editor.selectionChanged.connect(self.hide)

    def hide(self, *_args):
        self._hover_timer.stop()
        self._source = None
        if self.popup is not None:
            self.popup.close()

    def _document_changed(self, _position, removed, added):
        if removed or added:
            self.hide()

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
        if self.editor.textCursor().hasSelection() or self.popup is not None:
            self._hover_timer.stop()
            return
        if self._source_at_point is not None:
            source = self._source_at_point(point)
        else:
            source = self.source_at(self.editor.cursorForPosition(point).block())
        if source is None:
            self.hide()
            return
        self._source = source[0]
        self._hover_timer.start()

    def eventFilter(self, obj, event):
        kind = event.type()
        if self.popup is not None:
            if kind == QEvent.MouseMove:
                if event.globalPosition().toPoint() != self._popup_pointer:
                    self.hide()
                    return True
            elif kind in (QEvent.KeyPress, QEvent.ShortcutOverride) and event.key() == Qt.Key_Escape:
                if kind == QEvent.KeyPress:
                    self.hide()
                event.accept()
                return True
        if obj is self._viewport:
            if kind == QEvent.MouseMove:
                if not event.buttons():
                    self.show_at(event.position().toPoint())
                else:
                    self.hide()
            elif kind == QEvent.Leave:
                if self.popup is None:
                    self.hide()
            elif kind in (QEvent.Hide, QEvent.Resize, QEvent.MouseButtonPress, QEvent.Wheel):
                self.hide()
        return False

    def open_preview(self):
        self._hover_timer.stop()
        if not self._source or not self.editor.isVisible() or self.editor.textCursor().hasSelection():
            return
        from .markdown_preview import MarkdownReadingView

        if self.popup is not None:
            self.popup.close()
        popup = QDialog(self.editor, Qt.Popup)
        popup.setAttribute(Qt.WA_DeleteOnClose, True)
        popup.setWindowTitle("Table preview")
        layout = QVBoxLayout(popup)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(0)
        # Use the shared renderer for theme colors and row shading, then keep
        # only the table in a plain browser without reader controls or gutters.
        renderer = MarkdownReadingView(popup, enable_table_preview=False)
        renderer.set_theme_palette(self.editor.palette())
        renderer.set_markdown(self._source)
        table = next(frame for frame in renderer.browser.document().rootFrame().childFrames()
                     if isinstance(frame, QTextTable))
        cursor = QTextCursor(renderer.browser.document())
        cursor.setPosition(table.firstPosition() - 1)
        cursor.setPosition(table.lastPosition() + 1, QTextCursor.KeepAnchor)
        browser = QTextBrowser(popup)
        browser.setObjectName("tableHoverPreviewContent")
        browser.setAccessibleName("Read-only table preview")
        browser.setFrameShape(QFrame.NoFrame)
        browser.setPalette(renderer.browser.palette())
        paper = renderer._paper_color.name()
        text = renderer._text_color.name()
        popup.setStyleSheet(f"QDialog {{ background: {paper}; }}")
        browser.setStyleSheet(f"QTextBrowser {{ background: {paper}; color: {text}; border: none; }}")
        browser.setFont(renderer.browser.font())
        browser.setHtml(cursor.selection().toHtml())
        browser.setTextInteractionFlags(Qt.NoTextInteraction)
        browser.setContextMenuPolicy(Qt.NoContextMenu)
        browser.viewport().setMouseTracking(True)
        document = browser.document()
        document.setDocumentMargin(4)
        for frame in document.rootFrame().childFrames():
            fmt = frame.frameFormat()
            fmt.setMargin(0)
            fmt.setWidth(QTextLength(QTextLength.VariableLength, 0))
            frame.setFrameFormat(fmt)
            if isinstance(frame, QTextTable):
                widths = []
                for column in range(frame.columns()):
                    width = 0
                    for row in range(frame.rows()):
                        cell = frame.cellAt(row, column)
                        cell_width = 0
                        block = cell.firstCursorPosition().block()
                        last = cell.lastCursorPosition().block()
                        while block.isValid():
                            block_width = 0
                            fragments = block.begin()
                            while not fragments.atEnd():
                                fragment = fragments.fragment()
                                font = fragment.charFormat().font().resolve(document.defaultFont())
                                block_width += QFontMetricsF(font).horizontalAdvance(fragment.text())
                                fragments += 1
                            cell_width = max(cell_width, block_width)
                            if block == last:
                                break
                            block = block.next()
                        cell_format = cell.format().toTableCellFormat()
                        padding = cell_format.leftPadding() + cell_format.rightPadding()
                        width = max(width, cell_width + max(padding, 2 * frame.format().cellPadding()) + 4)
                    widths.append(QTextLength(QTextLength.FixedLength, ceil(width)))
                fmt = frame.format()
                fmt.setColumnWidthConstraints(widths)
                frame.setFormat(fmt)
        # Qt requires an empty paragraph before and after a table. Collapse
        # those structural paragraphs rather than displaying blank text rows.
        for block in (document.begin(), document.end().previous()):
            if block.isValid() and not block.text().strip() and QTextCursor(block).currentTable() is None:
                cursor = QTextCursor(block)
                fmt = block.blockFormat()
                fmt.setTopMargin(0)
                fmt.setBottomMargin(0)
                fmt.setLineHeight(1, QTextBlockFormat.FixedHeight.value)
                cursor.setBlockFormat(fmt)
        renderer.deleteLater()
        layout.addWidget(browser)
        bounds = popup_available_geometry(parent=self.editor)
        max_width = min(1100, bounds.width() - 32)
        max_height = min(700, bounds.height() - 32)
        document.setTextWidth(-1)
        table = next(frame for frame in document.rootFrame().childFrames() if isinstance(frame, QTextTable))
        table_rect = document.documentLayout().frameBoundingRect(table)
        width = min(max_width, max(100, ceil(max(document.idealWidth(), table_rect.right())) + 12))
        document.setTextWidth(width - 4)
        table_rect = document.documentLayout().frameBoundingRect(table)
        height = min(max_height, ceil(max(document.size().height(), table_rect.bottom())) + 12)
        if document.idealWidth() > width - 4:
            height = min(max_height, height + browser.horizontalScrollBar().sizeHint().height())
        if document.size().height() > max_height - 4:
            width = min(max_width, width + browser.verticalScrollBar().sizeHint().width())
        browser.setHorizontalScrollBarPolicy(
            Qt.ScrollBarAlwaysOff if width < max_width else Qt.ScrollBarAsNeeded
        )
        browser.setVerticalScrollBarPolicy(
            Qt.ScrollBarAlwaysOff if height < max_height else Qt.ScrollBarAsNeeded
        )
        popup.resize(width, height)
        center = self.editor.viewport().mapToGlobal(self.editor.viewport().rect().center())
        popup.move(clamp_popup_top_left(center - QPoint(popup.width() // 2, popup.height() // 2), popup.size(), bounds))
        def finished(*_args):
            QApplication.instance().removeEventFilter(self)
            if self.popup is popup:
                self.popup = None
            self._hover_timer.stop()
            self._source = None
            if isValid(self.editor) and self.editor.isVisible():
                self.editor.setFocus(Qt.OtherFocusReason)

        popup.finished.connect(finished)
        popup.setMouseTracking(True)
        browser.setMouseTracking(True)
        self.popup = popup
        self._popup_pointer = QCursor.pos()
        QApplication.instance().installEventFilter(self)
        popup.show()
        browser.setFocus()
