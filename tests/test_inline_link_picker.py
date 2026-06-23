"""Unit tests for inline quick-link picker behavior."""

from PySide6.QtCore import Qt

from sp.app.ui.inline_link_picker import InlineLinkPickerOverlay


def test_quick_link_shows_create_option_when_no_exact_match(qapp, monkeypatch):
    monkeypatch.setattr("sp.app.ui.inline_link_picker.config.search_pages", lambda *_: [])
    overlay = InlineLinkPickerOverlay(
        parent=qapp.activeWindow() or None,
        current_page_path="/Area/Area.md",
    )
    overlay.search.setText("Sprint Plan")
    overlay._refresh()

    assert overlay.list_widget.count() == 1
    item = overlay.list_widget.item(0)
    payload = item.data(Qt.UserRole)
    assert isinstance(payload, dict)
    assert payload.get("create") is True
    assert payload.get("target") == ":Area:Sprint_Plan"


def test_quick_link_shows_create_option_even_when_exact_page_exists(qapp, monkeypatch):
    monkeypatch.setattr(
        "sp.app.ui.inline_link_picker.config.search_pages",
        lambda *_: [{"path": "/Area/Sprint_Plan/Sprint_Plan.md"}],
    )
    overlay = InlineLinkPickerOverlay(
        parent=qapp.activeWindow() or None,
        current_page_path="/Area/Area.md",
    )
    overlay.search.setText("Sprint Plan")
    overlay._refresh()

    # Create option at row 0, search result at row 1
    assert overlay.list_widget.count() == 2
    create_payload = overlay.list_widget.item(0).data(Qt.UserRole)
    assert isinstance(create_payload, dict)
    assert create_payload.get("create") is True
    search_payload = overlay.list_widget.item(1).data(Qt.UserRole)
    assert not isinstance(search_payload, dict)


def test_quick_link_preserves_anchor_when_accepting_existing_page(qapp, monkeypatch):
    monkeypatch.setattr(
        "sp.app.ui.inline_link_picker.config.search_pages",
        lambda *_: [{"path": "/Journal/2026/06/23/23.md"}],
    )
    overlay = InlineLinkPickerOverlay(
        parent=qapp.activeWindow() or None,
        current_page_path="/Area/Area.md",
    )
    overlay.search.setText(":Journal:2026:06:23#dk-questions")
    overlay._refresh()

    overlay.list_widget.setCurrentRow(1)
    overlay._accept_current()

    assert overlay.selected_path() == ":Journal:2026:06:23#dk-questions"
    assert overlay.is_new_page() is False


def test_quick_link_preserves_anchor_on_create_target(qapp, monkeypatch):
    monkeypatch.setattr("sp.app.ui.inline_link_picker.config.search_pages", lambda *_: [])
    overlay = InlineLinkPickerOverlay(
        parent=qapp.activeWindow() or None,
        current_page_path="/Area/Area.md",
    )
    overlay.search.setText(":Journal:2026:06:23#dk-questions")
    overlay._refresh()

    payload = overlay.list_widget.item(0).data(Qt.UserRole)

    assert isinstance(payload, dict)
    assert payload.get("target") == ":Journal:2026:06:23#dk-questions"
