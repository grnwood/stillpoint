from __future__ import annotations

import json
from pathlib import Path

from sp.sync.crypto import derive_key_from_passphrase, encrypt_bytes, object_id_from_ciphertext
from sp.sync.engine import HomebaseSyncConfig, HomebaseSyncEngine
from sp.app.ui.main_window import MainWindow


class _FakeClient:
    def __init__(self, manifest_bytes: bytes, objects: dict[str, bytes]) -> None:
        self._manifest_bytes = manifest_bytes
        self._objects = dict(objects)

    def get_manifest(self, checkpoint_id: str) -> bytes:
        return self._manifest_bytes

    def get_object(self, object_id: str) -> bytes:
        return self._objects[object_id]


def test_apply_remote_checkpoint_skips_repeating_keep_local_conflict(tmp_path) -> None:
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    local_file = vault_root / "Page.md"
    local_file.write_text("local version", encoding="utf-8")

    cfg = HomebaseSyncConfig(
        vault_root=vault_root,
        vault_id="vault-123",
        device_id="device-local",
        remote_url="https://example.invalid",
        verify_ssl=True,
        auth_token="token",
        passphrase="secret",
    )
    engine = HomebaseSyncEngine(cfg)
    key = derive_key_from_passphrase(cfg.passphrase, cfg.vault_id)
    ciphertext = encrypt_bytes(key, b"server version")
    object_id = object_id_from_ciphertext(ciphertext)
    manifest = {
        "schema_version": 1,
        "vault_id": cfg.vault_id,
        "device_id": "device-remote",
        "entries": {
            "Page.md": {
                "object_id": object_id,
                "mtime": 1,
                "size": len(b"server version"),
                "kind": "file",
            }
        },
    }
    manifest_bytes = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    checkpoint_id = "checkpoint-abc"
    conflict_rel = "Page.sync-conflict-20260313-device-remote.md"
    engine._conflict_path.parent.mkdir(parents=True, exist_ok=True)
    engine._conflict_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "vault_id": cfg.vault_id,
                "conflicts": [
                    {
                        "ts": "2026-03-13T00:00:00Z",
                        "path": "Page.md",
                        "conflict_copy_path": conflict_rel,
                        "remote_checkpoint_id": checkpoint_id,
                        "remote_device_id": "device-remote",
                        "local_mtime": 10,
                        "remote_mtime": 1,
                        "reason": "local_newer_than_remote",
                        "resolved_at": "2026-03-13T00:01:00Z",
                        "resolution": "keep-local",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    client = _FakeClient(manifest_bytes, {object_id: ciphertext})
    applied_paths, _pulled_cache = engine._apply_remote_checkpoint(client, key, checkpoint_id)

    assert applied_paths == []
    assert local_file.read_text(encoding="utf-8") == "local version"
    assert not (vault_root / conflict_rel).exists()


def test_keep_local_resolution_removes_conflict_and_requests_sync(tmp_path, monkeypatch) -> None:
    class _DummyStatusBar:
        def __init__(self) -> None:
            self.messages = []

        def showMessage(self, message: str, timeout: int = 0) -> None:
            self.messages.append((message, timeout))

    class _DummyRightPanel:
        def __init__(self) -> None:
            self.task_refreshes = 0
            self.link_refreshes = []

        def refresh_tasks(self) -> None:
            self.task_refreshes += 1

        def refresh_links(self, path) -> None:
            self.link_refreshes.append(path)

    class _DummyEngine:
        def __init__(self) -> None:
            self.resolved = []
            self.synced = []

        def resolve_conflict_entry(self, conflict_copy_path: str, resolution: str = "merged") -> bool:
            self.resolved.append((conflict_copy_path, resolution))
            return True

        def sync_now(self, reason: str = "manual") -> None:
            self.synced.append(reason)

    class _Dummy:
        def __init__(self, vault_root: str) -> None:
            self.vault_root = vault_root
            self._homebase_sync_engine = _DummyEngine()
            self.right_panel = _DummyRightPanel()
            self.current_path = "/Elsewhere.md"
            self.refresh_tree_calls = 0
            self.open_calls = []
            self.status = _DummyStatusBar()

        def statusBar(self) -> _DummyStatusBar:
            return self.status

        def _ensure_config_active_vault_context(self) -> None:
            return None

        def _is_editor_idle_for_remote_reload(self) -> bool:
            return False

        def _refresh_tree(self) -> None:
            self.refresh_tree_calls += 1

        def _open_file(self, *args, **kwargs) -> None:
            self.open_calls.append((args, kwargs))

    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    local_file = vault_root / "Page.md"
    local_file.write_text("local version", encoding="utf-8")
    conflict_file = vault_root / "Page.sync-conflict-20260313-device-remote.md"
    conflict_file.write_text("server version", encoding="utf-8")
    entry = {
        "path": "Page.md",
        "conflict_copy_path": conflict_file.name,
        "remote_mtime": 1,
    }

    dummy = _Dummy(str(vault_root))
    monkeypatch.setattr("sp.app.ui.main_window.config.bump_tree_version", lambda: None)
    monkeypatch.setattr("sp.app.ui.main_window.config.bump_sync_revision", lambda: None)
    monkeypatch.setattr("sp.app.ui.main_window.indexer.index_page", lambda path, content: True)

    result = MainWindow._resolve_homebase_conflict_keep_local(dummy, entry)

    assert result is True
    assert local_file.read_text(encoding="utf-8") == "local version"
    assert not conflict_file.exists()
    assert dummy._homebase_sync_engine.resolved == [(conflict_file.name, "keep-local")]
    assert dummy._homebase_sync_engine.synced == ["conflict resolved (keep-local)"]
    assert dummy.right_panel.task_refreshes == 1
    assert dummy.right_panel.link_refreshes == ["/Elsewhere.md"]
    assert dummy.refresh_tree_calls == 1
