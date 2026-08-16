from __future__ import annotations

import re
from typing import Callable, Optional

from PySide6.QtCore import QEvent, QModelIndex, QPoint, QRect, QStringListModel, Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QCompleter,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from .path_utils import path_to_colon
from .screen_positioning import clamp_popup_top_left, popup_available_geometry


def _editor_task_symbols(text: str) -> str:
    """Render Markdown task markers the same way as the main editor."""
    return re.sub(
        r"(?m)^(\s*)[-*]\s*\[([ xX])\]\s*",
        lambda match: f"{match.group(1)}{'☑' if match.group(2).lower() == 'x' else '☐'} ",
        str(text or ""),
    )


class QuickCaptureProcessorDialog(QDialog):
    """Move-only controller for marker-free Quick Capture chunks."""

    SCOPES = (
        ("Active sources", "active"),
        ("Configured page only", "configured"),
        ("Calendar ±1 week", "calendar"),
        ("All capture pages", "all"),
    )

    def __init__(
        self,
        parent=None,
        *,
        item_provider: Callable[[str], tuple[list[dict], int]],
        activate_item: Callable[[dict], None],
        move_item: Callable[[dict, str], bool],
        undo_last: Callable[[], bool],
        page_search: Callable[[str], list[str]],
        anchor_rect: Optional[QRect] = None,
        vi_mode: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("quickCaptureProcessorDialog")
        self.setWindowTitle("Process Quick Captures")
        self.setModal(True)
        self.resize(390, 300)
        self._item_provider = item_provider
        self._activate_item = activate_item
        self._move_item = move_item
        self._undo_last = undo_last
        self._page_search = page_search
        self._anchor_rect = anchor_rect
        self._positioned = False
        self._vi_mode = bool(vi_mode)
        self._items: list[dict] = []
        self._index = 0
        self._older_count = 0
        self._choosing_destination = False
        self._move_in_progress = False

        layout = QVBoxLayout(self)
        self.scope = QComboBox(self)
        for label, value in self.SCOPES:
            self.scope.addItem(label, value)
        self.scope.currentIndexChanged.connect(self._reload_items)
        layout.addWidget(self.scope)

        self.position_label = QLabel(self)
        self.position_label.setObjectName("quickCapturePositionLabel")
        layout.addWidget(self.position_label)
        self.source_label = QLabel(self)
        self.source_label.setObjectName("quickCaptureSourceLabel")
        self.source_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.source_label)
        self.preview = QLabel(self)
        self.preview.setObjectName("quickCapturePreview")
        self.preview.setWordWrap(True)
        self.preview.setMinimumHeight(70)
        self.preview.setStyleSheet("padding: 8px; border: 1px solid palette(mid); border-radius: 5px;")
        layout.addWidget(self.preview)

        move_row = QHBoxLayout()
        self.move_button = QPushButton("M  Move", self)
        self.move_button.setObjectName("quickCaptureMoveButton")
        self.move_button.clicked.connect(self._focus_destination)
        move_row.addWidget(self.move_button)
        self.destination = QLineEdit(self)
        self.destination.setObjectName("quickCaptureDestination")
        self.destination.setPlaceholderText("Type target page…")
        move_row.addWidget(self.destination, 1)
        layout.addLayout(move_row)

        self._destination_model = QStringListModel([], self)
        self._destination_completer = QCompleter(self._destination_model, self)
        self._destination_completer.setCaseSensitivity(Qt.CaseInsensitive)
        self._destination_completer.setCompletionMode(QCompleter.PopupCompletion)
        self._destination_completer.setFilterMode(Qt.MatchContains)
        self._destination_completer.setWidget(self.destination)
        self._destination_completer.activated[str].connect(self._select_and_move)
        self.destination.textEdited.connect(self._destination_edited)
        self.destination.installEventFilter(self)
        self._destination_completer.popup().installEventFilter(self)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(50)
        self._search_timer.timeout.connect(self._refresh_destinations)

        navigation = QHBoxLayout()
        self.previous_button = QPushButton("↑ Previous", self)
        self.previous_button.clicked.connect(lambda: self._step(-1))
        navigation.addWidget(self.previous_button)
        self.next_button = QPushButton("Next ↓", self)
        self.next_button.clicked.connect(lambda: self._step(1))
        navigation.addWidget(self.next_button)
        self.undo_button = QPushButton("Undo", self)
        self.undo_button.clicked.connect(self._undo)
        navigation.addWidget(self.undo_button)
        self.close_button = QPushButton("Close", self)
        self.close_button.clicked.connect(self.reject)
        navigation.addWidget(self.close_button)
        layout.addLayout(navigation)

        for widget in (
            self.scope,
            self.move_button,
            self.previous_button,
            self.next_button,
            self.undo_button,
            self.close_button,
        ):
            widget.installEventFilter(self)
        move_shortcut = QShortcut(QKeySequence("M"), self)
        move_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        move_shortcut.activated.connect(self._focus_destination)
        undo_shortcut = QShortcut(QKeySequence("Ctrl+Z"), self)
        undo_shortcut.activated.connect(self._undo)
        self._shortcuts = [move_shortcut, undo_shortcut]
        self._reload_items()

    def current_item(self) -> Optional[dict]:
        if not self._items:
            return None
        return self._items[max(0, min(self._index, len(self._items) - 1))]

    def _reload_items(self) -> None:
        scope = str(self.scope.currentData() or "active")
        self._items, self._older_count = self._item_provider(scope)
        self._index = min(self._index, max(0, len(self._items) - 1))
        self.destination.clear()
        self._destination_model.setStringList([])
        self._destination_completer.popup().hide()
        self._choosing_destination = False
        self._show_current()

    def _show_current(self) -> None:
        item = self.current_item()
        if item is None:
            suffix = f" · {self._older_count} older outside this scope" if self._older_count else ""
            self.position_label.setText(f"No Quick Captures in this scope{suffix}")
            self.source_label.clear()
            self.preview.setText("All captures in this scope have been moved.")
            self._set_actions_enabled(False)
            return
        outside = f" · {self._older_count} older outside this scope" if self._older_count else ""
        self.position_label.setText(f"{self._index + 1} of {len(self._items)}{outside}")
        colon = path_to_colon(str(item.get("path") or ""))
        timestamp = str(item.get("timestamp") or "").strip()
        self.source_label.setText(
            f":{colon.lstrip(':')}" + (f" · {timestamp}" if timestamp else "")
        )
        text = _editor_task_symbols(str(item.get("text") or "").strip())
        self.preview.setText(text if len(text) <= 500 else text[:497] + "…")
        self._set_actions_enabled(True)
        self._activate_item(item)

    def _set_actions_enabled(self, enabled: bool) -> None:
        self.move_button.setEnabled(enabled)
        self.destination.setEnabled(enabled)
        self.previous_button.setEnabled(enabled and len(self._items) > 1)
        self.next_button.setEnabled(enabled and len(self._items) > 1)

    def _step(self, amount: int) -> None:
        if not self._items:
            return
        self._index = (self._index + amount) % len(self._items)
        self.destination.clear()
        self._destination_completer.popup().hide()
        self._choosing_destination = False
        self._show_current()

    def _focus_destination(self) -> None:
        if not self.current_item():
            return
        self._choosing_destination = True
        self.destination.setFocus(Qt.ShortcutFocusReason)
        self.destination.selectAll()
        self._refresh_destinations()

    def _destination_edited(self, _text: str) -> None:
        self._choosing_destination = True
        self._search_timer.start()

    def _refresh_destinations(self) -> None:
        query = self.destination.text().strip()
        self._destination_model.setStringList(self._page_search(query))
        self._destination_completer.setCompletionPrefix(query)
        popup = self._destination_completer.popup()
        popup.setCurrentIndex(QModelIndex())
        if self._destination_model.rowCount():
            self._destination_completer.complete()

    def _move_destination_selection(self, amount: int) -> bool:
        popup = self._destination_completer.popup()
        if not popup.isVisible():
            self._search_timer.stop()
            self._refresh_destinations()
        model = self._destination_completer.completionModel()
        count = model.rowCount()
        if not count:
            return False
        row = popup.currentIndex().row()
        if row < 0:
            row = 0 if amount > 0 else count - 1
        else:
            row = (row + amount) % count
        popup.setCurrentIndex(model.index(row, 0))
        return True

    def _select_and_move(self, destination: str) -> None:
        if self._move_in_progress:
            return
        item = self.current_item()
        value = str(destination or "").strip()
        if not item or not value:
            return
        self.destination.setText(value)
        self._destination_completer.popup().hide()
        self._move_in_progress = True
        try:
            if self._move_item(item, value):
                self._reload_items()
        finally:
            self._move_in_progress = False

    def _undo(self) -> None:
        if self._undo_last():
            self._reload_items()

    def eventFilter(self, watched, event) -> bool:  # type: ignore[override]
        if event.type() != QEvent.KeyPress:
            return super().eventFilter(watched, event)
        key = event.key()
        mods = event.modifiers() & ~Qt.KeypadModifier
        popup = self._destination_completer.popup()
        destination_event = watched is self.destination or watched is popup
        vi_dropdown = (
            self._vi_mode
            and destination_event
            and key in (Qt.Key_J, Qt.Key_K)
            and bool(mods & Qt.ControlModifier)
            and bool(mods & Qt.ShiftModifier)
            and not bool(mods & (Qt.AltModifier | Qt.MetaModifier))
        )
        if destination_event and (key in (Qt.Key_Down, Qt.Key_Up) or vi_dropdown):
            down = key in (Qt.Key_Down, Qt.Key_J)
            self._move_destination_selection(1 if down else -1)
            event.accept()
            return True
        if destination_event and key in (Qt.Key_Return, Qt.Key_Enter):
            index = popup.currentIndex()
            if index.isValid():
                self._select_and_move(str(index.data() or ""))
                event.accept()
                return True
            typed = self.destination.text().strip().casefold()
            exact = next(
                (value for value in self._destination_model.stringList() if value.casefold() == typed),
                None,
            )
            if exact:
                self._select_and_move(exact)
                event.accept()
                return True
        if destination_event and key == Qt.Key_Escape:
            self.destination.clear()
            self._destination_model.setStringList([])
            popup.hide()
            self._choosing_destination = False
            self.move_button.setFocus()
            event.accept()
            return True
        direction = 0
        if key == Qt.Key_Down:
            direction = 1
        elif key == Qt.Key_Up:
            direction = -1
        elif self._vi_mode and not destination_event and key in (Qt.Key_J, Qt.Key_K):
            direction = 1 if key == Qt.Key_J else -1
        if direction:
            self._step(direction)
            event.accept()
            return True
        return super().eventFilter(watched, event)

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        if self._positioned or self._anchor_rect is None:
            return
        self._positioned = True
        available = popup_available_geometry(self.parentWidget(), self._anchor_rect.center())
        outside_x = self._anchor_rect.right() + 8
        if outside_x + self.width() <= available.right() - 8:
            x = outside_x
        else:
            x = available.right() - self.width() - 8
        desired = QPoint(x, self._anchor_rect.top() + 12)
        self.move(clamp_popup_top_left(desired, self.size(), available))
