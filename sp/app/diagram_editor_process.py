"""Standalone process host for PlantUML and Mermaid editor windows."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import subprocess
import sys

from PySide6.QtCore import QByteArray, QEvent, QObject, QSettings, QTimer


EDITORS = ("plantuml", "mermaid")


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
) -> subprocess.Popen:
    """Launch a local diagram editor with an independent OS application identity."""
    return subprocess.Popen(
        editor_process_command(editor, file_path, **geometry_options),
        cwd=str(Path(__file__).resolve().parents[2]),
        env=os.environ.copy(),
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

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from sp.app.ui.diagram_editor_icons import (
        configure_diagram_editor_application,
        configure_diagram_editor_process,
    )

    configure_diagram_editor_process(args.editor)
    app = QApplication(sys.argv[:1])
    configure_diagram_editor_application(app, args.editor)
    try:
        from sp.app.ui.theme import apply_qt_palette

        apply_qt_palette(app)
    except Exception:
        pass

    if args.editor == "plantuml":
        from sp.app.ui.plantuml_editor_window import PlantUMLEditorWindow

        window = PlantUMLEditorWindow(args.path, parent=None)
    else:
        from sp.app.ui.mermaid_editor_window import MermaidEditorWindow

        window = MermaidEditorWindow(args.path, parent=None)

    geometry_store = StandaloneGeometryStore(
        window,
        args.editor,
        args.geometry_scope,
        initial_geometry=args.initial_geometry,
        initial_hsplit=args.initial_hsplit,
        initial_vsplit=args.initial_vsplit,
    )
    geometry_store.restore()
    window.setWindowFlag(Qt.Window, True)
    window.setWindowFlag(Qt.Tool, False)
    window.setWindowModality(Qt.NonModal)
    window.setAttribute(Qt.WA_DeleteOnClose, True)
    window.show()
    QTimer.singleShot(0, geometry_store.restore_splitters)
    return int(app.exec())


if __name__ == "__main__":
    raise SystemExit(main())
