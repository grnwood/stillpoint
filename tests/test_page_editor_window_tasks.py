from __future__ import annotations

from types import SimpleNamespace

import pytest
from datetime import date
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QDialog, QMenu, QMessageBox

from sp.app.ui import page_editor_window as page_editor_module
from sp.app.ui.page_editor_window import PageEditorWindow


def _new_window(monkeypatch, *, vi_mode: bool = True, callback=None) -> PageEditorWindow:
    monkeypatch.setattr(PageEditorWindow, "_load_content", lambda self: None)
    monkeypatch.setattr(page_editor_module.config, "load_vi_mode_enabled", lambda: vi_mode)
    return PageEditorWindow(
        api_base="http://127.0.0.1:1",
        vault_root="/tmp/test-vault",
        page_path="/Page/Page.md",
        read_only=False,
        open_in_main_callback=callback or (lambda _path, **_kwargs: None),
    )


def test_page_editor_enables_its_own_vi_task_rail(qtbot, monkeypatch) -> None:
    window = _new_window(monkeypatch)
    qtbot.addWidget(window)

    assert window.editor._task_hover_edit_enabled is True
    assert window.editor.viewportMargins().left() == window.editor.TASK_HOVER_RAIL_WIDTH


def test_page_editor_owns_inline_task_editor_and_mutation(qtbot, monkeypatch) -> None:
    refreshed_main: list[tuple[str, dict]] = []
    window = _new_window(
        monkeypatch,
        callback=lambda path, **kwargs: refreshed_main.append((path, kwargs)),
    )
    qtbot.addWidget(window)
    window.editor.set_markdown("- [ ] Call Sarah !! @phone >2026-08-20 <2026-08-21\n")
    window._last_saved_content = window.editor.to_markdown()

    opened: list[dict] = []

    class FakeTaskEditor:
        save_and_next = False

        def __init__(self, task, parent=None, **kwargs) -> None:
            self.window_modality = None
            opened.append({"task": task, "parent": parent, "kwargs": kwargs, "dialog": self})

        def setWindowModality(self, modality) -> None:
            self.window_modality = modality

        def exec(self) -> int:
            assert window._task_editor is self
            return QDialog.Accepted

        def values(self) -> dict:
            return {
                "text": "Call Alex",
                "status": "done",
                "priority": 3,
                "tags": ["phone", "followup"],
                "start": "2026-08-22",
                "due": "2026-08-23",
                "destination": None,
            }

    monkeypatch.setattr(page_editor_module, "TaskQuickEditor", FakeTaskEditor)

    class FakeResponse:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"paths": ["/Page/Page.md"], "tags": []}

    class FakeHttp:
        def __init__(self) -> None:
            self.posts: list[tuple[str, dict]] = []

        def get(self, _path, params=None):
            return FakeResponse()

        def post(self, path, json=None):
            self.posts.append((path, json))
            return FakeResponse()

        def close(self) -> None:
            return None

    window.http.close()
    fake_http = FakeHttp()
    window.http = fake_http
    reloads: list[str] = []
    monkeypatch.setattr(window, "_load_content", lambda: reloads.append(window._source_path))

    anchor = QPoint(120, 80)
    window.editor.taskEditRequested.emit(0, anchor)

    assert opened[0]["parent"] is window
    assert opened[0]["dialog"].window_modality == Qt.WindowModal
    assert opened[0]["kwargs"]["anchor_pos"] == anchor
    assert opened[0]["kwargs"]["vi_mode"] is True
    assert window._task_editor is None
    assert fake_http.posts[0][0] == "/api/tasks/mutate"
    payload = fake_http.posts[0][1]
    assert payload["targets"] == [
        {
            "path": "/Page/Page.md",
            "line": 1,
            "expected_text": "Call Sarah",
            "expected_status": "todo",
        }
    ]
    assert payload["text"] == "Call Alex"
    assert payload["status"] == "done"
    assert reloads == ["/Page/Page.md"]
    assert refreshed_main == [
        ("/Page/Page.md", {"force": True, "refresh_only": True})
    ]


def test_page_editor_context_status_and_undo_do_not_reload(qtbot, monkeypatch):
    window = _new_window(monkeypatch, callback=lambda *args, **kwargs: pytest.fail("main refreshed"))
    qtbot.addWidget(window)
    window.editor.set_markdown("Introduction\n- [ ] Clicked task @phone\n" + "Other text\n" * 150)
    window._last_saved_content = window.editor.to_markdown()
    window.editor.document().setModified(False)
    cursor = QTextCursor(window.editor.document().findBlockByNumber(100))
    cursor.movePosition(QTextCursor.Right, QTextCursor.KeepAnchor, 5)
    window.editor.setTextCursor(cursor)
    window.editor.verticalScrollBar().setValue(300)
    selection = (cursor.position(), cursor.anchor(), cursor.selectedText())
    scroll = window.editor.verticalScrollBar().value()
    document = window.editor.document()
    posts = []

    def post(path, payload):
        posts.append((path, payload))
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"undo_id": "undo-local"})

    monkeypatch.setattr(window, "_api_post", post)
    monkeypatch.setattr(window, "_load_content", lambda: pytest.fail("page reloaded"))
    monkeypatch.setattr(window.editor, "set_markdown", lambda *args: pytest.fail("buffer reset"))
    for label, marker in (("Mark Complete", "- [x]"), ("Reopen Task", "- [ ]"),
                          ("Mark Complete", "- [x]"), ("Undo Last Change", "- [ ]")):
        menu = window._task_context_menu(1, QPoint(100, 100))
        next(action for action in menu.actions() if action.text().startswith(label)).trigger()
        assert marker + " Clicked task @phone" in window.editor.to_markdown()
        assert window.editor.document() is document
        current = window.editor.textCursor()
        assert (current.position(), current.anchor(), current.selectedText()) == selection
        assert window.editor.verticalScrollBar().value() == scroll
        assert not window._is_dirty()
    assert posts[0][1]["targets"] == [{
        "path": "/Page/Page.md", "line": 2,
        "expected_text": "Clicked task", "expected_status": "todo",
    }]
    assert posts[-1][0] == "/api/tasks/undo/undo-local"


def test_page_editor_context_menu_matches_shared_task_menu(qtbot, monkeypatch):
    from sp.app.ui.task_context_menu import build_task_context_menu

    window = _new_window(monkeypatch)
    qtbot.addWidget(window)
    window.editor.set_markdown("Introduction\n- [ ] Task\n")
    window._last_saved_content = window.editor.to_markdown()
    task_menu = window._task_context_menu(1, QPoint(10, 10))
    expected = build_task_context_menu(window, [{"task": {"status": "todo"}}], lambda command: None)
    assert [action.text() for action in task_menu.actions()] == [action.text() for action in expected.actions()]
    base_menu = QMenu(window)
    shown = []
    base_menu.exec = lambda *args: shown.append(base_menu)
    monkeypatch.setattr(window.editor, "createStandardContextMenu", lambda: base_menu)
    cursor = QTextCursor(window.editor.document().findBlockByNumber(1))
    window._show_editor_context_menu(window.editor.cursorRect(cursor).center())
    assert any(action.text() == "Task actions" for action in shown[0].actions())
    assert window._task_context_menu(0, QPoint(10, 10)) is None
    window.editor.setReadOnly(True)
    assert window._task_context_menu(1, QPoint(10, 10)) is None


@pytest.mark.parametrize("label,field", [("Edit Task", None), ("Edit Tags", "tags"), ("Move…", "destination")])
def test_page_editor_context_edit_uses_local_task_dialog(qtbot, monkeypatch, label, field):
    window = _new_window(monkeypatch)
    qtbot.addWidget(window)
    window.editor.set_markdown("Introduction\n- [ ] Task\n")
    window._last_saved_content = window.editor.to_markdown()
    edits = []
    monkeypatch.setattr(window, "_edit_task_from_page_editor", lambda *args, **kwargs: edits.append((args, kwargs)))
    anchor = QPoint(50, 50)
    menu = window._task_context_menu(1, anchor)
    next(action for action in menu.actions() if action.text().startswith(label)).trigger()
    assert edits == [((1, anchor), {"focus_field": field})]


def test_page_editor_failed_save_or_mutation_keeps_task_open(qtbot, monkeypatch):
    window = _new_window(monkeypatch)
    qtbot.addWidget(window)
    window.editor.set_markdown("- [ ] Unsaved task\n")
    monkeypatch.setattr(window, "_save_current_file", lambda **kwargs: None)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args))
    monkeypatch.setattr(window, "_api_post", lambda *args: pytest.fail("unsaved task mutated"))
    window._task_context_menu(0, QPoint(10, 10)).actions()[0].trigger()
    assert warnings
    assert window.editor.to_markdown() == "- [ ] Unsaved task\n"
    window._last_saved_content = window.editor.to_markdown()

    def fail(*args):
        raise ValueError("Task changed on disk")

    monkeypatch.setattr(window, "_api_post", fail)
    window._task_context_menu(0, QPoint(10, 10)).actions()[0].trigger()
    assert len(warnings) == 2
    assert window.editor.to_markdown() == "- [ ] Unsaved task\n"


@pytest.mark.parametrize("role,label", [("due", "Set Due Date"), ("start", "Set Start Date")])
def test_page_editor_task_dates_target_clicked_task_in_own_window(qtbot, monkeypatch, role, label):
    from sp.app.ui import task_panel

    window = _new_window(monkeypatch)
    qtbot.addWidget(window)
    window.editor.set_markdown("Introduction\n- [ ] Clicked task\n")
    window._last_saved_content = window.editor.to_markdown()
    updates = []
    monkeypatch.setattr(window, "_mutate_task_from_page_editor", lambda *args: updates.append(args))
    quick_menu = QMenu(window)
    quick_menu.exec = lambda *args: next(action for action in quick_menu.actions() if action.text() == "Today").trigger()
    monkeypatch.setattr(task_panel, "TaskDateQuickMenu", lambda parent, **kwargs: quick_menu)
    menu = window._task_context_menu(1, QPoint(10, 10))
    next(action for action in menu.actions() if action.text().startswith(label)).trigger()
    task, changes = updates[0]
    assert task["path"] == "/Page/Page.md"
    assert task["line"] == 2
    assert task["text"] == "Clicked task"
    assert changes == {role: date.today().isoformat()}


def test_page_editor_task_delete_requires_confirmation(qtbot, monkeypatch):
    window = _new_window(monkeypatch)
    qtbot.addWidget(window)
    window.editor.set_markdown("- [ ] Task\n")
    window._last_saved_content = window.editor.to_markdown()
    updates = []
    monkeypatch.setattr(window, "_mutate_task_from_page_editor", lambda *args: updates.append(args))
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.No)
    menu = window._task_context_menu(0, QPoint(10, 10))
    action = next(action for action in menu.actions() if action.text() == "Delete Task…")
    action.trigger()
    assert updates == []
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
    action.trigger()
    assert updates[0][1] == {"delete": True}


def test_page_editor_saves_unsaved_text_before_completion_without_main_reload(qtbot, monkeypatch):
    window = _new_window(monkeypatch, callback=lambda *args, **kwargs: pytest.fail("main reloaded"))
    qtbot.addWidget(window)
    window.editor.set_markdown("Unsaved introduction\n- [ ] Task\n")
    posts = []

    def post(path, payload):
        posts.append((path, payload))
        return SimpleNamespace(status_code=200, raise_for_status=lambda: None, json=lambda: {"undo_id": "undo-1"})

    monkeypatch.setattr(window, "_api_post", post)
    monkeypatch.setattr(window, "_load_content", lambda: pytest.fail("page reloaded"))
    window._task_context_menu(1, QPoint(10, 10)).actions()[0].trigger()
    assert [path for path, payload in posts] == ["/api/file/write", "/api/tasks/mutate"]
    assert posts[0][1]["content"] == "Unsaved introduction\n- [ ] Task\n"
    assert window.editor.to_markdown() == "Unsaved introduction\n- [x] Task\n"
    assert not window._is_dirty()
