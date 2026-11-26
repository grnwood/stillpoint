"""Qt desktop screen recording with a retained draft and page-bound attachment save."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import os
import shutil
import sys
import tempfile
import uuid

from PySide6.QtCore import QCoreApplication, QEasingCurve, QElapsedTimer, QMicrophonePermission, QPropertyAnimation, QPoint, QRect, QSettings, QSize, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QPainter, QPen
from PySide6.QtMultimedia import (
    QAudioInput, QAudioOutput, QMediaCaptureSession, QMediaDevices,
    QMediaFormat, QMediaPlayer, QMediaRecorder, QScreenCapture,
)
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QComboBox, QFormLayout, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QMessageBox, QPushButton,
    QSlider, QVBoxLayout, QWidget,
)

from .audio_window import AudioWindow
from .voice_note_dialog import _SaveVoiceNote
from .video_preferences import RECORDING_PROFILES, countdown_seconds
from .screen_record_tray import ScreenRecordingTray
from .recording_hotkeys import RecordingHotkeys
from sp.app.macos_permissions import microphone_usage_description
from sp.app import config

ASSETS = Path(__file__).resolve().parents[2] / "assets"


def recording_formats() -> list[QMediaFormat]:
    """Prefer MP4; advertised encoders can still fail when hardware is absent."""
    formats = []
    seen = set()
    for container, codec in ((QMediaFormat.MPEG4, QMediaFormat.VideoCodec.H264),
                             (QMediaFormat.MPEG4, QMediaFormat.VideoCodec.MPEG4),
                             (QMediaFormat.WebM, QMediaFormat.VideoCodec.VP8),
                             (QMediaFormat.Matroska, QMediaFormat.VideoCodec.H264)):
        fmt = QMediaFormat(container)
        fmt.setVideoCodec(codec)
        fmt.resolveForEncoding(QMediaFormat.RequiresVideo)
        key = (fmt.fileFormat(), fmt.videoCodec())
        if key not in seen and fmt.videoCodec() != QMediaFormat.VideoCodec.Unspecified and fmt.isSupported(QMediaFormat.Encode):
            formats.append(fmt)
            seen.add(key)
    return formats


def recording_format() -> QMediaFormat | None:
    formats = recording_formats()
    return formats[0] if formats else None


class ScreenRecordDialog(AudioWindow):
    attachmentSaved = Signal(str)

    def __init__(self, parent, page_name: str, save_attachment):
        super().__init__(parent)
        self.setWindowTitle("Record screen")
        self.setWindowIcon(QIcon(str(ASSETS / "video-record.svg")))
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.resize(640, 520)
        self._save_attachment = save_attachment
        self._worker = None
        self._draft_dir = None
        self._path = None
        self._state = "idle"
        self._pending_record = False
        self._has_frame = False
        self._permission_pending = False
        self._count = 0
        self._is_compact = False
        self._placing_bar = False
        self._expanded_geometry = None
        self._pulse_bright = True
        self._recording_clock = QElapsedTimer()
        self._recorded_ms = 0
        self._bar_settings = QSettings("StillPoint", "StillPoint")
        self._show_recording_bar = self._bar_settings.value("screenRecording/showBar", True, type=bool)
        self._wayland = QGuiApplication.platformName().lower().startswith("wayland")
        self._format = recording_format()
        self._format_attempts = set()
        self._retry_format = None
        self._recording_failed = False
        self.review_video = None

        self.session = QMediaCaptureSession(self)
        self.capture = QScreenCapture(self)
        self.session.setScreenCapture(self.capture)
        self.recorder = QMediaRecorder(self)
        self.session.setRecorder(self.recorder)
        self.recorder.setQuality(QMediaRecorder.HighQuality)
        profile = RECORDING_PROFILES.get(self._bar_settings.value("screenRecording/profile", "standard"),
                                         RECORDING_PROFILES["standard"])
        _, self._max_width, self._max_height, frame_rate = profile
        self.recorder.setVideoFrameRate(frame_rate)
        if self._format is not None:
            self.recorder.setMediaFormat(self._format)
        self.recorder.recorderStateChanged.connect(self._recorder_state_changed)
        self.recorder.durationChanged.connect(self._duration_changed)
        self.recorder.errorOccurred.connect(self._encoder_error, Qt.QueuedConnection)
        self.capture.errorOccurred.connect(lambda _code, text: self._fail(
            "Screen capture failed: " + text +
            (". Allow StillPoint in System Settings → Privacy & Security → Screen Recording." if sys.platform == "darwin"
             else ". On Wayland, check that PipeWire and the desktop screen-sharing portal are installed." if self._wayland else "")))
        self.capture.activeChanged.connect(self._capture_active_changed)
        self.audio_input = None
        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.audio_output.setVolume(config.load_audio_settings()["playback_volume"] / 100)
        self.player.setAudioOutput(self.audio_output)
        self.player.playbackStateChanged.connect(self._playback_changed)
        self.player.positionChanged.connect(self._position_changed)
        self.player.durationChanged.connect(lambda duration: self.seek.setRange(0, duration))
        self.player.errorOccurred.connect(lambda _code, text: self._show_error("Playback failed: " + text))

        layout = QVBoxLayout(self)
        self.heading = heading = QLabel("Record screen")
        font = heading.font()
        font.setPointSize(font.pointSize() + 4)
        font.setBold(True)
        heading.setFont(font)
        layout.addWidget(heading)
        self.add_drag_handle(heading)
        self.destination = QLabel(f"Attach to: {page_name}")
        self.destination.setTextFormat(Qt.PlainText)
        self.destination.setWordWrap(True)
        layout.addWidget(self.destination)
        self.settings = QWidget()
        settings = QFormLayout(self.settings)
        settings.setContentsMargins(0, 0, 0, 0)
        self.screen = QComboBox()
        self.screen.setAccessibleName("Screen")
        for number, screen in enumerate(QGuiApplication.screens(), 1):
            self.screen.addItem(f"Display {number} · {screen.name()}", screen)
        self.choose_button = QPushButton("Choose screen…" if self._wayland else "Preview screen")
        self.choose_button.setAutoDefault(False)
        self.choose_button.clicked.connect(self._preview)
        screen_row = QHBoxLayout()
        if not self._wayland:
            screen_row.addWidget(self.screen, 1)
        screen_row.addWidget(self.choose_button)
        settings.addRow("Screen", screen_row)
        saved_screen = self._bar_settings.value("screenRecording/screenName", "")
        available_screens = QGuiApplication.screens()
        matching_screen = next((index for index, screen in enumerate(available_screens)
                                if screen.name() == saved_screen), None)
        if saved_screen and matching_screen is None and not self._wayland:
            self.screen.addItem(f"Unavailable: {saved_screen}", None)
            self.screen.setCurrentIndex(self.screen.count() - 1)
        elif matching_screen is not None:
            self.screen.setCurrentIndex(matching_screen)
        else:
            primary = QGuiApplication.primaryScreen()
            if primary in available_screens:
                self.screen.setCurrentIndex(available_screens.index(primary))
        self.screen.currentIndexChanged.connect(self._screen_changed)
        self.microphone = QComboBox()
        self.microphone.setAccessibleName("Microphone")
        self.microphone.addItem("Off", None)
        for device in QMediaDevices.audioInputs():
            self.microphone.addItem(device.description(), device)
        saved_microphone = self._bar_settings.value("screenRecording/microphoneId", "")
        saved_microphone_name = self._bar_settings.value("screenRecording/microphoneName", "")
        if saved_microphone:
            matching_microphone = next((index for index in range(1, self.microphone.count())
                if bytes(self.microphone.itemData(index).id()).hex() == saved_microphone), None)
            if matching_microphone is None:
                named = [index for index in range(1, self.microphone.count())
                         if self.microphone.itemText(index) == saved_microphone_name]
                matching_microphone = named[0] if len(named) == 1 else None
            if matching_microphone is None:
                self.microphone.addItem(f"Unavailable: {saved_microphone_name or 'saved microphone'}", None)
                self.microphone.setItemData(self.microphone.count() - 1, True, Qt.UserRole + 1)
                matching_microphone = self.microphone.count() - 1
            self.microphone.setCurrentIndex(matching_microphone)
        settings.addRow("Microphone", self.microphone)
        layout.addWidget(self.settings)
        self.video = QVideoWidget()
        self.video.setMinimumSize(320, 180)
        self.video.setStyleSheet("background-color: #181818;")
        layout.addWidget(self.video, 1)
        self.countdown_label = QLabel()
        self.countdown_label.setAlignment(Qt.AlignCenter)
        self.countdown_label.setMinimumHeight(180)
        self.countdown_label.setStyleSheet("background: #181818; color: #ff5252; font-size: 96px; font-weight: bold; border-radius: 8px;")
        self.countdown_label.hide()
        self._countdown_effect = QGraphicsOpacityEffect(self.countdown_label)
        self.countdown_label.setGraphicsEffect(self._countdown_effect)
        self._number_animation = QPropertyAnimation(self._countdown_effect, b"opacity", self)
        self._number_animation.setDuration(300)
        layout.addWidget(self.countdown_label, 1)

        self.session.setVideoOutput(self.video)
        self.video.videoSink().videoFrameChanged.connect(self._frame_ready)
        self.seek = QSlider(Qt.Horizontal)
        self.seek.setAccessibleName("Playback position")
        self.seek.sliderMoved.connect(self.player.setPosition)
        self.seek.hide()
        layout.addWidget(self.seek)
        status_row = QHBoxLayout()
        self.status = QLabel("Choose a screen to preview, or press Record")
        self.status.setWordWrap(True)
        status_row.addWidget(self.status, 1)
        self.timer_label = QLabel("00:00")
        status_row.addWidget(self.timer_label)
        layout.addLayout(status_row)
        controls = QHBoxLayout()
        controls.addStretch()
        self.recording_indicator = QLabel("● 00:00")
        self.recording_indicator.setStyleSheet("font-size: 12px; font-weight: bold; color: #ff5252;")
        self.recording_indicator.hide()
        self.add_drag_handle(self.recording_indicator)
        controls.addWidget(self.recording_indicator)
        self.record_button = self._button("record", "Record", self._record)
        self.pause_button = self._button("pause", "Pause", self._pause_resume)
        self.stop_button = self._button("stop", "Stop", self._stop)
        self.play_button = self._button("play", "Play", self._play)
        for button in (self.record_button, self.pause_button, self.stop_button, self.play_button):
            controls.addWidget(button)
        controls.addStretch()
        layout.addLayout(controls)
        self.filename = QLabel()
        self.filename.setTextFormat(Qt.PlainText)
        layout.addWidget(self.filename)
        self.error_label = QLabel()
        self.error_label.setTextFormat(Qt.PlainText)
        self.error_label.setWordWrap(True)
        self.error_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.error_label.hide()
        layout.addWidget(self.error_label)
        self.actions = QWidget()
        actions = QHBoxLayout(self.actions)
        actions.setContentsMargins(0, 0, 0, 0)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setAutoDefault(False)
        self.cancel_button.clicked.connect(self.reject)
        actions.addWidget(self.cancel_button)
        actions.addStretch()
        self.save_button = QPushButton("Save attachment")
        self.save_button.setAutoDefault(False)
        self.save_button.clicked.connect(self._save)
        actions.addWidget(self.save_button)
        layout.addWidget(self.actions)
        self._countdown = QTimer(self)
        self._countdown.setInterval(1000)
        self._countdown.timeout.connect(self._countdown_tick)
        self._start_timeout = QTimer(self)
        self._start_timeout.setSingleShot(True)
        self._start_timeout.setInterval(60000)
        self._start_timeout.timeout.connect(lambda: self._fail("No screen frames received. Check screen-sharing permissions and try again."))
        QCoreApplication.instance().aboutToQuit.connect(self._shutdown)
        QGuiApplication.instance().screenRemoved.connect(self._screen_removed)
        if self._format is None:
            self._show_error("No video encoder is available. Screen recording requires Qt Multimedia with its FFmpeg backend.")
        elif os.environ.get("QT_MEDIA_BACKEND", "ffmpeg").lower() != "ffmpeg":
            self._format = None
            self._show_error("Screen recording requires Qt’s FFmpeg multimedia backend. Restart without the QT_MEDIA_BACKEND override.")
        self.add_drag_handle(self)
        self.setCursor(Qt.ArrowCursor)
        self._position_save_timer = QTimer(self)
        self._position_save_timer.setSingleShot(True)
        self._position_save_timer.setInterval(250)
        self._position_save_timer.timeout.connect(self._remember_bar_position)
        self._bar_animation = QPropertyAnimation(self, b"geometry", self)
        self._bar_animation.setDuration(380)
        self._bar_animation.setEasingCurve(QEasingCurve.InOutCubic)
        self._bar_animation.finished.connect(self._bar_animation_finished)
        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(500)
        self._pulse_timer.timeout.connect(self._pulse_recording)
        self._tray_controls = ScreenRecordingTray(self)
        self._hotkeys = RecordingHotkeys(self)
        self._hotkeys.ready.connect(self._hotkeys_ready)
        self._hotkeys.activated.connect(self._global_recording_action)
        self._hotkeys.statusChanged.connect(lambda _status: self._tray_controls.update())
        self.screen.currentIndexChanged.connect(self._remember_capture_defaults)
        self.microphone.currentIndexChanged.connect(self._remember_capture_defaults)
        self._update_controls()

    def _remember_capture_defaults(self, *_args):
        screen = self.screen.currentData()
        if screen is not None and not self._wayland:
            self._bar_settings.setValue("screenRecording/screenName", screen.name())
        if not self.microphone.currentData(Qt.UserRole + 1):
            device = self.microphone.currentData()
            self._bar_settings.setValue("screenRecording/microphoneId", bytes(device.id()).hex() if device is not None else "")
            self._bar_settings.setValue("screenRecording/microphoneName", device.description() if device is not None else "")

    def _button(self, asset, label, callback):
        button = QPushButton()
        button.setIcon(QIcon(str(ASSETS / f"{asset}.svg")))
        button.setIconSize(QSize(32, 32))
        button.setFixedSize(52, 52)
        button.setAutoDefault(False)
        button.setCursor(Qt.PointingHandCursor)
        button.setToolTip(label)
        button.setAccessibleName(label)
        button.clicked.connect(callback)
        return button

    def _preview(self):
        if self._state != "idle" or self.capture.isActive():
            return self.capture.isActive()
        selected = self.screen.currentData()
        if not self._wayland and selected not in QGuiApplication.screens():
            self._show_error("The saved screen is unavailable. Choose an available screen before recording.")
            return False
        self.error_label.hide()
        self._has_frame = False
        if not self._wayland:
            self.capture.setScreen(selected)
        self.status.setText("Waiting for screen access…")
        self._start_timeout.start()
        self.capture.start()
        return True

    def _screen_changed(self, _index):
        if self._state == "idle":
            self.capture.stop()
            self._has_frame = False
            self.status.setText("Choose a screen to preview, or press Record")

    def _capture_active_changed(self, active):
        if not active and self._state in ("recording", "paused", "starting"):
            self._fail("Screen sharing ended. Any captured recording is retained.")

    def _frame_ready(self, frame):
        if not frame.isValid() or self._state not in ("idle", "waiting"):
            return
        self._has_frame = True
        self._start_timeout.stop()
        if self._pending_record:
            self._begin_countdown()
        else:
            self.status.setText("Ready to record")

    def _record(self):
        if self._state != "idle" or self._format is None or self._permission_pending:
            return
        if self.microphone.currentData(Qt.UserRole + 1):
            self._show_error("The saved microphone is unavailable. Choose another microphone or explicitly select Off.")
            return
        self._remember_capture_defaults()
        if self.microphone.currentData() is not None and sys.platform == "darwin":
            if not microphone_usage_description():
                self._show_error("Microphone access requires the StillPoint macOS app bundle. Choose Microphone Off or launch with tools/run-macos-dev.py.")
                return
            permission = QMicrophonePermission()
            app = QCoreApplication.instance()
            status = app.checkPermission(permission)
            if status == Qt.PermissionStatus.Undetermined:
                self._permission_pending = True
                self._update_controls()
                app.requestPermission(permission, self, self._microphone_permission)
                return
            if status == Qt.PermissionStatus.Denied:
                self._show_error("Microphone access is denied. Enable it in System Settings, or choose Microphone Off.")
                return
        self._pending_record = True
        if self.capture.isActive() and self._has_frame:
            self._begin_countdown()
        else:
            if not self._preview():
                self._pending_record = False
                return
            if self._pending_record:
                self._state = "waiting"
                self._update_controls()

    def _microphone_permission(self, permission):
        self._permission_pending = False
        self._update_controls()
        if permission.status() == Qt.PermissionStatus.Granted:
            self._record()
        else:
            self._show_error("Microphone access was denied. Choose Microphone Off to record silently.")

    def _begin_countdown(self):
        self._pending_record = False
        self._state = "shortcuts"
        self.status.setText("Registering global shortcuts; approve the desktop prompt if shown…")
        self._update_controls()
        self._hotkeys.start()

    def _hotkeys_ready(self):
        if self._state != "shortcuts":
            return
        if "unavailable" in self._hotkeys.status.lower():
            self._show_error(self._hotkeys.status)
        self._state = "countdown"
        if not self._bar_settings.value("screenRecording/useCountdown", True, type=bool):
            self._start_recording()
            return
        self._count = countdown_seconds(self._bar_settings)
        self.video.hide()
        self.countdown_label.show()
        self._show_countdown_number()
        self.status.setText(f"Recording starts in {self._count}…")
        self._update_controls()
        self._countdown.start()

    def _global_recording_action(self, action):
        if action == "pause" and self._state in ("recording", "paused"):
            self._pause_resume()
        elif action == "stop" and self._state in ("shortcuts", "countdown", "starting", "recording", "paused"):
            self._stop()

    def _countdown_tick(self):
        self._count -= 1
        if self._count:
            self._show_countdown_number()
            self.status.setText(f"Recording starts in {self._count}…")
            return
        self._countdown.stop()
        self._start_recording()

    def _show_countdown_number(self):
        self.countdown_label.setText(str(self._count))
        self.countdown_label.setAccessibleName(f"Recording starts in {self._count}")
        self._number_animation.stop()
        self._number_animation.setStartValue(0.25)
        self._number_animation.setEndValue(1.0)
        self._number_animation.start()

    def _hide_countdown(self):
        self._number_animation.stop()
        self.countdown_label.hide()
        if not self._is_compact and self.review_video is None:
            self.video.show()

    def _start_recording(self):
        self._hide_countdown()
        try:
            self._format_attempts.add((self._format.fileFormat(), self._format.videoCodec()))
            self._recording_failed = False
            self._draft_dir = Path(tempfile.mkdtemp(prefix="stillpoint-screen-"))
            suffix = "." + self._format.mimeType().preferredSuffix()
            self._path = self._draft_dir / (f"screen-{datetime.now():%Y-%m-%d_%H-%M-%S}-{uuid.uuid4().hex[:8]}" + suffix)
            self.filename.setText(self._path.name)
            device = self.microphone.currentData()
            if device is not None:
                if bytes(device.id()) not in [bytes(item.id()) for item in QMediaDevices.audioInputs()]:
                    raise OSError("The selected microphone was disconnected.")
                self.audio_input = QAudioInput(device, self)
                self.session.setAudioInput(self.audio_input)
            selected_screen = self.capture.screen()
            if selected_screen is not None:
                self.recorder.setVideoResolution(selected_screen.size().scaled(self._max_width, self._max_height, Qt.KeepAspectRatio))
            self.recorder.setOutputLocation(QUrl.fromLocalFile(str(self._path)))
            self._recorded_ms = 0
            self._recording_clock.invalidate()
            self._state = "starting"
            self._compact(True)
            self.status.setText("Starting recording…")
            self._update_controls()
            self.recorder.record()
        except (OSError, RuntimeError) as exc:
            self._fail(str(exc))

    def _compact(self, compact):
        if compact == self._is_compact:
            return
        was_visible = self.isVisible()
        self._bar_animation.stop()
        if compact:
            self._expanded_geometry = self.geometry()
            self._tray_controls.start()
        else:
            self._remember_bar_position()
            self._tray_controls.stop()
            self._hotkeys.close()
        self._placing_bar = True
        self._is_compact = compact
        self.setWindowFlag(Qt.WindowStaysOnTopHint, compact)
        self.setWindowFlag(Qt.FramelessWindowHint, compact)
        for widget in (self.heading, self.destination, self.settings, self.video,
                       self.filename, self.actions, self.status, self.timer_label):
            widget.setVisible(not compact)
        self.countdown_label.hide()
        self.recording_indicator.setVisible(compact)
        self.error_label.setVisible(not compact and bool(self.error_label.text()))
        self.seek.setVisible(not compact and self._state == "stopped")
        self.play_button.setVisible(not compact)
        self.record_button.setVisible(not compact)
        margins = (8, 2, 8, 2) if compact else (11, 11, 11, 11)
        self.layout().setContentsMargins(*margins)
        self.layout().setSpacing(0 if compact else 6)
        self.setCursor(Qt.OpenHandCursor if compact else Qt.ArrowCursor)
        for button in (self.pause_button, self.stop_button):
            button.setFixedSize(QSize(32, 32) if compact else QSize(52, 52))
            button.setIconSize(QSize(24, 24) if compact else QSize(32, 32))
        self.layout().activate()
        target_size = QSize(164, 36)
        show_bar = self._show_recording_bar or not self._tray_controls.available
        animate = compact and was_visible and self._state == "starting" and show_bar
        if compact:
            self.setMinimumSize(0, 0)
            self.setMaximumSize(16777215, 16777215)
            bar_position = self._bar_position(target_size)
            if not animate:
                self.setFixedSize(target_size)
        else:
            self.setMinimumSize(0, 0)
            self.setMaximumSize(16777215, 16777215)
            if self._expanded_geometry is not None:
                self.setGeometry(self._expanded_geometry)
            else:
                self.resize(640, 520)
        if (compact and show_bar and was_visible) or (not compact and (was_visible or self._state == "stopped")):
            self.show()
            if not compact and self._state == "stopped":
                self.raise_()
                self.activateWindow()
        elif compact and not show_bar:
            self.hide()
        if animate:
            self.setGeometry(self._expanded_geometry)
            self._bar_animation.setStartValue(self._expanded_geometry)
            self._bar_animation.setEndValue(QRect(bar_position, target_size))
            self._bar_animation.start()
        elif compact:
            self.setGeometry(QRect(bar_position, target_size))
        elif self._expanded_geometry is not None:
            self.setGeometry(self._expanded_geometry)
        if not animate:
            self._placing_bar = False
        if compact:
            self._pulse_timer.start()
        else:
            self._pulse_timer.stop()
        self._update_recording_indicator()
        self.update()

    def set_recording_bar_visible(self, visible):
        # Never hide the last usable recording controls on desktops without a tray.
        if not visible and not self._tray_controls.available:
            return
        self._show_recording_bar = bool(visible)
        self._bar_settings.setValue("screenRecording/showBar", self._show_recording_bar)
        if self._is_compact:
            if visible:
                self.show()
                self.raise_()
            else:
                self._remember_bar_position()
                self._bar_animation.stop()
                self._bar_animation_finished()
                self.hide()
        self._tray_controls.update()

    def _bar_animation_finished(self):
        if self._is_compact:
            self.setFixedSize(164, 36)
        self._placing_bar = False

    def _pulse_recording(self):
        if self._state == "recording" and self._recording_clock.isValid():
            self._duration_changed(self._recorded_ms + self._recording_clock.elapsed())
        self._pulse_bright = not self._pulse_bright
        self._update_recording_indicator()
        self.update()

    def _update_recording_indicator(self):
        paused = self._state == "paused"
        active = self._state == "recording"
        color = "#ffbd59" if paused or not active else ("#ff5252" if self._pulse_bright else "#b52f38")
        marker = "Ⅱ" if paused else "●" if active else "…"
        self.recording_indicator.setText(f"{marker} {self.timer_label.text()}")
        label = "Paused" if paused else "Recording" if active else "Starting recording"
        self.recording_indicator.setToolTip(label)
        self.recording_indicator.setAccessibleName(f"{label}, {self.timer_label.text()}")
        self.recording_indicator.setStyleSheet(f"font-size: 12px; font-weight: bold; color: {color};")
        self._recording_border_color = QColor(color)
        if hasattr(self, "_tray_controls"):
            self._tray_controls.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._is_compact:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setPen(QPen(getattr(self, "_recording_border_color", QColor("#ff5252")), 2))
            painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 5, 5)

    def _bar_position(self, size=None):
        size = size or self.size()
        screens = QGuiApplication.screens()
        saved = self._bar_settings.value("screenRecording/toolbarPosition")
        screen = QGuiApplication.screenAt(saved) if isinstance(saved, QPoint) else None
        if screen is None:
            selected = self.screen.currentData()
            screen = selected if selected in screens else QGuiApplication.primaryScreen()
            available = screen.availableGeometry()
            return QPoint(available.center().x() - size.width() // 2,
                          available.bottom() - size.height() - 12 + 1)
        available = screen.availableGeometry()
        return QPoint(max(available.left(), min(saved.x(), available.right() - size.width() + 1)),
                      max(available.top(), min(saved.y(), available.bottom() - size.height() + 1)))

    def moveEvent(self, event):
        super().moveEvent(event)
        if (getattr(self, "_is_compact", False) and not self._placing_bar
                and self.isVisible() and hasattr(self, "_position_save_timer")):
            self._position_save_timer.start()

    def _remember_bar_position(self):
        if self._is_compact and self.isVisible() and not self._placing_bar:
            self._bar_settings.setValue("screenRecording/toolbarPosition", self.geometry().topLeft())

    def _recorder_state_changed(self, state):
        if state == QMediaRecorder.RecordingState:
            if self._state != "recording":
                self._recording_clock.start()
            self._state = "recording"
            self.status.setText("Recording…")
        elif state == QMediaRecorder.PausedState:
            if self._recording_clock.isValid():
                self._recorded_ms += self._recording_clock.elapsed()
                self._recording_clock.invalidate()
            self._state = "paused"
            self._duration_changed(self._recorded_ms)
            self.status.setText("Paused")
        elif self._state in ("starting", "recording", "paused", "stopping"):
            self._state = "stopped"
            self.capture.stop()
            self.session.setAudioInput(None)
            if self.audio_input is not None:
                self.audio_input.deleteLater()
                self.audio_input = None
            self._state = "stopped"
            actual = self.recorder.actualLocation().toLocalFile()
            if actual:
                self._path = Path(actual)
                self.filename.setText(self._path.name)
            self.session.setVideoOutput(None)
            # Restoring the decorated window recreates native video surfaces.
            # Create a fresh playback widget only after that transition, rather
            # than sharing the capture sink across the window-flag change.
            self._compact(False)
            if not self._recording_failed and self._path and self._path.is_file():
                self._prepare_review_video()
            self.seek.show()
            self.status.setText("Recording failed" if self._recording_failed else "Ready to review and save")
        self._update_controls()

    def _prepare_review_video(self):
        if self.review_video is None:
            self.review_video = QVideoWidget(self)
            self.review_video.setMinimumSize(320, 180)
            self.review_video.setStyleSheet("background-color: #181818;")
            self.layout().replaceWidget(self.video, self.review_video)
            self.video.hide()
        self.review_video.show()
        self.player.setVideoOutput(self.review_video)
        self.player.setSource(QUrl.fromLocalFile(str(self._path)))

    def _encoder_error(self, _code, text):
        # Capability lists describe codecs, not whether a hardware device works.
        # Retry only before any media was recorded, keeping real drafts intact.
        if self.recorder.duration() == 0 and self._state in ("starting", "recording"):
            alternatives = [fmt for fmt in recording_formats()
                            if (fmt.fileFormat(), fmt.videoCodec()) not in self._format_attempts]
            if alternatives:
                self._retry_format = alternatives[0]
                self._state = "retrying"
                self.recorder.stop()
                QTimer.singleShot(0, self._retry_encoder)
                return
        self._recording_failed = self.recorder.duration() == 0
        self._fail("Video encoder failed: " + text)

    def _retry_encoder(self):
        if self._state != "retrying":
            return
        self.session.setAudioInput(None)
        if self.audio_input is not None:
            self.audio_input.deleteLater()
            self.audio_input = None
        if self._draft_dir:
            shutil.rmtree(self._draft_dir, ignore_errors=True)
        self._format = self._retry_format
        self._retry_format = None
        self.recorder.setMediaFormat(self._format)
        self._start_recording()

    def _pause_resume(self):
        if self._state == "paused":
            self.recorder.record()
        elif self._state == "recording":
            self.recorder.pause()

    def _stop(self):
        self._hide_countdown()
        self._countdown.stop()
        self._start_timeout.stop()
        self._pending_record = False
        if self._state in ("waiting", "shortcuts", "countdown"):
            self._hotkeys.close()
            self._state = "idle"
            self.capture.stop()
            self.status.setText("Ready to record")
        elif self._state in ("starting", "recording", "paused"):
            self._state = "stopping"
            self.status.setText("Finalizing recording…")
            self.recorder.stop()
            if self._state == "stopping" and self.recorder.recorderState() == QMediaRecorder.StoppedState:
                self._recorder_state_changed(QMediaRecorder.StoppedState)
        elif self._state == "stopped":
            self.player.stop()
        self._update_controls()

    def _play(self):
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def _playback_changed(self, state):
        playing = state == QMediaPlayer.PlayingState
        self.play_button.setIcon(QIcon(str(ASSETS / ("pause.svg" if playing else "play.svg"))))
        self.play_button.setToolTip("Pause playback" if playing else "Play")
        self.play_button.setAccessibleName(self.play_button.toolTip())

    def _position_changed(self, value):
        if not self.seek.isSliderDown():
            self.seek.setValue(value)
        self._duration_changed(value)

    def _duration_changed(self, milliseconds):
        if self._state == "recording" and self._recording_clock.isValid():
            milliseconds = self._recorded_ms + self._recording_clock.elapsed()
        seconds = milliseconds // 1000
        self.timer_label.setText(f"{seconds // 60:02d}:{seconds % 60:02d}")
        self._update_recording_indicator()

    def _update_controls(self):
        idle = self._state == "idle" and not self._permission_pending
        ready = not self._recording_failed and self._state == "stopped" and self._path is not None and self._path.is_file() and self._path.stat().st_size > 0
        self.settings.setEnabled(idle)
        self.record_button.setEnabled(idle and self._format is not None)
        self.pause_button.setEnabled(self._state in ("recording", "paused"))
        self.pause_button.setIcon(QIcon(str(ASSETS / ("play.svg" if self._state == "paused" else "pause.svg"))))
        self.pause_button.setToolTip("Resume" if self._state == "paused" else "Pause")
        self.pause_button.setAccessibleName(self.pause_button.toolTip())
        self.stop_button.setEnabled(self._state in ("waiting", "shortcuts", "countdown", "starting", "recording", "paused", "stopped"))
        self.play_button.setEnabled(ready)
        self.save_button.setEnabled(ready)
        self.cancel_button.setEnabled(self._state != "saving" and not self._permission_pending)
        self.cancel_button.setText("Discard" if self._path else "Cancel")
        self._update_recording_indicator()
        self.update()

    def _show_error(self, text):
        self.error_label.setText(text)
        self.error_label.show()

    def _fail(self, text):
        self._stop()
        self.capture.stop()
        self._compact(False)
        self._show_error(text + (f"\nDraft retained at {self._path}" if self._path else ""))
        self._update_controls()

    def _screen_removed(self, screen):
        if self.capture.screen() == screen:
            self._fail("The selected screen was disconnected.")

    def _save(self):
        if self._recording_failed or self._state != "stopped" or not self._path or not self._path.is_file():
            return
        self.player.stop()
        self.player.setSource(QUrl())
        self._start_save()

    def _start_save(self):
        self._state = "saving"
        self.status.setText("Saving attachment…")
        self.error_label.hide()
        self._update_controls()
        self._worker = _SaveVoiceNote(self._save_attachment, self._path, self._path.name, self)
        self._worker.finished.connect(self._save_finished)
        self._worker.start()

    def _save_finished(self):
        worker = self._worker
        self._worker = None
        if worker.error is not None:
            self._state = "stopped"
            self.status.setText("Ready to retry save")
            self._show_error(f"Could not save attachment: {worker.error}\nDraft retained at {self._path}")
            self.player.setSource(QUrl.fromLocalFile(str(self._path)))
            self._update_controls()
        else:
            self.attachmentSaved.emit(str(worker.result))
            self._cleanup()
            self.accept()
        worker.deleteLater()

    def _cleanup(self):
        self._hotkeys.close()
        self._tray_controls.stop()
        self._bar_animation.stop()
        self._pulse_timer.stop()
        self._number_animation.stop()
        self._countdown.stop()
        self._start_timeout.stop()
        self.player.stop()
        self.player.setSource(QUrl())
        self.capture.stop()
        self.session.setAudioInput(None)
        if self._draft_dir:
            shutil.rmtree(self._draft_dir, ignore_errors=True)
            self._draft_dir = None

    def _shutdown(self):
        self._hotkeys.close()
        self._tray_controls.stop()
        self._stop()
        self.capture.stop()
        self.player.stop()
        if self._worker is not None:
            self._worker.wait()
        # Do not delete unsaved recordings on application shutdown.

    def reject(self):
        if self._state in ("saving", "stopping", "retrying") or self._permission_pending:
            return
        if self._path or self._state in ("waiting", "shortcuts", "countdown", "starting", "recording", "paused"):
            answer = QMessageBox.question(self, "Discard screen recording?", "Discard this recording?",
                                          QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Cancel)
            if answer != QMessageBox.Discard:
                return
        if self._state in ("starting", "recording", "paused"):
            self._stop()
            # Wait for finalization before allowing draft deletion.
            QTimer.singleShot(0, self._discard_when_stopped)
            return
        self._cleanup()
        super().reject()

    def _discard_when_stopped(self):
        if self._state == "stopping":
            QTimer.singleShot(50, self._discard_when_stopped)
            return
        self._cleanup()
        super().reject()

    def closeEvent(self, event):
        event.ignore()
        self.reject()
