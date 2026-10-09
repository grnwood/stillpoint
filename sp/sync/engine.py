from __future__ import annotations

import gc
import difflib
import hashlib
import errno
import json
import os
import threading
import time
import calendar
import string
from queue import SimpleQueue
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import httpx
from nacl.exceptions import CryptoError

from sp.logging_flags import log_enabled
from sp.vault_boundary import path_crosses_nested_vault, validate_vault_root
from sp.sync.crypto import (
    decrypt_bytes,
    derive_key_from_passphrase,
    encrypt_bytes,
    object_id_from_ciphertext,
)
from sp.sync.homebase_client import HomebaseClient
from sp.sync.local_fs import (
    bytes_equal,
    conflict_copy_path,
    iter_files,
    read_bytes,
    sha256_file,
    stat_file,
    write_bytes_atomic,
)
from sp.sync.recovery import RecoveryError, RecoveryStore


_HOMEBASE_LOG = log_enabled("homebase_sync")
_HOMEBASECLIENT_LOG = log_enabled("homebaseclient")
_ANSI_BLUE = "\033[94m"
_ANSI_RED = "\033[91m"
_ANSI_RESET = "\033[0m"
_FULL_HASH_AUDIT_SECONDS = 24 * 60 * 60


class RecoveryCancelled(ValueError):
    """The user declined a protected checkpoint."""


def _log(message: str) -> None:
    if _HOMEBASE_LOG:
        color = _ANSI_RED if "conflict" in str(message).lower() else _ANSI_BLUE
        print(f"{color}[HomebaseClient] {message}{_ANSI_RESET}")


def _log_token(message: str) -> None:
    if _HOMEBASECLIENT_LOG:
        print(f"{_ANSI_BLUE}[HomebaseToken] {message}{_ANSI_RESET}")


def _utc_now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return dict(default)
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        return data if isinstance(data, dict) else dict(default)
    except Exception:
        return dict(default)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f"{path.suffix}.tmp")
    # Run a GC collection before encoding to flush any pending reference cycles
    # (e.g. httpx response objects held in exception tracebacks) so that the GC
    # is not triggered *during* json.dumps.  Without this, the cyclic GC can be
    # triggered by the memory allocations inside _make_iterencode and will
    # finalise objects whose __del__ methods corrupt the encoder's iteration
    # state, causing a fatal segmentation fault (CPython issue on 3.12+).
    gc.collect()
    text = json.dumps(payload, indent=2, sort_keys=True)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
    tmp.replace(path)


def _manifest_id_bytes(manifest_bytes: bytes) -> str:
    import hashlib

    return hashlib.sha256(manifest_bytes).hexdigest()


def _normalize_material_text(text: str) -> str:
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    lines = [line.rstrip() for line in normalized.split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def _title_only_markdown_heading(text: str) -> Optional[str]:
    normalized = _normalize_material_text(text)
    if not normalized:
        return None
    lines = normalized.split("\n")
    first_idx = None
    for idx, line in enumerate(lines):
        if line.strip():
            first_idx = idx
            break
    if first_idx is None:
        return None
    first = lines[first_idx].lstrip()
    if not first.startswith("#"):
        return None
    heading = first.lstrip("#").strip()
    if not heading:
        return None
    for line in lines[first_idx + 1 :]:
        if line.strip():
            return None
    return heading


def has_material_text_difference(local_text: str, remote_text: str) -> bool:
    local_normalized = _normalize_material_text(local_text)
    remote_normalized = _normalize_material_text(remote_text)
    if local_normalized == remote_normalized:
        return False
    local_heading = _title_only_markdown_heading(local_text)
    remote_heading = _title_only_markdown_heading(remote_text)
    if local_heading and remote_heading and local_heading == remote_heading:
        return False
    return True


def _is_unrepresentable_path_error(exc: OSError) -> bool:
    if getattr(exc, "errno", None) == errno.ENAMETOOLONG:
        return True
    if getattr(exc, "winerror", None) in {123, 206}:
        return True
    message = str(exc).lower()
    return any(
        marker in message
        for marker in (
            "file name too long",
            "filename too long",
            "path too long",
            "filename, directory name, or volume label syntax is incorrect",
        )
    )


@dataclass
class HomebaseSyncStatus:
    state: str = "idle"
    summary: str = "Idle"
    last_sync_at: Optional[str] = None
    last_error: Optional[str] = None
    pending: bool = False
    conflicts: int = 0
    pending_uploads: int = 0
    pending_downloads: int = 0
    transfer_workers: list[str] = field(default_factory=list)


@dataclass
class HomebaseSyncConfig:
    vault_root: Path
    vault_id: str
    device_id: str
    remote_url: str
    verify_ssl: bool
    auth_token: str
    passphrase: str
    local_ui_token: str = ""
    refresh_token: str = ""
    auto_sync: bool = True
    interval_seconds: int = 60
    push_debounce_seconds: int = 3
    max_parallel_transfers: int = 3
    token_update_callback: Optional[Callable[[str, str], None]] = None
    recovery_enabled: bool = True
    recovery_base_dir: Optional[Path] = None
    recovery_quota_bytes: int = 2 * 1024**3
    recovery_versions_per_file: int = 3
    recovery_daily_days: int = 7
    reset_review_confirmed: bool = False


class HomebaseSyncEngine:
    def __init__(
        self,
        cfg: HomebaseSyncConfig,
        status_callback: Optional[Callable[[HomebaseSyncStatus], None]] = None,
    ) -> None:
        cfg.vault_root = validate_vault_root(cfg.vault_root)
        self.cfg = cfg
        self.status_callback = status_callback
        self._thread: Optional[threading.Thread] = None
        self._stop = False
        self._cv = threading.Condition()
        self._next_run_at: Optional[float] = None
        self._last_interval_run_at: float = 0.0
        self._force_run = False
        self._ignore_backoff_once = False
        self._sync_suspended = False
        self._sync_in_progress = False
        self._status_lock = threading.Lock()
        self._status = HomebaseSyncStatus()
        self._no_change_streak = 0
        self._hibernating = False
        self._hibernate_after_checks = 3
        self._remote_updates_lock = threading.Lock()
        self._pending_remote_updates: list[str] = []
        self._last_pull_incomplete = False
        self._sync_dir = self.cfg.vault_root / ".stillpoint" / "sync"
        self._state_path = self._sync_dir / "local_state.json"
        self._conflict_path = self._sync_dir / "conflict_log.json"
        self._scan_path = self._sync_dir / "last_scan.json"
        self._object_cache_path = self._sync_dir / "object_cache.json"
        self._sync_errors_path = self._sync_dir / "sync_errors.json"
        self._local_deletions_path = self._sync_dir / "local_deletions.json"
        self._sync_error_summary_cache: Optional[tuple[tuple[int, int], dict[str, Any]]] = None
        self._recovery: Optional[RecoveryStore] = None
        self._review_cv = threading.Condition()
        self._pending_review: Optional[dict[str, Any]] = None
        self._review_decision: Optional[bool] = None
        self._interrupted_events: list[dict[str, Any]] = []

    def interrupted_recovery_events(self) -> list[dict[str, Any]]:
        return list(self._interrupted_events)

    def continue_after_interrupted_recovery(self) -> None:
        self.recovery.acknowledge_interrupted()
        self._interrupted_events = []
        self.resume_sync("interrupted recovery reviewed", sync_now=True)

    def pending_recovery_review(self) -> Optional[dict[str, Any]]:
        with self._review_cv:
            return dict(self._pending_review) if self._pending_review else None

    def decide_recovery_review(self, event_id: str, apply: bool) -> None:
        with self._review_cv:
            if not self._pending_review or self._pending_review["event_id"] != event_id:
                raise RecoveryError("Recovery review is no longer pending")
            self._review_decision = bool(apply)
            self._review_cv.notify_all()

    def _review_protected_plan(self, event: dict[str, Any], plan: list[dict[str, Any]], tracked_count: int) -> None:
        destructive = [item for item in plan if item["planned_action"] in {"overwrite", "delete"}]
        deletes = [item for item in destructive if item["planned_action"] == "delete"]
        if event["operation"] == "server-authoritative-reset" and self.cfg.reset_review_confirmed:
            return
        if not destructive and not (event["operation"] == "server-authoritative-reset" and tracked_count):
            return
        percentage = len(destructive) * 100 / max(1, tracked_count)
        delete_percentage = len(deletes) * 100 / max(1, tracked_count)
        reason = None
        if event["operation"] == "server-authoritative-reset" and tracked_count:
            reason = "Server-authoritative reset of a non-empty vault"
        elif len(destructive) >= 25 and percentage >= 10:
            reason = "Large number of files will be overwritten or deleted"
        elif len(deletes) >= 10 and delete_percentage >= 5:
            reason = "Large number of files will be deleted"
        else:
            known_devices = {
                prior.get("remote_device_id")
                for prior in self.recovery.list_events()
                if prior["event_id"] != event["event_id"]
            }
            if len(destructive) >= 25 and event.get("remote_device_id") not in known_devices:
                reason = "A new device is changing many files"
            severe_text_shrink = sum(
                item["planned_action"] == "overwrite"
                and item["path"].lower().endswith((".md", ".txt"))
                and (item.get("old_size") or 0) > 0
                and (item.get("new_size") or 0) <= (item["old_size"] * 0.1)
                for item in event["paths"]
            )
            if severe_text_shrink >= 10:
                reason = "Many text files would become empty or shrink by at least 90%"
        if reason is None:
            return
        with self._review_cv:
            self._pending_review = {
                "event_id": event["event_id"],
                "reason": reason,
                "checkpoint_id": event["target_checkpoint_id"],
                "remote_device_id": event["remote_device_id"],
                "creates": sum(item["planned_action"] == "create" for item in plan),
                "overwrites": sum(item["planned_action"] == "overwrite" for item in plan),
                "deletes": len(deletes),
                "percent": round(percentage, 1),
                "bytes_removed": sum(max(0, (item.get("old_size") or 0) - (item.get("new_size") or 0)) for item in event["paths"]),
                "paths": [item["path"] for item in destructive[:15]],
            }
            self._review_decision = None
            self._set_status_locked(state="review", summary="Waiting for review of destructive Homebase changes")
            while self._review_decision is None and not self._stop:
                self._review_cv.wait(timeout=0.2)
            approved = self._review_decision is True and not self._stop
            self._pending_review = None
            self._review_decision = None
        if not approved:
            self.recovery.set_state(event, "cancelled")
            raise RecoveryCancelled("Homebase pull cancelled during local recovery review")

    @property
    def recovery(self) -> RecoveryStore:
        if self._recovery is None:
            self._recovery = RecoveryStore(
                self.cfg.vault_root,
                self.cfg.vault_id,
                self.cfg.device_id,
                base_dir=self.cfg.recovery_base_dir,
                quota_bytes=self.cfg.recovery_quota_bytes,
            )
        return self._recovery

    def list_recovery_events(self) -> list[dict[str, Any]]:
        return self.recovery.list_events()

    def prune_recovery(self) -> None:
        self.recovery.prune(self.cfg.recovery_versions_per_file, self.cfg.recovery_daily_days)

    def _pause_for_interrupted_recovery(self) -> None:
        if not self.cfg.recovery_enabled:
            return
        interrupted = self.recovery.scan_interrupted()
        if not interrupted:
            return
        self._interrupted_events = interrupted
        with self._cv:
            self._sync_suspended = True
        self._set_status_locked(
            state="interrupted",
            summary="Interrupted Homebase recovery needs review",
            pending_downloads=0,
            transfer_workers=[],
        )

    def restore_recovery_event(
        self, event_id: str, paths: Optional[list[str]] = None, *, full: bool = False,
        confirm_deletions: bool = False, allow_changed_paths: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        interrupted = bool(self._interrupted_events)
        self.suspend_sync("local recovery")
        restored_ok = False
        try:
            restored = self.recovery.restore(
                event_id, paths, full=full, confirm_deletions=confirm_deletions,
                allow_changed_paths=allow_changed_paths or (),
            )
            if restored["state"] != "complete":
                raise RecoveryError("Some recovery paths could not be restored")
            self._queue_remote_updates([item["path"] for item in restored["paths"]])
            restored_ok = True
            return restored
        finally:
            if interrupted:
                if restored_ok:
                    self.continue_after_interrupted_recovery()
            else:
                self.resume_sync("local recovery", sync_now=restored_ok)

    def _canonical_rel_path(self, rel_path: str) -> str:
        rel_key = str(rel_path or "").strip().replace("\\", "/").lstrip("/")
        if not rel_key:
            return ""
        vault_name = self.cfg.vault_root.name
        root_shorthand = f"{vault_name}.md"
        canonical_root = f"{vault_name}/{vault_name}.md"
        if rel_key == root_shorthand:
            return canonical_root
        return rel_key

    def _iter_sync_files(self) -> list[tuple[str, Path]]:
        items = list(iter_files(self.cfg.vault_root))
        rel_keys = {
            str(rel or "").strip().replace("\\", "/").lstrip("/")
            for rel, _full in items
        }
        vault_name = self.cfg.vault_root.name
        root_shorthand = f"{vault_name}.md"
        canonical_root = f"{vault_name}/{vault_name}.md"
        results: list[tuple[str, Path]] = []
        for rel, full in items:
            rel_key = str(rel or "").strip().replace("\\", "/").lstrip("/")
            if rel_key == root_shorthand and canonical_root in rel_keys:
                _log(f"scan skip duplicate legacy root page path={rel_key} canonical={canonical_root}")
                continue
            results.append((self._canonical_rel_path(rel_key), full))
        return results

    def _local_path_for_rel(self, rel_path: str) -> Path:
        """Resolve a manifest path, including the legacy root-page shorthand."""
        rel_key = str(rel_path or "").strip().replace("\\", "/").lstrip("/")
        if not rel_key or any(part in {"", ".", ".."} for part in rel_key.split("/")):
            raise ValueError(f"Unsafe Homebase path: {rel_path}")
        canonical = self.cfg.vault_root / rel_key
        try:
            if not canonical.resolve().is_relative_to(self.cfg.vault_root):
                raise ValueError(f"Homebase path escapes vault: {rel_key}")
        except OSError as exc:
            if not _is_unrepresentable_path_error(exc):
                raise
        if path_crosses_nested_vault(self.cfg.vault_root, canonical):
            raise ValueError(f"Homebase path crosses into a separate nested vault: {rel_key}")
        try:
            if canonical.exists():
                return canonical
        except OSError:
            # Return the intended path so the caller's per-file error handling
            # can report an unrepresentable component instead of aborting the
            # entire checkpoint before other entries are applied.
            return canonical
        vault_name = self.cfg.vault_root.name
        if rel_key == f"{vault_name}/{vault_name}.md":
            shorthand = self.cfg.vault_root / f"{vault_name}.md"
            if shorthand.exists():
                return shorthand
        return canonical

    def _canonicalize_manifest_entries(self, entries: Any) -> dict[str, Any]:
        if not isinstance(entries, dict):
            return {}
        canonical: dict[str, Any] = {}
        for rel, meta in entries.items():
            rel_key = self._canonical_rel_path(str(rel or ""))
            if not rel_key:
                continue
            current_is_canonical = rel_key == str(rel or "").strip().replace("\\", "/").lstrip("/")
            if rel_key not in canonical or current_is_canonical:
                canonical[rel_key] = meta
        return canonical

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        if self.cfg.recovery_enabled:
            self._interrupted_events = self.recovery.scan_interrupted()
            if self._interrupted_events:
                self._sync_suspended = True
                self._set_status_locked(state="interrupted", summary="Interrupted Homebase recovery needs review")
            else:
                self.prune_recovery()
        self._stop = False
        # Anchor the first periodic run to engine startup.  Previously the run
        # loop recalculated a full interval after every timeout until some
        # other event happened to complete the first cycle, so an otherwise
        # quiet engine could wait forever for its initial interval sync.
        self._last_interval_run_at = time.monotonic()
        self._thread = threading.Thread(target=self._run_loop, name="homebase-sync", daemon=True)
        self._thread.start()
        _log(
            "engine start "
            f"vault_id={self.cfg.vault_id} device_id={self.cfg.device_id} "
            f"auto_sync={self.cfg.auto_sync} interval={self.cfg.interval_seconds}s "
            f"debounce={self.cfg.push_debounce_seconds}s"
        )

    def stop(self) -> None:
        with self._cv:
            self._stop = True
            self._cv.notify_all()
        with self._review_cv:
            self._review_cv.notify_all()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        _log("engine stop")

    def schedule_sync(self, reason: str = "event") -> None:
        delay = max(1, int(self.cfg.push_debounce_seconds))
        with self._cv:
            self._hibernating = False
            candidate_run_at = time.monotonic() + delay
            # Coalesce work without allowing a stream of filesystem events to
            # postpone the pending cycle indefinitely.
            if self._next_run_at is None:
                self._next_run_at = candidate_run_at
            else:
                self._next_run_at = min(self._next_run_at, candidate_run_at)
            if self._sync_in_progress:
                # Keep the current phase/progress visible.  Replacing it with
                # "Sync scheduled" made an active scan or transfer look stuck.
                self._set_status_locked(pending=True)
            elif self._sync_suspended:
                self._set_status_locked(
                    pending=True,
                    summary=f"Sync paused; changes queued ({reason})",
                )
            else:
                self._set_status_locked(
                    pending=True,
                    summary=f"Sync scheduled ({reason})",
                )
            self._cv.notify_all()
        _log(f"scheduled sync no later than {delay}s ({reason})")

    def suspend_sync(self, reason: str = "transaction") -> None:
        """Pause new cycles and wait for an active cycle to finish."""
        with self._cv:
            self._sync_suspended = True
            self._hibernating = False
            self._cv.notify_all()
            while self._sync_in_progress and not self._stop:
                self._cv.wait(timeout=0.1)
        _log(f"sync suspended ({reason})")

    def try_suspend_sync(self, reason: str = "transaction") -> bool:
        """Pause new cycles without blocking a caller on an active sync."""
        with self._cv:
            self._sync_suspended = True
            self._hibernating = False
            busy = self._sync_in_progress
            self._cv.notify_all()
        if busy:
            _log(f"sync suspension pending active cycle ({reason})")
            return False
        _log(f"sync suspended ({reason})")
        return True

    def resume_sync(self, reason: str = "transaction", *, sync_now: bool = False) -> None:
        """Resume cycles, optionally coalescing pending work into one immediate run."""
        with self._cv:
            self._sync_suspended = False
            if sync_now:
                self._force_run = True
                self._ignore_backoff_once = True
                self._next_run_at = None
                self._set_status_locked(pending=True, summary=f"Sync requested ({reason})")
            self._cv.notify_all()
        _log(f"sync resumed ({reason}) immediate={'yes' if sync_now else 'no'}")

    def sync_now(self, reason: str = "manual") -> None:
        with self._cv:
            self._hibernating = False
            self._force_run = True
            self._ignore_backoff_once = True
            if self._sync_in_progress:
                self._set_status_locked(pending=True)
            elif self._sync_suspended:
                self._set_status_locked(
                    pending=True,
                    summary=f"Sync paused; changes queued ({reason})",
                )
            else:
                self._set_status_locked(pending=True, summary=f"Sync requested ({reason})")
            self._cv.notify_all()
        _log(f"sync now requested ({reason})")

    def recheck_remote_checkpoint(self) -> None:
        """Force a manifest comparison without discarding the local diff cache."""
        state = _read_json(self._state_path, self._default_state())
        hb = state.setdefault("homebase", {})
        hb["last_seen_latest_checkpoint_id"] = None
        hb["last_error"] = None
        hb["error_count"] = 0
        hb["backoff_until"] = None
        _write_json(self._state_path, state)
        _log("remote checkpoint marked for recheck (local object cache preserved)")

    def reset_to_server_authoritative(self) -> None:
        """Reset local sync state and force local files to current server snapshot."""
        _log("reset start (server authoritative)")
        state = _read_json(self._state_path, self._default_state())
        hb = state.setdefault("homebase", {})
        key = derive_key_from_passphrase(self.cfg.passphrase, self.cfg.vault_id)
        client = HomebaseClient(
            base_url=self.cfg.remote_url,
            token=self.cfg.auth_token,
            vault_id=self.cfg.vault_id,
            local_ui_token=self.cfg.local_ui_token,
            verify_ssl=self.cfg.verify_ssl,
        )
        try:
            latest = client.get_latest()
            checkpoint_id = str(latest.get("checkpoint_id") or "").strip()
            if checkpoint_id:
                pulled_cache = self._apply_remote_checkpoint_authoritative(client, key, checkpoint_id)
                hb["last_seen_latest_checkpoint_id"] = checkpoint_id
                hb["last_pulled_checkpoint_id"] = checkpoint_id
                hb["last_pushed_checkpoint_id"] = checkpoint_id
                self._save_object_cache(pulled_cache)
            else:
                hb["last_seen_latest_checkpoint_id"] = None
                hb["last_pulled_checkpoint_id"] = None
                hb["last_pushed_checkpoint_id"] = None
                self._save_object_cache({})
            hb["last_sync_at"] = _utc_now_iso()
            hb["last_error"] = None
            hb["error_count"] = 0
            hb["backoff_until"] = None
            _write_json(self._state_path, state)
            _write_json(
                self._conflict_path,
                {
                    "schema_version": 1,
                    "vault_id": self.cfg.vault_id,
                    "conflicts": [],
                },
            )
            current_scan = {}
            for rel, full in self._iter_sync_files():
                file_stat = full.stat()
                current_scan[rel] = {
                    "size": int(file_stat.st_size),
                    "mtime": int(file_stat.st_mtime),
                    "mtime_ns": int(getattr(file_stat, "st_mtime_ns", 0) or 0),
                    "ctime_ns": int(getattr(file_stat, "st_ctime_ns", 0) or 0),
                    "content_sha256": sha256_file(full),
                }
            _write_json(
                self._scan_path,
                {
                    "schema_version": 1,
                    "vault_id": self.cfg.vault_id,
                    "updated_at": _utc_now_iso(),
                    "last_full_hash_epoch": float(time.time()),
                    "entries": current_scan,
                },
            )
            self._set_status_locked(
                state="idle",
                summary="Reset complete (server authoritative)",
                last_sync_at=hb["last_sync_at"],
                last_error=None,
                conflicts=0,
                pending=False,
            )
            _log("reset complete (server authoritative)")
        finally:
            client.close()
            self._pause_for_interrupted_recovery()

    def preview_local_authoritative(self) -> dict[str, Any]:
        """Describe the effect of replacing the shared head with this device."""
        client = HomebaseClient(
            base_url=self.cfg.remote_url,
            token=self.cfg.auth_token,
            vault_id=self.cfg.vault_id,
            local_ui_token=self.cfg.local_ui_token,
            verify_ssl=self.cfg.verify_ssl,
        )
        try:
            latest = client.get_latest()
            remote_head = str(latest.get("checkpoint_id") or "").strip()
            remote_entries: dict[str, Any] = {}
            if remote_head:
                manifest = json.loads(client.get_manifest(remote_head).decode("utf-8"))
                remote_entries = self._canonicalize_manifest_entries(manifest.get("entries", {}))
            local_items = {
                self._canonical_rel_path(rel): full
                for rel, full in self._iter_sync_files()
                if self._canonical_rel_path(rel)
            }
            local_paths = set(local_items)
            remote_paths = {
                self._canonical_rel_path(str(rel))
                for rel, meta in remote_entries.items()
                if isinstance(meta, dict)
                and not str(rel).startswith(".stillpoint/")
                and self._canonical_rel_path(str(rel))
            }
            key = derive_key_from_passphrase(self.cfg.passphrase, self.cfg.vault_id)
            text_suffixes = {
                ".md", ".txt", ".json", ".toml", ".yaml", ".yml", ".csv", ".tsv",
            }
            per_file_limit = 512 * 1024
            local_object_ids: dict[str, str] = {}
            local_sizes: dict[str, int] = {}
            local_preview_bytes: dict[str, bytes] = {}
            local_preview_notes: dict[str, str] = {}
            local_preview_budget = 5 * 1024 * 1024
            local_previewed_files = 0
            changed_paths: set[str] = set()
            for rel in sorted(local_paths):
                data = read_bytes(local_items[rel])
                object_id = object_id_from_ciphertext(encrypt_bytes(key, data))
                local_object_ids[rel] = object_id
                local_sizes[rel] = len(data)
                remote_object_id = str(
                    ((remote_entries.get(rel) or {}) if isinstance(remote_entries.get(rel), dict) else {}).get("object_id")
                    or ""
                ).strip().lower()
                needs_preview = rel not in remote_paths or object_id != remote_object_id
                if needs_preview and Path(rel).suffix.lower() in text_suffixes:
                    if len(data) > per_file_limit:
                        local_preview_notes[rel] = "Text preview omitted because the local file exceeds the preview limit."
                    elif local_previewed_files >= 100 or len(data) > local_preview_budget:
                        local_preview_notes[rel] = "Text preview omitted after the first 100 local files or 5 MiB."
                    else:
                        local_preview_bytes[rel] = data
                        local_preview_budget -= len(data)
                        local_previewed_files += 1
                if rel in remote_paths and object_id != remote_object_id:
                    changed_paths.add(rel)

            shared_paths = local_paths & remote_paths
            preview_budget = 5 * 1024 * 1024
            previewed_remote_files = 0
            changes: list[dict[str, Any]] = []

            def _remote_plaintext(rel: str, meta: dict[str, Any]) -> tuple[Optional[bytes], str]:
                nonlocal preview_budget, previewed_remote_files
                object_id = str(meta.get("object_id") or "").strip().lower()
                remote_size = int(meta.get("size", 0) or 0)
                if Path(rel).suffix.lower() not in text_suffixes:
                    return None, "Binary file; text comparison is unavailable."
                if remote_size > per_file_limit or remote_size > preview_budget:
                    return None, "Text preview omitted because the file exceeds the preview limit."
                if previewed_remote_files >= 100:
                    return None, "Text preview omitted after the first 100 remote files."
                try:
                    ciphertext = client.get_object(object_id)
                    if object_id_from_ciphertext(ciphertext) != object_id:
                        return None, "Remote preview failed its integrity check."
                    plaintext = decrypt_bytes(key, ciphertext)
                    preview_budget -= len(plaintext)
                    previewed_remote_files += 1
                    return plaintext, ""
                except Exception as exc:
                    return None, f"Remote preview unavailable: {exc}"

            for rel in sorted(local_paths - remote_paths):
                data = local_preview_bytes.get(rel)
                preview = ""
                if data is not None:
                    try:
                        preview = data.decode("utf-8")
                    except UnicodeDecodeError:
                        preview = "Binary file; text preview is unavailable."
                elif Path(rel).suffix.lower() in text_suffixes:
                    preview = local_preview_notes.get(
                        rel, "Text preview omitted because the file exceeds the preview limit."
                    )
                else:
                    preview = "Binary file; text preview is unavailable."
                changes.append({
                    "path": rel,
                    "action": "add",
                    "local_size": local_sizes[rel],
                    "remote_size": None,
                    "local_object_id": local_object_ids[rel],
                    "remote_object_id": "",
                    "preview": preview,
                })

            for rel in sorted(remote_paths - local_paths):
                meta = remote_entries.get(rel) if isinstance(remote_entries.get(rel), dict) else {}
                remote_data, note = _remote_plaintext(rel, meta)
                preview = note
                if remote_data is not None:
                    try:
                        preview = remote_data.decode("utf-8")
                    except UnicodeDecodeError:
                        preview = "Binary file; text preview is unavailable."
                changes.append({
                    "path": rel,
                    "action": "remove",
                    "local_size": None,
                    "remote_size": int(meta.get("size", 0) or 0),
                    "local_object_id": "",
                    "remote_object_id": str(meta.get("object_id") or "").strip().lower(),
                    "preview": preview,
                })

            for rel in sorted(changed_paths):
                meta = remote_entries.get(rel) if isinstance(remote_entries.get(rel), dict) else {}
                local_data = local_preview_bytes.get(rel)
                if local_data is None and Path(rel).suffix.lower() in text_suffixes:
                    remote_data, note = None, local_preview_notes.get(
                        rel, "Text diff omitted because the local file exceeds the preview limit."
                    )
                else:
                    remote_data, note = _remote_plaintext(rel, meta)
                preview = note
                if remote_data is not None and local_data is not None:
                    try:
                        before = remote_data.decode("utf-8").splitlines(keepends=True)
                        after = local_data.decode("utf-8").splitlines(keepends=True)
                        preview = "".join(
                            difflib.unified_diff(
                                before,
                                after,
                                fromfile="Homebase version",
                                tofile="This device",
                            )
                        ) or "Text content is equivalent after normalization."
                        if len(preview) > 200_000:
                            preview = preview[:200_000] + "\n\n…diff truncated…"
                    except UnicodeDecodeError:
                        preview = "Binary file; text comparison is unavailable."
                changes.append({
                    "path": rel,
                    "action": "replace",
                    "local_size": local_sizes[rel],
                    "remote_size": int(meta.get("size", 0) or 0),
                    "local_object_id": local_object_ids[rel],
                    "remote_object_id": str(meta.get("object_id") or "").strip().lower(),
                    "preview": preview,
                })

            return {
                "remote_head": remote_head,
                "local_files": len(local_paths),
                "remote_files": len(remote_paths),
                "local_only": len(local_paths - remote_paths),
                "remote_only": len(remote_paths - local_paths),
                "shared": len(shared_paths),
                "changed": len(changed_paths),
                "unchanged": len(shared_paths - changed_paths),
                "changes": changes,
            }
        finally:
            client.close()

    def publish_local_authoritative(self, expected_remote_head: str) -> dict[str, Any]:
        """Publish the current local vault without first applying the remote head.

        This is intentionally separate from normal sync and guarded by an
        expected-head check so a device cannot knowingly overwrite a snapshot
        that changed after the user reviewed the preview.
        """
        key = derive_key_from_passphrase(self.cfg.passphrase, self.cfg.vault_id)
        client = HomebaseClient(
            base_url=self.cfg.remote_url,
            token=self.cfg.auth_token,
            vault_id=self.cfg.vault_id,
            local_ui_token=self.cfg.local_ui_token,
            verify_ssl=self.cfg.verify_ssl,
        )
        try:
            latest = client.get_latest()
            remote_head = str(latest.get("checkpoint_id") or "").strip()
            if remote_head != str(expected_remote_head or "").strip():
                raise ValueError(
                    "Homebase changed after the preview. Review the latest state before publishing again."
                )

            file_items = self._iter_sync_files()
            manifest = {
                "schema_version": 1,
                "vault_id": self.cfg.vault_id,
                "created_at": _utc_now_iso(),
                "device_id": self.cfg.device_id,
                "entries": {},
            }
            current_scan: dict[str, dict[str, Any]] = {}
            current_object_map: dict[str, str] = {}

            def _prepare_and_upload(item: tuple[str, Path]) -> tuple[str, dict[str, Any], dict[str, Any], str, bool]:
                rel, full = item
                before = full.stat()
                plaintext = read_bytes(full)
                after = full.stat()
                if (
                    int(before.st_size) != int(after.st_size)
                    or int(getattr(before, "st_mtime_ns", 0) or 0)
                    != int(getattr(after, "st_mtime_ns", 0) or 0)
                ):
                    raise OSError(f"Local file changed while preparing authoritative publish: {rel}")
                envelope = encrypt_bytes(key, plaintext)
                object_id = object_id_from_ciphertext(envelope)
                uploaded = False
                if not client.has_object(object_id):
                    client.put_object(object_id, envelope)
                    uploaded = True
                manifest_entry = {
                    "size": int(after.st_size),
                    "mtime": int(after.st_mtime),
                    "kind": "file",
                    "object_id": object_id,
                }
                scan_entry = {
                    "size": int(after.st_size),
                    "mtime": int(after.st_mtime),
                    "mtime_ns": int(getattr(after, "st_mtime_ns", 0) or 0),
                    "ctime_ns": int(getattr(after, "st_ctime_ns", 0) or 0),
                    "content_sha256": hashlib.sha256(plaintext).hexdigest(),
                }
                return rel, manifest_entry, scan_entry, object_id, uploaded

            uploaded_objects = 0
            max_workers = min(max(1, int(self.cfg.max_parallel_transfers or 1)), max(1, len(file_items)))
            with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="homebase-authoritative") as executor:
                futures = [executor.submit(_prepare_and_upload, item) for item in file_items]
                for future in as_completed(futures):
                    rel, manifest_entry, scan_entry, object_id, uploaded = future.result()
                    manifest["entries"][rel] = manifest_entry
                    current_scan[rel] = scan_entry
                    current_object_map[rel] = object_id
                    uploaded_objects += int(uploaded)

            final_items = self._iter_sync_files()
            if {rel for rel, _full in final_items} != set(current_object_map):
                raise OSError(
                    "The local file set changed while the authoritative snapshot was being prepared. "
                    "Nothing was published as latest."
                )
            for rel, full in final_items:
                current_stat = full.stat()
                prepared = current_scan[rel]
                if (
                    int(current_stat.st_size) != int(prepared["size"])
                    or int(getattr(current_stat, "st_mtime_ns", 0) or 0)
                    != int(prepared["mtime_ns"])
                ):
                    raise OSError(
                        f"Local file changed while the authoritative snapshot was being prepared: {rel}. "
                        "Nothing was published as latest."
                    )

            manifest_bytes = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
            checkpoint_id = _manifest_id_bytes(manifest_bytes)
            # Recheck immediately before moving the shared head. The server API
            # does not expose compare-and-swap, so this is the narrowest safe
            # client-side guard available.
            latest_before_publish = client.get_latest()
            if str(latest_before_publish.get("checkpoint_id") or "").strip() != remote_head:
                raise ValueError(
                    "Homebase changed while local files were being prepared. Nothing was published as latest."
                )
            client.put_manifest(checkpoint_id, manifest_bytes)
            client.put_latest(checkpoint_id)

            state = _read_json(self._state_path, self._default_state())
            hb = state.setdefault("homebase", {})
            hb["last_seen_latest_checkpoint_id"] = checkpoint_id
            hb["last_pulled_checkpoint_id"] = checkpoint_id
            hb["last_pushed_checkpoint_id"] = checkpoint_id
            hb["last_sync_at"] = _utc_now_iso()
            hb["last_error"] = None
            hb["error_count"] = 0
            hb["backoff_until"] = None
            _write_json(self._state_path, state)
            _write_json(
                self._scan_path,
                {
                    "schema_version": 1,
                    "vault_id": self.cfg.vault_id,
                    "updated_at": hb["last_sync_at"],
                    "last_full_hash_epoch": float(time.time()),
                    "entries": current_scan,
                },
            )
            self._save_object_cache(current_object_map)
            self._save_local_deletions(set())
            _write_json(
                self._conflict_path,
                {"schema_version": 1, "vault_id": self.cfg.vault_id, "conflicts": []},
            )
            _write_json(
                self._sync_errors_path,
                {"schema_version": 1, "vault_id": self.cfg.vault_id, "errors": []},
            )
            self._sync_error_summary_cache = None
            return {
                "checkpoint_id": checkpoint_id,
                "files": len(current_object_map),
                "uploaded_objects": uploaded_objects,
            }
        finally:
            client.close()

    def get_status(self) -> HomebaseSyncStatus:
        with self._status_lock:
            return HomebaseSyncStatus(**self._status.__dict__)

    def consume_remote_updates(self) -> list[str]:
        with self._remote_updates_lock:
            if not self._pending_remote_updates:
                return []
            updates = list(self._pending_remote_updates)
            self._pending_remote_updates.clear()
            return updates

    def list_conflicts(self, limit: int = 200) -> list[dict[str, Any]]:
        payload = _read_json(self._conflict_path, {"conflicts": []})
        conflicts = payload.get("conflicts")
        if not isinstance(conflicts, list):
            return []
        unresolved: list[dict[str, Any]] = []
        for item in conflicts:
            if not isinstance(item, dict):
                continue
            if item.get("resolved_at"):
                continue
            conflict_copy = str(item.get("conflict_copy_path") or "").strip()
            if not conflict_copy:
                continue
            if not (self.cfg.vault_root / conflict_copy).exists():
                continue
            unresolved.append(dict(item))
        if limit > 0:
            unresolved = unresolved[-int(limit) :]
        return unresolved

    def _record_sync_error(self, *, path: str, phase: str, reason: str, object_id: str = "") -> None:
        rel_path = str(path or "").strip().replace("\\", "/").lstrip("/")
        if not rel_path:
            return
        phase_text = str(phase or "unknown").strip().lower() or "unknown"
        reason_text = str(reason or "").strip() or "Unknown error"
        object_id_text = str(object_id or "").strip().lower()

        payload = _read_json(
            self._sync_errors_path,
            {
                "schema_version": 1,
                "vault_id": self.cfg.vault_id,
                "errors": [],
            },
        )
        errors = payload.get("errors")
        if not isinstance(errors, list):
            errors = []
            payload["errors"] = errors
        matching_attempts = 0
        retained_errors: list[dict[str, Any]] = []
        for item in errors:
            if not isinstance(item, dict):
                continue
            same_error = bool(
                self._canonical_rel_path(str(item.get("path") or "")) == rel_path
                and str(item.get("phase") or "").strip().lower() == phase_text
                and str(item.get("object_id") or "").strip().lower() == object_id_text
            )
            if same_error:
                matching_attempts += max(1, int(item.get("attempts", 1) or 1))
                continue
            retained_errors.append(item)
        errors = retained_errors
        payload["errors"] = errors
        errors.append(
            {
                "ts": _utc_now_iso(),
                "path": rel_path,
                "phase": phase_text,
                "reason": reason_text,
                "object_id": object_id_text,
                "attempts": matching_attempts + 1,
            }
        )
        max_entries = 500
        if len(errors) > max_entries:
            payload["errors"] = errors[-max_entries:]
        _write_json(self._sync_errors_path, payload)
        self._sync_error_summary_cache = None

    def list_sync_errors(self, limit: int = 200) -> list[dict[str, Any]]:
        payload = _read_json(self._sync_errors_path, {"errors": []})
        errors = payload.get("errors")
        if not isinstance(errors, list):
            return []
        state = _read_json(self._state_path, self._default_state())
        last_success = str(state.get("homebase", {}).get("last_sync_at") or "").strip()
        sanitized: list[dict[str, Any]] = []
        for item in errors:
            if not isinstance(item, dict):
                continue
            path = str(item.get("path") or "").strip().replace("\\", "/").lstrip("/")
            if not path:
                continue
            detected_at = str(item.get("ts") or "").strip()
            resolved_at = str(item.get("resolved_at") or "").strip()
            # Older builds did not persist a resolved marker. A later clean
            # sync is proof that those failures no longer block the pull.
            active = not resolved_at and (not last_success or not detected_at or detected_at > last_success)
            sanitized.append(
                {
                    "ts": detected_at,
                    "path": path,
                    "phase": str(item.get("phase") or "unknown").strip() or "unknown",
                    "reason": str(item.get("reason") or "").strip() or "Unknown error",
                    "object_id": str(item.get("object_id") or "").strip().lower(),
                    "attempts": max(1, int(item.get("attempts", 1) or 1)),
                    "resolved_at": resolved_at,
                    "active": active,
                }
            )
        if limit > 0:
            sanitized = sanitized[-int(limit) :]
        # Show newest entries first in UI.
        sanitized.reverse()
        return sanitized

    def sync_error_summary(self) -> dict[str, Any]:
        """Return lightweight active/history counts for the status UI."""
        try:
            error_stat = self._sync_errors_path.stat()
            state_stat = self._state_path.stat()
            stamp = (
                int(getattr(error_stat, "st_mtime_ns", 0) or 0),
                int(getattr(state_stat, "st_mtime_ns", 0) or 0),
            )
        except OSError:
            stamp = (0, 0)
        cached = self._sync_error_summary_cache
        if cached and cached[0] == stamp:
            return dict(cached[1])
        errors = self.list_sync_errors(limit=0)
        active = sum(bool(item.get("active")) for item in errors)
        summary = {
            "active": active,
            "resolved": max(0, len(errors) - active),
            "total": len(errors),
            "latest": errors[0] if errors else None,
        }
        self._sync_error_summary_cache = (stamp, dict(summary))
        return summary

    def dismiss_resolved_sync_errors(self) -> int:
        """Remove historical errors that predate the latest successful sync."""
        payload = _read_json(self._sync_errors_path, {"errors": []})
        raw_errors = payload.get("errors")
        if not isinstance(raw_errors, list):
            return 0
        active_keys = {
            (
                str(item.get("path") or ""),
                str(item.get("phase") or ""),
                str(item.get("object_id") or ""),
                str(item.get("ts") or ""),
            )
            for item in self.list_sync_errors(limit=0)
            if item.get("active")
        }
        retained = [
            item
            for item in raw_errors
            if isinstance(item, dict)
            and (
                str(item.get("path") or ""),
                str(item.get("phase") or ""),
                str(item.get("object_id") or ""),
                str(item.get("ts") or ""),
            )
            in active_keys
        ]
        removed = len(raw_errors) - len(retained)
        if removed:
            payload["errors"] = retained
            _write_json(self._sync_errors_path, payload)
            self._sync_error_summary_cache = None
        return removed

    def _clear_sync_errors_for_paths(self, paths: set[str]) -> None:
        cleaned_paths = {
            self._canonical_rel_path(str(path or ""))
            for path in paths
            if self._canonical_rel_path(str(path or ""))
        }
        if not cleaned_paths:
            return
        payload = _read_json(self._sync_errors_path, {"errors": []})
        errors = payload.get("errors")
        if not isinstance(errors, list):
            return
        retained = [
            item
            for item in errors
            if not isinstance(item, dict)
            or self._canonical_rel_path(str(item.get("path") or "")) not in cleaned_paths
        ]
        if len(retained) == len(errors):
            return
        payload["errors"] = retained
        _write_json(self._sync_errors_path, payload)
        self._sync_error_summary_cache = None

    def _load_local_deletions(self) -> set[str]:
        payload = _read_json(self._local_deletions_path, {"paths": []})
        paths = payload.get("paths")
        if not isinstance(paths, list):
            return set()
        return {
            self._canonical_rel_path(str(path or ""))
            for path in paths
            if self._canonical_rel_path(str(path or ""))
        }

    def _save_local_deletions(self, paths: set[str]) -> None:
        cleaned = sorted(
            {
                self._canonical_rel_path(str(path or ""))
                for path in paths
                if self._canonical_rel_path(str(path or ""))
            }
        )
        _write_json(
            self._local_deletions_path,
            {
                "schema_version": 1,
                "vault_id": self.cfg.vault_id,
                "updated_at": _utc_now_iso(),
                "paths": cleaned,
            },
        )

    def mark_remote_path_deleted(self, path: str) -> bool:
        """Confirm that an absent local path should be removed from Homebase."""
        rel_path = self._canonical_rel_path(str(path or ""))
        if not rel_path or rel_path.startswith(".stillpoint/"):
            return False
        if ".." in Path(rel_path).parts:
            return False
        try:
            if self._local_path_for_rel(rel_path).exists():
                return False
        except OSError:
            # An unrepresentable path is necessarily absent for sync purposes.
            pass
        deletions = self._load_local_deletions()
        deletions.add(rel_path)
        self._save_local_deletions(deletions)
        _log(f"local deletion confirmed path={rel_path}")
        return True

    def preserve_local_files_for_missing_objects(self) -> dict[str, int]:
        """Repair matching objects and safely supersede irrecoverable remote versions.

        A missing object cannot be reconstructed from a different local version.
        Before allowing that local version to replace the broken checkpoint, retain
        a pinned recovery copy outside the vault.
        """
        errors = [
            item
            for item in self.list_sync_errors(limit=0)
            if item.get("active")
            and str(item.get("phase") or "").strip().lower() == "download"
            and self._is_valid_object_id(str(item.get("object_id") or "").strip().lower())
        ]
        if not errors:
            raise ValueError("There are no active missing Homebase objects to resolve")

        key = derive_key_from_passphrase(self.cfg.passphrase, self.cfg.vault_id)
        local_versions: list[tuple[str, str, bytes, bytes]] = []
        for error in errors:
            rel_path = self._canonical_rel_path(str(error.get("path") or ""))
            object_id = str(error.get("object_id") or "").strip().lower()
            if not rel_path or rel_path.startswith(".stillpoint/"):
                raise ValueError("A missing Homebase object has an unsafe path")
            local_path = self._local_path_for_rel(rel_path)
            if not local_path.is_file():
                raise ValueError(f"Local file required to preserve '/{rel_path}' is missing")
            plaintext = read_bytes(local_path)
            local_versions.append((rel_path, object_id, plaintext, encrypt_bytes(key, plaintext)))

        if not self.try_suspend_sync("missing Homebase object recovery"):
            raise ValueError(
                "Homebase is finishing an active sync. It has been paused; "
                "select this recovery action again once the status stops changing."
            )
        resumed = False
        client: Optional[HomebaseClient] = None
        try:
            client = HomebaseClient(
                base_url=self.cfg.remote_url,
                token=self.cfg.auth_token,
                vault_id=self.cfg.vault_id,
                local_ui_token=self.cfg.local_ui_token,
                verify_ssl=self.cfg.verify_ssl,
            )
            latest = client.get_latest()
            checkpoint_id = str(latest.get("checkpoint_id") or "").strip().lower()
            if not self._is_valid_object_id(checkpoint_id):
                raise ValueError("Homebase no longer has the broken checkpoint")
            manifest = json.loads(client.get_manifest(checkpoint_id).decode("utf-8"))
            entries = self._canonicalize_manifest_entries(manifest.get("entries", {}))
            for rel_path, object_id, _plaintext, _envelope in local_versions:
                remote_id = str((entries.get(rel_path) or {}).get("object_id") or "").strip().lower()
                if remote_id != object_id:
                    raise ValueError(
                        f"Homebase changed '/{rel_path}' since the missing-object error; retry sync first"
                    )

            repaired = 0
            replacements: list[tuple[str, str, bytes]] = []
            for rel_path, object_id, plaintext, envelope in local_versions:
                if object_id_from_ciphertext(envelope) == object_id:
                    client.put_object(object_id, envelope)
                    if not client.has_object(object_id):
                        raise ValueError(f"Homebase did not retain repaired object for '/{rel_path}'")
                    repaired += 1
                else:
                    replacements.append((rel_path, object_id, plaintext))

            if replacements:
                plan = [
                    {
                        "path": rel_path,
                        "planned_action": "preserve",
                        "expected_old_hash": hashlib.sha256(plaintext).hexdigest(),
                        "new_object_id": hashlib.sha256(plaintext).hexdigest(),
                        "new_size": len(plaintext),
                    }
                    for rel_path, _object_id, plaintext in replacements
                ]
                event = self.recovery.begin(
                    operation="missing-object-local-resolution",
                    source_checkpoint_id=checkpoint_id,
                    target_checkpoint_id=None,
                    remote_device_id=str(manifest.get("device_id") or "remote"),
                    plan=plan,
                )
                for rel_path, _object_id, _plaintext in replacements:
                    self.recovery.mark(event, rel_path, "applied")
                self.recovery.finish(event)
                self.recovery.pin(
                    event["event_id"],
                    True,
                    "Local files preserved before replacing missing Homebase objects",
                )

                state = _read_json(self._state_path, self._default_state())
                hb = state.setdefault("homebase", {})
                hb["last_seen_latest_checkpoint_id"] = checkpoint_id
                pending = {
                    self._canonical_rel_path(str(path or ""))
                    for path in hb.get("pending_missing_object_resolution_paths", [])
                    if self._canonical_rel_path(str(path or ""))
                }
                pending.update(rel_path for rel_path, _object_id, _plaintext in replacements)
                hb["pending_missing_object_resolution_paths"] = sorted(pending)
                _write_json(self._state_path, state)
                _log(
                    f"missing objects superseded locally checkpoint={checkpoint_id} "
                    f"paths={len(replacements)} recovery_event={event['event_id']}"
                )
            if repaired:
                _log(f"missing Homebase objects repaired count={repaired} checkpoint={checkpoint_id}")
            self.resume_sync("resolve missing Homebase objects", sync_now=True)
            resumed = True
            return {"repaired": repaired, "preserved": len(replacements)}
        finally:
            if client is not None:
                client.close()
            if not resumed:
                self.resume_sync("missing Homebase object recovery")

    def _complete_missing_object_resolutions(self, homebase: dict[str, Any]) -> None:
        paths = {
            self._canonical_rel_path(str(path or ""))
            for path in homebase.pop("pending_missing_object_resolution_paths", [])
            if self._canonical_rel_path(str(path or ""))
        }
        if paths:
            self._clear_sync_errors_for_paths(paths)

    def resolve_conflict_entry(self, conflict_copy_path: str, resolution: str = "merged") -> bool:
        cleaned = str(conflict_copy_path or "").strip().replace("\\", "/").lstrip("/")
        if not cleaned:
            return False
        payload = _read_json(self._conflict_path, {"conflicts": []})
        conflicts = payload.get("conflicts")
        if not isinstance(conflicts, list):
            return False
        changed = False
        resolved_at = _utc_now_iso()
        for item in conflicts:
            if not isinstance(item, dict):
                continue
            item_copy = str(item.get("conflict_copy_path") or "").strip().replace("\\", "/").lstrip("/")
            if item_copy != cleaned:
                continue
            if item.get("resolved_at"):
                continue
            item["resolved_at"] = resolved_at
            item["resolution"] = str(resolution or "merged")
            changed = True
        if changed:
            _write_json(self._conflict_path, payload)
            self._set_status_locked(conflicts=self._conflict_count())
        return changed

    def _resolved_conflict_resolution(self, path: str, remote_checkpoint_id: str) -> Optional[str]:
        cleaned_path = str(path or "").strip().replace("\\", "/").lstrip("/")
        cleaned_checkpoint = str(remote_checkpoint_id or "").strip()
        if not cleaned_path or not cleaned_checkpoint:
            return None
        payload = _read_json(self._conflict_path, {"conflicts": []})
        conflicts = payload.get("conflicts")
        if not isinstance(conflicts, list):
            return None
        for item in reversed(conflicts):
            if not isinstance(item, dict):
                continue
            if not item.get("resolved_at"):
                continue
            item_path = str(item.get("path") or "").strip().replace("\\", "/").lstrip("/")
            item_checkpoint = str(item.get("remote_checkpoint_id") or "").strip()
            if item_path != cleaned_path or item_checkpoint != cleaned_checkpoint:
                continue
            resolution = str(item.get("resolution") or "").strip()
            return resolution or None
        return None

    def _queue_remote_updates(self, paths: list[str]) -> None:
        if not paths:
            return
        with self._remote_updates_lock:
            for path in paths:
                cleaned = str(path or "").strip()
                if cleaned:
                    self._pending_remote_updates.append(cleaned)

    @staticmethod
    def _is_valid_object_id(value: Any) -> bool:
        text = str(value or "").strip().lower()
        return len(text) == 64 and all(ch in string.hexdigits.lower() for ch in text)

    def _load_object_cache(self) -> dict[str, str]:
        payload = _read_json(self._object_cache_path, {"entries": {}})
        entries = payload.get("entries")
        if not isinstance(entries, dict):
            return {}
        out: dict[str, str] = {}
        for rel, oid in entries.items():
            rel_path = str(rel or "").strip().replace("\\", "/").lstrip("/")
            if not rel_path:
                continue
            if rel_path.startswith(".stillpoint/"):
                continue
            oid_text = str(oid or "").strip().lower()
            if not self._is_valid_object_id(oid_text):
                continue
            out[rel_path] = oid_text
        return out

    def _save_object_cache(self, entries: dict[str, str]) -> None:
        sanitized: dict[str, str] = {}
        for rel, oid in entries.items():
            rel_path = str(rel or "").strip().replace("\\", "/").lstrip("/")
            if not rel_path or rel_path.startswith(".stillpoint/"):
                continue
            oid_text = str(oid or "").strip().lower()
            if not self._is_valid_object_id(oid_text):
                continue
            sanitized[rel_path] = oid_text
        _write_json(
            self._object_cache_path,
            {
                "schema_version": 1,
                "vault_id": self.cfg.vault_id,
                "updated_at": _utc_now_iso(),
                "entries": sanitized,
            },
        )

    def _set_transfer_workers(self, workers: list[str]) -> None:
        self._set_status_locked(transfer_workers=list(workers))

    def _update_transfer_worker(self, slot_index: int, message: str) -> None:
        with self._status_lock:
            workers = list(getattr(self._status, "transfer_workers", []) or [])
            while len(workers) <= slot_index:
                workers.append("Idle")
            workers[slot_index] = str(message or "").strip() or "Idle"
            self._status.transfer_workers = workers
        self._emit_status()

    def _claim_pending_upload(self, slot_index: int, rel_key: str) -> None:
        with self._status_lock:
            workers = list(getattr(self._status, "transfer_workers", []) or [])
            while len(workers) <= slot_index:
                workers.append("Idle")
            workers[slot_index] = f"HEAD {rel_key}"
            self._status.transfer_workers = workers
            pending_uploads = max(0, int(getattr(self._status, "pending_uploads", 0) or 0) - 1)
            self._status.pending_uploads = pending_uploads
            self._status.summary = (
                f"Uploading {pending_uploads} object(s) remaining..."
                if pending_uploads > 0
                else "Finishing active uploads..."
            )
        self._emit_status()

    def _emit_status(self) -> None:
        if not self.status_callback:
            return
        try:
            self.status_callback(self.get_status())
        except Exception:
            pass

    def _set_status_locked(self, **updates: Any) -> None:
        with self._status_lock:
            for key, value in updates.items():
                setattr(self._status, key, value)
        self._emit_status()

    def _run_loop(self) -> None:
        while True:
            with self._cv:
                if self._stop:
                    return
                if self._sync_suspended:
                    self._cv.wait(timeout=None)
                    continue
                now = time.monotonic()
                interval_due_in = None
                if self.cfg.auto_sync:
                    if self._last_interval_run_at <= 0:
                        interval_due_in = max(0.0, float(self.cfg.interval_seconds))
                    else:
                        elapsed = now - self._last_interval_run_at
                        interval_due_in = max(0.0, float(self.cfg.interval_seconds) - elapsed)
                scheduled_due_in = None
                if self._next_run_at is not None:
                    scheduled_due_in = max(0.0, self._next_run_at - now)
                timeout_candidates = [v for v in (interval_due_in, scheduled_due_in) if v is not None]
                timeout = min(timeout_candidates) if timeout_candidates else None
                should_run = self._force_run
                if not should_run and self._next_run_at is not None and now >= self._next_run_at:
                    should_run = True
                if not should_run and interval_due_in is not None and interval_due_in <= 0:
                    should_run = True
                if not should_run:
                    self._cv.wait(timeout=timeout)
                    continue
                self._force_run = False
                self._next_run_at = None
                self._sync_in_progress = True
            try:
                self._sync_once()
            except Exception as exc:
                # Keep the background sync thread alive on unexpected errors.
                self._set_status_locked(
                    state="offline",
                    summary="Sync error (see logs)",
                    last_error=str(exc),
                    pending=False,
                )
                _log(f"sync loop unexpected failure: {exc}")
            finally:
                with self._cv:
                    self._sync_in_progress = False
                    self._last_interval_run_at = time.monotonic()
                    self._cv.notify_all()

    def _default_state(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "vault_id": self.cfg.vault_id,
            "device_id": self.cfg.device_id,
            "remote_mode": "homebase_remote",
            "homebase": {
                "last_seen_latest_checkpoint_id": None,
                "last_pulled_checkpoint_id": None,
                "last_pushed_checkpoint_id": None,
                "last_sync_at": None,
                "last_error": None,
                "error_count": 0,
                "backoff_until": None,
            },
        }

    def _sync_once(self, allow_refresh_retry: bool = True) -> None:
        ignore_backoff = False
        with self._cv:
            if self._ignore_backoff_once:
                ignore_backoff = True
                self._ignore_backoff_once = False
        self._set_status_locked(
            state="syncing",
            summary="Syncing...",
            pending=False,
            last_error=None,
            transfer_workers=[],
            pending_uploads=0,
            pending_downloads=0,
        )
        _log(f"sync started ignore_backoff={ignore_backoff}")
        state = _read_json(self._state_path, self._default_state())
        hb = state.setdefault("homebase", {})
        backoff_until = hb.get("backoff_until")
        if not ignore_backoff and backoff_until and isinstance(backoff_until, str):
            try:
                backoff_ts = time.strptime(backoff_until, "%Y-%m-%dT%H:%M:%SZ")
                # backoff_until is persisted as UTC "Z", so convert with timegm (UTC),
                # not mktime (local time), otherwise retries can be delayed for hours.
                backoff_epoch = float(calendar.timegm(backoff_ts))
                now_epoch = float(time.time())
                if now_epoch < backoff_epoch:
                    remaining = int(max(0.0, backoff_epoch - now_epoch))
                    last_error = str(hb.get("last_error") or "").strip()
                    self._set_status_locked(
                        state="offline",
                        summary=f"Offline (retry in {remaining}s)",
                        last_error=last_error or None,
                        last_sync_at=str(hb.get("last_sync_at") or "").strip() or None,
                        pending=False,
                        transfer_workers=[],
                        pending_uploads=0,
                        pending_downloads=0,
                    )
                    _log(
                        f"sync deferred by backoff_until={backoff_until} "
                        f"remaining={remaining}s last_error={last_error or 'unknown'}"
                    )
                    return
            except Exception:
                pass

        key = derive_key_from_passphrase(self.cfg.passphrase, self.cfg.vault_id)
        client = HomebaseClient(
            base_url=self.cfg.remote_url,
            token=self.cfg.auth_token,
            vault_id=self.cfg.vault_id,
            local_ui_token=self.cfg.local_ui_token,
            verify_ssl=self.cfg.verify_ssl,
        )
        try:
            latest = client.get_latest()
            remote_head = latest.get("checkpoint_id")
            local_seen = hb.get("last_seen_latest_checkpoint_id")
            object_cache = self._load_object_cache()
            scan_state = _read_json(self._scan_path, {"entries": {}})
            previous_scan = scan_state.get("entries") if isinstance(scan_state.get("entries"), dict) else {}
            local_files_before_pull = self._iter_sync_files()
            local_file_count = len(local_files_before_pull)
            local_paths_before_pull = {
                self._canonical_rel_path(rel_path)
                for rel_path, _full_path in local_files_before_pull
            }
            previously_scanned_paths = {
                self._canonical_rel_path(str(rel_path or ""))
                for rel_path in previous_scan
                if self._canonical_rel_path(str(rel_path or ""))
            }
            detected_local_deletions = previously_scanned_paths - local_paths_before_pull
            confirmed_local_deletions = self._load_local_deletions()
            local_deletions = detected_local_deletions | confirmed_local_deletions
            if detected_local_deletions:
                _log(
                    "local deletions detected from prior scan "
                    f"count={len(detected_local_deletions)}"
                )
            needs_bootstrap_pull = bool(
                remote_head
                and local_file_count == 0
                and not hb.get("last_pushed_checkpoint_id")
                and (
                    not hb.get("last_pulled_checkpoint_id")
                    or not object_cache
                    or not previous_scan
                )
            )
            pulled_remote = bool(remote_head and (remote_head != local_seen or needs_bootstrap_pull))
            if pulled_remote:
                reason = "bootstrap-empty-local" if needs_bootstrap_pull and remote_head == local_seen else "head-changed"
                _log(f"pull: remote head changed {local_seen} -> {remote_head} reason={reason}")
                applied_paths, pulled_object_cache = self._apply_remote_checkpoint(
                    client,
                    key,
                    remote_head,
                    known_object_cache=object_cache,
                    locally_deleted_paths=local_deletions,
                )
                self._queue_remote_updates(applied_paths)
                object_cache = dict(pulled_object_cache)
                self._save_object_cache(object_cache)
                if self._last_pull_incomplete:
                    raise ValueError(
                        "Homebase checkpoint pull was incomplete; failed files will be retried"
                    )
                hb["last_seen_latest_checkpoint_id"] = remote_head
                hb["last_pulled_checkpoint_id"] = remote_head
            else:
                _log(f"pull: no remote change latest={remote_head}")

            scan_now_epoch = float(time.time())
            try:
                prior_full_hash_epoch = float(scan_state.get("last_full_hash_epoch") or 0.0)
            except (TypeError, ValueError):
                prior_full_hash_epoch = 0.0
            force_full_hash = bool(
                prior_full_hash_epoch <= 0.0
                or prior_full_hash_epoch > scan_now_epoch + 300.0
                or (scan_now_epoch - prior_full_hash_epoch) >= _FULL_HASH_AUDIT_SECONDS
            )
            scan_file_items = self._iter_sync_files() if pulled_remote else local_files_before_pull
            local_file_count = len(scan_file_items)
            self._set_status_locked(summary=f"Checking local vault ({local_file_count} file(s))...")
            scan_file_stats: dict[str, os.stat_result] = {}
            manifest = self._build_local_manifest(
                file_items=scan_file_items,
                file_stats=scan_file_stats,
            )
            manifest_entries = [
                (rel, meta)
                for rel, meta in manifest.get("entries", {}).items()
                if isinstance(meta, dict)
            ]
            current_scan: dict[str, dict[str, Any]] = {}
            scan_total = len(manifest_entries)
            hashed_files = 0
            reused_hashes = 0
            for scan_index, (rel, meta) in enumerate(manifest_entries, start=1):
                full_path = self._local_path_for_rel(rel)
                file_stat = scan_file_stats[rel]
                mtime_ns = int(getattr(file_stat, "st_mtime_ns", 0) or 0)
                ctime_ns = int(getattr(file_stat, "st_ctime_ns", 0) or 0)
                previous_entry = previous_scan.get(rel)
                reusable_hash = ""
                if not force_full_hash and isinstance(previous_entry, dict):
                    previous_hash = str(previous_entry.get("content_sha256") or "").strip().lower()
                    try:
                        metadata_matches = bool(
                            int(previous_entry.get("size", -1)) == int(file_stat.st_size)
                            and int(previous_entry.get("mtime_ns", -1)) == mtime_ns
                            and int(previous_entry.get("ctime_ns", -1)) == ctime_ns
                        )
                    except (TypeError, ValueError):
                        metadata_matches = False
                    if self._is_valid_object_id(previous_hash) and metadata_matches:
                        reusable_hash = previous_hash
                if reusable_hash:
                    content_hash = reusable_hash
                    reused_hashes += 1
                else:
                    content_hash = sha256_file(full_path)
                    hashed_files += 1
                current_scan[rel] = {
                    "size": int(file_stat.st_size),
                    "mtime": int(meta.get("mtime", 0)),
                    "mtime_ns": mtime_ns,
                    "ctime_ns": ctime_ns,
                    "content_sha256": content_hash,
                }
                if scan_index == scan_total or scan_index % 25 == 0:
                    self._set_status_locked(
                        summary=(
                            f"Checking local vault ({scan_index}/{scan_total}; "
                            f"{hashed_files} hashed)..."
                        )
                    )
            last_full_hash_epoch = scan_now_epoch if force_full_hash else prior_full_hash_epoch
            current_scan_payload = {
                "schema_version": 1,
                "vault_id": self.cfg.vault_id,
                "updated_at": _utc_now_iso(),
                "last_full_hash_epoch": last_full_hash_epoch,
                "entries": current_scan,
            }
            unchanged_scan = previous_scan == current_scan
            _log(
                f"scan complete files={len(current_scan)} unchanged_scan={unchanged_scan} "
                f"hashed={hashed_files} reused_hashes={reused_hashes} "
                f"full_audit={'yes' if force_full_hash else 'no'} "
                f"had_last_push={bool(hb.get('last_pushed_checkpoint_id'))}"
            )
            if (
                unchanged_scan
                and hb.get("last_pushed_checkpoint_id")
                and not pulled_remote
                and not confirmed_local_deletions
            ):
                last_sync_at = _utc_now_iso()
                self._no_change_streak = min(
                    self._no_change_streak + 1,
                    self._hibernate_after_checks,
                )
                conflicts = self._conflict_count()
                hb["last_sync_at"] = last_sync_at
                hb["last_error"] = None
                hb["error_count"] = 0
                hb["backoff_until"] = None
                _write_json(self._state_path, state)
                _write_json(self._scan_path, current_scan_payload)
                state_name = "idle"
                summary = "Up to date"
                if self._no_change_streak >= self._hibernate_after_checks:
                    self._hibernating = True
                    state_name = "hibernated"
                    summary = "Hibernated (periodic checks continue)"
                self._set_status_locked(
                    state=state_name,
                    summary=summary,
                    last_sync_at=last_sync_at,
                    conflicts=conflicts,
                    transfer_workers=[],
                    pending_uploads=0,
                    pending_downloads=0,
                )
                _log(
                    f"push skipped (no local changes) "
                    f"no_change_streak={self._no_change_streak}/{self._hibernate_after_checks} "
                    f"hibernating={'yes' if self._hibernating else 'no'}"
                )
                if conflicts > 0:
                    self._log_recent_conflicts()
                return
            self._no_change_streak = 0
            self._hibernating = False
            manifest_bytes = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
            checkpoint_id = _manifest_id_bytes(manifest_bytes)
            _log(
                f"manifest staged checkpoint_candidate={checkpoint_id} files={len(manifest.get('entries', {}))}"
            )

            upload_count = 0
            existing_count = 0
            reused_cached_count = 0
            # Only verify cached objects on the server when the local cache
            # might be stale — i.e. we have never pushed successfully before,
            # or the last sync cycle ended in an error.  After a clean push
            # the objects in the cache are confirmed server-side and HEAD
            # requests for every unchanged file would be wasteful.
            needs_cache_verify = not hb.get("last_pushed_checkpoint_id") or int(hb.get("error_count", 0)) > 0
            verified_missing = 0
            upload_jobs: list[tuple[str, str, bytes]] = []
            preparation_jobs: list[tuple[str, str, str, dict[str, Any]]] = []
            for rel_path, meta in manifest.get("entries", {}).items():
                if not isinstance(meta, dict):
                    continue
                rel_key = str(rel_path)
                if rel_key.startswith(".stillpoint/"):
                    continue
                cached_object_id = ""
                prev = previous_scan.get(rel_key)
                current = current_scan.get(rel_key)
                if isinstance(prev, dict) and isinstance(current, dict):
                    previous_hash = str(prev.get("content_sha256") or "").strip().lower()
                    current_hash = str(current.get("content_sha256") or "").strip().lower()
                    if previous_hash and previous_hash == current_hash:
                        cached_object_id = str(object_cache.get(rel_key) or "").strip().lower()
                if self._is_valid_object_id(cached_object_id) and (
                    not needs_cache_verify or pulled_remote
                ):
                    # The persisted content fingerprint proves that this path
                    # still contains the bytes represented by the cached id.
                    meta["object_id"] = cached_object_id
                    reused_cached_count += 1
                    continue
                preparation_jobs.append((rel_key, str(rel_path), cached_object_id, meta))

            if preparation_jobs:
                max_workers = max(1, int(self.cfg.max_parallel_transfers or 1))
                worker_count = min(max_workers, len(preparation_jobs))
                available_slots: SimpleQueue[int] = SimpleQueue()
                for slot_index in range(worker_count):
                    available_slots.put(slot_index)

                self._set_status_locked(
                    summary=f"Preparing {len(preparation_jobs)} local object(s)...",
                    transfer_workers=["Idle"] * worker_count,
                )

                def _prepare_local_object(
                    rel_key: str,
                    rel_path: str,
                    cached_object_id: str,
                ) -> tuple[str, Optional[bytes], bool, bool]:
                    slot_index = available_slots.get()
                    known_missing = False
                    try:
                        if self._is_valid_object_id(cached_object_id):
                            self._update_transfer_worker(slot_index, f"CHECK {rel_key}")
                            if client.has_object(cached_object_id):
                                return cached_object_id, None, True, False
                            known_missing = True
                            _log(
                                "cached object missing on server "
                                f"path={rel_key} object_id={cached_object_id}"
                            )

                        self._update_transfer_worker(slot_index, f"PREP {rel_key}")
                        full = self._local_path_for_rel(rel_path)
                        envelope = encrypt_bytes(key, read_bytes(full))
                        object_id = object_id_from_ciphertext(envelope)
                        known_remote_object_id = str(object_cache.get(rel_key) or "").strip().lower()
                        if object_id == known_remote_object_id and not known_missing:
                            # Deterministic encryption makes an equal object id
                            # proof that the local bytes equal the cached object.
                            if needs_cache_verify and not pulled_remote:
                                self._update_transfer_worker(slot_index, f"CHECK {rel_key}")
                                if not client.has_object(object_id):
                                    known_missing = True
                                    _log(
                                        "computed cached object missing on server "
                                        f"path={rel_key} object_id={object_id}"
                                    )
                                else:
                                    return object_id, None, True, False
                            else:
                                return object_id, None, True, False
                        return object_id, envelope, False, known_missing
                    finally:
                        self._update_transfer_worker(slot_index, "Idle")
                        available_slots.put(slot_index)

                with ThreadPoolExecutor(
                    max_workers=worker_count,
                    thread_name_prefix="homebase-prep",
                ) as executor:
                    future_map = {
                        executor.submit(
                            _prepare_local_object,
                            rel_key,
                            rel_path,
                            cached_object_id,
                        ): (rel_key, meta)
                        for rel_key, rel_path, cached_object_id, meta in preparation_jobs
                    }
                    completed_preparations = 0
                    for future in as_completed(future_map):
                        rel_key, meta = future_map[future]
                        object_id, envelope, reused, missing = future.result()
                        meta["object_id"] = object_id
                        if reused:
                            reused_cached_count += 1
                        elif envelope is not None:
                            upload_jobs.append((rel_key, object_id, envelope))
                        if missing:
                            verified_missing += 1
                        completed_preparations += 1
                        if (
                            completed_preparations == len(preparation_jobs)
                            or completed_preparations % 25 == 0
                        ):
                            self._set_status_locked(
                                summary=(
                                    "Preparing local objects "
                                    f"({completed_preparations}/{len(preparation_jobs)})..."
                                )
                            )
                _log(
                    f"object preparation phase complete workers={worker_count} "
                    f"queued={len(preparation_jobs)} uploads={len(upload_jobs)}"
                )

            if upload_jobs:
                max_workers = max(1, int(self.cfg.max_parallel_transfers or 1))
                worker_count = min(max_workers, len(upload_jobs))
                available_slots: SimpleQueue[int] = SimpleQueue()
                for slot_index in range(worker_count):
                    available_slots.put(slot_index)

                self._set_status_locked(
                    summary=f"Uploading {len(upload_jobs)} object(s)...",
                    pending_uploads=len(upload_jobs),
                    transfer_workers=["Idle"] * worker_count,
                )

                def _run_upload(rel_key: str, object_id: str, envelope: bytes) -> bool:
                    slot_index = available_slots.get()
                    try:
                        self._claim_pending_upload(slot_index, rel_key)
                        if client.has_object(object_id):
                            return False
                        self._update_transfer_worker(slot_index, f"PUT {rel_key}")
                        client.put_object(object_id, envelope)
                        return True
                    finally:
                        self._update_transfer_worker(slot_index, "Idle")
                        available_slots.put(slot_index)

                with ThreadPoolExecutor(
                    max_workers=worker_count,
                    thread_name_prefix="homebase-put",
                ) as executor:
                    future_map = {
                        executor.submit(_run_upload, rel_key, object_id, envelope): (rel_key, object_id)
                        for rel_key, object_id, envelope in upload_jobs
                    }
                    for future in as_completed(future_map):
                        uploaded = future.result()
                        if uploaded:
                            upload_count += 1
                        else:
                            existing_count += 1
                    self._set_status_locked(
                        summary="Publishing manifest...",
                        pending_uploads=0,
                    )
                _log(
                    f"object upload phase complete workers={worker_count} "
                    f"queued={len(upload_jobs)} uploaded={upload_count} existing={existing_count}"
                )

            self._set_status_locked(
                summary="Publishing manifest...",
                pending_uploads=0,
                transfer_workers=[],
            )
            manifest_bytes = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
            checkpoint_id = _manifest_id_bytes(manifest_bytes)
            current_object_map: dict[str, str] = {}
            for rel_path, meta in manifest.get("entries", {}).items():
                if not isinstance(meta, dict):
                    continue
                rel_key = str(rel_path).strip().replace("\\", "/").lstrip("/")
                oid = str(meta.get("object_id") or "").strip().lower()
                if rel_key and self._is_valid_object_id(oid):
                    current_object_map[rel_key] = oid
            resolving_missing_objects = bool(hb.get("pending_missing_object_resolution_paths"))
            if (
                not resolving_missing_objects
                and current_object_map == object_cache
                and (hb.get("last_pushed_checkpoint_id") or remote_head)
            ):
                hb["last_seen_latest_checkpoint_id"] = remote_head or hb.get("last_seen_latest_checkpoint_id")
                if not hb.get("last_pushed_checkpoint_id") and remote_head:
                    # Treat an exactly matching pulled checkpoint as the local
                    # baseline.  Publishing an equivalent manifest with only a
                    # new timestamp/device id creates needless checkpoint churn.
                    hb["last_pushed_checkpoint_id"] = remote_head
                hb["last_sync_at"] = _utc_now_iso()
                hb["last_error"] = None
                hb["error_count"] = 0
                hb["backoff_until"] = None
                self._complete_missing_object_resolutions(hb)
                _write_json(self._state_path, state)
                _write_json(self._scan_path, current_scan_payload)
                if confirmed_local_deletions:
                    self._save_local_deletions(set())
                    self._clear_sync_errors_for_paths(confirmed_local_deletions)
                conflicts = self._conflict_count()
                self._set_status_locked(
                    state="idle",
                    summary="Up to date",
                    last_sync_at=hb["last_sync_at"],
                    last_error=None,
                    conflicts=conflicts,
                    pending_uploads=0,
                    pending_downloads=0,
                    transfer_workers=[],
                )
                _log(
                    "push skipped (object map unchanged)"
                    f" files={len(current_object_map)} reused_cached={reused_cached_count}"
                )
                return
            _log(
                f"push publish checkpoint={checkpoint_id} uploaded_objects={upload_count} "
                f"reused_objects={existing_count} reused_cached={reused_cached_count} "
                f"cache_verify={'yes' if needs_cache_verify else 'no'} verified_missing={verified_missing}"
            )
            client.put_manifest(checkpoint_id, manifest_bytes)
            client.put_latest(checkpoint_id)
            hb["last_pushed_checkpoint_id"] = checkpoint_id
            hb["last_seen_latest_checkpoint_id"] = checkpoint_id
            hb["last_sync_at"] = _utc_now_iso()
            hb["last_error"] = None
            hb["error_count"] = 0
            hb["backoff_until"] = None
            self._complete_missing_object_resolutions(hb)
            _write_json(self._state_path, state)
            _write_json(self._scan_path, current_scan_payload)
            self._save_object_cache(current_object_map)
            if confirmed_local_deletions:
                self._save_local_deletions(set())
                self._clear_sync_errors_for_paths(confirmed_local_deletions)
            conflicts = self._conflict_count()
            self._set_status_locked(
                state="idle",
                summary="Up to date",
                last_sync_at=hb["last_sync_at"],
                last_error=None,
                conflicts=conflicts,
                pending_uploads=0,
                pending_downloads=0,
                transfer_workers=[],
            )
            _log(f"sync complete, uploaded={upload_count}, conflicts={conflicts}")
            if conflicts > 0:
                self._log_recent_conflicts()
        except httpx.HTTPStatusError as exc:
            self._hibernating = False
            self._no_change_streak = 0
            if exc.response is not None and exc.response.status_code == 401:
                _log_token(
                    "sync request returned 401; access token rejected, "
                    "attempting refresh if a refresh token is available"
                )
            if (
                allow_refresh_retry
                and exc.response is not None
                and exc.response.status_code == 401
                and self._refresh_tokens()
            ):
                _log("auth refresh succeeded; retrying sync")
                return self._sync_once(allow_refresh_retry=False)
            count = int(hb.get("error_count", 0)) + 1
            unauthorized = exc.response is not None and exc.response.status_code == 401
            delay = 10 if unauthorized else min(300, 2 ** min(8, count))
            hb["error_count"] = count
            hb["last_error"] = str(exc)
            hb["backoff_until"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + delay))
            _write_json(self._state_path, state)
            summary = "Unauthorized (use Reset Auth)" if unauthorized else "Offline (changes pending)"
            self._set_status_locked(
                state="offline",
                summary=summary,
                last_error=str(exc),
                transfer_workers=[],
                pending_uploads=0,
                pending_downloads=0,
            )
            _log(
                f"sync failed: {exc} "
                f"error_count={count} next_retry_in={delay}s backoff_until={hb['backoff_until']}"
            )
        except RecoveryCancelled as exc:
            self._set_status_locked(
                state="paused",
                summary="Homebase pull cancelled; remote checkpoint remains pending",
                last_error=str(exc),
                pending_downloads=0,
                transfer_workers=[],
            )
            _log(str(exc))
        except (httpx.HTTPError, OSError, ValueError) as exc:
            self._hibernating = False
            self._no_change_streak = 0
            count = int(hb.get("error_count", 0)) + 1
            delay = min(300, 2 ** min(8, count))
            hb["error_count"] = count
            hb["last_error"] = str(exc)
            hb["backoff_until"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + delay))
            _write_json(self._state_path, state)
            message = str(exc).lower()
            summary = "Offline (changes pending)"
            if "decryption failed" in message or "passphrase mismatch" in message:
                summary = "Auth error (check passphrase)"
            self._set_status_locked(
                state="offline",
                summary=summary,
                last_error=str(exc),
                transfer_workers=[],
                pending_uploads=0,
                pending_downloads=0,
            )
            _log(
                f"sync failed: {exc} "
                f"error_count={count} next_retry_in={delay}s backoff_until={hb['backoff_until']}"
            )
        finally:
            client.close()
            self._pause_for_interrupted_recovery()

    def _refresh_tokens(self) -> bool:
        refresh_token = str(self.cfg.refresh_token or "").strip()
        _log_token(
            f"refresh path: access={'set' if bool(self.cfg.auth_token) else 'missing'} "
            f"refresh={'set' if bool(refresh_token) else 'missing'}"
        )
        if not refresh_token:
            _log("auth refresh skipped (no refresh token)")
            _log_token("refresh skipped: no refresh token; re-auth required")
            return False
        url = f"{self.cfg.remote_url.rstrip('/')}/v1/homebase/bootstrap/refresh"
        headers: dict[str, str] = {}
        if self.cfg.local_ui_token:
            headers["x-local-ui-token"] = self.cfg.local_ui_token
        _log("auth 401 detected; attempting token refresh")
        _log_token("calling /v1/homebase/bootstrap/refresh")
        try:
            resp = httpx.post(
                url,
                json={"vault_id": self.cfg.vault_id, "refresh_token": refresh_token},
                headers=headers,
                timeout=20.0,
                verify=self.cfg.verify_ssl,
            )
            resp.raise_for_status()
            payload = resp.json()
            access = str(payload.get("access_token") or "").strip()
            refreshed = str(payload.get("refresh_token") or "").strip()
            if not access or not refreshed:
                _log("auth refresh failed (missing access/refresh token in response)")
                _log_token("refresh failed: response missing rotated tokens")
                return False
            self.cfg.auth_token = access
            self.cfg.refresh_token = refreshed
            if self.cfg.token_update_callback:
                try:
                    self.cfg.token_update_callback(access, refreshed)
                except Exception:
                    pass
            _log("auth refresh completed")
            _log_token("refresh succeeded: rotated access+refresh tokens stored")
            return True
        except Exception as exc:
            details = ""
            try:
                if "resp" in locals() and getattr(resp, "text", None):
                    details = f" status={resp.status_code} body={resp.text[:300]}"
            except Exception:
                details = ""
            _log(f"auth refresh failed: {exc}{details}")
            if "resp" in locals():
                try:
                    _log_token(f"refresh failed: status={resp.status_code}")
                except Exception:
                    _log_token("refresh failed: response unavailable")
            else:
                _log_token("refresh failed: request/transport exception")
            return False

    def _build_local_manifest(
        self,
        *,
        file_items: Optional[list[tuple[str, Path]]] = None,
        file_stats: Optional[dict[str, os.stat_result]] = None,
    ) -> dict[str, Any]:
        entries: dict[str, Any] = {}
        items = file_items if file_items is not None else self._iter_sync_files()
        for rel, full in items:
            file_stat = full.stat()
            if file_stats is not None:
                file_stats[rel] = file_stat
            entries[rel] = {
                "size": int(file_stat.st_size),
                "mtime": int(file_stat.st_mtime),
                "kind": "file",
                "object_id": "",
            }
        return {
            "schema_version": 1,
            "vault_id": self.cfg.vault_id,
            "created_at": _utc_now_iso(),
            "device_id": self.cfg.device_id,
            "entries": entries,
        }

    def _apply_remote_checkpoint(
        self,
        client: HomebaseClient,
        key: bytes,
        checkpoint_id: str,
        *,
        known_object_cache: Optional[dict[str, str]] = None,
        locally_deleted_paths: Optional[set[str]] = None,
    ) -> tuple[list[str], dict[str, str]]:
        _log(f"pull begin checkpoint={checkpoint_id}")
        self._last_pull_incomplete = False
        manifest_bytes = client.get_manifest(checkpoint_id)
        manifest = json.loads(manifest_bytes.decode("utf-8"))
        entries = self._canonicalize_manifest_entries(manifest.get("entries", {}))
        remote_device_id = str(manifest.get("device_id") or "remote")
        _log(f"pull manifest entries={len(entries)} remote_device={remote_device_id}")
        applied_paths: list[str] = []
        pulled_cache: dict[str, str] = {}
        downloaded = 0
        written_new = 0
        overwritten = 0
        unchanged = 0
        conflicts = 0
        cached_unchanged = 0
        download_errors = 0
        apply_errors = 0
        resolved_error_paths: set[str] = set()
        known_objects = dict(known_object_cache) if isinstance(known_object_cache, dict) else {}
        local_deletions = {
            self._canonical_rel_path(str(path or ""))
            for path in (locally_deleted_paths or set())
            if self._canonical_rel_path(str(path or ""))
        }
        remote_paths = {
            self._canonical_rel_path(str(rel))
            for rel, meta in entries.items()
            if isinstance(meta, dict)
            and not str(rel).startswith(".stillpoint/")
            and self._is_valid_object_id(str(meta.get("object_id") or "").strip().lower())
        }
        remote_deleted_paths = set(known_objects) - remote_paths
        applied_remote_deletions = 0
        preserved_local_deletions = 0
        cold_local_matches = 0
        # A copied/older vault may have no .stillpoint sync metadata even though
        # many of its files already equal the remote checkpoint.  Homebase
        # encryption is deterministic within a vault, so computing the local
        # encrypted object id gives us a safe content comparison without first
        # downloading the remote object.
        for rel, meta in entries.items():
            if not isinstance(meta, dict) or str(rel).startswith(".stillpoint/"):
                continue
            rel_key = self._canonical_rel_path(str(rel))
            if rel_key in local_deletions:
                continue
            remote_object_id = str(meta.get("object_id") or "").strip().lower()
            if not self._is_valid_object_id(remote_object_id):
                continue
            if str(known_objects.get(rel_key) or "").strip().lower() == remote_object_id:
                continue
            local_path = self._local_path_for_rel(rel_key)
            try:
                local_is_file = local_path.is_file()
            except OSError:
                local_is_file = False
            if not local_is_file:
                continue
            try:
                local_envelope = encrypt_bytes(key, read_bytes(local_path))
                local_object_id = object_id_from_ciphertext(local_envelope)
            except OSError:
                continue
            if local_object_id == remote_object_id:
                known_objects[rel_key] = remote_object_id
                cold_local_matches += 1
        if cold_local_matches:
            _log(
                f"pull cold-start local matches={cold_local_matches}; "
                "matching remote downloads skipped"
            )
        relevant_entries = [
            (rel, meta)
            for rel, meta in entries.items()
            if isinstance(meta, dict)
            and not str(rel).startswith(".stillpoint/")
            and meta.get("object_id")
            and self._canonical_rel_path(str(rel)) not in local_deletions
            and str(known_objects.get(self._canonical_rel_path(str(rel))) or "").strip().lower()
            != str(meta.get("object_id") or "").strip().lower()
        ]
        remaining_downloads = len(relevant_entries)
        download_outcomes: dict[str, tuple[Optional[bytes], Optional[Exception]]] = {}
        if remaining_downloads:
            max_workers = max(1, int(self.cfg.max_parallel_transfers or 1))
            worker_count = min(max_workers, remaining_downloads)
            available_slots: SimpleQueue[int] = SimpleQueue()
            for slot_index in range(worker_count):
                available_slots.put(slot_index)
            self._set_status_locked(
                summary=f"Pulling {remaining_downloads} object(s)...",
                pending_downloads=remaining_downloads,
                transfer_workers=["Idle"] * worker_count,
            )

            def _run_download(rel_key: str, object_id_text: str) -> tuple[Optional[bytes], Optional[Exception]]:
                slot_index = available_slots.get()
                try:
                    self._update_transfer_worker(slot_index, f"GET {rel_key}")
                    ciphertext = client.get_object(object_id_text)
                    actual_object_id = object_id_from_ciphertext(ciphertext)
                    if actual_object_id != object_id_text:
                        raise ValueError(
                            f"Homebase object integrity failed for '{rel_key}' "
                            f"(expected {object_id_text}, received {actual_object_id})"
                        )
                    try:
                        plaintext = decrypt_bytes(key, ciphertext)
                    except CryptoError as exc:
                        raise ValueError(
                            f"Homebase decryption failed for '{rel_key}' "
                            "(passphrase mismatch or corrupted object)"
                        ) from exc
                    return plaintext, None
                except Exception as exc:
                    return None, exc
                finally:
                    self._update_transfer_worker(slot_index, "Idle")
                    available_slots.put(slot_index)

            with ThreadPoolExecutor(
                max_workers=worker_count,
                thread_name_prefix="homebase-get",
            ) as executor:
                future_map = {}
                for rel, meta in relevant_entries:
                    rel_key = self._canonical_rel_path(str(rel))
                    object_id_text = str(meta.get("object_id") or "").strip().lower()
                    future = executor.submit(_run_download, rel_key, object_id_text)
                    future_map[future] = rel_key
                completed_downloads = 0
                for future in as_completed(future_map):
                    rel_key = future_map[future]
                    plaintext, error = future.result()
                    download_outcomes[rel_key] = (plaintext, error)
                    completed_downloads += 1
                    pending = max(0, remaining_downloads - completed_downloads)
                    self._set_status_locked(
                        summary=(
                            f"Pulling {pending} object(s)..."
                            if pending
                            else "Applying pulled files..."
                        ),
                        pending_downloads=pending,
                    )
            _log(
                f"object download phase complete workers={worker_count} "
                f"queued={remaining_downloads}"
            )
        # Decide every mutation before touching the vault. The recovery event
        # is the write-ahead record for both deletions and downloaded files.
        plan: list[dict[str, Any]] = []
        for rel_key in sorted(remote_deleted_paths):
            local_path = self._local_path_for_rel(rel_key)
            if not local_path.is_file():
                continue
            local_bytes = read_bytes(local_path)
            local_object_id = object_id_from_ciphertext(encrypt_bytes(key, local_bytes))
            if local_object_id == str(known_objects.get(rel_key) or "").strip().lower():
                plan.append({
                    "path": rel_key, "planned_action": "delete",
                    "expected_old_hash": hashlib.sha256(local_bytes).hexdigest(),
                    "new_object_id": None, "new_size": None,
                })
            else:
                preserved_local_deletions += 1
        for rel, meta in entries.items():
            if not isinstance(meta, dict) or str(rel).startswith(".stillpoint/") or not meta.get("object_id"):
                continue
            rel_key = self._canonical_rel_path(str(rel))
            object_id_text = str(meta.get("object_id") or "").strip().lower()
            if rel_key in local_deletions or str(known_objects.get(rel_key) or "").strip().lower() == object_id_text:
                continue
            plaintext, error = download_outcomes.get(rel_key, (None, None))
            if error is not None or plaintext is None:
                continue
            local_path = self._local_path_for_rel(rel_key)
            new_hash = hashlib.sha256(plaintext).hexdigest()
            if not local_path.is_file():
                plan.append({"path": rel_key, "planned_action": "create", "new_object_id": new_hash, "new_size": len(plaintext)})
                continue
            local_bytes = read_bytes(local_path)
            if local_bytes == plaintext:
                continue
            if rel_key.lower().endswith((".md", ".txt")):
                try:
                    if not has_material_text_difference(local_bytes.decode("utf-8"), plaintext.decode("utf-8")):
                        continue
                except UnicodeDecodeError:
                    pass
            remote_mtime = int(meta.get("mtime", 0) or 0)
            if remote_mtime > 0 and remote_mtime >= int(local_path.stat().st_mtime):
                plan.append({
                    "path": rel_key, "planned_action": "overwrite",
                    "expected_old_hash": hashlib.sha256(local_bytes).hexdigest(),
                    "new_object_id": new_hash, "new_size": len(plaintext),
                })
            elif self._resolved_conflict_resolution(rel_key, checkpoint_id) != "keep-local":
                conflict_rel = conflict_copy_path(rel_key, remote_device_id)
                conflict_path = self.cfg.vault_root / conflict_rel
                conflict_old = read_bytes(conflict_path) if conflict_path.is_file() else None
                plan.append({
                    "path": conflict_rel,
                    "planned_action": "overwrite" if conflict_old is not None else "conflict-copy",
                    "expected_old_hash": hashlib.sha256(conflict_old).hexdigest() if conflict_old is not None else None,
                    "new_object_id": new_hash, "new_size": len(plaintext),
                })
        recovery_event: Optional[dict[str, Any]] = None
        planned_actions = {item["path"]: item for item in plan}
        if plan and self.cfg.recovery_enabled:
            self._set_status_locked(summary="Preparing local recovery...")
            try:
                recovery_event = self.recovery.begin(
                    operation="normal-pull",
                    source_checkpoint_id=_read_json(self._state_path, self._default_state()).get("homebase", {}).get("last_pulled_checkpoint_id"),
                    target_checkpoint_id=checkpoint_id,
                    remote_device_id=remote_device_id,
                    plan=plan,
                )
            except (OSError, RecoveryError) as exc:
                for item in plan:
                    if item["planned_action"] in {"overwrite", "delete"}:
                        self._record_sync_error(path=item["path"], phase="recovery", reason=str(exc))
                raise
            _log(f"recovery protected event={recovery_event['event_id']} paths={len(plan)}")
            self._review_protected_plan(recovery_event, plan, len(known_objects))
            self.recovery.set_state(recovery_event, "applying")
        for rel_key in sorted(remote_deleted_paths):
            action = planned_actions.get(rel_key)
            if not action or action["planned_action"] != "delete":
                continue
            local_path = self._local_path_for_rel(rel_key)
            try:
                if hashlib.sha256(read_bytes(local_path)).hexdigest() != action["expected_old_hash"]:
                    raise RecoveryError(f"Local file changed after recovery preparation: {rel_key}")
                local_path.unlink()
                if recovery_event:
                    self.recovery.mark(recovery_event, rel_key, "applied")
            except (OSError, RecoveryError) as deletion_exc:
                if recovery_event:
                    self.recovery.mark(recovery_event, rel_key, "failed", str(deletion_exc))
                self._last_pull_incomplete = True
                apply_errors += 1
                self._record_sync_error(path=rel_key, phase="delete", reason=str(deletion_exc), object_id=str(known_objects.get(rel_key) or ""))
                continue
            applied_remote_deletions += 1
            applied_paths.append(rel_key)
            resolved_error_paths.add(rel_key)
        for rel, meta in entries.items():
            if not isinstance(meta, dict):
                continue
            if str(rel).startswith(".stillpoint/"):
                continue
            object_id = meta.get("object_id")
            if not object_id:
                continue
            rel_key = self._canonical_rel_path(str(rel))
            object_id_text = str(object_id).strip().lower()
            if rel_key in local_deletions:
                # The path existed in the last successful local scan, or the
                # user explicitly confirmed removal after a path error.  Keep
                # the remote id as the comparison baseline so the push half
                # publishes a manifest without this entry.
                if self._is_valid_object_id(object_id_text):
                    pulled_cache[rel_key] = object_id_text
                unchanged += 1
                _log(
                    f"pull decision=local-delete path={rel_key} "
                    f"object_id={object_id_text}"
                )
                continue
            local_path = self._local_path_for_rel(rel_key)
            if (
                self._is_valid_object_id(object_id_text)
                and str(known_objects.get(rel_key) or "").strip().lower() == object_id_text
            ):
                # Object ids are content-addressed encrypted bytes.  A matching
                # id proves this path is unchanged from the client's known
                # checkpoint, so there is nothing to download or decrypt.  A
                # local edit (or deletion) is intentionally left for the push
                # half of the cycle.
                pulled_cache[rel_key] = object_id_text
                cached_unchanged += 1
                unchanged += 1
                _log(f"pull decision=cached-object-unchanged path={rel_key} object_id={object_id_text}")
                continue
            plaintext, dl_exc = download_outcomes.get(
                rel_key,
                (None, ValueError(f"Homebase download result missing for '{rel_key}'")),
            )
            if isinstance(dl_exc, httpx.HTTPStatusError):
                # Authentication and server failures apply to the whole sync
                # attempt.  Let the outer handler refresh on 401 (or back off)
                # instead of recording a partial checkpoint as successfully
                # seen.  A genuine missing object remains path-local so other
                # valid entries can still be recovered during this pass.
                status_code = dl_exc.response.status_code if dl_exc.response is not None else 0
                if status_code != 404:
                    raise dl_exc
                # Don't abort the entire pull for a single missing object.
                # Remove from pulled_cache so the next sync cycle retries.
                pulled_cache.pop(rel_key, None)
                self._last_pull_incomplete = True
                download_errors += 1
                self._record_sync_error(
                    path=rel_key,
                    phase="download",
                    reason=f"/{rel_key}: {dl_exc}",
                    object_id=object_id_text,
                )
                remaining_downloads = max(0, remaining_downloads - 1)
                self._set_status_locked(
                    summary=(
                        f"Pulling {remaining_downloads} object(s)..."
                        if remaining_downloads
                        else "Applying pulled files..."
                    ),
                    pending_downloads=remaining_downloads,
                    transfer_workers=["Idle"],
                )
                _log(
                    f"pull decision=download-error path={rel} "
                    f"object_id={object_id_text} error={dl_exc}"
                )
                continue
            if dl_exc is not None:
                raise dl_exc
            if plaintext is None:
                raise ValueError(f"Homebase download returned no data for '{rel_key}'")
            downloaded += 1
            remote_mtime = int(meta.get("mtime", 0) or 0)
            try:
                if not local_path.exists():
                    if self.cfg.recovery_enabled and (
                        rel_key not in planned_actions or planned_actions[rel_key]["planned_action"] != "create"
                    ):
                        raise RecoveryError(f"Local path changed since recovery planning: {rel_key}")
                    self._update_transfer_worker(0, f"WRITE {rel_key}")
                    write_bytes_atomic(local_path, plaintext)
                    if recovery_event:
                        self.recovery.mark(recovery_event, rel_key, "applied")
                    if self._is_valid_object_id(object_id_text):
                        pulled_cache[rel_key] = object_id_text
                    written_new += 1
                    resolved_error_paths.add(rel_key)
                    applied_paths.append(str(rel))
                    remaining_downloads = max(0, remaining_downloads - 1)
                    self._set_status_locked(
                        summary=(
                            f"Pulling {remaining_downloads} object(s)..."
                            if remaining_downloads
                            else "Applying pulled files..."
                        ),
                        pending_downloads=remaining_downloads,
                        transfer_workers=["Idle"],
                    )
                    _log(
                        f"pull decision=new-file path={rel} remote_mtime={remote_mtime} "
                        f"remote_checkpoint={checkpoint_id} remote_device={remote_device_id}"
                    )
                    continue
                local_bytes = read_bytes(local_path)
                if bytes_equal(local_bytes, plaintext):
                    if self._is_valid_object_id(object_id_text):
                        pulled_cache[rel_key] = object_id_text
                    unchanged += 1
                    resolved_error_paths.add(rel_key)
                    continue
                if str(rel_key).lower().endswith((".md", ".txt")):
                    try:
                        local_text = local_bytes.decode("utf-8")
                        remote_text = plaintext.decode("utf-8")
                    except UnicodeDecodeError:
                        local_text = ""
                        remote_text = ""
                    else:
                        if not has_material_text_difference(local_text, remote_text):
                            unchanged += 1
                            resolved_error_paths.add(rel_key)
                            remaining_downloads = max(0, remaining_downloads - 1)
                            self._set_status_locked(
                                summary=(
                                    f"Pulling {remaining_downloads} object(s)..."
                                    if remaining_downloads
                                    else "Applying pulled files..."
                                ),
                                pending_downloads=remaining_downloads,
                                transfer_workers=["Idle"],
                            )
                            _log(
                                f"pull decision=non-material-text path={rel} remote_checkpoint={checkpoint_id} "
                                f"remote_device={remote_device_id}"
                            )
                            continue
                # Prefer last-writer-wins for normal cross-device edits:
                # if remote mtime is newer-or-equal, replace local contents directly.
                _, local_mtime = stat_file(local_path)
                local_mtime_i = int(local_mtime)
                if remote_mtime > 0 and remote_mtime >= int(local_mtime):
                    action = planned_actions.get(rel_key)
                    if self.cfg.recovery_enabled and (
                        not action or action["planned_action"] != "overwrite"
                        or hashlib.sha256(local_bytes).hexdigest() != action["expected_old_hash"]
                    ):
                        raise RecoveryError(f"Local file changed since recovery planning: {rel_key}")
                    self._update_transfer_worker(0, f"WRITE {rel_key}")
                    write_bytes_atomic(local_path, plaintext)
                    if recovery_event:
                        self.recovery.mark(recovery_event, rel_key, "applied")
                    if self._is_valid_object_id(object_id_text):
                        pulled_cache[rel_key] = object_id_text
                    overwritten += 1
                    resolved_error_paths.add(rel_key)
                    applied_paths.append(str(rel))
                    remaining_downloads = max(0, remaining_downloads - 1)
                    self._set_status_locked(
                        summary=(
                            f"Pulling {remaining_downloads} object(s)..."
                            if remaining_downloads
                            else "Applying pulled files..."
                        ),
                        pending_downloads=remaining_downloads,
                        transfer_workers=["Idle"],
                    )
                    _log(
                        f"pull decision=overwrite-lww path={rel} local_mtime={local_mtime_i} "
                        f"remote_mtime={remote_mtime} remote_checkpoint={checkpoint_id} "
                        f"remote_device={remote_device_id}"
                    )
                    continue
                prior_resolution = self._resolved_conflict_resolution(rel_key, checkpoint_id)
                if prior_resolution == "keep-local":
                    resolved_error_paths.add(rel_key)
                    _log(
                        f"pull decision=keep-local path={rel} local_mtime={local_mtime_i} "
                        f"remote_mtime={remote_mtime} remote_checkpoint={checkpoint_id} "
                        f"remote_device={remote_device_id}"
                    )
                    unchanged += 1
                    remaining_downloads = max(0, remaining_downloads - 1)
                    self._set_status_locked(
                        summary=(
                            f"Pulling {remaining_downloads} object(s)..."
                            if remaining_downloads
                            else "Applying pulled files..."
                        ),
                        pending_downloads=remaining_downloads,
                        transfer_workers=["Idle"],
                    )
                    continue
                conflict_rel = conflict_copy_path(rel_key, remote_device_id)
                conflict_path = self.cfg.vault_root / conflict_rel
                conflict_action = planned_actions.get(conflict_rel)
                if self.cfg.recovery_enabled and not conflict_action:
                    raise RecoveryError(f"Conflict path changed since recovery planning: {conflict_rel}")
                if conflict_action and conflict_action.get("expected_old_hash") is not None:
                    if not conflict_path.is_file() or hashlib.sha256(read_bytes(conflict_path)).hexdigest() != conflict_action["expected_old_hash"]:
                        raise RecoveryError(f"Conflict path changed since recovery planning: {conflict_rel}")
                elif conflict_path.exists():
                    raise RecoveryError(f"Conflict path appeared since recovery planning: {conflict_rel}")
                self._update_transfer_worker(0, f"WRITE {conflict_rel}")
                write_bytes_atomic(conflict_path, plaintext)
                if recovery_event:
                    self.recovery.mark(recovery_event, conflict_rel, "applied")
                applied_paths.append(str(conflict_rel))
                reason = "local_newer_than_remote" if remote_mtime > 0 else "remote_mtime_missing"
                _log(
                    f"pull decision=conflict-copy path={rel} local_mtime={local_mtime_i} "
                    f"remote_mtime={remote_mtime} reason={reason} "
                    f"remote_checkpoint={checkpoint_id} remote_device={remote_device_id} "
                    f"conflict_copy={conflict_rel}"
                )
                self._record_conflict(
                    path=rel_key,
                    conflict_copy=str(conflict_rel),
                    remote_checkpoint_id=checkpoint_id,
                    remote_device_id=remote_device_id,
                    local_mtime=local_mtime_i,
                    remote_mtime=remote_mtime,
                    reason=reason,
                )
                conflicts += 1
                remaining_downloads = max(0, remaining_downloads - 1)
                self._set_status_locked(
                    summary=(
                        f"Pulling {remaining_downloads} object(s)..."
                        if remaining_downloads
                        else "Applying pulled files..."
                    ),
                    pending_downloads=remaining_downloads,
                    transfer_workers=["Idle"],
                )
            except (OSError, RecoveryError) as apply_exc:
                # Keep pull progress moving when a specific local path cannot be
                # represented on this platform (e.g., WinError 123).
                pulled_cache.pop(rel_key, None)
                if recovery_event:
                    event_path = rel_key if rel_key in planned_actions else conflict_copy_path(rel_key, remote_device_id)
                    if event_path in planned_actions:
                        self.recovery.mark(recovery_event, event_path, "failed", str(apply_exc))
                self._last_pull_incomplete = True
                apply_errors += 1
                path_error = _is_unrepresentable_path_error(apply_exc)
                if path_error:
                    error_reason = (
                        f"/{rel_key}: this remote path cannot be represented on the local filesystem; "
                        f"rename it from another client ({apply_exc})"
                    )
                else:
                    error_reason = f"/{rel_key}: {apply_exc}"
                self._record_sync_error(
                    path=rel_key,
                    phase="path" if path_error else "apply",
                    reason=error_reason,
                    object_id=object_id_text,
                )
                remaining_downloads = max(0, remaining_downloads - 1)
                self._set_status_locked(
                    summary=(
                        f"Pulling {remaining_downloads} object(s)..."
                        if remaining_downloads
                        else "Applying pulled files..."
                    ),
                    pending_downloads=remaining_downloads,
                    transfer_workers=["Idle"],
                )
                _log(
                    f"pull decision=local-apply-error path={rel} "
                    f"object_id={object_id_text} error={apply_exc}"
                )
                continue
        if recovery_event:
            self.recovery.finish(recovery_event)
            if recovery_event["state"] == "complete":
                self.prune_recovery()
        if resolved_error_paths:
            self._clear_sync_errors_for_paths(resolved_error_paths)
        self._set_status_locked(
            pending_downloads=0,
            transfer_workers=[],
            summary="Scanning local changes...",
        )
        _log(
            f"pull complete downloaded={downloaded} written_new={written_new} "
            f"overwritten={overwritten} unchanged={unchanged} "
            f"cached_unchanged={cached_unchanged} cold_local_matches={cold_local_matches} "
            f"remote_deletions={applied_remote_deletions} "
            f"preserved_local_deletions={preserved_local_deletions} "
            f"conflicts={conflicts} "
            f"download_errors={download_errors} apply_errors={apply_errors}"
        )
        return applied_paths, pulled_cache

    def _apply_remote_checkpoint_authoritative(
        self,
        client: HomebaseClient,
        key: bytes,
        checkpoint_id: str,
    ) -> dict[str, str]:
        _log(f"reset pull begin checkpoint={checkpoint_id}")
        manifest_bytes = client.get_manifest(checkpoint_id)
        manifest = json.loads(manifest_bytes.decode("utf-8"))
        entries = self._canonicalize_manifest_entries(manifest.get("entries", {}))
        pulled_cache: dict[str, str] = {}
        downloaded: list[tuple[str, bytes, int]] = []
        plan: list[dict[str, Any]] = []
        for rel, meta in entries.items():
            if not isinstance(meta, dict):
                continue
            if str(rel).startswith(".stillpoint/"):
                continue
            object_id = str(meta.get("object_id") or "").strip()
            if not object_id:
                continue
            object_id_text = object_id.lower()
            rel_key = self._canonical_rel_path(str(rel))
            if self._is_valid_object_id(object_id_text):
                pulled_cache[rel_key] = object_id_text
            ciphertext = client.get_object(object_id)
            actual_object_id = object_id_from_ciphertext(ciphertext)
            if actual_object_id != object_id_text:
                raise ValueError(
                    f"Homebase object integrity failed for '{rel_key}' "
                    f"(expected {object_id_text}, received {actual_object_id})"
                )
            try:
                plaintext = decrypt_bytes(key, ciphertext)
            except CryptoError as exc:
                raise ValueError(
                    f"Homebase decryption failed for '{rel_key}' (passphrase mismatch or corrupted object)"
                ) from exc
            local_path = self._local_path_for_rel(rel_key)
            old_bytes = read_bytes(local_path) if local_path.is_file() else None
            if old_bytes != plaintext:
                plan.append({
                    "path": rel_key,
                    "planned_action": "overwrite" if old_bytes is not None else "create",
                    "expected_old_hash": hashlib.sha256(old_bytes).hexdigest() if old_bytes is not None else None,
                    "new_object_id": hashlib.sha256(plaintext).hexdigest(),
                    "new_size": len(plaintext),
                })
            remote_mtime = int(meta.get("mtime", 0) or 0)
            downloaded.append((rel_key, plaintext, remote_mtime))
        recovery_event: Optional[dict[str, Any]] = None
        planned = {item["path"]: item for item in plan}
        if plan and self.cfg.recovery_enabled:
            self._set_status_locked(summary="Preparing local recovery...")
            recovery_event = self.recovery.begin(
                operation="server-authoritative-reset",
                source_checkpoint_id=_read_json(self._state_path, self._default_state()).get("homebase", {}).get("last_pulled_checkpoint_id"),
                target_checkpoint_id=checkpoint_id,
                remote_device_id=str(manifest.get("device_id") or "remote"),
                plan=plan,
            )
            self._review_protected_plan(recovery_event, plan, len(self._iter_sync_files()))
            self.recovery.set_state(recovery_event, "applying")
        written = 0
        for rel_key, plaintext, remote_mtime in downloaded:
            local_path = self._local_path_for_rel(rel_key)
            action = planned.get(rel_key)
            if action is None:
                continue
            current = read_bytes(local_path) if local_path.is_file() else None
            if self.cfg.recovery_enabled and (
                (action["planned_action"] == "create" and current is not None)
                or (action["planned_action"] == "overwrite" and (current is None or hashlib.sha256(current).hexdigest() != action["expected_old_hash"]))
            ):
                if recovery_event:
                    self.recovery.mark(recovery_event, rel_key, "failed", "Local file changed after protection")
                raise RecoveryError(f"Local file changed after recovery protection: {rel_key}")
            try:
                write_bytes_atomic(local_path, plaintext)
            except OSError as exc:
                if recovery_event:
                    self.recovery.mark(recovery_event, rel_key, "failed", str(exc))
                raise
            if recovery_event:
                self.recovery.mark(recovery_event, rel_key, "applied")
            if remote_mtime > 0:
                try:
                    os.utime(local_path, (remote_mtime, remote_mtime))
                except OSError:
                    pass
            written += 1
        if recovery_event:
            self.recovery.finish(recovery_event)
            self.prune_recovery()
        _log(f"reset pull complete written={written}")
        return pulled_cache

    def _record_conflict(
        self,
        path: str,
        conflict_copy: str,
        remote_checkpoint_id: str,
        remote_device_id: str,
        local_mtime: int,
        remote_mtime: int,
        reason: str,
    ) -> None:
        payload = _read_json(
            self._conflict_path,
            {
                "schema_version": 1,
                "vault_id": self.cfg.vault_id,
                "conflicts": [],
            },
        )
        conflicts = payload.setdefault("conflicts", [])
        if not isinstance(conflicts, list):
            conflicts = []
            payload["conflicts"] = conflicts
        updated = False
        for item in conflicts:
            if not isinstance(item, dict):
                continue
            if item.get("resolved_at"):
                continue
            item_path = str(item.get("path") or "").strip().replace("\\", "/").lstrip("/")
            item_checkpoint = str(item.get("remote_checkpoint_id") or "").strip()
            if item_path != str(path).strip().replace("\\", "/").lstrip("/"):
                continue
            if item_checkpoint != str(remote_checkpoint_id or "").strip():
                continue
            item["ts"] = _utc_now_iso()
            item["conflict_copy_path"] = conflict_copy
            item["remote_device_id"] = remote_device_id
            item["local_mtime"] = int(local_mtime)
            item["remote_mtime"] = int(remote_mtime)
            item["reason"] = reason
            updated = True
            break
        if not updated:
            conflicts.append(
                {
                    "ts": _utc_now_iso(),
                    "path": path,
                    "conflict_copy_path": conflict_copy,
                    "remote_checkpoint_id": remote_checkpoint_id,
                    "remote_device_id": remote_device_id,
                    "local_mtime": int(local_mtime),
                    "remote_mtime": int(remote_mtime),
                    "reason": reason,
                }
            )
        _write_json(self._conflict_path, payload)

    def _conflict_count(self) -> int:
        return len(self.list_conflicts(limit=1000000))

    def _log_recent_conflicts(self, limit: int = 5) -> None:
        conflicts = self.list_conflicts(limit=1000000)
        if not conflicts:
            return
        recent = conflicts[-max(1, int(limit)) :]
        _log(f"conflicts present total={len(conflicts)} showing_last={len(recent)}")
        for item in recent:
            if not isinstance(item, dict):
                continue
            _log(
                "conflict detail "
                f"path={item.get('path', '')} "
                f"conflict_copy={item.get('conflict_copy_path', '')} "
                f"reason={item.get('reason', 'unknown')} "
                f"local_mtime={item.get('local_mtime', '')} "
                f"remote_mtime={item.get('remote_mtime', '')} "
                f"remote_checkpoint={item.get('remote_checkpoint_id', '')} "
                f"remote_device={item.get('remote_device_id', '')} "
                f"ts={item.get('ts', '')}"
            )
