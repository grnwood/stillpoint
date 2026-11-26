from types import SimpleNamespace

import pytest
from PySide6.QtCore import QByteArray
from PySide6.QtWidgets import QDialogButtonBox, QMessageBox

from sp.app import config
from sp.app.ui.audio_preferences import AudioPreferences
from sp.app.ui.voice_note_dialog import VoiceNoteDialog


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "GLOBAL_CONFIG", tmp_path / "preferences.json")


def device(key, name):
    return SimpleNamespace(id=lambda: QByteArray(key), description=lambda: name, isNull=lambda: False)


def set_devices(monkeypatch, devices):
    from PySide6.QtMultimedia import QMediaDevices
    monkeypatch.setattr(QMediaDevices, "audioInputs", lambda: devices)
    monkeypatch.setattr(QMediaDevices, "defaultAudioInput", lambda: devices[0])


def test_last_selected_microphone_survives_new_dialog(qtbot, monkeypatch):
    inputs = [device(b"built-in", "Built-in"), device(b"usb", "USB microphone")]
    set_devices(monkeypatch, inputs)
    first = VoiceNoteDialog(None, "Page", lambda *args: None)
    qtbot.addWidget(first)
    first.microphone.setCurrentIndex(first.microphone.findText("USB microphone"))
    first.reject()
    assert config.load_audio_settings()["microphone_id"] == b"usb".hex()
    second = VoiceNoteDialog(None, "Page", lambda *args: None)
    qtbot.addWidget(second)
    assert second.microphone.currentText() == "USB microphone"
    second.reject()


def test_missing_microphone_falls_back_without_forgetting_preference(qtbot, monkeypatch):
    config.save_audio_settings({"microphone_id": b"usb".hex(), "microphone_name": "USB microphone"})
    inputs = [device(b"built-in", "Built-in")]
    set_devices(monkeypatch, inputs)
    popup = VoiceNoteDialog(None, "Page", lambda *args: None)
    qtbot.addWidget(popup)
    assert popup.microphone.currentText() == "System default"
    assert config.load_audio_settings()["microphone_id"] == b"usb".hex()
    inputs.append(device(b"usb", "USB microphone"))
    popup._devices_changed()
    assert popup.microphone.currentText() == "USB microphone"
    popup.microphone.setCurrentIndex(0)
    assert config.load_audio_settings()["microphone_id"] == ""
    popup.reject()


def test_device_reconnect_with_changed_id_uses_unique_name(qtbot, monkeypatch):
    config.save_audio_settings({"microphone_id": "old-id", "microphone_name": "USB microphone"})
    set_devices(monkeypatch, [device(b"new", "USB microphone")])
    popup = VoiceNoteDialog(None, "Page", lambda *args: None)
    qtbot.addWidget(popup)
    assert popup.microphone.currentText() == "USB microphone"
    popup.reject()


def test_audio_settings_preserve_transcription_when_microphone_changes():
    config.save_audio_settings({"transcription_server": "Speech server", "transcription_model": "speech-model"})
    config.save_audio_settings({"microphone_id": "0123", "microphone_name": "USB"})
    assert config.load_audio_settings()["transcription_server"] == "Speech server"
    assert config.load_audio_settings()["transcription_model"] == "speech-model"


def test_audio_preferences_keep_missing_device_and_custom_model(qtbot, monkeypatch):
    config.save_audio_settings({"microphone_id": "absent", "microphone_name": "USB", "transcription_server": "Missing server", "transcription_model": "custom-audio-model"})
    set_devices(monkeypatch, [])
    widget = AudioPreferences()
    qtbot.addWidget(widget)
    assert widget.microphone.currentData() == "absent"
    assert "unavailable" in widget.microphone.currentText()
    assert widget.transcription_model.currentText() == "custom-audio-model"
    widget.playback_volume.setValue(30)
    widget.recording_format.setCurrentIndex(widget.recording_format.findData(".wav"))
    widget.save()
    saved = config.load_audio_settings()
    assert saved["microphone_id"] == "absent"
    assert saved["transcription_model"] == "custom-audio-model"
    assert saved["recording_format"] == ".wav"
    assert saved["playback_volume"] == 30


def test_transcription_model_is_separate_and_not_automatically_chat_model(qtbot, monkeypatch):
    from sp.app.ui.ai_chat_panel import ServerManager
    monkeypatch.setattr(ServerManager, "list_server_names", lambda self: ["Speech server"])
    config._update_global_config({"default_ai_model": "chat-model", "server_models": {"Speech server": ["chat-model", "speech-model"]}})
    widget = AudioPreferences()
    qtbot.addWidget(widget)
    widget.transcription_server.setCurrentIndex(widget.transcription_server.findData("Speech server"))
    assert widget.transcription_model.currentText() == ""
    widget.transcription_model.setCurrentText("speech-model")
    widget.save()
    assert config.load_default_ai_model() == "chat-model"
    assert config.load_audio_settings()["transcription_model"] == "speech-model"


def test_preferences_audio_section_saves_only_on_ok(qtbot, monkeypatch):
    from sp.app.ui.preferences_dialog import PreferencesDialog
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: QMessageBox.Ok)
    monkeypatch.setattr(QMessageBox, "information", lambda *args: QMessageBox.Ok)
    dialog = PreferencesDialog()
    qtbot.addWidget(dialog)
    assert "Audio" in [dialog.section_list.item(index).text() for index in range(dialog.section_list.count())]
    dialog.audio_preferences.playback_volume.setValue(20)
    dialog.reject()
    assert config.load_audio_settings()["playback_volume"] == 75
    second = PreferencesDialog()
    qtbot.addWidget(second)
    second.audio_preferences.playback_volume.setValue(20)
    second.button_box.button(QDialogButtonBox.Ok).click()
    assert config.load_audio_settings()["playback_volume"] == 20


def test_recording_and_player_honor_audio_defaults(qtbot, monkeypatch):
    from sp.app.ui.audio_player_dialog import AudioPlayerDialog
    config.save_audio_settings({"recording_format": ".wav", "playback_volume": 40})
    recorder = VoiceNoteDialog(None, "Page", lambda *args: None)
    qtbot.addWidget(recorder)
    assert recorder.file_format.currentData() == ".wav"
    assert recorder.output.volume() == pytest.approx(0.4)
    player = AudioPlayerDialog()
    qtbot.addWidget(player)
    assert player.volume.value() == 40
    player.volume.setValue(35)
    assert config.load_audio_settings()["playback_volume"] == 35
    player.close()
    recorder.reject()
