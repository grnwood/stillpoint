from __future__ import annotations

import json
import os
import socket
import sqlite3

import pytest
from fastapi import HTTPException

from sp.app import config
from sp.server import api


def test_cloud_mode_redirects_runtime_state_out_of_synced_vault_and_back(
    tmp_path, monkeypatch
) -> None:
    vault = tmp_path / "OneDrive" / "Notes"
    vault.mkdir(parents=True)
    device_state = tmp_path / "device-state"
    monkeypatch.setenv("SP_CLOUD_VAULT_STATE_ROOT", str(device_state))

    config.set_active_vault(str(vault))
    try:
        original_db = vault / ".stillpoint" / "settings.db"
        assert original_db.is_file()
        legacy_lock = vault / ".stillpoint" / "stillpoint.lock"
        legacy_lock.write_text(
            json.dumps({"pid": os.getpid(), "host": "conversion-host"}),
            encoding="utf-8",
        )
        config.save_vault_remote_mode("none")

        config.transition_active_vault_remote_mode("cloud")

        marker = config.load_cloud_vault_metadata(vault)
        assert marker is not None
        local_state = device_state / marker["vault_id"]
        assert config.load_vault_remote_mode() == "cloud"
        assert config.vault_state_dir(vault) == local_state
        assert (local_state / "settings.db").is_file()
        assert not (local_state / "stillpoint.lock").exists()
        assert original_db.is_file()  # Frozen legacy snapshot; no longer opened.
        with sqlite3.connect(local_state / "settings.db") as connection:
            assert connection.execute(
                "SELECT value FROM kv WHERE key = 'vault_remote_mode'"
            ).fetchone() == ("cloud",)

        config.transition_active_vault_remote_mode("none")

        assert config.load_cloud_vault_metadata(vault) is None
        assert config.vault_state_dir(vault) == vault / ".stillpoint"
        assert original_db.is_file()
        assert config.load_vault_remote_mode() == "none"
        assert list((vault / ".stillpoint").glob("cloud-disabled-*.json"))
    finally:
        config.set_active_vault(None)


def test_cloud_marker_identifies_mode_before_vault_is_open(tmp_path, monkeypatch) -> None:
    vault = tmp_path / "Google Drive" / "Notes"
    vault.mkdir(parents=True)
    monkeypatch.setenv("SP_CLOUD_VAULT_STATE_ROOT", str(tmp_path / "local-state"))
    config.set_active_vault(str(vault))
    try:
        config.transition_active_vault_remote_mode("cloud")
    finally:
        config.set_active_vault(None)

    assert config.load_vault_remote_mode_for_path(vault) == "cloud"
    assert (vault / ".stillpoint" / "settings.db").is_file()


def test_cloud_mode_succeeds_when_windows_keeps_legacy_database_locked(
    tmp_path, monkeypatch
) -> None:
    vault = tmp_path / "OneDrive" / "Locked Notes"
    vault.mkdir(parents=True)
    device_state = tmp_path / "device-state"
    monkeypatch.setenv("SP_CLOUD_VAULT_STATE_ROOT", str(device_state))
    config.set_active_vault(str(vault))
    legacy_db = vault / ".stillpoint" / "settings.db"
    held_connection = sqlite3.connect(legacy_db)
    held_connection.execute("SELECT COUNT(*) FROM kv").fetchone()
    try:
        config.transition_active_vault_remote_mode("cloud")
        marker = config.load_cloud_vault_metadata(vault)
        assert marker is not None
        local_db = device_state / marker["vault_id"] / "settings.db"
        assert local_db.is_file()
        assert legacy_db.is_file()
        assert config.vault_settings_db_path(vault) == local_db
        assert config.load_vault_remote_mode() == "cloud"
        with sqlite3.connect(local_db) as connection:
            assert connection.execute("PRAGMA quick_check").fetchone() == ("ok",)
    finally:
        held_connection.close()
        config.set_active_vault(None)


def test_file_write_content_hash_detects_cloud_change_even_without_mtime_check(
    tmp_path, monkeypatch
) -> None:
    vault = tmp_path / "vault"
    page = vault / "Page" / "Page.md"
    page.parent.mkdir(parents=True)
    page.write_text("# Page\n\nfirst\n", encoding="utf-8")
    monkeypatch.setattr(api.vault_state, "get_root", lambda: vault)
    monkeypatch.setattr(api.config, "_vault_db_path", lambda: None)

    loaded = api.file_read(api.FilePathPayload(path="/Page/Page.md"))
    page.write_text("# Page\n\nchanged elsewhere\n", encoding="utf-8")

    with pytest.raises(HTTPException) as raised:
        api.file_write(
            api.FileWritePayload(path="/Page/Page.md", content="# Page\n\nmy edits\n"),
            if_match=f"sha256:{loaded['content_sha256']}",
            user=None,
        )

    assert raised.value.status_code == 409
    assert raised.value.detail["current_content"] == "# Page\n\nchanged elsewhere\n"
    assert raised.value.detail["current_content_sha256"] != loaded["content_sha256"]


def test_cloud_vault_discards_legacy_copied_lock_and_writes_scoped_lock(
    main_window, monkeypatch, tmp_path
) -> None:
    vault = tmp_path / "OneDrive" / "Notes"
    vault.mkdir(parents=True)
    monkeypatch.setenv("SP_CLOUD_VAULT_STATE_ROOT", str(tmp_path / "device-state"))
    config.set_active_vault(str(vault))
    try:
        config.transition_active_vault_remote_mode("cloud")
        lock_path = config.vault_state_dir(vault) / "stillpoint.lock"
        lock_path.write_text(
            json.dumps({"pid": os.getpid(), "host": socket.gethostname()}),
            encoding="utf-8",
        )
        main_window.vault_root = str(vault)

        assert main_window._check_and_acquire_vault_lock(str(vault)) is True

        owner = json.loads(lock_path.read_text(encoding="utf-8"))
        assert owner["pid"] == os.getpid()
        assert owner["state_dir"] == str(lock_path.parent.resolve())
        assert main_window._read_only is False
    finally:
        main_window._release_vault_lock()
        config.set_active_vault(None)
