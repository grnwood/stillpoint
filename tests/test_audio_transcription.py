from pathlib import Path
from threading import Event
import time
import wave

import httpx
import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog

from sp.app import config
from sp.app.ui import audio_player_dialog, audio_transcription


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "GLOBAL_CONFIG", tmp_path / "preferences.json")


@pytest.fixture
def audio(tmp_path):
    source = tmp_path / "note.wav"
    with wave.open(str(source), "wb") as output:
        output.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
        output.writeframes(b"\0\0" * 48000)
    return source


def wait_for(predicate):
    deadline = time.monotonic() + 5
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(10)
    assert predicate()


@pytest.mark.parametrize("auth,field,value,header", [
    ("openai", "api_key", "secret", "Authorization"),
    ("proxy", "api_secret", "secret", "x-api-secret"),
    ("custom", "custom_header_value", "secret", "x-speech-key"),
])
def test_transcription_uploads_audio_and_selected_model_with_existing_auth(audio, auth, field, value, header):
    requests = []

    def response(request):
        requests.append(request)
        return httpx.Response(200, json={"text": "  Hello from the recording.  "})

    server = {"base_url": "https://speech.example/v1", "auth_mode": auth,
              field: value, "custom_header_name": "x-speech-key"}
    result = audio_transcription.transcribe_audio(audio, server, "speech-model", "/v1/audio/transcriptions",
                                                 transport=httpx.MockTransport(response))
    assert result == "Hello from the recording."
    assert requests[0].url == "https://speech.example/v1/audio/transcriptions"
    assert requests[0].headers[header] == ("Bearer secret" if auth == "openai" else "secret")
    assert requests[0].headers["Content-Type"].startswith("multipart/form-data;")
    assert b'name="file"' in requests[0].content
    assert b"RIFF" in requests[0].content
    assert b"speech-model" in requests[0].content
    assert audio.exists()


def test_provider_error_explains_model_and_preserves_audio(audio):
    transport = httpx.MockTransport(lambda request: httpx.Response(400, json={"error": {"message": "Model does not support audio"}}))
    with pytest.raises(ValueError, match="Model does not support audio"):
        audio_transcription.transcribe_audio(audio, {"base_url": "https://speech.example"}, "chat-model",
                                             "/v1/audio/transcriptions", transport=transport)
    assert audio.exists()


@pytest.mark.parametrize("payload", [{}, {"text": ""}, {"text": ["not text"]}])
def test_non_transcription_response_is_rejected(audio, payload):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    with pytest.raises(ValueError, match="no transcript text"):
        audio_transcription.transcribe_audio(audio, {"base_url": "https://speech.example"}, "speech-model",
                                             "/v1/audio/transcriptions", transport=transport)


def test_transcription_path_cannot_redirect_credentials_to_another_server():
    with pytest.raises(ValueError, match="selected server"):
        audio_transcription.transcription_url({"base_url": "https://speech.example"}, "https://other.example/transcribe")
    assert audio_transcription.transcription_url({"base_url": "https://speech.example"}, "/custom/audio") == "https://speech.example/custom/audio"


def configure_transcription():
    config._update_global_config({"servers": [{"name": "Speech", "base_url": "https://speech.example", "auth_mode": "openai"}],
                                  "default_ai_model": "chat-model"})
    config.save_audio_settings({"transcription_server": "Speech", "transcription_model": "speech-model"})


def player(qtbot, audio):
    popup = audio_player_dialog.AudioPlayerDialog()
    popup.output.setMuted(True)
    qtbot.addWidget(popup)
    popup.open_local(audio)
    return popup


def test_transcribe_is_explicit_and_text_can_be_reviewed_copied_and_saved(qtbot, audio, monkeypatch, tmp_path):
    configure_transcription()
    calls = []

    def transcribe(source, server, model, path):
        calls.append((source, server["name"], model, path))
        return "Recognized words."

    monkeypatch.setattr(audio_transcription, "transcribe_audio", transcribe)
    popup = player(qtbot, audio)
    QTest.qWait(20)
    assert calls == []
    popup.transcribe_button.click()
    wait_for(lambda: popup.transcript.toPlainText() == "Recognized words.")
    assert calls == [(audio, "Speech", "speech-model", "/v1/audio/transcriptions")]
    assert popup.transcript_panel.isVisible()
    popup.transcript.setPlainText("Corrected words.")
    popup.copy_transcript_button.click()
    assert QApplication.clipboard().text() == "Corrected words."
    destination = tmp_path / "transcript.txt"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(destination), "Text files (*.txt)"))
    popup.save_transcript_button.click()
    assert destination.read_text() == "Corrected words."
    assert audio.exists()
    assert config.load_default_ai_model() == "chat-model"
    popup.close()


def test_unconfigured_transcription_does_not_send_audio(qtbot, audio, monkeypatch):
    calls = []
    monkeypatch.setattr(audio_transcription, "transcribe_audio", lambda *args: calls.append(args))
    popup = player(qtbot, audio)
    popup.transcribe_button.click()
    assert calls == []
    assert "Preferences → Audio" in popup.message.text()
    assert popup.transcribe_button.isEnabled()
    popup.close()


def test_failed_transcription_can_retry_without_losing_audio(qtbot, audio, monkeypatch):
    configure_transcription()
    attempts = []

    def transcribe(*args):
        attempts.append(args)
        if len(attempts) == 1:
            raise ValueError("Speech service unavailable")
        return "Retry succeeded."

    monkeypatch.setattr(audio_transcription, "transcribe_audio", transcribe)
    popup = player(qtbot, audio)
    popup.transcribe_button.click()
    wait_for(lambda: popup._transcription is None)
    assert "Speech service unavailable" in popup.message.text()
    assert audio.exists()
    popup.transcribe_button.click()
    wait_for(lambda: popup.transcript.toPlainText() == "Retry succeeded.")
    popup.close()


def test_closing_player_ignores_pending_transcription(qtbot, audio, monkeypatch):
    configure_transcription()
    entered, release, finished = Event(), Event(), Event()

    def transcribe(*args):
        entered.set()
        release.wait(3)
        finished.set()
        return "Late reply"

    monkeypatch.setattr(audio_transcription, "transcribe_audio", transcribe)
    popup = player(qtbot, audio)
    popup.transcribe_button.click()
    wait_for(entered.is_set)
    worker = popup._transcription
    popup.close()
    release.set()
    wait_for(finished.is_set)
    assert worker.cancelled.is_set()
    assert audio.exists()
