from pathlib import Path

import pytest
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest

from sp.app import config
from sp.app.folder_navigator import core
from sp.app.folder_navigator.editors import SourceEditor
from sp.app.folder_navigator.window import Window
from sp.app.ui.markdown_editor import MarkdownEditor

SOURCE = '# **Architecture Review**\n\n' + '\n\n'.join(f'Paragraph {i}' for i in range(12))


@pytest.fixture
def window(qtbot, monkeypatch, tmp_path):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setattr(config, 'GLOBAL_CONFIG', tmp_path / 'preferences.json')
    instance = Window(tmp_path)
    qtbot.addWidget(instance)
    instance.resize(1000, 650)
    instance.show()
    yield instance
    # Tests deliberately retain unsaved edits; close them without a modal
    # save prompt after the assertions (and before monkeypatch teardown).
    for tab in instance.all_tabs():
        if tab.editor is not None:
            tab.clean_text = tab.text_for_save()
            tab.editor.document().setModified(False)
    instance.close()


@pytest.mark.parametrize('limit,value', [('MAX_RICH_MARKDOWN_BYTES', 32),
                                         ('MAX_RICH_MARKDOWN_LINES', 3),
                                         ('MAX_RICH_MARKDOWN_LINE_CHARS', 8)])
def test_full_markdown_button_overrides_guard_and_enables_read_edit(window, monkeypatch, tmp_path, limit, value):
    monkeypatch.setattr(core, limit, value)
    path = tmp_path / 'large.md'
    path.write_text(SOURCE)
    window._reading_view_preferred = True
    window.open_file(path, pinned=True)
    old = window.active_tab()
    assert isinstance(old.editor, SourceEditor)
    assert not window.reading_view_action.isEnabled()
    assert old.rich_markdown_button.isVisible()
    old.rich_markdown_button.click()
    assert not old.rich_markdown_button.isEnabled()
    QTest.qWait(160)
    full = window.active_tab()
    assert full is not old
    assert window.tabs.count() == 1
    assert isinstance(full.editor, MarkdownEditor)
    assert full.property('folderMarkdownRendered')
    assert full.property('folderFullMarkdownOverride')
    assert not full.property('folderLargeMarkdownSource')
    assert full.text_for_save().strip() == SOURCE
    assert path.read_text() == SOURCE
    assert not full.dirty
    assert window.reading_view_action.isEnabled()
    assert full.reading_view_enabled
    assert 'Architecture Review' in full.reading_view.browser.toPlainText()
    assert '**Architecture' not in full.reading_view.browser.toPlainText()
    window.reading_view_action.trigger()
    assert not full.reading_view_enabled
    window.reading_view_action.trigger()
    assert full.reading_view_enabled
    assert full.text_for_save().strip() == SOURCE


def test_explicit_full_render_keeps_unsaved_buffer_and_file_metadata(window, monkeypatch, tmp_path):
    monkeypatch.setattr(core, 'MAX_RICH_MARKDOWN_BYTES', 32)
    path = tmp_path / 'large.md'
    path.write_bytes(SOURCE.replace('\n', '\r\n').encode('utf-16'))
    window.open_file(path, pinned=True)
    old = window.active_tab()
    loaded = old.loaded
    cursor = old.editor.textCursor()
    cursor.movePosition(QTextCursor.End)
    old.editor.setTextCursor(cursor)
    old.editor.insertPlainText('\n\nUnsaved **change** 🚀')
    source_line = old.editor.textCursor().blockNumber()
    expected = old.text_for_save()
    disk = path.read_bytes()
    old.rich_markdown_button.click()
    QTest.qWait(160)
    full = window.active_tab()
    assert isinstance(full.editor, MarkdownEditor)
    assert full.text_for_save().rstrip() == expected.rstrip()
    assert full.loaded is loaded
    assert full.loaded.encoding == 'utf-16'
    assert full.loaded.newline == '\r\n'
    assert full.dirty and full.pinned
    assert full.editor.textCursor().blockNumber() == source_line
    assert path.read_bytes() == disk


def test_failed_explicit_render_preserves_source_and_allows_retry(window, monkeypatch, tmp_path):
    monkeypatch.setattr(core, 'MAX_RICH_MARKDOWN_BYTES', 32)
    path = tmp_path / 'large.md'
    path.write_text(SOURCE)
    window.open_file(path, pinned=True)
    old = window.active_tab()
    old.editor.insertPlainText('Unsaved ')
    expected = old.text_for_save()
    original = MarkdownEditor.set_markdown

    def fail(*args):
        raise ValueError('Test render failure')

    monkeypatch.setattr(MarkdownEditor, 'set_markdown', fail)
    old.rich_markdown_button.click()
    QTest.qWait(40)
    assert window.active_tab() is old
    assert old.text_for_save() == expected
    assert old.dirty
    assert old.rich_markdown_button.isEnabled()
    assert 'failed' in old.notice.text()
    assert window.tabs.count() == 1
    monkeypatch.setattr(MarkdownEditor, 'set_markdown', original)
    old.rich_markdown_button.click()
    QTest.qWait(160)
    assert isinstance(window.active_tab().editor, MarkdownEditor)
    assert window.active_tab().text_for_save().rstrip() == expected.rstrip()
    assert window.active_tab().dirty


def test_leaving_preview_before_requested_render_does_not_reopen_it(window, monkeypatch, tmp_path):
    monkeypatch.setattr(core, 'MAX_RICH_MARKDOWN_BYTES', 32)
    path = tmp_path / 'large.md'
    path.write_text(SOURCE)
    other = tmp_path / 'other.txt'
    other.write_text('Other content')
    window.open_file(path)
    old = window.active_tab()
    old.rich_markdown_button.click()
    window.open_file(other)
    QTest.qWait(80)
    assert window.tabs.count() == 1
    assert window.active_tab().path == other
    assert not window.active_tab().markdown
