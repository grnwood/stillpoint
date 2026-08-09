from __future__ import annotations

import re
from typing import Any, Callable, Optional

from pathlib import Path

from PySide6.QtCore import QEvent, QFileInfo, QPoint, QSize, Qt, Signal
from PySide6.QtGui import QKeyEvent, QColor, QImage, QMouseEvent, QPixmap, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QCompleter,
    QDialog,
    QFileDialog,
    QFileIconProvider,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QGraphicsDropShadowEffect,
    QWidget,
)

from .theme import theme_color, theme_value


class QuickCaptureInput(QTextEdit):
    captureRequested = Signal()
    captureAndContinueRequested = Signal()
    dismissRequested = Signal()
    destinationRequested = Signal()
    imageAdded = Signal(object)
    imageFileAdded = Signal(object)

    def __init__(self, parent: Optional[QDialog] = None) -> None:
        super().__init__(parent)
        self._clipboard_image_counter = 0
        self._image_file_counter = 0
        self.setPlaceholderText("Type a thought or paste images...")
        self.setAcceptRichText(False)
        self.setTabChangesFocus(False)
        self.setAcceptDrops(True)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            modifiers = event.modifiers() & ~Qt.KeypadModifier
            if modifiers == Qt.ControlModifier:
                event.accept()
                self.captureRequested.emit()
                return
            if modifiers == (Qt.ControlModifier | Qt.ShiftModifier):
                event.accept()
                self.captureAndContinueRequested.emit()
                return
            if modifiers == Qt.NoModifier or modifiers == Qt.ShiftModifier:
                super().keyPressEvent(event)
                return
        if event.key() == Qt.Key_Escape:
            event.accept()
            self.dismissRequested.emit()
            return
        if event.key() == Qt.Key_P and event.modifiers() & Qt.ControlModifier:
            event.accept()
            self.destinationRequested.emit()
            return
        super().keyPressEvent(event)
        modifiers = event.modifiers() & ~Qt.KeypadModifier
        if event.key() == Qt.Key_Space and modifiers == Qt.NoModifier:
            self._maybe_expand_task_shortcut()

    def _maybe_expand_task_shortcut(self) -> None:
        cursor = self.textCursor()
        if cursor.hasSelection():
            return
        block = cursor.block()
        if not block.isValid():
            return
        pos_in_block = cursor.position() - block.position()
        if pos_in_block <= 0:
            return
        line_prefix = block.text()[:pos_in_block]
        match = re.match(r"^(?P<indent>\s*)\((?P<state>[xX*]?)\)\s$", line_prefix)
        if not match:
            return
        symbol = "☑" if (match.group("state") or "").strip().lower() in {"x", "*"} else "☐"
        replace_cursor = QTextCursor(cursor)
        replace_cursor.beginEditBlock()
        replace_cursor.movePosition(QTextCursor.Left, QTextCursor.KeepAnchor, len(line_prefix))
        replace_cursor.insertText(f"{match.group('indent') or ''}{symbol} ")
        replace_cursor.endEditBlock()
        self.setTextCursor(replace_cursor)

    def _insert_attachment_placeholder(self, placeholder: str) -> None:
        cursor = self.textCursor()
        cursor.insertText(placeholder)
        self.setTextCursor(cursor)

    def _next_clipboard_placeholder(self, image: QImage) -> str:
        self._clipboard_image_counter += 1
        return f"<clipboard-Image-{self._clipboard_image_counter}-{image.width()}x{image.height()}>"

    def _next_file_placeholder(self, path: Path, image: Optional[QImage] = None) -> str:
        self._image_file_counter += 1
        stem = re.sub(r"[^A-Za-z0-9._-]+", "-", path.stem).strip("-") or "image"
        if image is not None and not image.isNull():
            return f"<file-Image-{self._image_file_counter}-{stem}-{image.width()}x{image.height()}>"
        return f"<file-Attachment-{self._image_file_counter}-{stem}>"

    def add_local_file(self, path: Path) -> bool:
        if not path.is_file():
            return False
        image = QImage(str(path))
        if image.isNull():
            placeholder = self._next_file_placeholder(path)
            self.imageFileAdded.emit({"path": path, "placeholder": placeholder, "is_image": False})
        else:
            placeholder = self._next_file_placeholder(path, image)
            self.imageFileAdded.emit(
                {"path": path, "placeholder": placeholder, "is_image": True, "image": image}
            )
        self._insert_attachment_placeholder(placeholder)
        return True

    def insertFromMimeData(self, source) -> None:  # type: ignore[override]
        image = self._image_from_mime_data(source)
        if image is not None:
            placeholder = self._next_clipboard_placeholder(image)
            self.imageAdded.emit({"image": image, "placeholder": placeholder})
            self._insert_attachment_placeholder(placeholder)
            return
        if source and source.hasUrls():
            handled = False
            for url in source.urls():
                if url.isLocalFile():
                    path = Path(url.toLocalFile())
                    handled = self.add_local_file(path) or handled
            if handled:
                return
        super().insertFromMimeData(source)

    def _image_from_mime_data(self, source) -> Optional[QImage]:
        if source is None:
            return None
        if source.hasImage():
            try:
                image = source.imageData()
            except Exception:
                image = None
            if isinstance(image, QImage) and not image.isNull():
                return image
        image_formats = {
            "application/x-qt-image",
            "image/png",
            "image/jpeg",
            "image/jpg",
            "image/gif",
            "image/bmp",
            "image/webp",
            "image/tiff",
            "image/x-tiff",
            "public.png",
            "public.jpeg",
            "public.tiff",
        }
        try:
            formats = [str(fmt).lower() for fmt in source.formats()]
        except Exception:
            formats = []
        for fmt in formats:
            if fmt not in image_formats and not fmt.startswith("image/") and "tiff" not in fmt:
                continue
            try:
                raw = source.data(fmt)
            except Exception:
                raw = None
            if not raw:
                continue
            image = QImage()
            try:
                if image.loadFromData(bytes(raw)) and not image.isNull():
                    return image
            except Exception:
                continue
        return None

    def dragEnterEvent(self, event) -> None:  # type: ignore[override]
        if event.mimeData().hasImage() or event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # type: ignore[override]
        if event.mimeData().hasImage() or event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:  # type: ignore[override]
        image = self._image_from_mime_data(event.mimeData())
        if image is not None:
            placeholder = self._next_clipboard_placeholder(image)
            self.imageAdded.emit({"image": image, "placeholder": placeholder})
            self._insert_attachment_placeholder(placeholder)
            event.acceptProposedAction()
            return
        if event.mimeData().hasUrls():
            handled = False
            for url in event.mimeData().urls():
                if url.isLocalFile():
                    path = Path(url.toLocalFile())
                    handled = self.add_local_file(path) or handled
            if handled:
                event.acceptProposedAction()
                return
        super().dropEvent(event)


class QuickCaptureOverlay(QDialog):
    def __init__(
        self,
        *,
        parent,
        on_capture: Callable[[str, list[dict], Optional[str]], object],
        subtitle: Optional[str] = None,
        vault_options: Optional[list[dict[str, str]]] = None,
        selected_vault: Optional[str] = None,
        destination_options: Optional[list[dict[str, Any]]] = None,
        selected_destination: Optional[dict[str, Any]] = None,
        on_capture_with_destination: Optional[
            Callable[[str, list[dict], Optional[str], dict[str, Any]], Optional[dict]]
        ] = None,
        on_undo: Optional[Callable[[str], object]] = None,
        on_open_capture: Optional[Callable[[dict], None]] = None,
        capture_history: Optional[list[dict]] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Quick Capture")
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setModal(False)
        self._on_capture = on_capture
        self._subtitle = subtitle
        self._vault_options = vault_options or []
        self._selected_vault = selected_vault
        self._destination_options = destination_options or []
        self._selected_destination = selected_destination or {"page_mode": "today", "page_ref": None}
        self._on_capture_with_destination = on_capture_with_destination
        self._on_undo = on_undo
        self._on_open_capture = on_open_capture
        self._capture_history = [dict(entry) for entry in (capture_history or [])]
        self._attachments: list[dict] = []
        self._drag_origin: Optional[QPoint] = None
        self._app_filter_installed = False
        self._build_ui()
        self.setMinimumWidth(700)
        try:
            self.resize(700, self.sizeHint().height())
        except Exception:
            pass

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)

        card = QFrame(self)
        card.setObjectName("QuickCaptureCard")
        card.setStyleSheet(
            "QFrame#QuickCaptureCard {"
            "  background: "
            f"{theme_value('quick_capture.card.bg', '#000000')};"
            "  border: 1px solid "
            f"{theme_value('quick_capture.card.border', '#222222')};"
            "  border-radius: 10px;"
            "}"
        )
        try:
            shadow = QGraphicsDropShadowEffect(card)
            shadow.setBlurRadius(24)
            shadow.setOffset(0, 6)
            shadow.setColor(
                QColor(0, 0, 0, int(theme_value("quick_capture.card.shadow_alpha", 90)))
            )
            card.setGraphicsEffect(shadow)
        except Exception:
            pass
        outer.addWidget(card, 1)
        card.setMinimumWidth(680)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        self.drag_handle = QLabel("Quick Capture", card)
        self.drag_handle.setObjectName("QuickCaptureDragHandle")
        self.drag_handle.setCursor(Qt.OpenHandCursor)
        self.drag_handle.setStyleSheet(
            "color: "
            f"{theme_value('quick_capture.title.color', '#dfe6fa')}; "
            "font-size: "
            f"{theme_value('quick_capture.title.size_px', 12)}px; "
            "font-weight: 600; letter-spacing: 0.04em;"
        )
        self.drag_handle.installEventFilter(self)
        header.addWidget(self.drag_handle, 1)
        dismiss_btn = QPushButton("Cancel", card)
        dismiss_btn.clicked.connect(self.reject)
        dismiss_btn.setCursor(Qt.PointingHandCursor)
        dismiss_btn.setStyleSheet(
            "QPushButton {"
            "  color: "
            f"{theme_value('quick_capture.dismiss.text', '#dfe6fa')};"
            "  background: transparent;"
            "  border: 1px solid "
            f"{theme_value('quick_capture.dismiss.border', 'rgba(255, 255, 255, 0.28)')};"
            "  border-radius: 6px;"
            "  padding: 4px 10px;"
            "}"
            "QPushButton:hover {"
            "  background: "
            f"{theme_value('quick_capture.dismiss.hover_bg', 'rgba(255, 255, 255, 0.08)')};"
            "}"
        )
        header.addWidget(dismiss_btn, 0, Qt.AlignRight)
        layout.addLayout(header)

        self.input = QuickCaptureInput(card)
        self.input.setMinimumHeight(90)
        self.input.setFocusPolicy(Qt.StrongFocus)
        self.input.setStyleSheet(
            "font-size: "
            f"{theme_value('quick_capture.input.size_px', 18)}px; "
            "color: "
            f"{theme_value('quick_capture.input.text', '#ffffff')}; "
            "background: "
            f"{theme_value('quick_capture.input.bg', 'rgba(255, 255, 255, 0.08)')}; "
            "border: 1px solid "
            f"{theme_value('quick_capture.input.border', 'rgba(255, 255, 255, 0.5)')}; "
            "padding: 8px; border-radius: 6px;"
        )
        self.input.captureRequested.connect(lambda: self._capture(close_after=True))
        self.input.captureAndContinueRequested.connect(lambda: self._capture(close_after=False))
        self.input.dismissRequested.connect(self.reject)
        self.input.destinationRequested.connect(self._cycle_destination)
        self.input.imageAdded.connect(self._add_clipboard_image)
        self.input.imageFileAdded.connect(self._add_image_file)
        layout.addWidget(self.input)

        controls = QHBoxLayout()
        add_files_btn = QPushButton("Add files…", card)
        add_files_btn.clicked.connect(self._choose_files)
        controls.addWidget(add_files_btn)
        controls.addStretch(1)
        history_btn = QPushButton("History", card)
        history_btn.clicked.connect(self._toggle_history)
        controls.addWidget(history_btn)
        self.capture_btn = QPushButton("Capture", card)
        self.capture_btn.clicked.connect(lambda: self._capture(close_after=True))
        controls.addWidget(self.capture_btn)
        layout.addLayout(controls)

        hint = QLabel(
            "Ctrl+Enter capture · Ctrl+Shift+Enter capture another · Ctrl+P switch destination · Esc dismiss",
            card,
        )
        hint.setStyleSheet(
            "color: "
            f"{theme_value('quick_capture.hint.color', '#dfe6fa')}; "
            "font-size: "
            f"{theme_value('quick_capture.hint.size_px', 12)}px;"
        )
        layout.addWidget(hint)

        self.attachments_label = QLabel("", card)
        self.attachments_label.setStyleSheet(
            "color: "
            f"{theme_value('quick_capture.attachments.color', '#dfe6fa')}; "
            "font-size: "
            f"{theme_value('quick_capture.attachments.size_px', 11)}px;"
        )
        self.attachments_label.setWordWrap(True)
        layout.addWidget(self.attachments_label)

        self.attachments_widget = QWidget(card)
        self.attachments_layout = QVBoxLayout(self.attachments_widget)
        self.attachments_layout.setContentsMargins(0, 0, 0, 0)
        self.attachments_layout.setSpacing(4)
        layout.addWidget(self.attachments_widget)
        self.attachments_widget.hide()

        self.status_row = QHBoxLayout()
        self.status_label = QLabel("", card)
        self.status_label.setWordWrap(True)
        self.status_row.addWidget(self.status_label, 1)
        self.undo_btn = QPushButton("Undo", card)
        self.undo_btn.hide()
        self.undo_btn.clicked.connect(self._undo_last_capture)
        self.status_row.addWidget(self.undo_btn)
        self.open_btn = QPushButton("Open", card)
        self.open_btn.hide()
        self.open_btn.clicked.connect(self._open_last_capture)
        self.status_row.addWidget(self.open_btn)
        layout.addLayout(self.status_row)

        self.history_list = QListWidget(card)
        self.history_list.setMaximumHeight(130)
        self.history_list.itemActivated.connect(self._history_item_activated)
        self.history_list.hide()
        layout.addWidget(self.history_list)
        self._refresh_history()

        if self._subtitle:
            sub = QLabel(self._subtitle, card)
            sub.setStyleSheet(
                "color: "
                f"{theme_value('quick_capture.subtitle.color', '#9aa4b2')}; "
                "font-size: "
                f"{theme_value('quick_capture.subtitle.size_px', 11)}px;"
            )
            sub.setWordWrap(True)
            layout.addWidget(sub)

        if self._vault_options:
            vault_row = QHBoxLayout()
            vault_label = QLabel("Dropping to:", card)
            vault_label.setStyleSheet(
                "color: "
                f"{theme_value('quick_capture.vault_label.color', '#9aa4b2')}; "
                "font-size: "
                f"{theme_value('quick_capture.vault_label.size_px', 11)}px;"
            )
            vault_row.addWidget(vault_label)
            self.vault_combo = QComboBox(card)
            for entry in self._vault_options:
                self.vault_combo.addItem(entry.get("name") or entry.get("path") or "", entry.get("path"))
            if self._selected_vault:
                idx = self.vault_combo.findData(self._selected_vault)
                if idx >= 0:
                    self.vault_combo.setCurrentIndex(idx)
            self.vault_combo.currentIndexChanged.connect(self._on_vault_changed)
            vault_row.addWidget(self.vault_combo, 1)
            layout.addLayout(vault_row)

        if self._destination_options:
            destination_row = QHBoxLayout()
            destination_label = QLabel("Destination:", card)
            destination_row.addWidget(destination_label)
            self.destination_combo = QComboBox(card)
            self.destination_combo.setEditable(True)
            self.destination_combo.setInsertPolicy(QComboBox.NoInsert)
            for entry in self._destination_options:
                label = str(entry.get("label") or entry.get("page_ref") or "Today's Journal")
                self.destination_combo.addItem(label, dict(entry))
            completer = QCompleter(self.destination_combo.model(), self.destination_combo)
            completer.setCaseSensitivity(Qt.CaseInsensitive)
            completer.setFilterMode(Qt.MatchContains)
            self.destination_combo.setCompleter(completer)
            selected_idx = self._find_destination_index(self._selected_destination)
            if selected_idx >= 0:
                self.destination_combo.setCurrentIndex(selected_idx)
            self.destination_combo.currentIndexChanged.connect(self._on_destination_changed)
            destination_row.addWidget(self.destination_combo, 1)
            layout.addLayout(destination_row)

    def _capture(self, *, close_after: bool) -> None:
        text = (self.input.toPlainText() or "").strip()
        if not text:
            return
        destination = self._current_destination()
        try:
            if self._on_capture_with_destination is not None:
                result = self._on_capture_with_destination(
                    text,
                    list(self._attachments),
                    self._selected_vault,
                    destination,
                )
            else:
                result = self._on_capture(text, list(self._attachments), self._selected_vault)
        except Exception as exc:
            self._show_capture_error(str(exc) or "Capture failed")
            return
        if isinstance(result, dict) and not result.get("ok", True):
            self._show_capture_error(str(result.get("error") or "Capture failed"))
            return
        receipt = dict(result) if isinstance(result, dict) else {}
        receipt.setdefault("excerpt", text.replace("\n", " ")[:160])
        receipt.setdefault("destination", destination.get("label") or destination.get("page_ref") or "Today's Journal")
        if receipt.get("id"):
            self._capture_history.insert(0, receipt)
            self._capture_history = self._capture_history[:20]
        self._refresh_history()
        self._last_receipt = receipt
        self.status_label.setText(f"Saved to {receipt.get('destination') or 'destination'}.")
        self.status_label.setStyleSheet(f"color: {theme_value('quick_capture.subtitle.color', '#9aa4b2')};")
        self.undo_btn.setVisible(bool(receipt.get("id") and self._on_undo))
        self.open_btn.setVisible(bool(receipt.get("path") and self._on_open_capture))
        if close_after:
            self.accept()
            return
        self.input.clear()
        self._clear_attachments(remove_placeholders=False)
        self.input.setFocus()

    def _show_capture_error(self, message: str) -> None:
        self.status_label.setText(message)
        self.status_label.setStyleSheet("color: #ff8c8c;")
        self.undo_btn.hide()
        self.open_btn.hide()

    def _find_destination_index(self, target: dict[str, Any]) -> int:
        mode = target.get("page_mode") or "today"
        ref = str(target.get("page_ref") or "")
        for idx, entry in enumerate(self._destination_options):
            if (entry.get("page_mode") or "today") == mode and str(entry.get("page_ref") or "") == ref:
                return idx
        return -1

    def _current_destination(self) -> dict[str, Any]:
        if not hasattr(self, "destination_combo"):
            return dict(self._selected_destination)
        typed = self.destination_combo.currentText().strip()
        current_index = self.destination_combo.currentIndex()
        if current_index >= 0 and typed == self.destination_combo.itemText(current_index).strip():
            entry = self.destination_combo.itemData(current_index)
            if isinstance(entry, dict):
                return dict(entry)
        if typed:
            return {"label": typed, "page_mode": "custom", "page_ref": typed}
        return {"label": "Today's Journal", "page_mode": "today", "page_ref": None}

    def _on_destination_changed(self) -> None:
        self._selected_destination = self._current_destination()

    def _focus_destination(self) -> None:
        if not hasattr(self, "destination_combo"):
            return
        self.destination_combo.setFocus()
        self.destination_combo.lineEdit().selectAll()
        self.destination_combo.showPopup()

    def _cycle_destination(self) -> None:
        if not hasattr(self, "destination_combo") or self.destination_combo.count() < 1:
            self.input.setFocus()
            return
        cursor = self.input.textCursor()
        next_index = (self.destination_combo.currentIndex() + 1) % self.destination_combo.count()
        self.destination_combo.setCurrentIndex(next_index)
        self.input.setFocus()
        self.input.setTextCursor(cursor)

    def _toggle_history(self) -> None:
        self.history_list.setVisible(not self.history_list.isVisible())
        self.adjustSize()

    def _refresh_history(self) -> None:
        if not hasattr(self, "history_list"):
            return
        self.history_list.clear()
        for receipt in self._capture_history[:20]:
            destination = str(receipt.get("destination") or "Capture")
            excerpt = str(receipt.get("excerpt") or "").replace("\n", " ")
            item = QListWidgetItem(f"{destination} — {excerpt}")
            item.setData(Qt.UserRole, dict(receipt))
            self.history_list.addItem(item)

    def _history_item_activated(self, item: QListWidgetItem) -> None:
        receipt = item.data(Qt.UserRole)
        if isinstance(receipt, dict) and self._on_open_capture and receipt.get("path"):
            self._on_open_capture(receipt)

    def _undo_last_capture(self) -> None:
        receipt = getattr(self, "_last_receipt", None)
        capture_id = str(receipt.get("id") or "") if isinstance(receipt, dict) else ""
        if not capture_id or self._on_undo is None:
            return
        try:
            result = self._on_undo(capture_id)
        except Exception as exc:
            self._show_capture_error(str(exc) or "Undo failed")
            return
        if isinstance(result, dict) and not result.get("ok", False):
            self._show_capture_error(str(result.get("error") or "Undo failed"))
            return
        if result is False:
            self._show_capture_error("Undo failed")
            return
        self._capture_history = [item for item in self._capture_history if str(item.get("id") or "") != capture_id]
        self._refresh_history()
        self.status_label.setText("Capture undone.")
        self.undo_btn.hide()
        self.open_btn.hide()

    def _open_last_capture(self) -> None:
        receipt = getattr(self, "_last_receipt", None)
        if isinstance(receipt, dict) and self._on_open_capture:
            self._on_open_capture(receipt)

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        app = QApplication.instance()
        if app is not None and not self._app_filter_installed:
            app.installEventFilter(self)
            self._app_filter_installed = True
        self._sync_attachment_width()

    def hideEvent(self, event) -> None:  # type: ignore[override]
        app = QApplication.instance()
        if app is not None and self._app_filter_installed:
            app.removeEventFilter(self)
            self._app_filter_installed = False
        super().hideEvent(event)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._sync_attachment_width()

    def eventFilter(self, watched, event) -> bool:  # type: ignore[override]
        if (
            self.isVisible()
            and isinstance(event, QKeyEvent)
            and event.key() == Qt.Key_P
            and event.modifiers() & Qt.ControlModifier
            and event.type() in (QEvent.ShortcutOverride, QEvent.KeyPress)
        ):
            event.accept()
            if event.type() == QEvent.KeyPress:
                self._cycle_destination()
            return True
        if watched is self.drag_handle and isinstance(event, QMouseEvent):
            if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                self._drag_origin = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                self.drag_handle.setCursor(Qt.ClosedHandCursor)
                event.accept()
                return True
            if event.type() == QEvent.MouseMove and self._drag_origin is not None:
                self.move(event.globalPosition().toPoint() - self._drag_origin)
                event.accept()
                return True
            if event.type() == QEvent.MouseButtonRelease and self._drag_origin is not None:
                self._drag_origin = None
                self.drag_handle.setCursor(Qt.OpenHandCursor)
                event.accept()
                return True
        return super().eventFilter(watched, event)

    def _on_vault_changed(self) -> None:
        if not hasattr(self, "vault_combo"):
            return
        self._selected_vault = self.vault_combo.currentData()

    def _add_clipboard_image(self, payload) -> None:
        image = payload.get("image") if isinstance(payload, dict) else payload
        if image.isNull():
            return
        entry = {
            "kind": "clipboard",
            "image": image,
            "width": image.width(),
            "height": image.height(),
            "placeholder": payload.get("placeholder") if isinstance(payload, dict) else None,
        }
        self._attachments.append(entry)
        self._refresh_attachments()

    def _add_image_file(self, payload) -> None:
        path = payload.get("path") if isinstance(payload, dict) else payload
        if not path.exists():
            return
        image = payload.get("image") if isinstance(payload, dict) else QImage(str(path))
        if isinstance(payload, dict) and "is_image" in payload:
            is_image = bool(payload.get("is_image"))
        else:
            is_image = isinstance(image, QImage) and not image.isNull()
        entry = {
            "kind": "file",
            "path": path,
            "name": path.name,
            "width": image.width() if is_image and isinstance(image, QImage) else None,
            "height": image.height() if is_image and isinstance(image, QImage) else None,
            "image": image if is_image else None,
            "is_image": is_image,
            "placeholder": payload.get("placeholder") if isinstance(payload, dict) else None,
        }
        self._attachments.append(entry)
        self._refresh_attachments()

    def _refresh_attachments(self) -> None:
        while self.attachments_layout.count():
            item = self.attachments_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        if not self._attachments:
            self.attachments_label.setText("")
            self.attachments_widget.hide()
            return
        self.attachments_widget.show()
        self.attachments_label.setText(f"Attachments ({len(self._attachments)})")
        self._attachment_preview_labels: list[QLabel] = []
        for idx, entry in enumerate(self._attachments, start=1):
            name = entry.get("name") or f"clipboard image {idx}"
            width = entry.get("width")
            height = entry.get("height")
            row = QFrame(self.attachments_widget)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(4, 2, 4, 2)
            preview = QLabel(row)
            preview.setFixedSize(74, 74)
            preview.setAlignment(Qt.AlignCenter)
            preview.setStyleSheet(
                "background: rgba(255, 255, 255, 0.05);"
                "border: 1px solid rgba(255, 255, 255, 0.18);"
                "border-radius: 5px;"
            )
            image = entry.get("image")
            if isinstance(image, QImage) and not image.isNull():
                preview.setPixmap(
                    QPixmap.fromImage(image).scaled(
                        70,
                        70,
                        Qt.KeepAspectRatio,
                        Qt.SmoothTransformation,
                    )
                )
            elif entry.get("path"):
                icon = QFileIconProvider().icon(QFileInfo(str(entry.get("path"))))
                preview.setPixmap(icon.pixmap(QSize(48, 48)))
            self._attachment_preview_labels.append(preview)
            row_layout.addWidget(preview)
            detail = f"{name} — {width}x{height}" if width and height else str(name)
            row_layout.addWidget(QLabel(detail, row), 1)
            remove_btn = QPushButton("Remove", row)
            remove_btn.clicked.connect(lambda _checked=False, attached=entry: self._remove_attachment(attached))
            row_layout.addWidget(remove_btn)
            self.attachments_layout.addWidget(row)
        self._sync_attachment_width()

    def _choose_files(self) -> None:
        paths, _selected = QFileDialog.getOpenFileNames(self, "Add attachments")
        for raw_path in paths:
            self.input.add_local_file(Path(raw_path))

    def _remove_attachment(self, entry: dict) -> None:
        if entry not in self._attachments:
            return
        self._attachments.remove(entry)
        placeholder = str(entry.get("placeholder") or "")
        if placeholder:
            cursor = self.input.textCursor()
            position = cursor.position()
            text = self.input.toPlainText()
            self.input.setPlainText(text.replace(placeholder, ""))
            cursor = self.input.textCursor()
            cursor.setPosition(min(position, len(self.input.toPlainText())))
            self.input.setTextCursor(cursor)
        self._refresh_attachments()

    def _clear_attachments(self, *, remove_placeholders: bool) -> None:
        if remove_placeholders:
            for entry in list(self._attachments):
                self._remove_attachment(entry)
            return
        self._attachments.clear()
        self._refresh_attachments()

    def _sync_attachment_width(self) -> None:
        try:
            width = max(200, self.input.width())
            self.attachments_label.setMaximumWidth(width)
        except Exception:
            pass
