from pathlib import Path
import os
import subprocess

import pytest

from sp.app.folder_navigator.core import (ConflictError, atomic_save, content_matches,
    fuzzy_score, inside, read_text, walk_files)
from sp.app.folder_navigator.catalog import (
    CATALOG_DIRECTORY, CATALOG_FILENAME, FolderCatalog,
)


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
    monkeypatch.setattr(
        "sp.app.config.load_effective_theme_preference",
        lambda: "midnight-blue.json",
    )
    launch.launch(tmp_path)
    assert captured["command"][1:3] == ["-m", "sp.app.folder_navigator"]
    assert captured["kwargs"]["env"]["SP_THEME_OVERRIDE"] == "midnight-blue.json"
    source_root = str(Path(launch.__file__).resolve().parents[3])
    assert captured["kwargs"]["env"]["PYTHONPATH"].split(os.pathsep)[0] == source_root
    if os.name != "nt":
        assert captured["kwargs"]["start_new_session"]
    monkeypatch.setattr(__import__("sys"), "frozen", True, raising=False)
    launch.launch(tmp_path)
    assert captured["command"][1] == "--folder-navigator"


def test_frozen_macos_launch_uses_companion_app(tmp_path, monkeypatch):
    from sp.app.folder_navigator import launch

    main_executable = tmp_path / "StillPoint.app" / "Contents" / "MacOS" / "StillPoint"
    main_executable.parent.mkdir(parents=True)
    main_executable.touch()
    companion = tmp_path / "StillPoint Folder Navigator.app"
    companion.mkdir()
    root = tmp_path / "folder"
    root.mkdir()
    captured = {}

    monkeypatch.setattr(subprocess, "Popen", lambda command, **kwargs: captured.update(
        command=command, kwargs=kwargs
    ))
    monkeypatch.setattr(launch.sys, "frozen", True, raising=False)
    monkeypatch.setattr(launch.sys, "platform", "darwin")
    monkeypatch.setattr(launch.sys, "executable", str(main_executable))

    launch.launch(root)

    assert captured["command"] == [
        "open", "-na", str(companion), "--args", str(root.resolve())
    ]


def test_sqlite_catalog_is_persistent_scoped_and_excludes_metadata(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    visible = root / "notes.md"
    visible.write_text("notes")
    hidden = root / ".private.md"
    hidden.write_text("private")
    nested = root / "projects" / "roadmap.md"
    nested.parent.mkdir()
    nested.write_text("roadmap")

    catalog = FolderCatalog(root)
    generation = catalog.begin_refresh()
    assert catalog.upsert_paths([visible, hidden, nested], {nested}, generation) == 3
    catalog.finish_refresh(generation, complete=True)

    assert catalog.path == root / CATALOG_DIRECTORY / CATALOG_FILENAME
    assert catalog.path.is_file()
    assert catalog.count() == 3
    assert catalog.candidates("note", root) == [visible]
    assert catalog.candidates("road", nested.parent) == []
    assert catalog.candidates(
        "road", nested.parent, include_excluded=True
    ) == [nested]
    assert hidden in catalog.candidates("private", root, include_excluded=True)
    assert catalog.count() == FolderCatalog(root).count()
    assert catalog.path not in list(walk_files(root, root, hidden=True))


def test_sqlite_catalog_only_prunes_after_complete_refresh(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    first = root / "first.txt"
    second = root / "second.txt"
    first.write_text("first")
    second.write_text("second")
    catalog = FolderCatalog(root)

    generation = catalog.begin_refresh()
    catalog.upsert_paths([first, second], generation=generation)
    catalog.finish_refresh(generation, complete=True)

    generation = catalog.begin_refresh()
    catalog.upsert_paths([first], generation=generation)
    catalog.finish_refresh(generation, complete=False)
    assert set(catalog.candidates("", root, include_excluded=True)) == {first, second}

    generation = catalog.begin_refresh()
    catalog.upsert_paths([first], generation=generation)
    catalog.finish_refresh(generation, complete=True)
    assert catalog.candidates("", root, include_excluded=True) == [first]


def test_catalog_persists_layout_and_large_directory_notices(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    crowded = root / "crowded"
    crowded.mkdir()
    catalog = FolderCatalog(root)
    generation = catalog.begin_refresh()
    catalog.record_skipped_directories([(crowded, 901)], generation)
    catalog.set_ui_states({"tree_header": "header-state", "sort_column": "1"})
    catalog.finish_refresh(generation, complete=True)

    reopened = FolderCatalog(root)
    assert reopened.ui_states()["tree_header"] == "header-state"
    assert reopened.ui_states()["sort_column"] == "1"
    assert reopened.skipped_directories() == [(crowded, 901)]


def test_walk_files_skips_overfull_directories(tmp_path):
    crowded = tmp_path / "crowded"
    crowded.mkdir()
    for index in range(5):
        (crowded / f"{index}.txt").write_text(str(index))
    skipped = []
    paths = list(walk_files(
        tmp_path,
        tmp_path,
        hidden=True,
        max_directory_entries=3,
        skipped=lambda path, count: skipped.append((path, count)),
    ))
    assert paths == []
    assert skipped == [(crowded, 5)]


def test_tree_columns_and_sort_persist_in_sqlite(tmp_path, monkeypatch, app):
    from PySide6.QtCore import Qt
    from sp.app.folder_navigator.window import Window
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    root = tmp_path / "root"
    root.mkdir()

    window = Window(root)
    window.tree.setColumnWidth(1, 177)
    window.tree.setColumnHidden(2, True)
    window.tree.sortByColumn(1, Qt.DescendingOrder)
    window._persist_sqlite_layout()
    window.close()

    restored = Window(root)
    assert restored.tree.columnWidth(1) == 177
    assert restored.tree.isColumnHidden(2)
    assert restored.tree.header().sortIndicatorSection() == 1
    assert restored.tree.header().sortIndicatorOrder() == Qt.DescendingOrder
    restored.close()


def test_ctrl_tab_cycles_all_open_tabs(tmp_path, monkeypatch, app):
    from sp.app.folder_navigator.window import Window
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    paths = [tmp_path / name for name in ("a.txt", "b.txt", "c.txt")]
    for path in paths:
        path.write_text(path.name)
    window = Window(tmp_path)
    for path in paths:
        window.open_file(path, pinned=True)

    assert window.active_tab().path == paths[2]
    window.cycle_tab(1)
    assert window.active_tab().path == paths[1]
    window.cycle_tab(1)
    assert window.active_tab().path == paths[0]
    window.cycle_tab(1)
    assert window.active_tab().path == paths[2]
    window.close()


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


def test_markdown_tabs_use_stillpoint_editor_and_heading_picker(tmp_path, monkeypatch, app):
    from sp.app.folder_navigator.window import Window
    from sp.app.ui.markdown_editor import MarkdownEditor

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    page = tmp_path / "notes.md"
    page.write_text("# First\n\ntext\n\n## Second\n", encoding="utf-8")
    window = Window(tmp_path)
    window.open_file(page)
    tab = window.active_tab()
    assert isinstance(tab.editor, MarkdownEditor)
    assert tab.text_for_save().startswith("# First")
    window._reveal_editor_line(tab, 5)
    assert tab.editor.textCursor().blockNumber() == 4
    tab.editor.document().setModified(False)
    window.close()


def test_heading_picker_supports_platform_vi_navigation_chord(app):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from sp.app.folder_navigator.window import HeadingPicker

    picker = HeadingPicker([
        (1, "First", 1),
        (2, "Second", 5),
        (2, "Third", 9),
    ])
    picker.show()
    picker.query.setFocus()
    assert picker.results.currentRow() == 0

    QTest.keyClick(picker.query, Qt.Key_J, Qt.ControlModifier | Qt.ShiftModifier)
    assert picker.results.currentRow() == 1
    QTest.keyClick(picker.query, Qt.Key_K, Qt.ControlModifier | Qt.ShiftModifier)
    assert picker.results.currentRow() == 0

    picker.results.setFocus()
    QTest.keyClick(picker.results, Qt.Key_J, Qt.ControlModifier | Qt.ShiftModifier)
    assert picker.results.currentRow() == 1
    picker.reject()


def test_source_tabs_use_pygments_and_global_vi_setting(tmp_path, monkeypatch, app):
    import sp.app.folder_navigator.editors as editors
    from sp.app.folder_navigator.window import Window

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(editors.config, "load_vi_mode_enabled", lambda: True)
    source = tmp_path / "sample.py"
    source.write_text("def answer():\n    return 42\n", encoding="utf-8")
    window = Window(tmp_path)
    window.open_file(source)
    editor = window.active_tab().editor
    assert isinstance(editor, editors.SourceEditor)
    assert editor.syntax_highlighter.lexer.name == "Python"
    assert editor._vi_feature_enabled is True
    editor.document().setModified(False)
    window.close()


def test_search_result_click_reveals_line_in_existing_tab(tmp_path, monkeypatch, app):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QListWidgetItem
    from sp.app.folder_navigator.window import Window

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    source = tmp_path / "sample.txt"
    source.write_text("one\ntwo\nthree\n", encoding="utf-8")
    window = Window(tmp_path)
    window.open_file(source)
    item = QListWidgetItem("Line 3: three")
    item.setData(Qt.UserRole, (source, 3))
    window._open_search_item(item)
    assert window.active_tab().editor.textCursor().blockNumber() == 2
    assert window.active_tab().editor.extraSelections()
    window.active_tab().editor.document().setModified(False)
    window.close()


def test_filter_chicklet_and_remove_action_stay_in_sync(tmp_path, monkeypatch, app):
    from sp.app.folder_navigator.window import Window

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    nested = tmp_path / "nested"
    nested.mkdir()
    window = Window(tmp_path)
    window.apply_filter(nested)
    assert not window.filter_label.isHidden()
    assert "×" in window.filter_label.text()
    assert window.clear_filter_action.isEnabled()
    window.clear_filter_action.trigger()
    assert window.scope == window.root
    assert not window.clear_filter_action.isEnabled()
    window.close()


def test_image_preview_honors_orientation_and_fits(tmp_path, monkeypatch, app):
    import time
    from PIL import Image
    from sp.app.folder_navigator.window import ImageView, Window

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    root = tmp_path / "root"
    root.mkdir()
    image_path = root / "rotated.jpg"
    image = Image.new("RGB", (2000, 1000), "navy")
    exif = Image.Exif()
    exif[274] = 6
    image.save(image_path, exif=exif)
    window = Window(root)
    window.show()
    window.open_file(image_path)
    deadline = time.monotonic() + 3
    view = None
    while view is None and time.monotonic() < deadline:
        app.processEvents()
        view = window.active_tab().findChild(ImageView)
    assert view is not None
    app.processEvents()
    assert (view.original.width(), view.original.height()) == (1000, 2000)
    assert view.zoom < 1.0
    window.close()


def test_tree_shift_enter_focuses_editor_and_applies_vi_cursor_style(tmp_path, monkeypatch, app):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    import sp.app.folder_navigator.editors as editors
    from sp.app.folder_navigator.window import Window

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(editors.config, "load_vi_mode_enabled", lambda: True)
    monkeypatch.setattr(editors.config, "load_vi_cursor_style", lambda: "line")
    source = tmp_path / "sample.py"
    source.write_text("print('focused')\n", encoding="utf-8")
    window = Window(tmp_path)
    window.show()
    index = window.model.index(str(source))
    window.tree.setCurrentIndex(index)
    window.tree.setFocus()

    QTest.keyClick(window.tree, Qt.Key_Return, Qt.ShiftModifier)
    app.processEvents()

    editor = window.active_tab().editor
    assert editor.hasFocus()
    assert editor._vi_cursor_style == "line"
    assert editor.cursorWidth() == 2
    editor.document().setModified(False)
    window.close()


def test_markdown_tree_preview_and_plain_enter_keep_folder_focus(tmp_path, monkeypatch, app):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    import sp.app.folder_navigator.editors as editors
    from sp.app.folder_navigator.window import Window

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(editors.config, "load_vi_mode_enabled", lambda: True)
    first = tmp_path / "first.md"
    second = tmp_path / "second.md"
    first.write_text("# First\n", encoding="utf-8")
    second.write_text("# Second\n", encoding="utf-8")
    window = Window(tmp_path)
    window.show()
    window.tree.setFocus()

    for path in (first, second):
        index = window.model.index(str(path))
        window.tree.setCurrentIndex(index)
        app.processEvents()
        assert window.tree.hasFocus()
        assert window.active_tab().path == path
        assert not window.active_tab().dirty

    QTest.keyClick(window.tree, Qt.Key_Return)
    app.processEvents()
    assert window.tree.hasFocus()
    assert window.active_tab().pinned
    assert window.tabs.count() == 1
    for tab in window.all_tabs():
        tab.editor.document().setModified(False)
    window.close()


def test_folder_navigator_uses_distinct_application_icon(tmp_path, monkeypatch, app):
    from sp.app.folder_navigator.icon import get_folder_navigator_icon
    from sp.app.folder_navigator.window import Window

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    icon = get_folder_navigator_icon()
    window = Window(tmp_path)

    assert not icon.isNull()
    assert not get_folder_navigator_icon().isNull()
    assert not window.windowIcon().isNull()
    window.close()


@pytest.mark.parametrize("suffix", [".py", ".md"])
def test_vi_escape_returns_editor_focus_to_selected_file(
        tmp_path, monkeypatch, app, suffix):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    import sp.app.folder_navigator.editors as editors
    from sp.app.folder_navigator.window import Window

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(editors.config, "load_vi_mode_enabled", lambda: True)
    source = tmp_path / f"sample{suffix}"
    source.write_text("editor focus\n", encoding="utf-8")
    window = Window(tmp_path)
    window.show()
    index = window.model.index(str(source))
    window.tree.setCurrentIndex(index)
    window.tree.setFocus()
    QTest.keyClick(window.tree, Qt.Key_Return, Qt.ShiftModifier)
    app.processEvents()

    editor = window.active_tab().editor
    assert editor.hasFocus()

    # Escape leaves insert mode first; the next Escape returns to file navigation.
    QTest.keyClick(editor, Qt.Key_I)
    QTest.keyClick(editor, Qt.Key_Escape)
    app.processEvents()
    assert editor.hasFocus()
    QTest.keyClick(editor, Qt.Key_Escape)
    app.processEvents()

    assert window.tree.hasFocus()
    assert window.tree.currentIndex() == index
    editor.document().setModified(False)
    window.close()


def test_source_editor_page_navigation_and_vi_page_chords(tmp_path, monkeypatch, app):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QTextCursor
    from PySide6.QtTest import QTest
    import sp.app.folder_navigator.editors as editors

    monkeypatch.setattr(editors.config, "load_vi_mode_enabled", lambda: True)
    editor = editors.SourceEditor("sample.py")
    editors.configure_source_editor(editor)
    editor.resize(500, 180)
    editor.setPlainText("\n".join(f"line {number}" for number in range(200)))
    editor.show()
    editor.moveCursor(QTextCursor.Start)
    editor.setFocus()
    app.processEvents()

    QTest.keyClick(editor, Qt.Key_PageDown)
    page_down_block = editor.textCursor().blockNumber()
    assert page_down_block > 0
    QTest.keyClick(editor, Qt.Key_PageUp)
    assert editor.textCursor().blockNumber() < page_down_block

    editor.moveCursor(QTextCursor.Start)
    QTest.keyClick(editor, Qt.Key_J, Qt.ControlModifier | Qt.ShiftModifier)
    chord_down_block = editor.textCursor().blockNumber()
    assert chord_down_block > 0
    QTest.keyClick(editor, Qt.Key_K, Qt.ControlModifier | Qt.ShiftModifier)
    assert editor.textCursor().blockNumber() < chord_down_block
    editor.close()


def test_source_editor_vi_selection_and_clipboard_commands(monkeypatch, app):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QTextCursor
    from PySide6.QtTest import QTest
    import sp.app.folder_navigator.editors as editors

    monkeypatch.setattr(editors.config, "load_vi_mode_enabled", lambda: True)
    editor = editors.SourceEditor("sample.py")
    editors.configure_source_editor(editor)
    editor.setPlainText("abc\ndef\nghi")
    editor.show()
    editor.moveCursor(QTextCursor.Start)
    editor.setFocus()

    QTest.keyClick(editor, Qt.Key_Right, Qt.ShiftModifier)
    assert editor.textCursor().selectedText() == "a"
    QTest.keyClick(editor, Qt.Key_C)
    assert app.clipboard().text() == "a"
    QTest.keyClick(editor, Qt.Key_X)
    assert editor.toPlainText() == "bc\ndef\nghi"
    QTest.keyClick(editor, Qt.Key_P)
    assert editor.toPlainText() == "abc\ndef\nghi"

    editor.moveCursor(QTextCursor.Start)
    QTest.keyClick(editor, Qt.Key_N, Qt.ShiftModifier)
    assert editor.textCursor().hasSelection()
    assert "abc" in editor.textCursor().selectedText()
    QTest.keyClick(editor, Qt.Key_U, Qt.ShiftModifier)
    assert not editor.textCursor().hasSelection()
    editor.close()


def test_source_editor_native_shift_arrow_selection_without_vi(monkeypatch, app):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QTextCursor
    from PySide6.QtTest import QTest
    import sp.app.folder_navigator.editors as editors

    monkeypatch.setattr(editors.config, "load_vi_mode_enabled", lambda: False)
    editor = editors.SourceEditor("sample.txt")
    editors.configure_source_editor(editor)
    editor.setPlainText("native selection")
    editor.show()
    editor.moveCursor(QTextCursor.Start)
    editor.setFocus()
    QTest.keyClick(editor, Qt.Key_Right, Qt.ShiftModifier)
    assert editor.textCursor().selectedText() == "n"
    editor.close()


def test_source_editor_vi_slash_opens_find_bar(tmp_path, monkeypatch, app):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    import sp.app.folder_navigator.editors as editors
    from sp.app.folder_navigator.window import Window

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(editors.config, "load_vi_mode_enabled", lambda: True)
    source = tmp_path / "sample.txt"
    source.write_text("find this text\n", encoding="utf-8")
    window = Window(tmp_path)
    window.show()
    window.open_file(source)
    tab = window.active_tab()
    tab.editor.setFocus()

    QTest.keyClick(tab.editor, Qt.Key_Slash)
    app.processEvents()

    assert not tab.find_bar.isHidden()
    assert tab.find_query.hasFocus()
    assert tab.text_for_save() == "find this text\n"
    tab.editor.document().setModified(False)
    window.close()


def test_folder_and_editor_panels_show_active_focus_border(tmp_path, monkeypatch, app):
    from sp.app.folder_navigator.window import Window

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    source = tmp_path / "sample.txt"
    source.write_text("focus borders\n", encoding="utf-8")
    window = Window(tmp_path)
    window.show()
    window.open_file(source)
    app.processEvents()

    window.tree.setFocus()
    app.processEvents()
    assert "2px solid transparent" not in window.rail.styleSheet()
    assert "2px solid transparent" in window.tabs.styleSheet()

    window.active_tab().editor.setFocus()
    app.processEvents()
    assert "2px solid transparent" in window.rail.styleSheet()
    assert "2px solid transparent" not in window.tabs.styleSheet()
    window.active_tab().editor.document().setModified(False)
    window.close()


def test_specialized_editor_labels_are_extension_specific():
    from sp.app.folder_navigator.window import Window

    assert Window._specialized_editor_label(Path("diagram.puml")) == "Open PlantUML Editor"
    assert Window._specialized_editor_label(Path("diagram.MMD")) == "Open Mermaid Editor"
    assert Window._specialized_editor_label(Path("board.excalidraw")) == "Open Excalidraw"
    assert Window._specialized_editor_label(Path("notes.md")) is None


def test_specialized_editor_launcher_keeps_window_alive(tmp_path, monkeypatch, app):
    import sp.app.ui.plantuml_editor_window as plantuml_editor
    from PySide6.QtWidgets import QMainWindow
    from sp.app.folder_navigator.window import Window

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    diagram = tmp_path / "diagram.puml"
    diagram.write_text("@startuml\n@enduml\n", encoding="utf-8")
    opened = []

    class FakePlantUMLEditor(QMainWindow):
        def __init__(self, file_path, parent=None):
            super().__init__(parent)
            opened.append(file_path)

    monkeypatch.setattr(plantuml_editor, "PlantUMLEditorWindow", FakePlantUMLEditor)
    window = Window(tmp_path)
    window._open_specialized_editor(diagram)

    assert opened == [str(diagram)]
    assert len(window.specialized_editor_windows) == 1
    window.specialized_editor_windows[0].close()
    window.close()
