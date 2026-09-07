from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QWidget

from sp.app.ui.tags_tab import TagChicklet, TagsTab


def test_selected_tag_chicklet_uses_vault_accent(qtbot) -> None:
    chicklet = TagChicklet("project", accent_color="#6a3fc8")
    qtbot.addWidget(chicklet)

    chicklet.setChecked(True)

    style = chicklet.styleSheet().lower()
    assert "background-color: #6a3fc8" in style
    assert "border: 2px solid #6a3fc8" in style

    focus_rule = style.split("qpushbutton:focus", 1)[1].split("}", 1)[0]
    assert "background-color: #" in focus_rule
    assert "background-color: #6a3fc8" not in focus_rule
    assert "color: #" in focus_rule
    assert "border: 3px" not in focus_rule


def test_selected_tag_text_contrasts_light_and_dark_focus_backgrounds(qtbot) -> None:
    light = TagChicklet("light", accent_color="#f5e663")
    dark = TagChicklet("dark", accent_color="#201040")
    qtbot.addWidget(light)
    qtbot.addWidget(dark)

    light.setChecked(True)
    dark.setChecked(True)

    light_style = light.styleSheet().lower()
    dark_style = dark.styleSheet().lower()
    assert "color: #111111" in light_style
    assert "color: #ffffff" in dark_style
    for style in (light_style, dark_style):
        focus_rule = style.split("qpushbutton:focus", 1)[1].split("}", 1)[0]
        assert "background-color: #" in focus_rule
        assert "color: #" in focus_rule


def test_tags_tab_forwards_changed_accent_to_existing_chicklets(qtbot) -> None:
    tab = TagsTab()
    qtbot.addWidget(tab)
    tab._add_tag_chicklet("project", 1)

    tab.set_vault_accent_color("#d97706")
    tab.tag_chicklets["project"].setChecked(True)

    assert "background-color: #d97706" in tab.tag_chicklets["project"].styleSheet().lower()


def test_loading_tags_reads_current_vault_accent(qtbot, monkeypatch) -> None:
    tab = TagsTab()
    qtbot.addWidget(tab)
    monkeypatch.setattr("sp.app.config.load_vault_accent_color", lambda: "#b84ee8")
    monkeypatch.setattr("sp.app.config._get_conn", lambda: object())
    monkeypatch.setattr(tab, "_fetch_tag_summary", lambda _conn: [("project", 1)])

    tab._load_tags()
    tab.tag_chicklets["project"].setChecked(True)

    style = tab.tag_chicklets["project"].styleSheet().lower()
    assert "background-color: #b84ee8" in style


def test_hidden_stale_tags_wait_until_tab_is_shown(qtbot, qapp, monkeypatch) -> None:
    host = QWidget()
    tab = TagsTab(host)
    qtbot.addWidget(host)
    loads: list[bool] = []
    monkeypatch.setattr(tab, "_load_tags", lambda: loads.append(True))
    tab._tags_loaded = True

    tab.mark_tags_stale()
    assert loads == []

    host.show()
    tab.show()
    qapp.processEvents()
    assert loads == [True]
    assert tab._tags_stale is False


def test_reloading_tags_does_not_accumulate_flow_layout_items(qtbot, monkeypatch) -> None:
    tab = TagsTab()
    qtbot.addWidget(tab)
    monkeypatch.setattr(tab, "_fetch_tag_summary", lambda _conn: [("one", 1), ("two", 2)])
    monkeypatch.setattr("sp.app.config._get_conn", lambda: object())

    tab._load_tags()
    tab._load_tags()

    assert tab.tags_layout.count() == 2
    assert set(tab.tag_chicklets) == {"one", "two"}


def test_keyboard_moves_from_search_through_tags_to_results(qtbot, qapp, monkeypatch) -> None:
    tab = TagsTab()
    qtbot.addWidget(tab)
    monkeypatch.setattr(tab, "_refresh_results", lambda: None)
    tab._add_tag_chicklet("amazon", 1)
    tab._add_tag_chicklet("azure", 1)
    tab._display_results(["/Page/Page.md"])
    tab.show()
    qapp.processEvents()

    tab.tag_search.setFocus()
    QTest.keyClick(tab.tag_search, Qt.Key_Tab)
    assert tab.tag_chicklets["amazon"].hasFocus()

    QTest.keyClick(tab.tag_chicklets["amazon"], Qt.Key_Return)
    assert tab.tag_chicklets["amazon"].isChecked()
    assert tab.selected_tags == {"amazon"}

    QTest.keyClick(tab.tag_chicklets["amazon"], Qt.Key_Tab)
    assert tab.tag_chicklets["azure"].hasFocus()
    QTest.keyClick(tab.tag_chicklets["azure"], Qt.Key_Tab)
    assert tab.results_tree.hasFocus()


def test_results_tab_navigation_and_enter_focus_semantics(qtbot, qapp) -> None:
    tab = TagsTab()
    qtbot.addWidget(tab)
    tab._add_tag_chicklet("amazon", 1)
    tab._display_results(["/One/One.md", "/Two/Two.md"])
    tab.show()
    qapp.processEvents()
    kept_focus: list[str] = []
    editor_focus: list[str] = []
    tab.pageNavigationRequested.connect(lambda path, _line: kept_focus.append(path))
    tab.pageNavigationWithEditorFocusRequested.connect(
        lambda path, _line: editor_focus.append(path)
    )

    tab.results_tree.setFocus()
    tab.results_tree.setCurrentItem(tab.results_tree.topLevelItem(0))
    QTest.keyClick(tab.results_tree, Qt.Key_Tab)
    assert tab.results_tree.currentItem().data(0, Qt.UserRole) == "/Two/Two.md"
    QTest.keyClick(tab.results_tree, Qt.Key_Backtab, Qt.ShiftModifier)
    assert tab.results_tree.currentItem().data(0, Qt.UserRole) == "/One/One.md"

    QTest.keyClick(tab.results_tree, Qt.Key_Return, Qt.ShiftModifier)
    assert kept_focus == ["/One/One.md"]
    assert editor_focus == []
    QTest.keyClick(tab.results_tree, Qt.Key_Return)
    assert editor_focus == ["/One/One.md"]


def test_journal_day_result_uses_recent_page_date_label(qtbot) -> None:
    tab = TagsTab()
    qtbot.addWidget(tab)

    tab._display_results(
        [
            "/Journal/2026/04/28/28.md",
            "/Projects/Launch/Launch.md",
        ]
    )

    assert tab.results_tree.topLevelItem(0).text(0) == "28-Apr-26"
    assert tab.results_tree.topLevelItem(1).text(0) == "Launch"
    assert (
        tab.results_tree.topLevelItem(0).data(0, Qt.UserRole)
        == "/Journal/2026/04/28/28.md"
    )


def test_focus_visuals_identify_tag_and_results_targets(qtbot, qapp) -> None:
    tab = TagsTab()
    qtbot.addWidget(tab)
    tab.set_vault_accent_color("#7c3aed")
    tab._add_tag_chicklet("amazon", 1)
    tab._display_results(["/One/One.md"])
    tab.show()
    qapp.processEvents()

    chicklet = tab.tag_chicklets["amazon"]
    chicklet.setFocus()
    qapp.processEvents()
    assert tab.focus_indicator.text() == "FOCUS · #amazon"
    assert "border: 2px solid #7c3aed" in tab.tags_scroll_area.styleSheet().lower()
    assert "qpushbutton:focus" in chicklet.styleSheet().lower()

    tab.results_tree.setFocus()
    qapp.processEvents()
    assert tab.focus_indicator.text() == "FOCUS · PAGES"
    assert "border: 2px solid #7c3aed" in tab.results_tree.styleSheet().lower()


def test_vi_keys_move_between_tag_chicklets_and_into_results(
    qtbot, qapp, monkeypatch
) -> None:
    host = QWidget()
    host._vi_enabled = True
    tab = TagsTab(host)
    qtbot.addWidget(host)
    monkeypatch.setattr(tab, "_refresh_results", lambda: None)
    tab._add_tag_chicklet("one", 1)
    tab._add_tag_chicklet("two", 1)
    tab._display_results(["/Page/Page.md"])
    host.show()
    tab.show()
    qapp.processEvents()

    first = tab.tag_chicklets["one"]
    second = tab.tag_chicklets["two"]
    first.setFocus()
    QTest.keyClick(first, Qt.Key_L)
    assert second.hasFocus()
    QTest.keyClick(second, Qt.Key_H)
    assert first.hasFocus()
    QTest.keyClick(first, Qt.Key_J)
    assert second.hasFocus()
    QTest.keyClick(second, Qt.Key_J)
    assert tab.results_tree.hasFocus()


def test_unselected_tag_hover_style_has_explicit_contrasting_text(qtbot) -> None:
    chicklet = TagChicklet("project", accent_color="#7c3aed")
    qtbot.addWidget(chicklet)

    style = chicklet.styleSheet().lower()

    assert "qpushbutton:hover" in style
    assert "background-color: palette(midlight)" not in style
    hover_rule = style.split("qpushbutton:hover", 1)[1].split("}", 1)[0]
    assert "background-color: #" in hover_rule
    assert "color: #" in hover_rule
