import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QContextMenuEvent, QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QMenu

from sp.app import config
from sp.app.ui.main_window import MainWindow
from sp.app.ui.markdown_editor import MarkdownEditor
from sp.app.ui.page_editor_window import PageEditorWindow


@pytest.fixture
def page_editor(qtbot, monkeypatch):
    monkeypatch.setattr(PageEditorWindow, "_load_content", lambda self: None)
    monkeypatch.setattr(config, "load_vi_mode_enabled", lambda: False)
    calls = []
    window = PageEditorWindow(
        api_base="http://127.0.0.1:1", vault_root="/tmp/vault", page_path="/Page/Page.md",
        read_only=False, open_in_main_callback=lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    qtbot.addWidget(window)
    window.editor.set_markdown("Before [:Target|Visible link] after\n")
    window._last_saved_content = window.editor.to_markdown()
    window.editor.document().setModified(False)
    QTest.qWait(30)
    yield window, calls
    window._read_only = True


def link_cursor(editor):
    cursor = editor.textCursor()
    cursor.setPosition(editor.toPlainText().index("Visible link") + 3)
    assert editor._link_under_cursor(cursor)
    return cursor


def test_links_are_editable_text_without_click_or_enter_navigation(page_editor, qapp):
    window, callbacks = page_editor
    editor = window.editor
    activated = []
    editor.linkActivated.connect(activated.append)
    point = editor.cursorRect(link_cursor(editor)).center()
    QTest.mouseMove(editor.viewport(), point)
    assert editor.viewport().cursor().shape() == Qt.IBeamCursor
    QTest.mouseClick(editor.viewport(), Qt.LeftButton, pos=point)
    assert editor.textCursor().position() > 0
    QTest.mouseDClick(editor.viewport(), Qt.LeftButton, pos=point)
    assert editor.textCursor().hasSelection()
    cursor = link_cursor(editor)
    editor.setTextCursor(cursor)
    QTest.keyClick(editor, Qt.Key_Return)
    qapp.processEvents()
    assert activated == []
    assert callbacks == []
    assert editor.document().blockCount() >= 3
    assert "[:Target|Visible link]" in editor.to_markdown()


def test_link_picker_preserves_single_page_context_menu(page_editor, monkeypatch, qapp):
    window, callbacks = page_editor

    class Picker:
        def __init__(self, *args, **kwargs):
            pass

        def exec(self):
            return QDialog.Rejected

    monkeypatch.setattr("sp.app.ui.page_editor_window.InsertLinkDialog", Picker)
    window._insert_link()
    assert window.editor.contextMenuPolicy() == Qt.CustomContextMenu
    menus = []
    menu = QMenu(window)
    menu.addAction("Copy")
    menu.exec = lambda *args: menus.append(menu)
    monkeypatch.setattr(window.editor, "createStandardContextMenu", lambda: menu)
    point = window.editor.cursorRect(link_cursor(window.editor)).center()
    event = QContextMenuEvent(QContextMenuEvent.Mouse, point, window.editor.viewport().mapToGlobal(point))
    qapp.sendEvent(window.editor.viewport(), event)
    assert len(menus) == 1
    labels = [action.text() for action in menus[0].actions()]
    assert "Edit Link…" in labels
    assert "Remove Link" in labels
    assert "Copy Link Target" in labels
    assert "Insert link…" in labels
    assert "Insert Date…" in labels
    assert not any(label in labels for label in ("View...", "Page", "Navigate", "Focus Mode", "Audience Mode"))
    assert callbacks == []


def test_main_editor_still_activates_links(qtbot):
    editor = MarkdownEditor()
    qtbot.addWidget(editor)
    editor.set_markdown("Before [:Target|Visible link] after\n")
    QTest.qWait(30)
    activated = []
    editor.linkActivated.connect(activated.append)
    point = editor.cursorRect(link_cursor(editor)).center()
    QTest.mouseClick(editor.viewport(), Qt.LeftButton, pos=point)
    assert activated == [":Target"]


def test_detached_navigation_keys_do_not_navigate_main(qtbot, monkeypatch, page_editor, qapp):
    window, callbacks = page_editor
    monkeypatch.setattr(config, "has_active_vault", lambda: False)
    navigated = []
    for method in ("_navigate_history_back", "_navigate_history_forward", "_navigate_hierarchy_up",
                   "_navigate_hierarchy_down", "_show_quick_vault_picker"):
        monkeypatch.setattr(MainWindow, method, lambda self, *args: navigated.append("main"))
    main = MainWindow("http://127.0.0.1:1")
    qtbot.addWidget(main)
    window.activateWindow()
    window.editor.setFocus()
    window.editor.set_vi_mode_enabled(True)
    window.editor._enter_vi_navigation_mode()
    qapp.processEvents()
    picker_requests = []
    window.editor.vaultPickerRequested.connect(lambda *args: picker_requests.append(args))
    for key in (Qt.Key_H, Qt.Key_J, Qt.Key_K, Qt.Key_L, Qt.Key_Left, Qt.Key_Right):
        QTest.keyClick(window.editor, key, Qt.AltModifier)
    QTest.keyClick(window.editor, Qt.Key_V)
    QTest.keyClick(window.editor, Qt.Key_V, Qt.ControlModifier | Qt.AltModifier)
    assert navigated == []
    assert picker_requests == []
    assert window._source_path == "/Page/Page.md"
    assert callbacks == []
    main.activateWindow()
    main.editor.setFocus()
    qapp.processEvents()
    QTest.keyClick(main.editor, Qt.Key_Left, Qt.AltModifier)
    assert navigated == ["main"]
