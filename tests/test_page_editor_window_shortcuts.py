import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QKeySequence, QWheelEvent, QShortcut, QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QMainWindow, QToolBar

from sp.app.ui import keyboard_shortcuts
from sp.app.ui.page_editor_window import PageEditorWindow
from sp.app.ui.main_window import MainWindow


def new_editor(qtbot, monkeypatch):
    monkeypatch.setattr(PageEditorWindow, "_load_content", lambda self: None)
    monkeypatch.setattr("sp.app.ui.page_editor_window.config.load_popup_font_size", lambda default: 14)
    monkeypatch.setattr("sp.app.ui.page_editor_window.config.save_popup_font_size", lambda value: None)
    window = PageEditorWindow(
        api_base="http://127.0.0.1:1", vault_root="/tmp/test-vault",
        page_path="/Page/Page.md", read_only=False,
        open_in_main_callback=lambda *args, **kwargs: None,
    )
    qtbot.addWidget(window)
    return window


@pytest.mark.parametrize("modifier", [Qt.ControlModifier, Qt.MetaModifier])
def test_page_editor_primary_wheel_zooms_and_plain_wheel_scrolls(qapp, qtbot, monkeypatch, modifier):
    window = new_editor(qtbot, monkeypatch)

    def wheel(delta, modifiers):
        event = QWheelEvent(
            QPointF(10, 10), QPointF(10, 10), QPoint(), QPoint(0, delta),
            Qt.NoButton, modifiers, Qt.NoScrollPhase, False,
        )
        qapp.sendEvent(window.editor.viewport(), event)

    wheel(120, modifier)
    assert window._font_size == 15
    assert window.editor.font().pointSizeF() == 15
    wheel(-120, modifier)
    assert window._font_size == 14
    wheel(120, Qt.NoModifier)
    assert window._font_size == 14
    assert not window._is_dirty()


@pytest.mark.parametrize("system,modifier", [("Darwin", "Cmd"), ("Windows", "Ctrl"), ("Linux", "Ctrl")])
def test_page_editor_tooltips_name_platform_shortcuts(qtbot, monkeypatch, system, modifier):
    monkeypatch.setattr(keyboard_shortcuts.platform, "system", lambda: system)
    window = new_editor(qtbot, monkeypatch)
    actions = {action.text(): action for toolbar in window.findChildren(QToolBar) for action in toolbar.actions()}
    assert actions["Save"].shortcut() == QKeySequence(QKeySequence.Save)
    assert actions["Save"].shortcutContext() == Qt.WindowShortcut
    assert actions["Save"].toolTip() == f"Save ({modifier}+S)"
    assert f"{modifier}+R" in actions["Reload"].toolTip()
    assert f"{modifier}+L" in actions["Insert Link…"].toolTip()
    assert f"{modifier}+mouse wheel" in actions["A+"].toolTip()
    assert f"{modifier}+mouse wheel" in actions["A-"].toolTip()


@pytest.mark.parametrize("vi_mode", [False, True])
def test_insert_link_hotkey_opens_detached_picker_once_with_selection(qapp, qtbot, monkeypatch, vi_mode):
    monkeypatch.setattr("sp.app.ui.page_editor_window.config.load_vi_mode_enabled", lambda: vi_mode)
    main = QMainWindow()
    qtbot.addWidget(main)
    main_calls = []
    conflicting_shortcut = QShortcut(QKeySequence("Ctrl+L"), main)
    conflicting_shortcut.setContext(Qt.ApplicationShortcut)
    conflicting_shortcut.activated.connect(lambda: main_calls.append("main"))
    window = new_editor(qtbot, monkeypatch)
    window.editor.set_markdown("Selected words\n")
    window._last_saved_content = window.editor.to_markdown()
    window.activateWindow()
    window.editor.setFocus()
    qapp.processEvents()
    cursor = window.editor.textCursor()
    cursor.setPosition(0)
    cursor.setPosition(8, QTextCursor.KeepAnchor)
    window.editor.setTextCursor(cursor)
    opened = []

    class LinkPicker:
        def __init__(self, parent, selected_text):
            opened.append((parent, selected_text))

        def exec(self):
            return QDialog.Rejected

    monkeypatch.setattr("sp.app.ui.page_editor_window.InsertLinkDialog", LinkPicker)
    QTest.keyClick(window.editor, Qt.Key_L, Qt.ControlModifier)
    assert opened == [(window, "Selected")]
    assert main_calls == []
    assert window.editor.textCursor().selectedText() == "Selected"
    assert not window._is_dirty()


def test_print_shortcut_does_not_print_main_page_from_detached_editor(qapp, qtbot, monkeypatch):
    monkeypatch.setattr("sp.app.ui.main_window.config.has_active_vault", lambda: False)
    printed = []
    monkeypatch.setattr(MainWindow, "_print_current_page", lambda self: printed.append(self))
    main = MainWindow("http://127.0.0.1:1")
    qtbot.addWidget(main)
    window = new_editor(qtbot, monkeypatch)
    window.activateWindow()
    window.editor.setFocus()
    qapp.processEvents()
    QTest.keyClick(window.editor, Qt.Key_P, Qt.ControlModifier)
    assert printed == []
    main.activateWindow()
    main.editor.setFocus()
    qapp.processEvents()
    QTest.keyClick(main.editor, Qt.Key_P, Qt.ControlModifier)
    assert printed == [main]
