import pytest
from PySide6.QtCore import QMimeData
from PySide6.QtGui import QFont, QTextCharFormat, QTextCursor
from PySide6.QtTest import QTest

from sp.app.ui.markdown_editor import MarkdownEditor


def format_at(block, index):
    offset = len(block.text()[:index].encode('utf-16-le')) // 2
    return next(QTextCharFormat(span.format) for span in block.layout().formats()
                if span.start <= offset < span.start + span.length)


@pytest.mark.parametrize('level', [1, 2, 5])
@pytest.mark.parametrize('marker', ['**', '*', '***'])
@pytest.mark.parametrize('route', ['paste', 'load'])
def test_heading_emphasis_hides_markers_without_losing_heading_typography(qtbot, level, marker, route):
    editor = MarkdownEditor()
    qtbot.addWidget(editor)
    editor.show()
    source = '#' * level + f' Intro {marker}🚀 Architecture Review{marker} suffix\n\nBody text.'
    if route == 'paste':
        mime = QMimeData()
        mime.setText(source)
        editor.insertFromMimeData(mime)
    else:
        editor.set_markdown(source)
    QTest.qWait(40)
    block = editor.document().firstBlock()
    text = block.text()
    base = editor.highlighter.heading_styles[level - 1]
    for index in (text.index(marker), text.rindex(marker)):
        hidden = format_at(block, index)
        assert hidden.foreground().color().alpha() == 0
        assert hidden.fontPointSize() <= 1
    content = format_at(block, text.index('Architecture'))
    assert content.fontPointSize() == base.fontPointSize()
    assert content.foreground().color() == base.foreground().color()
    assert content.fontItalic() == (len(marker) in {1, 3})
    if len(marker) in {2, 3}:
        assert content.fontWeight() == QFont.Weight.Bold
    trailing = format_at(block, len(text) - 1)
    assert trailing.fontPointSize() == base.fontPointSize()
    assert trailing.foreground().color() == base.foreground().color()
    assert editor.to_markdown().strip() == source


def test_folder_navigator_pasted_bold_headings_match_main_editor(qtbot, tmp_path):
    from sp.app.folder_navigator.core import read_text
    from sp.app.folder_navigator.window import Tab
    from PySide6.QtWidgets import QApplication

    path = tmp_path / 'review.md'
    path.write_text('')
    tab = Tab(path, read_text(path), markdown=True, root=tmp_path)
    qtbot.addWidget(tab)
    tab.resize(900, 600)
    tab.show()
    source = '# **Architecture Review: Review Decision Framework**\n\n' \
             '## **1. What changed architecturally**\n\n' \
             '**Business processes own the work.**'
    mime = QMimeData()
    mime.setText(source)
    tab.editor.insertFromMimeData(mime)
    QTest.qWait(40)
    for block_number in (0, 2):
        block = tab.editor.document().findBlockByNumber(block_number)
        for marker in (block.text().index('**'), block.text().rindex('**')):
            assert format_at(block, marker).foreground().color().alpha() == 0
    assert tab.editor.to_markdown().strip() == source
    cursor = tab.editor.textCursor()
    cursor.select(QTextCursor.Document)
    tab.editor.setTextCursor(cursor)
    tab.editor.copy()
    copied = QApplication.clipboard().mimeData()
    assert '**' not in copied.text()
    assert '<h1><strong>Architecture Review:' in copied.html()
