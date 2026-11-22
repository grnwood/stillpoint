from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor, QTextDocument
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QTextEdit

from sp.app.ui.markdown_clipboard import INTERNAL_MARKDOWN, clipboard_from_html, clipboard_from_markdown
from sp.app.ui.markdown_editor import MarkdownEditor
from sp.app.ui.markdown_preview import MarkdownReadingView
from sp.markdown_html import render_markdown_html

SOURCE = '''## Project update

Some **bold**, *italic*, and [website](https://example.com/report).

- First item
- Second item

| Item | Qty |
| --- | ---: |
| Alpha | 2 |

```python
if ready:
    print("<value> & done")
```
'''


def select_all(widget):
    cursor = widget.textCursor()
    cursor.select(QTextCursor.Document)
    widget.setTextCursor(cursor)


def assert_formatted_payload(mime, markdown):
    assert mime.hasHtml()
    assert mime.hasText()
    assert mime.hasFormat(INTERNAL_MARKDOWN)
    assert bytes(mime.data(INTERNAL_MARKDOWN)).decode() == markdown
    html = mime.html()
    for tag in ("<h2>", "<strong>", "<em>", "<ul>", "<table", "<pre>", "<code>"):
        assert tag in html
    assert 'href="https://example.com/report"' in html
    for unwanted in ("font-size", "font-family", "background", "color:", "sp-source", "qrichtext", "-qt-", 'class=', 'id='):
        assert unwanted not in html
    plain = mime.text()
    assert "Some bold, italic, and website." in plain
    assert "• First item" in plain
    assert "Item\tQty" in plain
    assert "Alpha\t2" in plain
    assert '    print("<value> & done")' in plain
    assert "**" not in plain and "```" not in plain and "##" not in plain
    assert "\u200b" not in plain

    # Exercise the HTML part as a rich-text recipient would, independently of
    # StillPoint's preferred internal Markdown MIME type.
    document = QTextDocument()
    document.setHtml(html)
    assert document.find("bold").charFormat().fontWeight() >= 600
    assert document.find("italic").charFormat().fontItalic()
    assert document.find("First item").block().textList() is not None
    assert document.find("Project update").blockFormat().headingLevel() == 2
    assert len(document.rootFrame().childFrames()) == 1


def test_shared_clipboard_formats_are_readable_and_theme_free(qapp):
    assert_formatted_payload(clipboard_from_markdown(SOURCE), SOURCE)
    plain = clipboard_from_markdown(SOURCE, mode="plain")
    assert not plain.hasHtml()
    assert not plain.hasFormat(INTERNAL_MARKDOWN)
    assert plain.text() == clipboard_from_markdown(SOURCE).text()
    markdown = clipboard_from_markdown(SOURCE, mode="markdown")
    assert markdown.text() == SOURCE
    assert not markdown.hasHtml()
    assert markdown.hasFormat("text/markdown")


def test_qt_html_is_converted_to_semantic_formatting(qapp):
    mime = clipboard_from_html('''<html><head><style>body { background: #320a18; }</style></head>
<body style="color:#f4f7fb;font-size:30pt"><p id="sp-source-1">
<span style="font-weight:700; color:#fff; font-size:40pt">Bold</span> and
<span style="font-style:italic; background:#320a18">italic</span>
<a name="sp-source-2">&#8203;</a></p></body></html>''')
    assert "<strong>Bold</strong>" in mime.html()
    assert "<em>italic</em>" in mime.html()
    for value in ("#320a18", "#f4f7fb", "30pt", "40pt", "sp-source", "style="):
        assert value not in mime.html()
    assert mime.text().split() == ["Bold", "and", "italic"]
    markdown = bytes(mime.data(INTERNAL_MARKDOWN)).decode()
    assert "**Bold**" in markdown
    assert "*italic*" in markdown


@pytest.mark.parametrize("native", [False, True])
def test_editor_and_reader_copy_share_payloads_and_internal_roundtrip(qtbot, qapp, native):
    editor = MarkdownEditor()
    qtbot.addWidget(editor)
    editor.set_markdown(SOURCE)
    editor.show()
    QTest.qWait(30)
    source = editor.to_markdown()
    reader = MarkdownReadingView()
    qtbot.addWidget(reader)
    reader.set_markdown(source)
    reader.show()
    payloads = []
    for widget in (editor, reader.browser):
        select_all(widget)
        if native:
            # Native QTextEdit::copy bypasses Python's copy method, but must
            # still call the shared createMimeDataFromSelection override.
            QTextEdit.copy(widget)
        else:
            widget.copy()
        mime = QApplication.clipboard().mimeData()
        assert_formatted_payload(mime, source)
        payloads.append((mime.html(), mime.text()))
        target = MarkdownEditor()
        qtbot.addWidget(target)
        target.insertFromMimeData(mime)
        assert render_markdown_html(target.to_markdown().strip()) == render_markdown_html(source.strip())
    assert payloads[0] == payloads[1]


@pytest.mark.parametrize("kind", ["editor", "reader"])
def test_partial_copy_preserves_emphasis_without_extra_selected_text(qtbot, qapp, kind):
    if kind == "editor":
        widget = MarkdownEditor()
        qtbot.addWidget(widget)
        widget.set_markdown("Before **bold** after.")
    else:
        view = MarkdownReadingView()
        qtbot.addWidget(view)
        view.set_markdown("Before **bold** after.")
        widget = view.browser
    widget.show()
    QTest.qWait(30)
    widget.setTextCursor(widget.document().find("bold"))
    widget.copy()
    mime = QApplication.clipboard().mimeData()
    assert mime.text() == "bold"
    assert "<strong>bold</strong>" in mime.html()
    assert "Before" not in mime.html() and "after" not in mime.html()
    assert bytes(mime.data(INTERNAL_MARKDOWN)).decode().strip() == "**bold**"


def test_copy_has_same_meaning_in_both_app_hosts(main_window, qtbot, qapp, tmp_path, monkeypatch):
    from sp.app.folder_navigator.window import Window
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    path = tmp_path / "notes.md"
    path.write_text(SOURCE)
    folder = Window(tmp_path)
    qtbot.addWidget(folder)
    folder.open_file(path, pinned=True)
    tab = folder.active_tab()
    main_window.current_path = "/notes.md"
    main_window.editor.set_markdown(SOURCE)
    main_window._set_reading_view_enabled(True)
    tab.set_reading_view(True)
    QTest.qWait(40)
    for widget in (main_window.editor, main_window.reading_view.browser, tab.editor, tab.reading_view.browser):
        select_all(widget)
        widget.copy()
        mime = QApplication.clipboard().mimeData()
        assert mime.hasHtml()
        assert "<strong>bold</strong>" in mime.html()
        assert "background" not in mime.html()
        assert mime.hasFormat(INTERNAL_MARKDOWN)
    main_window._set_reading_view_enabled(False)
    main_window.editor.document().setModified(False)
    tab.editor.document().setModified(False)


def test_reader_context_copy_variants_are_explicit(qtbot, qapp):
    view = MarkdownReadingView()
    qtbot.addWidget(view)
    view.set_markdown(SOURCE)
    select_all(view.browser)
    menu = view.browser.createStandardContextMenu()
    plain = next(action for action in menu.actions() if action.text() == "Copy as Plain Text")
    markdown = next(action for action in menu.actions() if action.text() == "Copy as Markdown")
    plain.trigger()
    assert not QApplication.clipboard().mimeData().hasHtml()
    assert "**" not in QApplication.clipboard().text()
    markdown.trigger()
    assert QApplication.clipboard().text() == SOURCE
    assert not QApplication.clipboard().mimeData().hasHtml()
    assert QApplication.clipboard().mimeData().hasFormat("text/markdown")
    menu.deleteLater()


def test_editor_plain_copy_action_uses_readable_text(qtbot):
    editor = MarkdownEditor()
    qtbot.addWidget(editor)
    editor.set_markdown(SOURCE)
    select_all(editor)
    menu = editor.createStandardContextMenu()
    next(action for action in menu.actions() if action.text() == "Copy as Plain Text").trigger()
    mime = QApplication.clipboard().mimeData()
    assert not mime.hasHtml() and not mime.hasFormat(INTERNAL_MARKDOWN)
    assert "Some bold, italic, and website." in mime.text()
    menu.deleteLater()


def test_reader_selected_section_uses_source_semantics_without_extra_blocks(qtbot, qapp):
    view = MarkdownReadingView()
    qtbot.addWidget(view)
    view.set_markdown('Before.\n\n## Selected\n\n- First\n- Second\n\nAfter.')
    view.show()
    QTest.qWait(30)
    start = view.browser.document().find('Selected').block().position()
    end = view.browser.document().find('After.').block().position()
    cursor = QTextCursor(view.browser.document())
    cursor.setPosition(start)
    cursor.setPosition(end, QTextCursor.KeepAnchor)
    view.browser.setTextCursor(cursor)
    view.browser.copy()
    mime = QApplication.clipboard().mimeData()
    assert '<h2>Selected</h2>' in mime.html()
    assert '<ul>' in mime.html()
    assert 'Before.' not in mime.html() and 'After.' not in mime.html()
    markdown = bytes(mime.data(INTERNAL_MARKDOWN)).decode()
    assert markdown.strip() == '## Selected\n\n- First\n- Second'


def test_partial_reader_code_keeps_line_breaks_and_indentation(qtbot, qapp):
    view = MarkdownReadingView()
    qtbot.addWidget(view)
    view.set_markdown('Before.\n\n```python\nfirst()\n    second()\n\nthird()\n```\n\nAfter.')
    start = view.browser.document().find('first()').selectionStart()
    end = view.browser.document().find('second()').selectionEnd()
    cursor = QTextCursor(view.browser.document())
    cursor.setPosition(start)
    cursor.setPosition(end, QTextCursor.KeepAnchor)
    view.browser.setTextCursor(cursor)
    view.browser.copy()
    mime = QApplication.clipboard().mimeData()
    assert mime.text() == 'first()\n    second()'
    assert mime.html().count('<pre>') == 1
    assert 'third()' not in mime.html() and 'After.' not in mime.html()


def test_partial_reader_internal_link_retains_target(qtbot):
    view = MarkdownReadingView()
    qtbot.addWidget(view)
    view.set_markdown('Before [:Projects:Alpha|Project Alpha] after.')
    view.browser.setTextCursor(view.browser.document().find('Project Alpha'))
    view.browser.copy()
    mime = QApplication.clipboard().mimeData()
    assert mime.text() == 'Project Alpha'
    markdown = bytes(mime.data(INTERNAL_MARKDOWN)).decode()
    assert markdown == '[:Projects:Alpha|Project Alpha]'
    assert 'Before' not in mime.html()


def test_reader_plain_and_markdown_context_actions_are_explicit(qtbot):
    view = MarkdownReadingView()
    qtbot.addWidget(view)
    view.set_markdown('## Title\n\n**Bold**')
    select_all(view.browser)
    menu = view.browser.createStandardContextMenu()
    actions = {action.text(): action for action in menu.actions()}
    actions['Copy as Plain Text'].trigger()
    mime = QApplication.clipboard().mimeData()
    assert mime.text() == 'Title\n\nBold'
    assert not mime.hasHtml() and not mime.hasFormat(INTERNAL_MARKDOWN)
    actions['Copy as Markdown'].trigger()
    mime = QApplication.clipboard().mimeData()
    assert mime.text() == view.last_markdown
    assert mime.hasFormat('text/markdown') and not mime.hasHtml()
    menu.deleteLater()


@pytest.mark.parametrize('kind', ['editor', 'readonly_editor', 'reader'])
def test_copy_keyboard_shortcut_uses_shared_formats(qtbot, qapp, kind):
    if kind == 'reader':
        view = MarkdownReadingView()
        qtbot.addWidget(view)
        view.set_markdown('Some **bold** text.')
        widget = view.browser
        view.show()
    else:
        widget = MarkdownEditor()
        qtbot.addWidget(widget)
        widget.set_markdown('Some **bold** text.')
        widget.setReadOnly(kind == 'readonly_editor')
        widget.show()
    widget.activateWindow()
    widget.setFocus()
    QTest.qWait(30)
    select_all(widget)
    QApplication.clipboard().clear()
    QTest.keyClick(widget, Qt.Key_C, Qt.ControlModifier)
    mime = QApplication.clipboard().mimeData()
    assert mime.hasHtml() and mime.hasFormat(INTERNAL_MARKDOWN)
    assert mime.text() == 'Some bold text.'
    assert '<strong>bold</strong>' in mime.html()


def test_plain_copy_keeps_nested_list_structure(qapp):
    mime = clipboard_from_markdown('- Parent\n  - Child\n- Next', mode='plain')
    assert mime.text() == '• Parent\n  • Child\n• Next'


def test_vi_copy_uses_formatted_clipboard_and_retains_markdown_buffer(qtbot):
    editor = MarkdownEditor()
    qtbot.addWidget(editor)
    editor.set_markdown('Some **bold** text.')
    select_all(editor)
    assert editor._vi_copy_to_buffer()
    mime = QApplication.clipboard().mimeData()
    assert mime.hasHtml()
    assert mime.text() == 'Some bold text.'
    assert bytes(mime.data(INTERNAL_MARKDOWN)).decode().strip() == 'Some **bold** text.'
    assert editor._vi_clipboard.strip() == 'Some **bold** text.'


def test_editor_partial_selection_crossing_emphasis_boundaries_is_clean(qtbot):
    editor = MarkdownEditor()
    qtbot.addWidget(editor)
    editor.show()
    editor.set_markdown('Before **bold** normal *italic* after.')
    QTest.qWait(30)
    first = editor.document().find('bold')
    last = editor.document().find('italic')
    cursor = QTextCursor(editor.document())
    cursor.setPosition(first.selectionStart())
    cursor.setPosition(last.selectionEnd(), QTextCursor.KeepAnchor)
    editor.setTextCursor(cursor)
    editor.copy()
    mime = QApplication.clipboard().mimeData()
    assert mime.text() == 'bold normal italic'
    assert '<strong>bold</strong>' in mime.html()
    assert '<em>italic</em>' in mime.html()
    assert 'Before' not in mime.html() and 'after.' not in mime.html()
    assert bytes(mime.data(INTERNAL_MARKDOWN)).decode().strip() == '**bold** normal *italic*'
