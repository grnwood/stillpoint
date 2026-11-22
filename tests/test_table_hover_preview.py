from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QTextBrowser, QToolButton

from sp.app.ui.markdown_editor import MarkdownEditor
from sp.app.ui.table_hover_preview import TableHoverPreview
from sp.app.ui.markdown_preview import MarkdownReadingView


TABLE = '| Name | Value |\n| --- | --- |\n| A | 1 |\n| B | 2 |'


def test_folder_navigator_markdown_editor_has_table_preview(qtbot, tmp_path):
    from sp.app.folder_navigator.core import read_text
    from sp.app.folder_navigator.window import Tab

    path = tmp_path / 'table.md'
    path.write_text(TABLE)
    tab = Tab(path, read_text(path), markdown=True, root=tmp_path)
    qtbot.addWidget(tab)
    tab.resize(800, 500)
    tab.show()
    QTest.qWait(50)
    editor = tab.editor
    controller = editor._table_hover_preview
    before = editor.toPlainText()
    modified = editor.document().isModified()
    block = editor.document().findBlockByNumber(2)
    controller.show_at(editor.cursorRect(QTextCursor(block)).center())
    assert controller._hover_timer.isActive()
    QTest.qWait(1100)
    assert controller.popup.isVisible()
    browser = controller.popup.findChild(QTextBrowser, "tableHoverPreviewContent")
    assert '<table' in browser.toHtml()
    assert not controller.popup.findChildren(QToolButton)
    controller.popup.close()
    assert editor.toPlainText() == before
    assert editor.document().isModified() == modified


def test_hover_preview_preserves_document_cursor_and_undo(qtbot):
    editor = MarkdownEditor()
    qtbot.addWidget(editor)
    editor.resize(800, 500)
    editor.set_markdown('Before\n\n' + TABLE + '\n\nAfter')
    editor.show()
    QTest.qWait(180)
    controller = TableHoverPreview(editor)
    before = editor.toPlainText()
    revision = editor.document().revision()
    position = editor.textCursor().position()
    modified = editor.document().isModified()
    block = editor.document().findBlockByNumber(4)
    point = editor.cursorRect(QTextCursor(block)).center()
    controller.show_at(point)
    assert controller._hover_timer.isActive()
    assert controller.popup is None
    controller.show_at(editor.cursorRect(QTextCursor(block.next())).center())
    QTest.qWait(1100)
    popup = controller.popup
    assert popup is not None and popup.isVisible()
    browser = popup.findChild(QTextBrowser, "tableHoverPreviewContent")
    assert '<table' in browser.toHtml()
    assert not popup.findChildren(QToolButton)
    assert editor.toPlainText() == before
    assert editor.document().revision() == revision
    assert editor.document().isModified() == modified
    assert editor.textCursor().position() == position
    QTest.keyClick(browser, Qt.Key_Escape)
    QTest.qWait(20)
    assert controller.popup is None
    assert editor.toPlainText() == before


def test_hover_ignores_prose_code_and_selection_and_closes_on_edit(qtbot):
    editor = MarkdownEditor()
    qtbot.addWidget(editor)
    editor.resize(800, 500)
    editor.set_markdown('Prose | not a table\n\n```text\n' + TABLE + '\n```\n\n' + TABLE)
    editor.show()
    QTest.qWait(180)
    controller = TableHoverPreview(editor)
    assert controller.source_at(editor.document().begin()) is None
    assert controller.source_at(editor.document().findBlockByNumber(3)) is None
    block = editor.document().findBlockByNumber(9)
    assert controller.source_at(block) is not None
    point = editor.cursorRect(QTextCursor(block)).center()
    editor.selectAll()
    controller.show_at(point)
    assert not controller._hover_timer.isActive()
    cursor = editor.textCursor()
    cursor.clearSelection()
    editor.setTextCursor(cursor)
    controller.show_at(point)
    controller.open_preview()
    assert controller.popup is not None
    editor.insertPlainText('edit')
    assert controller.popup is None
    assert not controller._hover_timer.isActive()


def test_reader_table_hover_delay_and_mouse_dismiss(qtbot):
    view = MarkdownReadingView()
    qtbot.addWidget(view)
    view.resize(800, 500)
    view.set_markdown('Before\n\n' + TABLE + '\n\nAfter')
    view.show()
    QTest.qWait(40)
    controller = view._table_hover_preview
    position = view._source_line_positions[3]
    cursor = QTextCursor(view.browser.document())
    cursor.setPosition(position)
    point = view.browser.cursorRect(cursor).center()
    assert view._table_source_at_point(point)[0].strip() == TABLE
    QTest.mouseMove(view.browser.viewport(), point)
    assert controller._hover_timer.isActive()
    QTest.qWait(500)
    assert controller.popup is None
    QTest.qWait(350)
    popup = controller.popup
    assert popup is not None and popup.isVisible()
    browser = popup.findChild(QTextBrowser, "tableHoverPreviewContent")
    assert not popup.findChildren(QToolButton)
    assert view._copy_buttons
    assert popup.width() < 500
    assert popup.height() < 250
    QTest.mouseMove(browser.viewport(), QPoint(30, 30))
    QTest.qWait(30)
    assert controller.popup is None
    QTest.qWait(1100)
    assert controller.popup is None
    controller.show_at(point)
    QTest.qWait(1100)
    assert controller.popup is not None
    QTest.keyClick(controller.popup.findChild(QTextBrowser, "tableHoverPreviewContent"), Qt.Key_Escape)
    assert controller.popup is None


def test_reader_leaving_table_or_selecting_cancels_hover(qtbot):
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QApplication, QToolButton

    view = MarkdownReadingView()
    qtbot.addWidget(view)
    view.resize(800, 500)
    view.set_markdown(TABLE)
    view.show()
    QTest.qWait(40)
    controller = view._table_hover_preview
    cursor = QTextCursor(view.browser.document())
    cursor.setPosition(view._source_line_positions[1])
    point = view.browser.cursorRect(cursor).center()
    assert not any(button.text() == 'Preview table' for button in view.findChildren(QToolButton))
    controller.show_at(point)
    QApplication.sendEvent(view.browser.viewport(), QEvent(QEvent.Leave))
    QTest.qWait(1100)
    assert controller.popup is None
    controller.show_at(point)
    view.browser.selectAll()
    QTest.qWait(1100)
    assert controller.popup is None
    cursor.clearSelection()
    view.browser.setTextCursor(cursor)
    controller.show_at(point)
    view.hide()
    QTest.qWait(1100)
    assert controller.popup is None
