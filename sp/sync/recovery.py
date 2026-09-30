"""Device-local, write-ahead recovery records for Homebase file changes."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import uuid
from datetime import date, datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from sp.sync.local_fs import write_bytes_atomic
from sp.vault_boundary import path_crosses_nested_vault


SCHEMA_VERSION = 1
DEFAULT_QUOTA = 2 * 1024**3


class RecoveryError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _atomic_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2, sort_keys=True).encode("utf-8")
    fd, name = tempfile.mkstemp(prefix=".event-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        os.chmod(path, 0o600)
        try:
            directory = os.open(path.parent, os.O_RDONLY)
        except OSError:
            directory = None
        if directory is not None:
            try:
                os.fsync(directory)
            except OSError:
                pass
            finally:
                os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class RecoveryStore:
    def __init__(
        self,
        vault_root: Path,
        vault_id: str,
        device_id: str,
        *,
        base_dir: Path | None = None,
        quota_bytes: int = DEFAULT_QUOTA,
    ) -> None:
        self.vault_root = Path(vault_root).resolve()
        self.vault_id = str(vault_id)
        self.device_id = str(device_id)
        for value in (self.vault_id, self.device_id):
            if not value or any(not (char.isalnum() or char in "-_") for char in value):
                raise RecoveryError("Invalid recovery vault or device id")
        self.base_dir = Path(base_dir or Path.home() / ".stillpoint" / "homebase-recovery").resolve()
        self.root = self.base_dir / self.vault_id / self.device_id
        if self.root == self.vault_root or self.root.is_relative_to(self.vault_root):
            raise RecoveryError("Recovery storage must be outside the vault")
        self.objects = self.root / "objects"
        self.events = self.root / "events"
        self.quota_bytes = int(quota_bytes)
        for directory in (self.root.parent, self.root, self.objects, self.events):
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(directory, 0o700)
        self.db_path = self.root / "recovery.sqlite"
        with self._db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS events (event_id TEXT PRIMARY KEY, created_at TEXT, state TEXT, pinned INTEGER, label TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS refs (event_id TEXT, path TEXT, object_id TEXT, PRIMARY KEY(event_id, path))")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RecoveryError("Recovery database uses a newer unsupported schema")
            db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        os.chmod(self.db_path, 0o600)
        self._rebuild_index()

    def _rebuild_index(self) -> None:
        """Recover index entries after a crash between manifest and DB writes."""
        for path in self.events.glob("*.json"):
            try:
                event = json.loads(path.read_text(encoding="utf-8"))
                if int(event.get("schema_version", 0)) > SCHEMA_VERSION:
                    raise RecoveryError("Recovery event uses a newer unsupported schema")
                if event.get("schema_version") != SCHEMA_VERSION or event.get("vault_id") != self.vault_id or event.get("device_id") != self.device_id:
                    continue
                if path.stem != event.get("event_id"):
                    continue
                for item in event["paths"]:
                    self.path(item["path"])
                with self._db() as db:
                    db.execute("INSERT OR REPLACE INTO events VALUES (?, ?, ?, ?, ?)", (
                        event["event_id"], event["created_at"], event["state"], int(event["pinned"]), event.get("label"),
                    ))
                    db.execute("DELETE FROM refs WHERE event_id = ?", (event["event_id"],))
                    db.executemany("INSERT INTO refs VALUES (?, ?, ?)", (
                        (event["event_id"], item["path"], item["old_object_id"])
                        for item in event["paths"] if item.get("old_object_id")
                    ))
            except RecoveryError:
                raise
            except (OSError, ValueError, KeyError, TypeError):
                continue

    def _db(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def path(self, rel: str) -> Path:
        raw = str(rel)
        pure = PurePosixPath(raw)
        if not raw or raw.startswith("/") or "\\" in raw or any(part in ("", ".", "..") for part in raw.split("/")):
            raise RecoveryError(f"Unsafe recovery path: {raw}")
        if pure.parts[0] == ".stillpoint":
            raise RecoveryError(f"Recovery path targets vault metadata: {raw}")
        target = self.vault_root.joinpath(*pure.parts)
        if not target.resolve().is_relative_to(self.vault_root) or path_crosses_nested_vault(self.vault_root, target):
            raise RecoveryError(f"Recovery path escapes vault or enters nested vault: {raw}")
        return target

    def _object_path(self, digest: str) -> Path:
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise RecoveryError("Invalid recovery object id")
        return self.objects / digest[:2] / digest

    def _put_object(self, content: bytes) -> tuple[str, bool]:
        digest = hashlib.sha256(content).hexdigest()
        destination = self._object_path(digest)
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(destination.parent, 0o700)
        if destination.exists():
            if hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
                raise RecoveryError(f"Corrupt recovery object: {digest}")
            return digest, False
        fd, name = tempfile.mkstemp(prefix=".object-", dir=destination.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(name, 0o600)
            if not destination.exists():
                os.replace(name, destination)
                try:
                    directory = os.open(destination.parent, os.O_RDONLY)
                    try:
                        os.fsync(directory)
                    finally:
                        os.close(directory)
                except OSError:
                    pass
            if hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
                raise RecoveryError(f"Corrupt recovery object: {digest}")
            return digest, True
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def read_object(self, digest: str) -> bytes:
        content = self._object_path(digest).read_bytes()
        if hashlib.sha256(content).hexdigest() != digest:
            raise RecoveryError(f"Corrupt recovery object: {digest}")
        return content

    def usage_bytes(self) -> int:
        return sum(path.stat().st_size for path in self.objects.glob("*/*") if path.is_file())

    def device_usage_bytes(self) -> int:
        return sum(
            path.stat().st_size
            for path in self.base_dir.glob(f"*/{self.device_id}/objects/*/*")
            if path.is_file()
        )

    def _save(self, event: dict[str, Any]) -> None:
        if event.get("schema_version") != SCHEMA_VERSION:
            raise RecoveryError("Unsupported recovery event schema")
        _atomic_json(self.events / f"{event['event_id']}.json", event)
        with self._db() as db:
            db.execute(
                "INSERT OR REPLACE INTO events VALUES (?, ?, ?, ?, ?)",
                (event["event_id"], event["created_at"], event["state"], int(event["pinned"]), event.get("label")),
            )
            db.execute("DELETE FROM refs WHERE event_id = ?", (event["event_id"],))
            db.executemany(
                "INSERT INTO refs VALUES (?, ?, ?)",
                ((event["event_id"], item["path"], item["old_object_id"]) for item in event["paths"] if item.get("old_object_id")),
            )

    def load(self, event_id: str) -> dict[str, Any]:
        if not event_id or any(c not in "0123456789abcdef" for c in event_id):
            raise RecoveryError("Invalid recovery event id")
        event = json.loads((self.events / f"{event_id}.json").read_text(encoding="utf-8"))
        if event.get("schema_version") != SCHEMA_VERSION or event.get("vault_id") != self.vault_id or event.get("device_id") != self.device_id:
            raise RecoveryError("Recovery event has invalid identity or schema")
        for item in event["paths"]:
            self.path(item["path"])
        return event

    def list_events(self) -> list[dict[str, Any]]:
        with self._db() as db:
            ids = [row[0] for row in db.execute("SELECT event_id FROM events ORDER BY created_at DESC")]
        events = []
        for event_id in ids:
            try:
                events.append(self.load(event_id))
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return events

    def scan_interrupted(self) -> list[dict[str, Any]]:
        interrupted = []
        for event in self.list_events():
            state = event["state"]
            if state == "preparing":
                self.set_state(event, "failed")
                continue
            if state not in {"protected", "applying"}:
                continue
            if state == "applying":
                for item in event["paths"]:
                    target = self.path(item["path"])
                    current = target.read_bytes() if target.is_file() else None
                    current_hash = hashlib.sha256(current).hexdigest() if current is not None else None
                    old_hash = item.get("old_object_id")
                    new_hash = item.get("new_object_id")
                    if current_hash == new_hash:
                        item["result"] = "applied"
                    elif current_hash == old_hash:
                        item["result"] = "not-applied"
                    else:
                        item["result"] = "diverged"
                self._save(event)
            interrupted.append(event)
        return interrupted

    def acknowledge_interrupted(self) -> None:
        for event in self.scan_interrupted():
            self.set_state(event, "cancelled" if event["state"] == "protected" else "partial")

    def begin(
        self,
        *,
        operation: str,
        source_checkpoint_id: str | None,
        target_checkpoint_id: str | None,
        remote_device_id: str | None,
        plan: Iterable[dict[str, Any]],
    ) -> dict[str, Any]:
        items = [dict(item) for item in plan]
        if not items:
            raise RecoveryError("Cannot create an empty recovery event")
        event = {
            "schema_version": SCHEMA_VERSION,
            "event_id": uuid.uuid4().hex,
            "vault_id": self.vault_id,
            "device_id": self.device_id,
            "created_at": _now(),
            "completed_at": None,
            "operation": operation,
            "source_checkpoint_id": source_checkpoint_id,
            "target_checkpoint_id": target_checkpoint_id,
            "remote_device_id": remote_device_id,
            "state": "preparing",
            "pinned": False,
            "label": None,
            "paths": [],
        }
        self._save(event)
        try:
            for item in items:
                rel = item["path"]
                target = self.path(rel)
                action = item["planned_action"]
                if action not in {"create", "overwrite", "delete", "conflict-copy"}:
                    raise RecoveryError(f"Invalid recovery action: {action}")
                current = target.read_bytes() if target.is_file() else None
                expected = item.get("expected_old_hash")
                if expected is not None and hashlib.sha256(current or b"").hexdigest() != expected:
                    raise RecoveryError(f"Local file changed during recovery preparation: {rel}")
                if action in {"overwrite", "delete"} and current is None:
                    raise RecoveryError(f"Local file disappeared during recovery preparation: {rel}")
                if action == "create" and current is not None:
                    raise RecoveryError(f"Local file appeared during recovery preparation: {rel}")
                old_id = None
                old_size = None
                old_mtime_ns = None
                old_mode = None
                if action in {"overwrite", "delete"}:
                    old_id, _ = self._put_object(current)
                    stat = target.stat()
                    old_size = len(current)
                    old_mtime_ns = stat.st_mtime_ns
                    old_mode = stat.st_mode & 0o777
                event["paths"].append({
                    "path": rel,
                    "planned_action": action,
                    "result": "pending",
                    "old_object_id": old_id,
                    "new_object_id": item.get("new_object_id"),
                    "old_size": old_size,
                    "new_size": item.get("new_size"),
                    "old_mtime_ns": old_mtime_ns,
                    "old_mode": old_mode,
                    "error": None,
                })
            self._save(event)
            if self.device_usage_bytes() > self.quota_bytes:
                self.prune()
                if self.device_usage_bytes() > self.quota_bytes:
                    raise RecoveryError("Local recovery storage quota is full")
            event["state"] = "protected"
            self._save(event)
            return event
        except Exception:
            event["state"] = "failed"
            event["completed_at"] = _now()
            self._save(event)
            raise

    def set_state(self, event: dict[str, Any], state: str) -> None:
        event["state"] = state
        if state in {"complete", "partial", "cancelled", "failed"}:
            event["completed_at"] = _now()
        self._save(event)

    def mark(self, event: dict[str, Any], path: str, result: str, error: str | None = None) -> None:
        for item in event["paths"]:
            if item["path"] == path:
                item["result"] = result
                item["error"] = error
                self._save(event)
                return
        raise RecoveryError(f"Path absent from recovery event: {path}")

    def finish(self, event: dict[str, Any]) -> None:
        self.set_state(event, "complete" if all(item["result"] == "applied" for item in event["paths"]) else "partial")

    def pin(self, event_id: str, pinned: bool, label: str | None = None) -> None:
        event = self.load(event_id)
        if event["state"] not in {"complete", "partial"}:
            raise RecoveryError("Only completed or partial recovery events can be pinned")
        event["pinned"] = bool(pinned)
        event["label"] = label.strip() if pinned and label else None
        self._save(event)

    def delete(self, event_id: str) -> None:
        event = self.load(event_id)
        if event["pinned"] or event["state"] not in {"complete", "partial", "cancelled", "failed"}:
            raise RecoveryError("Pinned or incomplete recovery events cannot be deleted")
        with self._db() as db:
            db.execute("DELETE FROM refs WHERE event_id = ?", (event_id,))
            db.execute("DELETE FROM events WHERE event_id = ?", (event_id,))
        (self.events / f"{event_id}.json").unlink()
        self._sweep_objects()

    def _sweep_objects(self) -> None:
        # A malformed or unindexed retained manifest may still reference an
        # object. Keep all objects until that event has been inspected.
        if len(self.list_events()) != len(list(self.events.glob("*.json"))):
            return
        referenced = {
            item["old_object_id"]
            for event in self.list_events()
            for item in event["paths"]
            if item.get("old_object_id")
        }
        for path in self.objects.glob("*/*"):
            if path.is_file() and path.name not in referenced:
                path.unlink()

    def prune(self, versions_per_file: int = 3, daily_days: int = 7) -> None:
        events = self.list_events()
        keep: set[str] = set()
        versions: dict[str, set[str]] = {}
        days: set[str] = set()
        today = datetime.now(timezone.utc).date()
        for event in events:
            event_id = event["event_id"]
            if event["pinned"] or event["state"] not in {"complete", "partial", "cancelled", "failed"}:
                keep.add(event_id)
            if event["state"] not in {"complete", "partial"}:
                continue
            day = event["created_at"][:10]
            try:
                age_days = (today - date.fromisoformat(day)).days
                recent = 0 <= age_days < daily_days
            except ValueError:
                recent = False
            if recent and day not in days:
                keep.add(event_id)
                days.add(day)
            for item in event["paths"]:
                digest = item.get("old_object_id")
                if digest:
                    seen = versions.setdefault(item["path"], set())
                    if digest not in seen and len(seen) < versions_per_file:
                        seen.add(digest)
                        keep.add(event_id)
        for event in reversed(events):
            if event["event_id"] not in keep:
                self.delete(event["event_id"])

    def integrity_check(self) -> dict[str, list[str]]:
        with self._db() as db:
            referenced = {row[0] for row in db.execute("SELECT DISTINCT object_id FROM refs")}
        result: dict[str, list[str]] = {"missing": [], "corrupt": [], "orphaned": []}
        for digest in referenced:
            try:
                self.read_object(digest)
            except FileNotFoundError:
                result["missing"].append(digest)
            except RecoveryError:
                result["corrupt"].append(digest)
        for path in self.objects.glob("*/*"):
            if path.is_file() and path.name not in referenced:
                result["orphaned"].append(path.name)
        return result

    def restore(self, event_id: str, paths: Iterable[str] | None = None, *, full: bool = False, confirm_deletions: bool = False, allow_changed_paths: Iterable[str] = ()) -> dict[str, Any]:
        original = self.load(event_id)
        selected = set(paths) if paths is not None else {item["path"] for item in original["paths"]}
        allowed = set(allow_changed_paths)
        changes: list[tuple[dict[str, Any], bytes | None]] = []
        plan: list[dict[str, Any]] = []
        for item in original["paths"]:
            rel = item["path"]
            if rel not in selected or item["result"] != "applied":
                continue
            target = self.path(rel)
            current = target.read_bytes() if target.is_file() else None
            prior = self.read_object(item["old_object_id"]) if item.get("old_object_id") else None
            action = item["planned_action"]
            if action == "create":
                if not full:
                    continue
                if not confirm_deletions:
                    raise RecoveryError("Removing files created by the pull requires confirmation")
                if current is None:
                    continue
                if hashlib.sha256(current).hexdigest() != item.get("new_object_id"):
                    raise RecoveryError(f"Created file changed since pull: {rel}")
            elif prior is None:
                continue
            elif action == "overwrite" and rel not in allowed and (current is None or hashlib.sha256(current).hexdigest() != item.get("new_object_id")):
                raise RecoveryError(f"File changed since pull: {rel}")
            elif action == "delete" and current is not None and rel not in allowed:
                raise RecoveryError(f"Deleted file was recreated since pull: {rel}")
            changes.append((item, prior))
            plan.append({"path": rel, "planned_action": "delete" if prior is None else ("overwrite" if current is not None else "create"), "expected_old_hash": hashlib.sha256(current).hexdigest() if current is not None else None, "new_object_id": hashlib.sha256(prior).hexdigest() if prior is not None else None, "new_size": len(prior) if prior is not None else None})
        if not changes:
            raise RecoveryError("No restorable paths selected")
        reversal = self.begin(operation="restore", source_checkpoint_id=event_id, target_checkpoint_id=None, remote_device_id=None, plan=plan)
        self.set_state(reversal, "applying")
        for item, prior in changes:
            rel = item["path"]
            target = self.path(rel)
            try:
                if prior is None:
                    target.unlink()
                else:
                    write_bytes_atomic(target, prior)
                    if item.get("old_mode") is not None:
                        os.chmod(target, item["old_mode"])
                    os.utime(target, None)
                self.mark(reversal, rel, "applied")
            except OSError as exc:
                self.mark(reversal, rel, "failed", str(exc))
        self.finish(reversal)
        return reversal
