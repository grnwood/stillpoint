from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from sp.app import git_support
from .utility_header import UtilityPanelHeader


class VaultGitPanel(QWidget):
    """A compact, vault-focused Git rail using non-developer language."""

    getUpdatesRequested = Signal()
    saveChangesRequested = Signal()
    publishRequested = Signal()
    changeActivated = Signal(str)

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
        self.save_button.clicked.connect(self.saveChangesRequested)
        self.publish_button = QToolButton(self.header)
        self.publish_button.setText("↑")
        self.publish_button.setToolTip("Publish changes (Git push)")
        self.publish_button.clicked.connect(self.publishRequested)
        for button in (self.refresh_button, self.get_button, self.save_button, self.publish_button):
            button.setAutoRaise(True)
            self.header.add_trailing_widget(button)
        layout.addWidget(self.header)

        self.summary = QLabel("Loading repository status…")
        self.summary.setWordWrap(True)
        self.summary.setContentsMargins(8, 7, 8, 7)
        layout.addWidget(self.summary)
        self.changes = QListWidget()
        self.changes.setObjectName("vaultGitChanges")
        self.changes.setWordWrap(True)
        self.changes.itemActivated.connect(self._activate_item)
        self.changes.itemClicked.connect(self._activate_item)
        layout.addWidget(self.changes, 1)
        self.refresh()

    @staticmethod
    def _status_name(code: str) -> str:
        return {
            "M": "Modified",
            "A": "Added",
            "D": "Deleted",
            "R": "Renamed",
            "C": "Copied",
            "T": "Type changed",
            "U": "Conflict",
            "?": "New",
        }.get(code, "Changed")

    def refresh(self) -> None:
        self.changes.clear()
        try:
            state = git_support.repository_state(self.vault_root)
            entries = git_support.status(self.vault_root)
        except git_support.GitError as exc:
            self.summary.setText(f"Repository needs attention: {exc}")
            return
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
            heading = QListWidgetItem(f"{title} ({len(group)})")
            heading.setFlags(Qt.NoItemFlags)
            self.changes.addItem(heading)
            for entry in group:
                code = "U" if entry.conflicted else (entry.worktree if entry.worktree not in {" ", "?"} else entry.index)
                if entry.index == "?":
                    code = "?"
                item = QListWidgetItem(f"{self._status_name(code)}  {entry.path}")
                item.setData(Qt.UserRole, entry.path)
                item.setToolTip(entry.path)
                self.changes.addItem(item)

    def _activate_item(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.UserRole)
        if path:
            self.changeActivated.emit(str(path))


class GitDiffDialog(QDialog):
    def __init__(self, title: str, diff_text: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(900, 650)
        layout = QVBoxLayout(self)
        viewer = QTextEdit()
        viewer.setReadOnly(True)
        viewer.setLineWrapMode(QTextEdit.NoWrap)
        viewer.setPlainText(diff_text or "No line-by-line differences are available.")
        layout.addWidget(viewer, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class GitHistoryDialog(QDialog):
    restored = Signal(list)

    def __init__(self, vault_root: str | Path, relative_path: str, parent=None) -> None:
        super().__init__(parent)
        self.vault_root = Path(vault_root).resolve()
        self.relative_path = str(relative_path).replace("\\", "/").strip("/")
        self._entries: list[git_support.GitHistoryEntry] = []
        self.setWindowTitle(f"Versions · {self.relative_path or self.vault_root.name}")
        self.resize(980, 650)
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self.versions_only = QCheckBox("Named versions only")
        self.versions_only.setChecked(True)
        self.versions_only.toggled.connect(self.refresh)
        top.addWidget(self.versions_only)
        top.addStretch(1)
        layout.addLayout(top)
        splitter = QSplitter()
        self.history_list = QListWidget()
        self.history_list.currentRowChanged.connect(self._show_selected)
        splitter.addWidget(self.history_list)
        self.diff_view = QTextEdit()
        self.diff_view.setReadOnly(True)
        self.diff_view.setLineWrapMode(QTextEdit.NoWrap)
        splitter.addWidget(self.diff_view)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, 1)

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
        try:
            self._entries = git_support.history(
                self.vault_root,
                self.relative_path,
                versions_only=self.versions_only.isChecked(),
            )
        except git_support.GitError as exc:
            self._entries = []
            self.diff_view.setPlainText(str(exc))
            return
        for entry in self._entries:
            title = entry.version_name or entry.subject or "Saved change"
            date = entry.committed_at[:10] if entry.committed_at else ""
            item = QListWidgetItem(f"{title}\n{date} · {entry.author}")
            item.setToolTip(entry.commit)
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
        try:
            diff = git_support.diff_from_revision(
                self.vault_root, entry.commit, self.relative_path
            )
        except git_support.GitError as exc:
            diff = str(exc)
        self.diff_view.setPlainText(diff or "This item matches the current working copy.")

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
