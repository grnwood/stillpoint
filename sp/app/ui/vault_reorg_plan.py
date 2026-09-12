from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass(frozen=True)
class _HistoryEntry:
    label: str
    before: list[dict]
    after: list[dict]
    size_bytes: int


class StagingPlanHistory:
    """Bounded undo/redo history for an uncommitted reorganization plan."""

    def __init__(self, *, max_commands: int = 100, max_bytes: int = 5 * 1024 * 1024) -> None:
        self.plan: list[dict] = []
        self.max_commands = max(1, int(max_commands))
        self.max_bytes = max(1024, int(max_bytes))
        self._undo: list[_HistoryEntry] = []
        self._redo: list[_HistoryEntry] = []
        self._undo_bytes = 0

    @staticmethod
    def _clone(plan: list[dict]) -> list[dict]:
        return copy.deepcopy(plan)

    @staticmethod
    def _entry_size(label: str, before: list[dict], after: list[dict]) -> int:
        try:
            return len(
                json.dumps(
                    {"label": label, "before": before, "after": after},
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode("utf-8")
            )
        except (TypeError, ValueError):
            return len(repr((label, before, after)).encode("utf-8"))

    def perform(self, label: str, mutation: Callable[[list[dict]], None]) -> bool:
        before = self._clone(self.plan)
        try:
            mutation(self.plan)
        except Exception:
            # A compound staging gesture is all-or-nothing. Restore in place
            # so every widget holding the shared plan list stays synchronized.
            self.plan[:] = before
            raise
        if self.plan == before:
            return False
        after = self._clone(self.plan)
        entry = _HistoryEntry(
            label=(label or "Change staging plan").strip(),
            before=before,
            after=after,
            size_bytes=self._entry_size(label, before, after),
        )
        self._undo.append(entry)
        self._undo_bytes += entry.size_bytes
        self._redo.clear()
        self._trim()
        return True

    def _trim(self) -> None:
        # Always preserve the newest complete command, even if that command is
        # larger than the normal memory target.
        while len(self._undo) > 1 and (
            len(self._undo) > self.max_commands or self._undo_bytes > self.max_bytes
        ):
            removed = self._undo.pop(0)
            self._undo_bytes = max(0, self._undo_bytes - removed.size_bytes)

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    @property
    def undo_label(self) -> Optional[str]:
        return self._undo[-1].label if self._undo else None

    @property
    def redo_label(self) -> Optional[str]:
        return self._redo[-1].label if self._redo else None

    @property
    def command_count(self) -> int:
        return len(self._undo) + len(self._redo)

    @property
    def size_bytes(self) -> int:
        return self._undo_bytes + sum(entry.size_bytes for entry in self._redo)

    def undo(self) -> Optional[str]:
        if not self._undo:
            return None
        entry = self._undo.pop()
        self._undo_bytes = max(0, self._undo_bytes - entry.size_bytes)
        self.plan[:] = self._clone(entry.before)
        self._redo.append(entry)
        return entry.label

    def redo(self) -> Optional[str]:
        if not self._redo:
            return None
        entry = self._redo.pop()
        self.plan[:] = self._clone(entry.after)
        self._undo.append(entry)
        self._undo_bytes += entry.size_bytes
        self._trim()
        return entry.label

    def reset(self) -> None:
        self.plan.clear()
        self._undo.clear()
        self._redo.clear()
        self._undo_bytes = 0

    def clear_history(self) -> None:
        self._undo.clear()
        self._redo.clear()
        self._undo_bytes = 0
