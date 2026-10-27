"""Durable crash-recovery snapshots for the standalone diagram editors."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile

from PySide6.QtCore import QStandardPaths


@dataclass(frozen=True)
class DiagramRecoverySnapshot:
    target: Path
    content: str
    base_hash: str
    updated_at: str

    def base_matches(self, content: str) -> bool:
        return self.base_hash == _content_hash(content)


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def recovery_directory() -> Path:
    override = os.environ.get("STILLPOINT_DIAGRAM_RECOVERY_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    app_data = QStandardPaths.writableLocation(QStandardPaths.GenericDataLocation)
    root = Path(app_data) / "StillPoint" if app_data else Path.home() / ".stillpoint"
    return root / "diagram-recovery"


def recovery_path(target: Path | str) -> Path:
    resolved = str(Path(target).expanduser().resolve())
    key = hashlib.sha256(resolved.encode("utf-8")).hexdigest()
    return recovery_directory() / f"{key}.json"


def write_recovery(target: Path | str, content: str, base_content: str) -> Path:
    target_path = Path(target).expanduser().resolve()
    destination = recovery_path(target_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "target": str(target_path),
        "content": content,
        "base_hash": _content_hash(base_content),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.stem}-", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            json.dump(payload, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return destination


def load_recovery(target: Path | str) -> DiagramRecoverySnapshot | None:
    target_path = Path(target).expanduser().resolve()
    source = recovery_path(target_path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if (
        not isinstance(payload, dict)
        or payload.get("version") != 1
        or payload.get("target") != str(target_path)
        or not isinstance(payload.get("content"), str)
    ):
        return None
    return DiagramRecoverySnapshot(
        target=target_path,
        content=payload["content"],
        base_hash=str(payload.get("base_hash") or ""),
        updated_at=str(payload.get("updated_at") or ""),
    )


def remove_recovery(target: Path | str) -> None:
    try:
        recovery_path(target).unlink(missing_ok=True)
    except OSError:
        pass
