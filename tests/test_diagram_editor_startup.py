from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtGui import QIcon

from sp.app.mermaid_renderer import RenderResult as MermaidRenderResult
from sp.app.plantuml_renderer import RenderResult as PlantumlRenderResult
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
    monkeypatch.setattr("sp.app.ui.mermaid_editor_window._should_use_web_preview", lambda: False)
    calls: list[str] = []

    def fake_render(self, text: str, *, theme: str = "neutral", background_color: str | None = None):
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
