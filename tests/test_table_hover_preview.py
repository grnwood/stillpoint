from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest

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
    assert controller.button.isVisible()
    controller.button.click()
    assert controller.popup.isVisible()
    reader = controller.popup.findChild(MarkdownReadingView)
    assert '<table>' in reader.last_html
    assert reader._copy_buttons
    controller.popup.close()
    assert editor.toPlainText() == before
    assert editor.document().isModified() == modified


def test_hover_preview_preserves_document_cursor_and_undo(qtbot):
    editor = MarkdownEditor()
    qtbot.addWidget(editor)
    editor.resize(800, 500)
    editor.set_markdown('Before\n\n' + TABLE + '\n\nAfter')
    editor.show()
    QTest.qWait(30)
    controller = TableHoverPreview(editor)
    before = editor.toPlainText()
    revision = editor.document().revision()
    position = editor.textCursor().position()
    modified = editor.document().isModified()
    block = editor.document().findBlockByNumber(4)
    point = editor.cursorRect(QTextCursor(block)).center()
    controller.show_at(point)
    assert controller.button.isVisible()
    first_position = controller.button.pos()
    controller.show_at(editor.cursorRect(QTextCursor(block.next())).center())
    assert controller.button.pos() == first_position
    controller.button.click()
    popup = controller.popup
    assert popup is not None and popup.isVisible()
    reader = popup.findChild(MarkdownReadingView)
    assert '<table>' in reader.last_html
    assert len(reader._copy_buttons) == 1
    assert editor.toPlainText() == before
    assert editor.document().revision() == revision
    assert editor.document().isModified() == modified
    assert editor.textCursor().position() == position
    QTest.keyClick(reader.browser, Qt.Key_Escape)
    QTest.qWait(20)
    assert controller.popup is None
    assert editor.toPlainText() == before


def test_hover_ignores_prose_code_and_selection_and_closes_on_edit(qtbot):
    editor = MarkdownEditor()
    qtbot.addWidget(editor)
    editor.resize(800, 500)
    editor.set_markdown('Prose | not a table\n\n```text\n' + TABLE + '\n```\n\n' + TABLE)
    editor.show()
    QTest.qWait(30)
    controller = TableHoverPreview(editor)
    assert controller.source_at(editor.document().begin()) is None
    assert controller.source_at(editor.document().findBlockByNumber(3)) is None
    block = editor.document().findBlockByNumber(9)
    assert controller.source_at(block) is not None
    point = editor.cursorRect(QTextCursor(block)).center()
    editor.selectAll()
    controller.show_at(point)
    assert controller.button.isHidden()
    cursor = editor.textCursor()
    cursor.clearSelection()
    editor.setTextCursor(cursor)
    controller.show_at(point)
    controller.open_preview()
    assert controller.popup is not None
    editor.insertPlainText('edit')
    assert controller.popup is None
    assert controller.button.isHidden()
