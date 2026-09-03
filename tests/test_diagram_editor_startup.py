from __future__ import annotations

import base64
import json
import time
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QIcon, QWheelEvent
from PySide6.QtWidgets import QListWidgetItem, QInputDialog, QScrollArea, QWidget

from sp.app.mermaid_renderer import MermaidRenderer, RenderResult as MermaidRenderResult
from sp.app.plantuml_renderer import RenderResult as PlantumlRenderResult
from sp.app.ui.attachments_panel import AttachmentsPanel
from sp.app.ui import excalidraw_window
from sp.app.ui.excalidraw_window import ExcalidrawWindow
from sp.app.ui import mermaid_editor_window
from sp.app.ui import webengine_env
from sp.app.ui.mermaid_editor_window import MermaidEditorWindow
from sp.app.ui.plantuml_editor_window import PlantUMLEditorWindow, ZoomablePreviewLabel
from sp.server import api as server_api


def _wait_for(qapp, predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    qapp.processEvents()
    assert predicate()


def _wheel_event(*, pixel_y: int = 0, angle_y: int = 0, modifiers=Qt.NoModifier) -> QWheelEvent:
    return QWheelEvent(
        QPointF(10, 10),
        QPointF(10, 10),
        QPoint(0, pixel_y),
        QPoint(0, angle_y),
        Qt.NoButton,
        modifiers,
        Qt.ScrollUpdate,
        False,
    )


def test_preview_touchpad_scroll_pans_but_mouse_wheel_zooms(qtbot) -> None:
    area = QScrollArea()
    label = ZoomablePreviewLabel()
    label.resize(1000, 1000)
    area.setWidget(label)
    area.resize(200, 200)
    qtbot.addWidget(area)
    area.show()
    area.verticalScrollBar().setValue(200)
    zooms: list[int] = []
    label.zoomRequested.connect(zooms.append)

    label.wheelEvent(_wheel_event(pixel_y=-30))

    assert area.verticalScrollBar().value() == 230
    assert zooms == []

    label.wheelEvent(_wheel_event(angle_y=120))

    assert zooms == [1]


def test_preview_ctrl_touchpad_scroll_zooms_without_panning(qtbot) -> None:
    area = QScrollArea()
    label = ZoomablePreviewLabel()
    label.resize(1000, 1000)
    area.setWidget(label)
    area.resize(200, 200)
    qtbot.addWidget(area)
    area.show()
    area.verticalScrollBar().setValue(200)
    zooms: list[int] = []
    label.zoomRequested.connect(zooms.append)

    label.wheelEvent(_wheel_event(pixel_y=45, modifiers=Qt.ControlModifier))

    assert area.verticalScrollBar().value() == 200
    assert zooms == [1]


def _patch_common_editor_deps(monkeypatch) -> None:
    monkeypatch.setattr("sp.app.main.get_app_icon", lambda: QIcon())
    monkeypatch.setattr("sp.app.ui.plantuml_editor_window.config.load_enable_ai_chats", lambda: False)
    monkeypatch.setattr("sp.app.ui.plantuml_editor_window.config.load_vi_mode_enabled", lambda: False)
    monkeypatch.setattr("sp.app.ui.plantuml_editor_window.config.load_vi_cursor_style", lambda: "line")
    monkeypatch.setattr("sp.app.ui.plantuml_editor_window.config.load_puml_auto_render", lambda default=False: False)
    monkeypatch.setattr("sp.app.ui.plantuml_editor_window.config.load_puml_editor_zoom", lambda _=0: 0)
    monkeypatch.setattr("sp.app.ui.plantuml_editor_window.config.load_puml_preview_zoom", lambda _=0: 0)
    monkeypatch.setattr("sp.app.ui.plantuml_editor_window.config.load_puml_window_geometry", lambda: None)
    monkeypatch.setattr("sp.app.ui.plantuml_editor_window.config.load_puml_hsplit_state", lambda: None)
    monkeypatch.setattr("sp.app.ui.plantuml_editor_window.config.load_puml_vsplit_state", lambda: None)
    monkeypatch.setattr("sp.app.ui.mermaid_editor_window.config.load_enable_ai_chats", lambda: False)
    monkeypatch.setattr("sp.app.ui.mermaid_editor_window.config.load_vi_mode_enabled", lambda: False)
    monkeypatch.setattr("sp.app.ui.mermaid_editor_window.config.load_vi_cursor_style", lambda: "line")
    monkeypatch.setattr("sp.app.ui.mermaid_editor_window.config.load_mermaid_auto_render", lambda default=False: False)
    monkeypatch.setattr("sp.app.ui.mermaid_editor_window.config.load_mermaid_render_theme", lambda default="neutral": "neutral")
    monkeypatch.setattr("sp.app.ui.mermaid_editor_window.config.load_mermaid_editor_zoom", lambda _=0: 0)
    monkeypatch.setattr("sp.app.ui.mermaid_editor_window.config.load_mermaid_preview_zoom", lambda _=0: 0)
    monkeypatch.setattr("sp.app.ui.mermaid_editor_window.config.load_mermaid_window_geometry", lambda: None)
    monkeypatch.setattr("sp.app.ui.mermaid_editor_window.config.load_mermaid_hsplit_state", lambda: None)
    monkeypatch.setattr("sp.app.ui.mermaid_editor_window.config.load_mermaid_vsplit_state", lambda: None)


def test_diagram_editors_initialize_with_compact_ai_chat_pane(qapp, qtbot, monkeypatch, tmp_path: Path) -> None:
    _patch_common_editor_deps(monkeypatch)
    monkeypatch.setattr("sp.app.ui.plantuml_editor_window.config.load_enable_ai_chats", lambda: True)
    monkeypatch.setattr("sp.app.ui.mermaid_editor_window.config.load_enable_ai_chats", lambda: True)
    monkeypatch.setattr(PlantUMLEditorWindow, "_load_ai_servers_models", lambda self: None)
    monkeypatch.setattr(MermaidEditorWindow, "_load_ai_servers_models", lambda self: None)
    monkeypatch.setattr(PlantUMLEditorWindow, "_render", lambda self: None)
    monkeypatch.setattr(MermaidEditorWindow, "_render", lambda self: None)
    monkeypatch.setattr(mermaid_editor_window, "_inline_preview_preference_enabled", lambda: True)
    monkeypatch.setattr(mermaid_editor_window, "_should_use_web_preview", lambda: False)

    plant_path = tmp_path / "layout.puml"
    plant_path.write_text("@startuml\nAlice -> Bob\n@enduml\n", encoding="utf-8")
    mermaid_path = tmp_path / "layout.mmd"
    mermaid_path.write_text("flowchart TD\nA --> B\n", encoding="utf-8")

    windows = [PlantUMLEditorWindow(str(plant_path)), MermaidEditorWindow(str(mermaid_path))]
    for window in windows:
        qtbot.addWidget(window)
        window.show()
        _wait_for(qapp, lambda window=window: window._splitter_layout_initialized)

        horizontal_sizes = window.editor_preview_splitter.sizes()
        vertical_sizes = window._vertical_splitter.sizes()

        assert len(horizontal_sizes) == 2
        assert horizontal_sizes[0] > 0
        assert horizontal_sizes[1] > horizontal_sizes[0]
        assert len(vertical_sizes) == 2
        assert vertical_sizes[0] > vertical_sizes[1] >= 40
        assert vertical_sizes[1] <= max(1, sum(vertical_sizes) // 3)

        window.close()


def test_plantuml_editor_defers_initial_render_until_window_is_shown(qapp, monkeypatch, tmp_path: Path):
    _patch_common_editor_deps(monkeypatch)
    calls: list[str] = []

    def fake_render(self, text: str):
        calls.append(text)
        return PlantumlRenderResult(success=False, error_message="stub")

    monkeypatch.setattr("sp.app.plantuml_renderer.PlantUMLRenderer.render_svg", fake_render)

    file_path = tmp_path / "sample.puml"
    file_path.write_text("@startuml\nAlice -> Bob\n@enduml\n", encoding="utf-8")

    window = PlantUMLEditorWindow(str(file_path))
    assert calls == []

    window.show()
    _wait_for(qapp, lambda: len(calls) == 1)
    window.close()


def test_plantuml_editor_without_ai_has_single_preview_pane(qapp, monkeypatch, tmp_path: Path):
    _patch_common_editor_deps(monkeypatch)
    monkeypatch.setattr(
        "sp.app.plantuml_renderer.PlantUMLRenderer.render_svg",
        lambda self, text: PlantumlRenderResult(success=False, error_message="stub"),
    )

    file_path = tmp_path / "sample.puml"
    file_path.write_text("@startuml\nAlice -> Bob\n@enduml\n", encoding="utf-8")

    window = PlantUMLEditorWindow(str(file_path))

    assert window._vertical_splitter.count() == 1

    window.close()


def test_mermaid_editor_defers_initial_render_until_window_is_shown(qapp, monkeypatch, tmp_path: Path):
    _patch_common_editor_deps(monkeypatch)
    monkeypatch.setattr("sp.app.ui.mermaid_editor_window._inline_preview_preference_enabled", lambda: True)
    monkeypatch.setattr("sp.app.ui.mermaid_editor_window._should_use_web_preview", lambda: False)
    calls: list[str] = []

    def fake_render(
        self,
        text: str,
        *,
        theme: str = "neutral",
        background_color: str | None = None,
        normalize_for_qtsvg: bool = True,
    ):
        calls.append(text)
        return MermaidRenderResult(success=False, error_message="stub")

    monkeypatch.setattr("sp.app.mermaid_renderer.MermaidRenderer.render_svg", fake_render)

    file_path = tmp_path / "sample.mmd"
    file_path.write_text("flowchart TD\n  A --> B\n", encoding="utf-8")

    window = MermaidEditorWindow(str(file_path))
    assert calls == []

    window.show()
    _wait_for(qapp, lambda: len(calls) == 1)
    window.close()


def test_mermaid_qwebengine_load_applies_linux_env_first(monkeypatch):
    calls: list[str] = []

    monkeypatch.setattr(mermaid_editor_window, "_QWEBENGINE_VIEW_CLASS", None)
    monkeypatch.setattr(mermaid_editor_window, "_QWEBENGINE_IMPORT_ATTEMPTED", False)

    def fake_configure() -> None:
        calls.append("configure")

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "PySide6.QtWebEngineWidgets":
            calls.append("import")

            class FakeWebEngineView:
                pass

            class FakeModule:
                QWebEngineView = FakeWebEngineView

            return FakeModule()
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(mermaid_editor_window, "_configure_linux_webengine_env", fake_configure)
    original_import = __import__
    monkeypatch.setattr("builtins.__import__", fake_import)

    result = mermaid_editor_window._load_qwebengine_view_class()

    assert result is not None
    assert calls == ["configure", "import"]


def test_mermaid_linux_inline_uses_split_preview_without_webengine_import(monkeypatch):
    imports: list[str] = []
    monkeypatch.setattr(mermaid_editor_window.sys, "platform", "linux")
    monkeypatch.setenv("SP_ENABLE_MERMAID_WEB_PREVIEW", "1")
    monkeypatch.delenv("SP_MERMAID_ALLOW_INPROCESS_WEBENGINE", raising=False)
    monkeypatch.setattr(mermaid_editor_window, "_QWEBENGINE_VIEW_CLASS", None)
    monkeypatch.setattr(mermaid_editor_window, "_QWEBENGINE_IMPORT_ATTEMPTED", False)

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "PySide6.QtWebEngineWidgets":
            imports.append(name)
            raise AssertionError("Mermaid should not import in-process WebEngine on Linux by default")
        return original_import(name, globals, locals, fromlist, level)

    original_import = __import__
    monkeypatch.setattr("builtins.__import__", fake_import)

    assert mermaid_editor_window._should_use_web_preview() is False
    assert imports == []


def test_mermaid_inline_default_is_enabled(monkeypatch):
    monkeypatch.setattr("sp.app.config._read_global_config", lambda: {})

    assert mermaid_editor_window.config.load_mermaid_inline_web_preview() is True


def test_mermaid_linux_constructor_uses_inline_preview_column(monkeypatch, tmp_path: Path):
    _patch_common_editor_deps(monkeypatch)
    monkeypatch.setattr(mermaid_editor_window.sys, "platform", "linux")
    monkeypatch.setenv("SP_ENABLE_MERMAID_WEB_PREVIEW", "1")
    monkeypatch.delenv("SP_MERMAID_ALLOW_INPROCESS_WEBENGINE", raising=False)
    monkeypatch.setattr("sp.app.ui.mermaid_editor_window.config.load_mermaid_inline_web_preview", lambda: True)
    monkeypatch.setattr("sp.app.ui.mermaid_editor_window._should_use_web_preview", lambda: False)
    file_path = tmp_path / "sample.mmd"
    file_path.write_text("flowchart TD\n  A --> B\n", encoding="utf-8")

    window = MermaidEditorWindow(str(file_path))

    assert window._external_browser_preview is False
    assert window._use_web_preview is False
    assert window.preview_web is None
    assert window._vertical_splitter is not None
    window.close()


def test_excalidraw_attachment_double_click_emits_editor_request(qtbot, tmp_path: Path):
    panel = AttachmentsPanel()
    qtbot.addWidget(panel)
    drawing = tmp_path / "sample.excalidraw"
    drawing.write_text('{"type":"excalidraw","elements":[]}', encoding="utf-8")
    item = QListWidgetItem("sample.excalidraw")
    item.setData(Qt.UserRole, str(drawing))
    requested: list[str] = []
    panel.excalidrawEditorRequested.connect(requested.append)

    panel._open_attachment(item)

    assert requested == [str(drawing)]


def test_create_new_excalidraw_attachment_creates_file_and_opens(qtbot, monkeypatch, tmp_path: Path):
    page = tmp_path / "Page.md"
    page.write_text("# Page\n", encoding="utf-8")
    panel = AttachmentsPanel()
    qtbot.addWidget(panel)
    panel.current_page_path = page
    requested: list[str] = []
    panel.excalidrawEditorRequested.connect(requested.append)
    monkeypatch.setattr(QInputDialog, "getText", lambda *args, **kwargs: ("sketch", True))

    panel._create_new_excalidraw()

    drawing = tmp_path / "sketch.excalidraw"
    assert drawing.exists()
    assert '"type": "excalidraw"' in drawing.read_text(encoding="utf-8")
    assert requested == [str(drawing)]


def test_excalidraw_poc_window_loads_local_url(qapp, monkeypatch, tmp_path: Path):
    loaded: list[str] = []

    class FakeWebView(QWidget):
        def __init__(self, parent=None):
            super().__init__(parent)

        def load(self, url):
            loaded.append(url.toString())

    monkeypatch.setattr(excalidraw_window, "_load_qwebengine_view_class", lambda: FakeWebView)
    drawing = tmp_path / "sample.excalidraw"
    drawing.write_text('{"type":"excalidraw","elements":[]}', encoding="utf-8")

    window = ExcalidrawWindow(str(drawing), base_url="http://127.0.0.1:4777")

    assert loaded == ["http://127.0.0.1:4777/excalidraw/poc"]
    window.close()


def test_excalidraw_disable_flag_skips_webengine_import(monkeypatch):
    imports: list[str] = []
    monkeypatch.setenv("SP_DISABLE_EXCALIDRAW_WEBENGINE", "1")
    monkeypatch.setattr(excalidraw_window, "_QWEBENGINE_VIEW_CLASS", None)
    monkeypatch.setattr(excalidraw_window, "_QWEBENGINE_IMPORT_ATTEMPTED", False)

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "PySide6.QtWebEngineWidgets":
            imports.append(name)
            raise AssertionError("QtWebEngine should not be imported when disabled")
        return original_import(name, globals, locals, fromlist, level)

    original_import = __import__
    monkeypatch.setattr("builtins.__import__", fake_import)

    assert excalidraw_window._load_qwebengine_view_class() is None
    assert imports == []


def test_webengine_safe_profile_sets_software_rendering(monkeypatch):
    monkeypatch.setattr(webengine_env.sys, "platform", "linux")
    for name in (
        "SP_WEBENGINE_PROFILE",
        "QT_OPENGL",
        "QTWEBENGINE_DISABLE_SANDBOX",
        "QTWEBENGINE_CHROMIUM_FLAGS",
        "SP_WEBENGINE_EXTRA_FLAGS",
    ):
        monkeypatch.delenv(name, raising=False)

    webengine_env.configure_linux_webengine_env()

    assert os_environ("QT_OPENGL") == "software"
    assert os_environ("QTWEBENGINE_DISABLE_SANDBOX") == "1"
    flags = os_environ("QTWEBENGINE_CHROMIUM_FLAGS")
    assert "--use-gl=swiftshader" not in flags
    assert "--disable-vulkan" in flags
    assert "--disable-gpu" in flags


def test_webengine_xcb_profile_sets_platform(monkeypatch):
    monkeypatch.setattr(webengine_env.sys, "platform", "linux")
    monkeypatch.setenv("SP_WEBENGINE_PROFILE", "xcb")
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    monkeypatch.delenv("QTWEBENGINE_CHROMIUM_FLAGS", raising=False)

    webengine_env.configure_linux_webengine_env()

    assert os_environ("QT_QPA_PLATFORM") == "xcb"
    assert "--no-sandbox" in os_environ("QTWEBENGINE_CHROMIUM_FLAGS")


def test_webengine_swiftshader_profile_does_not_disable_gpu(monkeypatch):
    monkeypatch.setattr(webengine_env.sys, "platform", "linux")
    monkeypatch.setenv("SP_WEBENGINE_PROFILE", "swiftshader")
    monkeypatch.delenv("QTWEBENGINE_CHROMIUM_FLAGS", raising=False)

    webengine_env.configure_linux_webengine_env()

    flags = os_environ("QTWEBENGINE_CHROMIUM_FLAGS")
    assert "--use-gl=swiftshader" in flags
    assert "--disable-gpu" not in flags


def os_environ(name: str) -> str:
    import os

    return os.getenv(name, "")


def test_excalidraw_poc_route_serves_minimal_local_page():
    from sp.server.api import excalidraw_poc

    response = excalidraw_poc()
    content = response.body.decode("utf-8")
    assert response.status_code == 200
    assert "StillPoint Excalidraw POC" in content
    assert "foxnews" not in content.lower()


def test_excalidraw_api_loads_and_saves_scene(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(server_api.vault_state, "get_root", lambda: tmp_path)
    drawing = tmp_path / "Design" / "drawing.excalidraw"
    drawing.parent.mkdir()
    drawing.write_text(
        json.dumps({"type": "excalidraw", "version": 2, "elements": [], "appState": {}, "files": {}}),
        encoding="utf-8",
    )

    loaded = server_api.excalidraw_load("/Design/drawing.excalidraw")

    assert loaded["path"] == "/Design/drawing.excalidraw"
    assert loaded["title"] == "drawing.excalidraw"
    assert loaded["scene"]["type"] == "excalidraw"

    payload = server_api.ExcalidrawSavePayload(
        path="/Design/drawing.excalidraw",
        scene={"elements": [{"id": "one", "type": "rectangle"}]},
    )
    saved = server_api.excalidraw_save(payload)

    assert saved["ok"] is True
    persisted = json.loads(drawing.read_text(encoding="utf-8"))
    assert persisted["type"] == "excalidraw"
    assert persisted["elements"][0]["id"] == "one"


def test_excalidraw_api_load_repairs_incomplete_saved_scene(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(server_api.vault_state, "get_root", lambda: tmp_path)
    drawing = tmp_path / "broken.excalidraw"
    drawing.write_text(
        json.dumps(
            {
                "type": "excalidraw",
                "version": 2,
                "elements": [{"id": "arrow", "type": "arrow", "x": 0, "y": 0}],
                "appState": {},
                "files": {},
            }
        ),
        encoding="utf-8",
    )

    loaded = server_api.excalidraw_load("/broken.excalidraw")

    arrow = loaded["scene"]["elements"][0]
    assert arrow["groupIds"] == []
    assert len(arrow["points"]) == 2
    assert arrow["boundElements"] is None


def test_excalidraw_api_rejects_paths_outside_vault(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(server_api.vault_state, "get_root", lambda: tmp_path)

    try:
        server_api.excalidraw_load("/../outside.excalidraw")
    except server_api.HTTPException as exc:
        assert exc.status_code == 400
    else:
        raise AssertionError("Expected path traversal to be rejected")


def test_excalidraw_preview_writes_png_sidecar(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(server_api.vault_state, "get_root", lambda: tmp_path)
    png = b"\x89PNG\r\n\x1a\n" + b"stillpoint"
    payload = server_api.ExcalidrawPreviewPayload(
        path="/drawing.excalidraw",
        png_base64=base64.b64encode(png).decode("ascii"),
    )

    result = server_api.excalidraw_save_preview(payload)

    assert result == {"ok": True, "preview_path": "/drawing.excalidraw.png"}
    assert (tmp_path / "drawing.excalidraw.png").read_bytes() == png


def test_excalidraw_ai_parses_fenced_full_scene_response():
    response = """```json
{"type":"excalidraw","version":2,"elements":[{"id":"box"}],"appState":{},"files":{}}
```"""

    scene = server_api._parse_excalidraw_ai_scene(response)

    assert scene["type"] == "excalidraw"
    assert scene["elements"][0]["id"] == "box"
    assert scene["elements"][0]["type"] == "rectangle"
    assert scene["elements"][0]["groupIds"] == []
    assert scene["files"] == {}


def test_excalidraw_ai_neutralizes_generated_fractional_indices():
    response = json.dumps(
        {
            "type": "excalidraw",
            "version": 2,
            "elements": [{"id": "box", "index": "p9"}],
            "appState": {},
            "files": {},
        }
    )

    scene = server_api._parse_excalidraw_ai_scene(response)

    assert scene["elements"][0]["index"] is None


def test_excalidraw_ai_fills_required_element_arrays():
    response = json.dumps(
        {
            "type": "excalidraw",
            "version": 2,
            "elements": [
                {"id": "box", "type": "rectangle", "x": 10, "y": 20},
                {"id": "arrow", "type": "arrow", "x": 0, "y": 0},
                {"id": "label", "type": "text", "x": 5, "y": 5, "text": "Hello"},
            ],
            "appState": {},
            "files": {},
        }
    )

    scene = server_api._parse_excalidraw_ai_scene(response)

    box, arrow, label = scene["elements"]
    assert box["groupIds"] == []
    assert box["boundElements"] is None
    assert box["width"] == 100.0
    assert len(arrow["points"]) == 2
    assert arrow["startBinding"] is None
    assert arrow["endBinding"] is None
    assert label["originalText"] == "Hello"
    assert label["lineHeight"] == 1.25


def test_excalidraw_ai_scene_size_counts_bytes_and_elements():
    scene = {
        "type": "excalidraw",
        "version": 2,
        "elements": [{"id": str(index)} for index in range(3)],
        "appState": {},
        "files": {},
    }

    size_bytes, element_count = server_api._excalidraw_ai_scene_size(scene)

    assert size_bytes > 0
    assert element_count == 3


def test_excalidraw_summary_path_uses_filename_sidecar(tmp_path: Path):
    target = tmp_path / "folder" / "diagram.excalidraw"

    summary_path = server_api._excalidraw_summary_path(target)

    assert summary_path == tmp_path / "folder" / "diagram-summary.json"


def test_excalidraw_summary_parser_accepts_fenced_json():
    response = """```json
{"title":"Order Flow","diagram_type":"Integration Architecture","summary":"Orders move through the platform."}
```"""

    summary = server_api._parse_excalidraw_summary(response)

    assert summary["title"] == "Order Flow"
    assert summary["diagram_type"] == "Integration Architecture"


def test_excalidraw_summary_load_reports_missing_and_existing(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(server_api.vault_state, "get_root", lambda: tmp_path)
    drawing = tmp_path / "diagram.excalidraw"
    drawing.write_text(json.dumps({"type": "excalidraw", "version": 2, "elements": [], "appState": {}, "files": {}}), encoding="utf-8")

    missing = server_api.excalidraw_summary_load("/diagram.excalidraw")

    assert missing["exists"] is False
    assert missing["summary_path"] == "/diagram-summary.json"

    (tmp_path / "diagram-summary.json").write_text(json.dumps({"title": "Diagram"}), encoding="utf-8")

    existing = server_api.excalidraw_summary_load("/diagram.excalidraw")

    assert existing["exists"] is True
    assert existing["summary"] == {"title": "Diagram"}


def test_excalidraw_summary_generate_writes_sidecar(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(server_api.vault_state, "get_root", lambda: tmp_path)
    monkeypatch.setattr(server_api.config, "load_enable_ai_chats", lambda: True)
    monkeypatch.setattr(server_api, "_get_ai_server", lambda name: {"name": "Test AI", "default_model": "test-model"})
    monkeypatch.setattr(server_api, "_available_ai_models", lambda server: ["test-model"])
    drawing = tmp_path / "diagram.excalidraw"
    drawing.write_text(json.dumps({"type": "excalidraw", "version": 2, "elements": [], "appState": {}, "files": {}}), encoding="utf-8")

    def fake_request(server, messages, model):
        assert "enterprise architecture analyst" in messages[0]["content"]
        assert "Current Excalidraw scene JSON" in messages[1]["content"]
        return json.dumps({"title": "Diagram", "diagram_type": "Whiteboard", "summary": "A small diagram."})

    monkeypatch.setattr(server_api, "_request_excalidraw_ai_content", fake_request)
    payload = server_api.ExcalidrawSummaryPayload(
        path="/diagram.excalidraw",
        scene={"type": "excalidraw", "version": 2, "elements": [], "appState": {}, "files": {}},
        server="Test AI",
        model="test-model",
    )

    result = server_api.excalidraw_summary_generate(payload)

    assert result["ok"] is True
    assert result["summary_path"] == "/diagram-summary.json"
    assert json.loads((tmp_path / "diagram-summary.json").read_text(encoding="utf-8"))["title"] == "Diagram"


def test_excalidraw_chat_uses_summary_context_without_scene(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(server_api.vault_state, "get_root", lambda: tmp_path)
    monkeypatch.setattr(server_api.config, "load_enable_ai_chats", lambda: True)
    monkeypatch.setattr(server_api, "_get_ai_server", lambda name: {"name": "Test AI", "default_model": "test-model"})
    monkeypatch.setattr(server_api, "_available_ai_models", lambda server: ["test-model"])
    drawing = tmp_path / "diagram.excalidraw"
    drawing.write_text(json.dumps({"type": "excalidraw", "version": 2, "elements": [], "appState": {}, "files": {}}), encoding="utf-8")
    (tmp_path / "diagram-summary.json").write_text(json.dumps({"title": "Orders", "summary": "OMS owns order flow."}), encoding="utf-8")

    def fake_request(server, messages, model):
        content = messages[1]["content"]
        assert "Excalidraw architecture summary JSON" in content
        assert "OMS owns order flow" in content
        assert "Current Excalidraw scene JSON" not in content
        assert messages[2] == {"role": "user", "content": "Earlier question"}
        assert messages[3] == {"role": "assistant", "content": "Earlier answer"}
        assert messages[-1] == {"role": "user", "content": "What owns order flow?"}
        return "The diagram says OMS owns order flow."

    monkeypatch.setattr(server_api, "_request_excalidraw_ai_content", fake_request)
    payload = server_api.ExcalidrawChatPayload(
        path="/diagram.excalidraw",
        prompt="What owns order flow?",
        history=[
            {"role": "system", "content": "ignore me"},
            {"role": "user", "content": "Earlier question"},
            {"role": "assistant", "content": "Earlier answer"},
        ],
    )

    result = server_api.excalidraw_chat(payload)

    assert result["reply"] == "The diagram says OMS owns order flow."


def test_excalidraw_draw_rewrite_endpoint_returns_sanitized_scene(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(server_api.vault_state, "get_root", lambda: tmp_path)
    monkeypatch.setattr(server_api.config, "load_enable_ai_chats", lambda: True)
    monkeypatch.setattr(server_api, "_get_ai_server", lambda name: {"name": "Test AI", "default_model": "test-model"})
    monkeypatch.setattr(server_api, "_available_ai_models", lambda server: ["test-model"])

    def fake_request(server, messages, model):
        assert "You are editing an Excalidraw scene" in messages[0]["content"]
        assert "User request: rewrite it" in messages[1]["content"]
        return json.dumps(
            {
                "type": "excalidraw",
                "version": 2,
                "elements": [{"id": "box", "index": "p9"}],
                "appState": {},
                "files": {},
            }
        )

    monkeypatch.setattr(server_api, "_request_excalidraw_ai_content", fake_request)
    payload = server_api.ExcalidrawAiRewritePayload(
        path="/diagram.excalidraw",
        prompt="rewrite it",
        scene={"type": "excalidraw", "version": 2, "elements": [], "appState": {}, "files": {}},
    )

    result = server_api.excalidraw_ai_rewrite(payload)

    assert result["ok"] is True
    assert result["scene"]["elements"][0]["id"] == "box"
    assert result["scene"]["elements"][0]["index"] is None


def test_mermaid_external_browser_html_includes_export_controls(monkeypatch, tmp_path: Path):
    _patch_common_editor_deps(monkeypatch)
    monkeypatch.setattr("sp.app.ui.mermaid_editor_window._inline_preview_preference_enabled", lambda: False)
    monkeypatch.setattr("sp.app.ui.mermaid_editor_window._should_use_web_preview", lambda: False)

    file_path = tmp_path / "sample.mmd"
    file_path.write_text("flowchart TD\n  A --> B\n", encoding="utf-8")

    window = MermaidEditorWindow(str(file_path))
    html = window._build_mermaid_html("flowchart TD\n  A --> B\n", include_browser_controls=True)

    assert 'id="browser-controls"' in html
    assert 'id="zoom-out-btn"' in html
    assert 'id="zoom-in-btn"' in html
    assert 'id="fit-btn"' in html
    assert 'id="save-svg-btn"' in html
    assert 'id="export-png-btn"' in html
    assert 'id="copy-svg-btn"' in html
    assert 'id="copy-png-btn"' in html
    assert "const includeBrowserControls = true;" in html
    assert "flowchart: { htmlLabels: false }" in html
    assert "const payloadJsUrl = \"\";" in html
    assert "const serializer = new XMLSerializer();" in html

    window.close()


def test_mermaid_browser_preview_uses_system_browser_when_inline_disabled(monkeypatch, tmp_path: Path):
    _patch_common_editor_deps(monkeypatch)
    monkeypatch.setattr("sp.app.ui.mermaid_editor_window.sys.platform", "linux")
    monkeypatch.setattr("sp.app.ui.mermaid_editor_window._inline_preview_preference_enabled", lambda: False)
    monkeypatch.setattr("sp.app.ui.mermaid_editor_window._should_use_web_preview", lambda: False)
    opened: list[str] = []

    monkeypatch.setattr(
        "sp.app.ui.mermaid_editor_window.QDesktopServices.openUrl",
        lambda url: opened.append(url.toString()),
    )
    file_path = tmp_path / "sample.mmd"
    file_path.write_text("flowchart TD\n  A --> B\n", encoding="utf-8")

    window = MermaidEditorWindow(str(file_path))
    window._open_browser_preview_url("file:///tmp/preview.html")

    assert opened == ["file:///tmp/preview.html"]
    assert window._external_browser_preview is True
    window.close()


def test_mermaid_inline_html_omits_browser_controls_by_default(monkeypatch, tmp_path: Path):
    _patch_common_editor_deps(monkeypatch)
    monkeypatch.setattr("sp.app.ui.mermaid_editor_window._inline_preview_preference_enabled", lambda: True)
    monkeypatch.setattr("sp.app.ui.mermaid_editor_window._should_use_web_preview", lambda: False)

    file_path = tmp_path / "sample.mmd"
    file_path.write_text("flowchart TD\n  A --> B\n", encoding="utf-8")

    window = MermaidEditorWindow(str(file_path))
    html = window._build_mermaid_html("flowchart TD\n  A --> B\n")

    assert "const includeBrowserControls = false;" in html

    window.close()


def test_mermaid_external_browser_html_can_embed_pre_rendered_png(monkeypatch, tmp_path: Path):
    _patch_common_editor_deps(monkeypatch)
    monkeypatch.setattr("sp.app.ui.mermaid_editor_window._inline_preview_preference_enabled", lambda: False)
    monkeypatch.setattr("sp.app.ui.mermaid_editor_window._should_use_web_preview", lambda: False)

    file_path = tmp_path / "sample.mmd"
    file_path.write_text("flowchart TD\n  A --> B\n", encoding="utf-8")

    window = MermaidEditorWindow(str(file_path))
    html = window._build_mermaid_html(
        "flowchart TD\n  A --> B\n",
        include_browser_controls=True,
        payload_js_url="file:///tmp/preview_payload.js",
    )

    assert "const payloadJsUrl = \"file:///tmp/preview_payload.js\";" in html

    window.close()


def test_prepare_svg_for_export_replaces_foreignobject_labels_with_svg_text():
    renderer = MermaidRenderer()
    source = (
        '<svg xmlns="http://www.w3.org/2000/svg">'
        '<g class="label" transform="translate(10, 20)">'
        '<foreignObject width="120" height="40">'
        '<div xmlns="http://www.w3.org/1999/xhtml"><span><p>Hello<br />World</p></span></div>'
        '</foreignObject>'
        '</g>'
        '</svg>'
    )

    exported = renderer.prepare_svg_for_export(source)

    assert "<foreignObject" not in exported
    assert "<text" in exported
    assert "Hello" in exported
    assert "World" in exported


def test_prepare_mermaid_text_strips_styling_directives_and_html_breaks():
    renderer = MermaidRenderer()
    source = (
        "flowchart TD\n"
        "    A[Line 1<br/>Line 2] --> B[End]\n"
        "    classDef accent fill:#f00,stroke:#000\n"
        "    class A accent\n"
        "    style A fill:#fff,stroke:#333\n"
        "    linkStyle 0 stroke:#999\n"
    )

    prepared = renderer._prepare_mermaid_text(source)

    assert "classDef" not in prepared
    assert "class A accent" not in prepared
    assert "style A" not in prepared
    assert "linkStyle" not in prepared
    assert "Line 1\\nLine 2" in prepared


def test_qtsvg_normalization_converts_mindmap_foreignobject_labels():
    renderer = MermaidRenderer()
    source = (
        '<svg xmlns="http://www.w3.org/2000/svg" xmlns:html="http://www.w3.org/1999/xhtml" id="my-svg">'
        '<style>#my-svg .section-root text{fill:#333;}#my-svg .mindmap-node-label{dy:1em;}</style>'
        '<g class="node mindmap-node section-root" transform="translate(100, 80)">'
        '<circle r="43.5" cx="0" cy="0" />'
        '<g class="label" transform="translate(-33.5, -12)">'
        '<rect />'
        '<foreignObject width="67" height="24">'
        '<html:div><html:span class="nodeLabel"><html:p>StillPoint</html:p></html:span></html:div>'
        '</foreignObject>'
        '</g>'
        '</g>'
        '</svg>'
    )

    normalized = renderer._normalize_svg_for_qtsvg(source)

    assert "<foreignObject" not in normalized
    assert "<text" in normalized
    assert "StillPoint" in normalized
    assert "fill=\"#24292F\"" in normalized


def test_qtsvg_css_inliner_preserves_specific_sequence_line_strokes():
    renderer = MermaidRenderer()
    source = (
        '<svg xmlns="http://www.w3.org/2000/svg" id="my-svg">'
        '<style>'
        '#my-svg .messageLine0{stroke-width:1.5;stroke:#333;}'
        '#my-svg line{stroke:hsl(0, 0%, 83%);stroke-width:2px;}'
        '</style>'
        '<line x1="1" y1="2" x2="20" y2="2" class="messageLine0" />'
        '</svg>'
    )

    normalized = renderer._normalize_svg_for_qtsvg(source)

    assert 'class="messageLine0"' in normalized
    assert 'stroke="#333"' in normalized


def test_qtsvg_normalization_converts_css_color_functions():
    renderer = MermaidRenderer()
    source = (
        '<svg xmlns="http://www.w3.org/2000/svg" id="my-svg">'
        '<path class="row-rect-even" fill="hsl(240, 100%, 97.2745098039%)" '
        'stroke="rgb(0,0,0,0.5)" style="fill: hsl(240, 100%, 100%); stroke: hsl(0, 0%, 83%);" />'
        '</svg>'
    )

    normalized = renderer._normalize_svg_for_qtsvg(source)

    assert "hsl(" not in normalized
    assert "rgb(" not in normalized
    assert 'fill="#F1F1FF"' in normalized
    assert "stroke: #D4D4D4" in normalized


def test_render_failure_details_includes_stderr_and_traceback():
    result = MermaidRenderResult(
        success=False,
        error_message="Mermaid render error (exit 1)",
        stderr="stderr line 1\nstderr line 2",
    )

    details = mermaid_editor_window._render_failure_details(result, None)
    assert "Mermaid render error (exit 1)" in details
    assert "stderr line 1" in details
    assert "stderr line 2" in details

    try:
        raise RuntimeError("boom")
    except RuntimeError as exc:
        traceback_details = mermaid_editor_window._render_failure_details(None, exc)

    assert "RuntimeError" in traceback_details
    assert "boom" in traceback_details
