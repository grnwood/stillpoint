from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional, TextIO

from sp.logging_flags import log_enabled


PAGE_LOGGING_ENABLED = log_enabled("performance")


class PageLoadLogger:
    """Low-overhead, best-effort timing trace for the complete page-open path.

    Profiling must never make the editor less safe.  Consequently all output is
    performed after a measurement is recorded and failures are swallowed.  Set
    ``SP_LOG_PERFORMANCE=1`` to emit JSON lines to stderr, or additionally set
    ``SP_PAGE_PROFILE_PATH`` to append them to a file for later comparison.
    """

    def __init__(
        self,
        path: str,
        *,
        enabled: Optional[bool] = None,
        stream: Optional[TextIO] = None,
    ) -> None:
        self.path = path
        now = time.perf_counter()
        self._start = now
        self._last = now
        self._start_cpu = time.process_time()
        self._events: list[dict[str, Any]] = []
        self.enabled = PAGE_LOGGING_ENABLED if enabled is None else enabled
        self._stream = stream
        if self.enabled:
            self._record("start", now, step_ms=0.0)

    def _destination(self) -> Optional[TextIO]:
        if self._stream is not None:
            return self._stream
        profile_path = os.getenv("SP_PAGE_PROFILE_PATH", "").strip()
        if profile_path:
            try:
                target = Path(profile_path).expanduser()
                target.parent.mkdir(parents=True, exist_ok=True)
                return target.open("a", encoding="utf-8")
            except (OSError, ValueError):
                return sys.stderr
        return sys.stderr

    def _emit(self, payload: dict[str, Any]) -> None:
        destination: Optional[TextIO] = None
        should_close = False
        try:
            destination = self._destination()
            if destination is None:
                return
            should_close = destination not in (self._stream, sys.stderr, sys.stdout)
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True), file=destination, flush=True)
        except (OSError, TypeError, ValueError):
            # Diagnostics are deliberately non-fatal: navigation must not depend
            # on a writable log path or a healthy output stream.
            return
        finally:
            if should_close and destination is not None:
                try:
                    destination.close()
                except OSError:
                    pass

    def _record(self, label: str, now: float, *, step_ms: Optional[float] = None) -> None:
        if step_ms is None:
            step_ms = (now - self._last) * 1000.0
        event = {
            "type": "page_load_step",
            "label": label,
            "step_ms": round(step_ms, 3),
            "total_ms": round((now - self._start) * 1000.0, 3),
            "path": self.path,
        }
        self._events.append(event)
        self._emit(event)
        self._last = now

    def mark(self, label: str) -> None:
        if not self.enabled:
            return
        self._record(label, time.perf_counter())

    def end(self, label: str = "ready") -> None:
        if not self.enabled:
            return
        self.mark(label)
        elapsed_ms = (time.perf_counter() - self._start) * 1000.0
        cpu_ms = (time.process_time() - self._start_cpu) * 1000.0
        slowest = sorted(self._events[1:], key=lambda event: event["step_ms"], reverse=True)[:5]
        self._emit(
            {
                "type": "page_load_summary",
                "path": self.path,
                "elapsed_ms": round(elapsed_ms, 3),
                "cpu_ms": round(cpu_ms, 3),
                "unattributed_wait_ms": round(max(0.0, elapsed_ms - cpu_ms), 3),
                "steps": len(self._events),
                "slowest": [
                    {"label": event["label"], "step_ms": event["step_ms"]}
                    for event in slowest
                ],
            }
        )

    def attach_if(self, condition: bool) -> Optional["PageLoadLogger"]:
        """Return self when condition is true, else None (keeps call sites tidy)."""
        return self if condition else None
