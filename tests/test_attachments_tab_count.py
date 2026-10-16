from __future__ import annotations

from PySide6.QtWidgets import QWidget

from sp.app.ui.tabbed_right_panel import TabbedRightPanel


def _attachments_tab_text(panel: TabbedRightPanel) -> str:
    for index in range(panel.tabs.count()):
        if panel.tabs.widget(index) is panel.attachments_panel:
            return panel.tabs.tabText(index)
    raise AssertionError("Attachments tab was not found")


def _attachments_only_panel(qtbot) -> TabbedRightPanel:
    panel = TabbedRightPanel(
        enable_tasks=False,
        enable_calendar=False,
        enable_link_navigator=False,
        enable_map=False,
        enable_ai_chats=False,
    )
    qtbot.addWidget(panel)
    return panel


def test_attachment_count_is_hidden_until_deferred_listing_completes(
    qtbot, qapp, tmp_path
) -> None:
    page = tmp_path / "Page.md"
    page.write_text("# Page\n", encoding="utf-8")
    (tmp_path / "drawing.png").write_bytes(b"not-a-real-png")
    panel = _attachments_only_panel(qtbot)
    other_tab = QWidget()
    panel.tabs.addTab(other_tab, "Other")
    panel.tabs.setCurrentWidget(other_tab)
    panel.show()
    qapp.processEvents()

    panel.set_current_page(page)

    assert _attachments_tab_text(panel) == "Attachments"

    panel.tabs.setCurrentWidget(panel.attachments_panel)
    panel.sync_visible_panels()

    assert _attachments_tab_text(panel) == "Attachments (1)"


def test_definitive_empty_attachment_listing_shows_zero(qtbot, qapp, tmp_path) -> None:
    page = tmp_path / "Page.md"
    page.write_text("# Page\n", encoding="utf-8")
    panel = _attachments_only_panel(qtbot)
    panel.show()
    qapp.processEvents()

    panel.set_current_page(page)

    assert _attachments_tab_text(panel) == "Attachments (0)"
