"""Audio defaults, with device-local identity and independent transcription models."""
from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtMultimedia import QMediaDevices
from PySide6.QtWidgets import QComboBox, QFormLayout, QLabel, QLineEdit, QPushButton, QSizePolicy, QSlider, QVBoxLayout, QWidget

from sp.app import config


def microphone_id(device) -> str:
    return bytes(device.id()).hex()


class TranscriptionModelCombo(QComboBox):
    def showPopup(self):
        longest = max((self.fontMetrics().horizontalAdvance(self.itemText(index))
                       for index in range(self.count())), default=0)
        available = self.screen().availableGeometry().width() - 32
        self.view().setMinimumWidth(min(available, max(self.width(), longest + 64, 360)))
        super().showPopup()


class AudioPreferences(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._settings = config.load_audio_settings()
        self.devices = QMediaDevices(self)
        self.devices.audioInputsChanged.connect(self._refresh_microphones)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.microphone = QComboBox()
        self.microphone.setAccessibleName("Preferred microphone")
        form.addRow("Microphone:", self.microphone)
        self._refresh_microphones(initial=True)
        self.recording_format = QComboBox()
        self.recording_format.addItem("M4A (WAV fallback when unavailable)", ".m4a")
        self.recording_format.addItem("WAV (uncompressed)", ".wav")
        self.recording_format.setCurrentIndex(self.recording_format.findData(self._settings["recording_format"]))
        form.addRow("Recording format:", self.recording_format)
        self.playback_volume = QSlider(Qt.Horizontal)
        self.playback_volume.setAccessibleName("Default playback volume")
        self.playback_volume.setRange(0, 100)
        self.playback_volume.setValue(self._settings["playback_volume"])
        form.addRow("Playback volume:", self.playback_volume)
        layout.addLayout(form)
        note = QLabel("The recorder also remembers microphone changes. If that device is unavailable, recording uses the system default until it returns.")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addSpacing(12)
        layout.addWidget(QLabel("<b>Transcription defaults</b>"))
        transcription = QVBoxLayout()
        self.transcription_server = QComboBox()
        self.transcription_server.addItem("Not configured", "")
        from .ai_chat_panel import ServerManager
        self._manager = ServerManager()
        names = self._manager.list_server_names()
        saved_server = self._settings["transcription_server"]
        for name in names:
            self.transcription_server.addItem(name, name)
        if saved_server and saved_server not in names:
            self.transcription_server.addItem(f"{saved_server} (unavailable)", saved_server)
        self.transcription_server.setCurrentIndex(max(0, self.transcription_server.findData(saved_server)))
        transcription.addWidget(QLabel("Transcription server:"))
        transcription.addWidget(self.transcription_server)
        self.transcription_model = TranscriptionModelCombo()
        self.transcription_model.setEditable(True)
        self.transcription_model.setInsertPolicy(QComboBox.NoInsert)
        self.transcription_model.lineEdit().setPlaceholderText("Select or enter a speech-to-text model")
        self.transcription_model.setAccessibleName("Default model for transcription")
        transcription.addWidget(QLabel("Default model for transcription:"))
        transcription.addWidget(self.transcription_model)
        self.transcription_path = QLineEdit(self._settings["transcription_path"])
        self.transcription_path.setAccessibleName("Transcription API path")
        transcription.addWidget(QLabel("Transcription API path:"))
        transcription.addWidget(self.transcription_path)
        for field in (self.transcription_server, self.transcription_model, self.transcription_path):
            field.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.transcription_server.currentIndexChanged.connect(self._refresh_models)
        self._refresh_models(initial=True)
        layout.addLayout(transcription)
        reload_button = QPushButton("Reload configured servers and models")
        reload_button.clicked.connect(self._reload_servers)
        layout.addWidget(reload_button)
        help_text = QLabel("Choose a speech-to-text model on a server with an OpenAI-compatible audio transcription endpoint. You can enter a model ID if it is absent from the cached list. Use Transcribe in the audio player to send a recording to this server. Recording and saving alone do not send audio to an AI service.")
        help_text.setWordWrap(True)
        layout.addWidget(help_text)
        layout.addStretch()

    def _refresh_microphones(self, *, initial=False):
        selected = self._settings["microphone_id"] if initial else self.microphone.currentData()
        name = self._settings["microphone_name"] if initial else self.microphone.currentData(Qt.UserRole + 1)
        blocker = QSignalBlocker(self.microphone)
        self.microphone.clear()
        self.microphone.addItem("System default", "")
        self.microphone.setItemData(0, "", Qt.UserRole + 1)
        for device in QMediaDevices.audioInputs():
            self.microphone.addItem(device.description(), microphone_id(device))
            self.microphone.setItemData(self.microphone.count() - 1, device.description(), Qt.UserRole + 1)
        index = self.microphone.findData(selected or "")
        if index < 0:
            self.microphone.addItem(f"{name or 'Saved microphone'} (unavailable)", selected)
            index = self.microphone.count() - 1
            self.microphone.setItemData(index, name or "", Qt.UserRole + 1)
        self.microphone.setCurrentIndex(index)
        del blocker

    def _refresh_models(self, _index=None, *, initial=False):
        desired = self._settings["transcription_model"] if initial else self.transcription_model.currentText().strip()
        name = self.transcription_server.currentData()
        payload = config._read_global_config()
        models = payload.get("server_models", {}).get(name, [])
        # Cached model lists do not reliably expose audio capabilities; never
        # silently choose the first (usually chat-only) model for transcription.
        self.transcription_model.clear()
        self.transcription_model.addItem("")
        self.transcription_model.addItems([str(model) for model in models if model])
        self.transcription_model.setCurrentText(desired)

    def _reload_servers(self):
        selected = self.transcription_server.currentData()
        blocker = QSignalBlocker(self.transcription_server)
        self.transcription_server.clear()
        self.transcription_server.addItem("Not configured", "")
        for name in self._manager.list_server_names():
            self.transcription_server.addItem(name, name)
        if selected and self.transcription_server.findData(selected) < 0:
            self.transcription_server.addItem(f"{selected} (unavailable)", selected)
        self.transcription_server.setCurrentIndex(max(0, self.transcription_server.findData(selected)))
        del blocker
        self._refresh_models()

    def save(self):
        config.save_audio_settings({
            "microphone_id": self.microphone.currentData() or "",
            "microphone_name": self.microphone.currentData(Qt.UserRole + 1) or "",
            "recording_format": self.recording_format.currentData(),
            "playback_volume": self.playback_volume.value(),
            "transcription_server": self.transcription_server.currentData() or "",
            "transcription_model": self.transcription_model.currentText().strip(),
            "transcription_path": self.transcription_path.text().strip() or "/v1/audio/transcriptions",
        })
