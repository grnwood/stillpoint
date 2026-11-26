"""Compact Qt playback for local and authenticated remote audio attachments."""
from __future__ import annotations

from pathlib import Path
import shutil
import tempfile
import threading

from PySide6.QtCore import QCoreApplication, QIODevice, QObject, QRunnable, QSaveFile, QThreadPool, QUrl, Qt, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import QApplication, QFileDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QSlider, QVBoxLayout, QWidget

from .audio_window import AudioWindow
from sp.app import config
from .audio_transcription import TranscriptionWorker


def audio_time(milliseconds: int) -> str:
    seconds = max(0, milliseconds // 1000)
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes:02d}:{seconds:02d}"


class _DownloadSignals(QObject):
    finished = Signal(object, object)


class _AudioDownload(QRunnable):
    def __init__(self, client, remote_path, destination, cancelled):
        super().__init__()
        self.signals = _DownloadSignals()
        self.client = client
        self.remote_path = remote_path
        self.destination = destination
        self.cancelled = cancelled

    def run(self):
        error = None
        try:
            with self.client.stream("GET", "/api/file/raw", params={"path": self.remote_path}, timeout=5) as response:
                response.raise_for_status()
                with self.destination.open("wb") as output:
                    for chunk in response.iter_bytes(64 * 1024):
                        if self.cancelled.is_set():
                            return
                        output.write(chunk)
        except Exception as exc:
            error = exc
        finally:
            if self.cancelled.is_set():
                shutil.rmtree(self.destination.parent, ignore_errors=True)
        if not self.cancelled.is_set():
            self.signals.finished.emit(self, error)


class AudioPlayerDialog(AudioWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Audio player")
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.resize(380, 180)
        self._download = None
        self._temporary_dir = None
        self._remote = None
        self._auth_retried = False
        self._audio_path = None
        self._transcription = None
        self.player = QMediaPlayer(self)
        self.output = QAudioOutput(self)
        volume = config.load_audio_settings()["playback_volume"]
        self.output.setVolume(volume / 100)
        self.player.setAudioOutput(self.output)
        self.player.durationChanged.connect(self._duration_changed)
        self.player.positionChanged.connect(self._position_changed)
        self.player.playbackStateChanged.connect(self._playback_changed)
        self.player.seekableChanged.connect(self._seekable_changed)
        self.player.errorOccurred.connect(lambda _code, text: self._error(f"Could not play this audio: {text}"))

        layout = QVBoxLayout(self)
        self.title = QLabel("Audio player")
        self.title.setTextFormat(Qt.PlainText)
        font = self.title.font()
        font.setBold(True)
        self.title.setFont(font)
        self.title.setWordWrap(True)
        layout.addWidget(self.title)
        self.add_drag_handle(self.title)
        self.seek = QSlider(Qt.Horizontal)
        self.seek.setRange(0, 0)
        self.seek.setEnabled(False)
        self.seek.setAccessibleName("Playback position")
        self.seek.valueChanged.connect(self._seek)
        layout.addWidget(self.seek)
        self.time_label = QLabel("00:00 / 00:00")
        layout.addWidget(self.time_label)
        controls = QHBoxLayout()
        self.play_button = QPushButton("Play")
        self.play_button.setAutoDefault(False)
        self.play_button.clicked.connect(self._toggle_playback)
        controls.addWidget(self.play_button)
        self.stop_button = QPushButton("Stop")
        self.stop_button.setAutoDefault(False)
        self.stop_button.clicked.connect(self.player.stop)
        controls.addWidget(self.stop_button)
        controls.addWidget(QLabel("Volume"))
        self.volume = QSlider(Qt.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(volume)
        self.volume.setAccessibleName("Playback volume")
        self.volume.valueChanged.connect(lambda value: self.output.setVolume(value / 100))
        self.volume.valueChanged.connect(lambda value: config.save_audio_settings({"playback_volume": value}))
        controls.addWidget(self.volume, 1)
        layout.addLayout(controls)
        self.message = QLabel()
        self.message.setTextFormat(Qt.PlainText)
        self.message.setWordWrap(True)
        self.message.hide()
        layout.addWidget(self.message)
        self.retry_button = QPushButton("Retry download")
        self.retry_button.setAutoDefault(False)
        self.retry_button.clicked.connect(self._download_remote)
        self.retry_button.hide()
        layout.addWidget(self.retry_button)
        transcription_actions = QHBoxLayout()
        self.transcribe_button = QPushButton("Transcribe")
        self.transcribe_button.setAutoDefault(False)
        self.transcribe_button.setToolTip("Send this recording to the transcription server selected in Preferences → Audio")
        self.transcribe_button.clicked.connect(self._transcribe)
        transcription_actions.addWidget(self.transcribe_button)
        transcription_actions.addStretch()
        layout.addLayout(transcription_actions)
        self.transcript_panel = QWidget(self)
        transcript_layout = QVBoxLayout(self.transcript_panel)
        transcript_layout.setContentsMargins(0, 0, 0, 0)
        self.transcript = QPlainTextEdit()
        self.transcript.setAccessibleName("Audio transcript")
        self.transcript.setMinimumHeight(120)
        transcript_layout.addWidget(self.transcript)
        transcript_actions = QHBoxLayout()
        self.copy_transcript_button = QPushButton("Copy text")
        self.copy_transcript_button.setAutoDefault(False)
        self.copy_transcript_button.clicked.connect(lambda: QApplication.clipboard().setText(self.transcript.toPlainText()))
        self.save_transcript_button = QPushButton("Save transcript…")
        self.save_transcript_button.setAutoDefault(False)
        self.save_transcript_button.clicked.connect(self._save_transcript)
        transcript_actions.addWidget(self.copy_transcript_button)
        transcript_actions.addWidget(self.save_transcript_button)
        transcript_actions.addStretch()
        transcript_layout.addLayout(transcript_actions)
        layout.addWidget(self.transcript_panel)
        self.transcript_panel.hide()
        self._set_ready(False)
        QCoreApplication.instance().aboutToQuit.connect(self._reset)

    def open_local(self, path: Path):
        self._reset()
        self._remote = None
        self._set_title(path.name)
        if not path.is_file():
            self._error("This audio attachment no longer exists.")
            return
        self._load(path)

    def open_remote(self, client, remote_path: str, auth_prompt=None):
        self._reset()
        self._set_title(Path(remote_path).name)
        self._remote = (client, remote_path, auth_prompt)
        self._auth_retried = False
        self._download_remote()

    def _set_title(self, name):
        self.title.setText(name)
        self.setWindowTitle(f"Audio · {name}")

    def _load(self, path):
        self._audio_path = Path(path)
        self.message.hide()
        self.retry_button.hide()
        self._set_ready(True)
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        if self.player.error() == QMediaPlayer.NoError:
            self.player.play()

    def _download_remote(self):
        if self._download is not None or self._remote is None:
            return
        self._set_ready(False)
        self.message.setText("Downloading audio…")
        self.message.show()
        self.retry_button.hide()
        client, path, _auth = self._remote
        try:
            if self._temporary_dir is None:
                self._temporary_dir = Path(tempfile.mkdtemp(prefix="stillpoint-audio-"))
            destination = self._temporary_dir / ("audio" + Path(path).suffix)
            worker = _AudioDownload(client, path, destination, threading.Event())
            self._download = worker
            worker.signals.finished.connect(self._download_finished)
            QThreadPool.globalInstance().start(worker)
        except OSError as exc:
            self._error(str(exc))
            self.retry_button.show()

    def _download_finished(self, worker, error):
        if worker is not self._download:
            return
        self._download = None
        if error is not None:
            status = getattr(getattr(error, "response", None), "status_code", None)
            auth = self._remote[2]
            if status == 401 and auth and not self._auth_retried:
                self._auth_retried = True
                try:
                    authenticated = auth()
                except Exception:
                    authenticated = False
                if authenticated:
                    self._download_remote()
                    return
            self._error(f"Could not download this audio: {error}")
            self.retry_button.show()
            return
        self._load(worker.destination)

    def _set_ready(self, ready):
        self.play_button.setEnabled(ready)
        self.stop_button.setEnabled(ready)
        self.seek.setEnabled(ready and self.player.isSeekable())
        self.transcribe_button.setEnabled(ready and self._audio_path is not None and self._transcription is None)

    def _transcribe(self):
        if self._audio_path is None or self._transcription is not None:
            return
        from .ai_chat_panel import ServerManager
        settings = config.load_audio_settings()
        server = ServerManager().get_server(settings["transcription_server"])
        model = settings["transcription_model"]
        if server is None or not model:
            self.message.setText("Choose a transcription server and speech-to-text model in Preferences → Audio first.")
            self.message.show()
            self.adjustSize()
            return
        worker = TranscriptionWorker(self._audio_path, server, model, settings["transcription_path"], self._temporary_dir)
        self._transcription = worker
        worker.signals.finished.connect(self._transcription_finished)
        self.transcribe_button.setEnabled(False)
        self.transcribe_button.setText("Transcribing…")
        self.message.setText(f"Transcribing with {server['name']} · {model}…")
        self.message.show()
        worker.start()

    def _transcription_finished(self, worker, text, error):
        if worker is not self._transcription:
            return
        self._transcription = None
        self.transcribe_button.setText("Transcribe")
        self.transcribe_button.setEnabled(self._audio_path is not None)
        if error:
            self.message.setText(f"Transcription failed: {error}")
            self.message.show()
        else:
            self.message.hide()
            self.transcript.setPlainText(text)
            self.transcript_panel.show()
            self.resize(max(self.width(), 440), max(self.height(), 360))
        self.adjustSize()

    def _save_transcript(self):
        name = Path(self.title.text()).stem + ".txt"
        folder = self._audio_path.parent if self._remote is None and self._audio_path else Path.home()
        filename, _filter = QFileDialog.getSaveFileName(self, "Save transcript", str(folder / name), "Text files (*.txt)")
        if not filename:
            return
        output = QSaveFile(filename)
        data = self.transcript.toPlainText().encode("utf-8")
        if not output.open(QIODevice.WriteOnly) or output.write(data) != len(data) or not output.commit():
            self.message.setText(f"Could not save the transcript: {output.errorString()}")
            self.message.show()

    def _seekable_changed(self, seekable):
        self.seek.setEnabled(seekable)

    def _duration_changed(self, duration):
        self.seek.setRange(0, min(duration, 2147483647))
        self._position_changed(self.player.position())

    def _position_changed(self, position):
        if not self.seek.isSliderDown():
            self.seek.blockSignals(True)
            self.seek.setValue(position)
            self.seek.blockSignals(False)
        self.time_label.setText(f"{audio_time(self.seek.value())} / {audio_time(self.player.duration())}")

    def _seek(self, position):
        self.player.setPosition(position)
        self.time_label.setText(f"{audio_time(position)} / {audio_time(self.player.duration())}")

    def _playback_changed(self, state):
        self.play_button.setText("Pause" if state == QMediaPlayer.PlayingState else "Play")

    def _toggle_playback(self):
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            if self.player.mediaStatus() == QMediaPlayer.EndOfMedia:
                self.player.setPosition(0)
            self.player.play()

    def _error(self, message):
        self._set_ready(False)
        self.message.setText(message)
        self.message.show()
        self.adjustSize()

    def _reset(self):
        if self._transcription is not None:
            self._transcription.cancelled.set()
            self._transcription = None
        self._audio_path = None
        self.transcribe_button.setText("Transcribe")
        self.transcript.clear()
        self.transcript_panel.hide()
        if self._download is not None:
            self._download.cancelled.set()
            self._download = None
        self.player.stop()
        self.player.setSource(QUrl())
        if self._temporary_dir is not None:
            shutil.rmtree(self._temporary_dir, ignore_errors=True)
            self._temporary_dir = None
        self.message.hide()
        self.retry_button.hide()
        self._set_ready(False)
        self.seek.setRange(0, 0)
        self.time_label.setText("00:00 / 00:00")

    def done(self, result):
        self._reset()
        super().done(result)

    def closeEvent(self, event):
        self._reset()
        super().closeEvent(event)
