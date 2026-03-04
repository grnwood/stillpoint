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
