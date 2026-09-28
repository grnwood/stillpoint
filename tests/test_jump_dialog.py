from __future__ import annotations

from PySide6.QtCore import Qt

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
