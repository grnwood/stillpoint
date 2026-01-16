from __future__ import annotations

import os
import time
from typing import Optional


PAGE_LOGGING_ENABLED = os.getenv("ZIMX_DETAILED_PAGE_LOGGING", "0") not in (
    "0",
    "false",
    "False",
    "",
    None,
)


class PageLoadLogger:
    """Lightweight timing helper for page load + render steps."""

    def __init__(self, path: str) -> None:
        self.path = path
        now = time.perf_counter()
        self._start = now
        self._last = now
        self.enabled = PAGE_LOGGING_ENABLED
        if self.enabled:
            print(f"[PageLoadAndRender] start path={path}")

    def mark(self, label: str) -> None:
        if not self.enabled:
            return
        now = time.perf_counter()
        step_ms = (now - self._last) * 1000.0
        total_ms = (now - self._start) * 1000.0
        print(
            f"[PageLoadAndRender] {label} +{step_ms:.1f}ms total={total_ms:.1f}ms path={self.path}"
        )
        self._last = now

    def end(self, label: str = "ready") -> None:
        self.mark(label)

    def attach_if(self, condition: bool) -> Optional["PageLoadLogger"]:
        """Return self when condition is true, else None (keeps call sites tidy)."""
        return self if condition else None
