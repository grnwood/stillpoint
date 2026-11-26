import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMainWindow, QVBoxLayout, QWidget

from sp.app import config
from sp.app.ui.diagram_editor_search import DiagramEditorSearch
from sp.app.ui.plantuml_editor_window import ViPlainTextEdit, style_diagram_code_editor
from sp.app.ui.editor_search import SearchEngine


@pytest.fixture
def surface(qtbot, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "GLOBAL_CONFIG", tmp_path / "preferences.json")
    monkeypatch.setattr(config, "load_vi_mode_enabled", lambda: False)
    window = QMainWindow()
    central = QWidget()
    window.setCentralWidget(central)
    layout = QVBoxLayout(central)
    window.editor = ViPlainTextEdit()
    style_diagram_code_editor(window.editor, "plantuml_editor", "plantuml")
    layout.addWidget(window.editor)
    window.search = DiagramEditorSearch(window, window.editor, layout)
    window.editor.setPlainText("node one\nnode two\nNODE three")
    window.resize(700, 500)
    qtbot.addWidget(window)
    window.activateWindow()
    window.editor.setFocus()
    QTest.qWait(10)
    return window


def test_primary_find_and_replace_use_stillpoint_bar(surface):
    original = surface.editor.toPlainText()
    QTest.keyClick(surface.editor, Qt.Key_F, Qt.ControlModifier)
    assert surface.find_bar.isVisible()
    assert surface.find_bar.query_edit.hasFocus()
    assert surface.find_bar.replace_edit.isHidden()
    QTest.keyClick(surface.find_bar.query_edit, Qt.Key_Escape)
    assert surface.editor.hasFocus()
    QTest.keyClick(surface.editor, Qt.Key_H, Qt.ControlModifier)
    assert surface.find_bar.replace_edit.isVisible()
    assert surface.editor.toPlainText() == original


def test_vi_slash_find_repeat_and_previous_preserve_navigation_mode(surface, monkeypatch):
    monkeypatch.setattr(config, "load_vi_mode_enabled", lambda: True)
    surface.editor.set_vi_mode_enabled(True)
    original = surface.editor.toPlainText()
    QTest.keyClick(surface.editor, Qt.Key_Slash)
    assert surface.find_bar.query_edit.hasFocus()
    surface.find_bar.query_edit.setText("node")
    QTest.keyClick(surface.find_bar.query_edit, Qt.Key_Return)
    assert surface.editor.textCursor().selectionStart() == 0
    QTest.keyClick(surface.find_bar.query_edit, Qt.Key_Escape)
    assert surface.editor.hasFocus()
    assert surface.editor._vi_mode_active
    QTest.keyClick(surface.editor, Qt.Key_N)
    assert surface.editor.textCursor().selectionStart() == 9
    QTest.keyClick(surface.editor, Qt.Key_F3)
    assert surface.editor.textCursor().selectedText() == "NODE"
    QTest.keyClick(surface.editor, Qt.Key_F3, Qt.ShiftModifier)
    assert surface.editor.textCursor().selectionStart() == 9
    assert surface.editor.toPlainText() == original


def test_slash_in_insert_mode_inserts_literal_slash(surface, monkeypatch):
    monkeypatch.setattr(config, "load_vi_mode_enabled", lambda: True)
    surface.editor.set_vi_mode_enabled(True)
    surface.editor._enter_vi_insert_mode()
    QTest.keyClick(surface.editor, Qt.Key_Slash)
    assert surface.find_bar.isHidden()
    assert surface.editor.toPlainText().startswith("/")


def test_replace_and_replace_all_are_undoable(surface):
    original = surface.editor.toPlainText()
    QTest.keyClick(surface.editor, Qt.Key_H, Qt.ControlModifier)
    bar = surface.find_bar
    bar.query_edit.setText("node")
    bar.replace_edit.setText("actor")
    QTest.keyClick(bar.query_edit, Qt.Key_Return)
    bar.replace_btn.click()
    assert surface.editor.toPlainText().startswith("actor one")
    bar.replace_all_btn.click()
    assert surface.editor.toPlainText() == "actor one\nactor two\nactor three"
    surface.editor.undo()
    assert surface.editor.toPlainText() == "actor one\nnode two\nNODE three"
    surface.editor.undo()
    assert surface.editor.toPlainText() == original


def test_search_wrap_and_case_sensitivity(surface):
    search = surface.search
    surface.editor.moveCursor(QTextCursor.End)
    search.find("node")
    assert surface.editor.textCursor().selectionStart() == 0
    assert "Wrapped" in surface.statusBar().currentMessage()
    search.find("NODE", case_sensitive=True)
    assert surface.editor.textCursor().selectedText() == "NODE"
    search.find("absent")
    assert surface.statusBar().currentMessage() == "No match found."


def test_case_insensitive_replace_accepts_match_with_different_case(surface):
    surface.editor.moveCursor(QTextCursor.End)
    surface.search.find("NODE")
    surface.search.replace("actor")
    assert surface.editor.toPlainText().startswith("actor one")


def test_vi_preferences_refresh_without_leaving_insert_mode(surface, monkeypatch):
    style = ["block"]
    monkeypatch.setattr(config, "load_vi_mode_enabled", lambda: True)
    monkeypatch.setattr(config, "load_vi_cursor_style", lambda: style[0])
    surface.search.eventFilter(surface, QEvent(QEvent.WindowActivate))
    assert surface.editor._vi_cursor_style == "block"
    assert surface.editor._vi_mode_active
    surface.editor._enter_vi_insert_mode()
    style[0] = "line"
    surface.search.eventFilter(surface, QEvent(QEvent.WindowActivate))
    assert surface.editor._vi_cursor_style == "line"
    assert surface.editor._vi_insert_mode
    assert surface.editor._vi_line_outline_rect().isEmpty()


def test_replace_all_handles_unicode_and_respects_read_only(surface):
    editor = surface.editor
    editor.setPlainText("node node node")
    engine = SearchEngine(editor)
    assert engine.replace_all("node", "🙂node") == 3
    assert editor.toPlainText() == "🙂node 🙂node 🙂node"
    editor.undo()
    assert editor.toPlainText() == "node node node"
    editor.setReadOnly(True)
    engine.find_next("node")
    assert not engine.replace_current("changed")
    assert engine.replace_all("node", "changed") == 0
    assert editor.toPlainText() == "node node node"


def test_shared_engine_runs_through_main_markdown_editor_search_api(qtbot):
    from sp.app.ui.markdown_editor import MarkdownEditor

    editor = MarkdownEditor()
    qtbot.addWidget(editor)
    editor.setPlainText("node one\nNODE two")
    editor.moveCursor(QTextCursor.Start)
    assert isinstance(editor._search_engine, SearchEngine)
    assert editor.search_find_next("NODE")
    assert editor.textCursor().selectedText() == "node"
    assert editor.search_replace_current("actor")
    assert editor.search_replace_all("node", "🙂node") == 1
    assert editor.toPlainText() == "actor one\n🙂node two"
    editor.undo()
    assert editor.toPlainText() == "actor one\nNODE two"
