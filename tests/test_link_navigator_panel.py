from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QMenu
from PySide6.QtTest import QTest

import sp.app.ui.link_navigator_panel as link_nav
import sp.app.ui.ai_chat_panel as ai_chat_nav
import sp.app.ui.calendar_panel as calendar_nav
from sp.app.ui.ai_chat_panel import AIChatPanel
from sp.app.ui.calendar_panel import CalendarPanel
from sp.app.ui.link_navigator_panel import GalaxyGraphView, LinkNavigatorPanel, _NodeData
from sp.app.ui.tabbed_right_panel import TabbedRightPanel


def test_link_graph_keyboard_navigation_and_activation(qtbot, qapp) -> None:
    view = GalaxyGraphView()
    qtbot.addWidget(view)
    activated: list[tuple[str, bool]] = []
    view.nodeActivated.connect(lambda path, keep_focus: activated.append((path, keep_focus)))

    view.set_graph(
        "/Center/Center.md",
        [
            _NodeData("/Center/Center.md", "Center", 2),
            _NodeData("/Left/Left.md", "Left", 1),
            _NodeData("/Right/Right.md", "Right", 1),
            _NodeData("/Down/Down.md", "Down", 1),
        ],
        [
            ("/Center/Center.md", "/Right/Right.md"),
            ("/Center/Center.md", "/Down/Down.md"),
        ],
    )
    view._nodes["/Center/Center.md"].setPos(QPointF(0, 0))
    view._nodes["/Left/Left.md"].setPos(QPointF(-100, 0))
    view._nodes["/Right/Right.md"].setPos(QPointF(100, 0))
    view._nodes["/Down/Down.md"].setPos(QPointF(0, 100))
    view._base_positions = {path: QPointF(node.pos()) for path, node in view._nodes.items()}
    view._set_selected_path("/Center/Center.md")
    view.setFocus(Qt.OtherFocusReason)
    qapp.processEvents()

    QTest.keyClick(view, Qt.Key_L)
    assert view._selected_path == "/Right/Right.md"
    right_edge = next(edge for edge in view._edges if edge.target.data.path == "/Right/Right.md")
    assert right_edge.pen().color() == right_edge._active_pen.color()

    QTest.keyClick(view, Qt.Key_H)
    assert view._selected_path == "/Center/Center.md"

    QTest.keyClick(view, Qt.Key_J)
    assert view._selected_path == "/Down/Down.md"

    QTest.keyClick(view, Qt.Key_Return)
    assert activated == [("/Down/Down.md", False)]

    QTest.keyClick(view, Qt.Key_Return, Qt.ShiftModifier)
    assert activated[-1] == ("/Down/Down.md", True)


def test_link_graph_shift_click_preserves_focus_flag(qtbot, qapp) -> None:
    view = GalaxyGraphView()
    qtbot.addWidget(view)
    activated: list[tuple[str, bool]] = []
    view.nodeActivated.connect(lambda path, keep_focus: activated.append((path, keep_focus)))

    view.resize(420, 320)
    view.set_graph(
        "/Center/Center.md",
        [
            _NodeData("/Center/Center.md", "Center", 1),
            _NodeData("/Target/Target.md", "Target", 1),
        ],
        [("/Center/Center.md", "/Target/Target.md")],
    )
    view.show()
    qapp.processEvents()

    target = view._nodes["/Target/Target.md"]
    click_pos = view.mapFromScene(target.scenePos() + QPointF(target.radius * 0.7, 0))
    assert view.itemAt(click_pos) is target

    QTest.mouseClick(view.viewport(), Qt.LeftButton, pos=click_pos)
    QTest.mouseClick(view.viewport(), Qt.LeftButton, Qt.ShiftModifier, pos=click_pos)

    assert activated == [
        ("/Target/Target.md", False),
        ("/Target/Target.md", True),
    ]


def test_link_graph_hover_leave_is_debounced(qtbot, qapp) -> None:
    view = GalaxyGraphView()
    qtbot.addWidget(view)

    view.set_graph(
        "/Center/Center.md",
        [
            _NodeData("/Center/Center.md", "Center", 1),
            _NodeData("/Target/Target.md", "Target", 1),
        ],
        [("/Center/Center.md", "/Target/Target.md")],
    )

    view._on_hover_enter("/Target/Target.md")
    view._on_hover_leave("/Target/Target.md")
    assert view._hover_path == "/Target/Target.md"

    QTest.qWait(30)
    view._on_hover_enter("/Target/Target.md")
    QTest.qWait(120)

    assert view._hover_path == "/Target/Target.md"

    view._on_hover_leave("/Target/Target.md")
    QTest.qWait(120)

    assert view._hover_path is None


def test_focus_link_tab_refreshes_and_focuses_graph(qtbot, qapp, monkeypatch) -> None:
    panel = TabbedRightPanel(enable_tasks=False, enable_calendar=False, enable_map=False)
    qtbot.addWidget(panel)
    assert panel.link_panel is not None

    refreshed: list[str | None] = []
    monkeypatch.setattr(panel.link_panel, "set_page", lambda path: setattr(panel.link_panel, "current_page", path))
    monkeypatch.setattr(panel.link_panel, "refresh", lambda path=None, **_kwargs: refreshed.append(path))

    panel.focus_link_tab("/PageA/PageA.md")
    qapp.processEvents()

    assert refreshed[-1] == "/PageA/PageA.md"
    assert qapp.focusWidget() is panel.link_panel.graph_view


def test_link_panel_activation_focus_modes(main_window, monkeypatch) -> None:
    opened: list[str] = []
    focused: list[str] = []
    refreshed: list[str] = []
    focused_link_tab: list[str] = []

    monkeypatch.setattr(main_window, "_open_file", lambda path, *args, **kwargs: opened.append(path))
    monkeypatch.setattr(main_window, "_apply_navigation_focus", lambda target: focused.append(target))
    monkeypatch.setattr(main_window.right_panel, "refresh_links", lambda path=None: refreshed.append(path))
    monkeypatch.setattr(main_window.right_panel, "focus_link_tab", lambda path=None: focused_link_tab.append(path))

    main_window._open_link_from_panel("/PageA/PageA.md", keep_focus=False)
    main_window._open_link_from_panel("/PageB/PageB.md", keep_focus=True)

    assert opened == ["/PageA/PageA.md", "/PageB/PageB.md"]
    assert refreshed == ["/PageA/PageA.md"]
    assert focused_link_tab == ["/PageB/PageB.md"]
    assert focused == ["editor", "navigator"]


def test_link_navigator_context_menu_uses_readable_colors(qtbot) -> None:
    panel = LinkNavigatorPanel()
    qtbot.addWidget(panel)

    menu = QMenu(panel)
    panel._apply_link_menu_theme(menu)

    style = menu.styleSheet().lower()
    assert "qmenu::item:selected" in style
    assert "color:" in style
    assert "background:" in style


def test_link_graph_blank_left_click_requests_menu_and_right_click_pans(qtbot, qapp) -> None:
    view = GalaxyGraphView()
    qtbot.addWidget(view)
    requested: list[QPoint] = []
    view.blankCanvasClicked.connect(requested.append)

    view.resize(320, 240)
    view.show()
    qapp.processEvents()

    QTest.mouseClick(view.viewport(), Qt.LeftButton, pos=QPoint(4, 4))
    assert requested == [QPoint(4, 4)]

    QTest.mousePress(view.viewport(), Qt.RightButton, pos=QPoint(10, 10))
    assert view._is_panning is True
    assert requested == [QPoint(4, 4)]

    QTest.mouseRelease(view.viewport(), Qt.RightButton, pos=QPoint(10, 10))
    assert view._is_panning is False


def test_link_graph_uses_vault_accent_and_readable_active_label(qtbot) -> None:
    view = GalaxyGraphView()
    qtbot.addWidget(view)
    view.set_vault_accent_color("#f2d45c")
    view.set_graph(
        "/Center/Center.md",
        [_NodeData("/Center/Center.md", "Center", 1)],
        [],
    )

    node = view._nodes["/Center/Center.md"]
    assert node.brush().color().name().lower() == QColor("#f2d45c").name().lower()
    assert node.label_item.brush().color().name().lower() == "#111111"
    assert node.label_item.opacity() == 1.0


def test_link_graph_honors_light_theme(qtbot, monkeypatch) -> None:
    def fake_theme_value(path: str, default=None):
        values = {
            "markdown_editor.base.bg": "#ffffff",
            "markdown_editor.base.text": "#111111",
            "markdown_editor.base.selection_bg": "#2f6fed",
            "markdown_editor.base.selection_text": "#ffffff",
        }
        return values.get(path, default)

    monkeypatch.setattr(link_nav, "theme_value", fake_theme_value)
    view = GalaxyGraphView()
    qtbot.addWidget(view)
    view.set_graph(
        "/Center/Center.md",
        [
            _NodeData("/Center/Center.md", "Center", 1),
            _NodeData("/Other/Other.md", "Other", 1),
        ],
        [],
    )

    assert view._theme_colors["canvas"].name().lower() == "#ffffff"
    assert "background: #ffffff" in view.styleSheet().lower()
    other = view._nodes["/Other/Other.md"]
    assert other.label_item.brush().color().name().lower() == "#111111"


def test_link_graph_keeps_dark_theme_labels_readable(qtbot, monkeypatch) -> None:
    def fake_theme_value(path: str, default=None):
        values = {
            "markdown_editor.base.bg": "#101216",
            "markdown_editor.base.text": "#111111",
            "markdown_editor.base.selection_bg": "#2f6fed",
            "markdown_editor.base.selection_text": "#ffffff",
        }
        return values.get(path, default)

    monkeypatch.setattr(link_nav, "theme_value", fake_theme_value)
    view = GalaxyGraphView()
    qtbot.addWidget(view)
    view.set_graph(
        "/Center/Center.md",
        [
            _NodeData("/Center/Center.md", "Center", 1),
            _NodeData("/Other/Other.md", "Other", 1),
        ],
        [],
    )

    assert view._theme_colors["canvas"].name().lower() == "#101216"
    assert view._theme_colors["label"].name().lower() == "#ffffff"
    other = view._nodes["/Other/Other.md"]
    assert other.label_item.brush().color().name().lower() == "#ffffff"


def test_right_panel_forwards_vault_accent_to_link_navigator(qtbot) -> None:
    panel = TabbedRightPanel(enable_tasks=False, enable_calendar=False, enable_map=False)
    qtbot.addWidget(panel)
    assert panel.link_panel is not None

    panel.set_vault_accent_color("#37b24d")

    assert panel.link_panel._vault_accent_color == "#37b24d"
    assert panel.link_panel.graph_view._vault_accent_color == "#37b24d"


def test_right_panel_theme_refresh_updates_link_navigator_override(qtbot, monkeypatch) -> None:
    theme_colors = {
        "markdown_editor.base.bg": "#ffffff",
        "markdown_editor.base.text": "#111111",
        "markdown_editor.base.selection_bg": "#2f6fed",
        "markdown_editor.base.selection_text": "#ffffff",
    }

    def fake_theme_value(path: str, default=None):
        return theme_colors.get(path, default)

    monkeypatch.setattr(link_nav, "theme_value", fake_theme_value)
    panel = TabbedRightPanel(enable_tasks=False, enable_calendar=False, enable_map=False)
    qtbot.addWidget(panel)
    assert panel.link_panel is not None
    assert panel.link_panel.graph_view._theme_colors["canvas"].name().lower() == "#ffffff"

    theme_colors.update(
        {
            "markdown_editor.base.bg": "#101216",
            "markdown_editor.base.text": "#eeeeee",
            "markdown_editor.base.selection_bg": "#4f7cff",
            "markdown_editor.base.selection_text": "#ffffff",
        }
    )

    panel.apply_theme()

    assert panel.link_panel.graph_view._theme_colors["canvas"].name().lower() == "#101216"
    assert "background: #101216" in panel.link_panel.styleSheet().lower()


def test_ai_chat_and_calendar_apply_theme_refreshes_vault_overrides(qtbot, monkeypatch) -> None:
    ai_theme_values = {
        "ai_chat_panel.panel.bg": "#101216",
        "ai_chat_panel.panel.text": "#eeeeee",
        "ai_chat_panel.panel.surface_bg": "#181b20",
        "ai_chat_panel.panel.alt_bg": "#1f232b",
        "ai_chat_panel.panel.border": "#556070",
        "ai_chat_panel.panel.input_bg": "#1b2028",
        "ai_chat_panel.panel.input_text": "#eeeeee",
        "ai_chat_panel.panel.selected_bg": "#4f7cff",
        "ai_chat_panel.panel.selected_text": "#ffffff",
        "ai_chat_panel.panel.muted_text": "#aaaaaa",
    }
    calendar_theme_values = {
        "calendar_panel.calendar.selected_bg": "#4f7cff",
        "calendar_panel.calendar.selected_text": "#ffffff",
        "calendar_panel.calendar.grid_light": "#c8d0de",
        "calendar_panel.calendar.grid_dark": "#434b59",
        "calendar_panel.calendar.header_dark": "#222833",
        "calendar_panel.calendar.header_light": "#f2f5fb",
        "calendar_panel.calendar.nav_text_dark": "#e6e6e6",
        "calendar_panel.calendar.nav_text_light": "#1f1f1f",
        "calendar_panel.calendar.dim_text": "#a0a0a0",
        "calendar_panel.today.border": "#2b6cb0",
        "calendar_panel.today.bg": "#2b6cb0",
    }

    def fake_ai_theme_value(path: str, default=None):
        return ai_theme_values.get(path, default)

    def fake_calendar_theme_value(path: str, default=None):
        return calendar_theme_values.get(path, default)

    monkeypatch.setattr(ai_chat_nav, "theme_value", fake_ai_theme_value)
    monkeypatch.setattr(calendar_nav, "theme_value", fake_calendar_theme_value)

    ai_panel = AIChatPanel(font_size=13, api_client=None)
    qtbot.addWidget(ai_panel)
    calendar_panel = CalendarPanel()
    qtbot.addWidget(calendar_panel)

    ai_theme_values["ai_chat_panel.panel.bg"] = "#1a1f27"
    ai_theme_values["ai_chat_panel.panel.border"] = "#667085"
    calendar_theme_values["calendar_panel.calendar.selected_bg"] = "#2563eb"
    calendar_theme_values["calendar_panel.calendar.selected_text"] = "#ffffff"

    ai_panel.apply_theme()
    calendar_panel.apply_theme()

    assert "background: #1a1f27" in ai_panel.styleSheet().lower()
    assert calendar_panel._calendar_selected_bg.name().lower() == "#2563eb"


def test_right_panel_apply_theme_dispatches_to_calendar_and_ai_chat(qtbot, monkeypatch) -> None:
    panel = TabbedRightPanel(enable_tasks=False, enable_calendar=True, enable_map=False, enable_ai_chats=True)
    qtbot.addWidget(panel)
    assert panel.calendar_panel is not None
    assert panel.ai_chat_panel is not None

    calls: list[str] = []
    monkeypatch.setattr(panel.ai_chat_panel, "apply_theme", lambda: calls.append("ai"))
    monkeypatch.setattr(panel.calendar_panel, "apply_theme", lambda: calls.append("calendar"))

    panel.apply_theme()

    assert calls == ["ai", "calendar"]
