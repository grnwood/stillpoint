from __future__ import annotations

import os
import socket
import sys
import time
from collections import Counter
from functools import wraps
from traceback import extract_stack
from typing import Iterable

from sp.logging_flags import log_enabled


def enabled() -> bool:
    return log_enabled("event_loop")


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def log(message: str) -> None:
    if not enabled():
        return
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[EventLoopDiag {timestamp}] {message}", file=sys.stderr, flush=True)


def configured_fd(default: int = 11) -> int:
    return env_int("SP_EVENT_LOOP_FD", default)


def describe_fd(fd: int) -> str:
    fd_path = f"/proc/{os.getpid()}/fd/{fd}"
    try:
        target = os.readlink(fd_path)
    except FileNotFoundError:
        return "missing"
    except OSError as exc:
        return f"unavailable:{type(exc).__name__}:{exc}"

    if target.startswith("socket:"):
        details = _socket_details(fd)
        if details:
            return f"{target} {details}"
    return target


def log_fd_target(label: str, fd: int | None = None) -> None:
    if not enabled():
        return
    actual_fd = configured_fd() if fd is None else fd
    log(f"{label}: pid={os.getpid()} fd={actual_fd} target={describe_fd(actual_fd)}")
    if os.getenv("SP_EVENT_LOOP_DUMP_FDS", "0").strip().lower() in {"1", "true", "yes", "on"}:
        log(f"{label}: fd_snapshot={'; '.join(iter_fd_targets(limit=64))}")


def install_qt_event_sampler(app) -> None:
    if not enabled() or not env_bool("SP_EVENT_LOOP_SAMPLE_EVENTS"):
        return
    try:
        sampler = getattr(app, "_stillpoint_event_loop_sampler", None)
    except Exception:
        sampler = None
    if sampler is not None:
        return
    try:
        sampler = _QtEventSampler(app)
        app.installEventFilter(sampler.event_filter)
        app._stillpoint_event_loop_sampler = sampler
        sampler.start()
    except Exception as exc:
        log(f"failed to install Qt event sampler: {exc!r}")


def create_application(argv):
    """Create QApplication, using a notify-counting subclass when diagnostics are on."""
    from PySide6.QtWidgets import QApplication

    if not enabled():
        return QApplication(argv)

    class DiagnosticApplication(QApplication):
        def __init__(self, args) -> None:
            super().__init__(args)
            self._stillpoint_notify_diag = _NotifyCounter(self)
            self._stillpoint_notify_diag.start()

        def notify(self, receiver, event):  # type: ignore[override]
            try:
                self._stillpoint_notify_diag.record(receiver, event)
            except Exception:
                pass
            return super().notify(receiver, event)

    app = DiagnosticApplication(argv)
    log("QApplication notify counter installed")
    return app


def install_qtimer_probe() -> None:
    """Log QTimer creation/start intervals for event-loop wakeup diagnostics."""
    if not enabled() or not env_bool("SP_EVENT_LOOP_LOG_TIMERS", True):
        return
    try:
        from PySide6.QtCore import QTimer
    except Exception as exc:
        log(f"failed to import QTimer for probe: {exc!r}")
        return
    if getattr(QTimer, "_stillpoint_timer_probe_installed", False):
        return

    original_init = QTimer.__init__
    original_set_interval = QTimer.setInterval
    original_start = QTimer.start
    original_single_shot = QTimer.singleShot

    def _timer_label(timer) -> str:
        try:
            parent = timer.parent()
        except Exception:
            parent = None
        parent_label = _target_name(parent) if parent is not None else "<none>"
        return f"{type(timer).__name__}@{id(timer):x} parent={parent_label}"

    def _interval(timer) -> int | str:
        try:
            return int(timer.interval())
        except Exception:
            return "?"

    def _single_shot(timer) -> bool | str:
        try:
            return bool(timer.isSingleShot())
        except Exception:
            return "?"

    def __init__(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        log(
            "QTimer created "
            f"{_timer_label(self)} interval_ms={_interval(self)} "
            f"single_shot={_single_shot(self)} caller={_caller()}"
        )

    def setInterval(self, msec):  # noqa: N802 - Qt API casing
        result = original_set_interval(self, msec)
        log(
            "QTimer setInterval "
            f"{_timer_label(self)} interval_ms={_interval(self)} "
            f"single_shot={_single_shot(self)} caller={_caller()}"
        )
        return result

    def start(self, *args):
        requested = args[0] if args else _interval(self)
        result = original_start(self, *args)
        interval = _interval(self)
        log(
            "QTimer start "
            f"{_timer_label(self)} requested_ms={requested} interval_ms={interval} "
            f"single_shot={_single_shot(self)} caller={_caller()}"
        )
        return result

    def singleShot(*args, **kwargs):  # noqa: N802 - Qt API casing
        delay = args[0] if args else kwargs.get("msec", "?")
        log(f"QTimer.singleShot delay_ms={delay} caller={_caller()}")
        return original_single_shot(*args, **kwargs)

    try:
        QTimer.__init__ = __init__
        QTimer.setInterval = setInterval
        QTimer.start = start
        QTimer.singleShot = staticmethod(singleShot)
        QTimer._stillpoint_timer_probe_installed = True
        log("QTimer interval probe installed")
    except Exception as exc:
        log(f"failed to install QTimer interval probe: {exc!r}")


def install_ui_method_probe() -> None:
    """Count high-frequency widget mutations that can repeatedly post Qt work."""
    if not enabled() or not env_bool("SP_EVENT_LOOP_PROBE_UI_METHODS", True):
        return
    try:
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QAbstractButton, QLabel, QWidget
    except Exception as exc:
        log(f"failed to import widgets for UI method probe: {exc!r}")
        return
    if getattr(QWidget, "_stillpoint_ui_method_probe_installed", False):
        return

    counter = _UiMethodCounter()

    def _wrap(cls, method_name: str) -> None:
        original = getattr(cls, method_name, None)
        if original is None or getattr(original, "_stillpoint_wrapped", False):
            return

        @wraps(original)
        def wrapper(self, *args, **kwargs):
            counter.record(f"{cls.__name__}.{method_name}", self)
            return original(self, *args, **kwargs)

        wrapper._stillpoint_wrapped = True
        setattr(cls, method_name, wrapper)

    for name in ("update", "repaint", "resize", "show", "hide", "setVisible", "setStyleSheet"):
        _wrap(QWidget, name)
    for name in ("setText",):
        _wrap(QLabel, name)
        _wrap(QAbstractButton, name)
    for name in ("setIcon",):
        _wrap(QAbstractButton, name)

    timer = QTimer()
    timer.setInterval(max(250, env_int("SP_EVENT_LOOP_UI_METHOD_INTERVAL_MS", 1000)))
    timer.timeout.connect(counter.flush)
    timer.start()
    QWidget._stillpoint_ui_method_probe_installed = True
    QWidget._stillpoint_ui_method_probe_timer = timer
    log("UI mutation method probe installed")


def iter_fd_targets(limit: int = 64) -> Iterable[str]:
    fd_dir = f"/proc/{os.getpid()}/fd"
    try:
        names = sorted(os.listdir(fd_dir), key=lambda value: int(value) if value.isdigit() else 10**9)
    except OSError:
        return []
    out: list[str] = []
    for name in names[:limit]:
        if not name.isdigit():
            continue
        fd = int(name)
        out.append(f"{fd}->{describe_fd(fd)}")
    return out


def _socket_details(fd: int) -> str:
    try:
        dup_fd = os.dup(fd)
    except OSError:
        return ""
    sock = socket.socket(fileno=dup_fd)
    try:
        parts: list[str] = []
        try:
            parts.append(f"local={sock.getsockname()!r}")
        except OSError:
            pass
        try:
            parts.append(f"peer={sock.getpeername()!r}")
        except OSError:
            pass
        return " ".join(parts)
    finally:
        sock.close()


class _QtEventSampler:
    def __init__(self, app) -> None:
        from PySide6.QtCore import QObject, QTimer

        class _Filter(QObject):
            def __init__(self, owner: "_QtEventSampler") -> None:
                super().__init__(app)
                self._owner = owner

            def eventFilter(self, obj, event):  # type: ignore[override]
                self._owner.record(obj, event)
                return False

        self._app = app
        self._filter = _Filter(self)
        self._timer = QTimer(self._filter)
        self._timer.setInterval(max(250, env_int("SP_EVENT_LOOP_SAMPLE_INTERVAL_MS", 1000)))
        self._timer.timeout.connect(self.flush)
        self._started_at = time.monotonic()
        self._event_types: Counter[int] = Counter()
        self._targets: Counter[str] = Counter()
        self._timer_targets: Counter[str] = Counter()

    @property
    def event_filter(self):
        return self._filter

    def start(self) -> None:
        self._timer.start()
        log("Qt event sampler installed")

    def record(self, obj, event) -> None:
        try:
            event_type = int(event.type())
        except Exception:
            event_type = -1
        self._event_types[event_type] += 1
        target = self._target_name(obj)
        self._targets[target] += 1
        if event_type == 1:
            self._timer_targets[target] += 1

    def flush(self) -> None:
        now = time.monotonic()
        elapsed = max(now - self._started_at, 0.001)
        total = sum(self._event_types.values())
        rate = total / elapsed
        type_summary = ", ".join(
            f"{self._event_type_name(event_type)}={count}"
            for event_type, count in self._event_types.most_common(8)
        )
        target_summary = ", ".join(
            f"{name}={count}"
            for name, count in self._targets.most_common(8)
        )
        timer_summary = ", ".join(
            f"{name}={count}"
            for name, count in self._timer_targets.most_common(5)
        )
        log(
            "QApplication eventFilter sample "
            f"events={total} rate={rate:.1f}/s "
            f"types=[{type_summary or 'none'}] "
            f"targets=[{target_summary or 'none'}] "
            f"timer_targets=[{timer_summary or 'none'}]"
        )
        self._event_types.clear()
        self._targets.clear()
        self._timer_targets.clear()
        self._started_at = now

    @staticmethod
    def _event_type_name(event_type: int) -> str:
        try:
            from PySide6.QtCore import QEvent

            return QEvent.Type(event_type).name
        except Exception:
            return str(event_type)

    @staticmethod
    def _target_name(obj) -> str:
        return _target_name(obj)


class _NotifyCounter:
    def __init__(self, app) -> None:
        from PySide6.QtCore import QTimer

        self._started_at = time.monotonic()
        self._event_types: Counter[int] = Counter()
        self._targets: Counter[str] = Counter()
        self._timer = QTimer(app)
        self._timer.setInterval(max(250, env_int("SP_EVENT_LOOP_NOTIFY_INTERVAL_MS", 1000)))
        self._timer.timeout.connect(self.flush)

    def start(self) -> None:
        self._timer.start()

    def record(self, receiver, event) -> None:
        try:
            event_type = int(event.type())
        except Exception:
            event_type = -1
        self._event_types[event_type] += 1
        self._targets[_target_name(receiver)] += 1

    def flush(self) -> None:
        now = time.monotonic()
        elapsed = max(now - self._started_at, 0.001)
        total = sum(self._event_types.values())
        type_summary = ", ".join(
            f"{_event_type_name(event_type)}={count}"
            for event_type, count in self._event_types.most_common(10)
        )
        target_summary = ", ".join(
            f"{name}={count}"
            for name, count in self._targets.most_common(10)
        )
        log(
            "QApplication.notify sample "
            f"events={total} rate={total / elapsed:.1f}/s "
            f"types=[{type_summary or 'none'}] "
            f"targets=[{target_summary or 'none'}]"
        )
        self._event_types.clear()
        self._targets.clear()
        self._started_at = now


class _UiMethodCounter:
    def __init__(self) -> None:
        self._started_at = time.monotonic()
        self._methods: Counter[str] = Counter()
        self._targets: Counter[str] = Counter()

    def record(self, method: str, obj) -> None:
        self._methods[method] += 1
        self._targets[f"{method}:{_target_name(obj)}"] += 1

    def flush(self) -> None:
        now = time.monotonic()
        elapsed = max(now - self._started_at, 0.001)
        total = sum(self._methods.values())
        if total:
            method_summary = ", ".join(f"{name}={count}" for name, count in self._methods.most_common(10))
            target_summary = ", ".join(f"{name}={count}" for name, count in self._targets.most_common(10))
            log(
                "UI mutation sample "
                f"calls={total} rate={total / elapsed:.1f}/s "
                f"methods=[{method_summary}] targets=[{target_summary}]"
            )
        self._methods.clear()
        self._targets.clear()
        self._started_at = now


def _target_name(obj) -> str:
    if obj is None:
        return "<none>"
    class_name = type(obj).__name__
    try:
        meta = obj.metaObject()
        if meta is not None:
            class_name = meta.className()
    except Exception:
        pass
    try:
        object_name = obj.objectName()
    except Exception:
        object_name = ""
    if object_name:
        return f"{class_name}#{object_name}"
    return class_name


def _event_type_name(event_type: int) -> str:
    try:
        from PySide6.QtCore import QEvent

        return QEvent.Type(event_type).name
    except Exception:
        return str(event_type)


def _caller() -> str:
    try:
        frames = extract_stack(limit=8)
        for frame in reversed(frames[:-1]):
            filename = frame.filename.replace(os.getcwd() + os.sep, "")
            if filename.endswith("eventloop_diag.py"):
                continue
            if "PySide6" in filename:
                continue
            return f"{filename}:{frame.lineno}:{frame.name}"
    except Exception:
        pass
    return "<unknown>"
