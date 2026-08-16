from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QToolButton

from sp.app.ui.markdown_editor import MarkdownEditor


def _hover_block(editor: MarkdownEditor, block_number: int) -> None:
    block = editor.document().findBlockByNumber(block_number)
    cursor = QTextCursor(block)
    rect = editor.cursorRect(cursor)
    QTest.mouseMove(editor.viewport(), QPoint(8, rect.center().y()))


def test_main_editor_reuses_one_hover_button_for_vi_tasks(qtbot, qapp) -> None:
    editor = MarkdownEditor()
    editor.set_markdown("- [ ] Call Sarah\n\nNot a task\n- [x] Done\n")
    editor.set_task_hover_edit_enabled(True)
    qtbot.addWidget(editor)
    editor.set_vi_mode_enabled(True)
    qapp.processEvents()

    _hover_block(editor, 0)
    button = editor.findChild(QToolButton, "taskHoverEditButton")
    assert button is not None and button.isVisible()
    assert len(editor.findChildren(QToolButton, "taskHoverEditButton")) == 1
    assert editor._task_hover_block_number == 0

    _hover_block(editor, 2)
    assert not button.isVisible()

    _hover_block(editor, 3)
    assert button.isVisible()
    assert editor._task_hover_block_number == 3


def test_hover_edit_button_emits_task_block_and_hides_in_insert_mode(qtbot, qapp) -> None:
    editor = MarkdownEditor()
    editor.set_markdown("- [ ] Call Sarah\n")
    editor.set_task_hover_edit_enabled(True)
    qtbot.addWidget(editor)
    editor.set_vi_mode_enabled(True)
    qapp.processEvents()
    requested: list[tuple[int, object]] = []
    editor.taskEditRequested.connect(lambda block, anchor: requested.append((block, anchor)))

    _hover_block(editor, 0)
    button = editor.findChild(QToolButton, "taskHoverEditButton")
    QTest.mouseClick(button, Qt.LeftButton)
    assert requested and requested[0][0] == 0

    QTest.mouseMove(editor.viewport(), QPoint(40, editor.viewport().height() - 4))
    _hover_block(editor, 0)
    assert button.isVisible()
    editor._enter_vi_insert_mode()
    assert not button.isVisible()


def test_vi_e_requests_editor_for_task_on_cursor_line(qtbot, qapp) -> None:
    editor = MarkdownEditor()
    editor.set_markdown("Not a task\n- [ ] Call Sarah\n")
    editor.set_task_hover_edit_enabled(True)
    qtbot.addWidget(editor)
    editor.set_vi_mode_enabled(True)
    editor.show()
    editor.setFocus()
    requested: list[tuple[int, object]] = []
    editor.taskEditRequested.connect(lambda block, anchor: requested.append((block, anchor)))

    task_block = editor.document().findBlockByNumber(1)
    editor.setTextCursor(QTextCursor(task_block))
    QTest.keyClick(editor, Qt.Key_E)

    assert requested and requested[0][0] == 1

    requested.clear()
    editor.setTextCursor(QTextCursor(editor.document().findBlockByNumber(0)))
    QTest.keyClick(editor, Qt.Key_E)
    assert requested == []


def test_main_window_routes_hovered_line_to_shared_task_editor(main_window, monkeypatch) -> None:
    panel = main_window.right_panel.task_panel
    assert panel is not None
    opened: list[tuple[dict, dict]] = []
    monkeypatch.setattr(
        panel,
        "edit_task",
        lambda task, **kwargs: opened.append((task, kwargs)) or True,
    )
    main_window.current_path = "/PageA/PageA.md"
    main_window.editor.set_context(str(main_window.vault_root), main_window.current_path)
    main_window.editor.set_markdown(
        "# Page A\n\n- [ ] Call Sarah !! @phone >2026-08-20 <2026-08-21\n"
    )
    main_window.editor.document().setModified(False)
    main_window._dirty_flag = False
    anchor = QPoint(200, 150)

    main_window._edit_task_from_main_editor(2, anchor)

    assert len(opened) == 1
    task, kwargs = opened[0]
    assert task["path"] == "/PageA/PageA.md"
    assert task["line"] == 3
    assert task["text"] == "Call Sarah"
    assert task["priority"] == 2
    assert task["tags"] == ["phone"]
    assert kwargs["parent"] is main_window
    assert kwargs["anchor_pos"] == anchor
