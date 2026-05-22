from __future__ import annotations

import json

from sp.app import config


def test_save_homebase_vault_metadata_writes_only_non_secret_fields(tmp_path) -> None:
    entry = {
        "name": "Recovered Vault",
        "path": str(tmp_path),
        "server_url": "https://server.example",
        "verify_ssl": False,
        "vault_id": "vault-123",
        "username": "casey",
        "access_token": "access-token",
        "refresh_token": "refresh-token",
        "passphrase": "secret-passphrase",
    }

    assert config.save_homebase_vault_metadata(tmp_path, entry) is True

    metadata_path = tmp_path / ".stillpoint" / config.HOMEBASE_VAULT_METADATA_FILENAME
    payload = json.loads(metadata_path.read_text(encoding="utf-8"))

    assert payload["server_url"] == "https://server.example"
    assert payload["verify_ssl"] is False
    assert payload["vault_id"] == "vault-123"
    assert payload["vault_name"] == "Recovered Vault"
    assert "username" not in payload
    assert "access_token" not in payload
    assert "refresh_token" not in payload
    assert "passphrase" not in payload

    loaded = config.load_homebase_vault_metadata(tmp_path)
    assert loaded == {
        "mode": "connect",
        "server_url": "https://server.example",
        "verify_ssl": False,
        "vault_id": "vault-123",
        "vault_name": "Recovered Vault",
    }


def test_save_homebase_vault_profiles_omits_passphrase(monkeypatch, tmp_path) -> None:
    global_config = tmp_path / "stillpoint-config.json"
    monkeypatch.setattr(config, "GLOBAL_CONFIG", global_config)
    config.init_settings()

    config.save_homebase_vault_profiles(
        [
            {
                "id": "homebase::https://server.example::vault-123::/vaults/recovered",
                "kind": "homebase",
                "name": "Recovered Vault",
                "path": "/vaults/recovered",
                "server_url": "https://server.example",
                "verify_ssl": True,
                "vault_id": "vault-123",
                "username": "casey",
                "access_token": "access-token",
                "refresh_token": "refresh-token",
                "passphrase": "secret-passphrase",
                "store_passphrase": True,
                "sync_at_startup": False,
            }
        ]
    )

    payload = json.loads(global_config.read_text(encoding="utf-8"))
    stored = payload["homebase_vaults"][0]

    assert stored["access_token"] == "access-token"
    assert stored["refresh_token"] == "refresh-token"
    assert stored["store_passphrase"] is True
    assert stored["sync_at_startup"] is False
    assert "passphrase" not in stored

    loaded = config.load_homebase_vault_profiles()
    assert "passphrase" not in loaded[0]
    assert loaded[0]["store_passphrase"] is True
    assert loaded[0]["sync_at_startup"] is False


def test_apply_homebase_profile_keeps_passphrase_in_session_only(main_window, monkeypatch, tmp_path) -> None:
    vault_root = tmp_path / "vault"
    vault_root.mkdir(parents=True, exist_ok=True)
    saved_passphrases: list[str] = []
    saved_metadata: list[tuple[str, dict]] = []

    monkeypatch.setattr(main_window, "_ensure_config_active_vault_context", lambda: None)
    monkeypatch.setattr(main_window, "_configure_homebase_sync_for_vault", lambda: None)
    monkeypatch.setattr(main_window, "_apply_remote_mode_ui", lambda: None)
    monkeypatch.setattr(config, "save_vault_remote_mode", lambda value: None)
    monkeypatch.setattr(config, "save_homebase_remote_url", lambda value: None)
    monkeypatch.setattr(config, "save_homebase_verify_ssl", lambda value: None)
    monkeypatch.setattr(config, "save_homebase_vault_id", lambda value: None)
    monkeypatch.setattr(config, "save_homebase_username", lambda value: None)
    monkeypatch.setattr(config, "save_homebase_auth_token", lambda value: None)
    monkeypatch.setattr(config, "save_homebase_refresh_token", lambda value: None)
    monkeypatch.setattr(config, "save_homebase_store_passphrase", lambda value: None)
    monkeypatch.setattr(config, "save_homebase_auto_sync", lambda value: None)
    monkeypatch.setattr(config, "save_homebase_sync_at_startup", lambda value: None)
    monkeypatch.setattr(config, "save_homebase_interval_seconds", lambda value: None)
    monkeypatch.setattr(config, "save_homebase_push_debounce_seconds", lambda value: None)
    monkeypatch.setattr(config, "save_homebase_max_parallel_transfers", lambda value: None)
    monkeypatch.setattr(config, "save_homebase_passphrase", lambda value: saved_passphrases.append(value))
    monkeypatch.setattr(
        config,
        "save_homebase_vault_metadata",
        lambda path, profile: saved_metadata.append((str(path), dict(profile))) or True,
    )

    main_window.vault_root = str(vault_root)
    profile = {
        "id": f"homebase::https://server.example::vault-123::{vault_root}",
        "kind": "homebase",
        "name": "Recovered Vault",
        "path": str(vault_root),
        "server_url": "https://server.example",
        "verify_ssl": True,
        "vault_id": "vault-123",
        "username": "casey",
        "access_token": "access-token",
        "refresh_token": "refresh-token",
        "passphrase": "secret-passphrase",
        "store_passphrase": False,
        "auto_sync": True,
        "sync_at_startup": False,
        "interval_seconds": 60,
        "push_debounce_seconds": 3,
        "max_parallel_transfers": 3,
    }

    main_window._apply_homebase_profile(profile)

    assert main_window._load_homebase_session_passphrase(str(vault_root)) == "secret-passphrase"
    assert saved_passphrases == [""]
    assert saved_metadata[0][0] == str(vault_root)
    assert "passphrase" in saved_metadata[0][1]


def test_apply_homebase_profile_persists_passphrase_when_trusted(main_window, monkeypatch, tmp_path) -> None:
    vault_root = tmp_path / "vault"
    vault_root.mkdir(parents=True, exist_ok=True)
    saved_passphrases: list[str] = []
    saved_store_flags: list[bool] = []

    monkeypatch.setattr(main_window, "_ensure_config_active_vault_context", lambda: None)
    monkeypatch.setattr(main_window, "_configure_homebase_sync_for_vault", lambda: None)
    monkeypatch.setattr(main_window, "_apply_remote_mode_ui", lambda: None)
    monkeypatch.setattr(config, "save_vault_remote_mode", lambda value: None)
    monkeypatch.setattr(config, "save_homebase_remote_url", lambda value: None)
    monkeypatch.setattr(config, "save_homebase_verify_ssl", lambda value: None)
    monkeypatch.setattr(config, "save_homebase_vault_id", lambda value: None)
    monkeypatch.setattr(config, "save_homebase_username", lambda value: None)
    monkeypatch.setattr(config, "save_homebase_auth_token", lambda value: None)
    monkeypatch.setattr(config, "save_homebase_refresh_token", lambda value: None)
    monkeypatch.setattr(config, "save_homebase_store_passphrase", lambda value: saved_store_flags.append(value))
    monkeypatch.setattr(config, "save_homebase_passphrase", lambda value: saved_passphrases.append(value))
    monkeypatch.setattr(config, "load_homebase_passphrase", lambda default="": default)
    monkeypatch.setattr(config, "save_homebase_auto_sync", lambda value: None)
    monkeypatch.setattr(config, "save_homebase_sync_at_startup", lambda value: None)
    monkeypatch.setattr(config, "save_homebase_interval_seconds", lambda value: None)
    monkeypatch.setattr(config, "save_homebase_push_debounce_seconds", lambda value: None)
    monkeypatch.setattr(config, "save_homebase_max_parallel_transfers", lambda value: None)
    monkeypatch.setattr(config, "save_homebase_vault_metadata", lambda path, profile: True)

    main_window.vault_root = str(vault_root)
    profile = {
        "id": f"homebase::https://server.example::vault-123::{vault_root}",
        "kind": "homebase",
        "name": "Recovered Vault",
        "path": str(vault_root),
        "server_url": "https://server.example",
        "verify_ssl": True,
        "vault_id": "vault-123",
        "username": "casey",
        "access_token": "access-token",
        "refresh_token": "refresh-token",
        "passphrase": "trusted-passphrase",
        "store_passphrase": True,
        "auto_sync": True,
        "sync_at_startup": True,
        "interval_seconds": 60,
        "push_debounce_seconds": 3,
        "max_parallel_transfers": 3,
    }

    main_window._apply_homebase_profile(profile)

    assert saved_store_flags == [True]
    assert saved_passphrases == ["trusted-passphrase"]
    assert main_window._load_homebase_session_passphrase(str(vault_root)) == "trusted-passphrase"


def test_configure_homebase_sync_prompts_for_missing_passphrase(main_window, monkeypatch, tmp_path) -> None:
    vault_root = tmp_path / "vault"
    vault_root.mkdir(parents=True, exist_ok=True)
    saved_store_flags: list[bool] = []
    saved_passphrases: list[str] = []
    badge_states: list[str] = []

    class _DummySyncEngine:
        def __init__(self, cfg) -> None:
            self.cfg = cfg
            self.started = False
            self.scheduled: list[str] = []
            self.synced: list[str] = []

        def start(self) -> None:
            self.started = True

        def stop(self) -> None:
            return None

        def schedule_sync(self, reason: str) -> None:
            self.scheduled.append(reason)

        def sync_now(self, reason: str = "manual") -> None:
            self.synced.append(reason)

        def get_status(self):
            return None

    monkeypatch.setattr(main_window, "vault_root", str(vault_root))
    monkeypatch.setattr(main_window, "_is_homebase_mode_enabled", lambda: True)
    monkeypatch.setattr(main_window, "_ensure_config_active_vault_context", lambda: None)
    monkeypatch.setattr(
        main_window,
        "_prompt_homebase_passphrase_settings",
        lambda **kwargs: ("prompted-passphrase", False, True),
    )
    monkeypatch.setattr(main_window, "_ensure_homebase_watcher", lambda path: None)
    monkeypatch.setattr(main_window, "_poll_homebase_status", lambda: None)
    monkeypatch.setattr(main_window, "_refresh_homebase_user_info", lambda: None)
    monkeypatch.setattr(main_window, "_update_user_management_ui", lambda: None)
    monkeypatch.setattr(
        main_window,
        "_update_homebase_status_badge",
        lambda status: badge_states.append(status.summary if status else "none"),
    )

    monkeypatch.setattr(config, "load_homebase_remote_url", lambda default="": "https://server.example")
    monkeypatch.setattr(config, "load_homebase_auth_token", lambda default="": "access-token")
    monkeypatch.setattr(config, "load_homebase_refresh_token", lambda default="": "refresh-token")
    monkeypatch.setattr(config, "load_homebase_vault_id", lambda default="": "vault-123")
    monkeypatch.setattr(config, "ensure_homebase_vault_id", lambda: "vault-123")
    monkeypatch.setattr(config, "load_homebase_verify_ssl", lambda default=True: True)
    monkeypatch.setattr(config, "load_homebase_passphrase", lambda default="": default)
    monkeypatch.setattr(config, "load_homebase_store_passphrase", lambda default=False: False)
    monkeypatch.setattr(config, "save_homebase_store_passphrase", lambda value: saved_store_flags.append(value))
    monkeypatch.setattr(config, "save_homebase_passphrase", lambda value: saved_passphrases.append(value))
    monkeypatch.setattr(config, "load_homebase_device_id", lambda default="": "device-123")
    monkeypatch.setattr(config, "load_homebase_auto_sync", lambda default=True: True)
    monkeypatch.setattr(config, "load_homebase_sync_at_startup", lambda default=True: True)
    monkeypatch.setattr(config, "load_homebase_interval_seconds", lambda default=60: 60)
    monkeypatch.setattr(config, "load_homebase_push_debounce_seconds", lambda default=3: 3)
    monkeypatch.setattr(config, "load_homebase_max_parallel_transfers", lambda default=3: 3)
    monkeypatch.setattr(main_window, "_homebase_local_ui_token_for_url", lambda remote_url: "")
    monkeypatch.setattr(main_window, "_store_homebase_tokens", lambda access, refresh=None: None)

    import sp.app.ui.main_window as main_window_module

    monkeypatch.setattr(main_window_module, "HomebaseSyncEngine", _DummySyncEngine)

    main_window._configure_homebase_sync_for_vault()

    assert isinstance(main_window._homebase_sync_engine, _DummySyncEngine)
    assert main_window._homebase_sync_engine.started is True
    assert main_window._homebase_sync_engine.cfg.passphrase == "prompted-passphrase"
    assert main_window._homebase_sync_engine.scheduled == []
    assert main_window._homebase_sync_engine.synced == ["vault open"]
    assert saved_store_flags == [False]
    assert saved_passphrases == [""]
    assert main_window._load_homebase_session_passphrase(str(vault_root)) == "prompted-passphrase"
    assert badge_states == ["none"]


def test_configure_homebase_sync_skips_startup_sync_when_disabled(main_window, monkeypatch, tmp_path) -> None:
    vault_root = tmp_path / "vault"
    vault_root.mkdir(parents=True, exist_ok=True)

    class _DummySyncEngine:
        def __init__(self, cfg) -> None:
            self.cfg = cfg
            self.started = False
            self.synced: list[str] = []

        def start(self) -> None:
            self.started = True

        def stop(self) -> None:
            return None

        def sync_now(self, reason: str = "manual") -> None:
            self.synced.append(reason)

        def get_status(self):
            return None

    monkeypatch.setattr(main_window, "vault_root", str(vault_root))
    monkeypatch.setattr(main_window, "_is_homebase_mode_enabled", lambda: True)
    monkeypatch.setattr(main_window, "_ensure_homebase_watcher", lambda path: None)
    monkeypatch.setattr(main_window, "_poll_homebase_status", lambda: None)
    monkeypatch.setattr(main_window, "_refresh_homebase_user_info", lambda: None)
    monkeypatch.setattr(main_window, "_update_user_management_ui", lambda: None)
    monkeypatch.setattr(config, "load_homebase_remote_url", lambda default="": "https://server.example")
    monkeypatch.setattr(config, "load_homebase_auth_token", lambda default="": "access-token")
    monkeypatch.setattr(config, "load_homebase_refresh_token", lambda default="": "refresh-token")
    monkeypatch.setattr(config, "load_homebase_vault_id", lambda default="": "vault-123")
    monkeypatch.setattr(config, "ensure_homebase_vault_id", lambda: "vault-123")
    monkeypatch.setattr(config, "load_homebase_verify_ssl", lambda default=True: True)
    monkeypatch.setattr(config, "load_homebase_device_id", lambda default="": "device-123")
    monkeypatch.setattr(config, "load_homebase_auto_sync", lambda default=True: True)
    monkeypatch.setattr(config, "load_homebase_sync_at_startup", lambda default=True: False)
    monkeypatch.setattr(config, "load_homebase_interval_seconds", lambda default=60: 60)
    monkeypatch.setattr(config, "load_homebase_push_debounce_seconds", lambda default=3: 3)
    monkeypatch.setattr(config, "load_homebase_max_parallel_transfers", lambda default=3: 3)
    monkeypatch.setattr(main_window, "_homebase_local_ui_token_for_url", lambda remote_url: "")
    monkeypatch.setattr(main_window, "_store_homebase_tokens", lambda access, refresh=None: None)
    monkeypatch.setattr(main_window, "_load_homebase_session_passphrase", lambda vault_root=None: "prompted-passphrase")

    import sp.app.ui.main_window as main_window_module

    monkeypatch.setattr(main_window_module, "HomebaseSyncEngine", _DummySyncEngine)

    main_window._configure_homebase_sync_for_vault()

    assert isinstance(main_window._homebase_sync_engine, _DummySyncEngine)
    assert main_window._homebase_sync_engine.started is True
    assert main_window._homebase_sync_engine.synced == []


def test_trigger_homebase_sync_now_reconfigures_engine_before_manual_sync(main_window, monkeypatch, tmp_path) -> None:
    vault_root = tmp_path / "vault"
    vault_root.mkdir(parents=True, exist_ok=True)
    sync_reasons: list[str] = []

    class _DummyEngine:
        def sync_now(self, reason: str = "manual") -> None:
            sync_reasons.append(reason)

    class _DummyStatusBar:
        def __init__(self) -> None:
            self.messages: list[tuple[str, int]] = []

        def showMessage(self, text: str, timeout: int = 0) -> None:
            self.messages.append((text, timeout))

    status_bar = _DummyStatusBar()
    main_window.vault_root = str(vault_root)
    main_window._homebase_sync_engine = None

    monkeypatch.setattr(main_window, "_is_homebase_mode_enabled", lambda: True)
    monkeypatch.setattr(main_window, "statusBar", lambda: status_bar)

    def _configure() -> None:
        main_window._homebase_sync_engine = _DummyEngine()

    monkeypatch.setattr(main_window, "_configure_homebase_sync_for_vault", _configure)

    main_window._trigger_homebase_sync_now("badge")

    assert sync_reasons == ["badge"]
    assert status_bar.messages == [("Homebase sync requested.", 2500)]
