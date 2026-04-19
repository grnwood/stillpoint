from __future__ import annotations

import argparse
import json
import os
import shutil
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_ISO_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def _log(message: str) -> None:
    print(f"[HomebaseGC] {message}")


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return max(minimum, int(raw.strip()))
    except Exception:
        return default


def _env_float(name: str, default: float, *, minimum: float = 0.0) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return max(minimum, float(raw.strip()))
    except Exception:
        return default


def _read_json_default(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return dict(default)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return dict(default)
    return payload if isinstance(payload, dict) else dict(default)


def _parse_iso8601(value: str | None) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, _ISO_FORMAT).replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _format_bytes(value: int) -> str:
    size = float(max(0, value))
    units = ["B", "KB", "MB", "GB", "TB"]
    unit = units[0]
    for unit in units:
        if size < 1024.0 or unit == units[-1]:
            break
        size /= 1024.0
    return f"{size:.2f} {unit}"


def _iter_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return [path for path in root.rglob("*") if path.is_file()]


@dataclass(frozen=True)
class HomebaseGcPolicy:
    keep_latest: int
    keep_all_days: int
    keep_daily_days: int
    keep_weekly_days: int
    dry_run: bool
    min_checkpoint_count: int
    run_interval_seconds: float
    state_path: Path

    @classmethod
    def from_env(cls, *, vaults_root: Path) -> "HomebaseGcPolicy":
        gc_root = vaults_root / "homebase" / ".gc"
        return cls(
            keep_latest=_env_int("SP_HOMEBASE_GC_KEEP_LATEST", 50, minimum=1),
            keep_all_days=_env_int("SP_HOMEBASE_GC_KEEP_ALL_DAYS", 7, minimum=0),
            keep_daily_days=_env_int("SP_HOMEBASE_GC_KEEP_DAILY_DAYS", 30, minimum=0),
            keep_weekly_days=_env_int("SP_HOMEBASE_GC_KEEP_WEEKLY_DAYS", 90, minimum=0),
            dry_run=_env_bool("SP_HOMEBASE_GC_DRY_RUN", False),
            min_checkpoint_count=_env_int("SP_HOMEBASE_GC_MIN_CHECKPOINTS", 1, minimum=1),
            run_interval_seconds=_env_float("SP_HOMEBASE_GC_INTERVAL_SECONDS", 24 * 3600.0, minimum=60.0),
            state_path=gc_root / "state.json",
        )

    def describe(self) -> str:
        return (
            f"keep_latest={self.keep_latest} keep_all_days={self.keep_all_days} "
            f"keep_daily_days={self.keep_daily_days} keep_weekly_days={self.keep_weekly_days} "
            f"min_checkpoints={self.min_checkpoint_count} dry_run={'yes' if self.dry_run else 'no'} "
            f"interval_s={self.run_interval_seconds:.0f}"
        )

    def with_overrides(self, *, dry_run: bool | None = None) -> "HomebaseGcPolicy":
        if dry_run is None:
            return self
        return replace(self, dry_run=dry_run)


@dataclass
class CheckpointRecord:
    checkpoint_id: str
    created_at: datetime
    manifest_path: Path
    checkpoint_path: Path


@dataclass
class VaultGcStats:
    vault_id: str
    before_bytes: int = 0
    after_bytes: int = 0
    retained_checkpoints: int = 0
    total_checkpoints: int = 0
    deleted_manifest_count: int = 0
    deleted_manifest_bytes: int = 0
    deleted_checkpoint_count: int = 0
    deleted_checkpoint_bytes: int = 0
    deleted_object_count: int = 0
    deleted_object_bytes: int = 0
    deleted_paths: list[str] | None = None

    def __post_init__(self) -> None:
        if self.deleted_paths is None:
            self.deleted_paths = []

    @property
    def saved_bytes(self) -> int:
        return max(0, self.before_bytes - self.after_bytes)


def _vault_base_paths(vaults_root: Path) -> list[Path]:
    root = vaults_root / "homebase"
    if not root.exists():
        return []
    return sorted(
        [path for path in root.iterdir() if path.is_dir() and path.name != ".gc"],
        key=lambda path: path.name.lower(),
    )


def _collect_checkpoint_records(vault_base: Path) -> list[CheckpointRecord]:
    records: list[CheckpointRecord] = []
    for checkpoint_path in sorted((vault_base / "checkpoints").glob("*.json")):
        payload = _read_json_default(checkpoint_path, {})
        checkpoint_id = str(payload.get("checkpoint_id") or checkpoint_path.stem).strip().lower()
        if len(checkpoint_id) != 64:
            continue
        created_at = _parse_iso8601(str(payload.get("created_at") or ""))
        if created_at is None:
            created_at = datetime.fromtimestamp(checkpoint_path.stat().st_mtime, tz=timezone.utc)
        manifest_path = vault_base / "manifests" / checkpoint_id[:2] / checkpoint_id
        if not manifest_path.exists():
            continue
        records.append(
            CheckpointRecord(
                checkpoint_id=checkpoint_id,
                created_at=created_at,
                manifest_path=manifest_path,
                checkpoint_path=checkpoint_path,
            )
        )
    records.sort(key=lambda record: (record.created_at, record.checkpoint_id), reverse=True)
    return records


def _select_retained_checkpoints(records: list[CheckpointRecord], policy: HomebaseGcPolicy) -> set[str]:
    if not records:
        return set()
    retained: set[str] = set()
    now = datetime.now(timezone.utc)

    for record in records[: policy.keep_latest]:
        retained.add(record.checkpoint_id)

    day_buckets: set[str] = set()
    week_buckets: set[str] = set()
    for record in records:
        age = now - record.created_at
        age_days = max(0.0, age.total_seconds() / 86400.0)
        if age_days <= policy.keep_all_days:
            retained.add(record.checkpoint_id)
            continue
        if age_days <= policy.keep_daily_days:
            bucket = record.created_at.strftime("%Y-%m-%d")
            if bucket not in day_buckets:
                day_buckets.add(bucket)
                retained.add(record.checkpoint_id)
            continue
        if age_days <= policy.keep_weekly_days:
            iso_year, iso_week, _ = record.created_at.isocalendar()
            bucket = f"{iso_year:04d}-W{iso_week:02d}"
            if bucket not in week_buckets:
                week_buckets.add(bucket)
                retained.add(record.checkpoint_id)

    if len(retained) < policy.min_checkpoint_count:
        for record in records[: policy.min_checkpoint_count]:
            retained.add(record.checkpoint_id)

    return retained


def _collect_reachable_object_ids(records: list[CheckpointRecord], retained_ids: set[str]) -> set[str]:
    reachable: set[str] = set()
    for record in records:
        if record.checkpoint_id not in retained_ids:
            continue
        payload = _read_json_default(record.manifest_path, {})
        entries = payload.get("entries")
        if not isinstance(entries, dict):
            continue
        for meta in entries.values():
            if not isinstance(meta, dict):
                continue
            object_id = str(meta.get("object_id") or "").strip().lower()
            if len(object_id) == 64:
                reachable.add(object_id)
    return reachable


def _path_size(path: Path) -> int:
    try:
        return int(path.stat().st_size)
    except Exception:
        return 0


def _delete_path(path: Path, *, dry_run: bool) -> int:
    size = _path_size(path)
    if dry_run:
        return size
    if not path.exists():
        return 0
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()
    return size


def _compute_vault_bytes(vault_base: Path) -> int:
    return sum(_path_size(path) for path in _iter_files(vault_base))


def _run_vault_gc(vault_base: Path, policy: HomebaseGcPolicy) -> VaultGcStats:
    stats = VaultGcStats(vault_id=vault_base.name)
    stats.before_bytes = _compute_vault_bytes(vault_base)
    records = _collect_checkpoint_records(vault_base)
    stats.total_checkpoints = len(records)
    retained_ids = _select_retained_checkpoints(records, policy)
    stats.retained_checkpoints = len(retained_ids)
    reachable_object_ids = _collect_reachable_object_ids(records, retained_ids)

    _log(
        f"vault={stats.vault_id} size_before={_format_bytes(stats.before_bytes)} "
        f"policy=({policy.describe()}) checkpoints={stats.total_checkpoints} retained={stats.retained_checkpoints}"
    )

    for record in records:
        if record.checkpoint_id in retained_ids:
            continue
        manifest_bytes = _delete_path(record.manifest_path, dry_run=policy.dry_run)
        checkpoint_bytes = _delete_path(record.checkpoint_path, dry_run=policy.dry_run)
        stats.deleted_manifest_count += 1
        stats.deleted_manifest_bytes += manifest_bytes
        stats.deleted_checkpoint_count += 1
        stats.deleted_checkpoint_bytes += checkpoint_bytes
        stats.deleted_paths.append(str(record.manifest_path))
        stats.deleted_paths.append(str(record.checkpoint_path))

    for object_path in sorted(_iter_files(vault_base / "objects")):
        object_id = object_path.name.strip().lower()
        if object_id in reachable_object_ids:
            continue
        object_bytes = _delete_path(object_path, dry_run=policy.dry_run)
        stats.deleted_object_count += 1
        stats.deleted_object_bytes += object_bytes
        stats.deleted_paths.append(str(object_path))

    if not policy.dry_run:
        for prefix_dir in sorted((vault_base / "objects").glob("*")):
            if prefix_dir.is_dir() and not any(prefix_dir.iterdir()):
                _delete_path(prefix_dir, dry_run=False)
        for prefix_dir in sorted((vault_base / "manifests").glob("*")):
            if prefix_dir.is_dir() and not any(prefix_dir.iterdir()):
                _delete_path(prefix_dir, dry_run=False)

    stats.after_bytes = stats.before_bytes - (
        stats.deleted_manifest_bytes + stats.deleted_checkpoint_bytes + stats.deleted_object_bytes
    )
    if not policy.dry_run:
        stats.after_bytes = _compute_vault_bytes(vault_base)

    _log(
        f"vault={stats.vault_id} deleted_manifests={stats.deleted_manifest_count} "
        f"deleted_checkpoints={stats.deleted_checkpoint_count} deleted_objects={stats.deleted_object_count} "
        f"saved={_format_bytes(stats.saved_bytes)} size_after={_format_bytes(stats.after_bytes)}"
    )
    _log(
        f"vault={stats.vault_id} savings_breakdown "
        f"manifests={_format_bytes(stats.deleted_manifest_bytes)} "
        f"checkpoints={_format_bytes(stats.deleted_checkpoint_bytes)} "
        f"objects={_format_bytes(stats.deleted_object_bytes)}"
    )
    if stats.deleted_paths:
        action_label = "would_delete" if policy.dry_run else "deleted"
        for deleted_path in stats.deleted_paths:
            _log(f"vault={stats.vault_id} {action_label}={deleted_path}")
    return stats


def _should_skip_run(policy: HomebaseGcPolicy) -> bool:
    payload = _read_json_default(policy.state_path, {})
    last_run_at = float(payload.get("last_run_at") or 0.0)
    if last_run_at <= 0:
        return False
    age = time.time() - last_run_at
    if age >= policy.run_interval_seconds:
        return False
    _log(
        f"skipping run because interval has not elapsed: age_s={age:.0f} required_s={policy.run_interval_seconds:.0f}"
    )
    return True


def _write_state(policy: HomebaseGcPolicy, *, vault_count: int, total_saved_bytes: int) -> None:
    policy.state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "last_run_at": time.time(),
        "last_run_at_iso": datetime.now(timezone.utc).strftime(_ISO_FORMAT),
        "vault_count": vault_count,
        "total_saved_bytes": total_saved_bytes,
        "dry_run": policy.dry_run,
    }
    policy.state_path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")), encoding="utf-8")


def run_homebase_gc(
    *,
    vaults_root: str | Path | None = None,
    force: bool = False,
    dry_run: bool | None = None,
) -> int:
    root_value = Path(vaults_root) if vaults_root is not None else Path(os.getenv("STILLPOINT_VAULTS_ROOT", "vaults"))
    vaults_root_path = root_value.expanduser().resolve()
    policy = HomebaseGcPolicy.from_env(vaults_root=vaults_root_path).with_overrides(dry_run=dry_run)
    _log(f"vaults_root={vaults_root_path}")
    _log(f"retention_policy={policy.describe()}")

    if not force and _should_skip_run(policy):
        return 0

    vault_bases = _vault_base_paths(vaults_root_path)
    if not vault_bases:
        _log("no Home Base vaults found")
        _write_state(policy, vault_count=0, total_saved_bytes=0)
        return 0

    total_before = 0
    total_after = 0
    total_saved = 0
    for vault_base in vault_bases:
        stats = _run_vault_gc(vault_base, policy)
        total_before += stats.before_bytes
        total_after += stats.after_bytes
        total_saved += stats.saved_bytes

    _log(
        f"summary vaults={len(vault_bases)} total_before={_format_bytes(total_before)} "
        f"total_after={_format_bytes(total_after)} total_saved={_format_bytes(total_saved)}"
    )
    _write_state(policy, vault_count=len(vault_bases), total_saved_bytes=total_saved)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Stillpoint Home Base retention janitor")
    parser.add_argument(
        "--force",
        action="store_true",
        default=None,
        help="Run immediately and bypass the interval gate for this invocation.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=None,
        help="Log what would be deleted without removing any files.",
    )
    args = parser.parse_args()

    force = bool(args.force) if args.force is not None else _env_bool("SP_HOMEBASE_GC_FORCE", False)
    dry_run = bool(args.dry_run) if args.dry_run is not None else None
    return run_homebase_gc(force=force, dry_run=dry_run)


if __name__ == "__main__":
    raise SystemExit(main())