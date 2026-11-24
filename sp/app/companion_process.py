"""Independent Map, Focus and Audience processes with a private Qt local channel.

The main process owns vault access and mutations. Companions own their native
windows and application identity; closing or quitting one cannot quit StillPoint.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import uuid

from PySide6.QtCore import QObject, QProcess, QTimer, Signal, Qt, QEvent, QEventLoop
from PySide6.QtNetwork import QLocalServer, QLocalSocket

KINDS = ("mindmap", "focus", "audience")


def companion_command(kind: str, endpoint: str) -> list[str]:
    if kind not in KINDS:
        raise ValueError(f"Unknown companion: {kind}")
    if getattr(sys, "frozen", False):
        return [sys.executable, "--companion", kind, "--endpoint", endpoint]
    return [sys.executable, "-m", "sp.app.companion_process",
            "--companion", kind, "--endpoint", endpoint]


class Channel(QObject):
    received = Signal(dict)

    def __init__(self, socket, parent=None):
        super().__init__(parent)
        self.socket = socket
        self.buffer = bytearray()
        socket.readyRead.connect(self.read)

    def send(self, message):
        self.socket.write((json.dumps(message, ensure_ascii=True) + "\n").encode())
        self.socket.flush()

    def read(self):
        self.buffer.extend(bytes(self.socket.readAll()))
        while b"\n" in self.buffer:
            raw, _, rest = self.buffer.partition(b"\n")
            self.buffer = bytearray(rest)
            try:
                message = json.loads(raw)
                if isinstance(message, dict):
                    self.received.emit(message)
            except (ValueError, UnicodeError):
                continue


class CompanionWindow(QObject):
    """Main-process handle, retaining the small ModeWindow interface callers use."""
    ready = Signal()
    closed = Signal(str, int)
    exited = Signal()
    headingActivated = Signal(str, int)
    headingCreateRequested = Signal(str, int, int, str)
    headingRenameRequested = Signal(str, int, int, str)
    headingReorderRequested = Signal(str, str, str, int)

    def __init__(self, owner, kind: str):
        super().__init__(owner)
        from sp.app.ui.markdown_editor import MarkdownEditor
        self.owner = owner
        self.mode = kind
        self._page_path = owner.current_path
        self._read_only = owner._read_only
        self._baseline = owner.editor.to_markdown()
        self.editor = MarkdownEditor()
        self.editor.set_markdown(self._baseline)
        self.editor.set_read_only_mode(self._read_only)
        self._cursor = owner.editor.textCursor().position()
        self._finished = False
        self._closing = False
        self._receiving = False
        self.channel = None
        self._menu = None
        self._actions = {}
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.UserAccessOption)
        # Short enough for macOS's Unix-domain socket path limit.
        self.endpoint = str(Path(tempfile.gettempdir()) / f"sp-{uuid.uuid4().hex[:16]}")
        if not self.server.listen(self.endpoint):
            raise RuntimeError(self.server.errorString())
        self.server.newConnection.connect(self._connected)
        self.process = QProcess(self)
        command = companion_command(kind, self.endpoint)
        self.process.setProgram(command[0])
        self.process.setArguments(command[1:])
        self.process.setWorkingDirectory(str(Path(__file__).resolve().parents[2]))
        self.process.setProcessChannelMode(QProcess.ForwardedChannels)
        self.process.finished.connect(self._process_finished)
        self.process.errorOccurred.connect(self._process_error)
        self.startup_timer = QTimer(self)
        self.startup_timer.setSingleShot(True)
        self.startup_timer.setInterval(30000)
        self.startup_timer.timeout.connect(self._startup_timeout)
        self.editor.textChanged.connect(self._mirror_changed)
        self.headingActivated.connect(owner._open_heading_from_map)
        self.headingCreateRequested.connect(owner._insert_heading_from_map_request)
        self.headingRenameRequested.connect(owner._rename_heading_from_map_request)
        self.headingReorderRequested.connect(owner._reorder_headings_from_map_request)
        if not hasattr(owner, "_companion_windows"):
            owner._companion_windows = []
        owner._companion_windows.append(self)

    def show(self):
        self.process.start()
        self.startup_timer.start()

    def snapshot(self):
        from sp.app import config
        return dict(type="init", kind=self.mode, path=self._page_path,
                    text=self.editor.to_markdown(), cursor=self._cursor,
                    vault_root=self.owner.vault_root, active_vault=config.get_active_vault(),
                    read_only=self._read_only, headings=list(self.owner._toc_headings or []),
                    settings=(config.load_focus_mode_settings() if self.mode == "focus"
                              else config.load_audience_mode_settings()),
                    geometry_scope=str(self.owner.vault_root),
                    screen=self.owner.screen().name(), font=self.owner.editor.font().toString())

    def _connected(self):
        socket = self.server.nextPendingConnection()
        if self.channel is not None:
            socket.abort()
            socket.deleteLater()
            return
        self.channel = Channel(socket, self)
        self.channel.received.connect(self._message)
        self.server.close()
        self.channel.send(self.snapshot())
        if self._closing:
            self.channel.send(dict(type="close"))

    def _message(self, message):
        if self._finished:
            return
        kind = message.get("type")
        if kind == "ready":
            self.startup_timer.stop()
            self.application_name = message.get("application", "")
            self.has_application_icon = bool(message.get("has_icon"))
            self.ready.emit()
        elif kind in {"edit", "closed"}:
            if self.mode != "mindmap":
                self._cursor = int(message.get("cursor", self._cursor))
            if not self._read_only and self.mode != "mindmap":
                self._receiving = True
                try:
                    text = message["text"]
                    if self.editor.to_markdown() != text:
                        self.editor.set_markdown(text)
                finally:
                    self._receiving = False
            if kind == "closed":
                self._finish()
        elif kind == "refresh":
            self.refresh()
        elif kind == "map_action":
            signals = {"activate": self.headingActivated, "create": self.headingCreateRequested,
                       "rename": self.headingRenameRequested, "reorder": self.headingReorderRequested}
            signal = signals.get(message.get("action"))
            if signal is not None:
                self._activation_source = message.get("source")
                signal.emit(*message["args"])
                if message.get("action") == "activate" and self._activation_source == "keyboard":
                    self.owner.raise_()
                    self.owner.activateWindow()
        elif kind == "status":
            self.owner.statusBar().showMessage(message["text"], message.get("duration", 3000))
        elif kind == "task_menu":
            self._build_task_menu(message)
        elif kind == "task_action":
            action = self._actions.get(message.get("action"))
            if action is not None:
                action.trigger()
                self._reload_after_task_mutation()

    def _mirror_changed(self):
        if not self._receiving and self.channel is not None:
            self.channel.send(dict(type="replace", text=self.editor.to_markdown()))

    def _commit(self):
        if self._read_only or self.mode == "mindmap":
            return
        text = self.editor.to_markdown()
        current = self.owner.editor.to_markdown()
        if self.owner.current_path == self._page_path and current in {self._baseline, text}:
            if current != text:
                self.owner.editor.set_markdown(text)
                self.owner.editor.document().setModified(True)
            self._baseline = text
        elif text != self._baseline:
            # Preserve both buffers if someone edited/navigated the main editor
            # while a companion was running, including after a process crash.
            fd, path = tempfile.mkstemp(prefix="StillPoint-recovered-", suffix=".md")
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                output.write(text)
            self.owner._alert(f"The main page changed while {self.mode.title()} was open. "
                              f"Its edits were preserved at {path}")
            self._baseline = text

    def _save_task_buffer(self):
        self._commit()
        if self.owner.current_path != self._page_path or self.owner.editor.to_markdown() != self.editor.to_markdown():
            return False
        self.owner._save_current_file(auto=True, reason="companion task action", force=True,
                                      allow_when_suspended=True)
        return not (self.owner._dirty_flag or self.owner.editor.document().isModified())

    def _reload_after_task_mutation(self):
        if self.owner.current_path != self._page_path:
            return
        self.owner._refresh_current_saved_state(force=True)
        text = self.owner.editor.to_markdown()
        self._baseline = text
        self.editor.set_markdown(text)
        self._mirror_changed()

    def _build_task_menu(self, message):
        from PySide6.QtCore import QPoint
        if self._menu is not None:
            self._menu.deleteLater()
        self._menu = self.owner._main_editor_task_context_menu(
            message["line"], QPoint(*message["anchor"]), editor=self.editor,
            page_path=self._page_path, parent=self.owner, prepare=self._save_task_buffer)
        self._actions = {}

        def describe(menu):
            result = []
            for action in menu.actions():
                key = str(len(self._actions))
                self._actions[key] = action
                result.append(dict(id=key, text=action.text(), enabled=action.isEnabled(),
                                   separator=action.isSeparator(), checked=action.isChecked(),
                                   checkable=action.isCheckable(),
                                   children=describe(action.menu()) if action.menu() else None))
            return result

        self.channel.send(dict(type="task_menu_result", items=describe(self._menu) if self._menu else []))

    def consume_activation_source(self):
        return getattr(self, "_activation_source", None)

    def refresh(self, *, focus=False, line=None):
        if self.mode != "mindmap" or self.channel is None:
            return
        path = self.owner.current_path
        self.channel.send(dict(type="map_content", path=path,
                               text=self.owner._get_editor_text_for_path(path) if path else "",
                               focus=focus, line=line))

    def close(self):
        self._closing = True
        if self.channel is not None and not self._finished:
            self.channel.send(dict(type="close"))
        return True

    def _finish(self):
        if self._finished:
            return
        self._finished = True
        self.startup_timer.stop()
        self._commit()
        self.closed.emit(self.mode, self._cursor)

    def _process_finished(self, *_):
        if self.channel is not None:
            self.channel.read()
        if not self._finished and self.mode != "mindmap":
            self.owner.statusBar().showMessage(
                f"{self.mode.title()} exited unexpectedly; its last received edits were restored.", 5000)
        self._finish()
        self.server.close()
        if self in self.owner._companion_windows:
            self.owner._companion_windows.remove(self)
        self.exited.emit()
        self.editor.deleteLater()
        self.deleteLater()

    def _process_error(self, error):
        if error == QProcess.FailedToStart:
            self.owner._alert(f"Unable to open {self.mode.title()}: {self.process.errorString()}")
            self._process_finished()

    def _startup_timeout(self):
        self.owner._alert(f"{self.mode.title()} did not finish starting.")
        self.process.kill()


class CompanionHost(QObject):
    def __init__(self, app, channel):
        super().__init__(app)
        self.app = app
        self.channel = channel
        self.window = None
        self._replacing = False
        self._last_text = ""
        self._menu_items = None
        self._menu_loop = None
        self._closed = False
        channel.received.connect(self.message)
        channel.socket.disconnected.connect(self.disconnected)
        app.aboutToQuit.connect(self.flush_final)

    def message(self, message):
        kind = message.get("type")
        if kind == "init":
            self.build(message)
        elif kind == "task_menu_result":
            self._menu_items = message["items"]
            if self._menu_loop is not None:
                self._menu_loop.quit()
        elif self.window is not None:
            if kind == "close":
                self.window.close()
            elif kind == "replace" and self.kind != "mindmap":
                self._replacing = True
                try:
                    editor = self.window.editor
                    cursor = editor.textCursor().position()
                    editor.set_markdown(message["text"])
                    from PySide6.QtGui import QTextCursor
                    restored = editor.textCursor()
                    restored.setPosition(min(cursor, editor.document().characterCount() - 1))
                    editor.setTextCursor(restored)
                    self._last_text = editor.to_markdown()
                    self.window._overlay_initial_markdown = self._last_text
                    self.window._base_markdown_on_open = self._last_text
                    if self.window._reading_toggle.isChecked():
                        self.window._set_reading_view(True, persist=False)
                finally:
                    self._replacing = False
            elif kind == "map_content":
                self.set_map_content(message)

    def build(self, state):
        from PySide6.QtGui import QFont, QKeySequence, QShortcut
        from PySide6.QtWidgets import QMainWindow
        from sp.app import config
        from sp.app.ui.diagram_editor_icons import configure_diagram_editor_application
        self.kind = state["kind"]
        if state.get("active_vault"):
            # Do not change the user's persisted startup-vault preference.
            self._vault_context = config.push_active_vault_context(state["active_vault"])
        from sp.app.ui.theme import apply_qt_palette
        apply_qt_palette(self.app)
        configure_diagram_editor_application(self.app, self.kind)
        self.app.setQuitOnLastWindowClosed(True)
        if self.kind == "mindmap":
            from sp.app.ui.map_panel import MapPanel
            self.panel = MapPanel()
            self.window = QMainWindow()
            self.window.setWindowTitle("StillPoint Map")
            self.window.setWindowIcon(self.app.windowIcon())
            self.window.setCentralWidget(self.panel)
            self.window.setFocusProxy(self.panel.preview_label)
            self.panel.closeWindowRequested.connect(self.window.close)
            self.panel.windowModeToggleRequested.connect(self.toggle_map_mode)
            self.panel.focusSyncRequested.connect(lambda: self.channel.send(dict(type="refresh")))
            self.panel.statusMessageRequested.connect(
                lambda text, duration: self.channel.send(dict(type="status", text=text, duration=duration)))
            for signal, action in ((self.panel.headingActivated, "activate"),
                                   (self.panel.headingCreateRequested, "create"),
                                   (self.panel.headingRenameRequested, "rename"),
                                   (self.panel.headingReorderRequested, "reorder")):
                signal.connect(lambda *args, action=action: self.channel.send(
                    dict(type="map_action", action=action, args=args,
                         source=self.panel.consume_activation_source() if action == "activate" else None)))
            self.panel.set_window_mode_control(visible=True, windowed=False)
            self.window.resize(820, 720)
            from sp.app.diagram_editor_process import StandaloneGeometryStore
            self.geometry = StandaloneGeometryStore(self.window, self.kind, state["geometry_scope"])
            self.geometry.restore()
            self.set_map_content(dict(state, focus=True, line=state.get("line", 1)))
        else:
            from sp.app.ui.markdown_editor import MarkdownEditor
            from sp.app.ui.mode_window import ModeWindow
            self.base = MarkdownEditor()
            font = QFont()
            font.fromString(state["font"])
            self.base.setFont(font)
            self.base.set_markdown(state["text"])
            self.window = ModeWindow(self.kind, self.base, vault_root=state["vault_root"],
                                     page_path=state["path"], read_only=state["read_only"],
                                     heading_provider=lambda: state["headings"], settings=state["settings"],
                                     initial_cursor=state["cursor"])
            self.window.editor.task_context_menu_provider = self.task_menu
            self.window._task_context_menu = self.task_menu
            self._last_text = self.window.editor.to_markdown()
            self.window.editor.textChanged.connect(self.send_edit)
            self.window.closed.connect(self.closed)
        screen = next((s for s in self.app.screens() if s.name() == state["screen"]), None)
        if screen is not None:
            self.window.setScreen(screen)
        self.window.installEventFilter(self)
        for sequence in ("Ctrl+W", "Ctrl+Q"):
            shortcut = QShortcut(QKeySequence(sequence), self.window)
            shortcut.activated.connect(self.window.close)
        self.window.showFullScreen()
        QTimer.singleShot(0, lambda: self.channel.send(dict(
            type="ready", application=self.app.applicationName(),
            has_icon=not self.app.windowIcon().isNull())))

    def send_edit(self):
        if self._replacing:
            return
        text = self.window.editor.to_markdown()
        if text != self._last_text:
            self._last_text = text
            self.channel.send(dict(type="edit", text=text,
                                   cursor=self.window.editor.textCursor().position()))

    def closed(self, _mode, position):
        self._closed = True
        self.channel.send(dict(type="closed", text=self.window.editor.to_markdown(), cursor=position))
        self.channel.socket.waitForBytesWritten(1000)

    def flush_final(self):
        """Native application Quit can bypass the window's close event."""
        if self._closed or self.window is None or self.channel.socket.state() != QLocalSocket.ConnectedState:
            return
        if self.kind == "mindmap":
            self._closed = True
            self.channel.send(dict(type="closed"))
            self.channel.socket.waitForBytesWritten(1000)
        else:
            self.closed(self.kind, self.window.editor.textCursor().position())

    def disconnected(self):
        if not self._closed and self.window is not None and self.kind != "mindmap":
            # The owner crashed/quit. Keep unsaved companion edits recoverable.
            text = self.window.editor.to_markdown()
            if text != self.window._overlay_initial_markdown:
                fd, path = tempfile.mkstemp(prefix="StillPoint-recovered-", suffix=".md")
                with os.fdopen(fd, "w", encoding="utf-8") as output:
                    output.write(text)
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.information(self.window, "Recovered edits", f"Your edits were preserved at {path}")
        self.app.quit()

    def toggle_map_mode(self):
        if self.window.isFullScreen():
            self.window.showNormal()
        else:
            self.window.showFullScreen()
        self.panel.set_window_mode_control(visible=True, windowed=not self.window.isFullScreen())

    def set_map_content(self, message):
        if message.get("path"):
            self.panel.set_content(message["path"], message["text"])
        else:
            self.panel.clear_content()
        if message.get("focus"):
            self.window.setWindowState(self.window.windowState() & ~Qt.WindowMinimized)
            self.window.show()
            self.window.raise_()
            self.window.activateWindow()
            QTimer.singleShot(0, self.window, lambda: self.focus_map(message.get("line")))

    def focus_map(self, line):
        if line is not None:
            self.panel.focus_heading_at_line(line)
        self.panel.preview_label.setFocus(Qt.ActiveWindowFocusReason)

    def eventFilter(self, watched, event):
        if watched is self.window:
            if event.type() == QEvent.WindowActivate and self.kind == "mindmap":
                self.channel.send(dict(type="refresh"))
                QTimer.singleShot(50, self.window, lambda: self.focus_map(None))
            elif event.type() == QEvent.Close and self.kind == "mindmap":
                self._closed = True
                self.channel.send(dict(type="closed"))
                self.channel.socket.waitForBytesWritten(1000)
        return False

    def task_menu(self, block_number, anchor):
        from PySide6.QtWidgets import QMenu
        self.send_edit()
        self._menu_items = None
        self._menu_loop = QEventLoop()
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(self._menu_loop.quit)
        self.channel.send(dict(type="task_menu", line=block_number, anchor=[anchor.x(), anchor.y()]))
        timer.start(5000)
        self._menu_loop.exec()
        timer.stop()
        timer.deleteLater()
        self._menu_loop = None
        if not self._menu_items:
            return None

        def populate(menu, items):
            for item in items:
                if item["separator"]:
                    menu.addSeparator()
                elif item["children"] is not None:
                    submenu = menu.addMenu(item["text"])
                    submenu.setEnabled(item["enabled"])
                    populate(submenu, item["children"])
                else:
                    action = menu.addAction(item["text"])
                    action.setEnabled(item["enabled"])
                    action.setCheckable(item["checkable"])
                    action.setChecked(item["checked"])
                    action.triggered.connect(lambda _checked=False, key=item["id"]:
                                             self.channel.send(dict(type="task_action", action=key)))
        menu = QMenu(self.window)
        populate(menu, self._menu_items)
        return menu


def main(argv=None):
    parser = argparse.ArgumentParser(description="StillPoint companion window")
    parser.add_argument("--companion", choices=KINDS, required=True)
    parser.add_argument("--endpoint", required=True)
    args = parser.parse_args(argv)
    from PySide6.QtWidgets import QApplication
    from sp.app.ui.diagram_editor_icons import configure_diagram_editor_process
    configure_diagram_editor_process(args.companion)
    app = QApplication(sys.argv[:1])
    socket = QLocalSocket(app)
    channel = Channel(socket, app)
    host = CompanionHost(app, channel)
    socket.connectToServer(args.endpoint)
    if not socket.waitForConnected(10000):
        return 1
    return int(app.exec())


if __name__ == "__main__":
    raise SystemExit(main())
