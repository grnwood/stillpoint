"""Vault-root validation and nested-vault boundary helpers.

A StillPoint vault must never be opened from a directory that contains another
vault.  Treating such a container as one vault makes recursive page and sync
scans merge otherwise independent data sets.
"""

from __future__ import annotations

import os
from pathlib import Path


METADATA_DIR = ".stillpoint"


class NestedVaultError(ValueError):
    """Raised when a proposed vault root contains one or more child vaults."""

    def __init__(self, root: Path, nested_roots: list[Path]) -> None:
        self.root = root
        self.nested_roots = nested_roots
        shown = ", ".join(str(path) for path in nested_roots[:5])
        if len(nested_roots) > 5:
            shown += f", and {len(nested_roots) - 5} more"
        super().__init__(
            f"The selected folder contains separate StillPoint vaults: {shown}. "
            "Choose one exact vault folder instead of their parent folder."
        )


def find_nested_vault_roots(root: str | Path, *, limit: int = 20) -> list[Path]:
    """Return child directories containing a ``.stillpoint`` vault marker.

    The selected root's own metadata directory is expected and ignored.  Once a
    nested vault is found its contents are pruned, both for speed and to avoid
    reporting grandchildren that belong to the same independent vault.
    """
    root_path = Path(root).expanduser().resolve()
    if not root_path.is_dir():
        return []

    found: list[Path] = []
    for current_text, dirnames, _filenames in os.walk(root_path, followlinks=False):
        current = Path(current_text)
        dirnames.sort(key=str.casefold)
        # Never inspect metadata contents.  A marker below the selected root
        # identifies the directory containing it as a separate vault.
        has_marker = METADATA_DIR in dirnames
        dirnames[:] = [
            name
            for name in dirnames
            if name != METADATA_DIR and not (current / name).is_symlink()
        ]
        if current != root_path and has_marker:
            found.append(current)
            dirnames[:] = []
            if len(found) >= max(1, int(limit)):
                break
    return sorted(found, key=lambda path: str(path).casefold())


def validate_vault_root(root: str | Path) -> Path:
    """Resolve *root* and reject a container that crosses vault boundaries."""
    root_path = Path(root).expanduser().resolve()
    nested = find_nested_vault_roots(root_path)
    if nested:
        raise NestedVaultError(root_path, nested)
    return root_path


def is_nested_vault_root(root: str | Path, candidate: str | Path) -> bool:
    """Return whether *candidate* is a child vault root beneath *root*."""
    root_path = Path(root).expanduser().resolve()
    candidate_path = Path(candidate).expanduser().resolve()
    return candidate_path != root_path and (candidate_path / METADATA_DIR).is_dir()


def path_crosses_nested_vault(root: str | Path, path: str | Path) -> bool:
    """Return whether *path* lies inside a child vault beneath *root*."""
    root_path = Path(root).expanduser().resolve()
    candidate = Path(path).expanduser().resolve()
    if candidate != root_path and root_path not in candidate.parents:
        return False
    current = candidate
    while current != root_path:
        try:
            if (current / METADATA_DIR).is_dir():
                return True
        except OSError:
            # Invalid or unrepresentable leaf names are handled by the caller;
            # their ancestors still need to be checked for a vault boundary.
            pass
        current = current.parent
    return False
