from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QMenu
from PySide6.QtTest import QTest

import sp.app.ui.link_navigator_panel as link_nav
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
