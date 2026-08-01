from __future__ import annotations

from types import SimpleNamespace

from sp.app import config
from sp.app.ui import ai_chat_panel


def test_operations_model_config_round_trip(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(config, "GLOBAL_CONFIG", tmp_path / "stillpoint_config.json")

    assert config.load_default_ai_operations_model() is None
    config.save_default_ai_operations_model("small-utility-model")
    assert config.load_default_ai_operations_model() == "small-utility-model"


def test_operations_resolver_prefers_operations_model(monkeypatch) -> None:
    server = {"name": "Primary", "default_model": "server-default"}
    manager = SimpleNamespace(
        get_server=lambda name: server if name == "Primary" else None,
        load_servers=lambda: [server],
    )
    monkeypatch.setattr(config, "load_default_ai_server", lambda: "Primary")
    monkeypatch.setattr(config, "load_default_ai_operations_model", lambda: "utility-small")
    monkeypatch.setattr(config, "load_default_ai_model", lambda: "chat-large")
    monkeypatch.setattr(
        config,
        "_read_global_config",
        lambda: {"server_models": {"Primary": ["chat-large", "utility-small", "server-default"]}},
    )

    assert ai_chat_panel.resolve_operations_server_and_model(manager) == (server, "utility-small")


def test_operations_resolver_uses_compatibility_fallbacks(monkeypatch) -> None:
    server = {"name": "First", "default_model": "server-default"}
    manager = SimpleNamespace(get_server=lambda name: None, load_servers=lambda: [server])
    monkeypatch.setattr(config, "load_default_ai_server", lambda: "Missing")
    monkeypatch.setattr(config, "load_default_ai_operations_model", lambda: "stale-utility")
    monkeypatch.setattr(config, "load_default_ai_model", lambda: "chat-default")
    monkeypatch.setattr(
        config,
        "_read_global_config",
        lambda: {"server_models": {"First": ["chat-default", "server-default"]}},
    )

    assert ai_chat_panel.resolve_operations_server_and_model(manager) == (server, "chat-default")


def test_operations_resolver_supports_servers_without_model_discovery(monkeypatch) -> None:
    server = {"name": "Compatible", "default_model": "server-default"}
    manager = SimpleNamespace(get_server=lambda name: server, load_servers=lambda: [server])
    monkeypatch.setattr(config, "load_default_ai_server", lambda: "Compatible")
    monkeypatch.setattr(config, "load_default_ai_operations_model", lambda: "utility-custom")
    monkeypatch.setattr(config, "load_default_ai_model", lambda: "chat-default")
    monkeypatch.setattr(config, "_read_global_config", lambda: {})

    assert ai_chat_panel.resolve_operations_server_and_model(manager) == (server, "utility-custom")


def test_operations_resolver_returns_none_without_server(monkeypatch) -> None:
    manager = SimpleNamespace(get_server=lambda name: None, load_servers=lambda: [])
    monkeypatch.setattr(config, "load_default_ai_server", lambda: "Missing")

    assert ai_chat_panel.resolve_operations_server_and_model(manager) is None


def test_calendar_and_task_insights_delegate_to_operations_resolver(monkeypatch) -> None:
    from sp.app.ui import calendar_panel, task_panel

    expected = ({"name": "Primary"}, "utility-small")
    calendar_calls = []
    task_calls = []

    monkeypatch.setattr(
        calendar_panel,
        "resolve_operations_server_and_model",
        lambda: calendar_calls.append(True) or expected,
    )
    monkeypatch.setattr(
        task_panel,
        "resolve_operations_server_and_model",
        lambda: task_calls.append(True) or expected,
    )

    calendar = calendar_panel.CalendarPanel.__new__(calendar_panel.CalendarPanel)
    tasks = task_panel.TaskPanel.__new__(task_panel.TaskPanel)

    assert calendar._resolve_ai_server_and_model() == expected
    assert tasks._resolve_ai_server_and_model() == expected
    assert calendar_calls == [True]
    assert task_calls == [True]
