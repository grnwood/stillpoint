from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QEvent, QItemSelectionModel, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QFontMetrics, QIcon, QKeySequence, QPalette, QShortcut, QTextCursor, QTextFormat
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStyle,
    QStyledItemDelegate,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from sp.app import git_support
from .canvas_navigation import install_primary_wheel_zoom
from .utility_header import UtilityPanelHeader


_WINDOWS = sys.platform == "win32"


class CommitBeforePushDialog(QDialog):
    """Offer a small, explicit stage-and-commit step before pushing."""

    def __init__(self, unstaged_paths: list[str], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Commit Before Push")
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignTop)
        count = len(unstaged_paths)
        explanation = QLabel(
            f"{count} unstaged file{'s' if count != 1 else ''} will not be included in a push "
            "unless they are staged and committed first."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        files = QLabel("\n".join(f"• {path}" for path in unstaged_paths))
        files.setObjectName("commitBeforePushFileList")
        files.setTextInteractionFlags(Qt.TextSelectableByMouse)
        files.setWordWrap(False)
        files.setTextFormat(Qt.PlainText)
        files.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        files.setStyleSheet("padding: 6px; color: palette(text); background: palette(base);")
        file_scroll = QScrollArea()
        file_scroll.setWidget(files)
        file_scroll.setWidgetResizable(True)
        file_scroll.setFixedHeight(min(
            220,
            files.sizeHint().height() + 2 * file_scroll.frameWidth()
            + file_scroll.horizontalScrollBar().sizeHint().height(),
        ))
        layout.addWidget(file_scroll)
        self.stage_checkbox = QCheckBox(
            f"Automatically stage these {count} file{'s' if count != 1 else ''} and commit before pushing"
        )
        self.stage_checkbox.setChecked(True)
        layout.addWidget(self.stage_checkbox)
        self.message_edit = QLineEdit()
        self.message_edit.setPlaceholderText("Commit message")
        layout.addWidget(self.message_edit)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        push_button = self.buttons.button(QDialogButtonBox.Ok)
        push_button.setText("Commit and Push")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.stage_checkbox.toggled.connect(self._sync_state)
        self.message_edit.textChanged.connect(self._sync_state)
        self._sync_state()
        self.message_edit.setFocus()
        self.resize(920, self.sizeHint().height())

    def _sync_state(self) -> None:
        staging = self.stage_checkbox.isChecked()
        self.message_edit.setEnabled(staging)
        push_button = self.buttons.button(QDialogButtonBox.Ok)
        push_button.setText("Commit and Push" if staging else "Push Existing Commits")
        push_button.setEnabled(not staging or bool(self.message_edit.text().strip()))

    def should_stage(self) -> bool:
        return self.stage_checkbox.isChecked()

    def commit_message(self) -> str:
        return self.message_edit.text().strip()


def git_status_info(code: str) -> tuple[str, str, str]:
    """Return the compact badge, name, and explanation for a Git status."""
    return {
        "M": ("MOD", "Modified", "Content differs from the latest saved version."),
        "A": ("ADD", "Added", "Ready to be included in the next saved version."),
        "D": ("DEL", "Deleted", "Will be removed in the next saved version."),
        "R": ("REN", "Renamed", "Moved or renamed since the latest saved version."),
        "C": ("CPY", "Copied", "Copied since the latest saved version."),
        "T": ("TYP", "Type changed", "The file type or storage mode changed."),
        "U": ("CNF", "Conflict", "Conflicting changes must be resolved before saving."),
        "?": ("NEW", "New", "Not yet included in Git history."),
    }.get(code, ("CHG", "Changed", "Changed since the latest saved version."))


class GitPathLabel(QLabel):
    """Single-line path label that keeps the filename visible when narrow."""

    def __init__(self, path: str, parent=None) -> None:
        super().__init__(parent)
        self._full_path = str(path)
        self.setMinimumWidth(0)
        self.setWordWrap(False)
        self.setToolTip(self._full_path)
        self._update_elided_text()

    def fullPath(self) -> str:
        return self._full_path

    def _elided_path(self, width: int) -> str:
        metrics = self.fontMetrics()
        if width <= 0 or metrics.horizontalAdvance(self._full_path) <= width:
            return self._full_path
        if "  →  " in self._full_path:
            return metrics.elidedText(
                self._full_path, Qt.TextElideMode.ElideMiddle, width
            )
        normalized = self._full_path.replace("\\", "/")
        parts = normalized.split("/")
        for count in range(len(parts), 0, -1):
            candidate = "…/" + "/".join(parts[-count:])
            if metrics.horizontalAdvance(candidate) <= width:
                return candidate
        prefix = "…/"
        filename_width = max(0, width - metrics.horizontalAdvance(prefix))
        return prefix + metrics.elidedText(
            parts[-1], Qt.TextElideMode.ElideMiddle, filename_width
        )

    def _update_elided_text(self) -> None:
        QLabel.setText(self, self._elided_path(self.contentsRect().width()))

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._update_elided_text()


def git_change_row(
    path: str,
    code: str,
    *,
    original_path: str | None = None,
    trailing_margin: int = 34,
) -> QWidget:
    """Build the shared status-badge and responsive-path row."""
    abbreviation, name, explanation = git_status_info(code)
    row = QWidget()
    row.setObjectName("vaultGitChangeRow")
    row.setAttribute(Qt.WA_StyledBackground, True)
    row.setStyleSheet("QWidget#vaultGitChangeRow { background: transparent; }")
    row_layout = QHBoxLayout(row)
    row_layout.setContentsMargins(
        10 if _WINDOWS else 8,
        7 if _WINDOWS else 5,
        trailing_margin,
        7 if _WINDOWS else 5,
    )
    row_layout.setSpacing(9 if _WINDOWS else 8)

    status = QLabel(abbreviation)
    status.setObjectName("vaultGitStatusCode")
    status.setFixedWidth(42 if _WINDOWS else 38)
    status.setAlignment(Qt.AlignCenter)
    status.setToolTip(f"{name} — {explanation}")
    status.setAccessibleName(f"Git status: {name}")
    color = {
        "CNF": "#c62828",
        "DEL": "#c62828",
        "NEW": "#2e7d32",
        "ADD": "#2e7d32",
        "MOD": "#ad6800",
    }.get(abbreviation, "#546e7a")
    status.setStyleSheet(
        "font-family: monospace; font-weight: 700; padding: 2px 3px; "
        f"border-radius: 3px; color: {color}; background: transparent;"
    )
    row_layout.addWidget(status, 0)

    display_path = str(path)
    if original_path and code == "R":
        display_path = f"{original_path}  →  {path}"
    path_label = GitPathLabel(display_path)
    path_label.setObjectName("vaultGitFilePath")
    path_label.setAccessibleName(f"Changed file: {path}")
    if _WINDOWS:
        path_font = path_label.font()
        path_font.setPointSize(max(10, path_font.pointSize()))
        path_label.setFont(path_font)
    row_layout.addWidget(path_label, 1)
    return row


def git_section_row(title: str, count: int) -> QWidget:
    """Build the shared compact heading for a Git status group."""
    row = QWidget()
    row.setObjectName("vaultGitSectionRow")
    layout = QHBoxLayout(row)
    layout.setContentsMargins(
        10 if _WINDOWS else 8,
        8 if _WINDOWS else 7,
        8,
        6 if _WINDOWS else 4,
    )
    label = QLabel(f"{title.upper()}  ·  {count}")
    label.setObjectName("vaultGitSectionLabel")
    label.setStyleSheet(
        f"font-size: {'9pt' if _WINDOWS else '10px'}; "
        "font-weight: 600; color: palette(text);"
    )
    layout.addWidget(label)
    layout.addStretch(1)
    return row


class VaultGitStatusList(QListWidget):
    """Multi-select status list with a compact reset action on hover."""

    resetRequested = Signal(list)
    previewRequested = Signal(str, bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.setStyleSheet(
            "QListWidget::item { border-bottom: 1px solid palette(midlight); padding: 0; }"
            "QListWidget::item:selected { background: palette(highlight); }"
        )
        self._pointer_item: Optional[QListWidgetItem] = None
        self._hover_item: Optional[QListWidgetItem] = None
        self.reset_button = QToolButton(self.viewport())
        self.reset_button.setObjectName("vaultGitResetButton")
        self.reset_button.setAutoRaise(False)
        self.reset_button.setFixedSize(24, 22)
        self.reset_button.setIconSize(QSize(17, 17))
        reset_icon = Path(__file__).resolve().parents[2] / "assets" / "reset-file.svg"
        self.reset_button.setIcon(QIcon(str(reset_icon)))
        self.reset_button.setToolTip("Reset this change")
        self.reset_button.setAccessibleName("Reset change")
        self.reset_button.clicked.connect(self._request_reset)
        self.reset_button.hide()
        self.itemSelectionChanged.connect(self._selection_changed)
        self.itemClicked.connect(lambda item: self._request_preview(item, False))
        self.itemDoubleClicked.connect(lambda item: self._request_preview(item, True))
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_reset_menu)

    @staticmethod
    def _item_path(item: Optional[QListWidgetItem]) -> str:
        return str(item.data(Qt.UserRole) or "") if item is not None else ""

    @staticmethod
    def _is_untracked(item: Optional[QListWidgetItem]) -> bool:
        return bool(item is not None and item.data(Qt.UserRole + 1) == "?")

    def selected_file_items(self) -> list[QListWidgetItem]:
        return sorted(
            [item for item in self.selectedItems() if self._item_path(item)],
            key=self.row,
        )

    def _reset_items(self) -> list[QListWidgetItem]:
        selected = self.selected_file_items()
        if len(selected) > 1:
            return selected
        return [self._hover_item] if self._item_path(self._hover_item) else []

    def _request_reset(self) -> None:
        paths = [self._item_path(item) for item in self._reset_items()]
        if paths:
            self.resetRequested.emit(list(dict.fromkeys(paths)))

    def _show_reset_menu(self, position) -> None:
        item = self.itemAt(position)
        if not self._item_path(item):
            return
        if item not in self.selectedItems():
            self.clearSelection()
            item.setSelected(True)
            self.setCurrentItem(item)
        self._hover_item = item
        reset_items = self._reset_items()
        if not reset_items:
            return
        menu = QMenu(self)
        all_new = all(self._is_untracked(selected) for selected in reset_items)
        label = (
            "Remove New File"
            if len(reset_items) == 1 and all_new
            else "Revert Change" if len(reset_items) == 1 else f"Reset {len(reset_items)} Changes"
        )
        action = menu.addAction(label)
        action.setToolTip(
            "Move the new file to Trash"
            if all_new
            else "Restore tracked files to the latest saved Git version"
        )
        action.triggered.connect(self._request_reset)
        menu.exec(self.viewport().mapToGlobal(position))

    def _request_preview(self, item: Optional[QListWidgetItem], focus_diff: bool) -> None:
        path = self._item_path(item)
        if path:
            self.previewRequested.emit(path, bool(focus_diff))

    def _move_file_selection(self, direction: int) -> None:
        row = self.currentRow()
        if row < 0:
            row = -1 if direction > 0 else self.count()
        candidate = row + direction
        while 0 <= candidate < self.count():
            item = self.item(candidate)
            if self._item_path(item):
                self.setCurrentItem(item)
                self._request_preview(item, False)
                return
            candidate += direction

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        modifiers = event.modifiers() & ~Qt.KeypadModifier
        if modifiers == Qt.NoModifier and event.key() in {
            Qt.Key_Down,
            Qt.Key_Up,
            Qt.Key_J,
            Qt.Key_K,
        }:
            self._move_file_selection(
                1 if event.key() in {Qt.Key_Down, Qt.Key_J} else -1
            )
            event.accept()
            return
        if event.key() in {Qt.Key_Return, Qt.Key_Enter} and modifiers in {
            Qt.NoModifier,
            Qt.ShiftModifier,
        }:
            self._request_preview(
                self.currentItem(),
                modifiers == Qt.ShiftModifier,
            )
            event.accept()
            return
        super().keyPressEvent(event)

    def _show_action_for_item(self, item: Optional[QListWidgetItem]) -> None:
        self._hover_item = item
        reset_items = self._reset_items()
        if not reset_items:
            self.reset_button.hide()
            return
        anchor = reset_items[0] if len(self.selected_file_items()) > 1 else item
        if anchor is None:
            self.reset_button.hide()
            return
        count = len(reset_items)
        all_new = all(self._is_untracked(selected) for selected in reset_items)
        if count == 1:
            tooltip = (
                "Remove new file (move to Trash)"
                if all_new
                else "Revert change to the latest saved version"
            )
        else:
            tooltip = (
                f"Remove {count} new files (move to Trash)"
                if all_new
                else f"Reset {count} selected changes"
            )
        self.reset_button.setToolTip(tooltip)
        rect = self.visualItemRect(anchor)
        self.reset_button.move(
            self.viewport().width() - self.reset_button.width() - 5,
            rect.center().y() - self.reset_button.height() // 2,
        )
        self.reset_button.show()
        self.reset_button.raise_()

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        item = self.itemAt(event.pos())
        if item is not self._pointer_item:
            self._pointer_item = item
            self._show_action_for_item(item)
        super().mouseMoveEvent(event)

    def _selection_changed(self) -> None:
        if self._pointer_item is not None:
            self._show_action_for_item(self._pointer_item)

    def leaveEvent(self, event) -> None:  # type: ignore[override]
        self.reset_button.hide()
        self._pointer_item = None
        self._hover_item = None
        super().leaveEvent(event)

    def scrollContentsBy(self, dx: int, dy: int) -> None:  # type: ignore[override]
        self.reset_button.hide()
        super().scrollContentsBy(dx, dy)

    def clear(self) -> None:  # type: ignore[override]
        self.reset_button.hide()
        self._pointer_item = None
        self._hover_item = None
        super().clear()


class GitLogDelegate(QStyledItemDelegate):
    """Compact commit rows with a graph rail and terminal-style metadata."""

    def sizeHint(self, option, index):
        return QSize(0, QFontMetrics(option.font).height() * 2 + 20)

    def paint(self, painter, option, index):
        entry = index.data(Qt.UserRole)
        if entry is None:
            return
        painter.save()
        painter.setClipRect(option.rect)
        rect = option.rect
        palette = option.palette
        dark = palette.color(QPalette.Base).lightness() < 128
        accent = QColor("#63d8c4" if dark else "#087f72")
        foreground = palette.color(QPalette.Text)
        muted = QColor(foreground)
        muted.setAlpha(165)
        divider = QColor(foreground)
        divider.setAlpha(48)
        if option.state & (QStyle.State_Selected | QStyle.State_MouseOver):
            background = QColor(accent)
            background.setAlpha(38 if option.state & QStyle.State_Selected else 18)
            painter.fillRect(rect, background)
        rail_x = rect.left() + 13
        painter.setPen(divider)
        painter.drawLine(rail_x, rect.top(), rail_x, rect.bottom())
        painter.drawLine(rect.left() + 28, rect.bottom(), rect.right() - 8, rect.bottom())
        painter.setRenderHint(painter.RenderHint.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(accent)
        painter.drawEllipse(rail_x - 3, rect.top() + 15, 6, 6)

        subject_font = QFont(option.font)
        subject_font.setWeight(QFont.Medium)
        painter.setFont(subject_font)
        metrics = QFontMetrics(subject_font)
        left, width = rect.left() + 28, max(0, rect.width() - 40)
        line_height = metrics.height()
        painter.setPen(foreground)
        painter.drawText(QRect(left, rect.top() + 8, width, line_height), Qt.AlignVCenter,
                         metrics.elidedText(entry.subject, Qt.ElideRight, width))
        mono = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        mono.setPointSizeF(max(8, option.font.pointSizeF() - 1))
        painter.setFont(mono)
        metadata_metrics = QFontMetrics(mono)
        hash_width = metadata_metrics.horizontalAdvance(entry.commit[:8] + "  ")
        metadata_top = rect.top() + 10 + line_height
        painter.setPen(accent)
        painter.drawText(QRect(left, metadata_top, hash_width, line_height), Qt.AlignVCenter, entry.commit[:8])
        painter.setPen(muted)
        metadata = f"{entry.committed_at[:10]} · {entry.author}"
        painter.drawText(QRect(left + hash_width, metadata_top, max(0, width - hash_width), line_height),
                         Qt.AlignVCenter, metadata_metrics.elidedText(metadata, Qt.ElideRight, max(0, width - hash_width)))
        painter.restore()


class GitLogPanel(QWidget):
    commitActivated = Signal(object, str)

    def __init__(self, root, parent=None):
        super().__init__(parent)
        self.root = Path(root)
        self.relative_path = ""
        self._scoped_path = ""
        self._loaded = False
        self._activation_commit = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        header = QHBoxLayout()
        header.setContentsMargins(10, 7, 8, 7)
        self.scope_label = QLabel("History · Entire vault")
        self.scope_label.setTextFormat(Qt.PlainText)
        self.scope_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        header.addWidget(self.scope_label, 1)
        self.all_button = QToolButton()
        self.all_button.setText("All")
        self.all_button.setCheckable(True)
        self.all_button.setToolTip("Show history for the entire vault")
        self.all_button.clicked.connect(self._toggle_all)
        header.addWidget(self.all_button)
        refresh = QToolButton()
        refresh.setText("↻")
        refresh.setToolTip("Refresh history")
        refresh.clicked.connect(self.refresh)
        header.addWidget(refresh)
        layout.addLayout(header)
        self.entries = QListWidget()
        self.entries.setWordWrap(False)
        self.entries.setMinimumHeight(120)
        self.entries.setTextElideMode(Qt.ElideRight)
        self.entries.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.entries.setMouseTracking(True)
        self.entries.setUniformItemSizes(True)
        self.entries.setItemDelegate(GitLogDelegate(self.entries))
        self.entries.setStyleSheet("QListWidget { border: none; outline: 0; }")
        self.entries.currentItemChanged.connect(self._activate)
        self.entries.itemClicked.connect(lambda item: self._activate(item, None))
        layout.addWidget(self.entries)
        self.message = QLabel("Right-click and choose history to load commits.")
        self.message.setWordWrap(True)
        self.message.setContentsMargins(10, 7, 10, 7)
        layout.addWidget(self.message)

    def show_history(self, relative_path=""):
        self.relative_path = str(relative_path).strip("/")
        self._scoped_path = self.relative_path
        self._loaded = True
        self.refresh()

    def _toggle_all(self, checked):
        self.relative_path = "" if checked else self._scoped_path
        self._loaded = True
        self.refresh()

    def refresh(self):
        if not self._loaded:
            return
        self.all_button.setChecked(not self.relative_path)
        self.all_button.setEnabled(bool(self._scoped_path))
        self.all_button.setToolTip(
            f"Return to history for {self._scoped_path}"
            if not self.relative_path and self._scoped_path else "Show history for the entire vault"
        )
        self.scope_label.setText("History · " + (Path(self.relative_path).name if self.relative_path else "Entire vault"))
        self.scope_label.setToolTip(self.relative_path or str(self.root))
        selected = self.entries.currentItem()
        selected_commit = selected.data(Qt.UserRole).commit if selected is not None else None
        self.entries.blockSignals(True)
        self.entries.clear()
        try:
            commits = git_support.recent_commits(self.root, self.relative_path)
            for entry in commits:
                item = QListWidgetItem(f"{entry.subject}\n{entry.commit[:8]} · {entry.committed_at[:16].replace('T', ' ')} · {entry.author}")
                item.setData(Qt.UserRole, entry)
                item.setToolTip(f"{entry.subject}\n{entry.author} · {entry.committed_at}\n{entry.commit}")
                self.entries.addItem(item)
                if entry.commit == selected_commit:
                    self.entries.setCurrentItem(item)
            self.message.setText(f"{len(commits)} recent commits · Select a commit to inspect its files." if commits else "No commits for this location yet.")
        except git_support.GitError as exc:
            self.message.setText(str(exc))
        finally:
            self.entries.blockSignals(False)

    def _activate(self, item, _previous):
        if item is not None:
            entry = item.data(Qt.UserRole)
            # Selection and click fire together for a newly selected row.
            # A later click on that same row should still reopen its preview.
            if self._activation_commit == entry.commit:
                return
            self._activation_commit = entry.commit
            QTimer.singleShot(0, self, lambda: setattr(self, "_activation_commit", None))
            self.commitActivated.emit(entry, self.relative_path)


class GitCommitViewer(QWidget):
    """Commit files and a visual patch hosted in the normal editor area."""

    def __init__(self, root, entry, relative_path="", parent=None):
        super().__init__(parent)
        self.root, self.entry = root, entry
        layout = QVBoxLayout(self)
        title = QLabel(f"{entry.subject}\n{entry.author} · {entry.committed_at} · {entry.commit[:10]}")
        title.setTextFormat(Qt.PlainText)
        title.setTextInteractionFlags(Qt.TextSelectableByMouse)
        title.setWordWrap(True)
        layout.addWidget(title)
        self.files = QComboBox()
        self.files.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        layout.addWidget(self.files)
        self.diff_layout = QVBoxLayout()
        layout.addLayout(self.diff_layout, 1)
        self.viewer = None
        self._changes = []
        try:
            self._changes = git_support.commit_files(root, entry.commit)
            for file in self._changes:
                path = f"{file.original_path} → {file.path}" if file.original_path else file.path
                counts = "binary" if file.additions is None else f"+{file.additions} / −{file.deletions}"
                self.files.addItem(f"{git_status_info(file.status)[1]} · {path} · {counts}")
                self.files.setItemData(self.files.count() - 1, path, Qt.ToolTipRole)
            preferred = next((i for i, file in enumerate(self._changes)
                              if file.path == relative_path or file.path.startswith(relative_path + "/")), 0)
            self.files.setCurrentIndex(preferred)
            self.files.currentIndexChanged.connect(self._show_file)
            if self._changes:
                self._show_file(preferred)
            else:
                self.diff_layout.addWidget(QLabel("This commit has no file changes."))
        except git_support.GitError as exc:
            self.diff_layout.addWidget(QLabel(str(exc)))

    def _show_file(self, index):
        file = self._changes[index]
        try:
            paths = [file.original_path, file.path] if file.original_path else [file.path]
            patch = git_support.commit_diff(self.root, self.entry.commit, paths)
        except git_support.GitError as exc:
            patch = str(exc)
        if self.viewer is not None:
            self.diff_layout.removeWidget(self.viewer)
            self.viewer.deleteLater()
        self.viewer = GitDiffEditor(Path(file.path).name, patch, binary=file.additions is None,
                                    comparison_text="Changes made in this commit")
        self.diff_layout.addWidget(self.viewer)


class VaultGitPanel(QWidget):
    """A compact, vault-focused Git rail using non-developer language."""

    getUpdatesRequested = Signal()
    saveChangesRequested = Signal()
    publishRequested = Signal()
    changeActivated = Signal(str)
    resetRequested = Signal(list)
    statusChanged = Signal(object, object)
    changePreviewRequested = Signal(str, bool)

    def __init__(self, vault_root: str | Path, parent=None) -> None:
        super().__init__(parent)
        self.vault_root = Path(vault_root).resolve()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.header = UtilityPanelHeader("Git")
        self.header.set_detail(self.vault_root.name, str(self.vault_root))
        self.refresh_button = QToolButton(self.header)
        self.refresh_button.setText("↻")
        self.refresh_button.setToolTip("Refresh changes")
        self.refresh_button.clicked.connect(self.refresh)
        self.get_button = QToolButton(self.header)
        self.get_button.setText("↓")
        self.get_button.setToolTip("Get updates (Git pull)")
        self.get_button.clicked.connect(self.getUpdatesRequested)
        self.save_button = QToolButton(self.header)
        self.save_button.setText("●")
        self.save_button.setToolTip("Save changes (Git commit)")
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self.saveChangesRequested)
        self.publish_button = QToolButton(self.header)
        self.publish_button.setText("↑")
        self.publish_button.setToolTip("Publish changes (Git push)")
        self.publish_button.setEnabled(False)
        self.publish_button.clicked.connect(self.publishRequested)
        for button in (self.refresh_button, self.get_button, self.save_button, self.publish_button):
            button.setAutoRaise(True)
            self.header.add_trailing_widget(button)
        layout.addWidget(self.header)

        self.summary = QLabel("Loading repository status…")
        self.summary.setWordWrap(True)
        self.summary.setContentsMargins(8, 7, 8, 7)
        layout.addWidget(self.summary)
        self.changes = VaultGitStatusList()
        self.changes.setObjectName("vaultGitChanges")
        self.changes.setWordWrap(False)
        self.changes.resetRequested.connect(self.resetRequested)
        self.changes.previewRequested.connect(self.changePreviewRequested)
        self.log_panel = GitLogPanel(self.vault_root)
        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(self.changes)
        splitter.addWidget(self.log_panel)
        splitter.setSizes([300, 300])
        layout.addWidget(splitter, 1)
        self.changes.setContextMenuPolicy(Qt.CustomContextMenu)
        self.changes.customContextMenuRequested.connect(self._history_menu)
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(10000)
        self.refresh_timer.timeout.connect(self.refresh)
        self.refresh_timer.start()
        self.refresh()

    def _history_menu(self, point):
        menu = QMenu(self)
        path = self.changes._item_path(self.changes.itemAt(point)) or ""
        menu.addAction("Show History", lambda: self.log_panel.show_history(path))
        menu.exec(self.changes.viewport().mapToGlobal(point))

    @staticmethod
    def _status_info(code: str) -> tuple[str, str, str]:
        return git_status_info(code)

    @classmethod
    def _change_row(cls, entry: git_support.GitStatusEntry, code: str) -> QWidget:
        return git_change_row(
            entry.path,
            code,
            original_path=entry.original_path,
        )

    @staticmethod
    def _section_row(title: str, count: int) -> QWidget:
        return git_section_row(title, count)

    def refresh(self) -> None:
        self.log_panel.refresh()
        current_path = self.changes._item_path(self.changes.currentItem())
        selected_paths = {
            self.changes._item_path(item) for item in self.changes.selected_file_items()
        }
        self.changes.clear()
        try:
            state = git_support.repository_state(self.vault_root)
            entries = git_support.status(self.vault_root)
        except git_support.GitError as exc:
            self._update_action_state(None, None)
            self.summary.setText(f"Repository needs attention: {exc}")
            self.statusChanged.emit(None, str(exc))
            return
        self._update_action_state(state, entries)
        self.statusChanged.emit(state, entries)
        details = [state.branch or "No commits yet"]
        if state.ahead:
            details.append(f"{state.ahead} to publish")
        if state.behind:
            details.append(f"{state.behind} update{'s' if state.behind != 1 else ''} available")
        details.append(
            "No changes" if not entries else f"{len({entry.path for entry in entries})} changed file{'s' if len({entry.path for entry in entries}) != 1 else ''}"
        )
        self.summary.setText(" · ".join(details))

        groups = (
            ("Conflicts", [entry for entry in entries if entry.conflicted]),
            ("Saved for next commit", [entry for entry in entries if not entry.conflicted and entry.index not in {" ", "?"}]),
            ("Changes", [entry for entry in entries if not entry.conflicted and entry.worktree not in {" ", "?"}]),
            ("New files", [entry for entry in entries if entry.index == "?"]),
        )
        for title, group in groups:
            if not group:
                continue
            heading = QListWidgetItem()
            heading.setFlags(Qt.NoItemFlags)
            heading.setSizeHint(QSize(0, 30))
            self.changes.addItem(heading)
            self.changes.setItemWidget(heading, self._section_row(title, len(group)))
            for entry in group:
                code = "U" if entry.conflicted else (entry.worktree if entry.worktree not in {" ", "?"} else entry.index)
                if entry.index == "?":
                    code = "?"
                item = QListWidgetItem()
                item.setData(Qt.UserRole, entry.path)
                item.setData(Qt.UserRole + 1, entry.index)
                item.setToolTip(entry.path)
                item.setSizeHint(QSize(0, 34))
                self.changes.addItem(item)
                self.changes.setItemWidget(item, self._change_row(entry, code))
                if entry.path in selected_paths:
                    item.setSelected(True)
                if entry.path == current_path:
                    self.changes.setCurrentItem(
                        item, QItemSelectionModel.SelectionFlag.NoUpdate
                    )

    def _update_action_state(
        self,
        state: git_support.GitRepositoryState | None,
        entries: list[git_support.GitStatusEntry] | None,
    ) -> None:
        has_changes = bool(entries) if entries is not None else False
        unstaged = git_support.unstaged_paths(entries or [])
        can_offer_commit = (
            0 < len(unstaged) <= 10
            and not any(entry.conflicted for entry in (entries or []))
        )
        has_unpushed_commits = bool(state is not None and state.ahead > 0)
        self.save_button.setEnabled(has_changes)
        self.publish_button.setEnabled(has_unpushed_commits or can_offer_commit)
        self.save_button.setToolTip(
            "Save changes (Git commit)" if has_changes else "No changes to commit"
        )
        self.publish_button.setToolTip(
            "Publish changes (Git push)"
            if has_unpushed_commits
            else (
                f"Commit and push {len(unstaged)} unstaged file{'s' if len(unstaged) != 1 else ''}"
                if can_offer_commit
                else "No commits to push"
            )
        )

class GitDiffEditor(QWidget):
    """A visual, side-by-side working-copy comparison for the editor pane."""

    _HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

    def __init__(
        self,
        title: str,
        diff_text: str,
        parent=None,
        *,
        binary: bool = False,
        comparison_text: str = "Working file compared with the latest saved version",
    ) -> None:
        super().__init__(parent)
        self.diff_text = diff_text or ""
        self.binary = bool(binary)
        self._hunk_rows: list[int] = []
        self._unified_hunk_lines: list[int] = []
        self._current_hunk = -1
        self._shortcuts: list[QShortcut] = []
        fixed_font = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        self._font_point_size = max(6, fixed_font.pointSize() if fixed_font.pointSize() > 0 else 12)
        layout = QVBoxLayout(self)

        header = QHBoxLayout()
        title_label = QLabel(title)
        title_label.setStyleSheet("font-weight: 600; font-size: 14px;")
        title_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        header.addWidget(title_label, 1)
        self.previous_change_button = QToolButton()
        self.previous_change_button.setText("↑")
        self.previous_change_button.setToolTip("Previous change (Up or Ctrl/Shift+K)")
        self.previous_change_button.clicked.connect(lambda: self.move_change(-1))
        self.change_count_label = QLabel("No line changes")
        self.change_count_label.setObjectName("gitDiffChangeCount")
        self.next_change_button = QToolButton()
        self.next_change_button.setText("↓")
        self.next_change_button.setToolTip("Next change (Down or Ctrl/Shift+J)")
        self.next_change_button.clicked.connect(lambda: self.move_change(1))
        header.addWidget(self.previous_change_button)
        header.addWidget(self.change_count_label)
        header.addWidget(self.next_change_button)
        self.view_combo = QComboBox()
        self.view_combo.addItem("Side by side")
        self.view_combo.addItem("Unified patch")
        self.view_combo.setToolTip("Choose how to view this comparison")
        header.addWidget(self.view_combo)
        layout.addLayout(header)

        additions, deletions = self._change_counts(self.diff_text)
        summary = QHBoxLayout()
        comparison = QLabel(comparison_text)
        comparison.setStyleSheet("color: palette(text);")
        summary.addWidget(comparison)
        summary.addStretch(1)
        removed_label = QLabel(f"− {deletions}")
        removed_label.setToolTip("Removed lines")
        removed_label.setStyleSheet(
            "padding: 2px 7px; border-radius: 8px; color: #c62828; background: rgba(198,40,40,0.12);"
        )
        added_label = QLabel(f"+ {additions}")
        added_label.setToolTip("Added lines")
        added_label.setStyleSheet(
            "padding: 2px 7px; border-radius: 8px; color: #2e7d32; background: rgba(46,125,50,0.12);"
        )
        summary.addWidget(removed_label)
        summary.addWidget(added_label)
        layout.addLayout(summary)

        self.views = QStackedWidget()
        self.side_by_side = self._build_side_by_side_view()
        self.unified = self._build_unified_view()
        self.views.addWidget(self.side_by_side)
        self.views.addWidget(self.unified)
        self.view_combo.currentIndexChanged.connect(self.views.setCurrentIndex)
        layout.addWidget(self.views, 1)

        self.side_by_side.installEventFilter(self)
        self.unified.installEventFilter(self)
        for sequence, direction in (
            ("Ctrl+J", 1),
            ("Shift+J", 1),
            ("Ctrl+K", -1),
            ("Shift+K", -1),
        ):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            shortcut.activated.connect(
                lambda selected=direction: self.move_change(selected)
            )
            self._shortcuts.append(shortcut)
        self._sync_change_navigation()
        install_primary_wheel_zoom(self, self.adjust_font_size)

    @staticmethod
    def _change_counts(diff_text: str) -> tuple[int, int]:
        additions = 0
        deletions = 0
        in_hunk = False
        for line in diff_text.splitlines():
            if line.startswith("@@"):
                in_hunk = True
                continue
            if not in_hunk:
                continue
            if line.startswith("+"):
                additions += 1
            elif line.startswith("-"):
                deletions += 1
        return additions, deletions

    @classmethod
    def side_by_side_rows(cls, diff_text: str) -> list[dict[str, object]]:
        """Convert a unified patch into aligned rows for the visual table."""
        lines = diff_text.splitlines()
        rows: list[dict[str, object]] = []
        old_line = 0
        new_line = 0
        in_hunk = False
        index = 0

        def append_change_block(removed: list[str], added: list[str]) -> None:
            nonlocal old_line, new_line
            for offset in range(max(len(removed), len(added))):
                old_text = removed[offset] if offset < len(removed) else None
                new_text = added[offset] if offset < len(added) else None
                kind = (
                    "replace"
                    if old_text is not None and new_text is not None
                    else ("remove" if old_text is not None else "add")
                )
                rows.append(
                    {
                        "kind": kind,
                        "old_line": old_line if old_text is not None else None,
                        "old_text": old_text or "",
                        "new_line": new_line if new_text is not None else None,
                        "new_text": new_text or "",
                    }
                )
                if old_text is not None:
                    old_line += 1
                if new_text is not None:
                    new_line += 1

        while index < len(lines):
            line = lines[index]
            match = cls._HUNK_RE.match(line)
            if match:
                old_line = int(match.group(1))
                old_count = int(match.group(2) or 1)
                new_line = int(match.group(3))
                new_count = int(match.group(4) or 1)
                in_hunk = True
                old_range = (
                    "new content"
                    if old_count == 0
                    else f"old lines {old_line}–{old_line + old_count - 1}"
                )
                new_range = (
                    "content removed"
                    if new_count == 0
                    else f"new lines {new_line}–{new_line + new_count - 1}"
                )
                rows.append({"kind": "hunk", "text": f"{old_range} → {new_range}"})
                index += 1
                continue
            if not in_hunk:
                index += 1
                continue
            if line.startswith("-"):
                removed: list[str] = []
                added: list[str] = []
                while index < len(lines) and lines[index].startswith("-"):
                    removed.append(lines[index][1:])
                    index += 1
                while index < len(lines) and lines[index].startswith("+"):
                    added.append(lines[index][1:])
                    index += 1
                append_change_block(removed, added)
                continue
            if line.startswith("+"):
                added = []
                while index < len(lines) and lines[index].startswith("+"):
                    added.append(lines[index][1:])
                    index += 1
                append_change_block([], added)
                continue
            if line.startswith(" "):
                text = line[1:]
                rows.append(
                    {
                        "kind": "context",
                        "old_line": old_line,
                        "old_text": text,
                        "new_line": new_line,
                        "new_text": text,
                    }
                )
                old_line += 1
                new_line += 1
            elif line.startswith("\\ No newline"):
                rows.append({"kind": "note", "text": "No newline at end of file"})
            index += 1
        return rows

    def _build_side_by_side_view(self) -> QWidget:
        rows = [] if self.binary else self.side_by_side_rows(self.diff_text)
        if not rows:
            message = QLabel(
                self.diff_text.strip()
                or "This file has no line-by-line changes to display."
            )
            message.setAlignment(Qt.AlignCenter)
            message.setWordWrap(True)
            message.setTextInteractionFlags(Qt.TextSelectableByMouse)
            message.setStyleSheet("padding: 30px; color: palette(text);")
            return message

        table = QTableWidget(len(rows), 4)
        table.setObjectName("gitVisualDiffTable")
        table.setHorizontalHeaderLabels(["Old", "Before", "New", "After"])
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        table.setSelectionBehavior(QAbstractItemView.SelectItems)
        table.setWordWrap(False)
        table.setAlternatingRowColors(False)
        table.verticalHeader().hide()
        table.setShowGrid(False)
        table.setFont(self._diff_font())
        table.setStyleSheet(
            "QTableWidget { background: palette(base); color: palette(text); }"
            "QHeaderView::section { background: palette(window); color: palette(text); "
            "padding: 4px; border: 0; border-bottom: 1px solid palette(mid); }"
        )
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        table.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
        table.verticalHeader().setDefaultSectionSize(self._diff_row_height())
        removed_bg = QColor(198, 40, 40, 45)
        added_bg = QColor(46, 125, 50, 45)
        hunk_bg = QColor(25, 118, 210, 45)
        muted = self.palette().color(QPalette.PlaceholderText)
        readable_text = self.palette().color(QPalette.Text)

        for row_index, row in enumerate(rows):
            kind = str(row.get("kind") or "")
            if kind in {"hunk", "note"}:
                text = str(row.get("text") or "")
                if kind == "hunk":
                    text = "Change section  ·  " + text
                item = QTableWidgetItem(text)
                item.setBackground(hunk_bg)
                item.setForeground(readable_text)
                table.setItem(row_index, 0, item)
                table.setSpan(row_index, 0, 1, 4)
                if kind == "hunk":
                    self._hunk_rows.append(row_index)
                continue
            values = (
                "" if row.get("old_line") is None else str(row["old_line"]),
                str(row.get("old_text") or ""),
                "" if row.get("new_line") is None else str(row["new_line"]),
                str(row.get("new_text") or ""),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column in {0, 2}:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    item.setForeground(muted)
                if kind in {"remove", "replace"} and column in {0, 1}:
                    item.setBackground(removed_bg)
                if kind in {"add", "replace"} and column in {2, 3}:
                    item.setBackground(added_bg)
                table.setItem(row_index, column, item)
        return table

    def _diff_font(self) -> QFont:
        font = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        font.setPointSize(self._font_point_size)
        return font

    def _diff_row_height(self) -> int:
        return max(22, QFontMetrics(self._diff_font()).height() + 8)

    def adjust_font_size(self, delta: int) -> None:
        new_size = max(6, min(28, self._font_point_size + int(delta)))
        if new_size == self._font_point_size:
            return
        self._font_point_size = new_size
        font = self._diff_font()
        if isinstance(self.side_by_side, QTableWidget):
            self.side_by_side.setFont(font)
            self.side_by_side.verticalHeader().setDefaultSectionSize(
                self._diff_row_height()
            )
        self.unified.setFont(font)

    def _sync_change_navigation(self) -> None:
        count = len(self._hunk_rows)
        enabled = count > 0 and isinstance(self.side_by_side, QTableWidget)
        self.previous_change_button.setEnabled(enabled)
        self.next_change_button.setEnabled(enabled)
        if not enabled:
            self.change_count_label.setText("No line changes")
            return
        self.show_change(0)

    def show_change(self, index: int) -> None:
        if not self._hunk_rows or not isinstance(self.side_by_side, QTableWidget):
            return
        self._current_hunk = index % len(self._hunk_rows)
        table = self.side_by_side
        table.clearSelection()
        row = self._hunk_rows[self._current_hunk]
        item = table.item(row, 0)
        if item is not None:
            table.setCurrentItem(item)
            table.scrollToItem(item, QAbstractItemView.PositionAtTop)
        if self._current_hunk < len(self._unified_hunk_lines):
            block = self.unified.document().findBlockByNumber(
                self._unified_hunk_lines[self._current_hunk]
            )
            if block.isValid():
                self.unified.setTextCursor(QTextCursor(block))
                self.unified.centerCursor()
        self.change_count_label.setText(
            f"Change {self._current_hunk + 1} of {len(self._hunk_rows)}"
        )

    def move_change(self, direction: int) -> None:
        if self._hunk_rows:
            self.show_change(self._current_hunk + (1 if direction > 0 else -1))

    def focus_diff(self) -> None:
        target = self.views.currentWidget()
        target.setFocus(Qt.ShortcutFocusReason)

    def eventFilter(self, watched, event) -> bool:  # type: ignore[override]
        if event.type() == QEvent.KeyPress:
            modifiers = event.modifiers() & ~Qt.KeypadModifier
            if modifiers == Qt.NoModifier and event.key() in {Qt.Key_Up, Qt.Key_Down}:
                self.move_change(1 if event.key() == Qt.Key_Down else -1)
                return True
        return super().eventFilter(watched, event)

    def _build_unified_view(self) -> QPlainTextEdit:
        viewer = QPlainTextEdit()
        viewer.setReadOnly(True)
        viewer.setLineWrapMode(QPlainTextEdit.NoWrap)
        viewer.setFont(self._diff_font())
        viewer.setPlainText(
            self.diff_text or "This item matches the latest saved version."
        )
        selections: list[QTextEdit.ExtraSelection] = []
        document = viewer.document()
        for line_number, line in enumerate(self.diff_text.splitlines()[:20_000]):
            color = None
            if line.startswith("+") and not line.startswith("+++"):
                color = QColor(46, 125, 50, 55)
            elif line.startswith("-") and not line.startswith("---"):
                color = QColor(198, 40, 40, 55)
            elif line.startswith("@@"):
                color = QColor(25, 118, 210, 55)
                self._unified_hunk_lines.append(line_number)
            if color is None:
                continue
            selection = QTextEdit.ExtraSelection()
            selection.cursor = QTextCursor(document.findBlockByNumber(line_number))
            selection.format.setBackground(color)
            selection.format.setProperty(QTextFormat.FullWidthSelection, True)
            selections.append(selection)
        viewer.setExtraSelections(selections)
        return viewer


class GitHistoryDialog(QDialog):
    restored = Signal(list)

    def __init__(self, vault_root: str | Path, relative_path: str, parent=None) -> None:
        super().__init__(parent)
        self.vault_root = Path(vault_root).resolve()
        self.relative_path = str(relative_path).replace("\\", "/").strip("/")
        self._entries: list[git_support.GitHistoryEntry] = []
        self.setWindowTitle(f"Versions · {self.relative_path or self.vault_root.name}")
        self.resize(1280, 800)
        self.setMinimumSize(880, 560)
        self._files: list[git_support.GitCommitFile] = []
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self.versions_only = QCheckBox("Named versions only")
        self.versions_only.setChecked(True)
        self.versions_only.toggled.connect(self.refresh)
        top.addWidget(self.versions_only)
        self.include_backups = QCheckBox("Include automatic backups")
        self.include_backups.setChecked(False)
        self.include_backups.setEnabled(False)
        self.include_backups.toggled.connect(self.refresh)
        self.versions_only.toggled.connect(
            lambda checked: self.include_backups.setEnabled(not checked)
        )
        top.addWidget(self.include_backups)
        top.addStretch(1)
        layout.addLayout(top)
        splitter = QSplitter()
        self.history_list = QListWidget()
        self.history_list.setWordWrap(False)
        self.history_list.setMinimumWidth(250)
        self.history_list.currentRowChanged.connect(self._show_selected)
        splitter.addWidget(self.history_list)
        details = QWidget()
        details_layout = QVBoxLayout(details)
        details_layout.setContentsMargins(0, 0, 0, 0)
        self.commit_details = QLabel()
        self.commit_details.setTextFormat(Qt.PlainText)
        self.commit_details.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.commit_details.setWordWrap(True)
        details_layout.addWidget(self.commit_details)
        self.file_summary = QLabel()
        details_layout.addWidget(self.file_summary)
        self.file_table = QTableWidget(0, 4)
        self.file_table.setHorizontalHeaderLabels(["Change", "File (from vault root)", "+ Lines", "− Lines"])
        self.file_table.setWordWrap(False)
        self.file_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.file_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.file_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.file_table.verticalHeader().hide()
        self.file_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.file_table.horizontalHeader().setStretchLastSection(False)
        self.file_table.currentCellChanged.connect(lambda *_: self._show_file_diff())
        self.comparison = QComboBox()
        self.comparison.addItems([
            "Changes in this commit",
            "Compare selected file with current working copy",
            "Compare original page/folder with current working copy",
        ])
        self.comparison.setItemText(2, f"Compare {self.relative_path or 'entire vault'} with current working copy")
        self.comparison.currentIndexChanged.connect(lambda *_: self._show_file_diff())
        details_layout.addWidget(self.comparison)
        self.diff_view = QTextEdit()
        self.diff_view.setReadOnly(True)
        self.diff_view.setLineWrapMode(QTextEdit.NoWrap)
        self.diff_view.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        file_splitter = QSplitter(Qt.Vertical)
        file_splitter.addWidget(self.file_table)
        file_splitter.addWidget(self.diff_view)
        file_splitter.setSizes([220, 400])
        details_layout.addWidget(file_splitter, 1)
        splitter.addWidget(details)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([320, 940])
        layout.addWidget(splitter, 1)

        layout.addWidget(GitPathLabel(
            f"Restore applies to: {self.relative_path or self.vault_root.name}", self
        ))
        action_row = QHBoxLayout()
        self.restore_button = QPushButton("Restore This Version")
        self.restore_button.clicked.connect(self._restore_selected)
        self.latest_button = QPushButton("Restore Latest Saved")
        self.latest_button.clicked.connect(self._restore_latest)
        action_row.addWidget(self.restore_button)
        action_row.addWidget(self.latest_button)
        action_row.addStretch(1)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        action_row.addWidget(close_button)
        layout.addLayout(action_row)
        self.refresh()

    def refresh(self) -> None:
        self.history_list.clear()
        self.diff_view.clear()
        self.file_table.setRowCount(0)
        self._files = []
        self.commit_details.clear()
        self.file_summary.clear()
        self.restore_button.setEnabled(False)
        try:
            self._entries = git_support.history(
                self.vault_root,
                self.relative_path,
                versions_only=self.versions_only.isChecked(),
                include_automatic_backups=self.include_backups.isChecked(),
            )
        except git_support.GitError as exc:
            self._entries = []
            self.diff_view.setPlainText(str(exc))
            return
        for entry in self._entries:
            title = entry.version_name or entry.subject or "Saved change"
            date = entry.committed_at[:16].replace("T", " ") if entry.committed_at else ""
            item = QListWidgetItem(f"{title}\n{date} · {entry.author}\n{entry.commit[:10]}")
            item.setToolTip(f"{entry.subject}\n{entry.committed_at}\n{entry.commit}")
            self.history_list.addItem(item)
        if self._entries:
            self.history_list.setCurrentRow(0)
        else:
            self.diff_view.setPlainText(
                "No named versions include this item. Turn off ‘Named versions only’ to see all saved changes."
            )

    def _selected_entry(self) -> Optional[git_support.GitHistoryEntry]:
        row = self.history_list.currentRow()
        return self._entries[row] if 0 <= row < len(self._entries) else None

    def _show_selected(self, _row: int) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        self.restore_button.setEnabled(True)
        self.commit_details.setText(
            f"{entry.subject}\n{entry.author} · {entry.committed_at}\n{entry.commit}"
        )
        self.file_table.blockSignals(True)
        self.file_table.setRowCount(0)
        self._files = []
        try:
            self._files = git_support.commit_files(self.vault_root, entry.commit)
        except git_support.GitError as exc:
            self.file_summary.setText("Could not load changed files.")
            self.diff_view.setPlainText(str(exc))
            self.file_table.blockSignals(False)
            return
        additions = sum(file.additions or 0 for file in self._files)
        deletions = sum(file.deletions or 0 for file in self._files)
        binary_count = sum(file.additions is None for file in self._files)
        count = len(self._files)
        summary = f"{count} changed file{'s' if count != 1 else ''} in this commit · +{additions} / −{deletions} lines"
        if binary_count:
            summary += f" · {binary_count} binary file{'s' if binary_count != 1 else ''}"
        self.file_summary.setText(summary)
        self.file_table.setRowCount(len(self._files))
        preferred_row = next((row for row, file in enumerate(self._files)
                              if file.path == self.relative_path
                              or file.path.startswith(self.relative_path + "/")), 0)
        for row, file in enumerate(self._files):
            path = f"{file.original_path} → {file.path}" if file.original_path else file.path
            values = [git_status_info(file.status)[1], path,
                      "Binary" if file.additions is None else str(file.additions),
                      "—" if file.deletions is None else str(file.deletions)]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(path)
                self.file_table.setItem(row, column, item)
        self.file_table.blockSignals(False)
        if self._files:
            self.file_table.setCurrentCell(preferred_row, 1)
        else:
            self.diff_view.setPlainText("This commit has no file changes.")
            if self.comparison.currentIndex() == 2:
                self._show_file_diff()

    def _show_file_diff(self) -> None:
        entry = self._selected_entry()
        row = self.file_table.currentRow()
        if entry is None:
            return
        if self.comparison.currentIndex() != 2 and not 0 <= row < len(self._files):
            return
        try:
            if self.comparison.currentIndex() == 2:
                diff = git_support.diff_from_revision(self.vault_root, entry.commit, self.relative_path)
                empty_message = "This page/folder matches the current working copy."
            elif self.comparison.currentIndex() == 0:
                file = self._files[row]
                paths = [file.original_path, file.path] if file.original_path else [file.path]
                diff = git_support.commit_diff(self.vault_root, entry.commit, paths)
                empty_message = "This file has no content changes in this commit."
            else:
                file = self._files[row]
                diff = git_support.diff_from_revision(self.vault_root, entry.commit, file.path)
                empty_message = "This file matches the current working copy."
        except git_support.GitError as exc:
            diff = str(exc)
            empty_message = ""
        self.diff_view.setPlainText(diff or empty_message)
        selections = []
        for number, line in enumerate((diff or "").splitlines()[:20_000]):
            color = None
            if line.startswith("+") and not line.startswith("+++"):
                color = QColor(46, 125, 50, 55)
            elif line.startswith("-") and not line.startswith("---"):
                color = QColor(198, 40, 40, 55)
            elif line.startswith("@@"):
                color = QColor(25, 118, 210, 55)
            if color is not None:
                selection = QTextEdit.ExtraSelection()
                selection.cursor = QTextCursor(self.diff_view.document().findBlockByNumber(number))
                selection.format.setBackground(color)
                selection.format.setProperty(QTextFormat.FullWidthSelection, True)
                selections.append(selection)
        self.diff_view.setExtraSelections(selections)

    def _restore_selected(self) -> None:
        entry = self._selected_entry()
        if entry is not None:
            self._restore(entry.commit, entry.version_name or entry.subject)

    def _restore_latest(self) -> None:
        self._restore("HEAD", "the latest saved version")

    def _restore(self, revision: str, label: str) -> None:
        detail = (
            f"Restore {self.relative_path} from {label}?\n\n"
            "The restored content will become an unsaved change. Files created after that version "
            "will not be deleted."
        )
        if QMessageBox.question(self, "Restore Version", detail) != QMessageBox.Yes:
            return
        try:
            restored = git_support.restore_path(self.vault_root, revision, self.relative_path)
        except git_support.GitError as exc:
            QMessageBox.warning(self, "Could Not Restore Version", str(exc))
            return
        self.restored.emit([str(path) for path in restored])
        QMessageBox.information(
            self,
            "Version Restored",
            f"Restored {len(restored)} file{'s' if len(restored) != 1 else ''}. Review and save the changes when ready.",
        )
        self._show_selected(self.history_list.currentRow())
