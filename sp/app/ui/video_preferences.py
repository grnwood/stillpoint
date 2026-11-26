"""Device-local defaults shared by preferences and screen capture."""
from PySide6.QtCore import QSettings
from PySide6.QtGui import QGuiApplication
from PySide6.QtMultimedia import QMediaDevices
from PySide6.QtWidgets import QCheckBox, QComboBox, QFormLayout, QLabel, QSpinBox, QVBoxLayout, QWidget


# Maximum size and frame rate; preserve the display's aspect ratio.
RECORDING_PROFILES = {
    "standard": ("Standard · 1080p, 30 fps", 1920, 1080, 30),
    "high": ("High quality · 1440p, 60 fps", 2560, 1440, 60),
    "compact": ("Smaller files · 720p, 24 fps", 1280, 720, 24),
}


def countdown_seconds(settings):
    try:
        return max(1, min(60, int(settings.value("screenRecording/countdownSeconds", 3))))
    except (TypeError, ValueError):
        return 3


class VideoPreferences(QWidget):
    def __init__(self, parent=None, *, settings=None):
        super().__init__(parent)
        self._settings = settings if settings is not None else QSettings("StillPoint", "StillPoint")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.monitor = QComboBox()
        self.monitor.addItem("Primary monitor", "")
        for number, screen in enumerate(QGuiApplication.screens(), 1):
            self.monitor.addItem(f"Display {number} · {screen.name()}", screen.name())
        self._select(self.monitor, self._settings.value("screenRecording/screenName", ""))
        form.addRow("Default monitor:", self.monitor)
        self.profile = QComboBox()
        for key, profile in RECORDING_PROFILES.items():
            self.profile.addItem(profile[0], key)
        index = self.profile.findData(self._settings.value("screenRecording/profile", "standard"))
        self.profile.setCurrentIndex(max(0, index))
        form.addRow("Recording profile:", self.profile)
        self.microphone = QComboBox()
        self.microphone.addItem("Off", "")
        for device in QMediaDevices.audioInputs():
            self.microphone.addItem(device.description(), bytes(device.id()).hex())
        self._select(self.microphone, self._settings.value("screenRecording/microphoneId", ""),
                     self._settings.value("screenRecording/microphoneName", ""))
        form.addRow("Default microphone:", self.microphone)
        self.use_timer = QCheckBox("Use countdown timer")
        self.use_timer.setChecked(self._settings.value("screenRecording/useCountdown", True, type=bool))
        form.addRow(self.use_timer)
        self.countdown = QSpinBox()
        self.countdown.setRange(1, 60)
        self.countdown.setSuffix(" seconds")
        self.countdown.setValue(countdown_seconds(self._settings))
        self.countdown.setEnabled(self.use_timer.isChecked())
        self.use_timer.toggled.connect(self.countdown.setEnabled)
        form.addRow("Countdown:", self.countdown)
        layout.addLayout(form)
        note = QLabel("On Wayland, the desktop screen-sharing picker chooses the monitor. "
                      "Monitor and microphone changes in the recorder become the new defaults.")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch()

    @staticmethod
    def _select(combo, value, label=""):
        index = combo.findData(value)
        if index < 0:
            combo.addItem(f"Unavailable: {label or value}", value)
            index = combo.count() - 1
        combo.setCurrentIndex(index)

    def save(self):
        for key, value in {
            "screenName": self.monitor.currentData(),
            "profile": self.profile.currentData(),
            "microphoneId": self.microphone.currentData(),
            "microphoneName": self.microphone.currentText() if self.microphone.currentData() else "",
            "useCountdown": self.use_timer.isChecked(),
            "countdownSeconds": self.countdown.value(),
        }.items():
            self._settings.setValue("screenRecording/" + key, value)
