from __future__ import annotations

import sqlite3

import pytest
from fastapi import HTTPException

from sp.app import config
from sp.server import api


def test_cloud_mode_moves_runtime_state_out_of_synced_vault_and_back(
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
        config.save_vault_remote_mode("none")

        config.transition_active_vault_remote_mode("cloud")

        marker = config.load_cloud_vault_metadata(vault)
        assert marker is not None
        local_state = device_state / marker["vault_id"]
        assert config.load_vault_remote_mode() == "cloud"
        assert config.vault_state_dir(vault) == local_state
        assert (local_state / "settings.db").is_file()
        assert not original_db.exists()
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
    assert not (vault / ".stillpoint" / "settings.db").exists()


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
