from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QDialog

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
