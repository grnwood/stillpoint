from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal, QEvent, QObject
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QCheckBox,
)


class FindReplaceBar(QWidget):
    findNextRequested = Signal(str, bool, bool)  # query, backwards, case_sensitive
    replaceRequested = Signal(str)  # replacement text
    replaceAllRequested = Signal(str, str, bool)  # query, replacement, case_sensitive
    closed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setVisible(False)
        self.setStyleSheet(
            "QWidget {"
            "  background: palette(base);"
            "  border-top: 1px solid #555;"
            "}"
            "QLineEdit {"
            "  border: 1px solid #777;"
            "  border-radius: 4px;"
            "  padding: 4px 6px;"
            "}"
            "QLineEdit:focus {"
            "  border: 1px solid #5aa1ff;"
            "}"
            "QPushButton {"
            "  padding: 4px 8px;"
            "}"
        )
        self._pending_backwards: Optional[bool] = None
        self._last_backwards: bool = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)

        row1 = QHBoxLayout()
        row1.setContentsMargins(0, 0, 0, 0)
        row1.addWidget(QLabel("Find:"))
        self.query_edit = QLineEdit()
        row1.addWidget(self.query_edit, 1)
        layout.addLayout(row1)

        self.replace_row = QHBoxLayout()
        self.replace_row.setContentsMargins(0, 0, 0, 0)
        replace_label = QLabel("Replace:")
        replace_label.setMinimumWidth(70)
        first_label = row1.itemAt(0).widget()
        if first_label:
            first_label.setMinimumWidth(70)
        self.replace_row.addWidget(replace_label)
        self.replace_edit = QLineEdit()
        self.replace_row.addWidget(self.replace_edit, 1)
        self.replace_btn = QPushButton("Replace")
        self.replace_btn.clicked.connect(self._emit_replace)
        self.replace_row.addWidget(self.replace_btn)
        self.replace_all_btn = QPushButton("Replace All")
        self.replace_all_btn.clicked.connect(self._emit_replace_all)
        self.replace_row.addWidget(self.replace_all_btn)
        layout.addLayout(self.replace_row)

        # Buttons row (keeps tab order after inputs)
        actions_row = QHBoxLayout()
        actions_row.setContentsMargins(0, 0, 0, 0)
        self.find_prev_btn = QPushButton("Find Prev")
        self.find_prev_btn.clicked.connect(lambda: self._emit_find(backwards=True))
        actions_row.addWidget(self.find_prev_btn)
        self.find_next_btn = QPushButton("Find Next")
        self.find_next_btn.clicked.connect(lambda: self._emit_find(backwards=False))
        actions_row.addWidget(self.find_next_btn)
        self.case_checkbox = QCheckBox("Case sensitive")
        self.case_checkbox.setChecked(False)
        actions_row.addWidget(self.case_checkbox)
        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.hide_bar)
        actions_row.addWidget(self.close_btn)
        actions_row.addStretch()
        layout.addLayout(actions_row)

        self.query_edit.installEventFilter(self)
        self.replace_edit.installEventFilter(self)
        self.setFocusPolicy(Qt.NoFocus)
        self._set_replace_mode(False)

    def _emit_find(self, backwards: Optional[bool] = None) -> None:
        direction: bool
        if backwards is not None:
            direction = backwards
        elif self._pending_backwards is not None:
            direction = self._pending_backwards
        else:
            direction = self._last_backwards
        self._pending_backwards = None
        self._last_backwards = direction
        self.findNextRequested.emit(self.query_edit.text(), direction, self.case_checkbox.isChecked())

    def _emit_replace(self) -> None:
        self.replaceRequested.emit(self.replace_edit.text())
        self._emit_find()

    def _emit_replace_all(self) -> None:
        self.replaceAllRequested.emit(self.query_edit.text(), self.replace_edit.text(), self.case_checkbox.isChecked())

    def show_bar(self, *, replace: bool, query: str, backwards: bool) -> None:
        self._set_replace_mode(replace)
        self._pending_backwards = backwards
        self._last_backwards = backwards
        if query:
            self.query_edit.setText(query)
            self.query_edit.selectAll()
        self.setVisible(True)
        self.query_edit.setFocus(Qt.ShortcutFocusReason)
        self.raise_()

    def hide_bar(self) -> None:
        self.setVisible(False)
        self.closed.emit()

    def current_query(self) -> str:
        return self.query_edit.text()

    def current_replacement(self) -> str:
        return self.replace_edit.text()

    def focus_query(self) -> None:
        self.query_edit.setFocus(Qt.ShortcutFocusReason)
        self.query_edit.selectAll()

    def _set_replace_mode(self, enabled: bool) -> None:
        self.replace_btn.setVisible(enabled)
        self.replace_all_btn.setVisible(enabled)
        self.replace_edit.setVisible(enabled)
        for i in range(self.replace_row.count()):
            item = self.replace_row.itemAt(i)
            widget = item.widget()
            if widget:
                widget.setVisible(enabled)
        self.replace_row.setEnabled(enabled)

    def eventFilter(self, obj: QObject, event: QEvent):  # type: ignore[override]
        if event.type() == QEvent.KeyPress:
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                if obj == self.query_edit:
                    backwards = bool(event.modifiers() & Qt.ShiftModifier)
                    self._emit_find(backwards=backwards)
                    return True
                if obj == self.replace_edit:
                    self._emit_replace()
                    return True
            if event.key() == Qt.Key_Escape:
                self.hide_bar()
                return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event):  # type: ignore[override]
        if event.key() == Qt.Key_Escape:
            self.hide_bar()
            event.accept()
            return
        super().keyPressEvent(event)
