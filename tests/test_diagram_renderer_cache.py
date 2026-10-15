import json


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


def test_mermaid_discovers_nvm_cli_without_shell_path(tmp_path, monkeypatch):
    from sp.app.mermaid_renderer import MermaidRenderer

    nvm_bin = tmp_path / "nvm" / "versions" / "node" / "v25.2.1" / "bin"
    nvm_bin.mkdir(parents=True)
    mmdc = nvm_bin / "mmdc"
    node = nvm_bin / "node"
    mmdc.touch(mode=0o755)
    node.touch(mode=0o755)
    monkeypatch.setenv("NVM_DIR", str(tmp_path / "nvm"))
    monkeypatch.delenv("NVM_BIN", raising=False)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setattr("sp.app.mermaid_renderer.shutil.which", lambda name: None)

    renderer = MermaidRenderer(cache_dir=tmp_path / "cache")
    assert renderer.discover_mmdc() == mmdc
    assert renderer._mmdc_env()["PATH"].split(os.pathsep)[0] == str(nvm_bin)


def test_mermaid_windows_npm_wrapper_runs_through_cmd(tmp_path, monkeypatch):
    from sp.app.mermaid_renderer import MermaidRenderer

    renderer = MermaidRenderer(cache_dir=tmp_path / "cache")
    renderer._mmdc_path = tmp_path / "mmdc.cmd"
    monkeypatch.setattr("sp.app.mermaid_renderer._is_windows_platform", lambda: True)
    monkeypatch.setenv("COMSPEC", "C:/Windows/System32/cmd.exe")

    assert renderer._mmdc_command() == [
        "C:/Windows/System32/cmd.exe",
        "/d",
        "/s",
        "/c",
        str(renderer._mmdc_path),
    ]


def test_mermaid_windows_uses_installed_chrome_for_puppeteer(tmp_path, monkeypatch):
    from sp.app.mermaid_renderer import MermaidRenderer

    local_app_data = tmp_path / "Local"
    chrome = local_app_data / "Google" / "Chrome" / "Application" / "chrome.exe"
    chrome.parent.mkdir(parents=True)
    chrome.touch()
    monkeypatch.setattr("sp.app.mermaid_renderer._is_windows_platform", lambda: True)
    monkeypatch.setattr("sp.app.mermaid_renderer.shutil.which", lambda _name: None)
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    for name in ("PUPPETEER_EXECUTABLE_PATH", "CHROME_PATH", "PROGRAMFILES", "PROGRAMFILES(X86)", "ProgramW6432"):
        monkeypatch.delenv(name, raising=False)

    renderer = MermaidRenderer(cache_dir=tmp_path / "cache")
    config_path = tmp_path / "puppeteer-config.json"
    renderer._write_puppeteer_config(config_path)

    assert renderer._discover_puppeteer_browser() == chrome
    assert json.loads(config_path.read_text(encoding="utf-8"))["executablePath"] == str(chrome)
    assert renderer._mmdc_env()["PUPPETEER_EXECUTABLE_PATH"] == str(chrome)


def test_mermaid_failure_diagnostics_include_stdout_stderr_and_command(tmp_path):
    from sp.app.mermaid_renderer import MermaidRenderer

    renderer = MermaidRenderer(cache_dir=tmp_path / "cache")
    renderer._mmdc_path = tmp_path / "mmdc.cmd"
    details = renderer._failure_diagnostics(
        command=[str(renderer._mmdc_path), "-i", "diagram.mmd"],
        stdout="browser output",
        stderr="puppeteer error",
    )

    assert str(renderer._mmdc_path) in details
    assert "browser output" in details
    assert "puppeteer error" in details
import os
