from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QToolButton,
    QVBoxLayout,
    QTextEdit,
    QTextBrowser,
    QWidget,
)

from sp.app import config
from .agent_tool_loop import AgentLoopConfig, AgentToolLoopWorker, DEFAULT_AGENT_SYSTEM_PROMPT


class AgentToolInput(QTextEdit):
    runRequested = Signal()
    acceptRequested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setPlaceholderText("Ask the agent…  (Ctrl+Enter to run)")
        self.setAcceptRichText(False)
        self.setTabChangesFocus(False)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and (event.modifiers() & Qt.ControlModifier):
            event.accept()
            self.runRequested.emit()
            return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not (event.modifiers() & Qt.ControlModifier):
            if not (self.toPlainText() or "").strip():
                event.accept()
                self.acceptRequested.emit()
                return
        super().keyPressEvent(event)


class AgentToolOverlay(QDialog):
    def __init__(
        self,
        *,
        parent: QWidget,
        server_config: dict,
        model: str,
        http_client,
        vault_key: str,
        current_path: str,
        on_accept: Callable[[str], None],
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Agent Tools")
        self.setModal(False)
        self._server_config = server_config
        self._model = model
        self._http_client = http_client
        self._vault_key = vault_key
        self._current_path = current_path
        self._on_accept = on_accept
        self._worker: Optional[AgentToolLoopWorker] = None
        self._last_answer = ""
        self._live_events: list[str] = []
        self._build_ui()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        card = QFrame(self)
        card.setFrameShape(QFrame.NoFrame)
        outer.addWidget(card, 1)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        title_row = QHBoxLayout()
        title = QLabel("Agent")
        title.setStyleSheet("font-weight: 600; font-size: 13px; color: #666;")
        title_row.addWidget(title)
        title_row.addStretch(1)
        self._status = QLabel("")
        self._status.setStyleSheet("color: #666; font-size: 12px;")
        title_row.addWidget(self._status)
        self._toggle_tools_btn = QToolButton(self)
        self._toggle_tools_btn.setText("Tools")
        self._toggle_tools_btn.setCheckable(True)
        self._toggle_tools_btn.setChecked(False)
        self._toggle_tools_btn.clicked.connect(self._toggle_tools_panel)
        title_row.addWidget(self._toggle_tools_btn)
        layout.addLayout(title_row)

        self.chat_view = QTextBrowser(self)
        self.chat_view.setOpenExternalLinks(False)
        self.chat_view.setOpenLinks(False)
        self.chat_view.setStyleSheet(
            "QTextBrowser { border: 1px solid #2a2a2a; border-radius: 6px; padding: 8px; }"
        )
        layout.addWidget(self.chat_view, 1)

        self.tool_log = QPlainTextEdit(self)
        self.tool_log.setReadOnly(True)
        self.tool_log.setVisible(False)
        self.tool_log.setStyleSheet(
            "QPlainTextEdit { border: 1px dashed #333; border-radius: 6px; padding: 6px; color: #666; }"
        )
        layout.addWidget(self.tool_log, 0)

        self.input = AgentToolInput(self)
        self.input.setFixedHeight(70)
        self.input.runRequested.connect(self._run_agent)
        self.input.acceptRequested.connect(self._accept_last)
        layout.addWidget(self.input)

        button_row = QHBoxLayout()
        self._run_btn = QPushButton("Run", self)
        self._run_btn.clicked.connect(self._run_agent)
        self._accept_btn = QPushButton("Insert", self)
        self._accept_btn.clicked.connect(self._accept_last)
        self._close_btn = QPushButton("Close", self)
        self._close_btn.clicked.connect(self.close)
        button_row.addStretch(1)
        button_row.addWidget(self._run_btn)
        button_row.addWidget(self._accept_btn)
        button_row.addWidget(self._close_btn)
        layout.addLayout(button_row)

    def _toggle_tools_panel(self) -> None:
        visible = self._toggle_tools_btn.isChecked()
        self.tool_log.setVisible(visible)

    def _append_tool_log(self, text: str) -> None:
        if not text:
            return
        self.tool_log.appendPlainText(text)
        self._live_events.append(text)
        preview = "\n".join(self._live_events[-30:])
        self.chat_view.setPlainText(f"Running agent tools...\n\n{preview}")

    def _set_status(self, text: str) -> None:
        self._status.setText(text or "")

    def _ensure_approval(self) -> bool:
        if config.is_agent_tool_approved(self._vault_key):
            return True
        choice = QMessageBox.question(
            self,
            "Enable Agent Tools",
            "Allow agent tools for this vault? This will let the agent read vault pages.\n"
            "You will only be asked once per vault.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if choice == QMessageBox.Yes:
            config.approve_agent_tool_for_vault(self._vault_key)
            return True
        return False

    def _run_agent(self) -> None:
        if self._worker is not None:
            return
        prompt = (self.input.toPlainText() or "").strip()
        if not prompt:
            return
        if not self._ensure_approval():
            return
        self._set_status("Running…")
        self._run_btn.setEnabled(False)
        self._accept_btn.setEnabled(False)
        self._live_events = []
        self.chat_view.setPlainText("Running agent tools...")
        config_obj = AgentLoopConfig(
            server_config=self._server_config,
            model=self._model,
            system_prompt=DEFAULT_AGENT_SYSTEM_PROMPT,
        )
        self._worker = AgentToolLoopWorker(
            config=config_obj,
            client=self._http_client,
            user_prompt=prompt,
            context={"current_path": self._current_path},
        )
        self._worker.toolLog.connect(self._append_tool_log)
        self._worker.finished.connect(self._handle_finished)
        self._worker.failed.connect(self._handle_failed)
        self._worker.start()

    def _handle_finished(self, text: str) -> None:
        self._last_answer = text or ""
        if self._live_events:
            log = "\n".join(self._live_events[-30:])
            self.chat_view.setPlainText(f"{log}\n\nFinal:\n{self._last_answer}")
        else:
            self.chat_view.setPlainText(self._last_answer)
        self._set_status("Done")
        self._run_btn.setEnabled(True)
        self._accept_btn.setEnabled(True)
        self._worker = None

    def _handle_failed(self, err: str) -> None:
        self._set_status("Failed")
        if self._live_events:
            log = "\n".join(self._live_events[-30:])
            self.chat_view.setPlainText(f"{log}\n\nError: {err}")
        else:
            self.chat_view.setPlainText(f"Error: {err}")
        self._run_btn.setEnabled(True)
        self._accept_btn.setEnabled(True)
        self._worker = None

    def _accept_last(self) -> None:
        text = (self._last_answer or "").strip()
        if not text:
            return
        try:
            self._on_accept(text)
        except Exception:
            pass
        self.close()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self._worker is not None:
            try:
                self._worker.request_cancel()
            except Exception:
                pass
        super().closeEvent(event)
