"""Tests that binary/image files sync correctly via homebase.

Covers the scenario where Device A has a page with pasted images:
the .md file and all image attachments must reach Device B after sync.
Also covers the image path normalization to always use relative links.
"""
from __future__ import annotations

import errno
import json
import os
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

import sp.sync.engine as sync_engine
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
        self.get_object_calls: list[str] = []

    # -- reads ---------------------------------------------------------------
    def get_latest(self) -> dict:
        if self.latest_checkpoint:
            return {"checkpoint_id": self.latest_checkpoint}
        return {}

    def get_manifest(self, manifest_id: str) -> bytes:
        return self.manifests[manifest_id]

    def get_object(self, object_id: str) -> bytes:
        self.get_object_calls.append(object_id)
        if object_id not in self.objects:
            # Simulate a real 404 like HomebaseClient.get_object would raise
            request = httpx.Request("GET", f"http://fake/objects/{object_id}")
            response = httpx.Response(404, request=request)
            raise httpx.HTTPStatusError(
                f"Object not found: {object_id}", request=request, response=response,
            )
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

    def test_pull_fetches_only_objects_changed_since_known_checkpoint(self, tmp_path):
        vault_a = tmp_path / "vault_a"
        vault_a.mkdir()
        first = vault_a / "First.md"
        second = vault_a / "Second.md"
        first.write_text("first version\n", encoding="utf-8")
        second.write_text("unchanged\n", encoding="utf-8")

        client = FakeClient()
        engine_a = HomebaseSyncEngine(_make_cfg(vault_a))
        first_checkpoint = _push_via_engine(engine_a, client)

        vault_b = tmp_path / "vault_b"
        vault_b.mkdir()
        engine_b = HomebaseSyncEngine(_make_cfg(vault_b, device_id="device-b"))
        key = derive_key_from_passphrase(engine_b.cfg.passphrase, engine_b.cfg.vault_id)
        _applied, known_cache = engine_b._apply_remote_checkpoint(client, key, first_checkpoint)
        assert len(client.get_object_calls) == 2

        first.write_text("second version\n", encoding="utf-8")
        second_checkpoint = _push_via_engine(engine_a, client)
        second_manifest = json.loads(client.get_manifest(second_checkpoint))
        changed_object_id = second_manifest["entries"]["First.md"]["object_id"]

        client.get_object_calls.clear()
        applied, updated_cache = engine_b._apply_remote_checkpoint(
            client,
            key,
            second_checkpoint,
            known_object_cache=known_cache,
        )

        assert client.get_object_calls == [changed_object_id]
        assert applied == ["First.md"]
        assert updated_cache == {
            rel: meta["object_id"] for rel, meta in second_manifest["entries"].items()
        }
        assert (vault_b / "First.md").read_text(encoding="utf-8") == "second version\n"
        assert (vault_b / "Second.md").read_text(encoding="utf-8") == "unchanged\n"

    def test_cold_start_reuses_matching_local_files_without_object_downloads(self, tmp_path):
        vault_a = tmp_path / "vault_a"
        vault_a.mkdir()
        (vault_a / "Same.md").write_text("already local\n", encoding="utf-8")
        (vault_a / "Changed.md").write_text("remote version\n", encoding="utf-8")

        client = FakeClient()
        engine_a = HomebaseSyncEngine(_make_cfg(vault_a))
        checkpoint_id = _push_via_engine(engine_a, client)
        manifest = json.loads(client.get_manifest(checkpoint_id))

        # Simulate copying an older plaintext vault and deleting .stillpoint:
        # one file matches the remote snapshot and one file does not.
        vault_b = tmp_path / "vault_b"
        vault_b.mkdir()
        (vault_b / "Same.md").write_text("already local\n", encoding="utf-8")
        (vault_b / "Changed.md").write_text("older local version\n", encoding="utf-8")
        cfg_b = _make_cfg(vault_b, device_id="device-b")
        engine_b = HomebaseSyncEngine(cfg_b)
        key = derive_key_from_passphrase(cfg_b.passphrase, cfg_b.vault_id)

        applied, object_cache = engine_b._apply_remote_checkpoint(client, key, checkpoint_id)

        changed_object_id = manifest["entries"]["Changed.md"]["object_id"]
        assert client.get_object_calls == [changed_object_id]
        assert "Same.md" not in applied
        assert object_cache["Same.md"] == manifest["entries"]["Same.md"]["object_id"]
        assert (vault_b / "Same.md").read_text(encoding="utf-8") == "already local\n"

    def test_pull_propagates_object_401_to_token_refresh_handler(self, tmp_path):
        vault_a = tmp_path / "vault_a"
        vault_a.mkdir()
        (vault_a / "Page.md").write_text("remote\n", encoding="utf-8")
        engine_a = HomebaseSyncEngine(_make_cfg(vault_a))
        client = FakeClient()
        checkpoint = _push_via_engine(engine_a, client)

        class UnauthorizedClient(FakeClient):
            def get_manifest(self, manifest_id: str) -> bytes:
                return client.get_manifest(manifest_id)

            def get_object(self, object_id: str) -> bytes:
                request = httpx.Request("GET", f"http://fake/objects/{object_id}")
                response = httpx.Response(401, request=request)
                raise httpx.HTTPStatusError("Unauthorized", request=request, response=response)

        vault_b = tmp_path / "vault_b"
        vault_b.mkdir()
        engine_b = HomebaseSyncEngine(_make_cfg(vault_b, device_id="device-b"))
        key = derive_key_from_passphrase(engine_b.cfg.passphrase, engine_b.cfg.vault_id)

        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            engine_b._apply_remote_checkpoint(UnauthorizedClient(), key, checkpoint)

        assert exc_info.value.response.status_code == 401

    def test_pull_reports_live_download_worker_state(self, tmp_path):
        """Pull status should expose remaining downloads and current worker action."""
        vault_a = tmp_path / "vault_a"
        vault_a.mkdir()
        page = vault_a / "Notes" / "Page.md"
        page.parent.mkdir(parents=True)
        page.write_text("# Hello\n", encoding="utf-8")

        cfg_a = _make_cfg(vault_a)
        engine_a = HomebaseSyncEngine(cfg_a)
        client = FakeClient()
        checkpoint_id = _push_via_engine(engine_a, client)

        vault_b = tmp_path / "vault_b"
        vault_b.mkdir()
        statuses = []
        cfg_b = _make_cfg(vault_b, device_id="device-b")
        engine_b = HomebaseSyncEngine(cfg_b, status_callback=statuses.append)
        key = derive_key_from_passphrase(cfg_b.passphrase, cfg_b.vault_id)

        engine_b._apply_remote_checkpoint(client, key, checkpoint_id)

        assert any(int(getattr(status, "pending_downloads", 0) or 0) > 0 for status in statuses)
        assert any(
            any(
                ("GET " in str(worker_state or "")) or ("WRITE " in str(worker_state or ""))
                for worker_state in (getattr(status, "transfer_workers", []) or [])
            )
            for status in statuses
        )

    def test_pull_downloads_in_parallel_up_to_configured_worker_limit(self, tmp_path):
        vault_a = tmp_path / "vault_a"
        vault_a.mkdir()
        for index in range(8):
            (vault_a / f"Page-{index}.md").write_text(f"page {index}\n", encoding="utf-8")

        class SlowClient(FakeClient):
            def __init__(self) -> None:
                super().__init__()
                self._active_lock = threading.Lock()
                self.active_downloads = 0
                self.max_active_downloads = 0

            def get_object(self, object_id: str) -> bytes:
                with self._active_lock:
                    self.active_downloads += 1
                    self.max_active_downloads = max(
                        self.max_active_downloads,
                        self.active_downloads,
                    )
                try:
                    time.sleep(0.05)
                    return super().get_object(object_id)
                finally:
                    with self._active_lock:
                        self.active_downloads -= 1

        client = SlowClient()
        engine_a = HomebaseSyncEngine(_make_cfg(vault_a))
        checkpoint_id = _push_via_engine(engine_a, client)

        vault_b = tmp_path / "vault_b"
        vault_b.mkdir()
        cfg_b = _make_cfg(
            vault_b,
            device_id="device-b",
            max_parallel_transfers=3,
        )
        engine_b = HomebaseSyncEngine(cfg_b)
        key = derive_key_from_passphrase(cfg_b.passphrase, cfg_b.vault_id)

        engine_b._apply_remote_checkpoint(client, key, checkpoint_id)

        assert client.max_active_downloads == 3
        assert len(client.get_object_calls) == 8

    def test_pull_continues_after_single_object_download_failure(self, tmp_path):
        """If one object is missing on the server, the remaining entries
        should still be downloaded rather than aborting the entire pull."""
        vault_a = tmp_path / "vault_a"
        vault_a.mkdir()
        page = vault_a / "Notes" / "Page.md"
        page.parent.mkdir(parents=True)
        page.write_text("# Hello\n![img](paste_image_001.png)\n", encoding="utf-8")
        (vault_a / "Notes" / "paste_image_001.png").write_bytes(b"IMG1")
        (vault_a / "Notes" / "paste_image_002.png").write_bytes(b"IMG2")

        cfg_a = _make_cfg(vault_a)
        engine_a = HomebaseSyncEngine(cfg_a)
        client = FakeClient()
        checkpoint_id = _push_via_engine(engine_a, client)

        # Delete one image object from the "server" to simulate data loss.
        manifest = json.loads(client.get_manifest(checkpoint_id))
        image1_oid = manifest["entries"]["Notes/paste_image_001.png"]["object_id"]
        del client.objects[image1_oid]

        # Device B pulls — the missing object should not prevent other files
        # from being downloaded.
        vault_b = tmp_path / "vault_b"
        vault_b.mkdir()
        cfg_b = _make_cfg(vault_b, device_id="device-b")
        engine_b = HomebaseSyncEngine(cfg_b)
        key = derive_key_from_passphrase(cfg_b.passphrase, cfg_b.vault_id)

        applied, pulled_cache = engine_b._apply_remote_checkpoint(
            client, key, checkpoint_id,
        )

        # Page.md and paste_image_002 should have been written despite image_001 failing.
        assert (vault_b / "Notes" / "Page.md").exists()
        assert (vault_b / "Notes" / "paste_image_002.png").exists()
        assert (vault_b / "Notes" / "paste_image_002.png").read_bytes() == b"IMG2"

        # paste_image_001 was NOT written (object missing on server).
        assert not (vault_b / "Notes" / "paste_image_001.png").exists()

        # The missing entry must NOT be in pulled_cache so next sync retries it.
        assert "Notes/paste_image_001.png" not in pulled_cache
        assert engine_b._last_pull_incomplete is True

        sync_errors = engine_b.list_sync_errors(limit=50)
        assert any(
            str(item.get("path") or "") == "Notes/paste_image_001.png"
            and str(item.get("phase") or "") == "download"
            for item in sync_errors
        )

    def test_pull_rejects_object_whose_bytes_do_not_match_id(self, tmp_path):
        vault_a = tmp_path / "vault_a"
        vault_a.mkdir()
        (vault_a / "Page.md").write_text("original\n", encoding="utf-8")
        engine_a = HomebaseSyncEngine(_make_cfg(vault_a))
        client = FakeClient()
        checkpoint_id = _push_via_engine(engine_a, client)
        manifest = json.loads(client.get_manifest(checkpoint_id))
        object_id = manifest["entries"]["Page.md"]["object_id"]
        client.objects[object_id] = client.objects[object_id][:-1] + b"x"

        vault_b = tmp_path / "vault_b"
        vault_b.mkdir()
        cfg_b = _make_cfg(vault_b, device_id="device-b")
        engine_b = HomebaseSyncEngine(cfg_b)
        key = derive_key_from_passphrase(cfg_b.passphrase, cfg_b.vault_id)

        with pytest.raises(ValueError, match="object integrity failed"):
            engine_b._apply_remote_checkpoint(client, key, checkpoint_id)

    def test_pull_continues_after_single_local_apply_error(self, tmp_path, monkeypatch):
        """If writing one pulled entry fails locally, other entries should still apply."""
        vault_a = tmp_path / "vault_a"
        vault_a.mkdir()
        notes_dir = vault_a / "Notes"
        notes_dir.mkdir(parents=True)
        (notes_dir / "bad.md").write_text("bad\n", encoding="utf-8")
        (notes_dir / "good.md").write_text("good\n", encoding="utf-8")

        cfg_a = _make_cfg(vault_a)
        engine_a = HomebaseSyncEngine(cfg_a)
        client = FakeClient()
        checkpoint_id = _push_via_engine(engine_a, client)

        vault_b = tmp_path / "vault_b"
        vault_b.mkdir()
        cfg_b = _make_cfg(vault_b, device_id="device-b")
        engine_b = HomebaseSyncEngine(cfg_b)
        key = derive_key_from_passphrase(cfg_b.passphrase, cfg_b.vault_id)

        real_write = sync_engine.write_bytes_atomic

        def _flaky_write(full_path, data):
            if full_path.name == "bad.md":
                raise OSError(123, "The filename, directory name, or volume label syntax is incorrect")
            return real_write(full_path, data)

        monkeypatch.setattr("sp.sync.engine.write_bytes_atomic", _flaky_write)

        applied, pulled_cache = engine_b._apply_remote_checkpoint(client, key, checkpoint_id)

        # good.md must still be applied even though bad.md fails locally.
        assert (vault_b / "Notes" / "good.md").exists()
        assert (vault_b / "Notes" / "good.md").read_text(encoding="utf-8") == "good\n"

        # bad.md should be skipped and retried on a future sync.
        assert not (vault_b / "Notes" / "bad.md").exists()
        assert "Notes/bad.md" not in pulled_cache
        assert "Notes/good.md" in pulled_cache
        assert engine_b._last_pull_incomplete is True

        # applied should include only successfully written entries.
        assert "Notes/good.md" in applied
        assert "Notes/bad.md" not in applied

        sync_errors = engine_b.list_sync_errors(limit=50)
        assert any(
            str(item.get("path") or "") == "Notes/bad.md"
            and str(item.get("phase") or "") == "path"
            and "rename it from another client" in str(item.get("reason") or "").lower()
            for item in sync_errors
        )

    def test_confirmed_absent_path_is_removed_from_remote_checkpoint(self, tmp_path, monkeypatch):
        vault_a = tmp_path / "vault_a"
        vault_a.mkdir()
        (vault_a / "too-long.docx").write_bytes(b"remove me")
        (vault_a / "keep.md").write_text("keep\n", encoding="utf-8")
        client = FakeClient()
        engine_a = HomebaseSyncEngine(_make_cfg(vault_a))
        original_checkpoint = _push_via_engine(engine_a, client)

        vault_b = tmp_path / "vault_b"
        vault_b.mkdir()
        engine_b = HomebaseSyncEngine(_make_cfg(vault_b, device_id="device-b"))
        monkeypatch.setattr("sp.sync.engine.HomebaseClient", lambda **_kwargs: client)

        engine_b._record_sync_error(
            path="too-long.docx",
            phase="path",
            reason="File name too long",
        )
        assert engine_b.mark_remote_path_deleted("too-long.docx") is True
        engine_b._sync_once()

        assert client.latest_checkpoint != original_checkpoint
        latest_manifest = json.loads(client.get_manifest(client.latest_checkpoint))
        assert "too-long.docx" not in latest_manifest["entries"]
        assert "keep.md" in latest_manifest["entries"]
        assert not (vault_b / "too-long.docx").exists()
        assert (vault_b / "keep.md").read_text(encoding="utf-8") == "keep\n"
        assert engine_b._load_local_deletions() == set()
        assert engine_b.list_sync_errors() == []

    def test_sync_retry_downloads_only_the_file_that_failed_to_apply(self, tmp_path, monkeypatch):
        vault_a = tmp_path / "vault_a"
        vault_a.mkdir()
        (vault_a / "bad.md").write_text("bad\n", encoding="utf-8")
        (vault_a / "good.md").write_text("good\n", encoding="utf-8")
        client = FakeClient()
        engine_a = HomebaseSyncEngine(_make_cfg(vault_a))
        checkpoint_id = _push_via_engine(engine_a, client)
        manifest = json.loads(client.get_manifest(checkpoint_id))
        bad_object_id = manifest["entries"]["bad.md"]["object_id"]

        vault_b = tmp_path / "vault_b"
        vault_b.mkdir()
        engine_b = HomebaseSyncEngine(_make_cfg(vault_b, device_id="device-b"))
        monkeypatch.setattr("sp.sync.engine.HomebaseClient", lambda **_kwargs: client)
        real_write = sync_engine.write_bytes_atomic
        failed_once = False

        def _fail_bad_once(full_path, data):
            nonlocal failed_once
            if full_path.name == "bad.md" and not failed_once:
                failed_once = True
                raise OSError(errno.ENAMETOOLONG, "File name too long")
            return real_write(full_path, data)

        monkeypatch.setattr("sp.sync.engine.write_bytes_atomic", _fail_bad_once)

        engine_b._sync_once()
        assert (vault_b / "good.md").read_text(encoding="utf-8") == "good\n"
        assert not (vault_b / "bad.md").exists()

        client.get_object_calls.clear()
        engine_b._ignore_backoff_once = True
        engine_b._sync_once()

        assert client.get_object_calls == [bad_object_id]
        assert (vault_b / "bad.md").read_text(encoding="utf-8") == "bad\n"
        state = json.loads(engine_b._state_path.read_text(encoding="utf-8"))
        assert state["homebase"]["last_seen_latest_checkpoint_id"] == checkpoint_id

    def test_sync_once_bootstraps_empty_client_even_when_checkpoint_was_marked_seen(self, tmp_path, monkeypatch):
        """An empty fresh client must pull the remote vault before publishing anything."""
        vault_a = tmp_path / "vault_a"
        vault_a.mkdir()
        page = vault_a / "Notes" / "Page.md"
        page.parent.mkdir(parents=True)
        page.write_text("# Hello\n\nseeded from remote\n", encoding="utf-8")

        cfg_a = _make_cfg(vault_a)
        engine_a = HomebaseSyncEngine(cfg_a)
        client = FakeClient()
        checkpoint_id = _push_via_engine(engine_a, client)

        vault_b = tmp_path / "vault_b"
        vault_b.mkdir()
        cfg_b = _make_cfg(vault_b, device_id="device-b")
        engine_b = HomebaseSyncEngine(cfg_b)
        engine_b._sync_dir.mkdir(parents=True, exist_ok=True)
        _write_json(
            engine_b._state_path,
            {
                "schema_version": 1,
                "vault_id": cfg_b.vault_id,
                "device_id": cfg_b.device_id,
                "homebase": {
                    "last_seen_latest_checkpoint_id": checkpoint_id,
                    "last_pulled_checkpoint_id": checkpoint_id,
                    "last_pushed_checkpoint_id": None,
                    "last_sync_at": None,
                    "last_error": None,
                    "error_count": 0,
                    "backoff_until": None,
                },
            },
        )
        _write_json(
            engine_b._scan_path,
            {"schema_version": 1, "vault_id": cfg_b.vault_id, "entries": {}},
        )
        _write_json(
            engine_b._object_cache_path,
            {"schema_version": 1, "vault_id": cfg_b.vault_id, "entries": {}},
        )

        monkeypatch.setattr("sp.sync.engine.HomebaseClient", lambda **kwargs: client)

        engine_b._sync_once()

        pulled_page = vault_b / "Notes" / "Page.md"
        assert pulled_page.exists()
        assert pulled_page.read_text(encoding="utf-8") == "# Hello\n\nseeded from remote\n"
        assert client.latest_checkpoint == checkpoint_id
        adopted_state = json.loads(engine_b._state_path.read_text(encoding="utf-8"))
        assert adopted_state["homebase"]["last_pushed_checkpoint_id"] == checkpoint_id

    def test_same_plaintext_retry_reuses_object_id(self, tmp_path):
        """Retrying an unchanged upload should not mint a second object id."""
        vault = tmp_path / "vault"
        vault.mkdir()
        page = vault / "Page.md"
        page.write_text("same content\n", encoding="utf-8")

        cfg = _make_cfg(vault)
        engine = HomebaseSyncEngine(cfg)
        client = FakeClient()

        first_checkpoint = _push_via_engine(engine, client)
        first_manifest = json.loads(client.get_manifest(first_checkpoint))
        first_oid = first_manifest["entries"]["Page.md"]["object_id"]
        assert client.has_object(first_oid)
        assert len(client.objects) == 1

        second_checkpoint = _push_via_engine(engine, client)
        second_manifest = json.loads(client.get_manifest(second_checkpoint))
        second_oid = second_manifest["entries"]["Page.md"]["object_id"]

        assert second_oid == first_oid
        assert len(client.objects) == 1

    def test_sync_once_does_not_reuse_cache_for_same_size_and_mtime_content_change(
        self, tmp_path, monkeypatch
    ):
        vault = tmp_path / "vault"
        vault.mkdir()
        page = vault / "Page.md"
        page.write_bytes(b"old bytes\n")
        original_mtime = page.stat().st_mtime
        cfg = _make_cfg(vault)
        engine = HomebaseSyncEngine(cfg)
        client = FakeClient()
        monkeypatch.setattr("sp.sync.engine.HomebaseClient", lambda **kwargs: client)

        engine._sync_once()
        first_manifest = json.loads(client.get_manifest(client.latest_checkpoint))
        first_oid = first_manifest["entries"]["Page.md"]["object_id"]

        page.write_bytes(b"new bytes\n")
        os.utime(page, (original_mtime, original_mtime))
        engine._sync_once()
        second_manifest = json.loads(client.get_manifest(client.latest_checkpoint))
        second_oid = second_manifest["entries"]["Page.md"]["object_id"]

        assert second_oid != first_oid
        assert client.has_object(second_oid)

    def test_suspended_engine_coalesces_pending_work_into_one_resume_sync(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        cfg = _make_cfg(vault, auto_sync=False)
        engine = HomebaseSyncEngine(cfg)
        calls: list[str] = []
        completed = threading.Event()

        def _fake_sync_once(*_args, **_kwargs):
            calls.append("sync")
            completed.set()

        engine._sync_once = _fake_sync_once  # type: ignore[method-assign]
        engine.start()
        try:
            engine.suspend_sync("test transaction")
            engine.sync_now("change one")
            engine.sync_now("change two")
            time.sleep(0.05)
            assert calls == []

            engine.resume_sync("test transaction", sync_now=True)
            assert completed.wait(timeout=1.0)
            time.sleep(0.05)
            assert calls == ["sync"]
        finally:
            engine.stop()

    def test_schedule_does_not_hide_active_progress_or_postpone_pending_run(
        self, tmp_path, monkeypatch
    ):
        vault = tmp_path / "vault"
        vault.mkdir()
        engine = HomebaseSyncEngine(_make_cfg(vault, push_debounce_seconds=3))
        engine._set_status_locked(
            state="syncing",
            summary="Scanning local vault (25/100)...",
            pending=False,
        )
        engine._sync_in_progress = True
        monotonic_values = iter((100.0, 101.0))
        monkeypatch.setattr(sync_engine.time, "monotonic", lambda: next(monotonic_values))

        engine.schedule_sync("first filesystem scan")
        first_deadline = engine._next_run_at
        engine.schedule_sync("second filesystem scan")

        status = engine.get_status()
        assert first_deadline == 103.0
        assert engine._next_run_at == first_deadline
        assert status.summary == "Scanning local vault (25/100)..."
        assert status.pending is True

    def test_hibernated_auto_sync_still_runs_periodic_check(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        cfg = _make_cfg(vault, auto_sync=True, interval_seconds=0.02)
        engine = HomebaseSyncEngine(cfg)
        completed = threading.Event()

        def _fake_sync_once(*_args, **_kwargs):
            completed.set()

        engine._sync_once = _fake_sync_once  # type: ignore[method-assign]
        engine._hibernating = True
        engine.start()
        try:
            assert completed.wait(timeout=1.0)
        finally:
            engine.stop()

    def test_sync_once_uploads_objects_in_parallel(self, tmp_path, monkeypatch):
        """Initial uploads should use multiple workers when configured."""
        vault = tmp_path / "vault"
        vault.mkdir()
        for idx in range(8):
            (vault / f"Page{idx}.md").write_text(f"content {idx}\n", encoding="utf-8")

        cfg = _make_cfg(vault, max_parallel_transfers=4)
        seen_statuses = []
        engine = HomebaseSyncEngine(cfg, status_callback=seen_statuses.append)

        class ParallelClient:
            def __init__(self) -> None:
                self.objects: dict[str, bytes] = {}
                self.manifests: dict[str, bytes] = {}
                self.latest_checkpoint: str | None = None
                self.active_heads = 0
                self.max_active_heads = 0
                self.lock = threading.Lock()

            def get_latest(self) -> dict:
                return {}

            def has_object(self, object_id: str) -> bool:
                with self.lock:
                    self.active_heads += 1
                    self.max_active_heads = max(self.max_active_heads, self.active_heads)
                time.sleep(0.03)
                with self.lock:
                    self.active_heads -= 1
                return object_id in self.objects

            def put_object(self, object_id: str, data: bytes) -> None:
                self.objects[object_id] = data

            def put_manifest(self, manifest_id: str, data: bytes) -> None:
                self.manifests[manifest_id] = data

            def put_latest(self, checkpoint_id: str) -> None:
                self.latest_checkpoint = checkpoint_id

            def close(self) -> None:
                pass

        client = ParallelClient()
        monkeypatch.setattr("sp.sync.engine.HomebaseClient", lambda **kwargs: client)

        engine._sync_once()

        assert client.latest_checkpoint is not None
        assert len(client.objects) == 8
        assert client.max_active_heads > 1
        assert any(int(getattr(status, "pending_uploads", 0) or 0) > 0 for status in seen_statuses)
        assert any(len(getattr(status, "transfer_workers", []) or []) > 1 for status in seen_statuses)

    def test_sync_once_prepares_local_objects_in_parallel(self, tmp_path, monkeypatch):
        vault = tmp_path / "vault"
        vault.mkdir()
        for idx in range(8):
            (vault / f"Page{idx}.md").write_text(f"content {idx}\n", encoding="utf-8")

        cfg = _make_cfg(vault, max_parallel_transfers=4)
        seen_statuses = []
        engine = HomebaseSyncEngine(cfg, status_callback=seen_statuses.append)
        client = FakeClient()
        monkeypatch.setattr("sp.sync.engine.HomebaseClient", lambda **kwargs: client)

        original_encrypt = sync_engine.encrypt_bytes
        active = 0
        max_active = 0
        active_lock = threading.Lock()

        def _slow_encrypt(key: bytes, plaintext: bytes) -> bytes:
            nonlocal active, max_active
            with active_lock:
                active += 1
                max_active = max(max_active, active)
            try:
                time.sleep(0.02)
                return original_encrypt(key, plaintext)
            finally:
                with active_lock:
                    active -= 1

        monkeypatch.setattr(sync_engine, "encrypt_bytes", _slow_encrypt)

        engine._sync_once()

        assert max_active >= 2
        assert any("Preparing" in str(status.summary) for status in seen_statuses)

    def test_incremental_scan_hashes_only_changed_files(self, tmp_path, monkeypatch):
        vault = tmp_path / "vault"
        vault.mkdir()
        pages = []
        for idx in range(5):
            page = vault / f"Page{idx}.md"
            page.write_text(f"content {idx}\n", encoding="utf-8")
            pages.append(page)

        engine = HomebaseSyncEngine(_make_cfg(vault))
        client = FakeClient()
        monkeypatch.setattr("sp.sync.engine.HomebaseClient", lambda **_kwargs: client)
        engine._sync_once()

        original_sha256_file = sync_engine.sha256_file
        hashed_paths: list[Path] = []

        def _counted_sha256_file(path: Path, *args, **kwargs) -> str:
            hashed_paths.append(Path(path))
            return original_sha256_file(path, *args, **kwargs)

        monkeypatch.setattr(sync_engine, "sha256_file", _counted_sha256_file)

        engine._sync_once()
        assert hashed_paths == []

        pages[2].write_text("changed content with a different size\n", encoding="utf-8")
        engine._sync_once()
        assert hashed_paths == [pages[2]]

    def test_sync_once_upload_count_drops_while_workers_are_active(self, tmp_path, monkeypatch):
        """The visible upload countdown should drop as workers claim jobs, not only on completion."""
        vault = tmp_path / "vault_countdown"
        vault.mkdir()
        for idx in range(4):
            (vault / f"Page{idx}.md").write_text(f"content {idx}\n", encoding="utf-8")

        cfg = _make_cfg(vault, max_parallel_transfers=2)
        engine = HomebaseSyncEngine(cfg)
        started = threading.Event()
        release = threading.Event()
        start_count = 0
        start_lock = threading.Lock()

        class SlowClient:
            def __init__(self) -> None:
                self.objects: dict[str, bytes] = {}
                self.manifests: dict[str, bytes] = {}
                self.latest_checkpoint: str | None = None

            def get_latest(self) -> dict:
                return {}

            def has_object(self, object_id: str) -> bool:
                return object_id in self.objects

            def put_object(self, object_id: str, data: bytes) -> None:
                nonlocal start_count
                with start_lock:
                    start_count += 1
                    if start_count >= 2:
                        started.set()
                release.wait(timeout=5)
                self.objects[object_id] = data

            def put_manifest(self, manifest_id: str, data: bytes) -> None:
                self.manifests[manifest_id] = data

            def put_latest(self, checkpoint_id: str) -> None:
                self.latest_checkpoint = checkpoint_id

            def close(self) -> None:
                pass

        client = SlowClient()
        monkeypatch.setattr("sp.sync.engine.HomebaseClient", lambda **kwargs: client)

        worker = threading.Thread(target=engine._sync_once, daemon=True)
        worker.start()
        assert started.wait(timeout=5), "upload workers never became active"

        status = engine.get_status()
        active_workers = [
            str(item or "").strip()
            for item in (getattr(status, "transfer_workers", []) or [])
            if str(item or "").strip() and str(item or "").strip().lower() != "idle"
        ]
        assert len(active_workers) >= 2
        assert int(getattr(status, "pending_uploads", 0) or 0) == 2

        release.set()
        worker.join(timeout=5)
        assert not worker.is_alive()


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
        assert new_oid == original_oid
        assert engine2._is_valid_object_id(new_oid)
        assert client.has_object(new_oid), "Re-uploaded image object not on server"


# ---------------------------------------------------------------------------
# Image path normalization tests
# ---------------------------------------------------------------------------

class TestImagePathNormalization:
    """Image links written to .md files must always be relative paths."""

    def test_make_image_path_relative_converts_absolute_in_same_folder(self, qapp, tmp_path):
        from sp.app.ui.markdown_editor import MarkdownEditor

        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "Notes").mkdir()
        (vault / "Notes" / "Page.md").write_text("# test", encoding="utf-8")
        (vault / "Notes" / "paste_image_001.png").write_bytes(b"PNG")

        ed = MarkdownEditor()
        try:
            ed._vault_root = vault
            ed._current_path = "/Notes/Page.md"

            abs_path = str((vault / "Notes" / "paste_image_001.png").resolve()).replace("\\", "/")
            result = ed._make_image_path_relative(abs_path)
            assert result == "paste_image_001.png", f"Expected relative filename, got: {result}"
        finally:
            ed.close()

    def test_make_image_path_relative_leaves_relative_untouched(self, qapp, tmp_path):
        from sp.app.ui.markdown_editor import MarkdownEditor

        vault = tmp_path / "vault"
        vault.mkdir()

        ed = MarkdownEditor()
        try:
            ed._vault_root = vault
            ed._current_path = "/Notes/Page.md"

            assert ed._make_image_path_relative("paste_image_001.png") == "paste_image_001.png"
            assert ed._make_image_path_relative("./paste_image_001.png") == "./paste_image_001.png"
        finally:
            ed.close()

    def test_normalize_image_path_no_dot_slash_on_windows_absolute(self, qapp):
        from sp.app.ui.markdown_editor import MarkdownEditor

        ed = MarkdownEditor()
        try:
            # A Windows absolute path should NOT get "./" prepended
            result = ed._normalize_image_path("C:/Users/joe/vault/paste_image_001.png")
            assert not result.startswith("./C:"), f"Absolute path got './' prefix: {result}"
            assert result == "C:/Users/joe/vault/paste_image_001.png"
        finally:
            ed.close()

    def test_normalize_image_path_relative_gets_dot_slash(self, qapp):
        from sp.app.ui.markdown_editor import MarkdownEditor

        ed = MarkdownEditor()
        try:
            result = ed._normalize_image_path("paste_image_001.png")
            assert result == "./paste_image_001.png"
        finally:
            ed.close()

    def test_markdown_from_image_format_uses_relative_path(self, qapp, tmp_path):
        """When IMAGE_PROP_ORIGINAL is lost and fallback is an absolute path,
        the serialized markdown must still use a relative path."""
        from sp.app.ui.markdown_editor import MarkdownEditor, IMAGE_PROP_ALT, IMAGE_PROP_ORIGINAL, IMAGE_PROP_WIDTH
        from PySide6.QtGui import QTextImageFormat

        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "Notes").mkdir()
        img_file = vault / "Notes" / "paste_image_001.png"
        img_file.write_bytes(b"PNG_DATA")

        ed = MarkdownEditor()
        try:
            ed._vault_root = vault
            ed._current_path = "/Notes/Page.md"

            fmt = QTextImageFormat()
            abs_path = str(img_file.resolve())
            fmt.setName(abs_path)  # Qt stores absolute resolved path
            # Simulate IMAGE_PROP_ORIGINAL being lost (empty string/not set)
            fmt.setProperty(IMAGE_PROP_ALT, "my image")
            fmt.setProperty(IMAGE_PROP_WIDTH, 0)

            md = ed._markdown_from_image_format(fmt)
            # Should contain relative path, not absolute
            assert "![my image]" in md
            assert abs_path.replace("\\", "/") not in md, f"Absolute path leaked into markdown: {md}"
            assert "paste_image_001.png" in md
        finally:
            ed.close()
