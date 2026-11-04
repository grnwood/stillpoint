from pathlib import Path

from sp.app import external_launch


def test_external_environment_restores_original_loader_path(monkeypatch) -> None:
    monkeypatch.setattr(external_launch.sys, "frozen", True, raising=False)
    env = external_launch.external_process_environment(
        {
            "LD_LIBRARY_PATH": "/tmp/stillpoint-bundle",
            "LD_LIBRARY_PATH_ORIG": "/usr/local/lib:/usr/lib",
            "QT_PLUGIN_PATH": "/tmp/stillpoint-bundle/PySide6/plugins",
            "KEEP_ME": "yes",
        }
    )

    assert env["LD_LIBRARY_PATH"] == "/usr/local/lib:/usr/lib"
    assert "LD_LIBRARY_PATH_ORIG" not in env
    assert "QT_PLUGIN_PATH" not in env
    assert env["KEEP_ME"] == "yes"


def test_external_environment_removes_bundle_loader_path_without_original(monkeypatch) -> None:
    monkeypatch.setattr(external_launch.sys, "frozen", True, raising=False)
    env = external_launch.external_process_environment(
        {"LD_LIBRARY_PATH": "/tmp/stillpoint-bundle", "PYTHONHOME": "/tmp/runtime"}
    )

    assert "LD_LIBRARY_PATH" not in env
    assert "PYTHONHOME" not in env


def test_source_checkout_preserves_user_runtime_environment(monkeypatch) -> None:
    monkeypatch.delattr(external_launch.sys, "frozen", raising=False)
    source = {"LD_LIBRARY_PATH": "/opt/user/lib", "QT_PLUGIN_PATH": "/opt/user/qt"}

    assert external_launch.external_process_environment(source) == source


def test_open_path_uses_clean_environment(tmp_path: Path, monkeypatch) -> None:
    calls: list[tuple[list[str], dict]] = []
    monkeypatch.setattr(external_launch.sys, "platform", "linux")
    monkeypatch.setattr(external_launch.sys, "frozen", True, raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/stillpoint-bundle")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/usr/lib")
    monkeypatch.setattr(
        external_launch.subprocess,
        "Popen",
        lambda command, **kwargs: calls.append((command, kwargs)),
    )

    assert external_launch.open_path_in_system(tmp_path) is True
    command, options = calls[0]
    assert command == ["xdg-open", str(tmp_path)]
    assert options["env"]["LD_LIBRARY_PATH"] == "/usr/lib"
    assert "LD_LIBRARY_PATH_ORIG" not in options["env"]
