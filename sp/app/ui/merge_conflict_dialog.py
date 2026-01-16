from __future__ import annotations

import difflib
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class MergeConflictDialog(QDialog):
    """Resolve conflicts between local and remote text with per-diff decisions."""

    def __init__(self, local_text: str, remote_text: str, path: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Resolve Conflict")
        self.resize(1200, 800)

        self._local_text = local_text or ""
        self._remote_text = remote_text or ""
        self._local_lines = self._local_text.splitlines()
        self._remote_lines = self._remote_text.splitlines()
        self._local_trailing_newline = self._local_text.endswith("\n")
        self._remote_trailing_newline = self._remote_text.endswith("\n")

        matcher = difflib.SequenceMatcher(a=self._local_lines, b=self._remote_lines)
        self._opcodes = matcher.get_opcodes()
        self._diffs: list[tuple[str, int, int, int, int]] = [
            opcode for opcode in self._opcodes if opcode[0] != "equal"
        ]
        self._decisions: list[Optional[str]] = [None] * len(self._diffs)
        self._current_diff = 0 if self._diffs else -1
        self._merged_text = self._local_text
        self._merged_ranges: dict[int, tuple[int, int]] = {}

        self.setStyleSheet(
            """
            QDialog { background-color: #0b0b0b; }
            QLabel { color: #e8e8e8; }
            QPushButton {
                background-color: #1a1a1a;
                color: #e8e8e8;
                border: 1px solid #2a2a2a;
                padding: 6px 12px;
            }
            QPushButton:disabled {
                color: #777;
                border-color: #222;
            }
            """
        )
        layout = QVBoxLayout(self)
        title = QLabel(f"Resolve conflict for {path}" if path else "Resolve conflict")
        title.setStyleSheet("font-weight: bold;")
        layout.addWidget(title)

        splitter = QSplitter(Qt.Horizontal)

        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_label = QLabel("Current (local)")
        self._left_edit = QPlainTextEdit()
        self._left_edit.setReadOnly(True)
        self._left_edit.setStyleSheet(
            "QPlainTextEdit { background-color: #101010; color: #e6e6e6; border: 1px solid #222; }"
        )
        left_layout.addWidget(left_label)
        left_layout.addWidget(self._left_edit)

        center_panel = QWidget()
        center_layout = QVBoxLayout(center_panel)
        center_layout.setContentsMargins(6, 0, 6, 0)
        center_layout.addStretch()
        self._apply_button = QPushButton("←")
        self._apply_button.setToolTip("Apply remote change to local")
        self._apply_button.clicked.connect(self._accept_current_change)
        center_layout.addWidget(self._apply_button)
        center_layout.addStretch()

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_label = QLabel("Server revision")
        self._right_edit = QPlainTextEdit()
        self._right_edit.setReadOnly(True)
        self._right_edit.setStyleSheet(
            "QPlainTextEdit { background-color: #101010; color: #e6e6e6; border: 1px solid #222; }"
        )
        right_layout.addWidget(right_label)
        right_layout.addWidget(self._right_edit)

        splitter.addWidget(left_panel)
        splitter.addWidget(center_panel)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setStretchFactor(2, 1)
        layout.addWidget(splitter, stretch=1)

        nav_layout = QHBoxLayout()
        self._prev_button = QPushButton("Previous")
        self._prev_button.clicked.connect(self._previous_diff)
        self._next_button = QPushButton("Next")
        self._next_button.clicked.connect(self._next_diff)
        nav_layout.addWidget(self._prev_button)
        nav_layout.addWidget(self._next_button)

        self._status_label = QLabel("")
        nav_layout.addWidget(self._status_label, stretch=1)

        self._reject_button = QPushButton("Reject Change")
        self._reject_button.clicked.connect(self._reject_current_change)
        self._accept_button = QPushButton("Accept Change")
        self._accept_button.clicked.connect(self._accept_current_change)
        self._accept_both_button = QPushButton("Accept Both")
        self._accept_both_button.clicked.connect(self._accept_both_change)
        nav_layout.addWidget(self._reject_button)
        nav_layout.addWidget(self._accept_button)
        nav_layout.addWidget(self._accept_both_button)
        layout.addLayout(nav_layout)

        action_layout = QHBoxLayout()
        action_layout.addStretch()
        self._final_accept = QPushButton("Accept Merge")
        self._final_accept.setEnabled(False)
        self._final_accept.clicked.connect(self._accept_merge)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        action_layout.addWidget(self._final_accept)
        action_layout.addWidget(cancel_button)
        layout.addLayout(action_layout)

        self._refresh_views()

    def merged_text(self) -> str:
        return self._merged_text

    def _refresh_views(self) -> None:
        self._recompute_merged_text()
        self._left_edit.setPlainText(self._merged_text)
        self._right_edit.setPlainText(self._remote_text)
        self._update_navigation_state()
        self._highlight_current_diff()

    def _recompute_merged_text(self) -> None:
        merged_lines: list[str] = []
        merged_ranges: dict[int, tuple[int, int]] = {}
        diff_index = 0
        line_cursor = 0
        for tag, i1, i2, j1, j2 in self._opcodes:
            if tag == "equal":
                merged_lines.extend(self._local_lines[i1:i2])
                line_cursor += i2 - i1
                continue
            decision = self._decisions[diff_index]
            if decision == "remote":
                segment = self._remote_lines[j1:j2]
            elif decision == "both":
                segment = self._local_lines[i1:i2] + self._remote_lines[j1:j2]
            else:
                segment = self._local_lines[i1:i2]
            merged_ranges[diff_index] = (line_cursor, line_cursor + len(segment))
            merged_lines.extend(segment)
            line_cursor += len(segment)
            diff_index += 1
        text = "\n".join(merged_lines)
        if self._local_trailing_newline or self._remote_trailing_newline:
            if text and not text.endswith("\n"):
                text += "\n"
        self._merged_text = text
        self._merged_ranges = merged_ranges

    def _update_navigation_state(self) -> None:
        has_diffs = bool(self._diffs)
        if not has_diffs:
            self._status_label.setText("No differences detected.")
            self._prev_button.setEnabled(False)
            self._next_button.setEnabled(False)
            self._accept_button.setEnabled(False)
            self._reject_button.setEnabled(False)
            self._apply_button.setEnabled(False)
            self._final_accept.setEnabled(True)
            return
        self._prev_button.setEnabled(self._current_diff > 0)
        self._next_button.setEnabled(self._current_diff < len(self._diffs) - 1)
        resolved = sum(1 for decision in self._decisions if decision is not None)
        self._status_label.setText(f"Change {self._current_diff + 1} of {len(self._diffs)} (resolved {resolved})")
        self._final_accept.setEnabled(all(decision is not None for decision in self._decisions))

    def _highlight_current_diff(self) -> None:
        self._clear_highlights(self._left_edit)
        self._clear_highlights(self._right_edit)
        if not self._diffs or self._current_diff < 0:
            return
        tag, i1, i2, j1, j2 = self._diffs[self._current_diff]
        left_range = self._merged_ranges.get(self._current_diff, (i1, i2))
        self._highlight_range(self._left_edit, left_range[0], left_range[1], QColor("#f6d365"))
        self._highlight_range(self._right_edit, j1, j2, QColor("#a8dadc"))

    def _highlight_range(self, editor: QPlainTextEdit, start: int, end: int, color: QColor) -> None:
        doc = editor.document()
        if start < 0:
            return
        start = min(start, doc.blockCount() - 1)
        end = min(end, doc.blockCount())
        if end <= start:
            end = start + 1
        cursor = QTextCursor(doc.findBlockByNumber(start))
        cursor.setPosition(cursor.position())
        end_block = doc.findBlockByNumber(end - 1)
        if not end_block.isValid():
            return
        cursor.setPosition(end_block.position() + end_block.length() - 1, QTextCursor.KeepAnchor)
        fmt = QTextCharFormat()
        fmt.setBackground(color)
        fmt.setForeground(QColor("#111111"))
        selection = QTextEdit.ExtraSelection()
        selection.cursor = cursor
        selection.format = fmt
        editor.setExtraSelections([selection])

    def _clear_highlights(self, editor: QPlainTextEdit) -> None:
        editor.setExtraSelections([])

    def _previous_diff(self) -> None:
        if self._current_diff > 0:
            self._current_diff -= 1
            self._update_navigation_state()
            self._highlight_current_diff()

    def _next_diff(self) -> None:
        if self._current_diff < len(self._diffs) - 1:
            self._current_diff += 1
            self._update_navigation_state()
            self._highlight_current_diff()

    def _accept_current_change(self) -> None:
        if not self._diffs or self._current_diff < 0:
            return
        self._decisions[self._current_diff] = "remote"
        self._refresh_views()

    def _reject_current_change(self) -> None:
        if not self._diffs or self._current_diff < 0:
            return
        self._decisions[self._current_diff] = "local"
        self._refresh_views()

    def _accept_both_change(self) -> None:
        if not self._diffs or self._current_diff < 0:
            return
        self._decisions[self._current_diff] = "both"
        self._refresh_views()

    def _accept_merge(self) -> None:
        self.accept()
