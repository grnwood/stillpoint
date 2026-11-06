from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from sp.app.ui.jump_dialog import JumpToPageDialog


def test_jump_dialog_bookmark_mode_lists_only_allowed_paths(qapp, monkeypatch) -> None:
    def _fail_search_pages(_term: str):
        raise AssertionError("search_pages should not run when allowed_paths are provided")

    monkeypatch.setattr("sp.app.ui.jump_dialog.config.search_pages", _fail_search_pages)

    dlg = JumpToPageDialog(
        launch_mode="bookmarks",
        allowed_paths=[
            "/Projects/Alpha/Alpha.md",
            "/Projects/Beta/Beta.md",
        ],
    )
    try:
        assert dlg.windowTitle() == "Jump to Bookmark"
        assert dlg.list_widget.count() == 2
        paths = [dlg.list_widget.item(i).data(Qt.UserRole) for i in range(dlg.list_widget.count())]
        assert paths == ["/Projects/Alpha/Alpha.md", "/Projects/Beta/Beta.md"]
        assert not dlg.selected_is_folder_navigator()
    finally:
        dlg.close()


def test_jump_dialog_bookmark_mode_filters_by_search_term(qapp) -> None:
    dlg = JumpToPageDialog(
        launch_mode="bookmarks",
        allowed_paths=[
            "/Projects/Alpha/Alpha.md",
            "/Projects/Beta/Beta.md",
        ],
    )
    try:
        dlg.search.setText("beta")
        assert dlg.list_widget.count() == 1
        assert dlg.list_widget.item(0).data(Qt.UserRole) == "/Projects/Beta/Beta.md"
    finally:
        dlg.close()


def test_jump_dialog_arrow_keys_move_results_while_search_keeps_focus(qapp) -> None:
    dlg = JumpToPageDialog(
        launch_mode="bookmarks",
        allowed_paths=[
            "/Projects/Alpha/Alpha.md",
            "/Projects/Beta/Beta.md",
            "/Projects/Gamma/Gamma.md",
        ],
    )
    try:
        dlg.show()
        dlg.search.setFocus()
        dlg.list_widget.setCurrentRow(0)

        QTest.keyClick(dlg.search, Qt.Key_Down)
        assert dlg.list_widget.currentRow() == 1
        assert dlg.focusWidget() is dlg.search

        QTest.keyClick(dlg.search, Qt.Key_Up)
        assert dlg.list_widget.currentRow() == 0
        assert dlg.focusWidget() is dlg.search
    finally:
        dlg.close()


def test_jump_dialog_bookmark_mode_includes_folder_navigators(qapp, tmp_path) -> None:
    folder = tmp_path / "Working Copy"
    folder.mkdir()
    dlg = JumpToPageDialog(
        launch_mode="bookmarks",
        allowed_paths=["/Projects/Alpha/Alpha.md"],
        folder_paths=[str(folder)],
    )
    try:
        assert dlg.list_widget.count() == 2
        folder_item = dlg.list_widget.item(1)
        assert not folder_item.icon().isNull()
        assert folder_item.data(Qt.UserRole) == str(folder)
        dlg.list_widget.setCurrentItem(folder_item)
        assert dlg.selected_is_folder_navigator()
        assert dlg.selected_path() == str(folder)

        dlg.search.setText("working")
        assert dlg.list_widget.count() == 1
        assert dlg.list_widget.item(0).data(Qt.UserRole) == str(folder)
    finally:
        dlg.close()


def test_jump_dialog_bookmark_mode_with_only_folder_navigators(qapp, tmp_path, monkeypatch) -> None:
    folder = tmp_path / "Working Copy"
    folder.mkdir()
    monkeypatch.setattr(
        "sp.app.ui.jump_dialog.config.search_pages",
        lambda _term: (_ for _ in ()).throw(AssertionError("unexpected page search")),
    )
    dlg = JumpToPageDialog(launch_mode="bookmarks", folder_paths=[str(folder)])
    try:
        assert dlg.list_widget.count() == 1
        assert dlg.selected_is_folder_navigator()
    finally:
        dlg.close()


def test_move_text_always_offers_child_page_under_current_journal_page(qapp, monkeypatch) -> None:
    monkeypatch.setattr(
        "sp.app.ui.jump_dialog.config.search_pages",
        lambda _term: [
            {"path": "/Topics/Compliance calls/Compliance calls.md", "title": "Compliance calls"},
        ],
    )
    dlg = JumpToPageDialog(
        launch_mode="move_text",
        current_page_path="/Journal/2026/11/02/02.md",
    )
    try:
        dlg.search.setText("Compliance calls")

        assert dlg.list_widget.count() == 2
        create_item = dlg.list_widget.item(1)
        assert "Create child page" in create_item.text()
        assert "under Journal:2026:11:02" in create_item.text()
        assert create_item.data(Qt.UserRole) == (
            "/Journal/2026/11/02/Compliance calls/Compliance calls.md"
        )
    finally:
        dlg.close()


def test_move_text_does_not_offer_create_when_exact_child_exists(qapp, monkeypatch) -> None:
    child_path = "/Journal/2026/11/02/Compliance calls/Compliance calls.md"
    monkeypatch.setattr(
        "sp.app.ui.jump_dialog.config.search_pages",
        lambda _term: [{"path": child_path, "title": "Compliance calls"}],
    )
    dlg = JumpToPageDialog(
        launch_mode="move_text",
        current_page_path="/Journal/2026/11/02/02.md",
    )
    try:
        dlg.search.setText("Compliance calls")

        assert dlg.list_widget.count() == 1
        assert dlg.list_widget.item(0).data(Qt.UserRole) == child_path
        assert "Create child page" not in dlg.list_widget.item(0).text()
    finally:
        dlg.close()
