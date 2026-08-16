from __future__ import annotations

from sp.app import config


def test_quick_capture_header_default(monkeypatch) -> None:
    monkeypatch.setattr(config, "_read_global_config", lambda: {})

    assert config.load_quick_capture_header() == "QuickCaptures"
    assert config.load_quick_capture_triage_enabled() is False


def test_quick_capture_header_save_cleans_value(monkeypatch) -> None:
    updates: list[dict] = []
    monkeypatch.setattr(config, "_update_global_config", lambda value: updates.append(value))

    config.save_quick_capture_header("## Captured")
    config.save_quick_capture_triage_enabled(True)

    assert updates == [
        {"quick_capture_header": "Captured"},
        {"quick_capture_triage_enabled": True},
    ]
