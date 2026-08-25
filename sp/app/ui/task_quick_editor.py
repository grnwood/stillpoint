from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Callable, Optional

from PySide6.QtCore import QEvent, QModelIndex, QPoint, QStringListModel, Qt, QTimer
from PySide6.QtGui import QColor, QKeySequence, QShortcut, QTextCursor, QTextOption
from PySide6.QtWidgets import (
    QComboBox,
    QCompleter,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QSizePolicy,
    QToolButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .date_insert_dialog import DateInsertDialog
from .path_utils import path_to_colon
from .screen_positioning import clamp_popup_top_left, popup_available_geometry
from .theme import theme_value


def parse_date_shortcut(value: str, *, today: Optional[date] = None) -> str:
    text = str(value or "").strip().casefold()
    if not text or text in {"clear", "none", "-"}:
        return ""
    anchor = today or date.today()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return date.fromisoformat(text).isoformat()
    if text == "today":
        return anchor.isoformat()
    if text == "tomorrow":
        return (anchor + timedelta(days=1)).isoformat()
    if text == "yesterday":
        return (anchor - timedelta(days=1)).isoformat()
    relative = re.fullmatch(r"([+-]\d+)\s*([dw])", text)
    if relative:
        amount = int(relative.group(1))
        if relative.group(2) == "w":
            amount *= 7
        return (anchor + timedelta(days=amount)).isoformat()
    weekdays = {
        "mon": 0,
        "monday": 0,
        "tue": 1,
        "tuesday": 1,
        "wed": 2,
        "wednesday": 2,
        "thu": 3,
        "thursday": 3,
        "fri": 4,
        "friday": 4,
        "sat": 5,
        "saturday": 5,
        "sun": 6,
        "sunday": 6,
    }
    weekday_text = text.removeprefix("next ").strip()
    if weekday_text in weekdays:
        delta = (weekdays[weekday_text] - anchor.weekday()) % 7
        if delta == 0 or text.startswith("next "):
            delta += 7
        return (anchor + timedelta(days=delta)).isoformat()
    if text == "next week":
        return (anchor + timedelta(days=7)).isoformat()
    raise ValueError(f"Unrecognized date: {value}")


class TaskQuickEditor(QDialog):
    def __init__(
        self,
        item: dict,
        parent=None,
        *,
        triage: bool = False,
        initial_outcome: Optional[str] = None,
        focus_field: Optional[str] = None,
        known_tags: Optional[list[str]] = None,
        page_search: Optional[Callable[[str], list[str]]] = None,
        anchor_pos: Optional[QPoint] = None,
        vi_mode: bool = False,
        vault_accent_color: Optional[str] = None,
        destination_required: bool = False,
        dialog_title: Optional[str] = None,
    ) -> None:
        super().__init__(parent)
        self.item = dict(item)
        self.triage = bool(triage)
        self.save_and_next = False
        self._anchor_pos = anchor_pos
        self._positioned = False
        self._vi_mode = bool(vi_mode)
        self._page_search = page_search
        self._destination_required = bool(destination_required)
        self._focus_inputs: list[QWidget] = []
        self._combo_views: list[QWidget] = []
        self._task_vi_edit_mode = not self._vi_mode or self.triage
        self.task_mode_hint: Optional[QLabel] = None
        accent = QColor(str(vault_accent_color or "").strip())
        self._accent_color = (
            accent.name()
            if accent.isValid()
            else str(theme_value("main_window.focus_border.default", "#4A90E2"))
        )
        self.setObjectName("taskQuickEditor")
        self._apply_focus_style()
        self.setWindowTitle(dialog_title or ("Process Capture" if triage else "Edit Task"))
        self.setModal(True)
        self.resize(560, 420 if triage else 390)

        layout = QVBoxLayout(self)
        source_path = path_to_colon(str(item.get("path") or ""))
        source = QLabel(f":{source_path.lstrip(':')}" if source_path else "")
        source.setObjectName("taskSourcePath")
        source.setTextInteractionFlags(Qt.TextSelectableByMouse)
        source.setStyleSheet("color: #777;")
        layout.addWidget(source)
        form = QFormLayout()
        if triage:
            self.text_edit = QPlainTextEdit()
            self.text_edit.setPlainText(str(item.get("text") or ""))
            self.text_edit.setMinimumHeight(105)
            form.addRow("Capture:", self.text_edit)
            self.outcome = QComboBox()
            self.outcome.addItem("Make Task", "task")
            self.outcome.addItem("File as Note", "file")
            self.outcome.addItem("Keep as Note", "note")
            self.outcome.addItem("Delete", "delete")
            recommendation = item.get("recommendation") or {}
            if not initial_outcome:
                initial_outcome = str(recommendation.get("action") or "") or None
            if initial_outcome:
                index = self.outcome.findData(initial_outcome)
                if index >= 0:
                    self.outcome.setCurrentIndex(index)
            form.addRow("Outcome:", self.outcome)
            if recommendation:
                reason = QLabel(
                    f"Suggested: {recommendation.get('label')} — {recommendation.get('reason')}"
                )
                reason.setWordWrap(True)
                reason.setStyleSheet("color: #777;")
                form.addRow("", reason)
        else:
            self.text_edit = QPlainTextEdit()
            self.text_edit.setPlainText(str(item.get("text") or ""))
            self.text_edit.setLineWrapMode(QPlainTextEdit.WidgetWidth)
            self.text_edit.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
            self.text_edit.setTabChangesFocus(True)
            self.text_edit.setMinimumHeight(82)
            self.text_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.MinimumExpanding)
            self.text_edit.cursorPositionChanged.connect(self._update_task_nav_cursor)
            form.addRow("Task:", self.text_edit)
            if self._vi_mode:
                self.task_mode_hint = QLabel()
                self.task_mode_hint.setStyleSheet("color: #777; font-size: 11px;")
                form.addRow("", self.task_mode_hint)
            self.outcome = None
        self._text_original_value = self.text_edit.toPlainText()

        self.status = QComboBox()
        self.status.addItem("Open", "todo")
        self.status.addItem("Complete", "done")
        self.status.setCurrentIndex(1 if item.get("status") == "done" else 0)
        form.addRow("Status:", self.status)

        self.priority = QComboBox()
        for level, label in enumerate(("None", "Low (!) ", "Medium (!!)", "High (!!!)")):
            self.priority.addItem(label.strip(), level)
        self.priority.setCurrentIndex(max(0, min(int(item.get("priority") or 0), 3)))
        form.addRow("Priority:", self.priority)

        self.start = QLineEdit(str(item.get("starts") or item.get("start") or ""))
        self.start.setPlaceholderText("today, fri, +3d, or clear")
        self.start_picker = self._date_field(self.start, "Choose start date")
        form.addRow("Start:", self.start_picker)
        self.due = QLineEdit(str(item.get("due") or ""))
        self.due.setPlaceholderText("tomorrow, next week, +1w, or clear")
        self.due_picker = self._date_field(self.due, "Choose due date")
        form.addRow("Due:", self.due_picker)
        self._line_original_values = {
            self.start: self.start.text(),
            self.due: self.due.text(),
        }
        self.tags = QLineEdit(
            " ".join(f"@{str(tag).lstrip('@')}" for tag in item.get("tags") or [])
        )
        self._tags_original_value = self.tags.text()
        self._tags_dirty = False
        self._tags_browsing = False
        self.tags.setPlaceholderText("@work @phone")
        tag_values = sorted(
            {f"@{str(tag).lstrip('@')}" for tag in (known_tags or []) if str(tag).strip()},
            key=str.casefold,
        )
        self._tag_model = QStringListModel(tag_values, self)
        self._tag_completer = QCompleter(self._tag_model, self)
        self._configure_completer(self._tag_completer)
        self._tag_completer.setWidget(self.tags)
        self._tag_completer.activated[str].connect(self._replace_tag_completion)
        self.tags.textEdited.connect(self._complete_tag)
        form.addRow("Tags:", self.tags)
        self.destination = QLineEdit()
        self.destination.setPlaceholderText(
            ":Projects:Launch"
            if self._destination_required
            else ":Projects:Launch (leave blank to keep in place)"
        )
        if triage:
            recommendation = item.get("recommendation") or {}
            self.destination.setText(str(recommendation.get("destination") or ""))
        self._destination_original_value = self.destination.text()
        self._destination_dirty = False
        self._destination_browsing = False
        self._destination_model = QStringListModel([], self)
        self._destination_completer = QCompleter(self._destination_model, self)
        self._configure_completer(self._destination_completer)
        # Keep this as a suggestions-only popup. Attaching via QLineEdit.setCompleter()
        # lets Qt treat the first match as an implicit completion in some styles.
        self._destination_completer.setWidget(self.destination)
        self._destination_completer.activated[str].connect(self._choose_destination_completion)
        self.destination.textEdited.connect(self._on_destination_edited)
        self._destination_search_timer = QTimer(self)
        self._destination_search_timer.setSingleShot(True)
        self._destination_search_timer.setInterval(50)
        self._destination_search_timer.timeout.connect(self._refresh_destination_completions)
        form.addRow("Move/File to:", self.destination)
        self.destination_error = QLabel("Choose a destination page.")
        self.destination_error.setStyleSheet("color: #c43d3d;")
        self.destination_error.setVisible(False)
        if self._destination_required:
            form.addRow("", self.destination_error)
        layout.addLayout(form)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        save_next = QShortcut(QKeySequence("Ctrl+Return"), self)
        save_next.activated.connect(self._accept_and_advance)
        save_next_enter = QShortcut(QKeySequence("Ctrl+Enter"), self)
        save_next_enter.activated.connect(self._accept_and_advance)
        focus_destination = QShortcut(QKeySequence("Ctrl+L"), self)
        focus_destination.activated.connect(self.destination.setFocus)
        self._shortcuts = [save_next, save_next_enter, focus_destination]
        if self._vi_mode:
            destination_next = QShortcut(QKeySequence("Ctrl+Shift+J"), self)
            destination_next.setContext(Qt.WidgetWithChildrenShortcut)
            destination_next.activated.connect(
                lambda: self._handle_vi_focus_or_dropdown(1)
            )
            destination_previous = QShortcut(QKeySequence("Ctrl+Shift+K"), self)
            destination_previous.setContext(Qt.WidgetWithChildrenShortcut)
            destination_previous.activated.connect(
                lambda: self._handle_vi_focus_or_dropdown(-1)
            )
            self._shortcuts.extend([destination_next, destination_previous])

        self._focus_inputs = [self.text_edit]
        if self.outcome is not None:
            self._focus_inputs.append(self.outcome)
        self._focus_inputs.extend(
            [self.status, self.priority, self.start, self.due, self.tags, self.destination]
        )
        for widget in self._focus_inputs:
            widget.installEventFilter(self)
        for combo in (self.outcome, self.status, self.priority):
            if combo is None:
                continue
            view = combo.view()
            view.installEventFilter(self)
            self._combo_views.append(view)
        self._combo_original_indices = {
            combo: combo.currentIndex()
            for combo in (self.outcome, self.status, self.priority)
            if combo is not None
        }

        if self._vi_mode and not self.triage:
            self._set_task_vi_edit_mode(False)

        focus_map = {
            "text": self.text_edit,
            "tags": self.tags,
            "destination": self.destination,
            "due": self.due,
            "start": self.start,
        }
        focus_map.get(focus_field or "text", self.text_edit).setFocus()

    @staticmethod
    def _configure_completer(completer: QCompleter) -> None:
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setCompletionMode(QCompleter.PopupCompletion)
        completer.setFilterMode(Qt.MatchContains)

    def _apply_focus_style(self) -> None:
        accent = self._accent_color
        self.setStyleSheet(
            f"""
            QDialog#taskQuickEditor QLineEdit:focus,
            QDialog#taskQuickEditor QPlainTextEdit:focus,
            QDialog#taskQuickEditor QComboBox:focus {{
                border: 2px solid {accent};
                border-radius: 5px;
            }}
            QDialog#taskQuickEditor QAbstractItemView::item:selected {{
                background-color: {accent};
            }}
            """
        )

    def _set_task_vi_edit_mode(self, editing: bool, *, append: bool = False) -> None:
        if not self._vi_mode or self.triage:
            return
        self._task_vi_edit_mode = bool(editing)
        self.text_edit.setReadOnly(not editing)
        if append and editing:
            cursor = self.text_edit.textCursor()
            cursor.movePosition(QTextCursor.Right)
            self.text_edit.setTextCursor(cursor)
        if self.task_mode_hint is not None:
            self.task_mode_hint.setText(
                "INSERT — Esc returns to navigation"
                if editing
                else "NAV — h/j/k/l or w/b move · i/a edits"
            )
        self.text_edit.setToolTip(
            "Vi insert mode; Esc returns to navigation"
            if editing
            else "Vi navigation mode; press i or a to edit"
        )
        if not editing:
            cursor = self.text_edit.textCursor()
            if cursor.atEnd() and cursor.position() > 0:
                cursor.movePosition(QTextCursor.Left)
                self.text_edit.setTextCursor(cursor)
        self._update_task_nav_cursor()

    def _update_task_nav_cursor(self) -> None:
        if (
            not self._vi_mode
            or self.triage
            or self._task_vi_edit_mode
            or not self.text_edit.hasFocus()
        ):
            self.text_edit.setExtraSelections([])
            return
        cursor = self.text_edit.textCursor()
        if cursor.atEnd() and cursor.position() > 0:
            cursor.movePosition(QTextCursor.Left)
            self.text_edit.setTextCursor(cursor)
        selection = QTextEdit.ExtraSelection()
        selection.cursor = cursor
        selection.cursor.movePosition(QTextCursor.Right, QTextCursor.KeepAnchor)
        if not selection.cursor.hasSelection() and cursor.position() > 0:
            selection.cursor.movePosition(QTextCursor.Left, QTextCursor.KeepAnchor)
        accent = QColor(self._accent_color)
        selection.format.setBackground(accent)
        selection.format.setForeground(QColor("#000000") if accent.lightness() > 150 else QColor("#ffffff"))
        self.text_edit.setExtraSelections([selection])

    def _handle_task_vi_key(self, event) -> bool:
        if not self._vi_mode or self.triage or self.focusWidget() is not self.text_edit:
            return False
        key = event.key()
        modifiers = event.modifiers() & ~Qt.KeypadModifier
        if self._task_vi_edit_mode:
            if key == Qt.Key_Escape:
                self._set_task_vi_edit_mode(False)
                event.accept()
                return True
            return False
        if modifiers and not (
            key in (Qt.Key_I, Qt.Key_A) and modifiers == Qt.ShiftModifier
        ):
            return False
        if key in (Qt.Key_I, Qt.Key_A):
            cursor = self.text_edit.textCursor()
            if event.text() == "I":
                cursor.movePosition(QTextCursor.StartOfLine)
                self.text_edit.setTextCursor(cursor)
                self._set_task_vi_edit_mode(True)
            elif event.text() == "A":
                cursor.movePosition(QTextCursor.EndOfLine)
                self.text_edit.setTextCursor(cursor)
                self._set_task_vi_edit_mode(True)
            else:
                self._set_task_vi_edit_mode(True, append=key == Qt.Key_A)
            event.accept()
            return True
        cursor_moves = {
            Qt.Key_H: QTextCursor.Left,
            Qt.Key_J: QTextCursor.Down,
            Qt.Key_K: QTextCursor.Up,
            Qt.Key_L: QTextCursor.Right,
            Qt.Key_W: QTextCursor.NextWord,
            Qt.Key_B: QTextCursor.PreviousWord,
            Qt.Key_0: QTextCursor.StartOfLine,
            Qt.Key_Q: QTextCursor.StartOfLine,
            Qt.Key_Semicolon: QTextCursor.EndOfLine,
            Qt.Key_Dollar: QTextCursor.EndOfLine,
        }
        operation = cursor_moves.get(key)
        if operation is not None:
            cursor = self.text_edit.textCursor()
            cursor.movePosition(operation)
            self.text_edit.setTextCursor(cursor)
            event.accept()
            return True
        # Navigation mode must never insert printable text accidentally.
        if event.text() and event.text().isprintable():
            event.accept()
            return True
        return False

    def _date_field(self, edit: QLineEdit, tooltip: str) -> QWidget:
        container = QWidget(self)
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        row.addWidget(edit, 1)
        button = QToolButton(container)
        button.setText("📅")
        button.setToolTip(tooltip)
        button.setFocusPolicy(Qt.NoFocus)
        button.clicked.connect(lambda _checked=False, target=edit, anchor=button: self._pick_date(target, anchor))
        row.addWidget(button)
        container.button = button  # type: ignore[attr-defined]
        return container

    def _pick_date(self, target: QLineEdit, anchor: QToolButton) -> None:
        anchor_pos = anchor.mapToGlobal(QPoint(0, anchor.height() + 4))
        picker = DateInsertDialog(
            self,
            anchor_pos=anchor_pos,
            accept_on_double_click=True,
            accept_on_enter=True,
            allow_nav_keys=False,
            use_vi_keys=self._vi_mode,
            keep_edit_focus=True,
            vault_accent_color=self._accent_color,
        )
        if picker.exec() == QDialog.Accepted:
            selected = picker.selected_date_text()
            if selected:
                target.setText(selected)
                target.setFocus()

    def _complete_tag(self, _text: str) -> None:
        self._tags_dirty = self.tags.text() != self._tags_original_value
        prefix = self.tags.text()[: self.tags.cursorPosition()]
        match = re.search(r"(?:^|[\s,])(@?[^\s,]*)$", prefix)
        if not match:
            self._tags_browsing = False
            self._tag_completer.popup().hide()
            return
        token = match.group(1)
        if not token:
            self._tags_browsing = False
            self._tag_completer.popup().hide()
            return
        self._tag_completer.setCompletionPrefix(token)
        if token and self._tag_completer.completionCount():
            self._tags_browsing = True
            self._tag_completer.popup().setMinimumWidth(self.tags.width())
            self._tag_completer.complete(self.tags.cursorRect())
            self._tag_completer.popup().clearSelection()
            self._tag_completer.popup().setCurrentIndex(QModelIndex())
        else:
            self._tags_browsing = False
            self._tag_completer.popup().hide()

    def _replace_tag_completion(self, completion: str) -> None:
        text = self.tags.text()
        cursor = self.tags.cursorPosition()
        match = re.search(r"(?:^|[\s,])(@?[^\s,]*)$", text[:cursor])
        if not match:
            return
        start = match.start(1)
        updated = text[:start] + completion + text[cursor:]
        self.tags.setText(updated)
        self.tags.setCursorPosition(start + len(completion))
        self._tags_dirty = self.tags.text() != self._tags_original_value
        self._tags_browsing = False
        self._tag_completer.popup().hide()

    def _reset_tags(self) -> bool:
        if not (self._tags_dirty or self._tags_browsing):
            return False
        self._tag_completer.popup().hide()
        self.tags.setText(self._tags_original_value)
        self.tags.setCursorPosition(len(self.tags.text()))
        self._tags_dirty = False
        self._tags_browsing = False
        return True

    def _on_destination_edited(self, _text: str) -> None:
        self._destination_dirty = self.destination.text() != self._destination_original_value
        self._destination_browsing = bool(self.destination.text().strip())
        self._queue_destination_search()

    def _queue_destination_search(self) -> None:
        if self.destination.text().strip():
            self._destination_search_timer.start()
        else:
            self._destination_search_timer.stop()
            self._destination_model.setStringList([])
            self._destination_completer.popup().hide()

    def _refresh_destination_completions(self) -> None:
        query = self.destination.text().strip().lstrip(":")
        if not self._page_search or not query:
            self._destination_model.setStringList([])
            return
        try:
            values = list(dict.fromkeys(self._page_search(query)))
        except Exception:
            values = []
        self._destination_model.setStringList(values)
        self._destination_completer.setCompletionPrefix(self.destination.text().strip())
        if values:
            self._destination_completer.popup().setMinimumWidth(self.destination.width())
            self._destination_completer.complete(self.destination.cursorRect())
            self._destination_completer.popup().clearSelection()
            self._destination_completer.popup().setCurrentIndex(QModelIndex())
        else:
            self._destination_completer.popup().hide()

    def _choose_destination_completion(self, value: str) -> None:
        self.destination.setText(str(value or ""))
        self.destination.setCursorPosition(len(self.destination.text()))
        self._destination_dirty = self.destination.text() != self._destination_original_value
        self._destination_browsing = False
        self._destination_search_timer.stop()
        self._destination_completer.popup().hide()

    def _reset_destination(self) -> bool:
        if not (self._destination_dirty or self._destination_browsing):
            return False
        self._destination_search_timer.stop()
        self._destination_completer.popup().hide()
        self._destination_model.setStringList([])
        self.destination.setText(self._destination_original_value)
        self.destination.setCursorPosition(len(self.destination.text()))
        self._destination_dirty = False
        self._destination_browsing = False
        return True

    def _move_destination_completion_selection(self, direction: int) -> bool:
        popup = self._destination_completer.popup()
        if not (self.destination.hasFocus() or popup.hasFocus()):
            return False
        if not popup.isVisible():
            return False
        model = self._destination_completer.completionModel()
        row_count = model.rowCount()
        if not row_count:
            return False
        current_row = popup.currentIndex().row()
        if direction > 0:
            next_row = 0 if current_row < 0 else min(current_row + 1, row_count - 1)
        else:
            next_row = row_count - 1 if current_row < 0 else max(current_row - 1, 0)
        popup.setCurrentIndex(model.index(next_row, 0))
        return True

    def _move_tag_completion_selection(self, direction: int) -> bool:
        popup = self._tag_completer.popup()
        if not (self.tags.hasFocus() or popup.hasFocus()) or not popup.isVisible():
            return False
        model = self._tag_completer.completionModel()
        row_count = model.rowCount()
        if not row_count:
            return False
        current_row = popup.currentIndex().row()
        if direction > 0:
            next_row = 0 if current_row < 0 else min(current_row + 1, row_count - 1)
        else:
            next_row = row_count - 1 if current_row < 0 else max(current_row - 1, 0)
        popup.setCurrentIndex(model.index(next_row, 0))
        return True

    def _active_combo_popup(self) -> Optional[QComboBox]:
        for combo in (self.outcome, self.status, self.priority):
            if combo is not None and combo.view().isVisible():
                return combo
        return None

    def _move_combo_selection(self, combo: QComboBox, direction: int) -> bool:
        if combo.count() <= 0:
            return False
        combo.setCurrentIndex((combo.currentIndex() + direction) % combo.count())
        return True

    def _reset_combo(self, combo: QComboBox) -> None:
        combo.hidePopup()
        combo.setCurrentIndex(self._combo_original_indices.get(combo, combo.currentIndex()))
        combo.setFocus()

    def _restore_focused_field(self, watched) -> bool:
        combo = watched if isinstance(watched, QComboBox) else next(
            (
                candidate
                for candidate in (self.outcome, self.status, self.priority)
                if candidate is not None and candidate.view() is watched
            ),
            None,
        )
        if combo is not None:
            original = self._combo_original_indices.get(combo, combo.currentIndex())
            if combo.currentIndex() != original or combo.view().isVisible():
                self._reset_combo(combo)
                return True
            return False
        if watched is self.text_edit:
            changed = self.text_edit.toPlainText() != self._text_original_value
            if changed:
                cursor_position = min(
                    self.text_edit.textCursor().position(),
                    len(self._text_original_value),
                )
                self.text_edit.setPlainText(self._text_original_value)
                cursor = self.text_edit.textCursor()
                cursor.setPosition(cursor_position)
                self.text_edit.setTextCursor(cursor)
                if self._vi_mode and not self.triage:
                    self._set_task_vi_edit_mode(False)
                return True
            return False
        if watched is self.tags:
            return self._reset_tags()
        if watched is self.destination:
            return self._reset_destination()
        if watched in self._line_original_values:
            original = self._line_original_values[watched]
            if watched.text() != original:
                watched.setText(original)
                watched.setCursorPosition(len(original))
                return True
        return False

    def _cycle_editor_focus(self, direction: int) -> bool:
        focused = self.focusWidget()
        if focused not in self._focus_inputs:
            return False
        current = self._focus_inputs.index(focused)
        self._focus_inputs[(current + direction) % len(self._focus_inputs)].setFocus()
        return True

    def _handle_vi_focus_or_dropdown(self, direction: int) -> None:
        tag_popup = self._tag_completer.popup()
        tag_active = self.tags.hasFocus() or tag_popup.hasFocus()
        if tag_active and self._tags_browsing:
            if not tag_popup.isVisible():
                self._complete_tag(self.tags.text())
            if tag_popup.isVisible():
                self._move_tag_completion_selection(direction)
            return
        popup = self._destination_completer.popup()
        destination_active = self.destination.hasFocus() or popup.hasFocus()
        if destination_active and self._destination_browsing:
            if not popup.isVisible():
                self._destination_search_timer.stop()
                self._refresh_destination_completions()
            if popup.isVisible():
                self._move_destination_completion_selection(direction)
            return
        combo = self._active_combo_popup()
        if combo is not None:
            self._move_combo_selection(combo, direction)
            return
        self._cycle_editor_focus(direction)

    def eventFilter(self, watched, event) -> bool:  # type: ignore[override]
        if watched is self.text_edit and event.type() in (QEvent.FocusIn, QEvent.FocusOut):
            QTimer.singleShot(0, self._update_task_nav_cursor)
        if (watched in self._focus_inputs or watched in self._combo_views) and event.type() == QEvent.KeyPress:
            key = event.key()
            if key == Qt.Key_Escape and self._restore_focused_field(watched):
                event.accept()
                return True
            if watched is self.text_edit and self._handle_task_vi_key(event):
                return True
            if watched in self._combo_views:
                combo = next(
                    (candidate for candidate in (self.outcome, self.status, self.priority) if candidate is not None and candidate.view() is watched),
                    None,
                )
                if combo is not None and key == Qt.Key_Escape:
                    self._reset_combo(combo)
                    event.accept()
                    return True
            if isinstance(watched, QComboBox) and key == Qt.Key_Escape:
                original = self._combo_original_indices.get(watched, watched.currentIndex())
                if watched.currentIndex() != original:
                    self._reset_combo(watched)
                    event.accept()
                    return True
            if (
                watched is self.text_edit
                and not self.triage
                and key in (Qt.Key_Return, Qt.Key_Enter)
                and not event.modifiers()
            ):
                self.accept()
                event.accept()
                return True
            completion_popup_open = (
                (watched is self.tags and self._tag_completer.popup().isVisible())
                or (
                    watched is self.destination
                    and self._destination_completer.popup().isVisible()
                )
            )
            if watched is self.tags and self._tag_completer.popup().isVisible():
                tag_popup = self._tag_completer.popup()
                modifiers = event.modifiers() & ~Qt.KeypadModifier
                vi_dropdown_key = (
                    self._vi_mode
                    and key in (Qt.Key_J, Qt.Key_K)
                    and bool(modifiers & Qt.ControlModifier)
                    and bool(modifiers & Qt.ShiftModifier)
                    and not bool(modifiers & (Qt.AltModifier | Qt.MetaModifier))
                )
                if vi_dropdown_key:
                    self._move_tag_completion_selection(1 if key == Qt.Key_J else -1)
                    event.accept()
                    return True
                if key in (Qt.Key_Return, Qt.Key_Enter) and tag_popup.currentIndex().isValid():
                    self._replace_tag_completion(str(tag_popup.currentIndex().data() or ""))
                    event.accept()
                    return True
                if key == Qt.Key_Escape:
                    self._reset_tags()
                    event.accept()
                    return True
            if watched is self.tags and key == Qt.Key_Escape and self._reset_tags():
                event.accept()
                return True
            if watched is self.destination and completion_popup_open:
                popup = self._destination_completer.popup()
                model = self._destination_completer.completionModel()
                modifiers = event.modifiers() & ~Qt.KeypadModifier
                vi_dropdown_key = (
                    self._vi_mode
                    and key in (Qt.Key_J, Qt.Key_K)
                    and bool(modifiers & Qt.ControlModifier)
                    and bool(modifiers & Qt.ShiftModifier)
                    and not bool(modifiers & (Qt.AltModifier | Qt.MetaModifier))
                )
                if key in (Qt.Key_Down, Qt.Key_Up) or vi_dropdown_key:
                    moving_down = key in (Qt.Key_Down, Qt.Key_J)
                    self._move_destination_completion_selection(1 if moving_down else -1)
                    event.accept()
                    return True
                if key in (Qt.Key_Return, Qt.Key_Enter) and popup.currentIndex().isValid():
                    self._choose_destination_completion(str(popup.currentIndex().data() or ""))
                    event.accept()
                    return True
                if key == Qt.Key_Escape:
                    self._reset_destination()
                    event.accept()
                    return True
            if watched is self.destination and key == Qt.Key_Escape and self._reset_destination():
                event.accept()
                return True
            modifiers = event.modifiers() & ~Qt.KeypadModifier
            destination_vi_key = (
                watched is self.destination
                and self._vi_mode
                and self._destination_browsing
                and key in (Qt.Key_J, Qt.Key_K)
                and bool(modifiers & Qt.ControlModifier)
                and bool(modifiers & Qt.ShiftModifier)
                and not bool(modifiers & (Qt.AltModifier | Qt.MetaModifier))
            )
            if destination_vi_key:
                self._handle_vi_focus_or_dropdown(1 if key == Qt.Key_J else -1)
                event.accept()
                return True
            if completion_popup_open and key in (Qt.Key_Down, Qt.Key_Up):
                return super().eventFilter(watched, event)
            direction = 0
            if key == Qt.Key_Down:
                direction = 1
            elif key == Qt.Key_Up:
                direction = -1
            elif self._vi_mode and key in (Qt.Key_J, Qt.Key_K):
                editable = isinstance(watched, (QLineEdit, QPlainTextEdit))
                if not editable or event.modifiers() & Qt.ControlModifier:
                    direction = 1 if key == Qt.Key_J else -1
            if direction:
                self._cycle_editor_focus(direction)
                event.accept()
                return True
        return super().eventFilter(watched, event)

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        if self._positioned or self._anchor_pos is None:
            return
        self._positioned = True
        self.adjustSize()
        bounds = popup_available_geometry(anchor=self._anchor_pos, parent=self.parentWidget() or self)
        desired = QPoint(self._anchor_pos.x() + 10, self._anchor_pos.y() - 32)
        self.move(clamp_popup_top_left(desired, self.size(), bounds, margin=8))

    def _accept_and_advance(self) -> None:
        self.save_and_next = True
        self.accept()

    def accept(self) -> None:  # type: ignore[override]
        if self._destination_required and not self.destination.text().strip():
            self.save_and_next = False
            self.destination_error.setVisible(True)
            self.destination.setFocus()
            return
        self.destination_error.setVisible(False)
        super().accept()

    def values(self) -> dict:
        raw_text = (
            self.text_edit.toPlainText()
            if isinstance(self.text_edit, QPlainTextEdit)
            else self.text_edit.text()
        )
        if not self.triage:
            raw_text = " ".join(raw_text.splitlines())
        tags = [value.lstrip("@") for value in re.split(r"[\s,]+", self.tags.text()) if value]
        destination = self.destination.text().strip() or None
        if self._destination_required and not destination:
            raise ValueError("Choose a destination page before saving.")
        return {
            "text": raw_text.strip(),
            "status": self.status.currentData(),
            "priority": int(self.priority.currentData() or 0),
            "start": parse_date_shortcut(self.start.text()),
            "due": parse_date_shortcut(self.due.text()),
            "tags": tags,
            "destination": destination,
            "action": self.outcome.currentData() if self.outcome else None,
        }
