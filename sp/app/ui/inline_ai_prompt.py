from __future__ import annotations

from typing import Callable, Optional

import platform

from PySide6.QtCore import Qt, Signal, QTimer, QPoint
from PySide6.QtGui import QKeyEvent, QFont, QFontDatabase, QColor
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QLabel,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from sp.app import config


class InlineAIPromptInput(QTextEdit):
    sendRequested = Signal()
    dismissRequested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setPlaceholderText("Ask AI...")
        self.setAcceptRichText(False)
        self.setTabChangesFocus(False)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if event.modifiers() & Qt.ShiftModifier:
                super().keyPressEvent(event)
                return
            event.accept()
            self.sendRequested.emit()
            return
        if event.key() == Qt.Key_Escape:
            event.accept()
            self.dismissRequested.emit()
            return
        super().keyPressEvent(event)


class InlineAIPromptOverlay(QDialog):
    def __init__(
        self,
        *,
        parent: QWidget,
        on_send: Callable[[str], None],
        anchor: Optional[QPoint] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Inline AI Prompt")
        self.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setModal(False)
        self._on_send = on_send
        self._anchor = anchor
        self._focus_timer = QTimer(self)
        self._focus_timer.setInterval(150)
        self._focus_timer.timeout.connect(self._ensure_input_focus)
        self._build_ui()

    def _default_chat_font_family(self) -> str:
        if platform.system() != "Windows":
            return ""
        families = {f.lower(): f for f in QFontDatabase().families()}
        for candidate in ("Segoe UI Variable", "Segoe UI"):
            picked = families.get(candidate.lower())
            if picked:
                return picked
        return ""

    def _apply_font(self) -> None:
        font = QFont()
        family = config.load_ai_chat_font_family() or self._default_chat_font_family()
        if family:
            font.setFamily(family)
        font.setPointSize(config.load_ai_chat_font_size(13))
        try:
            self.input.setFont(font)
        except Exception:
            pass

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)

        card = QFrame(self)
        card.setObjectName("InlineAICard")
        card.setStyleSheet(
            "QFrame#InlineAICard {"
            "  background: #0b0b0b;"
            "  border: 1px solid #1f1f1f;"
            "  border-radius: 12px;"
            "}"
        )
        try:
            shadow = QGraphicsDropShadowEffect(card)
            shadow.setBlurRadius(24)
            shadow.setOffset(0, 6)
            shadow.setColor(QColor(0, 0, 0, 120))
            card.setGraphicsEffect(shadow)
        except Exception:
            pass
        outer.addWidget(card, 1)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        title = QLabel("AI Quick Prompt", card)
        title.setStyleSheet("font-weight: 600; font-size: 12px; color: #9fb7a9;")
        layout.addWidget(title)

        self.input = InlineAIPromptInput(card)
        self.input.setMinimumHeight(60)
        self.input.setFocusPolicy(Qt.StrongFocus)
        self.input.setStyleSheet(
            "padding: 6px;"
            "background: #111;"
            "color: #d6f5d6;"
            "border: 1px solid #1f1f1f;"
            "border-radius: 8px;"
        )
        self.input.sendRequested.connect(self._send)
        self.input.dismissRequested.connect(self.reject)
        layout.addWidget(self.input)

        hint = QLabel("Enter to send, Esc to cancel (Shift+Enter for newline)", card)
        hint.setStyleSheet("color: #7b8f84; font-size: 11px;")
        layout.addWidget(hint)

        self.resize(420, 140)
        self._apply_font()

    def _send(self) -> None:
        text = (self.input.toPlainText() or "").strip()
        if text:
            try:
                self._on_send(text)
            except Exception:
                pass
        self.accept()

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        if self._anchor is not None:
            try:
                self.move(self._anchor)
            except Exception:
                pass
        self._ensure_input_focus()
        self._focus_timer.start()

    def hideEvent(self, event) -> None:  # type: ignore[override]
        self._focus_timer.stop()
        super().hideEvent(event)

    def _ensure_input_focus(self) -> None:
        if not self.isVisible():
            return
        if self.input.hasFocus():
            return
        try:
            self.raise_()
            self.activateWindow()
        except Exception:
            pass
        self.input.setFocus()
