from array import array
from pathlib import Path
from types import SimpleNamespace
import struct
import time
import wave

import httpx
import pytest
from PySide6.QtCore import QByteArray, QObject, Signal
from PySide6.QtMultimedia import QAudioDecoder, QAudioFormat
from PySide6.QtCore import QUrl
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox

from sp.app.ui import voice_recorder
from sp.app.ui.attachments_panel import AttachmentsPanel
from sp.app.ui.voice_note_dialog import VoiceNoteDialog, voice_filename
from sp.app import config


@pytest.fixture(autouse=True)
def isolated_audio_preferences(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "GLOBAL_CONFIG", tmp_path / "preferences.json")


def wait_for(predicate):
    deadline = time.monotonic() + 5
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(10)
    assert predicate()


@pytest.mark.parametrize("kind,code,values", [
    (QAudioFormat.Int16, "h", [-32768, 0, 32767]),
    (QAudioFormat.Int32, "i", [-2147483648, 0, 2147483647]),
    (QAudioFormat.Float, "f", [-1, 0, 1]),
    (QAudioFormat.UInt8, "B", [0, 128, 255]),
])
def test_device_samples_convert_to_valid_pcm_and_meter(kind, code, values):
    data, peak = voice_recorder.pcm16(array(code, values).tobytes(), kind)
    samples = struct.unpack("<hhh", data)
    assert samples[0] == -32768
    assert samples[1] == 0
    assert samples[2] >= 32512
    assert peak == 1


class FakeInput(QObject):
    readyRead = Signal()

    def __init__(self):
        super().__init__()
        self.data = b""

    def feed(self, data):
        self.data += data
        self.readyRead.emit()

    def readAll(self):
        data, self.data = self.data, b""
        return QByteArray(data)


class FakeSource(QObject):
    stateChanged = Signal(object)

    def __init__(self, device, fmt, parent):
        super().__init__(parent)
        self.input = FakeInput()
        self.error_code = 0
        self.suspended = False

    def start(self):
        return self.input

    def stop(self):
        pass

    def suspend(self):
        self.suspended = True

    def resume(self):
        self.suspended = False

    def error(self):
        return SimpleNamespace(value=self.error_code)


@pytest.fixture
def recorder(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(voice_recorder, "QAudioSource", FakeSource)
    recorder = voice_recorder.VoiceRecorder()
    device = SimpleNamespace(isNull=lambda: False, isFormatSupported=lambda fmt: True)
    recorder.start(device, tmp_path / "draft.wav")
    yield recorder
    recorder.stop()


def test_pause_excludes_samples_and_timer_and_stop_finalizes_wav(recorder):
    source = recorder._source
    source.input.feed(struct.pack("<h", 10000) * 48000)
    assert recorder.duration_ms == 1000
    recorder.pause()
    assert source.suspended
    source.input.feed(struct.pack("<h", -10000) * 48000)
    assert recorder.duration_ms == 1000
    recorder.resume()
    source.input.feed(struct.pack("<h", 20000) * 48000)
    recorder.stop()
    with wave.open(str(recorder.path)) as audio:
        assert audio.getnframes() == 96000
        assert audio.getnchannels() == 1
        assert audio.getsampwidth() == 2
        assert audio.getframerate() == 48000
        assert audio.readframes(48000) == struct.pack("<h", 10000) * 48000
        assert audio.readframes(48000) == struct.pack("<h", 20000) * 48000
    assert recorder.duration_ms == 2000
    assert recorder.state == "stopped"
    assert recorder._source is None


def test_partial_samples_are_preserved_between_device_reads(recorder):
    recorder._input.feed(b"\x10")
    assert recorder.frames == 0
    recorder._input.feed(b"\x00")
    recorder.stop()
    with wave.open(str(recorder.path)) as audio:
        assert audio.readframes(1) == b"\x10\x00"


def test_microphone_failure_retains_captured_audio(recorder):
    errors = []
    recorder.failed.connect(errors.append)
    recorder._input.feed(struct.pack("<h", 1234) * 480)
    recorder._source.error_code = 1
    recorder._source.stateChanged.emit(None)
    assert errors and "retained" in errors[0]
    assert recorder.state == "stopped"
    with wave.open(str(recorder.path)) as audio:
        assert audio.getnframes() == 480


def seed_draft(dialog):
    import tempfile
    directory = Path(tempfile.mkdtemp(prefix="stillpoint-voice-test-"))
    dialog._draft_dir = directory
    recorder = dialog.recorder
    recorder.path = directory / "recording.wav"
    recorder.format.setSampleRate(48000)
    recorder.format.setChannelCount(1)
    recorder.frames = 4800
    recorder.state = "stopped"
    with wave.open(str(recorder.path), "wb") as audio:
        audio.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
        audio.writeframes(struct.pack("<h", 1000) * recorder.frames)
    dialog.file_format.setCurrentIndex(dialog.file_format.findData(".wav"))
    dialog._update_controls()
    return directory


def test_dialog_does_not_open_microphone_until_record(qtbot, monkeypatch):
    opened = []
    monkeypatch.setattr(voice_recorder.VoiceRecorder, "start", lambda *args: opened.append(args))
    dialog = VoiceNoteDialog(None, "Page", lambda *args: None)
    qtbot.addWidget(dialog)
    assert opened == []
    assert not dialog.save_button.isEnabled()
    assert not dialog.pause_button.isEnabled()
    assert dialog.recorder.state == "idle"
    dialog.reject()


def test_failed_save_retains_draft_and_can_retry(qtbot):
    attempts = []

    def save(path, name):
        attempts.append((path, name))
        if len(attempts) == 1:
            raise OSError("Disk full")
        return name

    dialog = VoiceNoteDialog(None, "Page", save)
    qtbot.addWidget(dialog)
    directory = seed_draft(dialog)
    draft = dialog.recorder.path
    saved = []
    dialog.attachmentSaved.connect(saved.append)
    dialog.save_button.click()
    wait_for(lambda: not dialog._busy)
    assert draft.exists()
    assert "Disk full" in dialog.error_label.text()
    assert dialog.save_button.isEnabled()
    dialog.save_button.click()
    wait_for(lambda: bool(saved))
    assert len(attempts) == 2
    assert not directory.exists()


def test_discard_needs_explicit_choice(qtbot, monkeypatch):
    dialog = VoiceNoteDialog(None, "Page", lambda *args: None)
    qtbot.addWidget(dialog)
    directory = seed_draft(dialog)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Cancel)
    dialog.reject()
    assert dialog.isVisible() and directory.exists()
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Discard)
    dialog.reject()
    assert not directory.exists()


def test_local_recording_saved_as_attachment_without_overwrite(qtbot, tmp_path):
    page = tmp_path / "Page.md"
    page.write_text("# Page")
    panel = AttachmentsPanel()
    qtbot.addWidget(panel)
    assert not panel.record_button.isEnabled()
    panel.set_page(page)
    assert panel.record_button.isEnabled()
    changes = []
    panel.attachmentsModified.connect(changes.append)
    panel.record_button.click()
    dialog = panel._voice_dialog
    directory = seed_draft(dialog)
    existing = tmp_path / dialog.filename.text()
    existing.write_bytes(b"existing attachment")
    dialog.save_button.click()
    wait_for(lambda: panel._voice_dialog is None)
    assert existing.read_bytes() == b"existing attachment"
    destination = existing.with_name(existing.stem + " (1).wav")
    with wave.open(str(destination)) as audio:
        assert audio.getnframes() == 4800
    assert page.read_text() == "# Page"
    assert not directory.exists()
    assert changes == ["voice note recorded"]
    assert panel.attachments_list.selectedItems()[0].text() == destination.name
    panel.set_page(None)
    assert not panel.record_button.isEnabled()


def test_recording_destination_stays_bound_to_original_page(qtbot, tmp_path):
    first, second = tmp_path / "First", tmp_path / "Second"
    first.mkdir()
    second.mkdir()
    page = first / "First.md"
    page.write_text("# First")
    other_page = second / "Second.md"
    other_page.write_text("# Second")
    panel = AttachmentsPanel()
    qtbot.addWidget(panel)
    panel.set_page(page)
    panel.record_button.click()
    dialog = panel._voice_dialog
    seed_draft(dialog)
    filename = dialog.filename.text()
    panel.set_page(other_page)
    dialog.save_button.click()
    wait_for(lambda: panel._voice_dialog is None)
    assert (first / filename).exists()
    assert not (second / filename).exists()


def test_upload_auth_retry_runs_prompt_and_reopens_draft(qtbot):
    attempts, prompts, saved = [], [], []

    def upload(path, name):
        attempts.append(path.read_bytes())
        if len(attempts) == 1:
            response = httpx.Response(401, request=httpx.Request("POST", "https://example.test/files/attach"))
            response.raise_for_status()
        return name

    dialog = VoiceNoteDialog(None, "Page", upload, auth_prompt=lambda: prompts.append(True) or True)
    qtbot.addWidget(dialog)
    seed_draft(dialog)
    dialog.attachmentSaved.connect(saved.append)
    dialog.save_button.click()
    wait_for(lambda: bool(saved))
    assert prompts == [True]
    assert len(attempts) == 2 and attempts[0] == attempts[1]


def test_remote_recording_uses_original_page_and_retains_draft_on_failure(qtbot, tmp_path, monkeypatch):
    uploads = []
    entries = []

    def respond(request):
        if request.method == "GET":
            return httpx.Response(200, json={"attachments": entries})
        uploads.append(request.content)
        if len(uploads) == 1:
            return httpx.Response(503)
        entries.append({"attachment_path": "/Page/voice.wav"})
        return httpx.Response(200, json={"ok": True})

    client = httpx.Client(transport=httpx.MockTransport(respond), base_url="https://example.test")
    panel = AttachmentsPanel(api_client=client)
    panel.set_remote_mode(True, "https://example.test")
    panel.set_remote_vault_root("/vault")
    monkeypatch.setattr(panel, "_get_remote_cache_root", lambda: tmp_path)
    qtbot.addWidget(panel)
    panel.set_page(Path("/vault/Page/Page.md"))
    panel.record_button.click()
    dialog = panel._voice_dialog
    directory = seed_draft(dialog)
    dialog.save_button.click()
    wait_for(lambda: not dialog._busy)
    assert directory.exists()
    assert "503" in dialog.error_label.text()
    dialog.save_button.click()
    wait_for(lambda: panel._voice_dialog is None)
    assert not directory.exists()
    assert len(uploads) == 2
    assert b"/Page/Page.md" in uploads[0] and b"/Page/Page.md" in uploads[1]
    assert b"RIFF" in uploads[1]
    client.close()


def test_timestamp_filenames_are_unique_and_portable():
    first, second = voice_filename(".wav"), voice_filename(".wav")
    assert first != second
    assert first.startswith("voice-") and first.endswith(".wav")
    assert not any(character in first for character in ':\\/')


def test_missing_aac_encoder_offers_wav(qtbot, monkeypatch):
    monkeypatch.setattr("sp.app.ui.voice_note_dialog.aac_available", lambda: False)
    dialog = VoiceNoteDialog(None, "Page", lambda *args: None)
    qtbot.addWidget(dialog)
    assert dialog.file_format.count() == 1
    assert dialog.filename.text().endswith(".wav")
    dialog.reject()


def test_permission_denial_never_opens_capture(qtbot, monkeypatch):
    from sp.app.ui import voice_note_dialog
    from PySide6.QtCore import Qt

    dialog = VoiceNoteDialog(None, "Page", lambda *args: None)
    qtbot.addWidget(dialog)
    opened = []
    monkeypatch.setattr(dialog.recorder, "start", lambda *args: opened.append(args))
    monkeypatch.setattr(voice_note_dialog, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(voice_note_dialog, "microphone_usage_description", lambda: "Record voice notes")
    application = SimpleNamespace(checkPermission=lambda permission: Qt.PermissionStatus.Denied)
    monkeypatch.setattr(voice_note_dialog, "QCoreApplication", SimpleNamespace(instance=lambda: application))
    dialog._record()
    assert opened == []
    assert dialog.recorder.state == "idle"
    assert "denied" in dialog.error_label.text()
    dialog.reject()


def test_missing_macos_usage_declaration_does_not_request_permission(qtbot, monkeypatch):
    from sp.app.ui import voice_note_dialog

    dialog = VoiceNoteDialog(None, "Page", lambda *args: None)
    qtbot.addWidget(dialog)
    monkeypatch.setattr(voice_note_dialog, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(voice_note_dialog, "microphone_usage_description", lambda: None)
    calls = []
    application = SimpleNamespace(checkPermission=lambda permission: calls.append(permission))
    monkeypatch.setattr(voice_note_dialog, "QCoreApplication", SimpleNamespace(instance=lambda: application))
    dialog._record()
    assert calls == []
    assert not dialog._permission_pending
    assert "run-macos-dev.py" in dialog.error_label.text()
    dialog.reject()


def test_shutdown_finalizes_and_retains_unsaved_draft(qtbot, monkeypatch):
    dialog = VoiceNoteDialog(None, "Page", lambda *args: None)
    qtbot.addWidget(dialog)
    directory = seed_draft(dialog)
    dialog._shutdown()
    assert directory.exists() and dialog.recorder.path.exists()
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Discard)
    dialog.reject()


def test_encoding_failure_retains_wav_and_selects_fallback(qtbot, monkeypatch):
    monkeypatch.setattr("sp.app.ui.voice_note_dialog.aac_available", lambda: True)
    dialog = VoiceNoteDialog(None, "Page", lambda *args: None)
    qtbot.addWidget(dialog)
    directory = seed_draft(dialog)
    dialog.file_format.setCurrentIndex(dialog.file_format.findData(".m4a"))
    monkeypatch.setattr(voice_recorder.VoiceEncoder, "start",
                        lambda encoder, *args: encoder.failed.emit("Encoder unavailable"))
    dialog.save_button.click()
    assert directory.exists()
    assert dialog.recorder.path.exists()
    assert dialog.file_format.currentData() == ".wav"
    assert dialog.save_button.isEnabled()
    assert "Encoder unavailable" in dialog.error_label.text()
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Discard)
    dialog.reject()


def test_dialog_saves_m4a_after_encoding(qtbot):
    if not voice_recorder.aac_available():
        pytest.skip("This Qt backend does not provide AAC encoding")
    outputs = []

    def save(source, name):
        outputs.append((name, source.read_bytes()))
        return name

    dialog = VoiceNoteDialog(None, "Page", save)
    qtbot.addWidget(dialog)
    directory = seed_draft(dialog)
    dialog.file_format.setCurrentIndex(dialog.file_format.findData(".m4a"))
    complete = []
    dialog.attachmentSaved.connect(complete.append)
    dialog.save_button.click()
    wait_for(lambda: bool(complete))
    assert outputs[0][0].endswith(".m4a")
    assert b"ftyp" in outputs[0][1][:32]
    assert not directory.exists()


def test_disconnected_microphone_stops_and_retains_draft(qtbot, monkeypatch):
    from sp.app.ui import voice_note_dialog

    dialog = VoiceNoteDialog(None, "Page", lambda *args: None)
    qtbot.addWidget(dialog)
    directory = seed_draft(dialog)
    dialog.microphone.addItem("USB microphone", SimpleNamespace(id=lambda: b"usb", description=lambda: "USB microphone"))
    dialog.microphone.setCurrentIndex(dialog.microphone.count() - 1)
    dialog.recorder.state = "recording"
    monkeypatch.setattr(voice_note_dialog, "QMediaDevices", SimpleNamespace(audioInputs=lambda: []))
    dialog._devices_changed()
    assert dialog.recorder.state == "stopped"
    assert dialog.save_button.isEnabled()
    assert "disconnected" in dialog.error_label.text()
    assert directory.exists()
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Discard)
    dialog.reject()


def test_qt_encodes_complete_playable_m4a_without_microphone(qapp, tmp_path):
    if not voice_recorder.aac_available():
        pytest.skip("This Qt backend does not provide AAC encoding")
    source = tmp_path / "draft.wav"
    with wave.open(str(source), "wb") as audio:
        audio.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
        audio.writeframes(struct.pack("<h", 1000) * 48000)
    encoder = voice_recorder.VoiceEncoder()
    complete, errors = [], []
    encoder.succeeded.connect(complete.append)
    encoder.failed.connect(errors.append)
    encoder.start(source, tmp_path / "voice.m4a")
    wait_for(lambda: bool(complete or errors))
    assert errors == []
    assert source.exists()
    assert complete[0].stat().st_size > 0
    decoder = QAudioDecoder()
    decoded = []
    finished = []
    decoder.bufferReady.connect(lambda: decoded.append(decoder.read().duration()))
    decoder.finished.connect(lambda: finished.append(True))
    decoder.setSource(QUrl.fromLocalFile(str(complete[0])))
    decoder.start()
    wait_for(lambda: bool(finished))
    # AAC can include a small amount of encoder padding, but no lost chunks.
    assert 950000 <= sum(decoded) <= 1100000
    decoder.stop()


def test_recording_destination_uses_vault_colon_link(qtbot, tmp_path):
    folder = tmp_path / "Projects" / "Meeting"
    folder.mkdir(parents=True)
    page = folder / "Meeting.md"
    page.write_text("# Meeting")
    panel = AttachmentsPanel()
    qtbot.addWidget(panel)
    panel.set_vault_root(str(tmp_path))
    panel.set_page(page)
    panel.record_button.click()
    dialog = panel._voice_dialog
    assert dialog.destination.text() == "Attach to: Projects:Meeting"
    assert all(not button.icon().isNull() for button in (
        dialog.record_button, dialog.pause_button, dialog.stop_button, dialog.play_button,
    ))
    dialog.reject()
