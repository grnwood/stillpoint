from pathlib import Path
import os
import subprocess
import sys
import types

import pytest

from sp.app.folder_navigator.core import (ConflictError, atomic_save, content_matches,
    fuzzy_score, inside, pruned_relative_path, read_text,
    rich_markdown_fallback_reason, walk_files)
from sp.app.folder_navigator.catalog import (
    CATALOG_DIRECTORY, CATALOG_FILENAME, FolderCatalog,
)
from sp.app.folder_navigator.tabular import read_delimited_preview, read_workbook_preview
from sp.app.folder_navigator.editors import format_markdown_table


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


def test_large_markdown_guard_detects_bytes_lines_and_long_lines(monkeypatch):
    import sp.app.folder_navigator.core as core

    monkeypatch.setattr(core, "MAX_RICH_MARKDOWN_BYTES", 20)
    monkeypatch.setattr(core, "MAX_RICH_MARKDOWN_LINES", 3)
    monkeypatch.setattr(core, "MAX_RICH_MARKDOWN_LINE_CHARS", 8)

    assert "file size" in rich_markdown_fallback_reason("short", 21)
    assert "4 lines" in rich_markdown_fallback_reason("a\nb\nc\nd", 7)
    assert "character line" in rich_markdown_fallback_reason("123456789", 9)
    assert rich_markdown_fallback_reason("123456789\n" + "x" * 100, 10) == "a 9-character line"
    assert rich_markdown_fallback_reason("a\nb", 3) is None


def test_copied_html_code_block_opens_without_regex_highlighting(tmp_path, app, monkeypatch):
    from sp.app.folder_navigator.window import Window
    from sp.app.folder_navigator.editors import SourceEditor

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    path = tmp_path / "integration.md"
    span = '<span data-testid="renderer-code-block-line-1" class="token property">"eventType"</span>'
    content = "# Integration\n\n`" + span * 40 + "`\n"
    path.write_text(content, encoding="utf-8")
    assert "character line" in rich_markdown_fallback_reason(
        content, path.stat().st_size
    )

    window = Window(tmp_path)
    try:
        window.open_file(path, defer_enhancements=True)
        assert isinstance(window.active_tab().editor, SourceEditor)
        window.open_file(path, pinned=True)
        tab = window.active_tab()
        assert tab.pinned
        assert isinstance(tab.editor, SourceEditor)
        assert tab.editor.syntax_highlighter is None
        assert tab.editor.toPlainText() == content
        assert "Full Markdown preview skipped" in window.statusBar().currentMessage()
    finally:
        window.close()


def test_markdown_flyover_opens_full_editor_on_pin(tmp_path, app, monkeypatch):
    from sp.app.folder_navigator.window import Window
    from sp.app.ui.markdown_editor import MarkdownEditor

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    path = tmp_path / "note.md"
    path.write_text("# Heading\n\nA short note.\n", encoding="utf-8")
    window = Window(tmp_path)
    try:
        window.open_file(path, defer_enhancements=True)
        assert isinstance(window.active_tab().editor, MarkdownEditor)
        window.open_file(path, pinned=True)
        assert isinstance(window.active_tab().editor, MarkdownEditor)
        assert window.active_tab().pinned
        assert window.active_tab().property("folderMarkdownRendered")
    finally:
        window.close()


def test_supplied_wms_hover_files_keep_event_loop_responsive(tmp_path, app, monkeypatch):
    from PySide6.QtTest import QTest
    from sp.app.folder_navigator.editors import SourceEditor
    from sp.app.folder_navigator.window import Window
    from sp.app.ui.markdown_editor import MarkdownEditor

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    folder = Path(__file__).resolve().parents[1] / "dev-assets" / "mac-hang"
    large = folder / "3.3 WMS Integration.md"
    regular = folder / "3.3.1 WMS Events and Payloads.md"
    window = Window(folder)
    try:
        window.open_file(large, defer_enhancements=True)
        assert isinstance(window.active_tab().editor, SourceEditor)
        window.open_file(regular, defer_enhancements=True)
        QTest.qWait(window.markdown_preview_delay_ms + 100)
        assert isinstance(window.active_tab().editor, MarkdownEditor)
        assert window.active_tab().property("folderMarkdownRendered")
        window.open_file(large, pinned=True)
        assert isinstance(window.active_tab().editor, SourceEditor)
        assert "Full Markdown preview skipped" in window.statusBar().currentMessage()
    finally:
        window.close()


def test_failed_full_markdown_preview_keeps_editable_source(tmp_path, app, monkeypatch):
    from sp.app.folder_navigator.editors import SourceEditor
    from sp.app.folder_navigator.window import Window
    from sp.app.ui.markdown_editor import MarkdownEditor

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    path = tmp_path / "note.md"
    path.write_text("# Heading\n", encoding="utf-8")

    def fail_render(_self, _content):
        raise ValueError("invalid preview")

    monkeypatch.setattr(MarkdownEditor, "set_markdown", fail_render)
    window = Window(tmp_path)
    try:
        window.open_file(path, pinned=True)
        assert isinstance(window.active_tab().editor, SourceEditor)
        assert window.active_tab().editor.toPlainText() == "# Heading\n"
        assert "Full Markdown preview failed" in window.statusBar().currentMessage()
    finally:
        window.close()


def test_utf16_round_trip(tmp_path):
    path = tmp_path / "wide.txt"
    path.write_bytes("first\r\nsecond\r\n".encode("utf-16"))
    loaded = read_text(path)
    atomic_save(path, "first\nupdated\n", loaded)
    assert path.read_bytes().decode("utf-16") == "first\r\nupdated\r\n"


def test_format_markdown_table_aligns_columns_and_preserves_cell_pipes():
    source = (
        "Before\n\n"
        "Name | Reference | Score\n"
        "--- | :---: | ---:\n"
        "Ada | [Page|label] | 9\n"
        "Grace Hopper | `a|b` | 100\n\n"
        "After\n"
    )

    formatted, changed = format_markdown_table(source, 4)

    assert changed
    assert "| Name         |  Reference   | Score |" in formatted
    assert "| Ada          | [Page|label] |     9 |" in formatted
    assert "| Grace Hopper |    `a|b`     |   100 |" in formatted
    assert formatted.startswith("Before\n\n")
    assert formatted.endswith("\n\nAfter\n")


def test_rich_markdown_table_style_is_folder_navigator_only(
        tmp_path, monkeypatch, app):
    from sp.app.folder_navigator.window import Window
    from sp.app.ui.markdown_editor import MarkdownEditor

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    path = tmp_path / "table.md"
    path.write_text("| Name | Value |\n| --- | ---: |\n| A | 1 |\n")
    window = Window(tmp_path)
    window.open_file(path)
    ordinary_editor = MarkdownEditor()

    assert window.active_tab().editor.highlighter._folder_navigator_table_style
    assert not ordinary_editor.highlighter._folder_navigator_table_style
    assert (
        window.active_tab().editor.highlighter.folder_table_header_format.background().color()
        != window.active_tab().editor.highlighter.table_format.background().color()
    )
    ordinary_editor.close()
    window.close()


def test_folder_navigator_format_table_action_is_one_dirty_edit(
        tmp_path, monkeypatch, app):
    from sp.app.folder_navigator.window import Window

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    path = tmp_path / "table.md"
    path.write_text("| Name|Value |\n|---|---:|\n|Long name|2|\n")
    window = Window(tmp_path)
    window.open_file(path, pinned=True)
    tab = window.active_tab()
    cursor = tab.editor.textCursor()
    cursor.setPosition(tab.editor.document().findBlockByNumber(2).position())
    tab.editor.setTextCursor(cursor)

    window._format_active_markdown_table()

    assert "| Name      | Value |" in tab.text_for_save()
    assert "| Long name |     2 |" in tab.text_for_save()
    assert tab.dirty
    tab.editor.undo()
    assert "| Name|Value |" in tab.text_for_save()
    tab.editor.document().setModified(False)
    window.close()


def test_large_markdown_opens_lightweight_without_rich_override(
        tmp_path, monkeypatch, app):
    import sp.app.folder_navigator.core as core
    from sp.app.folder_navigator.editors import SourceEditor
    from sp.app.folder_navigator.window import Window

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(core, "MAX_RICH_MARKDOWN_BYTES", 32)
    path = tmp_path / "large.md"
    path.write_text("# Large\n\n" + "table row\n" * 20)
    window = Window(tmp_path)
    window.open_file(path, pinned=True)
    tab = window.active_tab()

    assert isinstance(tab.editor, SourceEditor)
    assert tab.property("folderLargeMarkdownSource")
    assert tab.editor.syntax_highlighter is None
    assert "lightweight mode" in tab.notice.text()
    assert not hasattr(tab, "rich_markdown_button")
    window.close()


def test_delimited_preview_detects_dialect_header_and_multiline_cells(tmp_path):
    path = tmp_path / "people.csv"
    path.write_text(
        'name;notes;score\r\nAlice;"first line\nsecond line";10\r\nBob;ok;20\r\n',
        encoding="utf-8",
    )

    preview = read_delimited_preview(path)

    assert preview.delimiter == ";"
    assert preview.has_header
    assert preview.rows == [
        ["name", "notes", "score"],
        ["Alice", "first line\nsecond line", "10"],
        ["Bob", "ok", "20"],
    ]
    assert not preview.truncated


def test_tsv_preview_uses_extension_fallback_for_ambiguous_content(tmp_path):
    path = tmp_path / "single.tsv"
    path.write_text("one\ttwo\n", encoding="utf-8")

    preview = read_delimited_preview(path)

    assert preview.delimiter == "\t"
    assert preview.rows == [["one", "two"]]


def test_workbook_preview_lazily_uses_calamine_and_selects_sheet(tmp_path, monkeypatch):
    class Sheet:
        end = (1, 1)
        total_height = 1
        total_width = 1

        def iter_rows(self):
            return iter((("label", "value"), ("answer", 42)))

    class Workbook:
        sheet_names = ["First", "Second"]

        @classmethod
        def from_path(cls, path):
            return cls()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get_sheet_by_name(self, name):
            assert name == "Second"
            return Sheet()

    monkeypatch.setitem(
        sys.modules, "python_calamine", types.SimpleNamespace(CalamineWorkbook=Workbook)
    )
    path = tmp_path / "book.xlsx"
    path.write_bytes(b"placeholder")

    preview = read_workbook_preview(path, "Second")

    assert preview.sheet_names == ("First", "Second")
    assert preview.sheet_name == "Second"
    assert preview.rows == [["label", "value"], ["answer", "42"]]
    assert (preview.total_rows, preview.total_columns) == (2, 2)


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


def test_csv_opens_in_incremental_table_and_can_switch_to_raw_text(
        tmp_path, monkeypatch, app):
    import time
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from sp.app.folder_navigator.window import SpreadsheetView, Window

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    path = tmp_path / "data.csv"
    path.write_text(
        "name,value\n" + "".join(f"item-{row},{row}\n" for row in range(650)),
        encoding="utf-8",
    )
    window = Window(tmp_path)
    window.show()
    window.open_file(path, pinned=True)
    deadline = time.monotonic() + 3
    while not getattr(window.active_tab(), "viewer", None) and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)

    viewer = window.active_tab().viewer
    assert isinstance(viewer, SpreadsheetView)
    assert viewer.model.rowCount() == 500
    assert viewer.model.canFetchMore()
    viewer.model.fetchMore()
    assert viewer.model.rowCount() == 650
    assert viewer.model.headerData(0, Qt.Horizontal) == "name"

    window._set_folder_rail_visible(False)
    viewer.table.setFocus()
    app.processEvents()
    QTest.keyClick(viewer.table, Qt.Key_Escape)
    app.processEvents()
    assert not window.rail.isHidden()
    assert window.tree.hasFocus()

    viewer.sourceRequested.emit()
    app.processEvents()
    assert window.active_tab().editor is not None
    assert window.active_tab().pinned
    assert "item-649,649" in window.active_tab().editor.toPlainText()
    window.close()


def test_table_preview_supports_vi_cell_navigation_and_header_sorting(app):
    from PySide6.QtCore import QItemSelectionModel, Qt
    from PySide6.QtTest import QTest
    from sp.app.folder_navigator.tabular import TablePreview
    from sp.app.folder_navigator.window import SpreadsheetView

    view = SpreadsheetView(
        TablePreview(
            [["name", "value"]]
            + [[f"item-{value}", str(value)] for value in range(100, 0, -1)],
            has_header=True,
        ),
        allow_source=True,
        vi_enabled=True,
    )
    view.resize(500, 220)
    view.show()
    view.activateWindow()
    app.processEvents()
    view.table.setCurrentIndex(view.model.index(0, 0))
    view.table.setFocus()
    QTest.keyClick(view.table, Qt.Key_L)
    QTest.keyClick(view.table, Qt.Key_J)
    assert (view.table.currentIndex().row(), view.table.currentIndex().column()) == (1, 1)

    QTest.keyClick(view.table, Qt.Key_G, Qt.ShiftModifier)
    assert (view.table.currentIndex().row(), view.table.currentIndex().column()) == (99, 1)
    QTest.keyClick(view.table, Qt.Key_G)
    assert (view.table.currentIndex().row(), view.table.currentIndex().column()) == (0, 1)

    QTest.keyClick(
        view.table, Qt.Key_J, Qt.ControlModifier | Qt.ShiftModifier
    )
    app.processEvents()
    page_down_row = view.table.currentIndex().row()
    assert page_down_row > 1
    QTest.keyClick(
        view.table, Qt.Key_K, Qt.ControlModifier | Qt.ShiftModifier
    )
    app.processEvents()
    assert view.table.currentIndex().row() < page_down_row

    selection = view.table.selectionModel()
    selection.setCurrentIndex(
        view.model.index(0, 0),
        QItemSelectionModel.ClearAndSelect,
    )
    QTest.keyClick(view.table, Qt.Key_L, Qt.ShiftModifier)
    QTest.keyClick(view.table, Qt.Key_J, Qt.ShiftModifier)
    assert {
        (index.row(), index.column()) for index in selection.selectedIndexes()
    } == {(0, 0), (0, 1), (1, 0), (1, 1)}
    QTest.keyClick(view.table, Qt.Key_C)
    assert app.clipboard().text() == "item-100\t100\nitem-99\t99"

    selection.setCurrentIndex(
        view.model.index(0, 0),
        QItemSelectionModel.ClearAndSelect,
    )
    QTest.keyClick(view.table, Qt.Key_Right, Qt.ShiftModifier)
    QTest.keyClick(view.table, Qt.Key_Down, Qt.ShiftModifier)
    assert len(selection.selectedIndexes()) == 4
    selection.setCurrentIndex(
        view.model.index(0, 0),
        QItemSelectionModel.ClearAndSelect,
    )
    QTest.keyClick(view.table, Qt.Key_N, Qt.ShiftModifier)
    assert {(index.row(), index.column()) for index in selection.selectedIndexes()} == {
        (0, 0), (1, 0)
    }
    QTest.keyClick(view.table, Qt.Key_U, Qt.ShiftModifier)
    assert [(index.row(), index.column()) for index in selection.selectedIndexes()] == [(0, 0)]

    view.table.horizontalHeader().sectionClicked.emit(1)
    assert view.model.data(view.model.index(0, 1)) == "1"
    assert view.model.data(view.model.index(99, 1)) == "100"
    view.table.horizontalHeader().sectionClicked.emit(1)
    assert view.model.data(view.model.index(0, 1)) == "100"
    view.close()


def test_table_preview_filters_selected_column_and_keeps_sorting(app):
    from PySide6.QtCore import Qt
    from sp.app.folder_navigator.tabular import TablePreview
    from sp.app.folder_navigator.window import SpreadsheetView

    view = SpreadsheetView(
        TablePreview(
            [
                ["name", "region", "amount"],
                ["Alpha", "East", "20"],
                ["Beta", "West", "10"],
                ["Gamma", "Northeast", "30"],
            ],
            has_header=True,
        )
    )
    view.filter_column.setCurrentIndex(view.filter_column.findData(1))
    view.filter_text.setText("east")
    view._apply_filter()

    assert view.model._data_row_count() == 2
    assert [
        view.model.data(view.model.index(row, 0))
        for row in range(view.model.rowCount())
    ] == ["Alpha", "Gamma"]
    assert "2 matching rows" in view.summary.text()

    view.table.horizontalHeader().sectionClicked.emit(2)
    assert view.model.data(view.model.index(0, 2)) == "20"
    assert view.model.data(view.model.index(1, 2)) == "30"
    view.filter_column.setCurrentIndex(view.filter_column.findData(-1))
    view.filter_text.setText("beta")
    view._apply_filter()
    assert view.model._data_row_count() == 1
    assert view.model.data(view.model.index(0, 0), Qt.DisplayRole) == "Beta"
    view.close()


def test_table_preview_excel_style_column_value_filters(app):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QDialog
    from sp.app.folder_navigator.tabular import TablePreview
    from sp.app.folder_navigator.window import ColumnFilterPopup, SpreadsheetView

    view = SpreadsheetView(
        TablePreview(
            [
                ["name", "region", "amount"],
                ["Alpha", "East", "20"],
                ["Beta", "West", "10"],
                ["Gamma", "East", "30"],
                ["Blank", "", "5"],
            ],
            has_header=True,
        )
    )
    assert view.value_filter_button.text() == "Filter Off"
    view.value_filter_button.setChecked(True)
    assert view.filter_header.filter_enabled
    assert view.value_filter_button.text() == "Filter On"
    assert "spreadsheetValueFilterToggle:checked" in view.value_filter_button.styleSheet()
    assert view.model.distinct_values(1) == ["", "East", "West"]

    values = view.model.distinct_values(1)
    view._set_column_value_filter(1, {"East"}, values)
    assert view.model._data_row_count() == 2
    assert 1 in view.filter_header.active_filter_columns
    assert [
        view.model.data(view.model.index(row, 0), Qt.DisplayRole)
        for row in range(view.model.rowCount())
    ] == ["Alpha", "Gamma"]
    # Other dropdowns reflect the rows admitted by already-active columns.
    assert view.model.distinct_values(2) == ["20", "30"]
    assert "2 matching rows" in view.summary.text()

    view._set_column_value_filter(1, set(values), values)
    assert view.model._data_row_count() == 4
    assert not view.model.value_filters
    view.value_filter_button.setChecked(False)
    assert not view.filter_header.filter_enabled
    assert view.value_filter_button.text() == "Filter Off"

    popup = ColumnFilterPopup("Region", ["East", "North", "West"], {"West"})
    popup.search.setText("east")
    popup.values_model.set_all(True)
    assert popup.selected_values() == {"East", "West"}
    popup.values_model.set_all(False)
    assert popup.selected_values() == {"West"}
    popup.apply_button.click()
    assert popup.result() == QDialog.Accepted

    clear_popup = ColumnFilterPopup("Region", ["East", "North", "West"], {"East"})
    clear_popup.search.setText("east")
    clear_popup.clear_button.click()
    assert clear_popup.selected_values() == {"East", "North", "West"}
    assert clear_popup.result() == QDialog.Accepted
    view.close()


def test_table_value_filter_reports_bounded_preview_and_disables_unsafe_tables(
        app, monkeypatch):
    import sp.app.folder_navigator.window as navigator
    from sp.app.folder_navigator.tabular import TablePreview

    bounded = navigator.SpreadsheetView(
        TablePreview([["name"], ["A"], ["B"]], has_header=True, truncated=True)
    )
    messages = []
    bounded.statusRequested.connect(lambda message, timeout: messages.append((message, timeout)))
    bounded.value_filter_button.setChecked(True)
    assert "loaded preview rows only" in messages[-1][0]
    bounded.close()

    monkeypatch.setattr(navigator, "VALUE_FILTER_MAX_ROWS", 1)
    unsafe = navigator.SpreadsheetView(
        TablePreview([["name"], ["A"], ["B"]], has_header=True)
    )
    assert not unsafe.value_filter_button.isEnabled()
    assert unsafe.value_filter_button.text() == "Filter Unavailable"
    assert "disabled" in unsafe.value_filter_disabled_reason
    assert not unsafe.filter_header.filter_enabled
    unsafe.close()


def test_ctrl_shift_enter_opens_tree_selection_with_os_handler(
        tmp_path, monkeypatch, app):
    import time
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QDesktopServices
    from PySide6.QtTest import QTest
    from sp.app.folder_navigator.window import Window

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    path = tmp_path / "native.txt"
    path.write_text("open me", encoding="utf-8")
    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toLocalFile()) or True)
    window = Window(tmp_path)
    window.show()
    deadline = time.monotonic() + 2
    index = window.model.index(str(path))
    while not index.isValid() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
        index = window.model.index(str(path))
    window.rail.setCurrentWidget(window.tree)
    window.tree.setCurrentIndex(index)
    window.tree.setFocus()
    app.processEvents()

    QTest.keyClick(
        window.tree, Qt.Key_Return, Qt.ControlModifier | Qt.ShiftModifier
    )
    app.processEvents()

    assert opened == [str(path)]
    window.close()


def test_preview_pinning_and_stale_restore(tmp_path, monkeypatch, app):
    from sp.app.folder_navigator.window import Window
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    for name in ("a.md", "b.md", "c.md"):
        (tmp_path / name).write_text(name)
    window = Window(tmp_path)
    window.open_file(tmp_path / "a.md")
    window.open_file(tmp_path / "b.md")
    assert [tab.path.name for tab in window.all_tabs()] == ["b.md"]
    assert "Preview" in window.tabs.tabToolTip(0)
    window.keep_open(0)
    assert "Pinned" in window.tabs.tabToolTip(0)
    window.open_file(tmp_path / "c.md")
    assert [tab.path.name for tab in window.all_tabs()] == ["b.md", "c.md"]
    window.active_tab().editor.insertPlainText("changed")
    assert window.active_tab().pinned and window.active_tab().dirty
    assert "Unsaved changes" in window.tabs.tabToolTip(window.tabs.currentIndex())
    window.open_file(tmp_path / "a.md")
    assert len(window.all_tabs()) == 3
    window.active_tab().editor.document().setModified(False)
    for tab in window.all_tabs():
        tab.editor.document().setModified(False)
    window.close()


def test_closing_last_tab_returns_focus_to_folder_tree(tmp_path, monkeypatch, app):
    from sp.app.folder_navigator.window import Window
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    path = tmp_path / "notes.txt"
    other_path = tmp_path / "other.txt"
    path.write_text("notes")
    other_path.write_text("other")
    window = Window(tmp_path)
    window.show()
    window.open_file(path, pinned=True)
    window.active_tab().editor.setFocus()
    window.active_tab().editor.document().setModified(False)

    window.close_tab(0)
    app.processEvents()

    assert window.tabs.count() == 0
    assert window.rail.currentIndex() == 0
    assert window.tree.hasFocus()
    assert Path(window.model.filePath(window.tree.currentIndex())) == path

    QTest.keyClick(window.tree, Qt.Key_Down)
    app.processEvents()

    assert Path(window.model.filePath(window.tree.currentIndex())) == other_path
    window.close()


def test_launch_focuses_folder_tree_with_vi_navigation_ready(
        tmp_path, monkeypatch, app):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from sp.app.folder_navigator.window import Window

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / ".stillpoint_config.json").write_text(
        '{"enable_vi_mode": true}', encoding="utf-8"
    )
    first = tmp_path / "alpha.txt"
    second = tmp_path / "bravo.txt"
    first.write_text("alpha", encoding="utf-8")
    second.write_text("bravo", encoding="utf-8")

    window = Window(tmp_path)
    window.show()
    QTest.qWait(100)
    app.processEvents()

    assert window.rail.currentIndex() == 0
    assert window.tree.hasFocus()
    current = window.tree.currentIndex()
    assert current.isValid()
    adjacent = window.tree.indexAbove(current)
    key = Qt.Key_K
    if not adjacent.isValid():
        adjacent = window.tree.indexBelow(current)
        key = Qt.Key_J
    assert adjacent.isValid()

    QTest.keyClick(window.tree, key)
    app.processEvents()

    assert window.tree.currentIndex() == adjacent
    assert window.tree.hasFocus()
    if window.active_tab() and window.active_tab().editor:
        window.active_tab().editor.document().setModified(False)
    window.close()


def test_unhandled_escape_returns_focus_to_folder_tree(tmp_path, monkeypatch, app):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from sp.app.folder_navigator.window import Window

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    path = tmp_path / "notes.txt"
    path.write_text("notes")
    window = Window(tmp_path)
    window.show()
    window.open_file(path, pinned=True)
    editor = window.active_tab().editor
    editor.setFocus()

    QTest.keyClick(editor, Qt.Key_Escape)
    app.processEvents()

    assert window.tree.hasFocus()
    assert Path(window.model.filePath(window.tree.currentIndex())) == path
    editor.document().setModified(False)
    window.close()


def test_editor_context_menu_is_shared_and_reveals_file(
        tmp_path, monkeypatch, app) -> None:
    from PySide6.QtCore import QPoint, Qt
    from sp.app.folder_navigator.window import Window

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    nested = tmp_path / "nested"
    nested.mkdir()
    markdown_path = nested / "notes.md"
    source_path = nested / "notes.py"
    markdown_path.write_text("# Notes\n")
    source_path.write_text("print('notes')\n")
    window = Window(tmp_path)
    window.show()
    window.open_file(markdown_path, pinned=True)
    window.open_file(source_path, pinned=True)
    app.processEvents()

    markdown_tab, source_tab = window.all_tabs()
    assert markdown_tab.editor.contextMenuPolicy() == Qt.CustomContextMenu
    assert source_tab.editor.contextMenuPolicy() == Qt.CustomContextMenu
    markdown_menu = window._create_editor_context_menu(markdown_tab, QPoint())
    source_menu = window._create_editor_context_menu(source_tab, QPoint())
    markdown_actions = [action.text().replace("&", "") for action in markdown_menu.actions()]
    source_actions = [action.text().replace("&", "") for action in source_menu.actions()]

    assert [
        action for action in markdown_actions
        if action not in {"Copy as Markdown", "Format Markdown Table"}
    ] == source_actions
    assert "Copy as Markdown" in markdown_actions
    assert "Copy as Markdown" not in source_actions
    assert "Format Markdown Table" in markdown_actions
    assert "Format Markdown Table" not in source_actions
    assert markdown_actions[-1] == "Reveal in Folder"
    assert not ({"Page", "Navigate", "Move", "AI Actions"} & set(markdown_actions))

    markdown_tab.editor.insertPlainText("unsaved ")
    next(action for action in markdown_menu.actions()
         if action.text() == "Copy as Markdown").trigger()
    assert app.clipboard().text() == markdown_tab.editor.to_markdown()
    assert app.clipboard().text() != markdown_path.read_text()

    markdown_menu.actions()[-1].trigger()
    app.processEvents()
    current = window.tree.currentIndex()
    assert Path(window.model.filePath(current)) == markdown_path
    assert window.tree.hasFocus()
    assert window.tree.isExpanded(window.model.index(str(nested)))

    markdown_tab.editor.document().setModified(False)
    source_tab.editor.document().setModified(False)
    markdown_menu.deleteLater()
    source_menu.deleteLater()
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
    monkeypatch.setattr(
        "sp.app.config.get_active_vault",
        lambda: str(tmp_path / "vault"),
    )
    launch.launch(tmp_path)
    assert captured["command"][1:3] == ["-m", "sp.app.folder_navigator"]
    assert captured["kwargs"]["env"]["SP_FOLDER_NAVIGATOR_THEME_OVERRIDE"] == "midnight-blue.json"
    assert "SP_THEME_OVERRIDE" not in captured["kwargs"]["env"]
    assert captured["kwargs"]["env"]["SP_FOLDER_NAVIGATOR_STILLPOINT_VAULT"] == str(
        tmp_path / "vault"
    )
    source_root = str(Path(launch.__file__).resolve().parents[3])
    assert captured["kwargs"]["env"]["PYTHONPATH"].split(os.pathsep)[0] == source_root
    if os.name != "nt":
        assert captured["kwargs"]["start_new_session"]
    monkeypatch.setattr(__import__("sys"), "frozen", True, raising=False)
    launch.launch(tmp_path)
    assert captured["command"][1] == "--folder-navigator"


def test_child_folder_navigator_preserves_launching_vault_theme(tmp_path, monkeypatch):
    from sp.app.folder_navigator import launch

    captured = {}

    def fake_popen(command, **kwargs):
        captured.update(command=command, kwargs=kwargs)
        return object()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setenv("SP_FOLDER_NAVIGATOR_THEME_OVERRIDE", "ember-rose.json")
    monkeypatch.setattr(
        "sp.app.config.load_effective_theme_preference",
        lambda: "midnight-blue.json",
    )

    launch.launch(tmp_path)

    assert captured["kwargs"]["env"]["SP_FOLDER_NAVIGATOR_THEME_OVERRIDE"] == "ember-rose.json"


def test_folder_breadcrumb_launches_when_no_instance_is_open(qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import Qt
    from sp.app.folder_navigator import instances
    from sp.app.folder_navigator import window as navigator

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    child = tmp_path / "child"
    child.mkdir()
    launched = []
    monkeypatch.setattr(navigator, "launch", lambda path: launched.append(path))
    monkeypatch.setattr(instances, "activate_existing", lambda *args, **kwargs: False)

    window = navigator.Window(tmp_path)
    window._open_folder_breadcrumb(child)
    window._open_folder_breadcrumb(child, Qt.ControlModifier)
    assert launched == [child, child]
    monkeypatch.setattr(instances, "activate_existing", lambda *args, **kwargs: True)
    window._open_folder_breadcrumb(child)
    assert launched == [child, child]
    window.close()


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


def test_macos_folder_navigator_sets_native_application_icon(qapp, tmp_path, monkeypatch):
    from sp.app.folder_navigator import icon as icon_module

    icon_path = tmp_path / "FolderNavigator.icns"
    icon_path.touch()
    native_calls = []

    monkeypatch.setattr(icon_module.sys, "platform", "darwin")
    monkeypatch.setattr(icon_module, "get_folder_navigator_icon_path", lambda: icon_path)
    monkeypatch.setattr(
        icon_module,
        "_set_macos_application_icon",
        lambda path: native_calls.append(path) or True,
    )

    icon_module.configure_folder_navigator_application(qapp)

    assert native_calls == [icon_path]


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
    assert catalog.directory_candidates(
        "proj", root, include_excluded=True
    ) == [nested.parent]
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


def test_walk_files_always_prunes_generated_and_vcs_trees(tmp_path):
    visible = tmp_path / "visible.txt"
    visible.write_text("visible")
    for directory in (
        ".git", "node_modules", ".venv", "__pycache__", "build", "dist",
        "target", ".next", "cmake-build-debug", "package.egg-info",
    ):
        child = tmp_path / directory
        child.mkdir()
        (child / "noise.txt").write_text("noise")
    (tmp_path / "bundle.min.js").write_text("generated")
    (tmp_path / "module.pyc").write_bytes(b"generated")

    assert list(walk_files(tmp_path, tmp_path, hidden=True)) == [visible]


def test_generated_pruning_keeps_ambiguous_authored_folders():
    for relative in (
        Path("assets/logo.svg"),
        Path("generated/schema.py"),
        Path("public/index.html"),
        Path("src/main.py"),
    ):
        assert not pruned_relative_path(relative)
    for relative in (
        Path("build/app.js"),
        Path("target/release/app"),
        Path("cmake-build-release/output.o"),
        Path("web/app.min.js"),
    ):
        assert pruned_relative_path(relative)


def test_catalog_scan_state_is_persistent(tmp_path):
    catalog = FolderCatalog(tmp_path)
    assert catalog.scan_state() == "not_started"
    generation = catalog.begin_refresh()
    catalog.finish_refresh(generation, complete=False, state="partial")

    assert FolderCatalog(tmp_path).scan_state() == "partial"


def test_large_folder_catalog_pauses_at_budget_and_can_continue(
        tmp_path, monkeypatch, app):
    import time
    import sp.app.folder_navigator.window as module

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(module, "MAX_INDEX_FILES", 2)
    monkeypatch.setattr(module, "MAX_INDEX_SECONDS", 60.0)
    for index in range(5):
        (tmp_path / f"file-{index}.txt").write_text(str(index))
    window = module.Window(tmp_path)
    window.show()

    window._warm_catalog()
    deadline = time.monotonic() + 3
    while window.catalog_running and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)

    assert window.catalog_state == "partial"
    assert window.catalog_indexed_this_run == 2
    assert window.index_continue_button.isVisibleTo(window)

    window._continue_catalog_indexing()
    deadline = time.monotonic() + 3
    while window.catalog_running and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)

    assert window.catalog_state == "complete"
    assert window.catalog_count == 5
    window.close()


def test_git_catalog_walk_avoids_ignored_and_generated_outputs(
        tmp_path, monkeypatch, app):
    from sp.app.folder_navigator.window import Window

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / ".gitignore").write_text("ignored-output/\n", encoding="utf-8")
    source = root / "src" / "main.py"
    source.parent.mkdir()
    source.write_text("print('human')\n", encoding="utf-8")
    ignored = root / "ignored-output" / "bundle.js"
    ignored.parent.mkdir()
    ignored.write_text("compiled", encoding="utf-8")
    tracked_build = root / "dist" / "bundle.js"
    tracked_build.parent.mkdir()
    tracked_build.write_text("compiled", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(root), "add", "-f", "dist/bundle.js"], check=True
    )

    window = Window(root)
    paths = window._git_catalog_paths()

    assert source in paths
    assert ignored not in paths
    assert tracked_build not in paths
    window.close()


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


def test_standard_zoom_actions_route_to_active_viewer(tmp_path, monkeypatch, app):
    from PySide6.QtGui import QAction, QKeySequence
    from sp.app.folder_navigator.window import Window

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    path = tmp_path / "notes.txt"
    path.write_text("notes", encoding="utf-8")
    window = Window(tmp_path)
    window.open_file(path, pinned=True)
    tab = window.active_tab()
    calls = []
    monkeypatch.setattr(tab.editor, "zoomIn", lambda amount=1: calls.append(("in", amount)))
    monkeypatch.setattr(tab.editor, "zoomOut", lambda amount=1: calls.append(("out", amount)))

    actions = {
        action.text().replace("&", ""): action
        for action in window.findChildren(QAction)
    }
    zoom_in = actions["Zoom In"]
    zoom_out = actions["Zoom Out"]
    assert zoom_in.shortcut().matches(QKeySequence(QKeySequence.ZoomIn)) == QKeySequence.ExactMatch
    assert zoom_out.shortcut().matches(QKeySequence(QKeySequence.ZoomOut)) == QKeySequence.ExactMatch

    zoom_in.trigger()
    zoom_out.trigger()
    assert calls == [("in", 1), ("out", 1)]
    tab.editor.document().setModified(False)
    window.close()


def test_quick_open_uses_ctrl_j_and_ctrl_p_prints_through_stillpoint(
        tmp_path, monkeypatch, app):
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QAction, QDesktopServices, QKeySequence
    from sp.app.folder_navigator.window import Window

    vault = tmp_path / "vault"
    folder = vault / "notes"
    folder.mkdir(parents=True)
    page = folder / "hello world.md"
    page.write_text("# Hello\n", encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("SP_FOLDER_NAVIGATOR_STILLPOINT_VAULT", str(vault))
    monkeypatch.setenv("SP_FOLDER_NAVIGATOR_API_BASE", "http://127.0.0.1:8765")
    window = Window(folder)
    window.open_file(page, pinned=True)
    options = {
        "include_subpages": False,
        "depth": 1,
        "include_header": True,
        "include_toc": False,
        "toc_title": "",
        "auto_pop_browser": True,
    }
    monkeypatch.setattr(window, "_show_print_dialog", lambda _path: options)
    monkeypatch.setattr(window, "_get_stillpoint_print_token", lambda _base: "print token")
    opened = []
    monkeypatch.setattr(
        QDesktopServices, "openUrl", lambda url: opened.append(QUrl(url)) or True
    )
    actions = {
        action.text().replace("&", ""): action
        for action in window.findChildren(QAction)
    }

    assert actions["Quick Open"].shortcut().matches(
        QKeySequence("Ctrl+J")
    ) == QKeySequence.ExactMatch
    assert actions["Print Page"].shortcut().matches(
        QKeySequence(QKeySequence.Print)
    ) == QKeySequence.ExactMatch

    actions["Print Page"].trigger()

    assert len(opened) == 1
    assert bytes(opened[0].toEncoded()).decode("ascii") == (
        "http://127.0.0.1:8765/print/notes/hello%20world.md"
        "?mode=page&auto=1&header=1&toc=0&token=print%20token"
    )
    window.active_tab().editor.document().setModified(False)
    window.close()


def test_print_outside_vault_stages_current_buffer_on_server(
        tmp_path, monkeypatch, app):
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices
    from sp.app.folder_navigator.window import Window

    folder = tmp_path / "outside"
    folder.mkdir()
    page = folder / "draft.md"
    page.write_text("# Disk\n", encoding="utf-8")
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("SP_FOLDER_NAVIGATOR_STILLPOINT_VAULT", str(vault))
    monkeypatch.setenv("SP_FOLDER_NAVIGATOR_API_BASE", "http://127.0.0.1:8765")
    window = Window(folder)
    window.open_file(page, pinned=True)
    window.active_tab().editor.insertPlainText("current buffer")
    options = {
        "include_subpages": False,
        "depth": 1,
        "include_header": True,
        "include_toc": False,
        "toc_title": "",
        "auto_pop_browser": False,
    }
    monkeypatch.setattr(window, "_show_print_dialog", lambda _path: options)
    monkeypatch.setattr(window, "_get_stillpoint_print_token", lambda _base: "token")
    staged = []
    monkeypatch.setattr(
        window,
        "_create_stillpoint_print_preview",
        lambda base, path, content: staged.append((base, path, content)) or "preview-id",
    )
    opened = []
    monkeypatch.setattr(
        QDesktopServices, "openUrl", lambda url: opened.append(QUrl(url)) or True
    )

    window.print_active()

    assert len(staged) == 1
    assert staged[0][0] == "http://127.0.0.1:8765"
    assert staged[0][1] == page
    assert "current buffer" in staged[0][2]
    assert bytes(opened[0].toEncoded()).decode("ascii") == (
        "http://127.0.0.1:8765/print-preview/preview-id"
        "?auto=0&header=1&toc=0&token=token"
    )
    window.active_tab().editor.document().setModified(False)
    window.close()


def test_zoom_is_shared_persisted_and_routes_to_folder_tree(
        tmp_path, monkeypatch, app):
    from sp.app.folder_navigator.window import Window

    home = tmp_path / "home"
    root = tmp_path / "root"
    home.mkdir()
    root.mkdir()
    paths = [root / name for name in ("one.txt", "two.txt", "three.txt")]
    for path in paths:
        path.write_text(path.name, encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: home)
    window = Window(root)
    window.show()
    window.open_file(paths[0], pinned=True)
    first = window.active_tab().editor
    first.setFocus()
    app.processEvents()
    editor_size = first.document().defaultFont().pointSizeF()

    window._zoom_active_view(1)
    assert first.document().defaultFont().pointSizeF() == editor_size + 1
    window.open_file(paths[1], pinned=True)
    assert window.active_tab().editor.document().defaultFont().pointSizeF() == editor_size + 1

    tree_size = window.tree.font().pointSizeF()
    window.tree.setFocus()
    app.processEvents()
    window._zoom_active_view(1)
    assert window.tree.font().pointSizeF() == tree_size + 1
    assert window.editor_zoom_steps == 1
    assert window.folder_zoom_steps == 1
    window.close()

    restored = Window(root)
    assert restored.editor_zoom_steps == 1
    assert restored.folder_zoom_steps == 1
    assert restored.tree.font().pointSizeF() == tree_size + 1
    restored.open_file(paths[2], pinned=True)
    assert restored.active_tab().editor.document().defaultFont().pointSizeF() == editor_size + 1
    restored.close()


def test_command_palette_uses_ctrl_shift_p_and_all_menu_actions(
        tmp_path, monkeypatch, app):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from sp.app.folder_navigator.window import Window

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    window = Window(tmp_path)
    window.show()
    window.activateWindow()
    window.tree.setFocus()
    app.processEvents()

    QTest.keyClick(window.tree, Qt.Key_P, Qt.ControlModifier | Qt.ShiftModifier)
    app.processEvents()

    assert window.command_palette.isVisible()
    labels = {
        str(action.property("commandLabel"))
        for action in window.command_palette.entries
    }
    assert "File / Add Bookmark to StillPoint" in labels
    assert "View / Zoom In" in labels
    assert "View / Columns / Size" in labels
    assert "Go / Quick Open" in labels
    assert "Go / Reveal in Folder" in labels
    assert "Go / Filter From Here" in labels
    assert len(window.command_palette.entries) == len(window._collect_command_actions())

    hidden = window.hidden_action.isChecked()
    window.command_palette.actionTriggered.emit(window.hidden_action)
    assert window.hidden_action.isChecked() is not hidden
    window.close()


def test_command_palette_reveals_the_active_tab_in_folder(
        tmp_path, monkeypatch, app):
    from sp.app.folder_navigator.window import Window

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    nested = tmp_path / "nested"
    nested.mkdir()
    first_path = tmp_path / "first.txt"
    active_path = nested / "active.txt"
    first_path.write_text("First\n", encoding="utf-8")
    active_path.write_text("Active\n", encoding="utf-8")
    window = Window(tmp_path)
    window.show()
    window.open_file(first_path, pinned=True)
    window.open_file(active_path, pinned=True)
    app.processEvents()

    assert window.active_tab().path == active_path
    assert window.reveal_active_tab_action.isEnabled()
    window.command_palette.actionTriggered.emit(window.reveal_active_tab_action)
    app.processEvents()

    current = window.tree.currentIndex()
    assert Path(window.model.filePath(current)) == active_path
    assert window.tree.isExpanded(window.model.index(str(nested)))

    for tab in window.all_tabs():
        if tab.editor:
            tab.editor.document().setModified(False)
    window.close()


def test_filter_from_here_uses_selected_folder_or_file_parent(
        tmp_path, monkeypatch, app):
    from sp.app.folder_navigator.window import Window

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    folder = tmp_path / "nested"
    folder.mkdir()
    path = folder / "note.txt"
    path.write_text("note", encoding="utf-8")
    window = Window(tmp_path)
    window.tree.setCurrentIndex(window.model.index(str(path)))

    window.filter_from_here()

    assert window.scope == folder
    assert Path(window.model.filePath(window.tree.rootIndex())) == folder
    window.close()


def test_filtered_escape_folds_scope_then_clears_filter(tmp_path, monkeypatch, app):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from sp.app.folder_navigator.window import Window

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    scope = tmp_path / "scope"
    nested = scope / "nested"
    nested.mkdir(parents=True)
    window = Window(tmp_path)
    try:
        window.show()
        window.apply_filter(scope)
        nested_index = window.model.index(str(nested))
        window.tree.expand(nested_index)
        window.tree.setCurrentIndex(nested_index)
        window.tree.setFocus()

        QTest.keyClick(window.tree, Qt.Key_Escape)
        assert window.scope == scope
        assert not window.tree.isExpanded(nested_index)
        assert not window.tree.currentIndex().isValid()
        assert not window.filter_label.isHidden()

        QTest.keyClick(window.tree, Qt.Key_Escape)
        assert window.scope == tmp_path
        assert not window.clear_filter_action.isEnabled()
        assert window.filter_label.isHidden()
    finally:
        window.close()


def test_tree_defaults_to_folders_first_and_preserves_explicit_sort(tmp_path, monkeypatch, app):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from sp.app.folder_navigator.window import Window

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / "z-folder").mkdir()
    (tmp_path / "a-folder").mkdir()
    (tmp_path / "b-file.txt").write_text("b", encoding="utf-8")
    (tmp_path / "c-file.txt").write_text("c", encoding="utf-8")
    window = Window(tmp_path)
    try:
        root_index = window.tree.rootIndex()
        QTest.qWait(120)
        app.processEvents()
        children = [window.model.index(row, 0, root_index)
                    for row in range(window.model.rowCount(root_index))]
        assert [window.model.isDir(index) for index in children] == [True, True, False, False]
        window.tree.sortByColumn(0, Qt.DescendingOrder)
        assert window.tree.header().sortIndicatorOrder() == Qt.DescendingOrder
    finally:
        window.close()


def test_add_bookmark_to_stillpoint_saves_folder_root(
        tmp_path, monkeypatch, app):
    from sp.app import config
    from sp.app.folder_navigator.window import Window

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("SP_FOLDER_NAVIGATOR_STILLPOINT_VAULT", str(tmp_path / "vault"))
    saved = []
    resets = []
    monkeypatch.setattr(config, "push_active_vault_context", lambda vault: ("token", vault))
    monkeypatch.setattr(config, "reset_active_vault_context", resets.append)
    monkeypatch.setattr(config, "load_folder_bookmarks", lambda: ["/existing"])
    monkeypatch.setattr(config, "save_folder_bookmarks", lambda paths: saved.append(list(paths)))
    window = Window(tmp_path)

    window._bookmark_in_stillpoint()

    assert saved == [["/existing", str(tmp_path.resolve())]]
    assert resets == [("token", str(tmp_path / "vault"))]
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


def test_quick_open_enter_focuses_opened_editor(tmp_path, monkeypatch, app):
    import time
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from sp.app.folder_navigator.window import Picker, Window

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    path = tmp_path / "target.txt"
    path.write_text("target", encoding="utf-8")
    window = Window(tmp_path)
    window.show()
    window.catalog_db.upsert_paths([path])
    window.tree.setFocus()
    picker = Picker(window, quick=True)
    picker.show()
    picker.query.setText("target")
    app.processEvents()

    QTest.keyClick(picker.query, Qt.Key_Return)
    deadline = time.monotonic() + 2
    while (
        (window.active_tab() is None or not window.active_tab().editor.hasFocus())
        and time.monotonic() < deadline
    ):
        app.processEvents()
        time.sleep(.01)

    assert window.active_tab().path == path
    assert window.active_tab().editor.hasFocus()
    window.active_tab().editor.document().setModified(False)
    window.close()


def test_quick_open_folder_target_reveals_without_filtering(tmp_path, monkeypatch, app):
    import time
    from PySide6.QtCore import Qt
    from sp.app.folder_navigator.window import Picker, Window

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    folder = tmp_path / "human-notes"
    folder.mkdir()
    page = folder / "page.md"
    page.write_text("# Page\n", encoding="utf-8")
    window = Window(tmp_path)
    window.catalog_db.upsert_paths([page])
    picker = Picker(window, quick=True)
    picker.query.setText("human")
    deadline = time.monotonic() + 2
    folder_row = -1
    while folder_row < 0 and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
        for row in range(picker.list.count()):
            if picker.list.item(row).data(Qt.UserRole + 1):
                folder_row = row
                break
    assert folder_row >= 0

    picker.list.setCurrentRow(folder_row)
    picker.accept_file()
    app.processEvents()

    assert window.scope == tmp_path
    assert Path(window.model.filePath(window.tree.rootIndex())) == tmp_path
    assert Path(window.model.filePath(window.tree.currentIndex())) == folder
    window.close()


def test_quick_open_lists_matching_folders_before_files(tmp_path, monkeypatch, app):
    import time
    from PySide6.QtCore import Qt
    from sp.app.folder_navigator.window import Picker, Window

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    folder = tmp_path / "match-folder"
    folder.mkdir()
    child = folder / "notes.txt"
    child.write_text("notes", encoding="utf-8")
    file = tmp_path / "match-file.txt"
    file.write_text("match", encoding="utf-8")
    window = Window(tmp_path)
    try:
        window.catalog_db.upsert_paths([file, child])
        picker = Picker(window, quick=True)
        picker.query.setText("match")
        deadline = time.monotonic() + 2
        while picker.list.count() < 2 and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.01)
        assert picker.list.count() >= 2
        assert picker.list.item(0).data(Qt.UserRole + 1)
        assert not picker.list.item(1).data(Qt.UserRole + 1)
        picker.close()
    finally:
        window.close()


def test_folder_navigator_vi_picker_keys(tmp_path, monkeypatch, app):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from sp.app.folder_navigator.window import Window

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    invoked = []
    monkeypatch.setattr(Window, "bookmark_picker", lambda self: invoked.append("bookmark"))
    monkeypatch.setattr(Window, "folder_picker", lambda self: invoked.append("folder"))
    window = Window(tmp_path)
    window.tree.vi_enabled = True
    window.show()
    window.tree.setFocus()

    QTest.keyClick(window.tree, Qt.Key_F)
    QTest.keyClick(window.tree, Qt.Key_V)

    assert invoked == ["bookmark", "folder"]
    window.close()


@pytest.mark.parametrize("suffix", [".txt", ".md"])
def test_folder_navigator_editor_vi_picker_keys(tmp_path, monkeypatch, app, suffix):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from sp.app.folder_navigator.window import Window

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    path = tmp_path / f"notes{suffix}"
    path.write_text("Notes", encoding="utf-8")
    window = Window(tmp_path)
    window.open_file(path)
    editor = window.active_tab().editor
    editor.set_vi_mode_enabled(True)
    if suffix == ".md":
        editor.set_vi_mode(True)
    invoked = []
    monkeypatch.setattr(window, "bookmark_picker", lambda: invoked.append("bookmark"))
    monkeypatch.setattr(window, "folder_picker", lambda: invoked.append("folder"))
    window.show()
    editor.setFocus()

    QTest.keyClick(editor, Qt.Key_F)
    QTest.keyClick(editor, Qt.Key_V)

    assert invoked == ["bookmark", "folder"]
    editor.document().setModified(False)
    window.close()


def test_bookmark_picker_fuzzy_selects_bookmarked_folder(tmp_path, monkeypatch, app):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QDialog
    from sp.app.folder_navigator.window import BookmarkPicker, Window

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    folder = tmp_path / "project-notes"
    folder.mkdir()
    window = Window(tmp_path)
    window.toggle_bookmark(folder)
    picker = BookmarkPicker(window)
    picker.show()
    picker.query.setText("prnt")
    assert picker.list.count() == 1

    QTest.keyClick(picker.query, Qt.Key_Return)

    assert picker.result() == QDialog.Accepted
    assert picker.selected_path == folder
    window.close()


def test_bookmark_picker_includes_stillpoint_folder_navigator(
        tmp_path, monkeypatch, app):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QDialog
    from sp.app.folder_navigator.window import BookmarkPicker, Window

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    folder = tmp_path / "another-project"
    folder.mkdir()
    window = Window(tmp_path)
    picker = BookmarkPicker(window, [str(folder)])
    picker.show()
    picker.query.setText("another")

    assert picker.list.count() == 1
    item = picker.list.item(0)
    assert not item.icon().isNull()
    assert item.data(Qt.UserRole + 1) is True
    QTest.keyClick(picker.query, Qt.Key_Return)
    assert picker.result() == QDialog.Accepted
    assert picker.selected_path == folder
    assert picker.selected_folder_navigator
    window.close()


def test_bookmark_picker_activates_selected_folder_navigator(
        tmp_path, monkeypatch, app):
    from PySide6.QtCore import QTimer
    from sp.app.folder_navigator.window import BookmarkPicker, Window

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    folder = tmp_path / "another-project"
    folder.mkdir()
    window = Window(tmp_path)
    monkeypatch.setattr(window, "_stillpoint_folder_bookmarks", lambda: [str(folder)])
    activated = []
    monkeypatch.setattr(window, "_open_folder_breadcrumb", lambda path: activated.append(path))
    original_exec = BookmarkPicker.exec

    def choose_first(picker):
        QTimer.singleShot(0, picker.choose)
        return original_exec(picker)

    monkeypatch.setattr(BookmarkPicker, "exec", choose_first)
    window.bookmark_picker()

    assert activated == [folder]
    window.close()


def test_quick_open_supports_standard_vi_selection_chord(
        tmp_path, monkeypatch, app):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QListWidgetItem
    from PySide6.QtTest import QTest
    from sp.app.folder_navigator.window import Picker, Window

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    window = Window(tmp_path)
    picker = Picker(window, quick=True)
    picker.list.clear()
    for label in ("first", "second", "third"):
        picker.list.addItem(QListWidgetItem(label))
    picker.list.setCurrentRow(0)
    picker.show()
    picker.query.setFocus()

    QTest.keyClick(
        picker.query,
        Qt.Key_J,
        Qt.ControlModifier | Qt.ShiftModifier,
    )
    assert picker.list.currentRow() == 1

    picker.list.setFocus()
    QTest.keyClick(
        picker.list,
        Qt.Key_K,
        Qt.ControlModifier | Qt.ShiftModifier,
    )
    assert picker.list.currentRow() == 0
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


def test_external_drop_copies_files_and_folders_and_updates_catalog(
        tmp_path, monkeypatch, app):
    import time
    from PySide6.QtWidgets import QAbstractItemView
    from sp.app.folder_navigator.window import Window

    home = tmp_path / "home"
    root = tmp_path / "root"
    source_area = tmp_path / "outside"
    home.mkdir()
    root.mkdir()
    source_area.mkdir()
    source_file = source_area / "loose.txt"
    source_file.write_text("loose", encoding="utf-8")
    source_folder = source_area / "bundle"
    source_folder.mkdir()
    nested_file = source_folder / "nested.md"
    nested_file.write_text("# Nested\n", encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: home)
    window = Window(root)

    assert window.tree.acceptDrops()
    assert window.tree.dragDropMode() == QAbstractItemView.DragDrop
    assert window.tree.dragEnabled()
    window._copy_dropped_paths([source_file, source_folder], root)

    deadline = time.monotonic() + 5
    copied_file = root / source_file.name
    copied_nested = root / source_folder.name / nested_file.name
    while time.monotonic() < deadline:
        app.processEvents()
        if copied_file.exists() and copied_nested.exists() and window.catalog_db \
                and copied_nested in window.catalog_db.candidates(
                    "nested", root, include_excluded=True
                ):
            break
        time.sleep(.01)

    assert copied_file.read_text(encoding="utf-8") == "loose"
    assert copied_nested.read_text(encoding="utf-8") == "# Nested\n"
    assert source_file.exists()
    assert nested_file.exists()
    assert copied_file in window.catalog_db.candidates(
        "loose", root, include_excluded=True
    )
    assert copied_nested in window.catalog_db.candidates(
        "nested", root, include_excluded=True
    )
    window.close()


def test_external_drop_does_not_overwrite_existing_file(
        tmp_path, monkeypatch, app):
    import time
    from sp.app.folder_navigator.window import Window

    home = tmp_path / "home"
    root = tmp_path / "root"
    source_area = tmp_path / "outside"
    home.mkdir()
    root.mkdir()
    source_area.mkdir()
    existing = root / "same.txt"
    existing.write_text("keep", encoding="utf-8")
    source = source_area / "same.txt"
    source.write_text("replace", encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: home)
    window = Window(root)

    window._copy_dropped_paths([source], root)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and "already exists" not in window.statusBar().currentMessage():
        app.processEvents()
        time.sleep(.01)

    assert existing.read_text(encoding="utf-8") == "keep"
    assert "already exists" in window.statusBar().currentMessage()
    window.close()


def test_internal_drop_moves_selected_files_and_folder_and_updates_open_tabs(
        tmp_path, monkeypatch, app):
    from sp.app.folder_navigator.window import Window

    home = tmp_path / "home"
    root = tmp_path / "root"
    destination = root / "destination"
    folder = root / "bundle"
    home.mkdir()
    destination.mkdir(parents=True)
    folder.mkdir()
    first = root / "first.txt"
    second = root / "second.txt"
    nested = folder / "nested.md"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")
    nested.write_text("# Nested\n", encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: home)
    window = Window(root)
    window.open_file(first, pinned=True)
    window.open_file(nested, pinned=True)
    window.toggle_bookmark(folder)
    window.catalog_db.upsert_paths([first, second, nested])

    window._move_dropped_paths([first, second, folder, nested], destination)

    moved_first = destination / first.name
    moved_second = destination / second.name
    moved_folder = destination / folder.name
    moved_nested = moved_folder / nested.name
    assert moved_first.read_text(encoding="utf-8") == "first"
    assert moved_second.read_text(encoding="utf-8") == "second"
    assert moved_nested.read_text(encoding="utf-8") == "# Nested\n"
    assert not first.exists() and not second.exists() and not folder.exists()
    assert {tab.path for tab in window.all_tabs()} == {moved_first, moved_nested}
    assert window.active_tab().path == moved_nested
    assert str(moved_folder) in window._bookmarks()
    assert moved_first in window.catalog_db.candidates("first", root)
    assert moved_nested in window.catalog_db.candidates("nested", root)
    assert nested not in window.catalog_db.candidates("nested", root)
    window.close()


def test_internal_drop_rejects_collisions_descendants_and_dirty_tabs(
        tmp_path, monkeypatch, app):
    from sp.app.folder_navigator.window import Window

    home = tmp_path / "home"
    root = tmp_path / "root"
    destination = root / "destination"
    folder = root / "bundle"
    home.mkdir()
    destination.mkdir(parents=True)
    folder.mkdir()
    child = folder / "child"
    child.mkdir()
    first = root / "first.txt"
    second = root / "second.txt"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")
    (destination / first.name).write_text("keep", encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: home)
    window = Window(root)

    window._move_dropped_paths([first, second], destination)
    assert first.exists() and second.exists()
    assert (destination / first.name).read_text(encoding="utf-8") == "keep"
    window._move_dropped_paths([folder], child)
    assert folder.exists() and child.exists()

    window.open_file(second, pinned=True)
    window.active_tab().editor.insertPlainText("unsaved")
    window._move_dropped_paths([second], destination)
    assert second.exists() and not (destination / second.name).exists()
    window.active_tab().editor.document().setModified(False)
    window.close()


def test_tree_internal_drop_routes_selected_rows_as_move(tmp_path, monkeypatch, app):
    import json
    from PySide6.QtCore import QItemSelectionModel, QMimeData, QPointF, Qt
    from sp.app.folder_navigator.window import Window

    home = tmp_path / "home"
    root = tmp_path / "root"
    target = root / "target"
    home.mkdir()
    target.mkdir(parents=True)
    files = [root / "one.txt", root / "two.txt"]
    for path in files:
        path.write_text(path.stem, encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: home)
    window = Window(root)
    window.show()
    app.processEvents()
    selection = window.tree.selectionModel()
    selection.clearSelection()
    for path in files:
        selection.select(
            window.model.index(str(path)),
            QItemSelectionModel.Select | QItemSelectionModel.Rows,
        )
    assert set(window.tree._selected_drag_paths()) == set(files)

    mime = QMimeData()
    mime.setData(
        window.tree.INTERNAL_PATHS_MIME,
        json.dumps([str(path) for path in window.tree._selected_drag_paths()]).encode(),
    )
    monkeypatch.setattr(window.tree, "_drop_directory", lambda point: target)

    class Drop:
        action = None
        accepted = False

        def source(self):
            return window.tree

        def mimeData(self):
            return mime

        def position(self):
            return QPointF(0, 0)

        def setDropAction(self, action):
            self.action = action

        def accept(self):
            self.accepted = True

    event = Drop()
    window.tree.dropEvent(event)
    assert event.accepted and event.action == Qt.MoveAction
    assert all((target / path.name).exists() and not path.exists() for path in files)
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


def test_ripgrep_search_prunes_generated_trees_and_honors_ignore_toggle(
        tmp_path, monkeypatch, app):
    import re
    import shutil
    import threading
    from sp.app.folder_navigator.window import Window

    rg = shutil.which("rg")
    if not rg:
        pytest.skip("ripgrep is not installed")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / ".git").mkdir()
    (tmp_path / ".gitignore").write_text("ignored.txt\n")
    visible = tmp_path / "visible.txt"
    visible.write_text("needle")
    ignored = tmp_path / "ignored.txt"
    ignored.write_text("needle")
    generated = tmp_path / "node_modules"
    generated.mkdir()
    (generated / "dependency.txt").write_text("needle")
    window = Window(tmp_path)

    default = window._ripgrep_search(
        rg, tmp_path, "needle", re.compile("needle", re.IGNORECASE),
        case=False, whole=False, regex=False, include_ignored=False,
        canceled=threading.Event(),
    )
    included = window._ripgrep_search(
        rg, tmp_path, "needle", re.compile("needle", re.IGNORECASE),
        case=False, whole=False, regex=False, include_ignored=True,
        canceled=threading.Event(),
    )

    assert {record[0] for record in default} == {visible}
    assert {record[0] for record in included} == {visible, ignored}
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
    from PySide6.QtGui import QColor, QTextFormat
    from PySide6.QtTest import QTest
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
    flashes = [selection for selection in tab.editor.extraSelections()
               if selection.format.property(QTextFormat.UserProperty) == 9991]
    assert len(flashes) == 1
    assert flashes[0].format.background().color() == QColor(window._folder_identity_accent)
    QTest.qWait(260)
    assert not any(selection.format.property(QTextFormat.UserProperty) == 9991
                   for selection in tab.editor.extraSelections())
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


def test_heading_picker_t_shortcut_focuses_filter_and_accepts_navigation(
        tmp_path, monkeypatch, app):
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtTest import QTest
    import sp.app.folder_navigator.editors as editors
    from sp.app.folder_navigator.window import HeadingPicker, Window

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(editors.config, "load_vi_mode_enabled", lambda: True)
    page = tmp_path / "notes.md"
    page.write_text("# First\n\ntext\n\n## Second\n", encoding="utf-8")
    window = Window(tmp_path)
    window.show()
    window.open_file(page)
    editor = window.active_tab().editor
    editor.setFocus()
    observed = {}

    def operate_picker():
        picker = next(
            widget for widget in app.topLevelWidgets()
            if isinstance(widget, HeadingPicker) and widget.isVisible()
        )
        observed["query_focus"] = picker.query.hasFocus()
        QTest.keyClicks(picker.query, "sec")
        observed["filtered_count"] = picker.results.count()
        QTest.keyClick(picker.query, Qt.Key_J, Qt.ControlModifier | Qt.ShiftModifier)
        observed["selected_row"] = picker.results.currentRow()
        QTest.keyClick(picker.query, Qt.Key_Return)

    QTimer.singleShot(20, operate_picker)
    QTest.keyClick(editor, Qt.Key_T)

    assert observed == {
        "query_focus": True,
        "filtered_count": 1,
        "selected_row": 0,
    }
    assert editor.textCursor().blockNumber() == 4
    editor.document().setModified(False)
    window.close()


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


def test_pygments_highlighter_reuses_token_formats(app):
    from pygments.token import Token
    from sp.app.folder_navigator.editors import SourceEditor

    editor = SourceEditor("sample.py")
    first = editor.syntax_highlighter._format_for_token(Token.Keyword)
    second = editor.syntax_highlighter._format_for_token(Token.Keyword)

    assert first is second
    assert len(editor.syntax_highlighter._formats) == 1
    editor.close()


def test_text_editor_status_rail_tracks_cursor_selection_and_file_format(
        tmp_path, monkeypatch, app):
    from PySide6.QtGui import QTextCursor
    from sp.app.folder_navigator.window import Window

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    source = tmp_path / "sample.py"
    source.write_bytes(b"one\r\ntwo")
    window = Window(tmp_path)
    window.open_file(source)
    tab = window.active_tab()

    assert tab.cursor_status.text() == "Ln 1, Col 1  ·  2 lines"
    assert tab.file_status.text() == "Python  ·  UTF-8  ·  CRLF  ·  Editable"

    cursor = tab.editor.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.Down)
    cursor.movePosition(QTextCursor.MoveOperation.Right)
    cursor.movePosition(
        QTextCursor.MoveOperation.Right,
        QTextCursor.MoveMode.KeepAnchor,
        2,
    )
    tab.editor.setTextCursor(cursor)

    assert tab.cursor_status.text() == "Ln 2, Col 4  ·  2 selected  ·  2 lines"
    tab.editor.document().setModified(False)
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
    from sp.app.ui.theme import theme_value

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    nested = tmp_path / "nested"
    nested.mkdir()
    window = Window(tmp_path)
    assert window.filter_label.isHidden()
    window.apply_filter(nested)
    assert window.filter_label.parentWidget() is window.statusBar()
    assert not window.filter_label.isHidden()
    assert window.filter_label.text() == "Filtered"
    assert str(nested) in window.filter_label.toolTip()
    assert str(theme_value("main_window.filter_badge.bg", "#c62828")) in window.filter_label.styleSheet()
    assert window.clear_filter_action.isEnabled()
    window.clear_filter_action.trigger()
    assert window.scope == window.root
    assert window.filter_label.isHidden()
    assert not window.clear_filter_action.isEnabled()
    window.apply_filter(nested)
    window.filter_label.click()
    assert window.scope == window.root
    assert window.filter_label.isHidden()
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
    initial_zoom = view.zoom
    window.tabs.setFocus()
    app.processEvents()
    window._zoom_active_view(1)
    assert view.zoom > initial_zoom
    window._zoom_active_view(-1)
    assert view.zoom == pytest.approx(initial_zoom)
    window.close()


def test_pdf_preview_uses_shared_zoom_commands(tmp_path, monkeypatch, app):
    from PySide6.QtGui import QPainter, QPdfWriter
    from PySide6.QtPdfWidgets import QPdfView
    from sp.app.folder_navigator.window import Window

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    pdf_path = tmp_path / "sample.pdf"
    writer = QPdfWriter(str(pdf_path))
    painter = QPainter(writer)
    painter.drawText(100, 100, "sample")
    painter.end()
    window = Window(tmp_path)
    window.open_file(pdf_path, pinned=True)
    viewer = window.active_tab().viewer.findChild(QPdfView)

    initial_zoom = viewer.zoomFactor()
    window._zoom_active_view(1)
    assert viewer.zoomFactor() > initial_zoom
    window._zoom_active_view(-1)
    assert viewer.zoomFactor() == pytest.approx(initial_zoom)
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
    window.tree_markdown_open_delay_ms = 20
    window.show()
    window.tree.setFocus()

    for path in (first, second):
        index = window.model.index(str(path))
        window.tree.setCurrentIndex(index)
        QTest.qWait(30)
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


def test_markdown_preview_render_is_debounced_during_tree_flybys(
        tmp_path, monkeypatch, app):
    from PySide6.QtTest import QTest
    from sp.app.folder_navigator.window import Window

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    first = tmp_path / "first.md"
    second = tmp_path / "second.md"
    first.write_text("# First\n", encoding="utf-8")
    second.write_text("# Second\n", encoding="utf-8")
    window = Window(tmp_path)
    window.tree_markdown_open_delay_ms = 80
    window.markdown_preview_delay_ms = 80
    window.show()
    window.tree.setFocus()

    window.tree.setCurrentIndex(window.model.index(str(first)))
    assert window.active_tab() is None
    window.tree.setCurrentIndex(window.model.index(str(second)))
    assert window.active_tab() is None

    QTest.qWait(300)
    app.processEvents()

    second_tab = window.active_tab()
    assert window.tree.hasFocus()
    assert second_tab.path == second
    assert window.tabs.count() == 1
    assert second_tab.property("folderMarkdownRendered")
    assert not second_tab.editor.toPlainText().startswith("#")
    assert second_tab.text_for_save().startswith("# Second")
    second_tab.editor.document().setModified(False)
    window.close()


def test_rendered_markdown_hover_remains_replaceable_preview(
        tmp_path, monkeypatch, app):
    from PySide6.QtTest import QTest
    from sp.app.folder_navigator.window import Window

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    markdown = tmp_path / "hover.md"
    plain = tmp_path / "next.txt"
    markdown.write_text("# Hovered\n", encoding="utf-8")
    plain.write_text("next\n", encoding="utf-8")
    window = Window(tmp_path)
    window.tree_markdown_open_delay_ms = 30
    window.markdown_preview_delay_ms = 30
    window.show()
    window.tree.setFocus()

    window.tree.setCurrentIndex(window.model.index(str(markdown)))
    QTest.qWait(120)
    app.processEvents()
    markdown_tab = window.active_tab()
    assert markdown_tab.property("folderMarkdownRendered")
    assert not markdown_tab.dirty
    assert not markdown_tab.pinned

    window.tree.setCurrentIndex(window.model.index(str(plain)))
    QTest.qWait(window.tree_source_open_delay_ms + 20)
    app.processEvents()
    assert window.tabs.count() == 1
    assert window.active_tab().path == plain
    window.active_tab().editor.document().setModified(False)
    window.close()


@pytest.mark.parametrize(
    "first_text",
    ["+CamelCase\n", "![alt](missing.png)\n"],
)
def test_markdown_render_normalization_stays_clean_and_replaceable(
        tmp_path, monkeypatch, app, first_text):
    from PySide6.QtTest import QTest
    from sp.app.folder_navigator.window import Window

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    first = tmp_path / "first.md"
    second = tmp_path / "second.md"
    first.write_text(first_text, encoding="utf-8")
    second.write_text("# Second\n", encoding="utf-8")
    window = Window(tmp_path)
    window.tree_markdown_open_delay_ms = 20
    window.markdown_preview_delay_ms = 20
    window.show()
    window.tree.setFocus()

    window.tree.setCurrentIndex(window.model.index(str(first)))
    QTest.qWait(100)
    app.processEvents()
    first_tab = window.active_tab()
    assert first_tab.path == first
    assert not first_tab.dirty
    assert not first_tab.pinned
    assert not window.tabs.tabText(window.tabs.indexOf(first_tab)).startswith("● ")

    window.tree.setCurrentIndex(window.model.index(str(second)))
    QTest.qWait(100)
    app.processEvents()

    assert window.tabs.count() == 1
    assert window.active_tab().path == second
    assert not window.active_tab().dirty
    window.active_tab().editor.document().setModified(False)
    window.close()


def test_source_flyby_defers_pygments_until_selection_lingers(
        tmp_path, monkeypatch, app):
    from PySide6.QtTest import QTest
    from sp.app.folder_navigator.editors import SourceEditor
    from sp.app.folder_navigator.window import Window

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    first = tmp_path / "first.py"
    second = tmp_path / "second.py"
    first.write_text("print('first')\n", encoding="utf-8")
    second.write_text("print('second')\n", encoding="utf-8")
    window = Window(tmp_path)
    window.tree_source_open_delay_ms = 80
    window.markdown_preview_delay_ms = 80
    window.show()
    window.tree.setFocus()

    window.tree.setCurrentIndex(window.model.index(str(first)))
    assert window.active_tab() is None
    window.tree.setCurrentIndex(window.model.index(str(second)))
    assert window.active_tab() is None

    QTest.qWait(100)
    app.processEvents()
    second_tab = window.active_tab()
    assert isinstance(second_tab.editor, SourceEditor)
    assert second_tab.editor.syntax_highlighter is None

    QTest.qWait(100)
    app.processEvents()
    assert window.tabs.count() == 1
    assert window.active_tab().path == second
    assert second_tab.editor.syntax_highlighter is not None
    second_tab.editor.document().setModified(False)
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


def test_folder_navigator_has_distinct_restrained_window_identity(
        tmp_path, monkeypatch, app):
    from PySide6.QtWidgets import QToolButton
    from sp.app.folder_navigator.window import Window

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    document = tmp_path / "notes.txt"
    document.write_text("notes")
    window = Window(tmp_path)

    assert window.windowTitle() == f"Folder Navigator — {tmp_path.name}"
    assert window.identity_bar.title_label.text() == "FOLDER NAVIGATOR"
    assert tmp_path.name in window.identity_bar.detail_label.text()
    assert window.identity_bar.detail_label.toolTip() == str(tmp_path)
    assert window.identity_bar.minimumHeight() == 28
    assert window.identity_bar.maximumHeight() == 28
    assert window.columns_toolbar.isAncestorOf(window.identity_bar)
    assert window.rail.tabText(0) == "Files"
    assert window.folder_panel_header.title_label.text() == "FILES"
    assert window.folder_panel_header.detail_label.text() == tmp_path.name
    assert window._folder_identity_accent in window.identity_bar.styleSheet()
    assert "QTreeView::item:hover" in window.tree.styleSheet()
    assert "border-bottom-color" not in window.tree.styleSheet()

    window.open_file(document, pinned=True)
    assert window.windowTitle() == (
        f"Folder Navigator — {tmp_path.name} — {document.name}"
    )
    breadcrumb = window.identity_bar.findChildren(QToolButton)
    assert [button.accessibleName() for button in breadcrumb] == [
        f"Open {tmp_path.name}",
        f"Open {document.name}",
    ]
    assert breadcrumb[-1].toolTip() == str(document)
    window.active_tab().editor.document().setModified(False)
    window.close()


def test_breadcrumb_reuses_segments_without_showing_detached_windows(app):
    from PySide6.QtWidgets import QToolButton
    from sp.app.ui.utility_header import CompactToolbarIdentity

    identity = CompactToolbarIdentity("Folder Navigator")
    identity.show()
    identity.set_breadcrumb([
        ("root", "root", "root"),
        ("first.txt", "first", "first"),
    ])
    widgets = list(identity._breadcrumb_widgets)
    selected = []
    identity.breadcrumbActivated.connect(selected.append)

    identity.set_breadcrumb([
        ("root", "root", "root"),
        ("second.txt", "second", "second"),
    ])
    assert identity._breadcrumb_widgets == widgets
    button = identity._breadcrumb_widgets[-1]
    assert isinstance(button, QToolButton)
    button.click()
    assert selected == ["second"]

    identity.set_breadcrumb([("root", "root", "root")])
    assert all(not widget.isVisible() for widget in widgets)
    assert all(widget.parent() is identity for widget in widgets)
    identity.close()


def test_folder_navigator_process_disables_inprocess_mermaid_webengine(monkeypatch):
    from sp.app.folder_navigator.icon import configure_folder_navigator_process

    monkeypatch.setenv("SP_DISABLE_MERMAID_WEB_PREVIEW", "0")
    configure_folder_navigator_process()

    assert os.environ["SP_DISABLE_MERMAID_WEB_PREVIEW"] == "1"


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

    QTest.keyClick(editor, Qt.Key_G, Qt.ShiftModifier)
    assert editor.textCursor().atEnd()
    QTest.keyClick(editor, Qt.Key_G)
    assert editor.textCursor().position() == 0

    # Escape leaves insert mode first; the next Escape returns to file navigation.
    QTest.keyClick(editor, Qt.Key_I)
    QTest.keyClick(editor, Qt.Key_Escape)
    app.processEvents()
    assert editor.hasFocus()
    if suffix == ".md":
        # Markdown normally consumes Escape to transform/clear a selection.
        # Folder Navigator should still treat an already-normal editor as a
        # one-Escape pane handoff.
        editor.selectAll()
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


@pytest.mark.parametrize("suffix", [".txt", ".md"])
def test_find_enter_cycles_results_and_escape_closes_bar(
        tmp_path, monkeypatch, app, suffix):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from sp.app.folder_navigator.window import Window

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    source = tmp_path / f"sample{suffix}"
    source.write_text("needle one needle two\n", encoding="utf-8")
    window = Window(tmp_path)
    window.show()
    window.open_file(source, pinned=True)
    tab = window.active_tab()
    if suffix == ".md":
        window._schedule_markdown_preview(tab, immediate=True)

    tab._show_find()
    tab.find_query.setText("needle")
    QTest.keyClick(tab.find_query, Qt.Key_Return)
    app.processEvents()
    assert tab.editor.textCursor().selectionStart() == 0
    assert tab.find_query.hasFocus()

    QTest.keyClick(tab.find_query, Qt.Key_Return)
    app.processEvents()
    assert tab.editor.textCursor().selectionStart() == 11
    assert tab.find_query.hasFocus()

    QTest.keyClick(tab.find_query, Qt.Key_Escape)
    app.processEvents()
    assert tab.find_bar.isHidden()
    assert tab.editor.hasFocus()
    assert not window.tree.hasFocus()
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
    assert f"1px solid {window._folder_identity_accent}" in window.rail.styleSheet()
    assert f"1px solid {window._folder_identity_accent}" not in window.tabs.styleSheet()

    window.active_tab().editor.setFocus()
    app.processEvents()
    assert f"1px solid {window._folder_identity_accent}" not in window.rail.styleSheet()
    assert f"1px solid {window._folder_identity_accent}" in window.tabs.styleSheet()
    window.active_tab().editor.document().setModified(False)
    window.close()


def test_ctrl_shift_b_toggles_and_persists_folder_rail(
        tmp_path, monkeypatch, app):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from sp.app.folder_navigator.window import Window

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    source = tmp_path / "sample.txt"
    source.write_text("rail toggle\n", encoding="utf-8")
    window = Window(tmp_path)
    window.show()
    window.open_file(source, pinned=True)
    window.tree.setFocus()
    app.processEvents()

    QTest.keyClick(window.tree, Qt.Key_B, Qt.ControlModifier | Qt.ShiftModifier)
    app.processEvents()
    assert window.rail.isHidden()
    assert window.active_tab().editor.hasFocus()
    assert not window.rail_visibility_action.isChecked()

    QTest.keyClick(
        window.active_tab().editor,
        Qt.Key_B,
        Qt.ControlModifier | Qt.ShiftModifier,
    )
    app.processEvents()
    assert not window.rail.isHidden()
    assert window.rail_visibility_action.isChecked()

    QTest.keyClick(
        window.active_tab().editor,
        Qt.Key_B,
        Qt.ControlModifier | Qt.ShiftModifier,
    )
    app.processEvents()
    assert window.rail.isHidden()
    window.active_tab().editor.document().setModified(False)
    window.close()

    restored = Window(tmp_path)
    assert restored.rail.isHidden()
    restored.close()


def test_window_activation_repairs_missing_folder_navigator_focus(
        tmp_path, monkeypatch, app):
    from sp.app.folder_navigator.window import Window

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    source = tmp_path / "sample.txt"
    source.write_text("activation focus\n", encoding="utf-8")
    window = Window(tmp_path)
    window.show()
    window.activateWindow()
    window.open_file(source, pinned=True)
    window.menuBar().setFocus()
    app.processEvents()

    window._repair_focus_after_activation()
    app.processEvents()
    assert window.active_tab().editor.hasFocus()

    window.active_tab().editor.document().setModified(False)
    window.close_tab(0)
    window._set_folder_rail_visible(False)
    window.menuBar().setFocus()
    window._repair_focus_after_activation()
    app.processEvents()
    assert not window.rail.isHidden()
    assert window.tree.hasFocus()
    window.close()


def test_specialized_editor_labels_are_extension_specific():
    from sp.app.folder_navigator.window import Window

    assert Window._specialized_editor_label(Path("diagram.puml")) == "Open PlantUML Editor"
    assert Window._specialized_editor_label(Path("diagram.MMD")) == "Open Mermaid Editor"
    assert Window._specialized_editor_label(Path("board.excalidraw")) == "Open Excalidraw"
    assert Window._specialized_editor_label(Path("notes.md")) is None


@pytest.mark.parametrize("suffix", [".puml", ".mmd"])
def test_diagram_selection_renders_preview_and_refreshes_after_disk_save(
        tmp_path, monkeypatch, app, suffix):
    import time
    from types import SimpleNamespace
    from PySide6.QtWidgets import QPushButton
    from sp.app.folder_navigator.window import ImageView, Window

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    calls = []

    def render(_renderer, source, **_kwargs):
        calls.append(source)
        return SimpleNamespace(
            success=True,
            svg_content=(
                '<svg xmlns="http://www.w3.org/2000/svg" width="80" height="40">'
                '<rect width="80" height="40" fill="#4488cc"/></svg>'
            ),
            error_message=None,
            stderr=None,
        )

    if suffix == ".puml":
        monkeypatch.setattr(
            "sp.app.plantuml_renderer.PlantUMLRenderer.render_svg", render
        )
        initial = "@startuml\nA -> B\n@enduml\n"
        updated = "@startuml\nA -> C\n@enduml\n"
    else:
        monkeypatch.setattr(
            "sp.app.mermaid_renderer.MermaidRenderer.render_svg", render
        )
        initial = "flowchart TD\nA --> B\n"
        updated = "flowchart TD\nA --> C\n"
    path = tmp_path / f"diagram{suffix}"
    path.write_text(initial, encoding="utf-8")
    other = tmp_path / "notes.txt"
    other.write_text("ordinary preview", encoding="utf-8")
    window = Window(tmp_path)
    window.open_file(path)

    deadline = time.monotonic() + 2
    while not isinstance(getattr(window.active_tab(), "viewer", None), ImageView):
        assert time.monotonic() < deadline
        app.processEvents()
        time.sleep(.01)
    assert window.active_tab().editor is None
    assert calls == [initial]
    open_label = (
        "Open in PlantUML Editor" if suffix == ".puml"
        else "Open in Mermaid Editor"
    )
    buttons = {
        button.text(): button
        for button in window.active_tab().viewer.findChildren(QPushButton)
    }
    assert open_label in buttons
    assert "Copy SVG" in buttons
    assert "Copy PNG" in buttons
    buttons["Copy PNG"].click()
    assert not app.clipboard().pixmap().isNull()
    buttons["Copy SVG"].click()
    assert app.clipboard().text().startswith('<svg xmlns="http://www.w3.org/2000/svg"')
    assert window.active_tab().viewer.label.autoFillBackground()
    assert (
        window.active_tab().viewer.label.palette().color(
            window.active_tab().viewer.label.backgroundRole()
        ).name()
        == "#ffffff"
    )

    # Flyover tabs are disposable, but the expensive render is reusable while
    # the source fingerprint remains unchanged.
    window.open_file(other)
    assert window._index_for(path) == -1
    window.open_file(path)
    deadline = time.monotonic() + 2
    while not isinstance(getattr(window.active_tab(), "viewer", None), ImageView):
        assert time.monotonic() < deadline
        app.processEvents()
        time.sleep(.01)
    assert calls == [initial]
    assert next(iter(window.diagram_preview_cache.values()))[1] is not None

    path.write_text(updated, encoding="utf-8")
    window._refresh_disk()
    deadline = time.monotonic() + 2
    while calls != [initial, updated]:
        assert time.monotonic() < deadline
        app.processEvents()
        time.sleep(.01)
    assert isinstance(window.active_tab().viewer, ImageView)
    window.close()


def test_tree_flyovers_only_hydrate_the_diagram_selection_that_settles(
        tmp_path, monkeypatch, app):
    import time
    from types import SimpleNamespace
    from PySide6.QtTest import QTest
    from sp.app.folder_navigator.window import ImageView, Window

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    calls = []

    def render(_renderer, source, **_kwargs):
        calls.append(source)
        return SimpleNamespace(
            success=True,
            svg_content=(
                '<svg xmlns="http://www.w3.org/2000/svg" width="80" height="40">'
                '<rect width="80" height="40" fill="#4488cc"/></svg>'
            ),
            error_message=None,
            stderr=None,
        )

    monkeypatch.setattr(
        "sp.app.plantuml_renderer.PlantUMLRenderer.render_svg", render
    )
    first = tmp_path / "first.puml"
    second = tmp_path / "second.puml"
    first_source = "@startuml\nA -> B\n@enduml\n"
    second_source = "@startuml\nA -> C\n@enduml\n"
    first.write_text(first_source, encoding="utf-8")
    second.write_text(second_source, encoding="utf-8")

    window = Window(tmp_path)
    window.preview_hydration_delay_ms = 80
    window.show()
    window.tree.setFocus()
    window.tree.setCurrentIndex(window.model.index(str(first)))
    assert window.active_tab().path == first
    assert not window.active_tab().property("folderPreviewHydrated")
    window.tree.setCurrentIndex(window.model.index(str(second)))
    assert window.active_tab().path == second
    assert calls == []

    deadline = time.monotonic() + 2
    while not isinstance(getattr(window.active_tab(), "viewer", None), ImageView):
        assert time.monotonic() < deadline
        QTest.qWait(10)
        app.processEvents()

    assert calls == [second_source]
    assert window.active_tab().property("folderPreviewHydrated")
    window.close()


def test_disposable_preview_queue_cancels_stale_work_before_it_starts(
        tmp_path, monkeypatch):
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor
    from sp.app.folder_navigator.window import Window

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    window = Window(tmp_path)
    window.preview_executor.shutdown(wait=False, cancel_futures=True)
    window.preview_executor = ThreadPoolExecutor(max_workers=1)
    blocker_started = threading.Event()
    release_blocker = threading.Event()
    calls = []

    def blocker():
        blocker_started.set()
        release_blocker.wait(2)

    window._submit_preview_job(blocker, kind="test", disposable=True)
    assert blocker_started.wait(1)
    stale = window._submit_preview_job(
        lambda: calls.append("stale"), kind="test", disposable=True
    )

    window._cancel_disposable_preview_jobs()
    current = window._submit_preview_job(
        lambda: calls.append("current"), kind="test", disposable=False
    )
    release_blocker.set()
    deadline = time.monotonic() + 2
    while not current.done() and time.monotonic() < deadline:
        time.sleep(.01)

    assert stale.cancelled()
    assert calls == ["current"]
    assert window.preview_metrics["test"]["completed"] == 2
    window.close()


def test_decoded_image_preview_is_reused_after_disposable_tab_is_replaced(
        tmp_path, monkeypatch, app):
    import time
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage, QImageReader
    from sp.app.folder_navigator.window import ImageView, Window

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    path = tmp_path / "large.png"
    image = QImage(320, 240, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.blue)
    assert image.save(str(path), "PNG")
    other = tmp_path / "notes.txt"
    other.write_text("ordinary preview", encoding="utf-8")
    reads = []
    original_read = QImageReader.read

    def counted_read(reader):
        reads.append(reader.fileName())
        return original_read(reader)

    monkeypatch.setattr(QImageReader, "read", counted_read)
    window = Window(tmp_path)
    window.open_file(path)
    deadline = time.monotonic() + 2
    while not isinstance(getattr(window.active_tab(), "viewer", None), ImageView):
        assert time.monotonic() < deadline
        app.processEvents()
        time.sleep(.01)
    assert reads == [str(path)]

    window.open_file(other)
    window.open_file(path)
    deadline = time.monotonic() + 2
    while not isinstance(getattr(window.active_tab(), "viewer", None), ImageView):
        assert time.monotonic() < deadline
        app.processEvents()
        time.sleep(.01)
    assert reads == [str(path)]
    assert len(window.image_preview_cache) == 1
    assert window.preview_metrics["image"]["cache_hits"] == 1

    previous_viewer = window.active_tab().viewer
    image.fill(Qt.GlobalColor.red)
    assert image.save(str(path), "PNG")
    window._refresh_disk()
    deadline = time.monotonic() + 2
    while window.active_tab().viewer is previous_viewer:
        assert time.monotonic() < deadline
        app.processEvents()
        time.sleep(.01)
    assert reads == [str(path), str(path)]
    assert len(window.image_preview_cache) == 1
    window.close()


def test_image_tree_flyover_uses_thumbnail_then_zoom_loads_full_resolution(
        tmp_path, monkeypatch, app):
    import time
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage, QImageReader
    from sp.app.folder_navigator.window import (
        IMAGE_FLYOVER_MAX_PIXELS, ImageView, Window,
    )

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    path = tmp_path / "large.png"
    image = QImage(2000, 1500, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.darkCyan)
    assert image.save(str(path), "PNG")
    reads = []
    original_read = QImageReader.read

    def counted_read(reader):
        reads.append(reader.scaledSize())
        return original_read(reader)

    monkeypatch.setattr(QImageReader, "read", counted_read)
    window = Window(tmp_path)
    window.preview_hydration_delay_ms = 20
    window.show()
    window.tree.setCurrentIndex(window.model.index(str(path)))

    deadline = time.monotonic() + 3
    while not isinstance(getattr(window.active_tab(), "viewer", None), ImageView):
        assert time.monotonic() < deadline
        app.processEvents()
        time.sleep(.01)
    thumbnail = window.active_tab().viewer
    assert thumbnail.original.width() * thumbnail.original.height() <= IMAGE_FLYOVER_MAX_PIXELS
    assert window.active_tab().preview_image_quality == "thumbnail"
    assert not reads[0].isEmpty()

    thumbnail.zoom_in()
    deadline = time.monotonic() + 3
    while (getattr(window.active_tab(), "preview_image_quality", None) != "full"
           or window.active_tab().viewer is thumbnail):
        assert time.monotonic() < deadline
        app.processEvents()
        time.sleep(.01)
    assert window.active_tab().viewer.original.size() == image.size()
    assert reads[-1].isEmpty()
    assert len(window.image_preview_cache) == 2
    window.keep_open(window.tabs.currentIndex())
    app.processEvents()
    assert len(reads) == 2
    window.close()


def test_diagram_image_view_uses_standard_mouse_and_trackpad_navigation(
        tmp_path, app):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QImage, QWheelEvent
    from sp.app.folder_navigator.window import ImageView

    path = tmp_path / "diagram.puml"
    path.write_text("@startuml\n@enduml\n", encoding="utf-8")
    image = QImage(1000, 1000, QImage.Format_ARGB32)
    image.fill(Qt.white)
    viewer = ImageView(path, image, canvas_color="#ffffff")
    viewer.resize(300, 300)
    viewer.show()
    app.processEvents()
    viewer.actual()
    app.processEvents()
    viewer.scroll.verticalScrollBar().setValue(100)

    trackpad = QWheelEvent(
        QPointF(10, 10), QPointF(10, 10), QPoint(0, -30), QPoint(),
        Qt.NoButton, Qt.NoModifier, Qt.ScrollUpdate, False,
    )
    viewer.label.wheelEvent(trackpad)
    assert viewer.scroll.verticalScrollBar().value() == 130
    assert viewer.zoom == 1.0

    mouse_wheel = QWheelEvent(
        QPointF(10, 10), QPointF(10, 10), QPoint(), QPoint(0, 120),
        Qt.NoButton, Qt.NoModifier, Qt.ScrollUpdate, False,
    )
    viewer.label.wheelEvent(mouse_wheel)
    assert viewer.zoom == pytest.approx(1.1)
    viewer.close()


def test_excalidraw_selection_uses_saved_png_preview(tmp_path, monkeypatch, app):
    import time
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QPushButton
    from sp.app.folder_navigator.window import ImageView, Window

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    path = tmp_path / "board.excalidraw"
    path.write_text('{"type":"excalidraw","elements":[]}', encoding="utf-8")
    preview = path.with_name(f"{path.name}.png")
    image = QImage(64, 48, QImage.Format.Format_ARGB32)
    image.fill(0xff4488cc)
    assert image.save(str(preview), "PNG")

    window = Window(tmp_path)
    window.open_file(path)
    deadline = time.monotonic() + 2
    while not isinstance(getattr(window.active_tab(), "viewer", None), ImageView):
        assert time.monotonic() < deadline
        app.processEvents()
        time.sleep(.01)

    assert window.active_tab().editor is None
    assert any(
        button.text() == "Open in Excalidraw Editor"
        for button in window.active_tab().viewer.findChildren(QPushButton)
    )
    other = tmp_path / "notes.txt"
    other.write_text("ordinary preview", encoding="utf-8")
    assert len(window.diagram_preview_cache) == 1
    window.open_file(other)
    assert window._index_for(path) == -1
    window.open_file(path)
    # A cache hit is scheduled onto the UI loop and launches no decoder job.
    assert window.diagram_preview_inflight == set()
    deadline = time.monotonic() + 2
    while not isinstance(getattr(window.active_tab(), "viewer", None), ImageView):
        assert time.monotonic() < deadline
        app.processEvents()
        time.sleep(.01)
    window.close()


def test_excalidraw_editor_url_targets_selected_vault_file(
        tmp_path, monkeypatch, app):
    from sp.app.folder_navigator.window import Window

    vault = tmp_path / "vault"
    vault.mkdir()
    drawing = vault / "diagrams" / "board.excalidraw"
    drawing.parent.mkdir()
    drawing.write_text('{"type":"excalidraw","elements":[]}', encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("SP_FOLDER_NAVIGATOR_STILLPOINT_VAULT", str(vault))
    monkeypatch.setenv("SP_FOLDER_NAVIGATOR_API_BASE", "http://127.0.0.1:8765")
    monkeypatch.setenv("SP_FOLDER_NAVIGATOR_LOCAL_UI_TOKEN", "local token")
    window = Window(vault)

    assert window._excalidraw_editor_url(drawing) == (
        "http://127.0.0.1:8765/excalidraw/edit?"
        "path=%2Fdiagrams%2Fboard.excalidraw&token=local%20token"
    )
    window.close()


def test_excalidraw_editor_url_uses_scoped_grant_outside_vault(
        tmp_path, monkeypatch, app):
    from sp.app.folder_navigator.window import Window

    vault = tmp_path / "vault"
    vault.mkdir()
    outside = tmp_path / "outside.excalidraw"
    outside.write_text('{"type":"excalidraw","elements":[]}', encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("SP_FOLDER_NAVIGATOR_STILLPOINT_VAULT", str(vault))
    monkeypatch.setenv("SP_FOLDER_NAVIGATOR_API_BASE", "http://127.0.0.1:8765")
    monkeypatch.setenv("SP_FOLDER_NAVIGATOR_LOCAL_UI_TOKEN", "local token")
    window = Window(tmp_path)
    grants = []
    monkeypatch.setattr(
        window,
        "_create_excalidraw_grant",
        lambda api_base, path: grants.append((api_base, path))
        or ("external:opaque-grant", "opaque-grant"),
    )

    url, grant = window._excalidraw_editor_url(outside, include_grant=True)

    assert grants == [("http://127.0.0.1:8765", outside.resolve())]
    assert url == (
        "http://127.0.0.1:8765/excalidraw/edit?"
        "path=external%3Aopaque-grant&token=local%20token"
    )
    assert grant == "opaque-grant"
    window.close()


@pytest.mark.parametrize(
    ("suffix", "expected_fragment"),
    [
        (".puml", "@startuml"),
        (".mmd", "flowchart TD"),
        (".excalidraw", '"type": "excalidraw"'),
    ],
)
def test_new_diagram_inline_name_preserves_extension_and_opens_editor(
        tmp_path, monkeypatch, app, suffix, expected_fragment):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from sp.app.folder_navigator.window import Window

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    window = Window(tmp_path)
    opened = []
    monkeypatch.setattr(window, "_open_specialized_editor", opened.append)
    monkeypatch.setattr(
        window, "_load_diagram_preview", lambda _path, **_kwargs: None
    )
    window._begin_new_file(
        tmp_path,
        window.model.index(str(tmp_path)),
        diagram_suffix=suffix,
    )

    assert window.new_file_edit.text() == f"diagram{suffix}"
    assert window.new_file_edit.selectedText() == "diagram"
    QTest.keyClicks(window.new_file_edit, "architecture")
    QTest.keyClick(window.new_file_edit, Qt.Key_Return)
    app.processEvents()

    created = tmp_path / f"architecture{suffix}"
    assert created.is_file()
    assert expected_fragment in created.read_text(encoding="utf-8")
    assert opened == [created]
    assert window.active_tab().path == created
    window.close()


def test_folder_context_menu_exposes_all_new_diagram_actions(
        tmp_path, monkeypatch, app):
    from PySide6.QtWidgets import QMenu
    from sp.app.folder_navigator.window import Window

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    window = Window(tmp_path)
    menu = QMenu(window)
    window._add_new_diagram_actions(
        menu, tmp_path, window.model.index(str(tmp_path))
    )

    assert [action.text() for action in menu.actions()] == [
        "New PlantUML Diagram",
        "New Mermaid Diagram",
        "New Excalidraw Diagram",
    ]
    window.close()


def test_file_context_menu_groups_actions_and_renames_open_file(
        tmp_path, monkeypatch, app):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from sp.app.folder_navigator.window import Window

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    source = tmp_path / "notes.txt"
    source.write_text("hello", encoding="utf-8")
    window = Window(tmp_path)
    window.show()
    window.open_file(source, pinned=True)
    window.toggle_bookmark(source)
    window.catalog_db.upsert_paths([source])

    menu = window._create_tree_context_menu(window.model.index(str(source)))
    labels = [action.text() for action in menu.actions() if not action.isSeparator()]
    assert labels == [
        "Open", "Open in New Tab", "Keep Open", "Open in Default Application",
        "Rename…", "Delete File…", "New", "Add to AI Chat Context", "Remove Bookmark",
        "Reveal in File Manager", "Copy Path", "Open Terminal Here",
    ]
    new_menu = next(action.menu() for action in menu.actions() if action.text() == "New")
    assert [action.text() for action in new_menu.actions() if not action.isSeparator()] == [
        "File", "Folder", "New PlantUML Diagram", "New Mermaid Diagram",
        "New Excalidraw Diagram",
    ]

    rename_action = next(action for action in menu.actions() if action.text() == "Rename…")
    conflict = tmp_path / "taken.txt"
    conflict.write_text("keep", encoding="utf-8")
    rename_action.trigger()
    app.processEvents()
    edit = window.rename_file_edit
    assert edit.text() == "notes.txt"
    assert edit.selectedText() == "notes"
    assert edit.geometry().y() == window.tree.visualRect(window.model.index(str(source))).y()
    QTest.keyClicks(edit, "taken")
    QTest.keyClick(edit, Qt.Key_Return)
    assert source.exists() and conflict.read_text(encoding="utf-8") == "keep"
    assert window.rename_file_edit is edit

    QTest.keyClicks(edit, "renamed.txt")
    QTest.keyClick(edit, Qt.Key_Return)
    target = tmp_path / "renamed.txt"
    assert target.read_text(encoding="utf-8") == "hello"
    assert not source.exists()
    assert window.active_tab().path == target
    assert str(target) in window._bookmarks() and str(source) not in window._bookmarks()
    assert target in window.catalog_db.candidates("renamed", tmp_path)
    assert source not in window.catalog_db.candidates("notes", tmp_path)
    assert window.rename_file_edit is None
    menu.deleteLater()
    window.close()


def test_inline_rename_escape_keeps_file(tmp_path, monkeypatch, app):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from sp.app.folder_navigator.window import Window

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    source = tmp_path / "notes.md"
    source.write_text("# Notes\n", encoding="utf-8")
    window = Window(tmp_path)
    window.show()
    window._rename_file(source)
    edit = window.rename_file_edit
    QTest.keyClicks(edit, "other")
    QTest.keyClick(edit, Qt.Key_Escape)
    app.processEvents()

    assert window.rename_file_edit is None
    assert source.exists()
    assert not (tmp_path / "other.md").exists()
    window.close()


def test_file_delete_confirms_and_protects_unsaved_tab(tmp_path, monkeypatch, app):
    from PySide6.QtWidgets import QMessageBox
    from sp.app.folder_navigator.window import Window

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    source = tmp_path / "notes.txt"
    source.write_text("hello", encoding="utf-8")
    window = Window(tmp_path)
    window.open_file(source, pinned=True)
    window.toggle_bookmark(source)
    window.catalog_db.upsert_paths([source])
    moved = []
    monkeypatch.setattr(window, "_move_file_to_trash", lambda path: (moved.append(path), path.unlink(), True)[-1])
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Cancel)
    window._delete_file(source)
    assert source.exists() and moved == []

    window.active_tab().editor.insertPlainText("unsaved")
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes)
    window._delete_file(source)
    assert source.exists() and moved == []

    window.active_tab().editor.document().setModified(False)
    window._delete_file(source)
    assert moved == [source]
    assert not source.exists()
    assert window._index_for(source) == -1
    assert str(source) not in window._bookmarks()
    assert source not in window.catalog_db.candidates("notes", tmp_path)
    window.close()


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


def test_excalidraw_launcher_uses_isolated_webengine_process_and_revokes_grant(
        tmp_path, monkeypatch, app):
    from sp.app.folder_navigator.window import Window

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("SP_DISABLE_EXCALIDRAW_WEBENGINE", raising=False)
    drawing = tmp_path / "diagram.excalidraw"
    drawing.write_text('{"type":"excalidraw","elements":[]}', encoding="utf-8")
    window = Window(tmp_path)
    monkeypatch.setattr(
        window,
        "_excalidraw_editor_url",
        lambda path, include_grant=False: (
            "http://127.0.0.1:8765/excalidraw/edit?path=external%3Agrant",
            "grant",
        ),
    )
    revoked = []
    refreshed = []
    monkeypatch.setattr(window, "_revoke_excalidraw_grant", revoked.append)
    monkeypatch.setattr(window, "_specialized_file_saved", refreshed.append)

    class FakeProcess:
        returncode = None

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = -15

    launched = {}
    process = FakeProcess()

    def fake_popen(command, **kwargs):
        launched.update(command=command, kwargs=kwargs)
        return process

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    window._open_specialized_editor(drawing)

    assert launched["command"][1:3] == ["-m", "sp.app.excalidraw_webview_process"]
    assert launched["command"][-2:] == ["--title", "Excalidraw - diagram.excalidraw"]
    assert len(window.excalidraw_processes) == 1
    assert window.excalidraw_process_timer.isActive()

    process.returncode = 0
    window._poll_excalidraw_processes()
    assert window.excalidraw_processes == []
    assert revoked == ["grant"]
    assert refreshed == [str(drawing)]
    assert not window.excalidraw_process_timer.isActive()
    window.close()


def test_specialized_editor_save_refreshes_existing_source_tab(
        tmp_path, monkeypatch, app):
    from sp.app.folder_navigator.window import Window

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    diagram = tmp_path / "diagram.puml"
    diagram.write_text("@startuml\nA -> B\n@enduml\n", encoding="utf-8")
    window = Window(tmp_path)
    window.open_file(diagram, pinned=True, force_text=True)
    tab = window.active_tab()
    assert "A -> B" in tab.editor.toPlainText()

    diagram.write_text("@startuml\nA -> C\n@enduml\n", encoding="utf-8")
    window._specialized_file_saved(str(diagram))
    app.processEvents()

    assert "A -> C" in tab.editor.toPlainText()
    assert "File reloaded after an external change" in tab.notice.text()
    tab.editor.document().setModified(False)
    window.close()


def test_inline_new_file_creates_and_opens_editor(tmp_path, monkeypatch, app):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from sp.app.folder_navigator.window import Window

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    folder = tmp_path / "notes"
    folder.mkdir()
    window = Window(tmp_path)
    window.show()
    window._begin_new_file(folder, window.model.index(str(folder)))
    edit = window.new_file_edit

    QTest.keyClicks(edit, "new-note.txt")
    QTest.keyClick(edit, Qt.Key_Return)
    app.processEvents()

    created = folder / "new-note.txt"
    assert created.is_file()
    assert window.new_file_edit is None
    assert window.active_tab().path == created
    assert window.active_tab().pinned
    assert window.active_tab().editor.hasFocus()
    window.active_tab().editor.document().setModified(False)
    window.close()


def test_inline_new_file_escape_cancels(tmp_path, monkeypatch, app):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from sp.app.folder_navigator.window import Window

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    window = Window(tmp_path)
    window.show()
    window._begin_new_file(tmp_path, window.model.index(str(tmp_path)))
    edit = window.new_file_edit
    QTest.keyClicks(edit, "cancel.txt")
    QTest.keyClick(edit, Qt.Key_Escape)
    app.processEvents()

    assert window.new_file_edit is None
    assert not (tmp_path / "cancel.txt").exists()
    window.close()


def test_inline_new_folder_creates_directory(tmp_path, monkeypatch, app):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from sp.app.folder_navigator.window import Window

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    parent = tmp_path / "notes"
    parent.mkdir()
    window = Window(tmp_path)
    window.show()
    window._begin_new_file(
        parent, window.model.index(str(parent)), create_folder=True
    )
    edit = window.new_file_edit

    QTest.keyClicks(edit, "projects")
    QTest.keyClick(edit, Qt.Key_Return)
    QTest.qWait(120)
    app.processEvents()

    assert (parent / "projects").is_dir()
    assert window.new_file_edit is None
    assert window.active_tab() is None
    assert window.tree.hasFocus()
    window.close()


def test_empty_tree_context_new_folder_targets_displayed_level(
        tmp_path, monkeypatch, app):
    from PySide6.QtCore import QPoint, Qt, QTimer
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QMenu
    from sp.app.folder_navigator.window import Window

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    window = Window(tmp_path)
    window.show()

    def choose_new_folder():
        menu = QApplication.activePopupWidget()
        assert isinstance(menu, QMenu)
        new_menu = next(action.menu() for action in menu.actions() if action.text() == "New")
        action = next(action for action in new_menu.actions() if action.text() == "Folder")
        action.trigger()
        menu.close()

    QTimer.singleShot(0, choose_new_folder)
    window._tree_menu(QPoint(window.tree.viewport().width() - 1,
                             window.tree.viewport().height() - 1))
    assert window.new_file_directory == tmp_path
    assert window.new_file_is_folder

    QTest.keyClicks(window.new_file_edit, "created-here")
    QTest.keyClick(window.new_file_edit, Qt.Key_Return)
    QTest.qWait(120)
    app.processEvents()

    assert (tmp_path / "created-here").is_dir()
    window.close()


def test_right_click_targets_tree_item_without_previewing_it(
        tmp_path, monkeypatch, app):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from sp.app.folder_navigator.window import Window

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")
    window = Window(tmp_path)
    window.show()
    window.open_file(first)
    window.reveal_tree(first)
    app.processEvents()

    first_index = window.model.index(str(first))
    second_index = window.model.index(str(second))
    window.tree.scrollTo(second_index)
    app.processEvents()
    point = window.tree.visualRect(second_index).center()
    event = QMouseEvent(
        QEvent.MouseButtonPress,
        QPointF(point),
        QPointF(window.tree.viewport().mapToGlobal(point)),
        Qt.RightButton,
        Qt.RightButton,
        Qt.NoModifier,
    )
    window.tree.mousePressEvent(event)
    app.processEvents()

    assert window.tree.currentIndex() == first_index
    assert window.active_tab().path == first
    assert window._index_for(second) == -1
    window.close()


def test_recent_tab_switcher_waits_for_modifier_release(tmp_path, monkeypatch, app):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from sp.app.folder_navigator.window import Window
    from sp.app.ui.keyboard_shortcuts import history_cycle_modifier_release_key

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    paths = [tmp_path / name for name in ("first.txt", "second.txt", "third.txt")]
    for path in paths:
        path.write_text(path.stem, encoding="utf-8")
    window = Window(tmp_path)
    window.show()
    for path in paths:
        window.open_file(path, pinned=True)

    assert window.active_tab().path == paths[2]
    window.reveal_tree(paths[2])
    app.processEvents()
    assert window.tree.hasFocus()
    assert window.active_tab().path == paths[2]
    window._cycle_tab_popup(False)
    assert window.tab_switcher.isVisible()
    assert window.tab_switcher_paths[window.tab_switcher_index] == paths[1]
    assert window.active_tab().path == paths[2]

    window._cycle_tab_popup(False)
    assert window.tab_switcher_paths[window.tab_switcher_index] == paths[0]
    assert window.active_tab().path == paths[2]

    release = QKeyEvent(
        QEvent.KeyRelease,
        history_cycle_modifier_release_key(),
        Qt.NoModifier,
    )
    assert window.eventFilter(window, release)
    assert window.active_tab().path == paths[0]
    assert window.active_tab().editor.hasFocus()
    assert not window.tab_switcher.isVisible()
    for tab in window.all_tabs():
        tab.editor.document().setModified(False)
    window.close()


def test_tab_switcher_defers_source_and_markdown_enhancements_until_settled(
        tmp_path, monkeypatch, app):
    from sp.app.folder_navigator.window import Window

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    source = tmp_path / "first.py"
    markdown = tmp_path / "second.md"
    source.write_text("print('first')\n", encoding="utf-8")
    markdown.write_text("# Second\n", encoding="utf-8")
    window = Window(tmp_path)
    window.open_file(source, pinned=True, defer_enhancements=True)
    source_tab = window.active_tab()
    window.open_file(markdown, pinned=True, defer_enhancements=True)
    markdown_tab = window.active_tab()

    assert window.pending_markdown_preview is markdown_tab
    assert window.markdown_preview_timer.isActive()
    window._cycle_tab_popup(False)

    assert window.pending_markdown_preview is None
    assert not window.markdown_preview_timer.isActive()
    window._activate_tab_switcher_selection()

    assert window.active_tab() is source_tab
    assert window.pending_markdown_preview is source_tab
    assert window.markdown_preview_timer.isActive()
    for tab in window.all_tabs():
        tab.editor.document().setModified(False)
    window.close()


def test_mouse_clicking_tab_focuses_its_editor(tmp_path, monkeypatch, app):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from sp.app.folder_navigator.window import Window

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    paths = [tmp_path / name for name in ("first.txt", "second.txt")]
    for path in paths:
        path.write_text(path.stem, encoding="utf-8")
    window = Window(tmp_path)
    window.show()
    for path in paths:
        window.open_file(path, pinned=True)
    window.tree.setFocus()
    app.processEvents()

    tab_bar = window.tabs.tabBar()
    QTest.mouseClick(tab_bar, Qt.LeftButton, pos=tab_bar.tabRect(0).center())
    app.processEvents()

    assert window.active_tab().path == paths[0]
    assert window.active_tab().editor.hasFocus()
    for tab in window.all_tabs():
        tab.editor.document().setModified(False)
    window.close()


def test_opening_flyover_does_not_reserialize_existing_markdown_tabs(
        tmp_path, monkeypatch, app):
    from sp.app.folder_navigator.window import Window

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    markdown_paths = [tmp_path / f"notes-{index}.md" for index in range(3)]
    target = tmp_path / "target.txt"
    for path in markdown_paths:
        path.write_text("# Heading\n" + ("body\n" * 200), encoding="utf-8")
    target.write_text("target", encoding="utf-8")
    window = Window(tmp_path)
    for path in markdown_paths:
        window.open_file(path, pinned=True)

    for tab in window.all_tabs():
        monkeypatch.setattr(
            tab,
            "text_for_save",
            lambda: pytest.fail("existing Markdown tab was reserialized"),
        )

    window.open_file(target, defer_enhancements=True)

    assert window.active_tab().path == target
    window.active_tab().editor.document().setModified(False)
    window.close()


def test_dirty_tab_uses_attention_color_until_clean(tmp_path, monkeypatch, app):
    from PySide6.QtGui import QColor, QPalette
    from sp.app.folder_navigator.window import Window
    from sp.app.ui.theme import theme_value

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    path = tmp_path / "notes.txt"
    path.write_text("clean", encoding="utf-8")
    window = Window(tmp_path)
    window.open_file(path, pinned=True)
    tab = window.active_tab()
    index = window.tabs.indexOf(tab)

    tab.editor.document().setModified(False)
    tab.editor.insertPlainText(" changed")
    app.processEvents()
    assert window.tabs.tabText(index).startswith("● ")
    assert window.tabs.tabBar().tabTextColor(index) == QColor(
        str(theme_value("main_window.badge.dirty_bg", "#e57373"))
    )

    tab.editor.document().setModified(False)
    app.processEvents()
    assert window.tabs.tabText(index) == path.name
    assert window.tabs.tabBar().tabTextColor(index) == window.tabs.tabBar().palette().color(
        QPalette.WindowText
    )
    window.close()


def test_markdown_dirty_tab_tracks_transformed_buffer_and_saved_baseline(
        tmp_path, monkeypatch, app):
    from PySide6.QtGui import QColor, QPalette
    from sp.app.folder_navigator.window import Window
    from sp.app.ui.theme import theme_value

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    path = tmp_path / "notes.md"
    path.write_text("# Clean\n", encoding="utf-8")
    window = Window(tmp_path)
    window.open_file(path, pinned=True)
    tab = window.active_tab()
    index = window.tabs.indexOf(tab)

    # Editing immediately also exercises Markdown's display-symbol rewrite,
    # which does not produce the same modificationChanged sequence as a plain
    # text editor.
    tab.editor.insertPlainText("x")
    app.processEvents()
    assert tab.dirty
    assert window.tabs.tabText(index).startswith("● ")
    assert window.tabs.tabBar().tabTextColor(index) == QColor(
        str(theme_value("main_window.badge.dirty_bg", "#e57373"))
    )

    assert window.save_tab(tab)
    app.processEvents()
    assert window.statusBar().currentMessage() == "Saved"
    assert tab.notice.isHidden()
    assert not tab.dirty
    assert window.tabs.tabText(index) == path.name
    assert window.tabs.tabBar().tabTextColor(index) == window.tabs.tabBar().palette().color(
        QPalette.WindowText
    )

    tab.editor.insertPlainText("y")
    app.processEvents()
    assert tab.dirty
    assert window.tabs.tabText(index).startswith("● ")
    tab.editor.document().setModified(False)
    window.close()
