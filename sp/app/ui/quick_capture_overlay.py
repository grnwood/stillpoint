from __future__ import annotations

from typing import Callable, Optional

from pathlib import Path

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QKeyEvent, QColor, QImage
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QTextEdit,
    QVBoxLayout,
    QGraphicsDropShadowEffect,
)


class QuickCaptureInput(QTextEdit):
    captureRequested = Signal()
    dismissRequested = Signal()
    imageAdded = Signal(object)
    imageFileAdded = Signal(object)

    def __init__(self, parent: Optional[QDialog] = None) -> None:
        super().__init__(parent)
        self.setPlaceholderText("Type a thought or paste/drag images...")
        self.setAcceptRichText(False)
        self.setTabChangesFocus(False)
        self.setAcceptDrops(True)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if event.modifiers() & Qt.ShiftModifier:
                super().keyPressEvent(event)
                return
            event.accept()
            self.captureRequested.emit()
            return
        if event.key() == Qt.Key_Escape:
            event.accept()
            self.dismissRequested.emit()
            return
        super().keyPressEvent(event)

    def insertFromMimeData(self, source) -> None:  # type: ignore[override]
        if source and source.hasImage():
            image = source.imageData()
            if isinstance(image, QImage) and not image.isNull():
                self.imageAdded.emit(image)
                return
        if source and source.hasUrls():
            handled = False
            for url in source.urls():
                if url.isLocalFile():
                    path = Path(url.toLocalFile())
                    if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}:
                        self.imageFileAdded.emit(path)
                        handled = True
            if handled:
                return
        super().insertFromMimeData(source)

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
        if event.mimeData().hasImage():
            image = event.mimeData().imageData()
            if isinstance(image, QImage) and not image.isNull():
                self.imageAdded.emit(image)
                event.acceptProposedAction()
                return
        if event.mimeData().hasUrls():
            handled = False
            for url in event.mimeData().urls():
                if url.isLocalFile():
                    path = Path(url.toLocalFile())
                    if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}:
                        self.imageFileAdded.emit(path)
                        handled = True
            if handled:
                event.acceptProposedAction()
                return
        super().dropEvent(event)


class QuickCaptureOverlay(QDialog):
    def __init__(
        self,
        *,
        parent,
        on_capture: Callable[[str, list[dict], Optional[str]], None],
        subtitle: Optional[str] = None,
        vault_options: Optional[list[dict[str, str]]] = None,
        selected_vault: Optional[str] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Quick Capture")
        self.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setModal(False)
        self._on_capture = on_capture
        self._subtitle = subtitle
        self._vault_options = vault_options or []
        self._selected_vault = selected_vault
        self._attachments: list[dict] = []
        self._focus_timer = QTimer(self)
        self._focus_timer.setInterval(150)
        self._focus_timer.timeout.connect(self._ensure_input_focus)
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
            "  background: #000000;"
            "  border: 1px solid #222222;"
            "  border-radius: 10px;"
            "}"
        )
        try:
            shadow = QGraphicsDropShadowEffect(card)
            shadow.setBlurRadius(24)
            shadow.setOffset(0, 6)
            shadow.setColor(QColor(0, 0, 0, 90))
            card.setGraphicsEffect(shadow)
        except Exception:
            pass
        outer.addWidget(card, 1)
        card.setMinimumWidth(680)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        self.input = QuickCaptureInput(card)
        self.input.setMinimumHeight(90)
        self.input.setFocusPolicy(Qt.StrongFocus)
        self.input.setStyleSheet(
            "font-size: 18px; color: white; background: rgba(255, 255, 255, 0.08);"
            "border: 1px solid rgba(255, 255, 255, 0.5); padding: 8px; border-radius: 6px;"
        )
        self.input.captureRequested.connect(self._capture)
        self.input.dismissRequested.connect(self.reject)
        self.input.imageAdded.connect(self._add_clipboard_image)
        self.input.imageFileAdded.connect(self._add_image_file)
        layout.addWidget(self.input)

        hint = QLabel("Enter to capture, Esc to dismiss", card)
        hint.setStyleSheet("color: #dfe6fa; font-size: 12px;")
        layout.addWidget(hint)

        self.attachments_label = QLabel("", card)
        self.attachments_label.setStyleSheet("color: #dfe6fa; font-size: 11px;")
        self.attachments_label.setWordWrap(True)
        layout.addWidget(self.attachments_label)

        if self._subtitle:
            sub = QLabel(self._subtitle, card)
            sub.setStyleSheet("color: #9aa4b2; font-size: 11px;")
            sub.setWordWrap(True)
            layout.addWidget(sub)

        if self._vault_options:
            vault_row = QHBoxLayout()
            vault_label = QLabel("Dropping to:", card)
            vault_label.setStyleSheet("color: #9aa4b2; font-size: 11px;")
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

    def _capture(self) -> None:
        text = (self.input.toPlainText() or "").strip()
        if text:
            try:
                self._on_capture(text, self._attachments, self._selected_vault)
            except Exception:
                pass
        self.accept()

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        self._ensure_input_focus()
        self._focus_timer.start()
        self._sync_attachment_width()

    def hideEvent(self, event) -> None:  # type: ignore[override]
        self._focus_timer.stop()
        super().hideEvent(event)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._sync_attachment_width()

    def _ensure_input_focus(self) -> None:
        if not self.isVisible():
            return
        if self.input.hasFocus():
            return
        self.raise_()
        self.activateWindow()
        self.input.setFocus()

    def _on_vault_changed(self) -> None:
        if not hasattr(self, "vault_combo"):
            return
        self._selected_vault = self.vault_combo.currentData()

    def _add_clipboard_image(self, image: QImage) -> None:
        if image.isNull():
            return
        entry = {
            "kind": "clipboard",
            "image": image,
            "width": image.width(),
            "height": image.height(),
        }
        self._attachments.append(entry)
        self._refresh_attachments()

    def _add_image_file(self, path: Path) -> None:
        if not path.exists():
            return
        image = QImage(str(path))
        if image.isNull():
            return
        entry = {
            "kind": "file",
            "path": path,
            "name": path.name,
            "width": image.width(),
            "height": image.height(),
        }
        self._attachments.append(entry)
        self._refresh_attachments()

    def _refresh_attachments(self) -> None:
        if not self._attachments:
            self.attachments_label.setText("")
            return
        lines = []
        for idx, entry in enumerate(self._attachments, start=1):
            name = entry.get("name") or f"clipboard image {idx}"
            width = entry.get("width")
            height = entry.get("height")
            if width and height:
                lines.append(f"{name} — {width}x{height}")
            else:
                lines.append(name)
        self.attachments_label.setText("Attachments: " + "; ".join(lines))
        self._sync_attachment_width()

    def _sync_attachment_width(self) -> None:
        try:
            width = max(200, self.input.width())
            self.attachments_label.setMaximumWidth(width)
        except Exception:
            pass
