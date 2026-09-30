"""Homebase device-local recovery browser."""

from __future__ import annotations

import difflib
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QFileDialog, QHBoxLayout, QInputDialog,
    QLabel, QListWidget, QMessageBox, QPushButton, QTextEdit, QVBoxLayout,
)


class HomebaseRecoveryDialog(QDialog):
    def __init__(self, parent, engine, restore_callback) -> None:
        super().__init__(parent)
        self.engine = engine
        self.store = engine.recovery
        self.restore_callback = restore_callback
        self.setWindowTitle("Homebase Local Recovery")
        self.resize(850, 620)
        layout = QVBoxLayout(self)
        self.usage = QLabel()
        layout.addWidget(self.usage)
        row = QHBoxLayout()
        layout.addLayout(row, 1)
        self.events = QListWidget()
        self.events.setMinimumWidth(350)
        row.addWidget(self.events, 1)
        right = QVBoxLayout()
        row.addLayout(right, 2)
        self.paths = QListWidget()
        self.paths.setSelectionMode(QAbstractItemView.ExtendedSelection)
        right.addWidget(self.paths, 1)
        self.preview = QTextEdit()
        self.preview.setReadOnly(True)
        right.addWidget(self.preview, 2)
        self.image_preview = QLabel()
        self.image_preview.setAlignment(Qt.AlignCenter)
        self.image_preview.hide()
        right.addWidget(self.image_preview, 2)
        buttons = QHBoxLayout()
        layout.addLayout(buttons)
        actions = (
            ("Restore Selected", self._restore_selected),
            ("Restore Entire Event", self._restore_all),
            ("Export Selected", self._export),
            ("Pin / Rename", self._pin),
            ("Unpin", self._unpin),
            ("Delete Event", self._delete),
            ("Continue Sync", self._continue_sync),
            ("Check Integrity", self._integrity),
        )
        for title, callback in actions:
            button = QPushButton(title)
            button.clicked.connect(callback)
            buttons.addWidget(button)
        self.events.currentItemChanged.connect(self._show_event)
        self.paths.currentItemChanged.connect(self._show_preview)
        self.refresh()

    def refresh(self) -> None:
        self.events.clear()
        self.usage.setText(f"Recovery storage: {self.store.usage_bytes() / 1024**2:.1f} MiB")
        for event in self.store.list_events():
            counts = {action: 0 for action in ("create", "overwrite", "delete")}
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
            text = (
                f"{event['created_at']}  {'📌 ' if event['pinned'] else ''}{label}\n"
                f"{event['state']} · +{counts['create']} ~{counts['overwrite']} -{counts['delete']} "
                f"· {event.get('remote_device_id') or 'local'} · {size / 1024:.1f} KiB\n"
                f"Checkpoint: {event.get('target_checkpoint_id') or 'local'}"
            )
            from PySide6.QtWidgets import QListWidgetItem
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, event["event_id"])
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
        from PySide6.QtWidgets import QListWidgetItem
        for path in event["paths"]:
            item = QListWidgetItem(f"{path['planned_action']}: {path['path']}  [{path['result']}]")
            item.setData(Qt.UserRole, path["path"])
            self.paths.addItem(item)

    def _show_preview(self, *_args) -> None:
        self.preview.clear()
        event = self._event()
        item = self.paths.currentItem()
        if not event or not item:
            return
        path = next((entry for entry in event["paths"] if entry["path"] == item.data(Qt.UserRole)), None)
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

    def _selected_paths(self) -> list[str]:
        return [item.data(Qt.UserRole) for item in self.paths.selectedItems()]

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
