from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtGui import QIcon

from sp.app.mermaid_renderer import MermaidRenderer, RenderResult as MermaidRenderResult
from sp.app.plantuml_renderer import RenderResult as PlantumlRenderResult
from sp.app.ui import mermaid_editor_window
from sp.app.ui.mermaid_editor_window import MermaidEditorWindow
from sp.app.ui.plantuml_editor_window import PlantUMLEditorWindow


def _wait_for(qapp, predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    qapp.processEvents()
    assert predicate()


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
