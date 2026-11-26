"""One configurable desktop capture shortcut per process."""
import weakref
from PySide6.QtCore import QCoreApplication, QSettings, QTimer, Qt
from PySide6.QtGui import QKeySequence
from .recording_hotkeys import RecordingStartShortcut


class CaptureShortcut(RecordingStartShortcut):
    def __init__(self, app):
        # Reuse window routing without registering the recording chord.
        super().__init__(app)
        self.hotkeys.activated.disconnect()
        self.hotkeys.activated.connect(self._activate)
        self.sequence = None

    def refresh(self):
        sequence = QSettings("StillPoint", "StillPoint").value("screenCapture/hotkey", "Alt+Shift+S")
        if sequence != self.sequence:
            self.hotkeys.close()
            self.sequence = sequence
            self.hotkeys._bindings = (("capture", sequence, "Capture screen region"),) if sequence else ()
            if sequence:
                QTimer.singleShot(0, self.hotkeys.start)
        for window in self._live_windows():
            action = window._capture_region_action
            action.setShortcut(QKeySequence(sequence) if "unavailable" in self.hotkeys.status.lower() else QKeySequence())
            action.setShortcutContext(Qt.ApplicationShortcut)

    def _status_changed(self, status):
        status = status.replace("Use the recording bar or tray.", "Use Capture Region in the command palette.")
        self.refresh()
        for window in self._live_windows():
            window._capture_region_action.setToolTip("Capture and annotate a screen region\n" + status)

    def _activate(self, action):
        if action != "capture":
            return
        windows = self._live_windows()
        target = self._last_window() if self._last_window else None
        if target not in windows:
            target = windows[-1] if windows else None
        if target is not None:
            target._capture_screen()


def register_capture_shortcut(window):
    app = QCoreApplication.instance()
    controller = getattr(app, "_stillpoint_capture_shortcut", None)
    if controller is None:
        controller = CaptureShortcut(app)
        app._stillpoint_capture_shortcut = controller
    if not any(ref() is window for ref in controller._windows):
        controller._windows.append(weakref.ref(window))
    controller._last_window = weakref.ref(window)
    controller.refresh()
