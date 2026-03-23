from __future__ import annotations

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
    assert ("switch", ("https://server", True, True)) in calls
    assert ("set_vault", ("/vaults/hybrid", "Hybrid Vault")) in calls
    assert ("apply_homebase", profile["id"]) in calls
    assert ("update_ui", None) in calls
    assert ("restore_history", None) in calls