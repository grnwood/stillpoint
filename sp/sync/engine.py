from __future__ import annotations

import json
import os
import threading
import time
import calendar
import string
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import httpx
from nacl.exceptions import CryptoError

from sp.logging_flags import log_enabled
from sp.sync.crypto import (
    decrypt_bytes,
    derive_key_from_passphrase,
    encrypt_bytes,
    object_id_from_ciphertext,
)
from sp.sync.homebase_client import HomebaseClient
from sp.sync.local_fs import bytes_equal, conflict_copy_path, iter_files, read_bytes, stat_file, write_bytes_atomic


_HOMEBASE_LOG = log_enabled("homebase_sync")
_ANSI_BLUE = "\033[94m"
_ANSI_RED = "\033[91m"
_ANSI_RESET = "\033[0m"


def _log(message: str) -> None:
    if _HOMEBASE_LOG:
        color = _ANSI_RED if "conflict" in str(message).lower() else _ANSI_BLUE
        print(f"{color}[HomebaseClient] {message}{_ANSI_RESET}")


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
    text = json.dumps(payload, indent=2, sort_keys=True)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
    tmp.replace(path)


def _manifest_id_bytes(manifest_bytes: bytes) -> str:
    import hashlib

    return hashlib.sha256(manifest_bytes).hexdigest()


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


@dataclass
class HomebaseSyncConfig:
    vault_root: Path
    vault_id: str
    device_id: str
    remote_url: str
    auth_token: str
    passphrase: str
    local_ui_token: str = ""
    refresh_token: str = ""
    auto_sync: bool = True
    interval_seconds: int = 60
    push_debounce_seconds: int = 3
    max_parallel_transfers: int = 6
    token_update_callback: Optional[Callable[[str, str], None]] = None


class HomebaseSyncEngine:
    def __init__(
        self,
        cfg: HomebaseSyncConfig,
        status_callback: Optional[Callable[[HomebaseSyncStatus], None]] = None,
    ) -> None:
        self.cfg = cfg
        self.status_callback = status_callback
        self._thread: Optional[threading.Thread] = None
        self._stop = False
        self._cv = threading.Condition()
        self._next_run_at: Optional[float] = None
        self._last_interval_run_at: float = 0.0
        self._force_run = False
        self._ignore_backoff_once = False
        self._status_lock = threading.Lock()
        self._status = HomebaseSyncStatus()
        self._no_change_streak = 0
        self._hibernating = False
        self._hibernate_after_checks = 3
        self._remote_updates_lock = threading.Lock()
        self._pending_remote_updates: list[str] = []
        self._sync_dir = self.cfg.vault_root / ".stillpoint" / "sync"
        self._state_path = self._sync_dir / "local_state.json"
        self._conflict_path = self._sync_dir / "conflict_log.json"
        self._scan_path = self._sync_dir / "last_scan.json"
        self._object_cache_path = self._sync_dir / "object_cache.json"

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop = False
        self._thread = threading.Thread(target=self._run_loop, name="homebase-sync", daemon=True)
        self._thread.start()
        _log(
            "engine start "
            f"vault_id={self.cfg.vault_id} device_id={self.cfg.device_id} "
            f"auto_sync={self.cfg.auto_sync} interval={self.cfg.interval_seconds}s "
            f"debounce={self.cfg.push_debounce_seconds}s"
        )
        if self.cfg.auto_sync:
            self.schedule_sync("startup")

    def stop(self) -> None:
        with self._cv:
            self._stop = True
            self._cv.notify_all()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        _log("engine stop")

    def schedule_sync(self, reason: str = "event") -> None:
        delay = max(1, int(self.cfg.push_debounce_seconds))
        with self._cv:
            self._hibernating = False
            self._next_run_at = time.monotonic() + delay
            self._set_status_locked(
                pending=True,
                summary=f"Sync scheduled ({reason})",
            )
            self._cv.notify_all()
        _log(f"scheduled sync in {delay}s ({reason})")

    def sync_now(self, reason: str = "manual") -> None:
        with self._cv:
            self._hibernating = False
            self._force_run = True
            self._ignore_backoff_once = True
            self._set_status_locked(pending=True, summary=f"Sync requested ({reason})")
            self._cv.notify_all()
        _log(f"sync now requested ({reason})")

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
            for rel, full in iter_files(self.cfg.vault_root):
                size, mtime = stat_file(full)
                current_scan[rel] = {"size": int(size), "mtime": int(mtime)}
            _write_json(
                self._scan_path,
                {
                    "schema_version": 1,
                    "vault_id": self.cfg.vault_id,
                    "updated_at": _utc_now_iso(),
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
                if self._hibernating and not self._force_run and self._next_run_at is None:
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
            self._last_interval_run_at = time.monotonic()

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
        self._set_status_locked(state="syncing", summary="Syncing...", pending=False, last_error=None)
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
                        summary="Offline (retry backoff)",
                        pending=False,
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
        )
        try:
            latest = client.get_latest()
            remote_head = latest.get("checkpoint_id")
            local_seen = hb.get("last_seen_latest_checkpoint_id")
            pulled_remote = bool(remote_head and remote_head != local_seen)
            object_cache = self._load_object_cache()
            if pulled_remote:
                _log(f"pull: remote head changed {local_seen} -> {remote_head}")
                applied_paths, pulled_object_cache = self._apply_remote_checkpoint(
                    client,
                    key,
                    remote_head,
                    local_object_cache=object_cache,
                )
                self._queue_remote_updates(applied_paths)
                hb["last_seen_latest_checkpoint_id"] = remote_head
                hb["last_pulled_checkpoint_id"] = remote_head
                object_cache = dict(pulled_object_cache)
                self._save_object_cache(object_cache)
            else:
                pulled_object_cache = {}
                _log(f"pull: no remote change latest={remote_head}")

            manifest = self._build_local_manifest()
            current_scan = {
                rel: {
                    "size": int(meta.get("size", 0)),
                    "mtime": int(meta.get("mtime", 0)),
                }
                for rel, meta in manifest.get("entries", {}).items()
                if isinstance(meta, dict)
            }
            scan_state = _read_json(self._scan_path, {"entries": {}})
            previous_scan = scan_state.get("entries") if isinstance(scan_state.get("entries"), dict) else {}
            unchanged_scan = previous_scan == current_scan
            _log(
                f"scan complete files={len(current_scan)} unchanged_scan={unchanged_scan} "
                f"had_last_push={bool(hb.get('last_pushed_checkpoint_id'))}"
            )
            if unchanged_scan and hb.get("last_pushed_checkpoint_id") and not pulled_remote:
                last_sync_at = _utc_now_iso()
                self._no_change_streak += 1
                conflicts = self._conflict_count()
                state_name = "idle"
                summary = "Up to date"
                if self._no_change_streak >= self._hibernate_after_checks:
                    self._hibernating = True
                    state_name = "hibernated"
                    summary = "Hibernated (waiting for edits/page load)"
                self._set_status_locked(
                    state=state_name,
                    summary=summary,
                    last_sync_at=last_sync_at,
                    conflicts=conflicts,
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
            for rel_path, meta in manifest.get("entries", {}).items():
                if not isinstance(meta, dict):
                    continue
                rel_key = str(rel_path)
                if rel_key.startswith(".stillpoint/"):
                    continue
                cached_object_id = ""
                if rel_key in pulled_object_cache:
                    cached_object_id = str(pulled_object_cache.get(rel_key) or "").strip().lower()
                else:
                    prev = previous_scan.get(rel_key)
                    if isinstance(prev, dict):
                        try:
                            prev_size = int(prev.get("size", -1))
                            prev_mtime = int(prev.get("mtime", -1))
                            cur_size = int(meta.get("size", -2))
                            cur_mtime = int(meta.get("mtime", -2))
                            if prev_size == cur_size and prev_mtime == cur_mtime:
                                cached_object_id = str(object_cache.get(rel_key) or "").strip().lower()
                        except Exception:
                            cached_object_id = ""
                if self._is_valid_object_id(cached_object_id):
                    meta["object_id"] = cached_object_id
                    reused_cached_count += 1
                    continue
                full = self.cfg.vault_root / rel_path
                plaintext = read_bytes(full)
                envelope = encrypt_bytes(key, plaintext)
                object_id = object_id_from_ciphertext(envelope)
                meta["object_id"] = object_id
                if not client.has_object(object_id):
                    client.put_object(object_id, envelope)
                    upload_count += 1
                else:
                    existing_count += 1

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
            if current_object_map == object_cache and hb.get("last_pushed_checkpoint_id"):
                hb["last_seen_latest_checkpoint_id"] = remote_head or hb.get("last_seen_latest_checkpoint_id")
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
                        "updated_at": _utc_now_iso(),
                        "entries": current_scan,
                    },
                )
                conflicts = self._conflict_count()
                self._set_status_locked(
                    state="idle",
                    summary="Up to date",
                    last_sync_at=hb["last_sync_at"],
                    last_error=None,
                    conflicts=conflicts,
                    pending_uploads=0,
                    pending_downloads=0,
                )
                _log(
                    "push skipped (object map unchanged)"
                    f" files={len(current_object_map)} reused_cached={reused_cached_count}"
                )
                return
            _log(
                f"push publish checkpoint={checkpoint_id} uploaded_objects={upload_count} "
                f"reused_objects={existing_count} reused_cached={reused_cached_count}"
            )
            client.put_manifest(checkpoint_id, manifest_bytes)
            client.put_latest(checkpoint_id)
            hb["last_pushed_checkpoint_id"] = checkpoint_id
            hb["last_seen_latest_checkpoint_id"] = checkpoint_id
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
                    "updated_at": _utc_now_iso(),
                    "entries": current_scan,
                },
            )
            self._save_object_cache(current_object_map)
            conflicts = self._conflict_count()
            self._set_status_locked(
                state="idle",
                summary="Up to date",
                last_sync_at=hb["last_sync_at"],
                last_error=None,
                conflicts=conflicts,
                pending_uploads=0,
                pending_downloads=0,
            )
            _log(f"sync complete, uploaded={upload_count}, conflicts={conflicts}")
            if conflicts > 0:
                self._log_recent_conflicts()
        except httpx.HTTPStatusError as exc:
            self._hibernating = False
            self._no_change_streak = 0
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
            )
            _log(
                f"sync failed: {exc} "
                f"error_count={count} next_retry_in={delay}s backoff_until={hb['backoff_until']}"
            )
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
            )
            _log(
                f"sync failed: {exc} "
                f"error_count={count} next_retry_in={delay}s backoff_until={hb['backoff_until']}"
            )
        finally:
            client.close()

    def _refresh_tokens(self) -> bool:
        refresh_token = str(self.cfg.refresh_token or "").strip()
        if not refresh_token:
            _log("auth refresh skipped (no refresh token)")
            return False
        url = f"{self.cfg.remote_url.rstrip('/')}/v1/homebase/bootstrap/refresh"
        headers: dict[str, str] = {}
        if self.cfg.local_ui_token:
            headers["x-local-ui-token"] = self.cfg.local_ui_token
        _log("auth 401 detected; attempting token refresh")
        try:
            resp = httpx.post(
                url,
                json={"vault_id": self.cfg.vault_id, "refresh_token": refresh_token},
                headers=headers,
                timeout=20.0,
            )
            resp.raise_for_status()
            payload = resp.json()
            access = str(payload.get("access_token") or "").strip()
            refreshed = str(payload.get("refresh_token") or "").strip()
            if not access or not refreshed:
                _log("auth refresh failed (missing access/refresh token in response)")
                return False
            self.cfg.auth_token = access
            self.cfg.refresh_token = refreshed
            if self.cfg.token_update_callback:
                try:
                    self.cfg.token_update_callback(access, refreshed)
                except Exception:
                    pass
            _log("auth refresh completed")
            return True
        except Exception as exc:
            details = ""
            try:
                if "resp" in locals() and getattr(resp, "text", None):
                    details = f" status={resp.status_code} body={resp.text[:300]}"
            except Exception:
                details = ""
            _log(f"auth refresh failed: {exc}{details}")
            return False

    def _build_local_manifest(self) -> dict[str, Any]:
        entries: dict[str, Any] = {}
        for rel, full in iter_files(self.cfg.vault_root):
            size, mtime = stat_file(full)
            entries[rel] = {
                "size": size,
                "mtime": mtime,
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
        local_object_cache: Optional[dict[str, str]] = None,
    ) -> tuple[list[str], dict[str, str]]:
        _log(f"pull begin checkpoint={checkpoint_id}")
        manifest_bytes = client.get_manifest(checkpoint_id)
        manifest = json.loads(manifest_bytes.decode("utf-8"))
        entries = manifest.get("entries", {})
        remote_device_id = str(manifest.get("device_id") or "remote")
        _log(f"pull manifest entries={len(entries)} remote_device={remote_device_id}")
        applied_paths: list[str] = []
        pulled_cache: dict[str, str] = {}
        downloaded = 0
        skipped_cached = 0
        written_new = 0
        overwritten = 0
        unchanged = 0
        conflicts = 0
        cache = local_object_cache or {}
        for rel, meta in entries.items():
            if not isinstance(meta, dict):
                continue
            if str(rel).startswith(".stillpoint/"):
                continue
            object_id = meta.get("object_id")
            if not object_id:
                continue
            rel_key = str(rel).strip().replace("\\", "/").lstrip("/")
            object_id_text = str(object_id).strip().lower()
            if self._is_valid_object_id(object_id_text):
                pulled_cache[rel_key] = object_id_text
            local_path = self.cfg.vault_root / rel
            if (
                rel_key
                and local_path.exists()
                and self._is_valid_object_id(object_id_text)
                and str(cache.get(rel_key) or "").strip().lower() == object_id_text
            ):
                unchanged += 1
                skipped_cached += 1
                continue
            ciphertext = client.get_object(str(object_id))
            try:
                plaintext = decrypt_bytes(key, ciphertext)
            except CryptoError as exc:
                raise ValueError(
                    f"Homebase decryption failed for '{rel}' (passphrase mismatch or corrupted object)"
                ) from exc
            downloaded += 1
            remote_mtime = int(meta.get("mtime", 0) or 0)
            if not local_path.exists():
                write_bytes_atomic(local_path, plaintext)
                written_new += 1
                applied_paths.append(str(rel))
                _log(
                    f"pull decision=new-file path={rel} remote_mtime={remote_mtime} "
                    f"remote_checkpoint={checkpoint_id} remote_device={remote_device_id}"
                )
                continue
            local_bytes = read_bytes(local_path)
            if bytes_equal(local_bytes, plaintext):
                unchanged += 1
                continue
            # Prefer last-writer-wins for normal cross-device edits:
            # if remote mtime is newer-or-equal, replace local contents directly.
            _, local_mtime = stat_file(local_path)
            local_mtime_i = int(local_mtime)
            if remote_mtime > 0 and remote_mtime >= int(local_mtime):
                write_bytes_atomic(local_path, plaintext)
                overwritten += 1
                applied_paths.append(str(rel))
                _log(
                    f"pull decision=overwrite-lww path={rel} local_mtime={local_mtime_i} "
                    f"remote_mtime={remote_mtime} remote_checkpoint={checkpoint_id} "
                    f"remote_device={remote_device_id}"
                )
                continue
            conflict_rel = conflict_copy_path(str(rel), remote_device_id)
            conflict_path = self.cfg.vault_root / conflict_rel
            write_bytes_atomic(conflict_path, plaintext)
            applied_paths.append(str(conflict_rel))
            reason = "local_newer_than_remote" if remote_mtime > 0 else "remote_mtime_missing"
            _log(
                f"pull decision=conflict-copy path={rel} local_mtime={local_mtime_i} "
                f"remote_mtime={remote_mtime} reason={reason} "
                f"remote_checkpoint={checkpoint_id} remote_device={remote_device_id} "
                f"conflict_copy={conflict_rel}"
            )
            self._record_conflict(
                path=str(rel),
                conflict_copy=str(conflict_rel),
                remote_checkpoint_id=checkpoint_id,
                remote_device_id=remote_device_id,
                local_mtime=local_mtime_i,
                remote_mtime=remote_mtime,
                reason=reason,
            )
            conflicts += 1
        _log(
            f"pull complete downloaded={downloaded} written_new={written_new} "
            f"overwritten={overwritten} unchanged={unchanged} "
            f"skipped_cached={skipped_cached} conflicts={conflicts}"
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
        entries = manifest.get("entries", {})
        pulled_cache: dict[str, str] = {}
        written = 0
        for rel, meta in entries.items():
            if not isinstance(meta, dict):
                continue
            if str(rel).startswith(".stillpoint/"):
                continue
            object_id = str(meta.get("object_id") or "").strip()
            if not object_id:
                continue
            object_id_text = object_id.lower()
            if self._is_valid_object_id(object_id_text):
                pulled_cache[str(rel)] = object_id_text
            ciphertext = client.get_object(object_id)
            try:
                plaintext = decrypt_bytes(key, ciphertext)
            except CryptoError as exc:
                raise ValueError(
                    f"Homebase decryption failed for '{rel}' (passphrase mismatch or corrupted object)"
                ) from exc
            local_path = self.cfg.vault_root / rel
            write_bytes_atomic(local_path, plaintext)
            remote_mtime = int(meta.get("mtime", 0) or 0)
            if remote_mtime > 0:
                try:
                    os.utime(local_path, (remote_mtime, remote_mtime))
                except OSError:
                    pass
            written += 1
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
