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


def test_quick_link_hides_create_option_when_exact_page_exists(qapp, monkeypatch):
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

    assert overlay.list_widget.count() == 1
    payload = overlay.list_widget.item(0).data(Qt.UserRole)
    assert not isinstance(payload, dict)
