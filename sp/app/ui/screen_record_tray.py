"""Recording controls attached to the app tray, or a temporary recording icon."""
from __future__ import annotations

from PySide6.QtCore import QObject, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon


class ScreenRecordingTray(QObject):
    def __init__(self, dialog):
        super().__init__(dialog)
        self.dialog = dialog
        self.tray = None
        self.menu = None
        self.recording_menu = None
        self._owns_tray = False
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._blink)
        self._blink_bright = True

    @property
    def available(self):
        return self.tray is not None and self.tray.isVisible()

    def start(self):
        if self.tray is not None:
            return
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        app = QApplication.instance()
        owner = getattr(app, "_stillpoint_tray_owner", None)
        tray = getattr(owner, "_tray_icon", None)
        menu = getattr(owner, "_tray_menu", None)
        if tray is not None and tray.isVisible() and menu is not None:
            self.tray, self.menu = tray, menu
            self._owns_tray = False
        else:
            # A temporary icon keeps hidden recordings reachable even when the
            # normal tray icon is disabled or belongs to a different process.
            self.tray = QSystemTrayIcon(self)
            self.menu = QMenu(self.dialog)
            self.tray.setContextMenu(self.menu)
            self._owns_tray = True
        self._original_icon = self.tray.icon()
        self._original_tooltip = self.tray.toolTip()
        self._recording_icon = self._colored_icon("#ed3b43")
        self._dim_recording_icon = self._colored_icon("#75242a")
        self._paused_icon = self._colored_icon("#f5b544", paused=True)
        self.recording_menu = QMenu("Screen recording", self.menu)
        self.pause_action = self.recording_menu.addAction("Pause recording")
        self.pause_action.triggered.connect(self.dialog._pause_resume)
        self.stop_action = self.recording_menu.addAction("Stop and review")
        self.stop_action.triggered.connect(self.dialog._stop)
        self.recording_menu.addSeparator()
        self.shortcuts_action = self.recording_menu.addAction("Global shortcuts")
        self.shortcuts_action.setEnabled(False)
        self.bar_action = self.recording_menu.addAction("Show recording bar")
        self.bar_action.setCheckable(True)
        self.bar_action.setChecked(self.dialog._show_recording_bar)
        self.bar_action.triggered.connect(self.dialog.set_recording_bar_visible)
        # Preserve the first two separators used by MainWindow's vault list.
        existing = self.menu.actions()
        if existing:
            self.menu.insertMenu(existing[-1], self.recording_menu)
        else:
            self.menu.addMenu(self.recording_menu)
        self.recording_menu.aboutToShow.connect(self.update)
        self.update()
        if self._owns_tray:
            self.tray.show()
        self._timer.start()

    @staticmethod
    def _colored_icon(color, *, paused=False):
        pixmap = QPixmap(QSize(32, 32))
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(color))
        painter.drawEllipse(2, 2, 28, 28)
        painter.setBrush(QColor("#ffffff"))
        if paused:
            painter.drawRoundedRect(10, 9, 4, 14, 1, 1)
            painter.drawRoundedRect(18, 9, 4, 14, 1, 1)
        else:
            painter.drawRoundedRect(10, 10, 12, 12, 2, 2)
        painter.end()
        return QIcon(pixmap)

    def _blink(self):
        self._blink_bright = not self._blink_bright if self.dialog._state == "recording" else True
        self.update()

    def update(self):
        if self.tray is None:
            return
        paused = self.dialog._state == "paused"
        active = self.dialog._state == "recording"
        label = "Paused" if paused else "Recording" if active else "Starting recording"
        elapsed = self.dialog.timer_label.text()
        self.tray.setIcon(self._paused_icon if paused else
                          self._dim_recording_icon if active and not self._blink_bright else self._recording_icon)
        self.tray.setToolTip(f"StillPoint · {label} · {elapsed}")
        self.recording_menu.setTitle(f"{label} · {elapsed}")
        self.pause_action.setText("Resume recording" if paused else "Pause recording")
        self.pause_action.setEnabled(active or paused)
        self.stop_action.setEnabled(self.dialog._state in ("starting", "recording", "paused"))
        self.bar_action.setChecked(self.dialog._show_recording_bar)
        if hasattr(self.dialog, "_hotkeys"):
            self.shortcuts_action.setText(self.dialog._hotkeys.status)

    def stop(self):
        self._timer.stop()
        if self.tray is None:
            return
        self.menu.removeAction(self.recording_menu.menuAction())
        self.recording_menu.deleteLater()
        self.recording_menu = None
        if self._owns_tray:
            self.tray.hide()
            self.tray.deleteLater()
            self.menu.deleteLater()
        else:
            self.tray.setIcon(self._original_icon)
            self.tray.setToolTip(self._original_tooltip)
        self.tray = None
        self.menu = None
