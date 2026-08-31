"""Helpers for locating resources in source and frozen application layouts."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def resource_candidates(rel_path: str) -> list[str]:
    """Return likely absolute paths for a bundled or source-tree resource."""
    relative = Path(rel_path)
    candidates: list[Path] = []
    base = getattr(sys, "_MEIPASS", None)
    if base:
        base_path = Path(base)
        candidates.extend((base_path / relative, base_path / "sp" / relative))
        candidates.extend((base_path / "_internal" / relative, base_path / "_internal" / "sp" / relative))
    try:
        executable_dir = Path(sys.argv[0]).resolve().parent
        candidates.extend((executable_dir / relative, executable_dir / "sp" / relative))
        candidates.extend((executable_dir / "_internal" / relative, executable_dir / "_internal" / "sp" / relative))
    except Exception:
        pass
    package_root = Path(__file__).resolve().parents[1]
    candidates.extend((package_root / relative, package_root.parent / relative))
    return [os.fspath(path) for path in candidates]