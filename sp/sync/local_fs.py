from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from typing import Iterator, Optional


EXCLUDE_DIRS = {".stillpoint"}
EXCLUDE_FILES = {"AGENTS.md"}


def iter_files(vault_root: Path) -> Iterator[tuple[str, Path]]:
    for root, dirs, files in os.walk(vault_root):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for name in files:
            if name in EXCLUDE_FILES:
                continue
            full = Path(root) / name
            rel = full.relative_to(vault_root).as_posix()
            if rel.startswith(".stillpoint/"):
                continue
            yield rel, full


def stat_file(full_path: Path) -> tuple[int, int]:
    st = full_path.stat()
    return int(st.st_size), int(st.st_mtime)


def read_bytes(full_path: Path) -> bytes:
    return full_path.read_bytes()


def write_bytes_atomic(full_path: Path, data: bytes) -> None:
    full_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = full_path.with_suffix(f"{full_path.suffix}.tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, full_path)


def bytes_equal(a: bytes, b: bytes) -> bool:
    return a == b


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def conflict_copy_path(rel_path: str, device_id: str, ts: Optional[int] = None) -> str:
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime(ts or int(time.time())))
    path = Path(rel_path)
    return f"{path.with_suffix('').as_posix()}.sync-conflict-{stamp}-{device_id}{path.suffix}"
