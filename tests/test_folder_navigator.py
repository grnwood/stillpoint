from pathlib import Path
import os
import subprocess

import pytest

from sp.app.folder_navigator.core import (ConflictError, atomic_save, content_matches,
    fuzzy_score, inside, read_text, walk_files)


def test_symlink_boundary_and_cancel(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "inside.txt").write_text("hello")
    (outside / "secret.txt").write_text("secret")
    (root / "escape").symlink_to(outside, target_is_directory=True)
    (root / "file-link").symlink_to(outside / "secret.txt")
    assert not inside(root, root / "escape" / "secret.txt")
    assert not inside(root, root / "file-link")
    assert list(walk_files(root, root)) == [root / "inside.txt"]
    assert list(walk_files(root, root, canceled=lambda: True)) == []


def test_atomic_save_detects_changes_and_preserves_newlines(tmp_path):
    path = tmp_path / "notes.md"
    path.write_bytes(b"one\r\ntwo\r\n")
    loaded = read_text(path)
    path.write_bytes(b"someone else\r\n")
    with pytest.raises(ConflictError):
        atomic_save(path, "mine\n", loaded)
    assert path.read_bytes() == b"someone else\r\n"
    result = atomic_save(path, "mine\n", loaded, overwrite=True)
    assert path.read_bytes() == b"mine\r\n"
    assert result == read_text(path).fingerprint


def test_binary_and_invalid_encoding_are_not_editable(tmp_path):
    path = tmp_path / "source.txt"
    for data in (b"hello\x00world", b"\xff\xfe\xff"):
        path.write_bytes(data)
        with pytest.raises(ValueError):
            read_text(path)


def test_utf16_round_trip(tmp_path):
    path = tmp_path / "wide.txt"
    path.write_bytes("first\r\nsecond\r\n".encode("utf-16"))
    loaded = read_text(path)
    atomic_save(path, "first\nupdated\n", loaded)
    assert path.read_bytes().decode("utf-16") == "first\r\nupdated\r\n"


def test_quick_open_ranking_and_content_matching():
    assert fuzzy_score("read", "README.md") > fuzzy_score("read", "docs/other-readme.md")
    assert fuzzy_score("main", "src/main.py", opened=True) > fuzzy_score("main", "src/main.py")
    assert fuzzy_score("xyz", "src/main.py") is None
    assert list(content_matches("A Cat\ncatfish", "cat", whole=True)) == [(1, "A Cat")]


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_preview_pinning_and_stale_restore(tmp_path, monkeypatch, app):
    from sp.app.folder_navigator.window import Window
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    for name in ("a.md", "b.md", "c.md"):
        (tmp_path / name).write_text(name)
    window = Window(tmp_path)
    window.open_file(tmp_path / "a.md")
    window.open_file(tmp_path / "b.md")
    assert [tab.path.name for tab in window.all_tabs()] == ["b.md"]
    window.keep_open(0)
    window.open_file(tmp_path / "c.md")
    assert [tab.path.name for tab in window.all_tabs()] == ["b.md", "c.md"]
    window.active_tab().editor.insertPlainText("changed")
    assert window.active_tab().pinned and window.active_tab().dirty
    window.open_file(tmp_path / "a.md")
    assert len(window.all_tabs()) == 3
    window.active_tab().editor.document().setModified(False)
    for tab in window.all_tabs():
        tab.editor.document().setModified(False)
    window.close()


def test_child_process_is_detached(tmp_path, monkeypatch):
    from sp.app.folder_navigator import launch
    captured = {}
    def fake_popen(command, **kwargs):
        captured.update(command=command, kwargs=kwargs)
        return object()
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    launch.launch(tmp_path)
    assert captured["command"][1:3] == ["-m", "sp.app.folder_navigator"]
    source_root = str(Path(launch.__file__).resolve().parents[3])
    assert captured["kwargs"]["env"]["PYTHONPATH"].split(os.pathsep)[0] == source_root
    if os.name != "nt":
        assert captured["kwargs"]["start_new_session"]
    monkeypatch.setattr(__import__("sys"), "frozen", True, raising=False)
    launch.launch(tmp_path)
    assert captured["command"][1] == "--folder-navigator"


def test_picker_shares_live_model_and_filter(tmp_path, monkeypatch, app):
    from sp.app.folder_navigator.window import Picker, Window
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    folder = tmp_path / "nested"
    folder.mkdir()
    (folder / "test.txt").write_text("test")
    window = Window(tmp_path)
    window.apply_filter(folder)
    picker = Picker(window)
    assert picker.tree.model() is window.tree.model()
    assert Path(window.model.filePath(picker.tree.rootIndex())) == folder
    picker.close()
    window.close()


def test_stale_tabs_omitted_and_quick_open_invalidates(tmp_path, monkeypatch, app):
    import json
    from sp.app.folder_navigator.window import Window
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    missing = tmp_path / "deleted.txt"
    settings = {str(tmp_path.resolve()): {"pinned": [str(missing)]}}
    (tmp_path / ".stillpoint_folder_navigator.json").write_text(json.dumps(settings))
    window = Window(tmp_path)
    assert window.tabs.count() == 0
    window.catalog.add(missing)
    window._refresh_disk()
    assert missing not in window.catalog
    window.close()


def test_search_limit_and_cancel(tmp_path, monkeypatch, app):
    import time
    from PySide6.QtCore import Qt
    import sp.app.folder_navigator.window as module
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(module, "MAX_RESULTS", 3)
    for index in range(8):
        (tmp_path / f"hit-{index}.txt").write_text("hit\n")
    window = module.Window(tmp_path)
    window.search_input.setText("hit")
    window.run_search()
    deadline = time.monotonic() + 3
    while "Complete" not in window.search_progress.text() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    assert sum(bool(window.search_results.item(i).data(Qt.UserRole))
               for i in range(window.search_results.count())) == 3
    assert "limit 3 reached" in window.search_progress.text()
    window.search_input.setText("other")
    window.run_search()
    window.search_cancel.set()
    deadline = time.monotonic() + 3
    while "Canceled" not in window.search_progress.text() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    assert "Canceled" in window.search_progress.text()
    window.close()


def test_image_preview_and_outside_bookmark(tmp_path, monkeypatch, app):
    import time
    from PySide6.QtGui import QImage
    from sp.app.folder_navigator.window import ImageView, Window
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    root = tmp_path / "root"
    root.mkdir()
    image = QImage(12, 18, QImage.Format_ARGB32)
    image.fill(0xff223344)
    assert image.save(str(root / "test.png"))
    window = Window(root)
    window.open_file(root / "test.png")
    deadline = time.monotonic() + 2
    while not window.active_tab().findChild(ImageView) and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    assert window.active_tab().findChild(ImageView)
    window.toggle_bookmark(tmp_path)
    assert not window._bookmarks()
    window.close()
