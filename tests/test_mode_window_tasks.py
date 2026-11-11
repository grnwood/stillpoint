from types import MethodType, SimpleNamespace
import hashlib

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMainWindow, QMenu

from sp.app.ui.main_window import MainWindow
from sp.app.ui.markdown_editor import MarkdownEditor
from sp.app.ui.mode_window import ModeWindow
from sp.app.ui.task_panel import TaskPanel
from sp.app.task_mutations import rewrite_task_line


@pytest.fixture(params=["focus", "audience"])
def task_mode(qtbot, monkeypatch, request):
    owner = QMainWindow()
    owner.editor = MarkdownEditor()
    owner.editor.set_markdown("Original\n- [ ] Original task\n")
    owner.setCentralWidget(owner.editor)
    qtbot.addWidget(owner)
    panel = TaskPanel()
    qtbot.addWidget(panel)
    monkeypatch.setattr(panel, "_schedule_mutation_refresh", lambda **kwargs: None)
    owner.right_panel = SimpleNamespace(task_panel=panel)
    owner.current_path = "/Page/Page.md"
    owner._dirty_flag = False
    owner._suspend_autosave = False
    owner._page_revisions = {}
    owner._page_windows = []
    owner._pending_task_editor_reload_paths = set()
    owner._update_dirty_indicator = lambda: None
    owner._capture_undo_snapshot = lambda *args, **kwargs: None
    owner._normalize_task_date_paths = lambda paths: set(paths)
    owner._main_editor_task_context_menu = MethodType(MainWindow._main_editor_task_context_menu, owner)
    owner._apply_task_status_in_place = MethodType(MainWindow._apply_task_status_in_place, owner)
    owner._update_page_revision = MethodType(MainWindow._update_page_revision, owner)
    calls = []
    disk = {"content": owner.editor.to_markdown()}

    def save(**kwargs):
        calls.append("save")
        disk["content"] = owner.editor.to_markdown()
        owner.editor.document().setModified(False)
        owner._dirty_flag = False

    def post(path, json):
        calls.append((path, json))
        if path == "/api/tasks/mutate":
            lines = disk["content"].splitlines(keepends=True)
            line = json["targets"][0]["line"] - 1
            lines[line] = rewrite_task_line(lines[line], status=json["status"])
            disk["content"] = "".join(lines)
            payload = {"paths": [owner.current_path], "undo_id": "undo-1"}
        else:
            assert path == "/api/file/read"
            payload = {"content": disk["content"], "rev": 2}
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: payload)

    owner._save_current_file = save
    owner.http = SimpleNamespace(post=post)
    panel.set_http_client(owner.http)
    window = ModeWindow(
        request.param, owner.editor, vault_root="/tmp/test-vault",
        page_path=owner.current_path, read_only=False, heading_provider=lambda: [], parent=owner,
    )
    owner._mode_window = window
    panel.taskDatesWillApply.connect(lambda paths: MainWindow._on_task_dates_will_apply(owner, paths))
    panel.taskDatesApplied.connect(lambda paths: MainWindow._on_task_dates_applied(owner, paths))
    qtbot.addWidget(window)
    QTest.qWait(50)
    window.editor.set_markdown("Unsaved introduction\n- [ ] Edited task @phone\n")
    return window, owner, panel, calls


def test_mode_task_menu_saves_and_updates_in_place_without_reading_page(task_mode, monkeypatch):
    window, owner, panel, calls = task_mode
    menu = window._task_context_menu(1, QPoint(10, 10))
    expected = panel._build_task_context_menu([{"task": {"status": "todo"}}])
    assert [a.text() for a in menu.actions()] == [a.text() for a in expected.actions()]
    assert menu.parentWidget() is window
    assert calls == []
    document = window.editor.document()
    base_document = owner.editor.document()
    monkeypatch.setattr(window.editor, "set_markdown", lambda *args: pytest.fail("mode buffer reset"))
    monkeypatch.setattr(window, "_reload_after_task_mutation", lambda: pytest.fail("mode reloaded"))
    menu.actions()[0].trigger()

    assert calls[0] == "save"
    path, payload = calls[1]
    assert path == "/api/tasks/mutate"
    assert payload["targets"][0]["line"] == 2
    assert payload["targets"][0]["expected_text"] == "Edited task"
    assert payload["status"] == "done"
    assert window.isVisible()
    assert window.editor.to_markdown() == "Unsaved introduction\n- [x] Edited task @phone\n"
    assert owner.editor.to_markdown() == window.editor.to_markdown()
    assert window.editor.document() is document
    assert owner.editor.document() is base_document
    assert len(calls) == 2
    assert owner._page_revisions[owner.current_path]["content_sha256"] == hashlib.sha256(
        owner.editor.to_markdown().encode("utf-8")
    ).hexdigest()
    assert not owner.editor.document().isModified()


def test_task_complete_and_reopen_keep_selection_scroll_and_other_blocks(task_mode):
    window, owner, panel, calls = task_mode
    editor = window.editor
    editor.set_markdown("Unsaved introduction\n- [ ] Edited task @phone\n" + "Other content\n" * 150)
    selected = QTextCursor(editor.document().findBlockByNumber(100))
    selected.movePosition(QTextCursor.Right, QTextCursor.KeepAnchor, 5)
    editor.setTextCursor(selected)
    editor.verticalScrollBar().setValue(300)
    selection = (selected.position(), selected.anchor(), selected.selectedText())
    scroll = editor.verticalScrollBar().value()
    other_block = editor.document().findBlockByNumber(100)
    other_revision = other_block.revision()
    for label, marker in (("Mark Complete", "- [x]"), ("Reopen Task", "- [ ]")):
        menu = window._task_context_menu(1, QPoint(10, 10))
        next(action for action in menu.actions() if action.text() == label).trigger()
        assert marker + " Edited task @phone" in editor.to_markdown()
        cursor = editor.textCursor()
        assert (cursor.position(), cursor.anchor(), cursor.selectedText()) == selection
        assert editor.verticalScrollBar().value() == scroll
        assert other_block.revision() == other_revision
    assert [call[0] for call in calls if isinstance(call, tuple)] == ["/api/tasks/mutate"] * 2
    window.close()
    assert not owner.editor.document().isModified()


def test_main_task_status_changes_do_not_reload_or_reset_buffer(task_mode, monkeypatch):
    window, owner, panel, calls = task_mode
    window._read_only = True
    owner._mode_window = None
    owner.editor.set_markdown("Introduction\n- [ ] Main task\n" + "Keep this text\n" * 100)
    cursor = QTextCursor(owner.editor.document().findBlockByNumber(50))
    owner.editor.setTextCursor(cursor)
    owner.editor.verticalScrollBar().setValue(200)
    position = cursor.position()
    scroll = owner.editor.verticalScrollBar().value()
    document = owner.editor.document()
    monkeypatch.setattr(owner.editor, "set_markdown", lambda *args: pytest.fail("main buffer reset"))
    owner._open_file = lambda *args, **kwargs: pytest.fail("main page reloaded")
    for label in ("Mark Complete", "Reopen Task"):
        menu = owner._main_editor_task_context_menu(1, QPoint(10, 10))
        next(action for action in menu.actions() if action.text() == label).trigger()
        assert owner.editor.document() is document
        assert owner.editor.textCursor().position() == position
        assert owner.editor.verticalScrollBar().value() == scroll
    assert owner.editor.to_markdown().startswith("Introduction\n- [ ] Main task\n")
    assert not owner._pending_task_editor_reload_paths
    assert [call[0] for call in calls if isinstance(call, tuple)] == ["/api/tasks/mutate"] * 2


def test_failed_task_mutation_does_not_change_visible_checkbox(task_mode, monkeypatch):
    window, owner, panel, calls = task_mode
    errors = []
    monkeypatch.setattr(panel, "_show_mutation_error", lambda exc: errors.append(exc))

    def fail(*args, **kwargs):
        raise ValueError("Task changed on disk")

    monkeypatch.setattr(owner.http, "post", fail)
    window._task_context_menu(1, QPoint(10, 10)).actions()[0].trigger()
    assert errors
    assert "- [ ] Edited task" in window.editor.to_markdown()
    assert "- [ ] Edited task" in owner.editor.to_markdown()


def test_mode_context_menu_adds_task_actions_at_clicked_line(task_mode, monkeypatch):
    window, owner, panel, calls = task_mode
    menus = []
    menu = QMenu(window.editor)
    menu.exec = lambda *args: menus.append(menu)
    monkeypatch.setattr(window.editor, "createStandardContextMenu", lambda: menu)
    cursor = QTextCursor(window.editor.document().findBlockByNumber(1))
    window._show_editor_context_menu(window.editor.cursorRect(cursor).center())
    assert any(action.text() == "Task actions" for action in menus[0].actions())
    assert calls == []


@pytest.mark.parametrize("label,field", [("Edit Task", None), ("Edit Tags", "tags"), ("Move…", "destination")])
def test_mode_task_editor_is_owned_by_mode_window(task_mode, monkeypatch, label, field):
    window, owner, panel, calls = task_mode
    edits = []
    monkeypatch.setattr(panel, "edit_task", lambda task, **kwargs: edits.append((task, kwargs)))
    anchor = QPoint(10, 10)
    menu = window._task_context_menu(1, anchor)
    next(action for action in menu.actions() if action.text().startswith(label)).trigger()
    assert edits[0][0]["text"] == "Edited task"
    assert edits[0][1] == {"focus_field": field, "parent": window, "anchor_pos": anchor}


@pytest.mark.parametrize("label,role", [("Set Due Date", "due"), ("Set Start Date", "start")])
def test_mode_task_date_menu_is_owned_by_mode_window(task_mode, monkeypatch, label, role):
    window, owner, panel, calls = task_mode
    dates = []
    monkeypatch.setattr(panel, "_open_task_date_quick_menu", lambda *args, **kwargs: dates.append((args, kwargs)))
    anchor = QPoint(10, 10)
    menu = window._task_context_menu(1, anchor)
    next(action for action in menu.actions() if action.text().startswith(label)).trigger()
    args, kwargs = dates[0]
    assert args[0] == role
    assert args[1][0]["task"]["text"] == "Edited task"
    assert args[2] == anchor
    assert kwargs["parent"] is window


def test_mode_task_menu_blocks_mutation_when_save_fails(task_mode, monkeypatch):
    window, owner, panel, calls = task_mode
    monkeypatch.setattr(owner, "_save_current_file", lambda **kwargs: None)
    warnings = []
    monkeypatch.setattr("sp.app.ui.mode_window.QMessageBox.warning", lambda *args: warnings.append(args))
    window._task_context_menu(1, QPoint(10, 10)).actions()[0].trigger()
    assert warnings
    assert calls == []


def test_mode_task_menu_excludes_plain_text_and_read_only_tasks(task_mode):
    window, owner, panel, calls = task_mode
    assert window._task_context_menu(0, QPoint(10, 10)) is None
    window.editor.setReadOnly(True)
    assert window._task_context_menu(1, QPoint(10, 10)) is None
