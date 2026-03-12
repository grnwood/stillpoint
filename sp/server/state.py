from __future__ import annotations

from dataclasses import dataclass
from contextvars import ContextVar
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
        self._session_roots: dict[str, Path] = {}
        self._context_root: ContextVar[Optional[Path]] = ContextVar("sp_server_vault_root", default=None)

    def set_root(self, path: str) -> Path:
        root_path = Path(path).expanduser().resolve()
        if not root_path.exists() or not root_path.is_dir():
            raise ValueError(f"Vault directory does not exist: {root_path}")
        with self._lock:
            self._state.root = root_path
        return root_path

    def bind_session_root(self, session_id: str, path: str) -> Path:
        root_path = Path(path).expanduser().resolve()
        if not root_path.exists() or not root_path.is_dir():
            raise ValueError(f"Vault directory does not exist: {root_path}")
        session_key = str(session_id or "").strip()
        if not session_key:
            raise ValueError("Missing session id")
        with self._lock:
            self._session_roots[session_key] = root_path
        return root_path

    def get_session_root(self, session_id: str) -> Optional[Path]:
        session_key = str(session_id or "").strip()
        if not session_key:
            return None
        with self._lock:
            return self._session_roots.get(session_key)

    def push_context_root(self, root: Optional[Path]):
        normalized = root.expanduser().resolve() if isinstance(root, Path) else None
        return self._context_root.set(normalized)

    def reset_context_root(self, token) -> None:
        try:
            self._context_root.reset(token)
        except Exception:
            pass

    def get_root(self) -> Path:
        current = self._context_root.get()
        if current is not None:
            return current
        with self._lock:
            if self._state.root is None:
                raise RuntimeError("Vault root is not set. Call /api/vault/select first.")
            return self._state.root


vault_state = StateManager()
