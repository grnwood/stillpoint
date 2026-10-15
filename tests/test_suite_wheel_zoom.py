from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QLabel, QWidget

from sp.app.ui import ai_chat_panel, calendar_panel, task_panel
from sp.app.ui.ai_chat_panel import AIChatPanel
from sp.app.ui.attachments_panel import AttachmentsPanel
from sp.app.ui.calendar_panel import CalendarPanel
from sp.app.ui.canvas_navigation import install_primary_wheel_zoom
from sp.app.ui.task_panel import TaskPanel


def _primary_wheel(*, modifier=Qt.ControlModifier, delta: int = 120) -> QWheelEvent:
    return QWheelEvent(
        QPointF(10, 10),
        QPointF(10, 10),
        QPoint(),
        QPoint(0, delta),
        Qt.NoButton,
        modifier,
        Qt.NoScrollPhase,
        False,
    )


def test_primary_wheel_zoom_filter_handles_children_and_cmd(qapp, qtbot) -> None:
    root = QWidget()
    child = QLabel(root)
    qtbot.addWidget(root)
    directions: list[int] = []
    install_primary_wheel_zoom(root, directions.append)

    qapp.sendEvent(child, _primary_wheel(modifier=Qt.MetaModifier))

    assert directions == [1]


def test_requested_utility_panels_support_primary_wheel_zoom(qapp, qtbot, monkeypatch) -> None:
    monkeypatch.setattr(task_panel.config, "load_enable_ai_chats", lambda: False)
    monkeypatch.setattr(calendar_panel.config, "load_enable_ai_chats", lambda: False)
    monkeypatch.setattr(task_panel.config, "save_panel_font_size", lambda *_args: None)
    monkeypatch.setattr(calendar_panel.config, "save_panel_font_size", lambda *_args: None)
    monkeypatch.setattr(ai_chat_panel.config, "save_panel_font_size", lambda *_args: None)

    task = TaskPanel()
    calendar = CalendarPanel()
    chat = AIChatPanel(api_client=None)
    attachments = AttachmentsPanel()
    for panel in (task, calendar, chat, attachments):
        qtbot.addWidget(panel)

    task_size = task._font_size
    calendar_size = calendar._font_size
    chat_size = chat.font_size

    qapp.sendEvent(task.task_tree.viewport(), _primary_wheel())
    qapp.sendEvent(calendar.calendar, _primary_wheel())
    qapp.sendEvent(chat.input_edit.viewport(), _primary_wheel())
    qapp.sendEvent(attachments.attachments_list.viewport(), _primary_wheel())

    assert task._font_size == task_size + 1
    assert calendar._font_size == calendar_size + 1
    assert chat.font_size == chat_size + 1
    assert attachments.zoom_level == 1


def test_main_editor_and_vault_tree_support_primary_wheel_zoom(qapp, main_window) -> None:
    editor_size = main_window.font_size
    tree_size = main_window.vault_tree_font_size

    qapp.sendEvent(main_window.editor.viewport(), _primary_wheel())
    qapp.sendEvent(main_window.tree_view.viewport(), _primary_wheel())

    assert main_window.font_size == editor_size + 1
    assert main_window.vault_tree_font_size == tree_size + 1
