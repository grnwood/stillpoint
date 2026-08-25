from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable

from nacl.exceptions import CryptoError

from sp.sync.crypto import decrypt_bytes, derive_key_from_passphrase


_HASH_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
ProgressFn = Callable[[str], None]


def _noop_progress(_message: str) -> None:
    return


def _utc_now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _load_json_object(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise FileNotFoundError(f"Expected a regular file: {path}")

    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate JSON key in {path}: {key}")
            result[key] = value
        return result

    payload = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=reject_duplicate_keys)
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return payload


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _safe_relative_path(value: object) -> tuple[str, tuple[str, ...]]:
    raw = str(value or "")
    if not raw or "\x00" in raw:
        raise ValueError("empty path or NUL byte")
    normalized = raw.replace("\\", "/")
    if normalized.startswith("/"):
        raise ValueError("absolute path")
    pure = PurePosixPath(normalized)
    parts = pure.parts
    if not parts or any(part in ("", ".", "..") for part in parts):
        raise ValueError("non-canonical or parent-relative path")
    if ":" in parts[0]:
        raise ValueError("drive-qualified path")
    return pure.as_posix(), parts


def _write_recovered_file(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        try:
            path.unlink()
        except OSError:
            pass
        raise


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(report, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _manifest_int(value: object, default: int = 0) -> tuple[int, bool]:
    try:
        return int(value), True
    except (TypeError, ValueError, OverflowError):
        return default, False


def load_passphrases(
    direct_values: Iterable[str] = (),
    passphrase_files: Iterable[Path] = (),
) -> list[str]:
    """Load candidates without logging or returning their values in reports."""
    candidates: list[str] = []
    for value in direct_values:
        if value:
            candidates.append(value)
    for path in passphrase_files:
        if path.is_symlink() or not path.is_file():
            raise FileNotFoundError(f"Passphrase file not found or not a regular file: {path}")
        with path.open("r", encoding="utf-8") as stream:
            for line in stream:
                value = line.rstrip("\r\n")
                if value:
                    candidates.append(value)

    unique: list[str] = []
    seen: set[str] = set()
    for value in candidates:
        if value not in seen:
            unique.append(value)
            seen.add(value)
    if not unique:
        raise ValueError("At least one non-empty passphrase candidate is required")
    return unique


def recover_homebase_vault(
    *,
    vaults_root: Path,
    vault_id: str,
    output_root: Path,
    passphrases: list[str],
    checkpoint_id: str = "",
    report_path: Path | None = None,
    progress: ProgressFn | None = None,
) -> dict[str, Any]:
    """Extract decryptable files from one Homebase checkpoint without changing it."""
    report_progress = progress or _noop_progress
    vaults_root = vaults_root.resolve()
    cleaned_vault_id = str(vault_id or "").strip()
    if not _ID_PATTERN.fullmatch(cleaned_vault_id):
        raise ValueError("Invalid Homebase vault id")
    if not passphrases or any(not value for value in passphrases):
        raise ValueError("At least one non-empty passphrase candidate is required")

    vault_base = vaults_root / "homebase" / cleaned_vault_id
    if not vault_base.is_dir():
        raise FileNotFoundError(f"Homebase vault not found: {vault_base}")

    output_root = output_root.resolve()
    if output_root.exists():
        raise FileExistsError(f"Recovery output must not already exist: {output_root}")
    if _is_relative_to(output_root, vaults_root):
        raise ValueError("Recovery output must be outside the vaults root")

    resolved_report_path = (
        report_path.resolve()
        if report_path is not None
        else output_root.with_name(f"{output_root.name}.recovery-report.json")
    )
    if resolved_report_path.exists():
        raise FileExistsError(f"Recovery report must not already exist: {resolved_report_path}")
    if _is_relative_to(resolved_report_path, vaults_root):
        raise ValueError("Recovery report must be outside the vaults root")
    if _is_relative_to(resolved_report_path, output_root):
        raise ValueError("Recovery report must be outside the plaintext output directory")

    selected_checkpoint = str(checkpoint_id or "").strip().lower()
    if not selected_checkpoint:
        latest_path = vault_base / "refs" / "latest.json"
        latest = _load_json_object(latest_path)
        selected_checkpoint = str(latest.get("checkpoint_id") or "").strip().lower()
    if not _HASH_PATTERN.fullmatch(selected_checkpoint):
        raise ValueError("Invalid or missing Homebase checkpoint id")

    manifest_path = vault_base / "manifests" / selected_checkpoint[:2] / selected_checkpoint
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise FileNotFoundError(f"Manifest not found or not a regular file: {manifest_path}")
    manifest_bytes = manifest_path.read_bytes()
    actual_manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
    if actual_manifest_hash != selected_checkpoint:
        raise ValueError(
            f"Manifest hash mismatch for checkpoint {selected_checkpoint}: got {actual_manifest_hash}"
        )
    manifest = _load_json_object(manifest_path)
    manifest_vault_id = str(manifest.get("vault_id") or "").strip()
    if manifest_vault_id and manifest_vault_id != cleaned_vault_id:
        raise ValueError(
            f"Manifest vault id mismatch: expected {cleaned_vault_id}, got {manifest_vault_id}"
        )
    entries = manifest.get("entries")
    if not isinstance(entries, dict):
        raise ValueError("Manifest entries must be a JSON object")

    report_progress(
        f"Recovering checkpoint {selected_checkpoint} with {len(passphrases)} passphrase candidate(s)"
    )
    keys = [derive_key_from_passphrase(value, cleaned_vault_id) for value in passphrases]
    output_root.mkdir(parents=True, mode=0o700)
    try:
        os.chmod(output_root, 0o700)
    except OSError:
        pass

    recovered: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    usage = {str(index): 0 for index in range(1, len(keys) + 1)}

    for index, (raw_rel, raw_meta) in enumerate(entries.items(), start=1):
        display_path = str(raw_rel)
        report_progress(f"[{index}/{len(entries)}] Recovering {display_path}")
        if not isinstance(raw_meta, dict):
            failures.append({"path": display_path, "reason": "invalid_manifest_entry"})
            continue
        try:
            rel_path, parts = _safe_relative_path(raw_rel)
        except ValueError as exc:
            failures.append(
                {"path": display_path, "reason": "unsafe_path", "detail": str(exc)}
            )
            continue
        if rel_path in seen_paths:
            failures.append({"path": rel_path, "reason": "duplicate_output_path"})
            continue
        seen_paths.add(rel_path)

        object_id = str(raw_meta.get("object_id") or "").strip().lower()
        if not _HASH_PATTERN.fullmatch(object_id):
            failures.append(
                {"path": rel_path, "object_id": object_id, "reason": "invalid_object_id"}
            )
            continue
        object_path = vault_base / "objects" / object_id[:2] / object_id
        if object_path.is_symlink() or not object_path.is_file():
            failures.append(
                {"path": rel_path, "object_id": object_id, "reason": "missing_object"}
            )
            continue
        ciphertext = object_path.read_bytes()
        actual_object_hash = hashlib.sha256(ciphertext).hexdigest()
        if actual_object_hash != object_id:
            failures.append(
                {
                    "path": rel_path,
                    "object_id": object_id,
                    "reason": "ciphertext_hash_mismatch",
                    "actual_object_hash": actual_object_hash,
                }
            )
            continue

        plaintext: bytes | None = None
        matched_index = 0
        for candidate_index, key in enumerate(keys, start=1):
            try:
                plaintext = decrypt_bytes(key, ciphertext)
                matched_index = candidate_index
                break
            except (CryptoError, ValueError):
                continue
        if plaintext is None:
            failures.append(
                {
                    "path": rel_path,
                    "object_id": object_id,
                    "reason": "no_passphrase_decrypted_object",
                }
            )
            continue

        target_path = output_root.joinpath(*parts)
        try:
            _write_recovered_file(target_path, plaintext)
        except OSError as exc:
            failures.append(
                {
                    "path": rel_path,
                    "object_id": object_id,
                    "reason": "output_write_failed",
                    "detail": str(exc),
                }
            )
            continue
        remote_mtime, valid_mtime = _manifest_int(raw_meta.get("mtime", 0) or 0)
        if not valid_mtime:
            warnings.append({"path": rel_path, "reason": "invalid_manifest_mtime"})
        if remote_mtime > 0:
            try:
                os.utime(target_path, (remote_mtime, remote_mtime))
            except OSError as exc:
                warnings.append(
                    {"path": rel_path, "reason": "mtime_not_preserved", "detail": str(exc)}
                )
        declared_size, valid_size = _manifest_int(raw_meta.get("size", len(plaintext)))
        if not valid_size:
            warnings.append({"path": rel_path, "reason": "invalid_manifest_size"})
        elif declared_size != len(plaintext):
            warnings.append(
                {
                    "path": rel_path,
                    "reason": "manifest_size_mismatch",
                    "declared_size": declared_size,
                    "recovered_size": len(plaintext),
                }
            )
        usage[str(matched_index)] += 1
        recovered.append(
            {
                "path": rel_path,
                "object_id": object_id,
                "passphrase_index": matched_index,
                "size": len(plaintext),
            }
        )

    report: dict[str, Any] = {
        "schema_version": 1,
        "created_at": _utc_now_iso(),
        "read_only_source": True,
        "vault_id": cleaned_vault_id,
        "checkpoint_id": selected_checkpoint,
        "vault_base": str(vault_base),
        "output_root": str(output_root),
        "report_path": str(resolved_report_path),
        "passphrase_candidate_count": len(keys),
        "passphrase_usage": usage,
        "manifest_entry_count": len(entries),
        "recovered_count": len(recovered),
        "failed_count": len(failures),
        "warning_count": len(warnings),
        "complete": not failures,
        "recovered": recovered,
        "failures": failures,
        "warnings": warnings,
    }
    _write_report(resolved_report_path, report)
    report_progress(
        f"Recovery complete: recovered={len(recovered)} failed={len(failures)} "
        f"report={resolved_report_path}"
    )
    return report
