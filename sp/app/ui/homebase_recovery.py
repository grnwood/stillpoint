"""Homebase device-local recovery browser."""

from __future__ import annotations

import difflib
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QPixmap, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QFileDialog, QHBoxLayout, QInputDialog,
    QHeaderView, QLabel, QListWidget, QMessageBox, QPlainTextEdit, QPushButton,
    QSplitter, QTextEdit, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .screen_positioning import fit_window_to_available_screen
from .theme import chrome_colors


class RecoveryDiffDialog(QDialog):
    """Read-only comparison of the current file and its protected preimage."""

    def __init__(self, path: str, current: bytes | None, protected: bytes, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("homebaseRecoveryDiffDialog")
        self.setWindowTitle(f"Recovery Diff — /{path}")
        layout = QVBoxLayout(self)
        current_bytes = current or b""
        current_missing = current is None
        try:
            current_text = current_bytes.decode("utf-8")
            protected_text = protected.decode("utf-8")
        except UnicodeDecodeError:
            summary = QLabel(
                "This file is binary and cannot be shown as a text diff.\n\n"
                f"Current: {'missing' if current_missing else f'{len(current_bytes):,} bytes'}\n"
                f"Protected copy: {len(protected):,} bytes"
            )
            summary.setWordWrap(True)
            layout.addWidget(summary, 1)
        else:
            matcher = difflib.SequenceMatcher(
                a=current_text.splitlines(),
                b=protected_text.splitlines(),
            )
            changes = [opcode for opcode in matcher.get_opcodes() if opcode[0] != "equal"]
            summary = QLabel(
                f"/{path} · {len(changes)} changed section(s)"
                + (" · current file is missing" if current_missing else "")
            )
            summary.setWordWrap(True)
            layout.addWidget(summary)
            splitter = QSplitter(Qt.Horizontal, self)
            current_editor = self._diff_panel(
                "Current vault file" + (" (missing)" if current_missing else ""),
                current_text,
                splitter,
                "homebaseRecoveryCurrentText",
            )
            splitter.addWidget(current_editor.parentWidget())
            protected_editor = self._diff_panel(
                "Protected copy (before Homebase change)",
                protected_text,
                splitter,
                "homebaseRecoveryProtectedText",
            )
            splitter.addWidget(protected_editor.parentWidget())
            splitter.setStretchFactor(0, 1)
            splitter.setStretchFactor(1, 1)
            layout.addWidget(splitter, 1)
            self._highlight_changes(current_editor, protected_editor, changes)
            current_editor.verticalScrollBar().valueChanged.connect(
                protected_editor.verticalScrollBar().setValue
            )
            protected_editor.verticalScrollBar().valueChanged.connect(
                current_editor.verticalScrollBar().setValue
            )
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        close_button = QPushButton("Close", self)
        close_button.clicked.connect(self.accept)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)
        fit_window_to_available_screen(self, QSize(1180, 760), parent=parent)

    @staticmethod
    def _diff_panel(
        title: str,
        text: str,
        parent: QWidget,
        object_name: str,
    ) -> QPlainTextEdit:
        panel = QWidget(parent)
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        label = QLabel(title, panel)
        label.setStyleSheet("font-weight: 600; padding: 4px 2px;")
        editor = QPlainTextEdit(panel)
        editor.setObjectName(object_name)
        editor.setReadOnly(True)
        editor.setLineWrapMode(QPlainTextEdit.NoWrap)
        editor.setPlainText(text)
        panel_layout.addWidget(label)
        panel_layout.addWidget(editor, 1)
        return editor

    @staticmethod
    def _selection(editor: QPlainTextEdit, start: int, end: int, color: QColor):
        document = editor.document()
        if document.blockCount() <= 0:
            return None
        start = min(max(0, start), document.blockCount() - 1)
        end = min(max(start + 1, end), document.blockCount())
        first = document.findBlockByNumber(start)
        last = document.findBlockByNumber(end - 1)
        if not first.isValid() or not last.isValid():
            return None
        cursor = QTextCursor(first)
        cursor.setPosition(last.position() + max(0, last.length() - 1), QTextCursor.KeepAnchor)
        selection = QTextEdit.ExtraSelection()
        selection.cursor = cursor
        selection.format = QTextCharFormat()
        selection.format.setBackground(color)
        return selection

    @classmethod
    def _highlight_changes(cls, current, protected, changes) -> None:
        current_color = QColor(130, 70, 70, 110)
        protected_color = QColor(55, 125, 90, 120)
        current_selections = []
        protected_selections = []
        for _tag, i1, i2, j1, j2 in changes:
            left = cls._selection(current, i1, i2, current_color)
            right = cls._selection(protected, j1, j2, protected_color)
            if left is not None:
                current_selections.append(left)
            if right is not None:
                protected_selections.append(right)
        current.setExtraSelections(current_selections)
        protected.setExtraSelections(protected_selections)


class HomebaseRecoveryDialog(QDialog):
    def __init__(self, parent, engine, restore_callback) -> None:
        super().__init__(parent)
        self.engine = engine
        self.store = engine.recovery
        self.restore_callback = restore_callback
        self.setWindowTitle("Homebase Local Recovery")
        layout = QVBoxLayout(self)
        introduction = QLabel(
            "StillPoint keeps protected copies before Homebase replaces or removes local files. "
            "Choose a recovery point to preview or restore an earlier version."
        )
        introduction.setWordWrap(True)
        layout.addWidget(introduction)
        self.usage = QLabel()
        self.usage.setToolTip(
            "Recovery copies stay only on this device and are pruned according to Homebase settings."
        )
        layout.addWidget(self.usage)
        row = QHBoxLayout()
        layout.addLayout(row, 1)
        events_panel = QWidget(self)
        events_layout = QVBoxLayout(events_panel)
        events_layout.setContentsMargins(0, 0, 0, 0)
        events_label = QLabel("Recovery points", events_panel)
        events_label.setStyleSheet("font-weight: 600; padding: 2px 0 4px 0;")
        events_layout.addWidget(events_label)
        self.events = QListWidget()
        self.events.setObjectName("homebaseRecoveryEventList")
        self.events.setMinimumWidth(350)
        colors = chrome_colors(self)
        self.events.setAlternatingRowColors(True)
        self.events.setSpacing(3)
        self.events.setStyleSheet(
            "QListWidget {"
            f" border: 1px solid {colors['border']}; padding: 4px;"
            "}"
            "QListWidget::item { padding: 9px 10px; border-bottom: 1px solid "
            f"{colors['border']}; }}"
            f"QListWidget::item:selected {{ background: {colors['selected']}; }}"
        )
        events_layout.addWidget(self.events, 1)
        row.addWidget(events_panel, 1)
        right = QVBoxLayout()
        row.addLayout(right, 2)
        paths_label = QLabel("Protected files — double-click a row to open the full diff")
        paths_label.setStyleSheet("font-weight: 600; padding: 2px 0 4px 0;")
        right.addWidget(paths_label)
        self.paths = QTreeWidget()
        self.paths.setObjectName("homebaseRecoveryProtectedFiles")
        self.paths.setHeaderLabels(["Protected file", "Homebase action", "Status"])
        self.paths.setRootIsDecorated(False)
        self.paths.setAlternatingRowColors(True)
        self.paths.setUniformRowHeights(False)
        self.paths.setIndentation(0)
        self.paths.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.paths.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.paths.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.paths.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.paths.header().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.paths.setStyleSheet(
            "QTreeWidget {"
            f" border: 1px solid {colors['border']};"
            "}"
            "QTreeWidget::item { padding: 8px 10px; border-bottom: 1px solid "
            f"{colors['border']}; border-right: 1px solid {colors['border']}; }}"
            f"QTreeWidget::item:selected {{ background: {colors['selected']}; }}"
            "QHeaderView::section { padding: 7px 10px; font-weight: 600; border: 0; "
            f"border-right: 1px solid {colors['border']}; border-bottom: 1px solid {colors['border']}; }}"
        )
        right.addWidget(self.paths, 1)
        preview_label = QLabel("Quick diff preview")
        preview_label.setStyleSheet("font-weight: 600; padding: 6px 0 2px 0;")
        right.addWidget(preview_label)
        self.preview = QTextEdit()
        self.preview.setReadOnly(True)
        right.addWidget(self.preview, 2)
        self.image_preview = QLabel()
        self.image_preview.setAlignment(Qt.AlignCenter)
        self.image_preview.hide()
        right.addWidget(self.image_preview, 2)
        primary_buttons = QHBoxLayout()
        layout.addLayout(primary_buttons)
        for title, callback in (
            ("Restore Selected", self._restore_selected),
            ("Restore Everything in This Recovery Point", self._restore_all),
            ("Continue Sync", self._continue_sync),
            ("Open Backup Folder", self._open_backup_folder),
        ):
            button = QPushButton(title)
            if title == "Open Backup Folder":
                button.setObjectName("homebaseOpenBackupFolderButton")
            button.clicked.connect(callback)
            primary_buttons.addWidget(button)
        primary_buttons.addStretch(1)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        primary_buttons.addWidget(close_button)

        self.advanced_toggle = QPushButton("Show advanced tools")
        self.advanced_toggle.setCheckable(True)
        layout.addWidget(self.advanced_toggle)
        self.advanced_tools = QWidget(self)
        advanced_buttons = QHBoxLayout(self.advanced_tools)
        advanced_buttons.setContentsMargins(0, 0, 0, 0)
        for title, callback in (
            ("Export Selected", self._export),
            ("Pin / Rename", self._pin),
            ("Unpin", self._unpin),
            ("Delete Recovery Point", self._delete),
            ("Check Storage Integrity", self._integrity),
        ):
            button = QPushButton(title)
            button.clicked.connect(callback)
            advanced_buttons.addWidget(button)
        advanced_buttons.addStretch(1)
        self.advanced_tools.hide()
        layout.addWidget(self.advanced_tools)
        self.advanced_toggle.toggled.connect(
            lambda checked: (
                self.advanced_tools.setVisible(checked),
                self.advanced_toggle.setText(
                    "Hide advanced tools" if checked else "Show advanced tools"
                ),
            )
        )
        self.events.currentItemChanged.connect(self._show_event)
        self.paths.currentItemChanged.connect(self._show_preview)
        self.paths.itemDoubleClicked.connect(self._open_diff_viewer)
        self.refresh()
        fit_window_to_available_screen(self, QSize(1100, 700), parent=parent)

    def refresh(self) -> None:
        self.events.clear()
        self.usage.setText(f"Protected local copies: {self.store.usage_bytes() / 1024**2:.1f} MiB")
        for event in self.store.list_events():
            counts = {action: 0 for action in ("create", "overwrite", "delete", "preserve")}
            for path in event["paths"]:
                action = path["planned_action"]
                if action in counts:
                    counts[action] += 1
            label = event.get("label") or event["operation"]
            size = sum(
                self.store._object_path(path["old_object_id"]).stat().st_size
                for path in event["paths"]
                if path.get("old_object_id") and self.store._object_path(path["old_object_id"]).is_file()
            )
            affected = counts["create"] + counts["overwrite"] + counts["delete"] + counts["preserve"]
            state_label = {
                "complete": "Ready to restore",
                "protected": "Waiting for review",
                "applying": "Interrupted while applying",
                "cancelled": "Pull cancelled",
            }.get(str(event.get("state") or ""), "Recovery available")
            text = (
                f"{'📌 ' if event['pinned'] else ''}{label} · {event['created_at']}\n"
                f"{affected} file(s) protected · {state_label}"
            )
            from PySide6.QtWidgets import QListWidgetItem
            item = QListWidgetItem(text)
            item.setSizeHint(QSize(0, 62))
            item.setData(Qt.UserRole, event["event_id"])
            item.setToolTip(
                f"Created: {event['created_at']}\n"
                f"State: {event.get('state') or 'unknown'}\n"
                f"Created files: {counts['create']}\n"
                f"Changed files: {counts['overwrite']}\n"
                f"Removed files: {counts['delete']}\n"
                f"Preserved local files: {counts['preserve']}\n"
                f"Source device: {event.get('remote_device_id') or 'local'}\n"
                f"Checkpoint: {event.get('target_checkpoint_id') or 'local'}\n"
                f"Protected bytes: {size / 1024:.1f} KiB"
            )
            self.events.addItem(item)

    def _event(self):
        item = self.events.currentItem()
        return self.store.load(item.data(Qt.UserRole)) if item else None

    def _show_event(self, *_args) -> None:
        self.paths.clear()
        self.preview.clear()
        self.image_preview.hide()
        event = self._event()
        if event is None:
            return
        for path in event["paths"]:
            action = {
                "create": "Added by Homebase",
                "overwrite": "Changed by Homebase",
                "delete": "Removed by Homebase",
                "conflict-copy": "Saved as a conflict copy",
                "preserve": "Saved before replacing a missing remote version",
            }.get(path["planned_action"], "Changed by Homebase")
            result = str(path.get("result") or "")
            status = {
                "applied": "Applied",
                "failed": "Needs attention",
                "pending": "Pending",
            }.get(result, result.replace("-", " ").title() or "Protected")
            item = QTreeWidgetItem([path["path"], action, status])
            item.setData(0, Qt.UserRole, path["path"])
            item.setSizeHint(0, QSize(0, 38))
            tooltip = (
                f"Action: {path.get('planned_action') or 'unknown'}\n"
                f"Result: {result or 'unknown'}\n"
                "Double-click to compare the current file with this protected copy."
            )
            for column in range(3):
                item.setToolTip(column, tooltip)
            self.paths.addTopLevelItem(item)

    def _show_preview(self, *_args) -> None:
        self.preview.clear()
        self.image_preview.hide()
        event = self._event()
        item = self.paths.currentItem()
        if not event or not item:
            return
        path = next((entry for entry in event["paths"] if entry["path"] == item.data(0, Qt.UserRole)), None)
        if not path:
            return
        old_id = path.get("old_object_id")
        target = self.store.path(path["path"])
        if not old_id:
            self.preview.setPlainText("This file was created by the pull. No prior bytes exist.")
            return
        try:
            old = self.store.read_object(old_id)
            current = target.read_bytes() if target.is_file() else b""
        except Exception as exc:
            self.preview.setPlainText(str(exc))
            return
        if path["path"].lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")):
            pixmap = QPixmap()
            if pixmap.loadFromData(old):
                self.image_preview.setPixmap(pixmap.scaled(400, 260, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                self.image_preview.show()
                self.preview.setPlainText(f"Prior image: {len(old)} bytes · current file: {len(current)} bytes")
                return
        try:
            before = old.decode("utf-8").splitlines(keepends=True)
            after = current.decode("utf-8").splitlines(keepends=True)
        except UnicodeDecodeError:
            self.preview.setPlainText(f"Binary file · prior size {len(old)} bytes · current size {len(current)} bytes")
            return
        diff = difflib.unified_diff(before, after, fromfile="before pull", tofile="current")
        self.preview.setPlainText("".join(diff) or "Text is identical.")

    def _open_diff_viewer(self, item: QTreeWidgetItem, _column: int = 0) -> None:
        event = self._event()
        if not event or not item:
            return
        rel_path = item.data(0, Qt.UserRole)
        path = next((entry for entry in event["paths"] if entry["path"] == rel_path), None)
        if not path:
            return
        old_id = path.get("old_object_id")
        if not old_id:
            QMessageBox.information(
                self,
                "Recovery Diff",
                "This file was created by the Homebase pull, so there is no protected earlier copy.",
            )
            return
        try:
            protected = self.store.read_object(old_id)
            target = self.store.path(path["path"])
            current = target.read_bytes() if target.is_file() else None
        except Exception as exc:
            QMessageBox.warning(self, "Recovery Diff", f"Could not open the diff: {exc}")
            return
        RecoveryDiffDialog(path["path"], current, protected, self).exec()

    def _selected_paths(self) -> list[str]:
        return [item.data(0, Qt.UserRole) for item in self.paths.selectedItems()]

    def _open_backup_folder(self) -> None:
        try:
            self.store.root.mkdir(mode=0o700, parents=True, exist_ok=True)
            opened = QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.store.root)))
        except Exception as exc:
            QMessageBox.warning(self, "Homebase Local Recovery", f"Could not open backup folder: {exc}")
            return
        if not opened:
            QMessageBox.warning(
                self,
                "Homebase Local Recovery",
                f"The file manager could not open:\n{self.store.root}",
            )

    def _restore_selected(self) -> None:
        event = self._event()
        selected = self._selected_paths()
        if event and selected and self.restore_callback(event["event_id"], selected, False):
            self.refresh()

    def _restore_all(self) -> None:
        event = self._event()
        if not event:
            return
        creates = sum(item["planned_action"] == "create" and item["result"] == "applied" for item in event["paths"])
        message = f"Restore the state before this event? {creates} file(s) created by it will be removed."
        if QMessageBox.question(self, "Restore Entire Event", message, QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        if self.restore_callback(event["event_id"], None, True):
            self.refresh()

    def _export(self) -> None:
        event = self._event()
        selected = set(self._selected_paths())
        if not event or not selected:
            return
        destination = QFileDialog.getExistingDirectory(self, "Export Preimages")
        if not destination:
            return
        target_root = (Path(destination) / f"homebase-recovery-{event['event_id'][:8]}").resolve()
        try:
            target_root.mkdir()
            for item in event["paths"]:
                if item["path"] not in selected or not item.get("old_object_id"):
                    continue
                output = target_root / item["path"]
                if not output.resolve().is_relative_to(target_root):
                    raise ValueError("Unsafe export path")
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(self.store.read_object(item["old_object_id"]))
        except Exception as exc:
            QMessageBox.critical(self, "Export Failed", str(exc))

    def _pin(self) -> None:
        event = self._event()
        if not event:
            return
        label, accepted = QInputDialog.getText(self, "Pin Recovery Point", "Name (optional):", text=event.get("label") or "")
        if accepted:
            try:
                self.store.pin(event["event_id"], True, label)
                self.refresh()
            except Exception as exc:
                QMessageBox.warning(self, "Pin Recovery Point", str(exc))

    def _unpin(self) -> None:
        event = self._event()
        if event and event["pinned"]:
            if QMessageBox.question(self, "Unpin Recovery Point", "Allow this event to be pruned?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
                self.store.pin(event["event_id"], False)
                self.refresh()

    def _delete(self) -> None:
        event = self._event()
        if event and QMessageBox.question(self, "Delete Recovery Event", "Delete this unpinned recovery event?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            try:
                self.store.delete(event["event_id"])
                self.refresh()
            except Exception as exc:
                QMessageBox.critical(self, "Delete Failed", str(exc))

    def _integrity(self) -> None:
        result = self.store.integrity_check()
        QMessageBox.information(self, "Recovery Integrity", "\n".join(f"{name}: {len(values)}" for name, values in result.items()))

    def _continue_sync(self) -> None:
        if not self.engine.interrupted_recovery_events():
            return
        if QMessageBox.question(self, "Continue Homebase Sync", "Continue after reviewing the interrupted recovery event?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            self.engine.continue_after_interrupted_recovery()
            self.refresh()
