from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMenu

from sp.app.ui.attachments_panel import AttachmentsPanel


@pytest.fixture
def panel(qtbot, tmp_path):
    page = tmp_path / "Page.md"
    page.write_text("# Page")
    for name in ("Logo.PNG", "photo.jpg", "flow.mmd", "report.pdf", "backup.zip", "data.custom", ".hidden.pdf"):
        (tmp_path / name).write_text("attachment")
    widget = AttachmentsPanel()
    widget.resize(360, 500)
    qtbot.addWidget(widget)
    widget.set_page(page)
    return widget


def visible_items(panel):
    listing = panel.attachments_list
    return [listing.item(row) for row in range(listing.count()) if not listing.item(row).isHidden()]


def visible_files(panel):
    return [item.text().split(" (", 1)[0] for item in visible_items(panel) if item.data(Qt.UserRole)]


def set_type(panel, name):
    panel.type_filter.setCurrentIndex(panel.type_filter.findData(name))


def test_filename_and_type_filters_combine_and_clear_without_changing_count(panel):
    counts = []
    panel.countReady.connect(lambda page, count: counts.append(count))
    panel.filename_filter.setText("LOGO")
    assert visible_files(panel) == ["Logo.PNG"]
    set_type(panel, "Documents")
    assert visible_files(panel) == []
    assert panel.empty_label.isVisible()
    assert panel.empty_label.text() == "No attachments match these filters."
    panel.filename_filter.clear()
    assert visible_files(panel) == ["report.pdf"]
    panel.hidden_files_button.click()
    assert visible_files(panel) == [".hidden.pdf", "report.pdf"]
    assert counts == [6, 6, 6, 7]
    set_type(panel, "")
    assert len(visible_files(panel)) == 7
    assert not panel.empty_label.isVisible()


def test_groups_are_nonselectable_and_empty_groups_disappear(panel):
    panel.group_button.click()
    assert [item.text() for item in visible_items(panel)] == [
        "Images", "Logo.PNG", "photo.jpg", "Diagrams", "flow.mmd",
        "Documents", "report.pdf", "Archives", "backup.zip", "Other", "data.custom",
    ]
    headers = [item for item in visible_items(panel) if not item.data(Qt.UserRole)]
    for header in headers:
        assert not header.flags() & Qt.ItemIsSelectable
        assert not header.flags() & Qt.ItemIsDragEnabled
    panel.filename_filter.setText("flow")
    assert [item.text() for item in visible_items(panel)] == ["Diagrams", "flow.mmd"]
    panel.filename_filter.setText("no match")
    assert visible_items(panel) == []
    panel.filename_filter.clear()
    panel.group_button.click()
    assert all(item.data(Qt.UserRole) for item in visible_items(panel))


def test_filtering_deselects_hidden_files_before_delete(panel, tmp_path):
    report = panel.attachments_list.findItems("report.pdf", Qt.MatchExactly)[0]
    report.setSelected(True)
    assert panel.remove_button.isEnabled()
    set_type(panel, "Images")
    assert not panel.attachments_list.selectedItems()
    assert not panel.remove_button.isEnabled()
    panel._remove_selected_attachments()
    assert (tmp_path / "report.pdf").exists()


def test_grouping_keeps_selection_and_deletes_only_selected_files(panel, tmp_path):
    report = panel.attachments_list.findItems("report.pdf", Qt.MatchExactly)[0]
    panel.attachments_list.setCurrentItem(report)
    panel.group_button.click()
    assert [item.text() for item in panel.attachments_list.selectedItems()] == ["report.pdf"]
    panel._remove_selected_attachments()
    assert not (tmp_path / "report.pdf").exists()
    assert (tmp_path / "Page.md").exists()
    assert (tmp_path / "Logo.PNG").exists()
    assert "Documents" not in [item.text() for item in visible_items(panel)]


def test_zoom_and_refresh_keep_filters_and_groups(panel):
    panel.group_button.click()
    set_type(panel, "Diagrams")
    panel.filename_filter.setText("flow")
    panel._zoom_in()
    assert panel.attachments_list.columnCount() == 3
    assert panel.attachments_list.iconSize().width() == 48
    assert [item.text() for item in visible_items(panel)] == ["Diagrams", "flow.mmd"]
    panel.group_button.click()
    assert panel.attachments_list.columnCount() == 3
    assert visible_files(panel) == ["flow.mmd"]
    panel.refresh()
    assert visible_files(panel) == ["flow.mmd"]


def test_remote_filtering_and_grouping_do_not_refetch_or_mutate(qtbot, tmp_path, monkeypatch):
    requests = []

    class Client:
        def get(self, path, params):
            requests.append(path)
            return SimpleNamespace(status_code=200, raise_for_status=lambda: None,
                                   json=lambda: {"attachments": [
                                       {"attachment_path": "/Page/photo.PNG", "size_bytes": 2048},
                                       {"attachment_path": "/Page/report.pdf"},
                                   ]})

        def post(self, *args, **kwargs):
            pytest.fail("Filters must not change server files")

    widget = AttachmentsPanel(api_client=Client())
    qtbot.addWidget(widget)
    widget.set_remote_mode(True, "https://example.test")
    widget.set_remote_vault_root("/vault")
    monkeypatch.setattr(widget, "_get_remote_cache_root", lambda: tmp_path)
    widget.set_page(Path("/vault/Page/Page.md"))
    widget.group_button.click()
    widget.filename_filter.setText("PHOTO")
    set_type(widget, "Images")
    assert [item.text().split(" (", 1)[0] for item in visible_items(widget)] == ["Images", "photo.PNG"]
    assert requests == ["/files/"]
    file_item = next(item for item in visible_items(widget) if item.data(Qt.UserRole))
    assert widget.attachments_list.item(file_item.row(), 2).text() == "2.0 KB"


def test_open_containing_folder_button_and_context_action(panel, tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr("sp.app.ui.attachments_panel.open_path_in_system", opened.append)
    panel.open_folder_button.click()
    assert opened == [tmp_path]

    def choose_folder(menu, pos):
        action = next(action for action in menu.actions() if action.text() == "Open containing folder")
        assert action.isEnabled()
        action.trigger()

    class Menu(QMenu):
        def exec(self, pos):
            choose_folder(self, pos)

    monkeypatch.setattr("sp.app.ui.attachments_panel.QMenu", Menu)
    panel._on_attachments_context_menu(QPoint())
    assert opened == [tmp_path, tmp_path]
    panel.set_remote_mode(True, "https://example.test")
    panel._open_folder()
    assert opened == [tmp_path, tmp_path]
    assert not panel.open_folder_button.isEnabled()


def test_filtering_keeps_complete_file_list_for_sync(panel, monkeypatch):
    synced = []
    monkeypatch.setattr(panel, "_sync_with_server", lambda paths: synced.append({path.name for path in paths}))
    panel.filename_filter.setText("no match")
    panel.group_button.click()
    panel.refresh()
    assert visible_files(panel) == []
    assert synced == [{"Logo.PNG", "photo.jpg", "flow.mmd", "report.pdf", "backup.zip", "data.custom", ".hidden.pdf"}]


def click_sort(panel, column):
    header = panel.attachments_list.horizontalHeader()
    position = QPoint(header.sectionViewportPosition(column) + header.sectionSize(column) // 2,
                      header.height() // 2)
    QTest.mouseClick(header.viewport(), Qt.LeftButton, Qt.NoModifier, position)
    QTest.qWait(10)


def test_details_columns_sort_filenames_types_and_numeric_sizes(qtbot, tmp_path):
    page = tmp_path / "Page.md"
    page.touch()
    for name, size in (("a.pdf", 2048), ("b.png", 10), ("c.mmd", 2)):
        (tmp_path / name).write_bytes(b"x" * size)
    panel = AttachmentsPanel()
    panel.resize(500, 400)
    qtbot.addWidget(panel)
    panel.set_page(page)
    listing = panel.attachments_list
    assert [listing.horizontalHeaderItem(col).text() for col in range(3)] == ["Filename", "Type", "Size"]
    assert [listing.item(row, 2).text() for row in range(3)] == ["2.0 KB", "10 B", "2 B"]

    click_sort(panel, 2)
    order = listing.horizontalHeader().sortIndicatorOrder()
    assert visible_files(panel) == (["c.mmd", "b.png", "a.pdf"] if order == Qt.AscendingOrder else ["a.pdf", "b.png", "c.mmd"])
    click_sort(panel, 2)
    assert visible_files(panel) == (["a.pdf", "b.png", "c.mmd"] if order == Qt.AscendingOrder else ["c.mmd", "b.png", "a.pdf"])
    click_sort(panel, 1)
    types = [listing.item(row, 1).text() for row in range(3)]
    assert types == sorted(types, key=str.casefold, reverse=listing.horizontalHeader().sortIndicatorOrder() == Qt.DescendingOrder)
    click_sort(panel, 0)
    assert visible_files(panel) == sorted(visible_files(panel), reverse=listing.horizontalHeader().sortIndicatorOrder() == Qt.DescendingOrder)


def test_sorting_keeps_filters_and_group_headings_with_their_files(panel):
    panel.group_button.click()
    panel.filename_filter.setText(".p")
    click_sort(panel, 2)
    assert [item.text() for item in visible_items(panel)] == ["Images", "Logo.PNG", "Documents", "report.pdf"]
    panel.filename_filter.clear()
    click_sort(panel, 0)
    rows = [item.text() for item in visible_items(panel)]
    assert rows.index("Images") < rows.index("Logo.PNG") < rows.index("Diagrams")
    assert rows.index("Images") < rows.index("photo.jpg") < rows.index("Diagrams")


def test_remote_listing_reports_sizes_for_indexed_and_discovered_files(tmp_path, monkeypatch):
    from sp.server import api

    folder = tmp_path / "Page"
    folder.mkdir()
    (folder / "Page.md").touch()
    (folder / "indexed.pdf").write_bytes(b"123")
    (folder / "discovered.png").write_bytes(b"1234567")
    monkeypatch.setattr(api, "_get_vault_root", lambda: tmp_path)
    monkeypatch.setattr(api.config, "list_page_attachments", lambda page: [{"attachment_path": "/Page/indexed.pdf"}])
    monkeypatch.setattr(api.config, "upsert_attachment_entry", lambda *args, **kwargs: None)
    result = api.list_files("/Page/Page.md")
    assert {entry["attachment_path"]: entry["size_bytes"] for entry in result["attachments"]} == {
        "/Page/indexed.pdf": 3, "/Page/discovered.png": 7,
    }
    assert {entry["attachment_path"]: entry["mtime_ns"] for entry in result["attachments"]} == {
        "/Page/indexed.pdf": (folder / "indexed.pdf").stat().st_mtime_ns,
        "/Page/discovered.png": (folder / "discovered.png").stat().st_mtime_ns,
    }
