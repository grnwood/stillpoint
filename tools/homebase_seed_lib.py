from __future__ import annotations

import json
import os
import stat
import time
import uuid
from pathlib import Path
from typing import Any

from argon2 import PasswordHasher

from sp.sync.crypto import derive_key_from_passphrase, encrypt_bytes, object_id_from_ciphertext
from sp.sync.local_fs import iter_files, read_bytes, stat_file


_PRIVATE_AUTH_FILE_MODE = 0o600
_PRIVATE_AUTH_DIR_MODE = 0o700


def utc_now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f"{path.suffix}.tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    write_bytes(path, json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _supports_posix_permissions() -> bool:
    return os.name != "nt"


def _chmod_path(path: Path, mode: int) -> None:
    if _supports_posix_permissions():
        os.chmod(path, mode)


def write_private_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _chmod_path(path.parent, _PRIVATE_AUTH_DIR_MODE)
    tmp = path.with_suffix(f"{path.suffix}.tmp")
    with open(tmp, "wb") as f:
        f.write(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        f.flush()
        os.fsync(f.fileno())
    _chmod_path(tmp, _PRIVATE_AUTH_FILE_MODE)
    os.replace(tmp, path)
    _chmod_path(path, _PRIVATE_AUTH_FILE_MODE)


def canonical_rel_path(vault_root: Path, rel_path: str) -> str:
    rel_key = str(rel_path or "").strip().replace("\\", "/").lstrip("/")
    if not rel_key:
        return ""
    vault_name = vault_root.name
    root_shorthand = f"{vault_name}.md"
    canonical_root = f"{vault_name}/{vault_name}.md"
    if rel_key == root_shorthand:
        return canonical_root
    return rel_key


def iter_seed_files(vault_root: Path) -> list[tuple[str, Path]]:
    items = list(iter_files(vault_root))
    rel_keys = {
        str(rel or "").strip().replace("\\", "/").lstrip("/")
        for rel, _full in items
    }
    vault_name = vault_root.name
    root_shorthand = f"{vault_name}.md"
    canonical_root = f"{vault_name}/{vault_name}.md"
    results: list[tuple[str, Path]] = []
    for rel, full in items:
        rel_key = str(rel or "").strip().replace("\\", "/").lstrip("/")
        if rel_key == root_shorthand and canonical_root in rel_keys:
            continue
        canonical = canonical_rel_path(vault_root, rel_key)
        if canonical:
            results.append((canonical, full))
    return results


def build_manifest_and_objects(source_root: Path, vault_id: str, passphrase: str, device_id: str) -> tuple[dict[str, Any], dict[str, bytes]]:
    key = derive_key_from_passphrase(passphrase, vault_id)
    entries: dict[str, Any] = {}
    objects: dict[str, bytes] = {}
    for rel, full in iter_seed_files(source_root):
        size, mtime = stat_file(full)
        plaintext = read_bytes(full)
        envelope = encrypt_bytes(key, plaintext)
        object_id = object_id_from_ciphertext(envelope)
        objects.setdefault(object_id, envelope)
        entries[rel] = {
            "size": int(size),
            "mtime": int(mtime),
            "kind": "file",
            "object_id": object_id,
        }
    manifest = {
        "schema_version": 1,
        "vault_id": vault_id,
        "created_at": utc_now_iso(),
        "device_id": device_id,
        "entries": entries,
    }
    return manifest, objects


def _checkpoint_id_for_manifest(manifest: dict[str, Any]) -> tuple[str, bytes]:
    import hashlib

    manifest_bytes = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(manifest_bytes).hexdigest(), manifest_bytes


def seed_homebase_vault(
    *,
    vaults_root: Path,
    vault_id: str,
    source_root: Path,
    passphrase: str,
    device_id: str,
    overwrite_latest: bool,
    vault_name: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    base = vaults_root / "homebase" / vault_id
    if not base.exists():
        raise FileNotFoundError(f"Homebase vault not found: {base}")
    if not source_root.exists() or not source_root.is_dir():
        raise FileNotFoundError(f"Source staging folder not found: {source_root}")

    latest_path = base / "refs" / "latest.json"
    latest_exists = latest_path.exists()
    if latest_exists and not overwrite_latest:
        raise RuntimeError(
            "Homebase latest checkpoint already exists. Pass --overwrite-latest to replace it."
        )

    manifest, objects = build_manifest_and_objects(source_root, vault_id, passphrase, device_id)
    checkpoint_id, manifest_bytes = _checkpoint_id_for_manifest(manifest)
    current_latest = ""
    if latest_exists:
        try:
            current_latest = str(json.loads(latest_path.read_text(encoding="utf-8")).get("checkpoint_id") or "")
        except Exception:
            current_latest = ""

    result = {
        "dry_run": bool(dry_run),
        "vault_id": vault_id,
        "checkpoint_id": checkpoint_id,
        "files": len(manifest["entries"]),
        "objects": len(objects),
        "source_root": str(source_root),
        "vault_base": str(base),
        "current_latest_checkpoint_id": current_latest,
        "would_replace_latest": bool(current_latest and current_latest != checkpoint_id),
    }
    if dry_run:
        return result

    for object_id, envelope in objects.items():
        write_bytes(base / "objects" / object_id[:2] / object_id, envelope)
    write_bytes(base / "manifests" / checkpoint_id[:2] / checkpoint_id, manifest_bytes)
    write_json(
        base / "checkpoints" / f"{checkpoint_id}.json",
        {
            "schema_version": 1,
            "vault_id": vault_id,
            "checkpoint_id": checkpoint_id,
            "manifest_id": checkpoint_id,
            "created_at": utc_now_iso(),
            "device_id": device_id,
            "parent_checkpoint_id": None,
        },
    )
    write_json(
        latest_path,
        {
            "schema_version": 1,
            "vault_id": vault_id,
            "checkpoint_id": checkpoint_id,
            "updated_at": utc_now_iso(),
        },
    )
    meta_path = base / "meta.json"
    if vault_name or not meta_path.exists():
        existing_meta: dict[str, Any] = {}
        if meta_path.exists():
            try:
                existing_meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                existing_meta = {}
        write_json(
            meta_path,
            {
                "schema_version": 1,
                "vault_id": vault_id,
                "vault_name": str(vault_name or existing_meta.get("vault_name") or "").strip(),
                "created_at": str(existing_meta.get("created_at") or utc_now_iso()),
            },
        )
    return result


def create_homebase_vault(
    *,
    vaults_root: Path,
    username: str,
    password: str,
    vault_name: str = "",
    vault_id: str = "",
    force: bool = False,
) -> dict[str, str]:
    cleaned_username = str(username or "").strip()
    if not cleaned_username or not password:
        raise ValueError("username and password are required")
    cleaned_vault_id = str(vault_id or "").strip() or str(uuid.uuid4())
    base = vaults_root / "homebase" / cleaned_vault_id
    if base.exists() and any(base.iterdir()) and not force:
        raise RuntimeError(f"Homebase vault already exists and is not empty: {base}")
    base.mkdir(parents=True, exist_ok=True)
    ph = PasswordHasher()
    now = utc_now_iso()
    auth_payload = {
        "schema_version": 2,
        "created_at": now,
        "users": {
            cleaned_username: {
                "username": cleaned_username,
                "password_hash": ph.hash(password),
                "role": "admin",
                "perm": "read_write",
                "created_at": now,
                "last_login_at": None,
                "last_password_change_at": now,
            }
        },
    }
    write_private_json(base / "auth" / "auth.json", auth_payload)
    write_json(
        base / "meta.json",
        {
            "schema_version": 1,
            "vault_id": cleaned_vault_id,
            "vault_name": str(vault_name or "").strip(),
            "created_at": now,
        },
    )
    return {
        "vault_id": cleaned_vault_id,
        "vault_base": str(base),
        "username": cleaned_username,
    }
