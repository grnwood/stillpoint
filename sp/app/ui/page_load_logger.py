from __future__ import annotations

import json
import os
import sys
import time
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Optional, TextIO, TypeVar

from sp.logging_flags import log_enabled


PAGE_LOGGING_ENABLED = log_enabled("performance")
PERFORMANCE_LOGGING_ENABLED = PAGE_LOGGING_ENABLED
_F = TypeVar("_F", bound=Callable[..., Any])


def _destination(stream: Optional[TextIO] = None) -> Optional[TextIO]:
    if stream is not None:
        return stream
    profile_path = (
        os.getenv("SP_PERFORMANCE_PROFILE_PATH", "").strip()
        or os.getenv("SP_PAGE_PROFILE_PATH", "").strip()
    )
    if profile_path:
        try:
            target = Path(profile_path).expanduser()
            target.parent.mkdir(parents=True, exist_ok=True)
            return target.open("a", encoding="utf-8")
        except (OSError, ValueError):
            return sys.stderr
    return sys.stderr


def _emit_payload(payload: dict[str, Any], stream: Optional[TextIO] = None) -> None:
    destination: Optional[TextIO] = None
    should_close = False
    try:
        destination = _destination(stream)
        if destination is None:
            return
        should_close = destination not in (stream, sys.stderr, sys.stdout)
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True), file=destination, flush=True)
    except (OSError, TypeError, ValueError):
        return
    finally:
        if should_close and destination is not None:
            try:
                destination.close()
            except OSError:
                pass


def performance_start() -> Optional[float]:
    """Return a span start timestamp only when performance logging is enabled."""
    return time.perf_counter() if PERFORMANCE_LOGGING_ENABLED else None


def emit_performance_span(
    name: str,
    started_at: Optional[float],
    *,
    path: Optional[str] = None,
    fields: Optional[dict[str, Any]] = None,
    enabled: Optional[bool] = None,
    stream: Optional[TextIO] = None,
    ended_at: Optional[float] = None,
) -> None:
    """Emit one best-effort duration record without affecting application flow."""
    active = PERFORMANCE_LOGGING_ENABLED if enabled is None else enabled
    if not active or started_at is None:
        return
    end = time.perf_counter() if ended_at is None else ended_at
    payload: dict[str, Any] = {
        "type": "performance_span",
        "name": name,
        "duration_ms": round(max(0.0, end - started_at) * 1000.0, 3),
    }
    if path:
        payload["path"] = path
    if fields:
        payload.update(fields)
    _emit_payload(payload, stream)


def measure_performance(name: str) -> Callable[[_F], _F]:
    """Decorate a synchronous UI operation with opt-in duration logging."""
    def decorator(func: _F) -> _F:
        @wraps(func)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            if not PERFORMANCE_LOGGING_ENABLED:
                return func(*args, **kwargs)
            started_at = time.perf_counter()
            try:
                return func(*args, **kwargs)
            finally:
                try:
                    owner = args[0] if args else None
                    path = None
                    for attr in ("current_page", "current_path", "_current_path", "_source_path"):
                        candidate = getattr(owner, attr, None)
                        if candidate:
                            path = str(candidate)
                            break
                    fields: dict[str, Any] = {"operation": func.__qualname__}
                    try:
                        fields["document_blocks"] = int(owner.document().blockCount())
                    except (AttributeError, RuntimeError, TypeError):
                        pass
                    for attr, label in (
                        ("_visible_tasks", "visible_tasks"),
                        ("tag_chicklets", "tags"),
                    ):
                        collection = getattr(owner, attr, None)
                        if collection is not None:
                            fields[label] = len(collection)
                    results_tree = getattr(owner, "results_tree", None)
                    if results_tree is not None:
                        fields["result_rows"] = int(results_tree.topLevelItemCount())
                    emit_performance_span(name, started_at, path=path, fields=fields)
                except Exception:
                    # Instrumentation must never change operation behavior,
                    # including while its owner widget is being destroyed.
                    pass

        return wrapped  # type: ignore[return-value]

    return decorator


class PageLoadLogger:
    """Low-overhead, best-effort timing trace for the complete page-open path.

    Profiling must never make the editor less safe.  Consequently all output is
    performed after a measurement is recorded and failures are swallowed.  Set
    ``SP_LOG_PERFORMANCE=1`` to emit JSON lines to stderr, or additionally set
    ``SP_PERFORMANCE_PROFILE_PATH`` (or the legacy ``SP_PAGE_PROFILE_PATH``)
    to append them to a file for later comparison.
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
        self._ended = False
        self._completed_phases: set[str] = set()
        if self.enabled:
            self._record("start", now, step_ms=0.0)

    def _destination(self) -> Optional[TextIO]:
        return _destination(self._stream)

    def _emit(self, payload: dict[str, Any]) -> None:
        # Diagnostics are deliberately non-fatal: navigation must not depend on
        # a writable log path or a healthy output stream.
        _emit_payload(payload, self._stream)

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
        if not self.enabled or self._ended:
            return
        self._record(label, time.perf_counter())

    def end(self, label: str = "ready") -> None:
        if not self.enabled or self._ended:
            return
        self.mark(label)
        self._ended = True
        self._emit_summary()

    def complete_phase(
        self,
        phase: str,
        label: str,
        *,
        required_phases: frozenset[str] = frozenset(("secondary", "images")),
    ) -> None:
        """Mark an asynchronous load phase and summarize once all phases finish."""
        if not self.enabled or self._ended or phase in self._completed_phases:
            return
        self.mark(label)
        self._completed_phases.add(phase)
        if required_phases.issubset(self._completed_phases):
            self._ended = True
            self._emit_summary()

    def _emit_summary(self) -> None:
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
