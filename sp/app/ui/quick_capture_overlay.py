from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent, QColor
from PySide6.QtWidgets import QDialog, QFrame, QLabel, QTextEdit, QVBoxLayout, QGraphicsDropShadowEffect


class QuickCaptureInput(QTextEdit):
    captureRequested = Signal()
    dismissRequested = Signal()

    def __init__(self, parent: Optional[QDialog] = None) -> None:
        super().__init__(parent)
        self.setPlaceholderText("Type your thought...")
        self.setAcceptRichText(False)
        self.setTabChangesFocus(False)

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


class QuickCaptureOverlay(QDialog):
    def __init__(
        self,
        *,
        parent,
        on_capture: Callable[[str], None],
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Quick Capture")
        self.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setModal(False)
        self._on_capture = on_capture
        self._build_ui()

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

        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        self.input = QuickCaptureInput(card)
        self.input.setMinimumHeight(90)
        self.input.setStyleSheet(
            "font-size: 18px; color: white; background: rgba(255, 255, 255, 0.08);"
            "border: 1px solid rgba(255, 255, 255, 0.5); padding: 8px; border-radius: 6px;"
        )
        self.input.captureRequested.connect(self._capture)
        self.input.dismissRequested.connect(self.reject)
        layout.addWidget(self.input)

        hint = QLabel("Enter to capture, Esc to dismiss", card)
        hint.setStyleSheet("color: #dfe6fa; font-size: 12px;")
        layout.addWidget(hint)

    def _capture(self) -> None:
        text = (self.input.toPlainText() or "").strip()
        if text:
            try:
                self._on_capture(text)
            except Exception:
                pass
        self.accept()
