from __future__ import annotations

import json
import threading
import time

from sp.app import config
from sp.app.ui import theme


def test_global_config_read_is_cached_until_file_changes(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "stillpoint-config.json"
    config_path.write_text(json.dumps({"theme_name": "default"}), encoding="utf-8")
    monkeypatch.setattr(config, "GLOBAL_CONFIG", config_path)
    json_loads = config.json.loads
    parse_count = 0

    def counting_loads(value: str) -> object:
        nonlocal parse_count
        parse_count += 1
        return json_loads(value)

    monkeypatch.setattr(config.json, "loads", counting_loads)

    first = config._read_global_config()
    cached = config._read_global_config()
    config_path.write_text(json.dumps({"theme_name": "custom-theme.json"}), encoding="utf-8")
    second = config._read_global_config()

    assert first["theme_name"] == "default"
    assert cached == first
    assert second["theme_name"] == "custom-theme.json"
    assert parse_count == 2


def test_global_config_write_refreshes_cache(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "stillpoint-config.json"
    monkeypatch.setattr(config, "GLOBAL_CONFIG", config_path)

    config._update_global_config({"theme_name": "light-theme.json"})

    assert config._read_global_config()["theme_name"] == "light-theme.json"


def test_theme_load_resolves_effective_preference_once(monkeypatch) -> None:
    calls = 0

    def load_preference() -> str:
        nonlocal calls
        calls += 1
        return "default"

    monkeypatch.setattr(theme.config, "load_effective_theme_preference", load_preference)
    theme.reload_theme()

    theme._load_theme()

    assert calls == 1


def test_homebase_user_info_refresh_is_non_blocking(main_window, monkeypatch, qapp) -> None:
    request_started = threading.Event()
    allow_response = threading.Event()
    applied: list[tuple[bool, bool]] = []

    class Response:
        status_code = 200

        @staticmethod
        def json() -> dict:
            return {"role": "admin", "can_write": True}

    def request(_method: str, _path: str, **_kwargs) -> Response:
        request_started.set()
        allow_response.wait(timeout=2.0)
        return Response()

    monkeypatch.setattr(main_window, "_is_homebase_mode_enabled", lambda: True)
    monkeypatch.setattr(main_window, "_homebase_request", request)
    monkeypatch.setattr(
        main_window,
        "_apply_homebase_user_permissions",
        lambda *, can_write, is_admin: applied.append((can_write, is_admin)),
    )

    started_at = time.perf_counter()
    main_window._refresh_homebase_user_info()
    elapsed = time.perf_counter() - started_at

    assert elapsed < 0.2
    assert request_started.wait(timeout=1.0)
    allow_response.set()
    deadline = time.monotonic() + 2.0
    while not applied and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert main_window._homebase_user_info_loaded is True
    assert applied == [(True, True)]
