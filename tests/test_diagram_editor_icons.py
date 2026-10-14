from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from sp.app.ui import diagram_editor_icons as icons


@pytest.mark.parametrize(
    ("platform", "expected_name"),
    [
        ("win32", "Mermaid.ico"),
        ("darwin", "Mermaid.icns"),
        ("linux", "mermaid-512x512.png"),
    ],
)
def test_editor_icon_path_prefers_native_platform_format(
        tmp_path: Path, monkeypatch, platform: str, expected_name: str) -> None:
    icon_dir = tmp_path / "sp" / "assets" / "icons"
    png_dir = icon_dir / "linux-png"
    png_dir.mkdir(parents=True)
    for path in (
        icon_dir / "Mermaid.ico",
        icon_dir / "Mermaid.icns",
        png_dir / "mermaid-512x512.png",
    ):
        path.touch()

    monkeypatch.setattr(icons.sys, "platform", platform)
    monkeypatch.setattr(icons, "_resource_roots", lambda: [tmp_path])

    selected = icons.get_diagram_editor_icon_path("mermaid")
    assert selected is not None
    assert selected.name == expected_name


@pytest.mark.parametrize("editor", ["plantuml", "mermaid", "excalidraw"])
def test_generated_editor_icon_assets_exist_and_load(qapp, editor: str) -> None:
    icon_path = icons.get_diagram_editor_icon_path(editor)

    assert icon_path is not None
    assert icon_path.is_file()
    assert not icons.get_diagram_editor_icon(editor).isNull()


def test_standalone_editor_uses_native_macos_icon(qapp, tmp_path, monkeypatch) -> None:
    from sp.app.folder_navigator import icon as folder_icon

    icon_path = tmp_path / "Excalidraw.icns"
    icon_path.touch()
    native_calls = []
    monkeypatch.setattr(icons.sys, "platform", "darwin")
    monkeypatch.setattr(icons, "get_diagram_editor_icon_path", lambda _editor: icon_path)
    monkeypatch.setattr(
        folder_icon,
        "_set_macos_application_icon",
        lambda path: native_calls.append(path) or True,
    )

    icons.configure_diagram_editor_application(qapp, "excalidraw")

    assert native_calls == [icon_path]


def test_standalone_editor_sets_distinct_windows_app_id(monkeypatch) -> None:
    app_ids = []
    shell32 = SimpleNamespace(
        SetCurrentProcessExplicitAppUserModelID=app_ids.append
    )
    monkeypatch.setattr(icons.sys, "platform", "win32")
    monkeypatch.setattr(
        icons.ctypes, "windll", SimpleNamespace(shell32=shell32), raising=False
    )

    icons.configure_diagram_editor_process("plantuml")

    assert app_ids == ["com.stillpoint.plantuml"]


def test_linux_editor_process_command_has_separate_module_identity(tmp_path, monkeypatch) -> None:
    from sp.app import diagram_editor_process

    source = tmp_path / "drawing.puml"
    monkeypatch.setattr(diagram_editor_process.sys, "frozen", False, raising=False)
    command = diagram_editor_process.editor_process_command("plantuml", source)

    assert command[1:4] == ["-m", "sp.app.diagram_editor_process", "--editor"]
    assert command[5:7] == ["--path", str(source.resolve())]
    assert command[-2:] == ["--geometry-scope", "default"]


def test_frozen_editor_process_command_returns_to_desktop_entrypoint(
        tmp_path, monkeypatch) -> None:
    from sp.app import diagram_editor_process

    source = tmp_path / "drawing.mmd"
    monkeypatch.setattr(diagram_editor_process.sys, "frozen", True, raising=False)
    monkeypatch.setattr(diagram_editor_process.sys, "executable", "/opt/stillpoint/stillpoint")
    command = diagram_editor_process.editor_process_command("mermaid", source)

    assert command == [
        "/opt/stillpoint/stillpoint",
        "--diagram-editor",
        "mermaid",
        "--diagram-editor-path",
        str(source.resolve()),
        "--diagram-editor-geometry-scope",
        "default",
    ]


def test_standalone_editor_geometry_and_splitters_persist(qapp, tmp_path) -> None:
    from PySide6.QtCore import QSettings, Qt
    from PySide6.QtWidgets import QLabel, QMainWindow, QSplitter

    from sp.app.diagram_editor_process import StandaloneGeometryStore

    settings = QSettings(str(tmp_path / "geometry.ini"), QSettings.IniFormat)

    def make_window(size):
        window = QMainWindow()
        window.resize(*size)
        window.editor_preview_splitter = QSplitter(Qt.Horizontal, window)
        window.editor_preview_splitter.addWidget(QLabel("editor"))
        window.editor_preview_splitter.addWidget(QLabel("preview"))
        window._vertical_splitter = QSplitter(Qt.Vertical, window)
        window._vertical_splitter.addWidget(QLabel("preview"))
        window._vertical_splitter.addWidget(QLabel("chat"))
        return window

    first = make_window((777, 555))
    first.editor_preview_splitter.setSizes([260, 517])
    first._vertical_splitter.setSizes([410, 145])
    first_store = StandaloneGeometryStore(
        first, "plantuml", "folder:/drawings", settings=settings
    )
    first_store.save()

    second = make_window((320, 240))
    second_store = StandaloneGeometryStore(
        second, "plantuml", "folder:/drawings", settings=settings
    )
    second_store.restore()
    second_store.restore_splitters()

    assert second.size() == first.size()
    assert second.editor_preview_splitter.sizes() == first.editor_preview_splitter.sizes()
    assert second._vertical_splitter.sizes() == first._vertical_splitter.sizes()
    first.close()
    second.close()


def test_standalone_editor_migrates_parent_geometry(qapp, tmp_path) -> None:
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QMainWindow

    from sp.app.diagram_editor_process import StandaloneGeometryStore

    previous = QMainWindow()
    previous.resize(611, 477)
    encoded = previous.saveGeometry().toBase64().data().decode("ascii")
    restored = QMainWindow()
    restored.resize(320, 240)
    settings = QSettings(str(tmp_path / "migration.ini"), QSettings.IniFormat)
    store = StandaloneGeometryStore(
        restored,
        "mermaid",
        "main",
        initial_geometry=encoded,
        settings=settings,
    )

    store.restore()

    assert restored.size() == previous.size()
    previous.close()
    restored.close()
