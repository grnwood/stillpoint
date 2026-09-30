from __future__ import annotations

import hashlib
import json
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from sp.sync.crypto import derive_key_from_passphrase, encrypt_bytes, object_id_from_ciphertext
from sp.sync.engine import HomebaseSyncConfig, HomebaseSyncEngine, RecoveryCancelled
from sp.sync.recovery import RecoveryError, RecoveryStore


class FakeClient:
    def __init__(self, manifest: dict, objects: dict[str, bytes]):
        self.manifest = json.dumps(manifest).encode()
        self.objects = objects

    def get_manifest(self, _checkpoint_id):
        return self.manifest

    def get_object(self, object_id):
        return self.objects[object_id]


def engine_for(tmp_path, *, quota=2 * 1024**3, reset_review_confirmed=False):
    vault = tmp_path / "vault"
    vault.mkdir(exist_ok=True)
    cfg = HomebaseSyncConfig(
        vault_root=vault, vault_id="vault-id", device_id="device-id",
        remote_url="https://example.invalid", verify_ssl=True,
        auth_token="token", passphrase="passphrase",
        recovery_base_dir=tmp_path / "recovery", recovery_quota_bytes=quota,
        reset_review_confirmed=reset_review_confirmed,
    )
    return HomebaseSyncEngine(cfg)


def remote_file(engine, content: bytes, *, mtime=None):
    key = derive_key_from_passphrase(engine.cfg.passphrase, engine.cfg.vault_id)
    ciphertext = encrypt_bytes(key, content)
    object_id = object_id_from_ciphertext(ciphertext)
    meta = {"object_id": object_id, "mtime": mtime or int(time.time()) + 60, "size": len(content)}
    return key, object_id, meta, ciphertext


def test_pull_overwrite_and_restore_is_reversible(tmp_path):
    engine = engine_for(tmp_path)
    local = engine.cfg.vault_root / "Page.md"
    local.write_bytes(b"old local bytes")
    key, object_id, meta, ciphertext = remote_file(engine, b"new remote bytes")
    client = FakeClient({"device_id": "other", "entries": {"Page.md": meta}}, {object_id: ciphertext})

    applied, _cache = engine._apply_remote_checkpoint(client, key, "checkpoint-new")
    assert applied == ["Page.md"]
    assert local.read_bytes() == b"new remote bytes"
    event = engine.list_recovery_events()[0]
    assert event["state"] == "complete"
    assert event["paths"][0]["old_object_id"] == hashlib.sha256(b"old local bytes").hexdigest()
    assert engine.recovery.root.is_relative_to(engine.cfg.vault_root) is False

    restored = engine.recovery.restore(event["event_id"], ["Page.md"])
    assert restored["state"] == "complete"
    assert local.read_bytes() == b"old local bytes"
    engine.recovery.restore(restored["event_id"], ["Page.md"])
    assert local.read_bytes() == b"new remote bytes"


def test_pull_deletion_and_full_restore_removes_new_files(tmp_path):
    engine = engine_for(tmp_path)
    vault = engine.cfg.vault_root
    deleted = vault / "Deleted.md"
    deleted.write_bytes(b"baseline")
    key, baseline_id, _meta, _ciphertext = remote_file(engine, b"baseline")
    _key, created_id, created_meta, created_ciphertext = remote_file(engine, b"created")
    client = FakeClient({"device_id": "other", "entries": {"Created.md": created_meta}}, {created_id: created_ciphertext})

    applied, _cache = engine._apply_remote_checkpoint(client, key, "checkpoint-new", known_object_cache={"Deleted.md": baseline_id})
    assert set(applied) == {"Deleted.md", "Created.md"}
    assert not deleted.exists()
    created = vault / "Created.md"
    assert created.read_bytes() == b"created"
    event = engine.list_recovery_events()[0]
    assert {item["planned_action"] for item in event["paths"]} == {"delete", "create"}

    engine.recovery.restore(event["event_id"], full=True, confirm_deletions=True)
    assert deleted.read_bytes() == b"baseline"
    assert not created.exists()


def test_reset_protects_overwritten_files(tmp_path):
    engine = engine_for(tmp_path, reset_review_confirmed=True)
    local = engine.cfg.vault_root / "Page.md"
    local.write_bytes(b"local")
    key, object_id, meta, ciphertext = remote_file(engine, b"remote")
    client = FakeClient({"device_id": "other", "entries": {"Page.md": meta}}, {object_id: ciphertext})

    engine._apply_remote_checkpoint_authoritative(client, key, "checkpoint-reset")
    event = engine.list_recovery_events()[0]
    assert event["operation"] == "server-authoritative-reset"
    assert engine.recovery.read_object(event["paths"][0]["old_object_id"]) == b"local"
    assert local.read_bytes() == b"remote"


def test_reset_waits_for_review_after_protection(tmp_path):
    engine = engine_for(tmp_path)
    local = engine.cfg.vault_root / "Page.md"
    local.write_bytes(b"local")
    key, object_id, meta, ciphertext = remote_file(engine, b"remote")
    client = FakeClient({"device_id": "other", "entries": {"Page.md": meta}}, {object_id: ciphertext})
    errors = []

    def reset():
        try:
            engine._apply_remote_checkpoint_authoritative(client, key, "checkpoint-reset")
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=reset)
    worker.start()
    deadline = time.monotonic() + 10
    review = None
    while time.monotonic() < deadline:
        review = engine.pending_recovery_review()
        if review:
            break
        time.sleep(0.01)
    assert review is not None
    assert local.read_bytes() == b"local"
    assert engine.recovery.load(review["event_id"])["state"] == "protected"
    engine.decide_recovery_review(review["event_id"], True)
    worker.join(timeout=10)
    assert not worker.is_alive() and not errors
    assert local.read_bytes() == b"remote"


def test_quota_or_corrupt_object_blocks_destructive_pull(tmp_path):
    engine = engine_for(tmp_path, quota=1)
    local = engine.cfg.vault_root / "Page.md"
    local.write_bytes(b"original")
    key, object_id, meta, ciphertext = remote_file(engine, b"remote")
    client = FakeClient({"device_id": "other", "entries": {"Page.md": meta}}, {object_id: ciphertext})
    with pytest.raises(RecoveryError, match="quota"):
        engine._apply_remote_checkpoint(client, key, "checkpoint-new")
    assert local.read_bytes() == b"original"

    store = RecoveryStore(engine.cfg.vault_root, "vault-id", "device-id", base_dir=tmp_path / "other")
    event = store.begin(operation="normal-pull", source_checkpoint_id=None, target_checkpoint_id="one", remote_device_id="other", plan=[{"path": "Page.md", "planned_action": "overwrite", "expected_old_hash": hashlib.sha256(b"original").hexdigest(), "new_object_id": hashlib.sha256(b"remote").hexdigest()}])
    local.write_bytes(b"remote")
    store.mark(event, "Page.md", "applied")
    store.finish(event)
    digest = event["paths"][0]["old_object_id"]
    store._object_path(digest).write_bytes(b"corrupt")
    with pytest.raises(RecoveryError, match="Corrupt"):
        store.restore(event["event_id"], ["Page.md"])


def test_dedup_pin_and_interrupted_reconciliation(tmp_path):
    engine = engine_for(tmp_path)
    local = engine.cfg.vault_root / "Page.md"
    local.write_bytes(b"same")
    store = engine.recovery
    plan = [{"path": "Page.md", "planned_action": "overwrite", "expected_old_hash": hashlib.sha256(b"same").hexdigest(), "new_object_id": hashlib.sha256(b"later").hexdigest()}]
    first = store.begin(operation="normal-pull", source_checkpoint_id=None, target_checkpoint_id="one", remote_device_id="other", plan=plan)
    second = store.begin(operation="normal-pull", source_checkpoint_id=None, target_checkpoint_id="two", remote_device_id="other", plan=plan)
    assert len(list(store.objects.glob("*/*"))) == 1
    store.set_state(first, "applying")
    local.write_bytes(b"later")
    interrupted = store.scan_interrupted()
    assert {event["event_id"] for event in interrupted} == {first["event_id"], second["event_id"]}
    assert store.load(first["event_id"])["paths"][0]["result"] == "applied"
    store.acknowledge_interrupted()
    store.pin(first["event_id"], True, "Good version")
    store.prune(versions_per_file=1, daily_days=0)
    assert store.load(first["event_id"])["pinned"] is True
    with pytest.raises(RecoveryError):
        store.path("../escape.md")
    (engine.cfg.vault_root / "child" / ".stillpoint").mkdir(parents=True)
    with pytest.raises(RecoveryError, match="nested vault"):
        store.path("child/Page.md")


def test_mass_deletion_waits_for_review_and_cancel_keeps_files(tmp_path):
    engine = engine_for(tmp_path)
    key = derive_key_from_passphrase(engine.cfg.passphrase, engine.cfg.vault_id)
    known = {}
    for index in range(10):
        path = engine.cfg.vault_root / f"Page{index}.md"
        path.write_bytes(b"baseline")
        known[path.name] = object_id_from_ciphertext(encrypt_bytes(key, b"baseline"))
    client = FakeClient({"device_id": "other", "entries": {}}, {})
    errors = []

    def pull():
        try:
            engine._apply_remote_checkpoint(client, key, "mass-delete", known_object_cache=known)
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=pull)
    worker.start()
    deadline = time.monotonic() + 10
    review = None
    while time.monotonic() < deadline:
        review = engine.pending_recovery_review()
        if review:
            break
        time.sleep(0.01)
    assert review is not None
    assert review["deletes"] == 10
    assert all((engine.cfg.vault_root / f"Page{index}.md").exists() for index in range(10))
    engine.decide_recovery_review(review["event_id"], False)
    worker.join(timeout=10)
    assert not worker.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], RecoveryCancelled)
    assert engine.recovery.load(review["event_id"])["state"] == "cancelled"
    assert all((engine.cfg.vault_root / f"Page{index}.md").exists() for index in range(10))


def test_local_edit_during_protection_is_not_overwritten(tmp_path, monkeypatch):
    engine = engine_for(tmp_path)
    local = engine.cfg.vault_root / "Page.md"
    local.write_bytes(b"planned old bytes")
    key, object_id, meta, ciphertext = remote_file(engine, b"remote")
    client = FakeClient({"device_id": "other", "entries": {"Page.md": meta}}, {object_id: ciphertext})
    original_begin = engine.recovery.begin

    def changed_begin(**kwargs):
        local.write_bytes(b"new local edit")
        return original_begin(**kwargs)

    monkeypatch.setattr(engine.recovery, "begin", changed_begin)
    with pytest.raises(RecoveryError, match="changed"):
        engine._apply_remote_checkpoint(client, key, "checkpoint-new")
    assert local.read_bytes() == b"new local edit"


def test_retention_keeps_latest_distinct_versions_and_shared_objects(tmp_path):
    engine = engine_for(tmp_path)
    store = engine.recovery
    local = engine.cfg.vault_root / "Page.md"
    events = []
    for index, content in enumerate((b"old", b"middle", b"new")):
        local.write_bytes(content)
        event = store.begin(
            operation="normal-pull", source_checkpoint_id=None,
            target_checkpoint_id=str(index), remote_device_id="other",
            plan=[{
                "path": "Page.md", "planned_action": "overwrite",
                "expected_old_hash": hashlib.sha256(content).hexdigest(),
                "new_object_id": hashlib.sha256(b"incoming").hexdigest(),
            }],
        )
        store.mark(event, "Page.md", "applied")
        store.finish(event)
        events.append(event)
        time.sleep(0.01)
    store.pin(events[0]["event_id"], True, "Old good copy")
    store.prune(versions_per_file=2, daily_days=0)
    retained = {event["event_id"] for event in store.list_events()}
    assert retained == {event["event_id"] for event in events}
    store.pin(events[0]["event_id"], False)
    store.prune(versions_per_file=2, daily_days=0)
    retained = {event["event_id"] for event in store.list_events()}
    assert retained == {events[1]["event_id"], events[2]["event_id"]}
    assert not store._object_path(hashlib.sha256(b"old").hexdigest()).exists()


def test_daily_retention_keeps_a_prior_day_during_rapid_pulls(tmp_path):
    engine = engine_for(tmp_path)
    store = engine.recovery
    local = engine.cfg.vault_root / "Page.md"
    events = []
    for index, content in enumerate((b"yesterday", b"today one", b"today two")):
        local.write_bytes(content)
        event = store.begin(
            operation="normal-pull", source_checkpoint_id=None,
            target_checkpoint_id=str(index), remote_device_id="other",
            plan=[{"path": "Page.md", "planned_action": "overwrite", "expected_old_hash": hashlib.sha256(content).hexdigest()}],
        )
        store.mark(event, "Page.md", "applied")
        store.finish(event)
        if index == 0:
            event["created_at"] = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat().replace("+00:00", "Z")
            store._save(event)
        events.append(event)
    store.prune(versions_per_file=1, daily_days=7)
    retained = {event["event_id"] for event in store.list_events()}
    assert events[0]["event_id"] in retained
    assert events[2]["event_id"] in retained
    assert events[1]["event_id"] not in retained


def test_recovery_manager_shows_event_and_on_demand_text_diff(tmp_path, qapp):
    from sp.app.ui.homebase_recovery import HomebaseRecoveryDialog

    engine = engine_for(tmp_path)
    local = engine.cfg.vault_root / "Page.md"
    local.write_bytes(b"before\n")
    event = engine.recovery.begin(
        operation="normal-pull", source_checkpoint_id=None,
        target_checkpoint_id="checkpoint", remote_device_id="other",
        plan=[{
            "path": "Page.md", "planned_action": "overwrite",
            "expected_old_hash": hashlib.sha256(b"before\n").hexdigest(),
            "new_object_id": hashlib.sha256(b"after\n").hexdigest(),
        }],
    )
    local.write_bytes(b"after\n")
    engine.recovery.mark(event, "Page.md", "applied")
    engine.recovery.finish(event)
    dialog = HomebaseRecoveryDialog(None, engine, lambda *_args: True)
    try:
        assert dialog.events.count() == 1
        dialog.events.setCurrentRow(0)
        assert dialog.paths.count() == 1
        dialog.paths.setCurrentRow(0)
        assert "-before" in dialog.preview.toPlainText()
        assert "+after" in dialog.preview.toPlainText()
    finally:
        dialog.close()
