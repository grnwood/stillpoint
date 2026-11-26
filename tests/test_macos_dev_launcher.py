import importlib.util
from pathlib import Path
import plistlib
from types import SimpleNamespace

from sp.app.macos_permissions import MICROPHONE_USAGE


def test_development_bundle_declares_microphone_and_keeps_virtualenv(tmp_path, monkeypatch):
    script = Path(__file__).resolve().parents[1] / "tools" / "run-macos-dev.py"
    spec = importlib.util.spec_from_file_location("stillpoint_dev_launcher", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    prefix = tmp_path / "venv"
    prefix.mkdir()
    (prefix / "lib").mkdir()
    (prefix / "pyvenv.cfg").write_text("home = /base/python/bin\ninclude-system-site-packages = false\n")
    interpreter = tmp_path / "base-python"
    interpreter.write_bytes(b"original interpreter")
    monkeypatch.setattr(module, "sys", SimpleNamespace(prefix=str(prefix), _base_executable=str(interpreter)))

    executable = module.prepare_bundle(tmp_path)

    contents = executable.parent.parent
    with (contents / "Info.plist").open("rb") as source:
        info = plistlib.load(source)
    assert info["NSMicrophoneUsageDescription"] == MICROPHONE_USAGE
    assert info["CFBundleExecutable"] == executable.name
    assert executable.read_bytes() == interpreter.read_bytes()
    assert (contents / "pyvenv.cfg").read_bytes() == (prefix / "pyvenv.cfg").read_bytes()
    assert (contents / "lib").resolve() == (prefix / "lib").resolve()
    assert module.prepare_bundle(tmp_path) == executable
    assert interpreter.read_bytes() == b"original interpreter"


def test_normal_source_launch_restarts_before_startup_with_arguments(tmp_path, monkeypatch):
    from sp.app import macos_permissions

    prefix = tmp_path / "venv"
    prefix.mkdir()
    (prefix / "pyvenv.cfg").write_text("home = /base/python/bin\n")
    arguments = ["sp.app.main", "--vault", "/Vault with spaces", "--port", "5055"]
    monkeypatch.setattr(macos_permissions, "sys", SimpleNamespace(
        platform="darwin", frozen=False, prefix=str(prefix), executable="/venv/bin/python", argv=arguments))
    monkeypatch.setattr(macos_permissions, "microphone_usage_description", lambda: None)
    monkeypatch.delenv("STILLPOINT_DEV_BUNDLE_ATTEMPTED", raising=False)
    calls = []
    monkeypatch.setattr(macos_permissions.os, "execve", lambda *args: calls.append(args))

    macos_permissions.relaunch_development_bundle()

    assert len(calls) == 1
    executable, argv, environment = calls[0]
    assert executable == "/venv/bin/python"
    assert argv[1].endswith("tools/run-macos-dev.py")
    assert argv[2:] == arguments[1:]
    assert environment["STILLPOINT_DEV_BUNDLE_ATTEMPTED"] == "1"
    monkeypatch.setenv("STILLPOINT_DEV_BUNDLE_ATTEMPTED", "1")
    macos_permissions.relaunch_development_bundle()
    assert len(calls) == 1


def test_packaged_or_declared_launch_does_not_restart(monkeypatch):
    from sp.app import macos_permissions

    monkeypatch.delenv("STILLPOINT_DEV_BUNDLE_ATTEMPTED", raising=False)
    calls = []
    monkeypatch.setattr(macos_permissions.os, "execve", lambda *args: calls.append(args))
    monkeypatch.setattr(macos_permissions, "sys", SimpleNamespace(platform="darwin", frozen=True))
    macos_permissions.relaunch_development_bundle()
    monkeypatch.setattr(macos_permissions, "sys", SimpleNamespace(platform="darwin", frozen=False))
    monkeypatch.setattr(macos_permissions, "microphone_usage_description", lambda: "Record voice notes")
    macos_permissions.relaunch_development_bundle()
    assert calls == []
