from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QTreeWidgetItem

from sp.app.ui.task_panel import TaskDateQuickMenu, TaskPanel


def _add_task(
    panel: TaskPanel,
    *,
    status: str = "todo",
    line: int = 3,
    text: str = "Call Sarah",
) -> dict:
    task = {
        "id": f"/Page/Page.md:{line}",
        "path": "/Page/Page.md",
        "line": line,
        "text": text,
        "status": status,
        "priority": 0,
        "tags": [],
    }
    item = QTreeWidgetItem(["", text, ""])
    item.setData(0, Qt.UserRole, task)
    panel.task_tree.addTopLevelItem(item)
    panel.task_tree.setCurrentItem(item)
    item.setSelected(True)
    return task


def test_space_toggles_selected_task_from_keyboard(qtbot, monkeypatch) -> None:
    panel = TaskPanel()
    qtbot.addWidget(panel)
    task = _add_task(panel)
    calls: list[tuple[list[dict], dict]] = []
    monkeypatch.setattr(
        panel,
        "_apply_task_mutation",
        lambda tasks, **changes: calls.append((tasks, changes)) or True,
    )

    QTest.keyClick(panel.task_tree, Qt.Key_Space)

    assert calls == [([task], {"status": "done"})]


def test_e_opens_keyboard_editor(qtbot, monkeypatch) -> None:
    panel = TaskPanel()
    qtbot.addWidget(panel)
    _add_task(panel)
    opened: list[bool] = []
    monkeypatch.setattr(panel, "_open_task_editor", lambda **_kwargs: opened.append(True))

    QTest.keyClick(panel.task_tree, Qt.Key_E)

    assert opened == [True]


def test_m_opens_full_editor_with_destination_focused(qtbot, monkeypatch) -> None:
    panel = TaskPanel()
    qtbot.addWidget(panel)
    _add_task(panel)
    opened: list[dict] = []
    monkeypatch.setattr(panel, "_open_task_editor", lambda **kwargs: opened.append(kwargs))

    QTest.keyClick(panel.task_tree, Qt.Key_M)

    assert opened == [{"focus_field": "destination"}]


def test_r_removes_selected_task_indicators(qtbot, monkeypatch) -> None:
    panel = TaskPanel()
    qtbot.addWidget(panel)
    task = _add_task(panel)
    calls: list[tuple[list[dict], dict]] = []
    monkeypatch.setattr(
        panel,
        "_apply_task_mutation",
        lambda tasks, **changes: calls.append((tasks, changes)) or True,
    )

    QTest.keyClick(panel.task_tree, Qt.Key_R)

    assert calls == [([task], {"remove_indicators": True})]


def test_r_advances_to_next_task_and_restores_tree_focus(qtbot, monkeypatch) -> None:
    panel = TaskPanel()
    qtbot.addWidget(panel)
    first = _add_task(panel, line=3, text="First")
    second = _add_task(panel, line=4, text="Second")
    first_item = panel.task_tree.topLevelItem(0)
    second_item = panel.task_tree.topLevelItem(1)
    panel.task_tree.clearSelection()
    panel.task_tree.setCurrentItem(first_item)
    first_item.setSelected(True)
    monkeypatch.setattr(panel, "_apply_task_mutation", lambda *_args, **_kwargs: True)

    QTest.keyClick(panel.task_tree, Qt.Key_R)

    assert panel._focus_after_removed_task == second
    assert panel._selected_task_data() == [first]

    panel.task_tree.takeTopLevelItem(0)
    focus_calls: list[object] = []
    monkeypatch.setattr(panel.task_tree, "setFocus", lambda reason: focus_calls.append(reason))
    panel._restore_focus_after_removed_task({second["id"]: second_item})

    assert panel.task_tree.currentItem() is second_item
    assert focus_calls == [Qt.OtherFocusReason]
    assert panel._focus_after_removed_task is None


def test_task_context_menu_offers_remove_indicators(qtbot) -> None:
    panel = TaskPanel()
    qtbot.addWidget(panel)
    task = _add_task(panel)

    menu = panel._build_task_context_menu([{"task": task}])
    labels = [action.text() for action in menu.actions()]

    assert "Remove Task Indicators (R)" in labels


def test_triage_mode_reuses_task_tree(qtbot, monkeypatch) -> None:
    panel = TaskPanel()
    qtbot.addWidget(panel)
    monkeypatch.setattr(
        panel,
        "_fetch_triage_items",
        lambda: [
            {
                "id": "capture-1",
                "path": "/Journal/2026/08/15/15.md",
                "text": "An idea",
                "expected_hash": "hash",
                "kind": "capture",
            }
        ],
    )

    panel._triage_btn.setChecked(True)

    assert panel._triage_mode is True
    assert panel.task_tree.topLevelItemCount() == 1
    assert panel.task_tree.headerItem().text(1) == "Capture"
    assert panel._triage_btn.text() == "Triage (1)"


def test_destination_search_uses_full_index_when_tasks_are_filtered(qtbot) -> None:
    calls: list[tuple[str, dict]] = []

    class Response:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {
                "pages": [
                    {"path": "/Outside/Launch/Launch.md", "title": "Launch"},
                ]
            }

    class Client:
        def get(self, path: str, *, params: dict):
            calls.append((path, params))
            return Response()

    panel = TaskPanel()
    qtbot.addWidget(panel)
    panel._http_client = Client()
    panel._nav_filter_prefix = "/Inside"

    results = panel._search_destination_pages("launch")

    assert calls == [("/api/pages/search", {"q": "launch", "limit": 40})]
    assert results == [":Outside:Launch"]


def test_date_quick_menu_supports_plain_and_chord_vi_navigation(qtbot) -> None:
    menu = TaskDateQuickMenu(use_vi_keys=True)
    qtbot.addWidget(menu)
    today = menu.addAction("Today")
    tomorrow = menu.addAction("Tomorrow")
    menu.addSeparator()
    next_week = menu.addAction("Next Week")

    assert menu.activeAction() is None
    QTest.keyClick(menu, Qt.Key_J)
    assert menu.activeAction() is today
    QTest.keyClick(menu, Qt.Key_J, Qt.ControlModifier | Qt.ShiftModifier)
    assert menu.activeAction() is tomorrow
    QTest.keyClick(menu, Qt.Key_K, Qt.ControlModifier | Qt.ShiftModifier)
    assert menu.activeAction() is today
    QTest.keyClick(menu, Qt.Key_K)
    assert menu.activeAction() is next_week


def test_editor_tag_candidates_include_vault_wide_tags(qtbot) -> None:
    class Response:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {"tags": [{"tag": "outside"}, {"tag": "work"}]}

    class Client:
        def get(self, path: str):
            assert path == "/tags"
            return Response()

    panel = TaskPanel()
    qtbot.addWidget(panel)
    panel._http_client = Client()
    panel._available_tags = {"inside"}
    panel._nav_filter_prefix = "/Inside"

    assert panel._known_tags_for_editor() == ["inside", "outside", "work"]
