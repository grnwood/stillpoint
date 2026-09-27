def test_plantuml_cache_key_is_stable_after_tool_discovery(tmp_path, monkeypatch):
    from sp.app.plantuml_renderer import PlantUMLRenderer, RenderResult

    renderer = PlantUMLRenderer(cache_dir=tmp_path / "plantuml-cache")
    java = tmp_path / "java"
    jar = tmp_path / "plantuml.jar"
    calls = []

    def discover_java():
        renderer._java_path = java
        renderer._java_available = True
        return True

    def discover_jar():
        renderer._jar_path = jar
        return jar

    def invoke(source):
        calls.append(source)
        return RenderResult(success=True, svg_content="<svg />")

    monkeypatch.setattr(renderer, "initialize_from_config", lambda: None)
    monkeypatch.setattr(renderer, "discover_java", discover_java)
    monkeypatch.setattr(renderer, "discover_jar", discover_jar)
    monkeypatch.setattr(renderer, "_invoke_plantuml", invoke)

    assert renderer.render_svg("@startuml\n@enduml").success
    assert renderer.render_svg("@startuml\n@enduml").success
    assert calls == ["@startuml\n@enduml"]


def test_mermaid_cache_key_is_stable_after_tool_discovery(tmp_path, monkeypatch):
    from sp.app.mermaid_renderer import MermaidRenderer, RenderResult

    renderer = MermaidRenderer(cache_dir=tmp_path / "mermaid-cache")
    executable = tmp_path / "mmdc"
    calls = []

    def discover():
        renderer._mmdc_path = executable
        return executable

    def invoke(source, **_kwargs):
        calls.append(source)
        return RenderResult(success=True, svg_content="<svg />")

    monkeypatch.setattr(renderer, "discover_mmdc", discover)
    monkeypatch.setattr(renderer, "_invoke_mmdc_svg", invoke)

    assert renderer.render_svg("flowchart TD\nA --> B").success
    assert renderer.render_svg("flowchart TD\nA --> B").success
    assert calls == ["flowchart TD\nA --> B"]
