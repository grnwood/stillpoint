from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sp.server import homebase_gc


def _checkpoint_id(seed: str) -> str:
    return (seed * 64)[:64]


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")), encoding="utf-8")


def _write_object(vault_base: Path, object_id: str, data: bytes) -> Path:
    object_path = vault_base / "objects" / object_id[:2] / object_id
    object_path.parent.mkdir(parents=True, exist_ok=True)
    object_path.write_bytes(data)
    return object_path


def _write_checkpoint(vault_base: Path, checkpoint_id: str, created_at: datetime, object_id: str) -> tuple[Path, Path]:
    manifest_path = vault_base / "manifests" / checkpoint_id[:2] / checkpoint_id
    checkpoint_path = vault_base / "checkpoints" / f"{checkpoint_id}.json"
    _write_json(
        manifest_path,
        {
            "schema_version": 1,
            "vault_id": vault_base.name,
            "created_at": _iso(created_at),
            "device_id": "device-a",
            "entries": {
                "Page.md": {
                    "kind": "file",
                    "mtime": int(created_at.timestamp()),
                    "size": 12,
                    "object_id": object_id,
                }
            },
        },
    )
    _write_json(
        checkpoint_path,
        {
            "schema_version": 1,
            "vault_id": vault_base.name,
            "checkpoint_id": checkpoint_id,
            "manifest_id": checkpoint_id,
            "created_at": _iso(created_at),
        },
    )
    return manifest_path, checkpoint_path


def test_homebase_gc_dry_run_logs_and_preserves_files(tmp_path, monkeypatch, capsys) -> None:
    vaults_root = tmp_path / "vaults"
    vault_base = vaults_root / "homebase" / "vault-one"
    now = datetime.now(timezone.utc)
    old_checkpoint = _checkpoint_id("a")
    new_checkpoint = _checkpoint_id("b")
    old_object = _checkpoint_id("c")
    new_object = _checkpoint_id("d")

    _write_object(vault_base, old_object, b"older-bytes")
    _write_object(vault_base, new_object, b"newer-bytes")
    old_manifest, old_meta = _write_checkpoint(vault_base, old_checkpoint, now - timedelta(days=10), old_object)
    new_manifest, new_meta = _write_checkpoint(vault_base, new_checkpoint, now - timedelta(hours=1), new_object)

    monkeypatch.setenv("SP_HOMEBASE_GC_KEEP_LATEST", "1")
    monkeypatch.setenv("SP_HOMEBASE_GC_KEEP_ALL_DAYS", "0")
    monkeypatch.setenv("SP_HOMEBASE_GC_KEEP_DAILY_DAYS", "0")
    monkeypatch.setenv("SP_HOMEBASE_GC_KEEP_WEEKLY_DAYS", "0")

    result = homebase_gc.run_homebase_gc(vaults_root=vaults_root, force=True, dry_run=True)

    assert result == 0
    assert old_manifest.exists()
    assert old_meta.exists()
    assert new_manifest.exists()
    assert new_meta.exists()
    assert (vault_base / "objects" / old_object[:2] / old_object).exists()
    assert (vault_base / "objects" / new_object[:2] / new_object).exists()

    output = capsys.readouterr().out
    assert "retention_policy=" in output
    assert "would_delete=" in output
    assert "savings_breakdown" in output
    assert "size_before=" in output


def test_homebase_gc_prunes_unreachable_duplicate_binary_objects(tmp_path, monkeypatch, capsys) -> None:
    vaults_root = tmp_path / "vaults"
    vault_base = vaults_root / "homebase" / "vault-two"
    now = datetime.now(timezone.utc)
    old_checkpoint = _checkpoint_id("1")
    new_checkpoint = _checkpoint_id("2")
    old_object = _checkpoint_id("3")
    new_object = _checkpoint_id("4")
    duplicate_bytes = b"same-binary-content"

    old_object_path = _write_object(vault_base, old_object, duplicate_bytes)
    new_object_path = _write_object(vault_base, new_object, duplicate_bytes)
    old_manifest, old_meta = _write_checkpoint(vault_base, old_checkpoint, now - timedelta(days=14), old_object)
    new_manifest, new_meta = _write_checkpoint(vault_base, new_checkpoint, now - timedelta(hours=2), new_object)

    monkeypatch.setenv("SP_HOMEBASE_GC_KEEP_LATEST", "1")
    monkeypatch.setenv("SP_HOMEBASE_GC_KEEP_ALL_DAYS", "0")
    monkeypatch.setenv("SP_HOMEBASE_GC_KEEP_DAILY_DAYS", "0")
    monkeypatch.setenv("SP_HOMEBASE_GC_KEEP_WEEKLY_DAYS", "0")

    result = homebase_gc.run_homebase_gc(vaults_root=vaults_root, force=True, dry_run=False)

    assert result == 0
    assert not old_manifest.exists()
    assert not old_meta.exists()
    assert not old_object_path.exists()
    assert new_manifest.exists()
    assert new_meta.exists()
    assert new_object_path.exists()

    output = capsys.readouterr().out
    assert "deleted_manifests=1" in output
    assert "deleted_checkpoints=1" in output
    assert "deleted_objects=1" in output
    assert f"deleted={old_object_path}" in output
    assert "total_saved=" in output


def test_select_retained_checkpoints_respects_minimum_count(tmp_path) -> None:
    vault_base = tmp_path / "vaults" / "homebase" / "vault-three"
    now = datetime.now(timezone.utc)
    records: list[homebase_gc.CheckpointRecord] = []
    for offset, seed in enumerate(("a", "b", "c"), start=1):
        checkpoint_id = _checkpoint_id(seed)
        manifest_path, checkpoint_path = _write_checkpoint(
            vault_base,
            checkpoint_id,
            now - timedelta(days=offset),
            _checkpoint_id(chr(ord("d") + offset)),
        )
        records.append(
            homebase_gc.CheckpointRecord(
                checkpoint_id=checkpoint_id,
                created_at=now - timedelta(days=offset),
                manifest_path=manifest_path,
                checkpoint_path=checkpoint_path,
            )
        )

    records.sort(key=lambda record: (record.created_at, record.checkpoint_id), reverse=True)
    policy = homebase_gc.HomebaseGcPolicy(
        keep_latest=0,
        keep_all_days=0,
        keep_daily_days=0,
        keep_weekly_days=0,
        dry_run=False,
        min_checkpoint_count=2,
        run_interval_seconds=86400.0,
        state_path=tmp_path / "vaults" / "homebase" / ".gc" / "state.json",
    )

    retained = homebase_gc._select_retained_checkpoints(records, policy)

    assert len(retained) == 2
    assert records[0].checkpoint_id in retained
    assert records[1].checkpoint_id in retained
    assert records[2].checkpoint_id not in retained


def test_homebase_gc_main_honors_cli_and_writes_state_file(tmp_path, monkeypatch, capsys) -> None:
    vaults_root = tmp_path / "vaults"
    vault_base = vaults_root / "homebase" / "vault-cli"
    now = datetime.now(timezone.utc)
    checkpoint_id = _checkpoint_id("e")
    object_id = _checkpoint_id("f")

    _write_object(vault_base, object_id, b"cli-state-bytes")
    _write_checkpoint(vault_base, checkpoint_id, now - timedelta(hours=1), object_id)

    monkeypatch.setenv("STILLPOINT_VAULTS_ROOT", str(vaults_root))
    monkeypatch.setattr(sys, "argv", ["homebase_gc.py", "--dry-run", "--force"])

    result = homebase_gc.main()

    assert result == 0
    state_path = vaults_root / "homebase" / ".gc" / "state.json"
    assert state_path.exists()
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["schema_version"] == 1
    assert state["vault_count"] == 1
    assert state["dry_run"] is True
    assert "last_run_at_iso" in state

    output = capsys.readouterr().out
    assert "retention_policy=" in output
    assert "dry_run=yes" in output


def test_homebase_gc_interval_gate_skips_until_force(tmp_path, monkeypatch, capsys) -> None:
    vaults_root = tmp_path / "vaults"
    vault_base = vaults_root / "homebase" / "vault-interval"
    now = datetime.now(timezone.utc)
    old_checkpoint = _checkpoint_id("7")
    new_checkpoint = _checkpoint_id("8")
    old_object = _checkpoint_id("9")
    new_object = _checkpoint_id("0")

    old_object_path = _write_object(vault_base, old_object, b"older")
    new_object_path = _write_object(vault_base, new_object, b"newer")
    old_manifest, old_meta = _write_checkpoint(vault_base, old_checkpoint, now - timedelta(days=10), old_object)
    new_manifest, new_meta = _write_checkpoint(vault_base, new_checkpoint, now - timedelta(hours=1), new_object)

    monkeypatch.setenv("SP_HOMEBASE_GC_KEEP_LATEST", "1")
    monkeypatch.setenv("SP_HOMEBASE_GC_KEEP_ALL_DAYS", "0")
    monkeypatch.setenv("SP_HOMEBASE_GC_KEEP_DAILY_DAYS", "0")
    monkeypatch.setenv("SP_HOMEBASE_GC_KEEP_WEEKLY_DAYS", "0")
    monkeypatch.setenv("SP_HOMEBASE_GC_INTERVAL_SECONDS", "86400")

    first_result = homebase_gc.run_homebase_gc(vaults_root=vaults_root, force=True, dry_run=True)
    assert first_result == 0
    _ = capsys.readouterr()

    skipped_result = homebase_gc.run_homebase_gc(vaults_root=vaults_root, force=False, dry_run=True)
    assert skipped_result == 0
    skipped_output = capsys.readouterr().out
    assert "skipping run because interval has not elapsed" in skipped_output
    assert old_manifest.exists()
    assert old_meta.exists()
    assert old_object_path.exists()

    forced_result = homebase_gc.run_homebase_gc(vaults_root=vaults_root, force=True, dry_run=False)
    assert forced_result == 0
    forced_output = capsys.readouterr().out
    assert "skipping run because interval has not elapsed" not in forced_output
    assert not old_manifest.exists()
    assert not old_meta.exists()
    assert not old_object_path.exists()
    assert new_manifest.exists()
    assert new_meta.exists()
    assert new_object_path.exists()