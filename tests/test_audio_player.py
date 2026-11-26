from pathlib import Path
import time
import wave

import httpx
import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtTest import QTest

from sp.app.ui import audio_player_dialog
from sp.app.ui.attachments_panel import AttachmentsPanel
from sp.app.ui.voice_note_dialog import VoiceNoteDialog
from sp.app.ui.voice_recorder import VoiceEncoder, aac_available
from sp.app import config


@pytest.fixture(autouse=True)
def isolated_audio_preferences(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "GLOBAL_CONFIG", tmp_path / "preferences.json")


def wait_for(predicate):
    deadline = time.monotonic() + 5
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(10)
    assert predicate()


@pytest.fixture(autouse=True)
def muted_audio(monkeypatch):
    original = audio_player_dialog.QAudioOutput

    def output(parent):
        result = original(parent)
        result.setMuted(True)
        return result

    monkeypatch.setattr(audio_player_dialog, "QAudioOutput", output)


@pytest.fixture
def audio_file(tmp_path):
    path = tmp_path / "voice.wav"
    with wave.open(str(path), "wb") as audio:
        audio.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
        audio.writeframes(b"\0\0" * 96000)
    return path


@pytest.mark.parametrize("suffix", [".wav", ".m4a"])
def test_double_click_opens_audio_player_instead_of_system_app(qtbot, audio_file, monkeypatch, suffix):
    path = audio_file
    if suffix == ".m4a":
        if not aac_available():
            pytest.skip("This backend has no AAC encoder")
        encoder = VoiceEncoder()
        results, errors = [], []
        encoder.succeeded.connect(results.append)
        encoder.failed.connect(errors.append)
        encoder.start(path, path.with_suffix(".m4a"))
        wait_for(lambda: bool(results or errors))
        assert not errors
        path = results[0]
    page = audio_file.parent / "Page.md"
    page.write_text("# Page")
    panel = AttachmentsPanel()
    panel.resize(450, 350)
    qtbot.addWidget(panel)
    panel.set_page(page)
    external = []
    monkeypatch.setattr("sp.app.ui.attachments_panel.open_path_in_system", external.append)
    item = next(panel.attachments_list.item(row) for row in range(panel.attachments_list.count())
                if panel.attachments_list.item(row).text() == path.name)
    point = panel.attachments_list.visualItemRect(item).center()
    QTest.mouseClick(panel.attachments_list.viewport(), Qt.LeftButton, pos=point)
    QTest.mouseDClick(panel.attachments_list.viewport(), Qt.LeftButton, pos=point)
    popup = panel._audio_player
    assert popup is not None and popup.isVisible()
    assert popup.windowModality() == Qt.NonModal
    assert panel.isEnabled()
    assert popup.player.source().toLocalFile() == str(path)
    wait_for(lambda: popup.player.duration() >= 1900)
    assert popup.title.text() == path.name
    assert not external
    popup.close()
    assert panel._audio_player is None


def test_player_controls_seek_volume_and_close_release_file(qtbot, audio_file):
    popup = audio_player_dialog.AudioPlayerDialog()
    qtbot.addWidget(popup)
    popup.open_local(audio_file)
    wait_for(lambda: popup.player.duration() == 2000)
    assert popup.player.playbackState() == QMediaPlayer.PlayingState
    popup.play_button.click()
    assert popup.player.playbackState() == QMediaPlayer.PausedState
    popup.seek.setValue(1000)
    assert popup.player.position() == 1000
    assert popup.time_label.text() == "00:01 / 00:02"
    popup.volume.setValue(25)
    assert popup.output.volume() == pytest.approx(0.25)
    popup.play_button.click()
    assert popup.player.playbackState() == QMediaPlayer.PlayingState
    popup.stop_button.click()
    assert popup.player.playbackState() == QMediaPlayer.StoppedState
    assert popup.player.position() == 0
    player = popup.player
    popup.close()
    assert player.source().isEmpty()
    assert audio_file.exists()


def test_remote_audio_download_uses_authenticated_client_and_cleans_up(qtbot, audio_file):
    requests = []

    def response(request):
        requests.append(request)
        return httpx.Response(200, content=audio_file.read_bytes())

    client = httpx.Client(base_url="https://example.test", headers={"Authorization": "Bearer test"},
                          transport=httpx.MockTransport(response))
    popup = audio_player_dialog.AudioPlayerDialog()
    qtbot.addWidget(popup)
    popup.open_remote(client, "/Page/remote voice.wav")
    wait_for(lambda: popup.player.duration() == 2000)
    local = Path(popup.player.source().toLocalFile())
    assert requests[0].url.path == "/api/file/raw"
    assert requests[0].url.params["path"] == "/Page/remote voice.wav"
    assert requests[0].headers["Authorization"] == "Bearer test"
    assert local.read_bytes() == audio_file.read_bytes()
    assert popup.title.text() == "remote voice.wav"
    popup.close()
    assert not local.exists()
    client.close()


def test_failed_remote_download_can_retry(qtbot, audio_file):
    attempts = []

    def response(request):
        attempts.append(request)
        return httpx.Response(503) if len(attempts) == 1 else httpx.Response(200, content=audio_file.read_bytes())

    client = httpx.Client(base_url="https://example.test", transport=httpx.MockTransport(response))
    popup = audio_player_dialog.AudioPlayerDialog()
    qtbot.addWidget(popup)
    popup.open_remote(client, "/voice.wav")
    wait_for(lambda: popup.retry_button.isVisible())
    assert "503" in popup.message.text()
    assert not popup.play_button.isEnabled()
    popup.retry_button.click()
    wait_for(lambda: popup.player.duration() == 2000)
    assert len(attempts) == 2
    popup.close()
    client.close()


def test_recorder_is_modeless_and_can_be_dragged_by_heading(qtbot, monkeypatch):
    popup = VoiceNoteDialog(None, "/Page.md", lambda *args: None)
    qtbot.addWidget(popup)
    assert popup.windowModality() == Qt.NonModal
    monkeypatch.setattr(popup, "windowHandle", lambda: type("Window", (), {"startSystemMove": lambda self: False})())
    popup.move(100, 100)
    before = popup.pos()
    events = [
        (QEvent.MouseButtonPress, QPointF(300, 300), Qt.LeftButton, Qt.LeftButton),
        (QEvent.MouseMove, QPointF(350, 330), Qt.NoButton, Qt.LeftButton),
        (QEvent.MouseButtonRelease, QPointF(350, 330), Qt.LeftButton, Qt.NoButton),
    ]
    for kind, global_position, button, buttons in events:
        event = QMouseEvent(kind, QPointF(5, 5), global_position, button, buttons, Qt.NoModifier)
        QCoreApplication.sendEvent(popup.heading, event)
    assert popup.pos() == before + QPoint(50, 30)
    assert popup.recorder.state == "idle"
    popup.reject()
