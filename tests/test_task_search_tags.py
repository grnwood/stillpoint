from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QListWidgetItem

from sp.app.ui.task_panel import TaskPanel
from sp.app.ui.task_panel import _active_tag_token, _should_suspend_nav_for_tag


def test_is_typing_tag_detects_active_tag_token() -> None:
    assert _active_tag_token("@todo", 5) == "@todo"
    assert _active_tag_token("fix @todo", 9) == "@todo"
    assert _active_tag_token("fix @todo later", 9) == "@todo"
    assert _active_tag_token("fix @todo later", 14) is None
    assert _active_tag_token("no tag", 2) is None
    assert _active_tag_token("", 0) is None


def test_should_suspend_nav_only_for_unknown_tag() -> None:
    available = {"todo", "wt"}
    assert _should_suspend_nav_for_tag("fix @to", 7, available) is True  # partial not known
    assert _should_suspend_nav_for_tag("fix @todo", 9, available) is False  # known tag; allow nav
    assert _should_suspend_nav_for_tag("fix @todo later", 14, available) is False  # cursor past tag
    assert _should_suspend_nav_for_tag("plain", 3, available) is False


def test_tag_click_activates_on_first_click(qtbot) -> None:
    panel = TaskPanel()
    qtbot.addWidget(panel)
    panel._refresh_tasks = lambda: None

    item = QListWidgetItem("@todo (1)")
    item.setData(Qt.UserRole, "@todo")
    panel.tag_list.addItem(item)

    panel.show()
    QApplication.processEvents()
    rect = panel.tag_list.visualItemRect(item)
    assert rect.isValid()

    QTest.mouseClick(panel.tag_list.viewport(), Qt.LeftButton, Qt.NoModifier, rect.center())
    QApplication.processEvents()

    assert "@todo" in panel.active_tags


def test_tag_empty_area_click_clears_active_tags(qtbot) -> None:
    panel = TaskPanel()
    qtbot.addWidget(panel)
    panel._refresh_tasks = lambda: None

    item = QListWidgetItem("@todo (1)")
    item.setData(Qt.UserRole, "@todo")
    panel.tag_list.addItem(item)
    panel.active_tags.add("@todo")

    panel.tag_list.setMinimumHeight(120)
    panel.show()
    QApplication.processEvents()
    empty_point = QPoint(6, panel.tag_list.viewport().height() - 6)

    QTest.mouseClick(panel.tag_list.viewport(), Qt.LeftButton, Qt.NoModifier, empty_point)
    QApplication.processEvents()

    assert panel.active_tags == set()


def test_tag_click_on_row_whitespace_activates_first_click(qtbot) -> None:
    panel = TaskPanel()
    qtbot.addWidget(panel)
    panel._refresh_tasks = lambda: None

    item = QListWidgetItem("@todo (1)")
    item.setData(Qt.UserRole, "@todo")
    panel.tag_list.addItem(item)
    panel.tag_list.setMinimumWidth(320)

    panel.show()
    QApplication.processEvents()

    rect = panel.tag_list.visualItemRect(item)
    assert rect.isValid()
    click_pos = QPoint(panel.tag_list.viewport().width() - 4, rect.center().y())

    QTest.mouseClick(panel.tag_list.viewport(), Qt.LeftButton, Qt.NoModifier, click_pos)
    QApplication.processEvents()

    assert "@todo" in panel.active_tags


def test_remote_refresh_does_not_clear_active_tags_when_tag_items_empty(qtbot) -> None:
    panel = TaskPanel()
    qtbot.addWidget(panel)

    panel.set_remote_mode(True)
    panel.active_tags = {"@hib"}
    panel._tag_source_tasks = []

    panel._refresh_tags()

    assert panel.active_tags == {"@hib"}


def test_api_backed_refresh_does_not_clear_active_tags_when_tag_items_empty(qtbot) -> None:
    panel = TaskPanel()
    qtbot.addWidget(panel)

    panel._http_client = object()
    panel.active_tags = {"todo"}
    panel._tag_source_tasks = []

    panel._refresh_tags()

    assert panel.active_tags == {"todo"}
