from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Optional


@dataclass
class VaultState:
    root: Optional[Path] = None


class StateManager:
    def __init__(self) -> None:
        self._state = VaultState()
        self._lock = RLock()

    def set_root(self, path: str) -> Path:
        root_path = Path(path).expanduser().resolve()
        if not root_path.exists() or not root_path.is_dir():
            raise ValueError(f"Vault directory does not exist: {root_path}")
        with self._lock:
            self._state.root = root_path
        return root_path

    def get_root(self) -> Path:
        with self._lock:
            if self._state.root is None:
                raise RuntimeError("Vault root is not set. Call /api/vault/select first.")
            return self._state.root


vault_state = StateManager()
