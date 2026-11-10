from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt, QMimeData
from PySide6.QtGui import QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from sp.app import config
from sp.server import file_ops
from sp.server.adapters.files import FileAccessError


def test_copy_page_subtree_preserves_content_and_avoids_overwrite(tmp_path, monkeypatch):
    entries = []
    monkeypatch.setattr(config, "ensure_page_entry", entries.append)
    monkeypatch.setattr(config, "bump_tree_version", lambda: 1)
    source = tmp_path / "Notes"
    source.mkdir()
    (source / "Notes.md").write_text("# Notes\nOriginal")
    (source / "image.png").write_bytes(b"image")
    child = source / "Child"
    child.mkdir()
    (child / "Child.md").write_text("child")
    result = file_ops.copy_folder(tmp_path, "/Notes", "/")
    assert result["path"] == "/Notes-Copy"
    assert (tmp_path / "Notes-Copy/Notes-Copy.md").read_text() == "# Notes\nOriginal"
    assert (tmp_path / "Notes-Copy/image.png").read_bytes() == b"image"
    assert (tmp_path / "Notes-Copy/Child/Child.md").read_text() == "child"
    assert "/Notes-Copy/Notes-Copy.md" in entries
    assert file_ops.copy_folder(tmp_path, "/Notes", "/")["path"] == "/Notes-Copy-2"
    with pytest.raises(FileAccessError):
        file_ops.copy_folder(tmp_path, "/Notes", "/Notes/Child")
    with pytest.raises(FileAccessError):
        file_ops.copy_folder(tmp_path, "/Notes", "/../outside")


def test_tree_copy_paste_shortcuts_are_local_to_trees(qapp, monkeypatch):
    from sp.app.ui.main_window import VaultTreeView
    from sp.app.folder_navigator.window import NavigatorTree
    for tree_type in (VaultTreeView, NavigatorTree):
        tree = tree_type()
        events = []
        tree.copyRequested.connect(lambda: events.append("copy"))
        tree.pasteRequested.connect(lambda: events.append("paste"))
        tree.deleteRequested.connect(lambda: events.append("delete"))
        try:
            QTest.keySequence(tree, QKeySequence(QKeySequence.Copy))
            QTest.keySequence(tree, QKeySequence(QKeySequence.Paste))
            QTest.keyClick(tree, Qt.Key_Delete)
            assert events == ["copy", "paste", "delete"]
            with monkeypatch.context() as mac:
                mac.setattr("sys.platform", "darwin")
                QTest.keyClick(tree, Qt.Key_Backspace)
            assert events[-1] == "delete"
            assert len(events) == 4
        finally:
            tree.close()


def test_vault_page_clipboard_pastes_into_selected_page(main_window, monkeypatch):
    from sp.app.ui.main_window import PATH_ROLE
    source_index = main_window.tree_model.index(0, 0)
    assert source_index.isValid()
    path = source_index.data(PATH_ROLE)
    if path == "/":
        source_index = main_window.tree_model.index(0, 0, source_index)
        path = source_index.data(PATH_ROLE)
    assert path and path != "/"
    main_window.tree_view.setCurrentIndex(source_index)
    main_window._copy_selected_vault_page()
    from sp.app.ui.path_utils import ensure_root_colon_link, path_to_colon
    assert main_window.statusBar().currentMessage() == f"Copied {ensure_root_colon_link(path_to_colon(path))}"
    calls = []
    monkeypatch.setattr(main_window.http, "post", lambda url, json: (
        calls.append((url, json)) or SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"path": "/Copy"})
    ))
    monkeypatch.setattr(main_window, "_refresh_tree", lambda: None)
    main_window._paste_selected_vault_page()
    assert main_window._pending_selection == "/Copy"
    assert main_window.tree_view.hasFocus()
    assert main_window.statusBar().currentMessage() == "Pasted :Copy"
    assert calls == [("/api/file/copy", {"from": path, "to": str(Path(path).parent)})]
    main_window._read_only = True
    monkeypatch.setattr(main_window, "_alert", lambda message: None)
    main_window._paste_selected_vault_page()
    assert len(calls) == 1
    QApplication.clipboard().clear()


def test_folder_tree_keyboard_copies_and_pastes_selected_file(qapp, tmp_path, monkeypatch):
    from sp.app.folder_navigator.window import Window
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    source = tmp_path / "notes.txt"
    source.write_text("hello")
    window = Window(tmp_path)
    monkeypatch.setattr(window.executor, "submit", lambda job, *args: job(*args))
    window.show()
    QTest.qWait(50)
    try:
        index = window.model.index(str(source))
        window.tree.setCurrentIndex(index)
        QTest.keySequence(window.tree, QKeySequence(QKeySequence.Copy))
        assert "Copied 1 item(s)" in window.statusBar().currentMessage()
        QTest.keySequence(window.tree, QKeySequence(QKeySequence.Paste))
        qapp.processEvents()
        assert "Pasted 1 item(s)" in window.statusBar().currentMessage()
        assert (tmp_path / "notes-Copy.txt").read_text() == "hello"
        assert source.read_text() == "hello"
        QTest.qWait(150)
        assert window.model.filePath(window.tree.currentIndex()) == str(tmp_path / "notes-Copy.txt")
        assert window.tree.hasFocus()
    finally:
        QApplication.clipboard().clear()
        window.close()


def test_vault_context_menu_copy_paste_targets_clicked_page(main_window, monkeypatch):
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QMenu
    from sp.app.ui.main_window import PATH_ROLE

    index = main_window.tree_model.index(0, 0)
    if index.data(PATH_ROLE) == "/":
        index = main_window.tree_model.index(0, 0, index)
    path = index.data(PATH_ROLE)
    assert path and path != "/"
    monkeypatch.setattr(main_window.tree_view, "indexAt", lambda pos: index)
    copied = []
    pasted = []
    monkeypatch.setattr(main_window, "_copy_vault_page", copied.append)
    monkeypatch.setattr(main_window, "_paste_vault_page", pasted.append)
    mime = QMimeData()
    mime.setData("application/x-stillpoint-page-copy", b"{}")
    QApplication.clipboard().setMimeData(mime)
    menus = []

    def inspect_menu():
        menu = QApplication.activePopupWidget()
        actions = {action.text(): action for action in menu.actions()}
        assert "Copy Page" in actions
        assert "Paste Page" in actions
        assert actions["Paste Page"].isEnabled() == (not main_window._read_only)
        actions["Copy Page"].trigger()
        if actions["Paste Page"].isEnabled():
            actions["Paste Page"].trigger()
        menus.append(menu)
        menu.close()

    from PySide6.QtCore import QTimer
    QTimer.singleShot(0, inspect_menu)
    main_window._open_context_menu(QPoint())
    assert copied == [path]
    assert pasted == [path]
    main_window._read_only = True
    QTimer.singleShot(0, inspect_menu)
    main_window._open_context_menu(QPoint())
    assert pasted == [path]
    QApplication.clipboard().clear()


def test_delete_key_routes_selected_items_to_existing_delete_handlers(qapp):
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QStandardItem, QStandardItemModel
    from sp.app.ui.main_window import MainWindow, PATH_ROLE, OPEN_ROLE
    from sp.app.folder_navigator.window import Window

    model = QStandardItemModel()
    item = QStandardItem("Notes")
    item.setData("/Notes", PATH_ROLE)
    item.setData("/Notes/Notes.md", OPEN_ROLE)
    model.appendRow(item)
    deleted = []
    index = item.index()
    tree = SimpleNamespace(currentIndex=lambda: index, visualRect=lambda idx: QRect(0, 0, 50, 20),
                           viewport=lambda: SimpleNamespace(mapToGlobal=lambda point: point))
    host = SimpleNamespace(tree_view=tree, _delete_path=lambda *args: deleted.append(args))
    MainWindow._delete_selected_vault_page(host)
    assert deleted[0][:2] == ("/Notes", "/Notes/Notes.md")
    item.setData("/", PATH_ROLE)
    MainWindow._delete_selected_vault_page(host)
    assert len(deleted) == 1

    selected = [Path("/notes.txt"), Path("/other.txt")]
    host = SimpleNamespace(tree=tree, model=SimpleNamespace(isDir=lambda idx: False),
                           _selected_files_for_context=lambda idx: selected,
                           _delete_files=lambda paths: deleted.append(paths))
    Window._delete_selected_tree_files(host)
    assert deleted[-1] == selected
