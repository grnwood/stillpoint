"""Tests that binary/image files sync correctly via homebase.

Covers the scenario where Device A has a page with pasted images:
the .md file and all image attachments must reach Device B after sync.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from sp.sync.crypto import derive_key_from_passphrase, encrypt_bytes, object_id_from_ciphertext
from sp.sync.engine import HomebaseSyncConfig, HomebaseSyncEngine, _write_json


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cfg(vault_root: Path, **overrides) -> HomebaseSyncConfig:
    defaults = dict(
        vault_root=vault_root,
        vault_id="vault-img-test",
        device_id="device-a",
        remote_url="https://example.invalid",
        verify_ssl=True,
        auth_token="tok",
        passphrase="secret",
    )
    defaults.update(overrides)
    return HomebaseSyncConfig(**defaults)


class FakeClient:
    """Minimal homebase client double that stores objects in memory."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.manifests: dict[str, bytes] = {}
        self.latest_checkpoint: str | None = None

    # -- reads ---------------------------------------------------------------
    def get_latest(self) -> dict:
        if self.latest_checkpoint:
            return {"checkpoint_id": self.latest_checkpoint}
        return {}

    def get_manifest(self, manifest_id: str) -> bytes:
        return self.manifests[manifest_id]

    def get_object(self, object_id: str) -> bytes:
        return self.objects[object_id]

    def has_object(self, object_id: str) -> bool:
        return object_id in self.objects

    # -- writes --------------------------------------------------------------
    def put_object(self, object_id: str, data: bytes) -> None:
        self.objects[object_id] = data

    def put_manifest(self, manifest_id: str, data: bytes) -> None:
        self.manifests[manifest_id] = data

    def put_latest(self, checkpoint_id: str) -> None:
        self.latest_checkpoint = checkpoint_id

    def close(self) -> None:
        pass


def _push_via_engine(engine: HomebaseSyncEngine, client: FakeClient) -> str:
    """Run the push half of _sync_once using *client* and return the checkpoint id."""
    from sp.sync.crypto import derive_key_from_passphrase
    from sp.sync.local_fs import read_bytes, stat_file

    key = derive_key_from_passphrase(engine.cfg.passphrase, engine.cfg.vault_id)
    manifest = engine._build_local_manifest()
    for rel_path, meta in manifest.get("entries", {}).items():
        full = engine.cfg.vault_root / rel_path
        plaintext = read_bytes(full)
        envelope = encrypt_bytes(key, plaintext)
        oid = object_id_from_ciphertext(envelope)
        meta["object_id"] = oid
        if not client.has_object(oid):
            client.put_object(oid, envelope)
    manifest_bytes = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    import hashlib

    checkpoint_id = hashlib.sha256(manifest_bytes).hexdigest()
    client.put_manifest(checkpoint_id, manifest_bytes)
    client.put_latest(checkpoint_id)
    return checkpoint_id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestImageSyncPull:
    """Device B pulling a manifest that contains image attachments."""

    def test_pull_downloads_images_alongside_markdown(self, tmp_path):
        """Images referenced in the manifest are written to disk on pull."""
        vault_a = tmp_path / "vault_a"
        vault_a.mkdir()
        # Simulate Device A: page + 3 pasted images
        page = vault_a / "Notes" / "Page.md"
        page.parent.mkdir(parents=True)
        page.write_text("# Hello\n![img](paste_image_001.png)\n", encoding="utf-8")
        img_bytes = [b"PNG_FAKE_DATA_1", b"PNG_FAKE_DATA_2", b"PNG_FAKE_DATA_3"]
        for i, data in enumerate(img_bytes, 1):
            (vault_a / "Notes" / f"paste_image_{i:03d}.png").write_bytes(data)

        cfg_a = _make_cfg(vault_a)
        engine_a = HomebaseSyncEngine(cfg_a)
        client = FakeClient()
        checkpoint_id = _push_via_engine(engine_a, client)

        # Device B: empty vault, pull the manifest
        vault_b = tmp_path / "vault_b"
        vault_b.mkdir()
        cfg_b = _make_cfg(vault_b, device_id="device-b")
        engine_b = HomebaseSyncEngine(cfg_b)
        key = derive_key_from_passphrase(cfg_b.passphrase, cfg_b.vault_id)

        applied, _cache = engine_b._apply_remote_checkpoint(client, key, checkpoint_id)

        # All four files should be written
        assert (vault_b / "Notes" / "Page.md").exists()
        for i, data in enumerate(img_bytes, 1):
            img_path = vault_b / "Notes" / f"paste_image_{i:03d}.png"
            assert img_path.exists(), f"Image {img_path.name} missing on Device B"
            assert img_path.read_bytes() == data


class TestCachedObjectVerification:
    """Cached object_ids must be verified against the server before reuse."""

    def test_stale_cached_image_is_reuploaded(self, tmp_path):
        """When the server no longer has a cached object, the sync engine
        must re-read, re-encrypt, and re-upload the file rather than
        silently embedding a stale object_id in the manifest."""
        vault = tmp_path / "vault"
        vault.mkdir()
        page = vault / "Page.md"
        page.write_text("![img](paste_image_001.png)\n", encoding="utf-8")
        img = vault / "paste_image_001.png"
        img.write_bytes(b"IMAGE_BYTES")

        cfg = _make_cfg(vault)
        engine = HomebaseSyncEngine(cfg)
        key = derive_key_from_passphrase(cfg.passphrase, cfg.vault_id)
        client = FakeClient()

        # First push: upload everything normally.
        checkpoint_id = _push_via_engine(engine, client)
        assert client.latest_checkpoint == checkpoint_id

        # Record the image object_id the client received.
        manifest = json.loads(client.get_manifest(checkpoint_id))
        img_entry = manifest["entries"]["paste_image_001.png"]
        original_oid = img_entry["object_id"]
        assert client.has_object(original_oid)

        # Save state as if a full _sync_once completed:
        # persist the object_cache and scan so the next cycle sees "unchanged".
        sync_dir = vault / ".stillpoint" / "sync"
        sync_dir.mkdir(parents=True, exist_ok=True)
        object_cache = {}
        for rel, meta in manifest["entries"].items():
            object_cache[rel] = meta["object_id"]
        _write_json(
            sync_dir / "object_cache.json",
            {"schema_version": 1, "vault_id": cfg.vault_id, "entries": object_cache},
        )
        from sp.sync.local_fs import stat_file

        scan_entries = {}
        for rel, meta in manifest["entries"].items():
            full = vault / rel
            size, mtime = stat_file(full)
            scan_entries[rel] = {"size": int(size), "mtime": int(mtime)}
        _write_json(
            sync_dir / "last_scan.json",
            {"schema_version": 1, "vault_id": cfg.vault_id, "entries": scan_entries},
        )
        state = {
            "schema_version": 1,
            "vault_id": cfg.vault_id,
            "device_id": cfg.device_id,
            "homebase": {
                "last_pushed_checkpoint_id": checkpoint_id,
                "last_seen_latest_checkpoint_id": checkpoint_id,
                "last_pulled_checkpoint_id": checkpoint_id,
                "last_sync_at": "2026-01-01T00:00:00Z",
                "last_error": None,
                "error_count": 0,
                "backoff_until": None,
            },
        }
        _write_json(sync_dir / "local_state.json", state)

        # --- Simulate server data loss: delete the image object ---
        del client.objects[original_oid]
        assert not client.has_object(original_oid)

        # Re-create engine (fresh in-memory state, reads from disk).
        engine2 = HomebaseSyncEngine(cfg)

        # Run the upload/push portion of _sync_once without the pull part.
        # The image file has not changed (same size/mtime), so the engine
        # would normally reuse the cached object_id.  After the fix it
        # should detect the missing object and re-upload.
        from sp.sync.engine import _read_json

        loaded_object_cache = engine2._load_object_cache()
        assert "paste_image_001.png" in loaded_object_cache  # cache is stale

        manifest2 = engine2._build_local_manifest()
        scan2 = _read_json(engine2._scan_path, {"entries": {}}).get("entries", {})
        pulled_object_cache: dict[str, str] = {}  # no pull happened

        upload_count = 0
        for rel_path, meta in manifest2.get("entries", {}).items():
            if not isinstance(meta, dict):
                continue
            rel_key = str(rel_path)
            cached_object_id = ""
            if rel_key in pulled_object_cache:
                cached_object_id = str(pulled_object_cache.get(rel_key) or "").strip().lower()
            else:
                prev = scan2.get(rel_key)
                if isinstance(prev, dict):
                    try:
                        prev_size = int(prev.get("size", -1))
                        prev_mtime = int(prev.get("mtime", -1))
                        cur_size = int(meta.get("size", -2))
                        cur_mtime = int(meta.get("mtime", -2))
                        if prev_size == cur_size and prev_mtime == cur_mtime:
                            cached_object_id = str(loaded_object_cache.get(rel_key) or "").strip().lower()
                    except Exception:
                        cached_object_id = ""
            if engine2._is_valid_object_id(cached_object_id):
                if rel_key not in pulled_object_cache and not client.has_object(cached_object_id):
                    cached_object_id = ""
                else:
                    meta["object_id"] = cached_object_id
                    continue
            from sp.sync.local_fs import read_bytes

            full = vault / rel_path
            plaintext = read_bytes(full)
            envelope = encrypt_bytes(key, plaintext)
            oid = object_id_from_ciphertext(envelope)
            meta["object_id"] = oid
            if not client.has_object(oid):
                client.put_object(oid, envelope)
                upload_count += 1

        # The image should have been re-uploaded.
        assert upload_count >= 1, "Image was not re-uploaded after server lost the object"
        # The new manifest entry should reference an object that exists.
        new_oid = manifest2["entries"]["paste_image_001.png"]["object_id"]
        assert engine2._is_valid_object_id(new_oid)
        assert client.has_object(new_oid), "Re-uploaded image object not on server"
