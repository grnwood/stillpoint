from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from sp.sync.crypto import derive_key_from_passphrase, encrypt_bytes, object_id_from_ciphertext
from tools.homebase_recover_lib import load_passphrases, recover_homebase_vault


def _make_store(
    root: Path,
    *,
    vault_id: str,
    files: list[tuple[str, bytes, str]],
) -> tuple[Path, str]:
    vault_base = root / "homebase" / vault_id
    entries: dict[str, dict[str, object]] = {}
    for rel_path, plaintext, passphrase in files:
        key = derive_key_from_passphrase(passphrase, vault_id)
        ciphertext = encrypt_bytes(key, plaintext)
        object_id = object_id_from_ciphertext(ciphertext)
        object_path = vault_base / "objects" / object_id[:2] / object_id
        object_path.parent.mkdir(parents=True, exist_ok=True)
        object_path.write_bytes(ciphertext)
        entries[rel_path] = {
            "size": len(plaintext),
            "mtime": 1_700_000_000,
            "kind": "file",
            "object_id": object_id,
        }
    manifest = {
        "schema_version": 1,
        "vault_id": vault_id,
        "created_at": "2026-08-21T00:00:00Z",
        "device_id": "test-device",
        "entries": entries,
    }
    manifest_bytes = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    checkpoint_id = hashlib.sha256(manifest_bytes).hexdigest()
    manifest_path = vault_base / "manifests" / checkpoint_id[:2] / checkpoint_id
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_bytes(manifest_bytes)
    latest_path = vault_base / "refs" / "latest.json"
    latest_path.parent.mkdir(parents=True, exist_ok=True)
    latest_path.write_text(json.dumps({"checkpoint_id": checkpoint_id}), encoding="utf-8")
    return vault_base, checkpoint_id


def _load_cli_module():
    module_path = Path(__file__).resolve().parent.parent / "tools" / "homebase-recover-vault.py"
    spec = importlib.util.spec_from_file_location("homebase_recover_vault", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_recovery_tries_multiple_passphrases_without_changing_store(tmp_path: Path) -> None:
    vaults_root = tmp_path / "vaults"
    vault_base, checkpoint_id = _make_store(
        vaults_root,
        vault_id="vault-123",
        files=[
            ("0-Home/0-Home.md", b"# Home\n", "old-secret"),
            ("0-Home/Quicknotes.md", b"# Quicknotes\n", "new-secret"),
        ],
    )
    before = {
        path.relative_to(vault_base).as_posix(): path.read_bytes()
        for path in vault_base.rglob("*")
        if path.is_file()
    }
    output = tmp_path / "recovered"

    report = recover_homebase_vault(
        vaults_root=vaults_root,
        vault_id="vault-123",
        output_root=output,
        passphrases=["new-secret", "old-secret"],
    )

    assert report["checkpoint_id"] == checkpoint_id
    assert report["complete"] is True
    assert report["recovered_count"] == 2
    assert report["failed_count"] == 0
    assert report["passphrase_usage"] == {"1": 1, "2": 1}
    assert (output / "0-Home" / "0-Home.md").read_bytes() == b"# Home\n"
    assert (output / "0-Home" / "Quicknotes.md").read_bytes() == b"# Quicknotes\n"
    after = {
        path.relative_to(vault_base).as_posix(): path.read_bytes()
        for path in vault_base.rglob("*")
        if path.is_file()
    }
    assert after == before
    report_text = (tmp_path / "recovered.recovery-report.json").read_text(encoding="utf-8")
    assert "new-secret" not in report_text
    assert "old-secret" not in report_text


def test_partial_recovery_reports_unknown_passphrase_and_cli_returns_two(tmp_path: Path) -> None:
    vaults_root = tmp_path / "vaults"
    _make_store(
        vaults_root,
        vault_id="vault-123",
        files=[
            ("Notes/good.md", b"good", "known-secret"),
            ("Notes/lost.md", b"lost", "unknown-secret"),
        ],
    )
    passphrase_file = tmp_path / "passphrases.txt"
    passphrase_file.write_text("known-secret\n", encoding="utf-8")
    output = tmp_path / "partial"
    module = _load_cli_module()

    exit_code = module.main(
        [
            "--vaults-root",
            str(vaults_root),
            "--vault-id",
            "vault-123",
            "--output",
            str(output),
            "--passphrase-file",
            str(passphrase_file),
        ]
    )

    assert exit_code == 2
    assert (output / "Notes" / "good.md").read_bytes() == b"good"
    assert not (output / "Notes" / "lost.md").exists()
    report = json.loads((tmp_path / "partial.recovery-report.json").read_text(encoding="utf-8"))
    assert report["complete"] is False
    assert report["failed_count"] == 1
    assert report["failures"][0]["reason"] == "no_passphrase_decrypted_object"


def test_recovery_rejects_ciphertext_hash_mismatch(tmp_path: Path) -> None:
    vaults_root = tmp_path / "vaults"
    vault_base, checkpoint_id = _make_store(
        vaults_root,
        vault_id="vault-123",
        files=[("Notes/Page.md", b"original", "secret")],
    )
    manifest = json.loads(
        (vault_base / "manifests" / checkpoint_id[:2] / checkpoint_id).read_text(encoding="utf-8")
    )
    object_id = manifest["entries"]["Notes/Page.md"]["object_id"]
    (vault_base / "objects" / object_id[:2] / object_id).write_bytes(b"corrupt")

    report = recover_homebase_vault(
        vaults_root=vaults_root,
        vault_id="vault-123",
        output_root=tmp_path / "recovered",
        passphrases=["secret"],
    )

    assert report["complete"] is False
    assert report["recovered_count"] == 0
    assert report["failures"][0]["reason"] == "ciphertext_hash_mismatch"


def test_recovery_does_not_write_unsafe_manifest_path(tmp_path: Path) -> None:
    vaults_root = tmp_path / "vaults"
    _vault_base, _checkpoint_id = _make_store(
        vaults_root,
        vault_id="vault-123",
        files=[("../escaped.md", b"no escape", "secret")],
    )

    report = recover_homebase_vault(
        vaults_root=vaults_root,
        vault_id="vault-123",
        output_root=tmp_path / "recovered",
        passphrases=["secret"],
    )

    assert report["failures"][0]["reason"] == "unsafe_path"
    assert not (tmp_path / "escaped.md").exists()


def test_recovery_refuses_existing_output_or_output_inside_vaults(tmp_path: Path) -> None:
    vaults_root = tmp_path / "vaults"
    _make_store(
        vaults_root,
        vault_id="vault-123",
        files=[("Notes/Page.md", b"page", "secret")],
    )
    existing = tmp_path / "existing"
    existing.mkdir()

    with pytest.raises(FileExistsError):
        recover_homebase_vault(
            vaults_root=vaults_root,
            vault_id="vault-123",
            output_root=existing,
            passphrases=["secret"],
        )
    with pytest.raises(ValueError, match="outside the vaults root"):
        recover_homebase_vault(
            vaults_root=vaults_root,
            vault_id="vault-123",
            output_root=vaults_root / "recovery",
            passphrases=["secret"],
        )


def test_load_passphrases_preserves_order_and_deduplicates(tmp_path: Path) -> None:
    candidates = tmp_path / "passphrases.txt"
    candidates.write_text("second secret\nfirst secret\n\n", encoding="utf-8")

    assert load_passphrases(["first secret"], [candidates]) == [
        "first secret",
        "second secret",
    ]
