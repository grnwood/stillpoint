import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from sp.app import config


@pytest.mark.parametrize("reading", [False, True])
@pytest.mark.parametrize("key,panel_name", [
    ("tasks", "task_panel"), ("calendar", "calendar_panel"),
    ("links", "link_panel"), ("map", "map_panel"), ("ai", "ai_chat_panel"),
])
def test_header_icons_expand_rail_and_open_requested_tab(main_window, monkeypatch, reading, key, panel_name):
    window = main_window
    window.resize(1200, 800)
    monkeypatch.setattr(config, "load_enable_ai_chats", lambda: True)
    window._feature_tasks_enabled = True
    window._feature_calendar_enabled = True
    window._feature_link_navigator_enabled = True
    window._feature_map_enabled = True
    window.right_panel.set_feature_flags(enable_tasks=True, enable_calendar=True, enable_link_navigator=True, enable_map=True)
    window.right_panel.set_ai_enabled(True)
    window._sync_right_header_actions()
    window._open_file("/PageA/PageA.md")
    window._set_reading_view_enabled(reading)
    window._set_right_panel_collapsed(True)
    QTest.qWait(20)
    action = window._right_header_actions[key]
    button = window.toolbar.widgetForAction(action)
    assert action.isVisible()
    assert not action.icon().isNull()
    assert button.isVisible()
    QTest.mouseClick(button, Qt.LeftButton)
    assert window._is_right_panel_expanded()
    assert window.right_panel.tabs.currentWidget() is getattr(window.right_panel, panel_name)
    assert window._reading_view_enabled == reading


def test_disabled_features_hide_header_icons(main_window, monkeypatch):
    window = main_window
    monkeypatch.setattr(config, "load_enable_ai_chats", lambda: False)
    window._feature_tasks_enabled = False
    window._feature_calendar_enabled = False
    window._feature_link_navigator_enabled = False
    window._feature_map_enabled = False
    window._sync_right_header_actions()
    assert all(not action.isVisible() for action in window._right_header_actions.values())
    assert window._toolbar_prefs_action.isVisible()
