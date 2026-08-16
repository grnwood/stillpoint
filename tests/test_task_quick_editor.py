from __future__ import annotations

from datetime import date

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QLabel, QPlainTextEdit

from sp.app.ui import task_quick_editor
from sp.app.ui.task_quick_editor import TaskQuickEditor, parse_date_shortcut


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("today", "2026-08-15"),
        ("tomorrow", "2026-08-16"),
        ("+3d", "2026-08-18"),
        ("+1w", "2026-08-22"),
        ("mon", "2026-08-17"),
        ("clear", ""),
        ("2026-09-01", "2026-09-01"),
    ],
)
def test_parse_date_shortcuts(value: str, expected: str) -> None:
    assert parse_date_shortcut(value, today=date(2026, 8, 15)) == expected


def test_parse_date_shortcut_rejects_unknown_value() -> None:
    with pytest.raises(ValueError, match="Unrecognized date"):
        parse_date_shortcut("after the conference", today=date(2026, 8, 15))


def test_task_editor_round_trips_fields(qtbot) -> None:
    editor = TaskQuickEditor(
        {
            "text": "Call Sarah",
            "status": "todo",
            "priority": 2,
            "starts": "2026-08-20",
            "due": "2026-08-21",
            "tags": ["phone"],
            "path": "/People/Sarah/Sarah.md",
        }
    )
    qtbot.addWidget(editor)

    values = editor.values()

    assert values["text"] == "Call Sarah"
    assert values["priority"] == 2
    assert values["start"] == "2026-08-20"
    assert values["due"] == "2026-08-21"
    assert values["tags"] == ["phone"]


def test_task_editor_uses_colon_source_and_wrapping_task_field(qtbot) -> None:
    editor = TaskQuickEditor(
        {
            "text": "A very long task that needs to wrap instead of disappearing off the edge",
            "path": "/Projects/Launch/Launch.md",
        }
    )
    qtbot.addWidget(editor)

    source = editor.findChild(QLabel, "taskSourcePath")
    assert source is not None
    assert source.text() == ":Projects:Launch"
    assert isinstance(editor.text_edit, QPlainTextEdit)
    assert editor.text_edit.lineWrapMode() == QPlainTextEdit.WidgetWidth

    editor.text_edit.setPlainText("first visual line\nsecond visual line")
    assert editor.values()["text"] == "first visual line second visual line"


def test_arrow_and_vi_keys_cycle_editor_inputs(qtbot, qapp) -> None:
    editor = TaskQuickEditor({"text": "Call Sarah"}, vi_mode=True)
    qtbot.addWidget(editor)

    editor.text_edit.setFocus()
    QTest.keyClick(editor.text_edit, Qt.Key_Down)
    qapp.processEvents()
    assert editor.status.hasFocus()

    QTest.keyClick(editor.status, Qt.Key_J)
    qapp.processEvents()
    assert editor.priority.hasFocus()

    QTest.keyClick(editor.priority, Qt.Key_Up)
    qapp.processEvents()
    assert editor.status.hasFocus()


def test_editor_can_open_with_move_destination_focused(qtbot) -> None:
    editor = TaskQuickEditor(
        {"text": "Call Sarah"},
        focus_field="destination",
    )
    qtbot.addWidget(editor)

    assert editor.destination.hasFocus()


def test_tag_and_destination_completions(qtbot) -> None:
    searches: list[str] = []

    def search_pages(query: str) -> list[str]:
        searches.append(query)
        return [":Projects:Launch", ":Archive:Launch Notes"]

    editor = TaskQuickEditor(
        {"text": "Call Sarah"},
        known_tags=["work", "phone"],
        page_search=search_pages,
    )
    qtbot.addWidget(editor)

    editor.tags.setText("@work ph")
    editor.tags.setCursorPosition(len(editor.tags.text()))
    editor._replace_tag_completion("@phone")
    assert editor.tags.text() == "@work @phone"

    editor.destination.setText(":launch")
    editor._refresh_destination_completions()
    assert searches == ["launch"]
    assert editor._destination_model.stringList() == [
        ":Projects:Launch",
        ":Archive:Launch Notes",
    ]


def test_completers_are_attached_and_populated_during_typing(qtbot, qapp) -> None:
    editor = TaskQuickEditor(
        {"text": "Call Sarah"},
        known_tags=["phone", "work"],
        page_search=lambda _query: [":Projects:Launch"],
    )
    qtbot.addWidget(editor)

    assert editor._tag_completer.widget() is editor.tags
    assert editor._destination_completer.widget() is editor.destination

    editor.tags.setFocus()
    QTest.keyClicks(editor.tags, "ph")
    qapp.processEvents()
    assert editor._tag_completer.completionCount() == 1

    editor.destination.setFocus()
    QTest.keyClicks(editor.destination, "lau")
    QTest.qWait(75)
    qapp.processEvents()
    assert editor._destination_completer.completionCount() == 1
    assert editor.destination.text() == "lau"
    assert not editor._destination_completer.popup().currentIndex().isValid()

    QTest.keyClick(editor.destination, Qt.Key_Down)
    QTest.keyClick(editor.destination, Qt.Key_Return)
    qapp.processEvents()
    assert editor.destination.text() == ":Projects:Launch"


def test_focused_inputs_use_vault_accent(qtbot) -> None:
    editor = TaskQuickEditor(
        {"text": "Call Sarah"},
        vault_accent_color="#12ab34",
    )
    qtbot.addWidget(editor)

    style = editor.styleSheet().lower()
    assert "qlineedit:focus" in style
    assert "qplaintextedit:focus" in style
    assert "qcombobox:focus" in style
    assert "border: 2px solid #12ab34" in style


def test_vi_shortcuts_navigate_destination_dropdown(qtbot, qapp) -> None:
    editor = TaskQuickEditor(
        {"text": "Call Sarah"},
        vi_mode=True,
        page_search=lambda _query: [
            ":Projects:Launch",
            ":Archive:Launch Notes",
            ":People:Launch Team",
        ],
    )
    qtbot.addWidget(editor)
    editor.destination.setFocus()
    QTest.keyClicks(editor.destination, "lau")

    popup = editor._destination_completer.popup()
    modifiers = Qt.ControlModifier | Qt.ShiftModifier
    assert not popup.currentIndex().isValid()

    shortcuts = {shortcut.key().toString(): shortcut for shortcut in editor._shortcuts}
    assert "Ctrl+Shift+J" in shortcuts
    assert "Ctrl+Shift+K" in shortcuts

    shortcuts["Ctrl+Shift+J"].activated.emit()
    assert popup.isVisible()
    assert popup.currentIndex().row() == 0
    QTest.keyClick(editor.destination, Qt.Key_J, modifiers)
    assert popup.currentIndex().row() == 1
    shortcuts["Ctrl+Shift+K"].activated.emit()
    assert popup.currentIndex().row() == 0

    QTest.keyClick(editor.destination, Qt.Key_Return)
    assert editor.destination.text() == ":Projects:Launch"


def test_vi_shortcuts_cycle_fields_when_destination_dropdown_is_inactive(qtbot) -> None:
    editor = TaskQuickEditor({"text": "Call Sarah"}, vi_mode=True)
    qtbot.addWidget(editor)
    shortcuts = {shortcut.key().toString(): shortcut for shortcut in editor._shortcuts}

    editor.text_edit.setFocus()
    shortcuts["Ctrl+Shift+J"].activated.emit()
    assert editor.status.hasFocus()
    shortcuts["Ctrl+Shift+K"].activated.emit()
    assert editor.text_edit.hasFocus()


def test_escape_resets_destination_then_vi_shortcuts_cycle_fields(qtbot, qapp) -> None:
    editor = TaskQuickEditor(
        {"text": "Call Sarah"},
        vi_mode=True,
        page_search=lambda _query: [":Projects:Launch"],
    )
    qtbot.addWidget(editor)
    editor.destination.setFocus()
    QTest.keyClicks(editor.destination, "lau")
    QTest.qWait(75)
    qapp.processEvents()
    assert editor._destination_completer.popup().isVisible()

    QTest.keyClick(editor.destination, Qt.Key_Escape)

    assert editor.destination.text() == ""
    assert not editor._destination_completer.popup().isVisible()
    assert editor._destination_browsing is False

    shortcuts = {shortcut.key().toString(): shortcut for shortcut in editor._shortcuts}
    shortcuts["Ctrl+Shift+K"].activated.emit()
    assert editor.tags.hasFocus()


def test_task_text_uses_vi_navigation_and_requires_insert_mode(qtbot, qapp) -> None:
    editor = TaskQuickEditor({"text": "alpha beta"}, vi_mode=True)
    qtbot.addWidget(editor)
    editor.text_edit.setFocus()
    cursor = editor.text_edit.textCursor()
    cursor.setPosition(0)
    editor.text_edit.setTextCursor(cursor)

    assert editor.text_edit.isReadOnly()
    assert editor.task_mode_hint is not None
    assert editor.task_mode_hint.text().startswith("NAV")
    qapp.processEvents()
    nav_cursor = editor.text_edit.extraSelections()
    assert len(nav_cursor) == 1
    assert nav_cursor[0].cursor.selectedText() == "a"

    QTest.keyClick(editor.text_edit, Qt.Key_L)
    assert editor.text_edit.textCursor().position() == 1
    assert editor.text_edit.extraSelections()[0].cursor.selectedText() == "l"
    QTest.keyClick(editor.text_edit, Qt.Key_W)
    assert editor.text_edit.textCursor().position() > 1
    original = editor.text_edit.toPlainText()
    QTest.keyClicks(editor.text_edit, "z")
    assert editor.text_edit.toPlainText() == original

    QTest.keyClick(editor.text_edit, Qt.Key_I)
    assert not editor.text_edit.isReadOnly()
    assert editor.task_mode_hint.text().startswith("INSERT")
    assert editor.text_edit.extraSelections() == []
    QTest.keyClicks(editor.text_edit, "z")
    assert editor.text_edit.toPlainText() != original

    QTest.keyClick(editor.text_edit, Qt.Key_Escape)
    qapp.processEvents()
    assert editor.text_edit.isReadOnly()
    assert editor.task_mode_hint.text().startswith("NAV")
    assert len(editor.text_edit.extraSelections()) == 1
    assert editor.result() == 0


def test_tag_dropdown_vi_navigation_escape_reset_and_focus_handoff(qtbot, qapp) -> None:
    editor = TaskQuickEditor(
        {"text": "Call Sarah", "tags": ["work"]},
        vi_mode=True,
        known_tags=["phone", "photo"],
    )
    qtbot.addWidget(editor)
    editor.tags.setFocus()
    editor.tags.setCursorPosition(len(editor.tags.text()))
    QTest.keyClicks(editor.tags, " ph")
    qapp.processEvents()
    popup = editor._tag_completer.popup()
    assert popup.isVisible()
    assert not popup.currentIndex().isValid()

    shortcuts = {shortcut.key().toString(): shortcut for shortcut in editor._shortcuts}
    shortcuts["Ctrl+Shift+J"].activated.emit()
    assert popup.currentIndex().row() == 0

    QTest.keyClick(editor.tags, Qt.Key_Escape)
    assert editor.tags.text() == "@work"
    assert not popup.isVisible()

    shortcuts["Ctrl+Shift+K"].activated.emit()
    assert editor.due.hasFocus()


def test_combo_dropdown_vi_navigation_escape_reset_and_focus_handoff(qtbot) -> None:
    editor = TaskQuickEditor({"text": "Call Sarah"}, vi_mode=True)
    qtbot.addWidget(editor)
    editor.status.setFocus()
    editor.status.showPopup()
    shortcuts = {shortcut.key().toString(): shortcut for shortcut in editor._shortcuts}

    shortcuts["Ctrl+Shift+J"].activated.emit()
    assert editor.status.currentIndex() == 1

    QTest.keyClick(editor.status.view(), Qt.Key_Escape)
    assert editor.status.currentIndex() == 0
    assert editor.status.hasFocus()

    shortcuts["Ctrl+Shift+J"].activated.emit()
    assert editor.priority.hasFocus()


@pytest.mark.parametrize("field_name", ["start", "due"])
def test_escape_restores_changed_date_field_before_closing(qtbot, qapp, field_name: str) -> None:
    editor = TaskQuickEditor(
        {
            "text": "Call Sarah",
            "start": "2026-08-20",
            "due": "2026-08-21",
        }
    )
    qtbot.addWidget(editor)
    field = getattr(editor, field_name)
    original = field.text()
    field.setFocus()
    field.setText("2026-09-30")

    QTest.keyClick(field, Qt.Key_Escape)
    qapp.processEvents()
    assert field.text() == original
    assert editor.isVisible()

    QTest.keyClick(field, Qt.Key_Escape)
    qapp.processEvents()
    assert not editor.isVisible()


def test_escape_restores_changed_task_text_before_closing(qtbot, qapp) -> None:
    editor = TaskQuickEditor({"text": "Call Sarah"})
    qtbot.addWidget(editor)
    editor.text_edit.setFocus()
    editor.text_edit.setPlainText("Changed task")

    QTest.keyClick(editor.text_edit, Qt.Key_Escape)
    qapp.processEvents()
    assert editor.text_edit.toPlainText() == "Call Sarah"
    assert editor.isVisible()

    QTest.keyClick(editor.text_edit, Qt.Key_Escape)
    qapp.processEvents()
    assert not editor.isVisible()


def test_required_destination_is_validated(qtbot, qapp) -> None:
    editor = TaskQuickEditor(
        {"text": "Call Sarah"},
        destination_required=True,
        dialog_title="Make Quick Capture a Task",
    )
    qtbot.addWidget(editor)
    editor.show()

    editor.accept()
    qapp.processEvents()
    assert editor.isVisible()
    assert editor.destination.hasFocus()
    assert editor.destination_error.isVisible()

    with pytest.raises(ValueError, match="destination"):
        editor.values()

    editor.destination.setText(":Projects:Launch")
    assert editor.values()["destination"] == ":Projects:Launch"


def test_escape_restores_changed_combo_before_closing(qtbot, qapp) -> None:
    editor = TaskQuickEditor({"text": "Call Sarah", "priority": 1})
    qtbot.addWidget(editor)
    editor.priority.setFocus()
    editor.priority.setCurrentIndex(3)

    QTest.keyClick(editor.priority, Qt.Key_Escape)
    qapp.processEvents()
    assert editor.priority.currentIndex() == 1
    assert editor.isVisible()

    QTest.keyClick(editor.priority, Qt.Key_Escape)
    qapp.processEvents()
    assert not editor.isVisible()


def test_date_picker_sets_selected_date(qtbot, monkeypatch) -> None:
    class FakeDateDialog:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def exec(self) -> int:
            return QDialog.Accepted

        def selected_date_text(self) -> str:
            return "2026-09-02"

    monkeypatch.setattr(task_quick_editor, "DateInsertDialog", FakeDateDialog)
    editor = TaskQuickEditor({"text": "Call Sarah"})
    qtbot.addWidget(editor)

    editor._pick_date(editor.due, editor.due_picker.button)

    assert editor.due.text() == "2026-09-02"
