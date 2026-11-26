"""Small native recorder dialog; no microphone access until Record is pressed."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import shutil
import sys
import tempfile
import uuid

from PySide6.QtCore import QCoreApplication, QEvent, QMicrophonePermission, QSignalBlocker, QThread, QTimer, QUrl, QSize, Qt, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaPlayer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QMessageBox, QProgressBar,
    QPushButton, QVBoxLayout, QWidget,
)

from .voice_recorder import VoiceEncoder, VoiceRecorder, aac_available
from .audio_window import AudioWindow
from sp.app.macos_permissions import microphone_usage_description
from sp.app import config
from .audio_preferences import microphone_id


def voice_filename(suffix: str) -> str:
    return f"voice-{datetime.now():%Y-%m-%d_%H-%M-%S}-{uuid.uuid4().hex[:8]}{suffix}"


class _SaveVoiceNote(QThread):
    def __init__(self, save, source, name, parent):
        super().__init__(parent)
        self.save = save
        self.source = source
        self.name = name
        self.result = None
        self.error = None
        self.error_status = None

    def run(self):
        try:
            self.result = self.save(self.source, self.name)
        except Exception as exc:
            self.error = str(exc)
            self.error_status = getattr(getattr(exc, "response", None), "status_code", None)


class VoiceNoteDialog(AudioWindow):
    attachmentSaved = Signal(str)

    def __init__(self, parent, page_name: str, save_attachment, *, auth_prompt=None):
        super().__init__(parent)
        self.setWindowTitle("Record voice note")
        microphone_icon = QIcon(str(Path(__file__).resolve().parents[2] / "assets" / "microphone.svg"))
        self.setWindowIcon(microphone_icon)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.resize(420, 330)
        self._save_attachment = save_attachment
        self._auth_prompt = auth_prompt
        self._auth_retried = False
        self._draft_dir = None
        self._busy = False
        self._permission_pending = False
        self._worker = None
        self._encoder = None
        self._active_device_id = None
        QCoreApplication.instance().aboutToQuit.connect(self._shutdown)
        self.recorder = VoiceRecorder(self)
        self.recorder.updated.connect(self._update_controls)
        self.recorder.failed.connect(self._show_error)
        self.devices = QMediaDevices(self)
        self.devices.audioInputsChanged.connect(self._devices_changed)
        self.player = QMediaPlayer(self)
        self.output = QAudioOutput(self)
        self.output.setVolume(config.load_audio_settings()["playback_volume"] / 100)
        self.player.setAudioOutput(self.output)
        self.player.playbackStateChanged.connect(self._update_controls)
        self.player.errorOccurred.connect(lambda _code, text: self._show_error(f"Playback failed: {text}"))

        layout = QVBoxLayout(self)
        self.heading = QWidget(self)
        heading = QHBoxLayout(self.heading)
        heading.setContentsMargins(0, 0, 0, 0)
        icon = QLabel()
        icon.setPixmap(microphone_icon.pixmap(32, 32))
        heading.addWidget(icon)
        title = QLabel("Voice note")
        font = title.font()
        font.setPointSize(font.pointSize() + 4)
        font.setBold(True)
        title.setFont(font)
        heading.addWidget(title, 1)
        layout.addWidget(self.heading)
        for handle in (self.heading, icon, title):
            self.add_drag_handle(handle)
        self.destination = QLabel(f"Attach to: {page_name}")
        self.destination.setTextFormat(Qt.PlainText)
        self.destination.setWordWrap(True)
        layout.addWidget(self.destination)
        self.microphone = QComboBox()
        self.microphone.setAccessibleName("Microphone")
        layout.addWidget(QLabel("Microphone"))
        layout.addWidget(self.microphone)
        self.timer_label = QLabel("00:00")
        self.timer_label.setAlignment(Qt.AlignCenter)
        font = self.timer_label.font()
        font.setPointSize(28)
        self.timer_label.setFont(font)
        layout.addWidget(self.timer_label)
        self.meter = QProgressBar()
        self.meter.setRange(0, 100)
        self.meter.setValue(0)
        self.meter.setTextVisible(False)
        self.meter.setAccessibleName("Microphone level")
        layout.addWidget(self.meter)
        self.status = QLabel("Ready to record")
        layout.addWidget(self.status)
        controls = QHBoxLayout()
        self.record_button = QPushButton()
        self.record_button.setIconSize(QSize(32, 32))
        self.record_button.setFixedSize(52, 52)
        self.record_button.setToolTip("Record")
        self.record_button.setAccessibleName("Record")
        self.record_button.clicked.connect(self._record)
        self.pause_button = QPushButton()
        self.pause_button.setIconSize(QSize(32, 32))
        self.pause_button.setFixedSize(52, 52)
        self.pause_button.setToolTip("Pause")
        self.pause_button.setAccessibleName("Pause")
        self.pause_button.clicked.connect(self._pause_resume)
        self.stop_button = QPushButton()
        self.stop_button.setIconSize(QSize(32, 32))
        self.stop_button.setFixedSize(52, 52)
        self.stop_button.setToolTip("Stop")
        self.stop_button.setAccessibleName("Stop")
        self.stop_button.clicked.connect(self._stop)
        self.play_button = QPushButton()
        self.play_button.setIconSize(QSize(32, 32))
        self.play_button.setFixedSize(52, 52)
        self.play_button.setToolTip("Play")
        self.play_button.setAccessibleName("Play")
        self.play_button.clicked.connect(self._play)
        for button, name in ((self.record_button, "record"), (self.pause_button, "pause"),
                             (self.stop_button, "stop"), (self.play_button, "play")):
            button.setIcon(QIcon(str(Path(__file__).resolve().parents[2] / "assets" / f"{name}.svg")))
        for button in (self.record_button, self.pause_button, self.stop_button, self.play_button):
            button.setAutoDefault(False)
            controls.addWidget(button)
        layout.addLayout(controls)
        formats = QHBoxLayout()
        formats.addWidget(QLabel("Save as"))
        self.file_format = QComboBox()
        if aac_available():
            self.file_format.addItem("M4A · compact audio", ".m4a")
        self.file_format.addItem("WAV · uncompressed audio", ".wav")
        desired_format = config.load_audio_settings()["recording_format"]
        self.file_format.setCurrentIndex(max(0, self.file_format.findData(desired_format)))
        self.file_format.currentIndexChanged.connect(self._update_filename)
        self.file_format.currentIndexChanged.connect(
            lambda _index: config.save_audio_settings({"recording_format": self.file_format.currentData()}))
        formats.addWidget(self.file_format, 1)
        layout.addLayout(formats)
        self.filename = QLabel()
        self.filename.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._basename = voice_filename("")
        self._update_filename()
        layout.addWidget(self.filename)
        self.error_label = QLabel()
        self.error_label.setTextFormat(Qt.PlainText)
        self.error_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        layout.addWidget(self.error_label)
        actions = QHBoxLayout()
        self.discard_button = QPushButton("Cancel")
        self.discard_button.setAutoDefault(False)
        self.discard_button.clicked.connect(self.reject)
        self.save_button = QPushButton("Save")
        self.save_button.setAutoDefault(False)
        self.save_button.clicked.connect(self._save)
        actions.addWidget(self.discard_button)
        actions.addStretch()
        actions.addWidget(self.save_button)
        layout.addLayout(actions)
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        self.microphone.currentIndexChanged.connect(self._remember_microphone)
        self._devices_changed()
        self._update_controls()

    def _devices_changed(self):
        devices = QMediaDevices.audioInputs()
        if self.recorder.state != "idle":
            selected = self.microphone.currentData()
            active_id = self._active_device_id or (bytes(selected.id()) if selected is not None else None)
            if (self.recorder.state in ("recording", "paused") and active_id is not None
                    and active_id not in [bytes(device.id()) for device in devices]):
                self.recorder.stop()
                self._show_error("The microphone was disconnected. Your captured audio is retained; you can save it.")
            return
        settings = config.load_audio_settings()
        selected_id = settings["microphone_id"]
        blocker = QSignalBlocker(self.microphone)
        self.microphone.clear()
        if devices:
            self.microphone.addItem("System default", None)
        matching = None
        for device in devices:
            self.microphone.addItem(device.description(), device)
            if microphone_id(device) == selected_id:
                matching = self.microphone.count() - 1
        # Some backends change a device ID after reconnecting. Only use its
        # saved name as a fallback when that name identifies a single device.
        if selected_id and matching is None and settings["microphone_name"]:
            matches = [index + 1 for index, device in enumerate(devices)
                       if device.description() == settings["microphone_name"]]
            if len(matches) == 1:
                matching = matches[0]
        self.microphone.setCurrentIndex(matching if matching is not None else (0 if devices else -1))
        del blocker
        self._update_controls()

    def _remember_microphone(self, _index):
        device = self.microphone.currentData()
        config.save_audio_settings({
            "microphone_id": microphone_id(device) if device is not None else "",
            "microphone_name": device.description() if device is not None else "",
        })

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.ActivationChange and self.isActiveWindow() and hasattr(self, "microphone"):
            self._devices_changed()

    def _update_filename(self):
        self.filename.setText(self._basename + self.file_format.currentData())

    def _record(self):
        if self._permission_pending or self._busy or self.recorder.state != "idle":
            return
        if sys.platform == "darwin":
            if not microphone_usage_description():
                self._show_error(
                    "Microphone access requires a macOS app bundle.\n"
                    "Quit this app, then restart from the repository with:\n"
                    "venv/bin/python tools/run-macos-dev.py\n"
                    "You can add your usual --vault arguments.\n"
                    "Packaged apps need a rebuild with the microphone declaration."
                )
                return
            permission = QMicrophonePermission()
            status = QCoreApplication.instance().checkPermission(permission)
            if status == Qt.PermissionStatus.Undetermined:
                self._permission_pending = True
                self._update_controls()
                QCoreApplication.instance().requestPermission(permission, self, self._permission_result)
                return
            if status == Qt.PermissionStatus.Denied:
                self._show_error("Microphone access is denied. Enable it for StillPoint in System Settings → Privacy & Security → Microphone.")
                return
        device = self.microphone.currentData()
        if device is None:
            device = QMediaDevices.defaultAudioInput()
        if device.isNull():
            self._show_error("No microphone is available. Connect one and try again.")
            return
        try:
            if self._draft_dir is None:
                self._draft_dir = Path(tempfile.mkdtemp(prefix="stillpoint-voice-"))
            self.error_label.hide()
            self._active_device_id = bytes(device.id())
            self.recorder.start(device, self._draft_dir / "recording.wav")
        except OSError as exc:
            self._show_error(f"Could not create the recording draft: {exc}")

    def _permission_result(self, permission):
        self._permission_pending = False
        self._update_controls()
        if permission.status() == Qt.PermissionStatus.Granted:
            self._record()
        else:
            self._show_error("Microphone access was denied. Enable it in System Settings to record a voice note.")

    def _pause_resume(self):
        if self.recorder.state == "paused":
            self.recorder.resume()
        else:
            self.recorder.pause()

    def _stop(self):
        self.recorder.stop()

    def _play(self):
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.stop()
        else:
            self.player.setSource(QUrl.fromLocalFile(str(self.recorder.path)))
            self.player.play()

    def _tick(self):
        seconds = self.recorder.duration_ms // 1000
        minutes, seconds = divmod(seconds, 60)
        self.timer_label.setText(f"{minutes:02d}:{seconds:02d}")
        peak = self.recorder.peak
        self.recorder.peak = 0.0
        self.meter.setValue(round(100 * peak ** 0.5))
        if not self._busy:
            self.status.setText("Input too loud" if peak >= 0.99 else {
                "idle": "Ready to record" if self.microphone.count() else "No microphone found",
                "recording": "Recording…", "paused": "Paused", "stopped": "Ready to save",
            }[self.recorder.state])

    def _update_controls(self, *_):
        state = self.recorder.state
        ready = state == "stopped" and self.recorder.frames > 0
        self.microphone.setEnabled(state == "idle" and not self._busy)
        self.record_button.setEnabled(state == "idle" and self.microphone.count() > 0
                                      and not self._permission_pending and not self._busy)
        self.pause_button.setEnabled(state in ("recording", "paused") and not self._busy)
        self.pause_button.setToolTip("Resume" if state == "paused" else "Pause")
        self.pause_button.setAccessibleName("Resume" if state == "paused" else "Pause")
        self.pause_button.setIcon(QIcon(str(Path(__file__).resolve().parents[2] / "assets" /
                                          ("play.svg" if state == "paused" else "pause.svg"))))
        self.stop_button.setEnabled(state in ("recording", "paused") and not self._busy)
        self.play_button.setEnabled(ready and not self._busy)
        self.play_button.setToolTip("Stop playback" if self.player.playbackState() == QMediaPlayer.PlayingState else "Play")
        self.play_button.setAccessibleName("Stop playback" if self.player.playbackState() == QMediaPlayer.PlayingState else "Play")
        self.play_button.setIcon(QIcon(str(Path(__file__).resolve().parents[2] / "assets" /
                                          ("stop.svg" if self.player.playbackState() == QMediaPlayer.PlayingState else "play.svg"))))
        self.save_button.setEnabled(ready and not self._busy)
        self.file_format.setEnabled(not self._busy)
        self.discard_button.setEnabled(not self._busy and not self._permission_pending)
        self.discard_button.setText("Discard" if self.recorder.frames else "Cancel")

    def _show_error(self, message):
        self.error_label.setText(message)
        self.error_label.show()
        self.layout().activate()
        needed = self.layout().totalHeightForWidth(self.width())
        if needed > self.height():
            self.resize(self.width(), needed)

    def _save(self):
        if self._busy or self.recorder.state != "stopped" or not self.recorder.frames:
            return
        self.player.stop()
        self.player.setSource(QUrl())
        self._busy = True
        self._auth_retried = False
        self.error_label.hide()
        self._update_controls()
        if self.file_format.currentData() == ".m4a":
            self.status.setText("Encoding audio…")
            self._encoder = VoiceEncoder(self)
            self._encoder.succeeded.connect(self._save_file)
            self._encoder.failed.connect(self._encoding_failed)
            self._encoder.start(self.recorder.path, self._draft_dir / "encoded.m4a")
        else:
            self._save_file(self.recorder.path)

    def _encoding_failed(self, message):
        self._busy = False
        self.file_format.setCurrentIndex(self.file_format.findData(".wav"))
        self._show_error(message + " Your WAV draft is retained; press Save to use it.")
        self._update_controls()

    def _save_file(self, path):
        self.status.setText("Saving attachment…")
        self._worker = _SaveVoiceNote(self._save_attachment, path, self.filename.text(), self)
        self._worker.finished.connect(self._save_finished)
        self._worker.start()

    def _save_finished(self):
        worker = self._worker
        self._worker = None
        self._busy = False
        if worker.error_status == 401 and self._auth_prompt and not self._auth_retried:
            self._auth_retried = True
            self._busy = True
            try:
                authenticated = self._auth_prompt()
            except Exception:
                authenticated = False
            if authenticated:
                self._save_file(worker.source)
                worker.deleteLater()
                return
            self._busy = False
        if worker.error is not None:
            self._show_error(f"Could not save the attachment: {worker.error}\nYour recording is retained at {self.recorder.path}. You can retry Save.")
            self._update_controls()
        else:
            self.attachmentSaved.emit(str(worker.result))
            self._cleanup()
            self.accept()
        worker.deleteLater()

    def _cleanup(self):
        self._timer.stop()
        self.player.stop()
        self.player.setSource(QUrl())
        self.recorder.stop()
        if self._draft_dir:
            shutil.rmtree(self._draft_dir, ignore_errors=True)
            self._draft_dir = None

    def _shutdown(self):
        # Application shutdown can destroy a dialog without calling reject().
        # Finalize the WAV, retaining unsaved drafts, and do not destroy a live
        # save thread while it owns an output file or HTTP request.
        self._timer.stop()
        self.player.stop()
        self.recorder.stop()
        if self._encoder is not None:
            self._encoder._fail("Application is closing. The WAV draft is retained.")
        if self._worker is not None:
            self._worker.wait()

    def reject(self):
        if self._busy or self._permission_pending:
            return
        if self.recorder.frames or self.recorder.state in ("recording", "paused"):
            answer = QMessageBox.question(self, "Discard voice note?", "Discard this recording?",
                                          QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Cancel)
            if answer != QMessageBox.Discard:
                return
        self._cleanup()
        super().reject()

    def closeEvent(self, event):
        event.ignore()
        self.reject()
