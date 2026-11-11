import pytest
from shiboken6 import Shiboken

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from sp.app.ui.main_window import MainWindow
from sp.app.ui.mode_window import ModeWindow


@pytest.mark.parametrize("mode", ["focus", "audience"])
def test_main_shortcuts_with_mode_window_open(qtbot, monkeypatch, mode):
    calls = []
    monkeypatch.setattr("sp.app.ui.main_window.config.has_active_vault", lambda: False)
    monkeypatch.setattr(MainWindow, "_jump_to_page", lambda self: calls.append("jump"))
    monkeypatch.setattr(
        MainWindow, "_show_find_bar",
        lambda self, **kwargs: calls.append(("main-find", kwargs["replace"])),
    )
    monkeypatch.setattr(MainWindow, "_insert_date", lambda self: calls.append("main-date"))
    monkeypatch.setattr(
        MainWindow, "_toggle_mode_overlay",
        lambda self, mode: calls.append(("main-toggle", mode)),
    )
    main = MainWindow("http://127.0.0.1:1")
    qtbot.addWidget(main)
    window = ModeWindow(
        mode, main.editor, vault_root=None, page_path="/Page.md",
        read_only=False, heading_provider=lambda: [], parent=main,
    )
    qtbot.addWidget(window)
    QTest.qWait(100)

    main.activateWindow()
    main.editor.setFocus()
    QTest.qWait(50)
    QTest.keyClick(main.editor, Qt.Key_J, Qt.ControlModifier)
    assert calls == ["jump"]
    QTest.keyClick(main.editor, Qt.Key_F, Qt.ControlModifier)
    QTest.keyClick(main.editor, Qt.Key_H, Qt.ControlModifier)
    QTest.keyClick(main.editor, Qt.Key_D, Qt.ControlModifier)
    QTest.keyClick(main.editor, Qt.Key_F, Qt.ControlModifier | Qt.AltModifier)
    assert calls == [
        "jump", ("main-find", False), ("main-find", True),
        "main-date", ("main-toggle", "focus"),
    ]
    main_calls = list(calls)

    window.activateWindow()
    window.editor.setFocus()
    QTest.qWait(50)
    QTest.keyClick(window.editor, Qt.Key_F, Qt.ControlModifier)
    assert window._find_bar.isVisible()
    QTest.keyClick(window.editor, Qt.Key_H, Qt.ControlModifier)
    assert calls == main_calls
    QTest.keyClick(window.editor, Qt.Key_A, Qt.ControlModifier | Qt.AltModifier)
    assert not window.isVisible()
    assert calls == main_calls


@pytest.mark.parametrize("mode", ["focus", "audience"])
def test_jump_commits_mode_edits_before_opening_selected_page(qtbot, monkeypatch, mode):
    from PySide6.QtWidgets import QDialog

    monkeypatch.setattr("sp.app.ui.main_window.config.has_active_vault", lambda: False)
    main = MainWindow("http://127.0.0.1:1")
    qtbot.addWidget(main)
    main.current_path = "/Original/Original.md"
    main.editor.set_markdown("Original body\n")
    main.editor.document().setModified(False)
    window = ModeWindow(
        mode, main.editor, vault_root=None, page_path=main.current_path,
        read_only=False, heading_provider=lambda: [], parent=main,
    )
    main._mode_window = window
    window.closed.connect(main._on_mode_overlay_closed)
    qtbot.addWidget(window)
    QTest.qWait(100)
    window.editor.set_markdown("Edited in mode\n")

    saved = []
    opened = []

    def save(**kwargs):
        saved.append((main.current_path, main.editor.to_markdown()))
        main.editor.document().setModified(False)

    def open_page(*args):
        if main._mode_window is not None:
            return MainWindow._open_file(main, *args)
        opened.append(args)

    class Picker:
        def __init__(self, *args, **kwargs):
            pass

        def exec(self):
            return QDialog.Accepted

        def selected_path(self):
            return "/Selected/Selected.md"

    monkeypatch.setattr(main, "_save_current_file", save)
    monkeypatch.setattr(main, "_open_file", open_page)
    monkeypatch.setattr("sp.app.ui.main_window.JumpToPageDialog", Picker)
    monkeypatch.setattr("sp.app.ui.main_window.config.has_active_vault", lambda: True)
    main.activateWindow()
    main.editor.setFocus()
    QTest.qWait(50)
    QTest.keyClick(main.editor, Qt.Key_J, Qt.ControlModifier)
    QTest.qWait(100)

    assert not Shiboken.isValid(window)
    assert saved == [("/Original/Original.md", "Edited in mode\n")]
    assert len(opened) == 1
    assert opened[0][0] == "/Selected/Selected.md"
    assert main._pending_reload_path is None
    main.current_path = None
