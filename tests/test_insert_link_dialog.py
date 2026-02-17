"""Unit tests for Insert Link Dialog behavior."""

import pytest
from PySide6.QtCore import Qt

from sp.app.ui.insert_link_dialog import InsertLinkDialog


@pytest.fixture(autouse=True)
def patch_dialog_config(monkeypatch):
    monkeypatch.setattr("sp.app.ui.insert_link_dialog.config.load_dialog_geometry", lambda *_: None)
    monkeypatch.setattr("sp.app.ui.insert_link_dialog.config.save_dialog_geometry", lambda *_: None)
    monkeypatch.setattr("sp.app.ui.insert_link_dialog.config.search_pages", lambda *_: [])


def test_dialog_creation(qapp):
    dialog = InsertLinkDialog()
    assert dialog.windowTitle() == "Insert Link"
    dialog.close()


def test_selected_text_seeds_link_name_and_cleans_line_breaks(qapp):
    dialog = InsertLinkDialog(selected_text="Line One\nLine Two")
    qapp.processEvents()

    assert dialog.search.text() == ""
    assert dialog.link_name.text() == "Line One Line Two"

    dialog.close()


def test_selected_colon_path_normalizes_non_http(qapp):
    dialog = InsertLinkDialog()
    dialog.search.setText("  :Page A:Child Node#My Anchor  ")

    assert dialog.selected_colon_path() == ":Page_A:Child_Node#My Anchor"
    dialog.close()


def test_selected_colon_path_preserves_http(qapp):
    dialog = InsertLinkDialog()
    dialog.search.setText("https://example.com/some path")

    assert dialog.selected_colon_path() == "https://example.com/some path"
    dialog.close()


def test_link_name_manual_edit_stops_auto_populate(qapp):
    dialog = InsertLinkDialog()

    dialog.search.setText("PageA")
    qapp.processEvents()
    assert dialog.link_name.text() == "PageA"

    dialog.link_name.setText("Custom")
    qapp.processEvents()

    dialog.search.setText("PageB")
    qapp.processEvents()

    assert dialog.link_name.text() == "Custom"
    dialog.close()


def test_http_search_auto_populates_link_name_when_not_manually_edited(qapp):
    dialog = InsertLinkDialog()
    dialog.search.setText("https://example.com")
    qapp.processEvents()

    assert dialog.link_name.text() == "https://example.com"
    assert dialog.list_widget.count() == 0

    dialog.close()


def test_filter_can_be_removed(qapp):
    filter_cleared = {"called": False}

    def clear_filter() -> None:
        filter_cleared["called"] = True

    dialog = InsertLinkDialog(
        filter_prefix="/PageA",
        filter_label=":PageA",
        clear_filter_cb=clear_filter,
    )

    assert dialog.filter_banner is not None
    assert ":PageA" in dialog.filter_banner.text()

    dialog._on_remove_filter("remove")

    assert filter_cleared["called"] is True
    assert dialog.filter_banner is not None
    assert not dialog.filter_banner.isVisible()

    dialog.close()


def test_return_key_accepts_dialog_when_text_exists(qapp):
    from PySide6.QtTest import QTest

    dialog = InsertLinkDialog()
    dialog.show()
    dialog.search.setText(":PageA")

    QTest.keyClick(dialog.search, Qt.Key_Return)
    qapp.processEvents()

    assert dialog.result() == dialog.Accepted
    dialog.close()


def test_create_option_is_shown_for_nonexistent_target(qapp, monkeypatch):
    monkeypatch.setattr("sp.app.ui.insert_link_dialog.config.search_pages", lambda *_: [])
    dialog = InsertLinkDialog(current_page_path="/Projects/Projects.md")
    dialog.show()
    dialog.search.setText("Plan 2026")
    qapp.processEvents()

    first = dialog.list_widget.item(0)
    assert first is not None
    payload = first.data(Qt.UserRole)
    assert isinstance(payload, dict)
    assert payload.get("create") is True
    assert payload.get("target") == ":Projects:Plan_2026"
    assert "Create new page 'Plan 2026' at 'Projects'" in first.text()

    dialog.list_widget.setCurrentRow(0)
    assert dialog._activate_current() is True
    assert dialog.should_create_new_page() is True
    assert dialog.selected_colon_path() == ":Projects:Plan_2026"
    dialog.close()


def test_create_option_is_still_shown_when_leaf_name_exists_elsewhere(qapp, monkeypatch):
    monkeypatch.setattr(
        "sp.app.ui.insert_link_dialog.config.search_pages",
        lambda *_: [{"path": "/Work/Plan_2026/Plan_2026.md", "title": "Plan 2026"}],
    )
    dialog = InsertLinkDialog(current_page_path="/Projects/Projects.md")
    dialog.show()
    dialog.search.setText("Plan 2026")
    qapp.processEvents()

    assert dialog.list_widget.count() >= 1
    payload = dialog.list_widget.item(0).data(Qt.UserRole)
    assert isinstance(payload, dict)
    assert payload.get("create") is True
    assert payload.get("target") == ":Projects:Plan_2026"
    assert dialog.should_create_new_page() is True
    dialog.close()


def test_create_option_not_shown_when_target_exists_under_current_parent(qapp, monkeypatch):
    monkeypatch.setattr(
        "sp.app.ui.insert_link_dialog.config.search_pages",
        lambda *_: [{"path": "/Projects/Plan_2026/Plan_2026.md", "title": "Plan 2026"}],
    )
    dialog = InsertLinkDialog(current_page_path="/Projects/Projects.md")
    dialog.show()
    dialog.search.setText("Plan 2026")
    qapp.processEvents()

    assert dialog.list_widget.count() == 1
    payload = dialog.list_widget.item(0).data(Qt.UserRole)
    assert not isinstance(payload, dict)
    assert dialog.should_create_new_page() is False
    dialog.close()


def test_anchor_does_not_trigger_create_when_base_page_exists(qapp, monkeypatch):
    monkeypatch.setattr(
        "sp.app.ui.insert_link_dialog.config.search_pages",
        lambda *_: [{"path": "/Journal/2026/02/17/17/17.md", "title": "Tuesday, February 17 2026"}],
    )
    dialog = InsertLinkDialog(current_page_path="/Projects/Projects.md")
    dialog.show()
    dialog.search.setText(":Journal:2026:02:17:17#cush-sap")
    qapp.processEvents()

    assert dialog.list_widget.count() >= 1
    first_payload = dialog.list_widget.item(0).data(Qt.UserRole)
    assert not isinstance(first_payload, dict)
    dialog.close()


def test_anchor_is_preserved_when_accepting_existing_page(qapp, monkeypatch):
    monkeypatch.setattr(
        "sp.app.ui.insert_link_dialog.config.search_pages",
        lambda *_: [{"path": "/Journal/2026/02/17/17/17.md", "title": "Tuesday, February 17 2026"}],
    )
    dialog = InsertLinkDialog(current_page_path="/Projects/Projects.md")
    dialog.show()
    dialog.search.setText(":Journal:2026:02:17:17#cush-sap")
    qapp.processEvents()

    dialog.list_widget.setCurrentRow(0)
    assert dialog._activate_current() is True
    assert dialog.should_create_new_page() is False
    assert dialog.selected_colon_path() == ":Journal:2026:02:17:17#cush-sap"
    dialog.close()
