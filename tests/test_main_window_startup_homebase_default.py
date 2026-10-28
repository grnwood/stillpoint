from __future__ import annotations

from sp.app import config
from sp.app.ui.main_window import MainWindow


def test_startup_opens_saved_homebase_default(main_window, monkeypatch) -> None:
    profile = {
        "id": "homebase::https://server::vault123::/vaults/hybrid",
        "kind": "homebase",
        "name": "Hybrid Vault",
        "path": "/vaults/hybrid",
        "server_url": "https://server",
        "vault_id": "vault123",
        "verify_ssl": True,
    }
    calls: list[tuple[str, object]] = []

    monkeypatch.setattr("sp.app.ui.main_window.config.load_default_vault", lambda: profile["id"])
    monkeypatch.setattr("sp.app.ui.main_window.config.load_homebase_vault_profiles", lambda: [profile])
    monkeypatch.setattr(MainWindow, "_select_vault", lambda self, startup=False, **_: False)
    monkeypatch.setattr(
        "sp.app.ui.main_window.QTimer.singleShot",
        lambda _ms, callback: calls.append(("timer", getattr(callback, "__name__", str(callback)))),
    )
    monkeypatch.setattr(
        main_window,
        "_switch_api_base",
        lambda server_url, is_remote, verify_tls: calls.append(("switch", (server_url, is_remote, verify_tls))),
    )
    monkeypatch.setattr(
        main_window,
        "_set_vault",
        lambda path, vault_name=None: calls.append(("set_vault", (path, vault_name))) or True,
    )
    monkeypatch.setattr(
        main_window,
        "_apply_homebase_profile",
        lambda selected: calls.append(("apply_homebase", selected["id"])),
    )
    monkeypatch.setattr(main_window, "_update_user_management_ui", lambda: calls.append(("update_ui", None)))
    monkeypatch.setattr(main_window, "_restore_recent_history", lambda: calls.append(("restore_history", None)))

    assert main_window.startup() is True
    assert ("switch", (main_window._local_api_base, False, True)) in calls
    assert ("set_vault", ("/vaults/hybrid", "Hybrid Vault")) in calls
    # _set_vault owns profile application; startup must not repeat it after the
    # vault has already been initialized.
    assert ("apply_homebase", profile["id"]) not in calls
    assert ("update_ui", None) in calls
    assert ("restore_history", None) in calls


def test_startup_treats_git_vault_as_local_when_homebase_default_remains(
    main_window, monkeypatch, tmp_path
) -> None:
    vault = tmp_path / "git-vault"
    vault.mkdir()
    config.set_active_vault(str(vault))
    try:
        config.save_vault_remote_mode("git")
    finally:
        config.set_active_vault(None)

    profile = {
        "id": f"homebase::https://server::vault123::{vault}",
        "kind": "homebase",
        "name": "Former Homebase Vault",
        "path": str(vault),
        "server_url": "https://server",
        "vault_id": "vault123",
    }
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr("sp.app.ui.main_window.config.load_default_vault", lambda: profile["id"])
    monkeypatch.setattr("sp.app.ui.main_window.config.load_homebase_vault_profiles", lambda: [profile])
    monkeypatch.setattr(
        "sp.app.ui.main_window.config.save_default_vault",
        lambda value: calls.append(("save_default", value)),
    )
    monkeypatch.setattr("sp.app.ui.main_window.config.save_last_vault", lambda value: None)
    monkeypatch.setattr("sp.app.ui.main_window.config.remember_vault", lambda path, name: None)
    monkeypatch.setattr(MainWindow, "_select_vault", lambda self, startup=False, **_: False)
    monkeypatch.setattr("sp.app.ui.main_window.QTimer.singleShot", lambda *_: None)
    monkeypatch.setattr(main_window, "_switch_api_base", lambda *_, **__: None)
    monkeypatch.setattr(
        main_window,
        "_set_vault",
        lambda path, vault_name=None: calls.append(("set_vault", (path, vault_name))) or True,
    )
    monkeypatch.setattr(
        main_window,
        "_apply_homebase_profile",
        lambda selected: calls.append(("apply_homebase", selected["id"])),
    )
    monkeypatch.setattr(main_window, "_update_user_management_ui", lambda: None)
    monkeypatch.setattr(main_window, "_restore_recent_history", lambda: None)

    assert main_window.startup() is True
    assert ("save_default", str(vault)) in calls
    assert ("set_vault", (str(vault), "Former Homebase Vault")) in calls
    assert not any(name == "apply_homebase" for name, _value in calls)


def test_switching_homebase_to_git_does_not_create_a_default(
    main_window, monkeypatch, tmp_path
) -> None:
    saved_defaults: list[str] = []
    remembered: list[tuple[str, str]] = []
    last_vaults: list[str] = []
    monkeypatch.setattr("sp.app.ui.main_window.config.load_default_vault", lambda: None)
    monkeypatch.setattr(
        "sp.app.ui.main_window.config.save_default_vault",
        saved_defaults.append,
    )
    monkeypatch.setattr(
        "sp.app.ui.main_window.config.remember_vault",
        lambda path, name: remembered.append((path, name)),
    )
    monkeypatch.setattr(
        "sp.app.ui.main_window.config.save_last_vault",
        last_vaults.append,
    )

    main_window._promote_git_vault_to_local_defaults(
        str(tmp_path),
        "Git Vault",
        stale_homebase_id="homebase::old-profile",
    )

    assert remembered == [(str(tmp_path), "Git Vault")]
    assert last_vaults == [str(tmp_path)]
    assert saved_defaults == []


def test_startup_repairs_accidental_unconfirmed_git_default_and_shows_picker(
    main_window, monkeypatch, tmp_path
) -> None:
    vault = tmp_path / "git-vault"
    vault.mkdir()
    config.set_active_vault(str(vault))
    try:
        config.save_vault_remote_mode("git")
    finally:
        config.set_active_vault(None)
    profile = {
        "id": f"homebase::https://server::vault123::{vault}",
        "kind": "homebase",
        "name": "Former Homebase Vault",
        "path": str(vault),
        "server_url": "https://server",
        "vault_id": "vault123",
    }
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr("sp.app.ui.main_window.config.load_default_vault", lambda: str(vault))
    monkeypatch.setattr(
        "sp.app.ui.main_window.config.load_default_vault_confirmation",
        lambda: None,
    )
    monkeypatch.setattr(
        "sp.app.ui.main_window.config.load_homebase_vault_profiles",
        lambda: [profile],
    )
    monkeypatch.setattr(
        "sp.app.ui.main_window.config.save_default_vault",
        lambda value: calls.append(("save_default", value)),
    )
    monkeypatch.setattr(
        main_window,
        "_select_vault",
        lambda startup=False, **_: calls.append(("select", startup)) or False,
    )
    monkeypatch.setattr(
        main_window,
        "_set_vault",
        lambda *_args, **_kwargs: calls.append(("set_vault", True)) or True,
    )

    assert main_window.startup() is False
    assert ("save_default", None) in calls
    assert ("select", True) in calls
    assert not any(name == "set_vault" for name, _value in calls)
