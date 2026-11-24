"""Standalone process host for PlantUML and Mermaid editor windows."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from PySide6.QtCore import QByteArray, QEvent, QLockFile, QObject, QSettings, QTimer, Qt
from PySide6.QtNetwork import QLocalServer, QLocalSocket


EDITORS = ("plantuml", "mermaid")


def _diagram_endpoint(editor: str, file_path: str | Path) -> str:
    if editor not in EDITORS:
        raise ValueError(f"Unsupported standalone diagram editor: {editor}")
    path = os.path.normcase(str(Path(file_path).expanduser().resolve()))
    key = hashlib.sha256(f"{Path.home()}\0{editor}\0{path}".encode("utf-8")).hexdigest()[:24]
    return str(Path(tempfile.gettempdir()) / f"sp-diagram-{key}")


def activate_existing_diagram(editor: str, file_path: str | Path) -> bool:
    """Ask the process owning this diagram to show its existing window."""
    endpoint = _diagram_endpoint(editor, file_path)
    if sys.platform == "win32":
        try:
            import ctypes

            pid, _, _ = QLockFile(endpoint + ".lock").getLockInfo()
            if pid > 0:
                ctypes.windll.user32.AllowSetForegroundWindow(pid)
        except (AttributeError, OSError, ValueError):
            pass
    socket = QLocalSocket()
    socket.connectToServer(endpoint)
    if not socket.waitForConnected(150):
        return False
    socket.disconnectFromServer()
    return True


class DiagramEditorInstance(QObject):
    """Reserve one editor per file, including while its window is starting."""

    def __init__(self, editor: str, file_path: str | Path):
        super().__init__()
        self.editor = editor
        self.file_path = file_path
        self.endpoint = _diagram_endpoint(editor, file_path)
        self.lock = QLockFile(self.endpoint + ".lock")
        self.lock.setStaleLockTime(0)
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.UserAccessOption)
        self.server.newConnection.connect(self._activate)
        self.window = None
        self._activation_pending = False

    def claim(self) -> bool:
        if not self.lock.tryLock(0):
            # Another process may have claimed the file just before opening
            # its socket. Wait briefly for that startup rather than duplicate it.
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                if activate_existing_diagram(self.editor, self.file_path):
                    return False
                time.sleep(0.02)
            raise RuntimeError("This diagram is already opening or its editor is not responding.")
        # Only the lock owner may clear a socket left behind by a crashed editor.
        QLocalServer.removeServer(self.endpoint)
        if not self.server.listen(self.endpoint):
            self.lock.unlock()
            raise RuntimeError(self.server.errorString())
        return True

    def bind(self, window):
        self.window = window
        self.setParent(window)
        window.destroyed.connect(self.close)
        if self._activation_pending:
            self._show_window()

    def _activate(self):
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            if socket is not None:
                socket.disconnectFromServer()
                socket.deleteLater()
            self._activation_pending = True
        if self.window is not None:
            self._show_window()

    def _show_window(self):
        self._activation_pending = False
        self.window.setWindowState(self.window.windowState() & ~Qt.WindowMinimized)
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()
        if sys.platform == "win32":
            try:
                import ctypes

                ctypes.windll.user32.SetForegroundWindow(int(self.window.winId()))
            except (AttributeError, OSError, ValueError):
                pass

    def close(self):
        self.server.close()
        self.lock.unlock()


def _enable_windows_mermaid_web_preview(env) -> None:
    env.pop("SP_MERMAID_DISABLE_INPROCESS_WEBENGINE", None)
    env.pop("SP_DISABLE_MERMAID_WEB_PREVIEW", None)
    env["SP_ENABLE_MERMAID_WEB_PREVIEW"] = "1"


class StandaloneGeometryStore(QObject):
    """Persist standalone editor layout without requiring an active vault DB."""

    def __init__(
        self,
        window,
        editor: str,
        scope_name: str,
        *,
        initial_geometry: str | None = None,
        initial_hsplit: str | None = None,
        initial_vsplit: str | None = None,
        settings: QSettings | None = None,
    ) -> None:
        super().__init__(window)
        self.window = window
        scope = hashlib.sha256(scope_name.encode("utf-8")).hexdigest()[:16]
        self.prefix = f"diagramEditors/{editor}/{scope}"
        self.settings = settings if settings is not None else QSettings(
            "StillPoint", "StillPoint"
        )
        self.initial_geometry = initial_geometry
        self.initial_hsplit = initial_hsplit
        self.initial_vsplit = initial_vsplit
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(400)
        self.timer.timeout.connect(self.save)
        window.installEventFilter(self)
        horizontal = getattr(window, "editor_preview_splitter", None)
        if horizontal is not None:
            horizontal.splitterMoved.connect(lambda *_: self.timer.start())
        vertical = getattr(window, "_vertical_splitter", None)
        if vertical is not None:
            vertical.splitterMoved.connect(lambda *_: self.timer.start())

    @staticmethod
    def _bytes(value) -> QByteArray:
        if isinstance(value, QByteArray):
            return value
        if value:
            return QByteArray.fromBase64(str(value).encode("ascii"))
        return QByteArray()

    def restore(self) -> None:
        geometry = self.settings.value(f"{self.prefix}/geometry", "")
        geometry = geometry or self.initial_geometry
        if geometry:
            self.window.restoreGeometry(self._bytes(geometry))

    def restore_splitters(self) -> None:
        horizontal = self.settings.value(f"{self.prefix}/hsplit", "")
        horizontal = horizontal or self.initial_hsplit
        if horizontal and hasattr(self.window, "editor_preview_splitter"):
            self.window.editor_preview_splitter.restoreState(self._bytes(horizontal))
        vertical = self.settings.value(f"{self.prefix}/vsplit", "")
        vertical = vertical or self.initial_vsplit
        splitter = getattr(self.window, "_vertical_splitter", None)
        if vertical and splitter is not None:
            splitter.restoreState(self._bytes(vertical))
        header = getattr(self.window, "header_controls", None)
        if header is not None:
            header.restore_visibility()

    def save(self) -> None:
        try:
            self.settings.setValue(
                f"{self.prefix}/geometry", self.window.saveGeometry()
            )
            if hasattr(self.window, "editor_preview_splitter"):
                self.settings.setValue(
                    f"{self.prefix}/hsplit",
                    self.window.editor_preview_splitter.saveState(),
                )
            splitter = getattr(self.window, "_vertical_splitter", None)
            if splitter is not None:
                self.settings.setValue(
                    f"{self.prefix}/vsplit", splitter.saveState()
                )
            self.settings.sync()
        except RuntimeError:
            pass

    def eventFilter(self, watched, event):  # type: ignore[override]
        if watched is self.window:
            if event.type() in (QEvent.Move, QEvent.Resize):
                self.timer.start()
            elif event.type() == QEvent.Close:
                self.timer.stop()
                self.save()
        return False


def editor_process_command(
    editor: str,
    file_path: str | Path,
    *,
    geometry_scope: str = "default",
    initial_geometry: str | None = None,
    initial_hsplit: str | None = None,
    initial_vsplit: str | None = None,
) -> list[str]:
    """Build a source-tree or frozen command for a standalone editor."""
    if editor not in EDITORS:
        raise ValueError(f"Unsupported standalone diagram editor: {editor}")
    resolved_path = str(Path(file_path).resolve())
    frozen = bool(getattr(sys, "frozen", False))
    if frozen:
        command = [
            sys.executable,
            "--diagram-editor",
            editor,
            "--diagram-editor-path",
            resolved_path,
        ]
    else:
        command = [
            sys.executable,
            "-m",
            "sp.app.diagram_editor_process",
            "--editor",
            editor,
            "--path",
            resolved_path,
        ]
    option_prefix = "--diagram-editor-" if frozen else "--"
    command.extend((f"{option_prefix}geometry-scope", geometry_scope))
    for name, value in (
        ("initial-geometry", initial_geometry),
        ("initial-hsplit", initial_hsplit),
        ("initial-vsplit", initial_vsplit),
    ):
        if value:
            command.extend((f"{option_prefix}{name}", value))
    return command


def launch_diagram_editor_process(
    editor: str,
    file_path: str | Path,
    **geometry_options,
) -> subprocess.Popen | None:
    """Launch a local diagram editor with an independent OS application identity."""
    if activate_existing_diagram(editor, file_path):
        return None
    env = os.environ.copy()
    if editor == "mermaid" and sys.platform == "win32":
        # Folder Navigator disables WebEngine in its own process. The Mermaid
        # editor is isolated specifically so it can safely use WebEngine.
        _enable_windows_mermaid_web_preview(env)
    return subprocess.Popen(
        editor_process_command(editor, file_path, **geometry_options),
        cwd=str(Path(__file__).resolve().parents[2]),
        env=env,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="StillPoint standalone diagram editor")
    parser.add_argument("--editor", required=True, choices=EDITORS)
    parser.add_argument("--path", required=True)
    parser.add_argument("--geometry-scope", default="default")
    parser.add_argument("--initial-geometry")
    parser.add_argument("--initial-hsplit")
    parser.add_argument("--initial-vsplit")
    args = parser.parse_args(argv)

    if args.editor == "mermaid" and sys.platform == "win32":
        _enable_windows_mermaid_web_preview(os.environ)

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from sp.app.ui.diagram_editor_icons import (
        configure_diagram_editor_application,
        configure_diagram_editor_process,
    )

    configure_diagram_editor_process(args.editor)
    app = QApplication(sys.argv[:1])
    instance = DiagramEditorInstance(args.editor, args.path)
    if not instance.claim():
        return 0
    app.aboutToQuit.connect(instance.close)
    configure_diagram_editor_application(app, args.editor)
    try:
        from sp.app.ui.theme import apply_qt_palette

        apply_qt_palette(app)
    except Exception:
        pass

    if args.editor == "plantuml":
        from sp.app.ui.plantuml_editor_window import PlantUMLEditorWindow

        window = PlantUMLEditorWindow(args.path, parent=None, settings_scope=args.geometry_scope)
    else:
        from sp.app.ui.mermaid_editor_window import MermaidEditorWindow

        window = MermaidEditorWindow(args.path, parent=None, settings_scope=args.geometry_scope)

    geometry_store = StandaloneGeometryStore(
        window,
        args.editor,
        args.geometry_scope,
        initial_geometry=args.initial_geometry,
        initial_hsplit=args.initial_hsplit,
        initial_vsplit=args.initial_vsplit,
    )
    geometry_store.restore()
    instance.bind(window)
    window.setWindowFlag(Qt.Window, True)
    window.setWindowFlag(Qt.Tool, False)
    window.setWindowModality(Qt.NonModal)
    window.setAttribute(Qt.WA_DeleteOnClose, True)
    window.show()
    QTimer.singleShot(0, geometry_store.restore_splitters)
    return int(app.exec())


if __name__ == "__main__":
    raise SystemExit(main())
