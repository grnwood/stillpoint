from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import httpx

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
_ANSI_RESET = "\033[0m"


def _log(message: str) -> None:
    if _HOMEBASE_LOG:
        print(f"{_ANSI_BLUE}[HomebaseClient] {message}{_ANSI_RESET}")


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
    auto_sync: bool = True
    interval_seconds: int = 60
    push_debounce_seconds: int = 3
    max_parallel_transfers: int = 6


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
        self._status_lock = threading.Lock()
        self._status = HomebaseSyncStatus()
        self._sync_dir = self.cfg.vault_root / ".stillpoint" / "sync"
        self._state_path = self._sync_dir / "local_state.json"
        self._conflict_path = self._sync_dir / "conflict_log.json"
        self._scan_path = self._sync_dir / "last_scan.json"

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
            self._next_run_at = time.monotonic() + delay
            self._set_status_locked(
                pending=True,
                summary=f"Sync scheduled ({reason})",
            )
            self._cv.notify_all()
        _log(f"scheduled sync in {delay}s ({reason})")

    def sync_now(self, reason: str = "manual") -> None:
        with self._cv:
            self._force_run = True
            self._set_status_locked(pending=True, summary=f"Sync requested ({reason})")
            self._cv.notify_all()
        _log(f"sync now requested ({reason})")

    def get_status(self) -> HomebaseSyncStatus:
        with self._status_lock:
            return HomebaseSyncStatus(**self._status.__dict__)

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
            self._sync_once()
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

    def _sync_once(self) -> None:
        self._set_status_locked(state="syncing", summary="Syncing...", pending=False, last_error=None)
        _log("sync started")
        state = _read_json(self._state_path, self._default_state())
        hb = state.setdefault("homebase", {})
        backoff_until = hb.get("backoff_until")
        if backoff_until and isinstance(backoff_until, str):
            try:
                backoff_ts = time.strptime(backoff_until, "%Y-%m-%dT%H:%M:%SZ")
                if time.time() < time.mktime(backoff_ts):
                    self._set_status_locked(
                        state="offline",
                        summary="Offline (retry backoff)",
                        pending=False,
                    )
                    _log(f"sync deferred by backoff_until={backoff_until}")
                    return
            except Exception:
                pass

        key = derive_key_from_passphrase(self.cfg.passphrase, self.cfg.vault_id)
        client = HomebaseClient(
            base_url=self.cfg.remote_url,
            token=self.cfg.auth_token,
            vault_id=self.cfg.vault_id,
        )
        try:
            latest = client.get_latest()
            remote_head = latest.get("checkpoint_id")
            local_seen = hb.get("last_seen_latest_checkpoint_id")
            pulled_remote = bool(remote_head and remote_head != local_seen)
            if pulled_remote:
                _log(f"pull: remote head changed {local_seen} -> {remote_head}")
                self._apply_remote_checkpoint(client, key, remote_head)
                hb["last_seen_latest_checkpoint_id"] = remote_head
                hb["last_pulled_checkpoint_id"] = remote_head
            else:
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
                hb["last_sync_at"] = _utc_now_iso()
                hb["last_error"] = None
                hb["backoff_until"] = None
                _write_json(self._state_path, state)
                conflicts = self._conflict_count()
                self._set_status_locked(
                    state="idle",
                    summary="Up to date",
                    last_sync_at=hb["last_sync_at"],
                    conflicts=conflicts,
                )
                _log("push skipped (no local changes)")
                return
            manifest_bytes = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
            checkpoint_id = _manifest_id_bytes(manifest_bytes)
            _log(
                f"manifest staged checkpoint_candidate={checkpoint_id} files={len(manifest.get('entries', {}))}"
            )

            upload_count = 0
            existing_count = 0
            for rel_path, meta in manifest.get("entries", {}).items():
                if not isinstance(meta, dict):
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
            _log(
                f"push publish checkpoint={checkpoint_id} uploaded_objects={upload_count} "
                f"reused_objects={existing_count}"
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
        except (httpx.HTTPError, OSError, ValueError) as exc:
            count = int(hb.get("error_count", 0)) + 1
            delay = min(300, 2 ** min(8, count))
            hb["error_count"] = count
            hb["last_error"] = str(exc)
            hb["backoff_until"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + delay))
            _write_json(self._state_path, state)
            self._set_status_locked(
                state="offline",
                summary="Offline (changes pending)",
                last_error=str(exc),
            )
            _log(f"sync failed: {exc}")
        finally:
            client.close()

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

    def _apply_remote_checkpoint(self, client: HomebaseClient, key: bytes, checkpoint_id: str) -> None:
        _log(f"pull begin checkpoint={checkpoint_id}")
        manifest_bytes = client.get_manifest(checkpoint_id)
        manifest = json.loads(manifest_bytes.decode("utf-8"))
        entries = manifest.get("entries", {})
        remote_device_id = str(manifest.get("device_id") or "remote")
        _log(f"pull manifest entries={len(entries)} remote_device={remote_device_id}")
        downloaded = 0
        written_new = 0
        unchanged = 0
        conflicts = 0
        for rel, meta in entries.items():
            if not isinstance(meta, dict):
                continue
            if str(rel).startswith(".stillpoint/"):
                continue
            object_id = meta.get("object_id")
            if not object_id:
                continue
            ciphertext = client.get_object(str(object_id))
            plaintext = decrypt_bytes(key, ciphertext)
            downloaded += 1
            local_path = self.cfg.vault_root / rel
            if not local_path.exists():
                write_bytes_atomic(local_path, plaintext)
                written_new += 1
                continue
            local_bytes = read_bytes(local_path)
            if bytes_equal(local_bytes, plaintext):
                unchanged += 1
                continue
            conflict_rel = conflict_copy_path(str(rel), remote_device_id)
            conflict_path = self.cfg.vault_root / conflict_rel
            write_bytes_atomic(conflict_path, plaintext)
            self._record_conflict(
                path=str(rel),
                conflict_copy=str(conflict_rel),
                remote_checkpoint_id=checkpoint_id,
                remote_device_id=remote_device_id,
            )
            _log(f"conflict copy created: {conflict_rel}")
            conflicts += 1
        _log(
            f"pull complete downloaded={downloaded} written_new={written_new} "
            f"unchanged={unchanged} conflicts={conflicts}"
        )

    def _record_conflict(
        self,
        path: str,
        conflict_copy: str,
        remote_checkpoint_id: str,
        remote_device_id: str,
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
            }
        )
        _write_json(self._conflict_path, payload)

    def _conflict_count(self) -> int:
        payload = _read_json(self._conflict_path, {"conflicts": []})
        conflicts = payload.get("conflicts")
        return len(conflicts) if isinstance(conflicts, list) else 0
