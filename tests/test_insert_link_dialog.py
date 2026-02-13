"""Unit tests for Insert Link Dialog behavior."""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from sp.app.ui.insert_link_dialog import InsertLinkDialog


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def patch_dialog_config(monkeypatch):
    monkeypatch.setattr("sp.app.ui.insert_link_dialog.config.load_dialog_geometry", lambda *_: None)
    monkeypatch.setattr("sp.app.ui.insert_link_dialog.config.save_dialog_geometry", lambda *_: None)
    monkeypatch.setattr("sp.app.ui.insert_link_dialog.config.search_pages", lambda *_: [])


def test_dialog_creation(app):
    dialog = InsertLinkDialog()
    assert dialog.windowTitle() == "Insert Link"
    dialog.close()


def test_selected_text_seeds_link_name_and_cleans_line_breaks(app):
    dialog = InsertLinkDialog(selected_text="Line One\nLine Two")
    QApplication.processEvents()

    assert dialog.search.text() == ""
    assert dialog.link_name.text() == "Line One Line Two"

    dialog.close()


def test_selected_colon_path_normalizes_non_http(app):
    dialog = InsertLinkDialog()
    dialog.search.setText("  :Page A:Child Node#My Anchor  ")

    assert dialog.selected_colon_path() == ":Page_A:Child_Node#My Anchor"
    dialog.close()


def test_selected_colon_path_preserves_http(app):
    dialog = InsertLinkDialog()
    dialog.search.setText("https://example.com/some path")

    assert dialog.selected_colon_path() == "https://example.com/some path"
    dialog.close()


def test_link_name_manual_edit_stops_auto_populate(app):
    dialog = InsertLinkDialog()

    dialog.search.setText("PageA")
    QApplication.processEvents()
    assert dialog.link_name.text() == "PageA"

    dialog.link_name.setText("Custom")
    QApplication.processEvents()

    dialog.search.setText("PageB")
    QApplication.processEvents()

    assert dialog.link_name.text() == "Custom"
    dialog.close()


def test_http_search_auto_populates_link_name_when_not_manually_edited(app):
    dialog = InsertLinkDialog()
    dialog.search.setText("https://example.com")
    QApplication.processEvents()

    assert dialog.link_name.text() == "https://example.com"
    assert dialog.list_widget.count() == 0

    dialog.close()


def test_filter_can_be_removed(app):
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


def test_return_key_accepts_dialog_when_text_exists(app):
    from PySide6.QtTest import QTest

    dialog = InsertLinkDialog()
    dialog.show()
    dialog.search.setText(":PageA")

    QTest.keyClick(dialog.search, Qt.Key_Return)
    QApplication.processEvents()

    assert dialog.result() == dialog.Accepted
    dialog.close()
