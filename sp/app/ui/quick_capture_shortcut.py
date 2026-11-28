"""Own the Quick Capture shortcut across StillPoint windows and processes."""
from __future__ import annotations

import os
import sys

from PySide6.QtCore import QLockFile, QObject, QTimer, Qt
from PySide6.QtGui import QKeySequence

from sp.app import config

from .recording_hotkeys import RecordingHotkeys


def quick_capture_hotkey_error(keys: QKeySequence) -> str:
    if keys.isEmpty():
        return ""
    modifiers = keys[0].keyboardModifiers()
    key = keys[0].key()
    if keys.count() != 1 or not modifiers & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier):
        return "Choose one shortcut using Ctrl, Alt, or the system modifier."
    if modifiers == Qt.AltModifier and Qt.Key_A <= key <= Qt.Key_Z:
        return "Alt+letter shortcuts are reserved for menu access."
    if not (
        Qt.Key_A <= key <= Qt.Key_Z or Qt.Key_0 <= key <= Qt.Key_9 or Qt.Key_F1 <= key <= Qt.Key_F12
        or key in (Qt.Key_Space, Qt.Key_Tab, Qt.Key_Return, Qt.Key_Enter)
    ):
        return "Choose a letter, number, function key (F1–F12), Space, Tab, or Enter."
    return ""


class QuickCaptureShortcut(QObject):
    def __init__(self, app):
        super().__init__(app)
        self._app = app
        self._lock = QLockFile(str(config.GLOBAL_CONFIG.with_name(".stillpoint-quick-capture.lock")))
        self._lock.setStaleLockTime(0)
        self._sequence = None
        self._hotkeys = None
        self._timer = QTimer(self)
        self._timer.setInterval(2000)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        app.aboutToQuit.connect(self.close)

    def _window(self):
        windows = self._app._stillpoint_windows
        active = self._app.activeWindow()
        if active in windows:
            return active
        owner = getattr(self._app, "_stillpoint_tray_owner", None)
        return owner if owner in windows else next(iter(windows), None)

    def refresh(self):
        sequence = config.load_quick_capture_app_hotkey()
        if not sequence:
            self.close()
            return
        if not self._lock.isLocked() and not self._lock.tryLock(0):
            return
        if sequence == self._sequence:
            return
        self._sequence = sequence
        self._stop_registration()

        keys = QKeySequence.fromString(sequence, QKeySequence.PortableText)
        error = quick_capture_hotkey_error(keys)
        if keys.isEmpty() or error:
            self._status(f"Global Quick Capture shortcut unavailable: {error or 'invalid key sequence'}")
            return
        native = keys.toString(QKeySequence.PortableText)
        if sys.platform == "darwin":
            # Qt's primary modifier is Command. Carbon labels the physical keys.
            parts = native.split("+")
            native = "+".join({"Ctrl": "Meta", "Meta": "Ctrl"}.get(part, part) for part in parts)

        self._hotkeys = RecordingHotkeys(
            self,
            bindings=(("quick_capture", native, "Open Quick Capture"),),
            fallback="Open Quick Capture from the Tools menu.",
        )
        self._hotkeys.statusChanged.connect(self._status)
        self._hotkeys.activated.connect(self._activate)
        self._hotkeys.start()

    def _activate(self, _action):
        window = self._window()
        if window is None:
            return
        token = self._hotkeys.activation_token
        if not token:
            window._show_quick_capture_overlay()
            return
        # Qt's Wayland backend consumes this token when showing/activating a window.
        name = "XDG_ACTIVATION_TOKEN"
        previous = os.environ.get(name)
        os.environ[name] = token
        try:
            window._show_quick_capture_overlay()
        finally:
            if previous is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = previous

    def _status(self, status):
        window = self._window()
        if window is not None and "Registering" not in status:
            window._action_quick_capture.setToolTip(status)
            if window.isActiveWindow() and ("unavailable" in status or "revoked" in status):
                window.statusBar().showMessage(status, 8000)

    def _stop_registration(self):
        if self._hotkeys is not None:
            self._hotkeys.close()
            self._hotkeys.deleteLater()
            self._hotkeys = None

    def close(self):
        self._stop_registration()
        self._sequence = None
        if self._lock.isLocked():
            self._lock.unlock()
