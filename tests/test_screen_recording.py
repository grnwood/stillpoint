from pathlib import Path
from types import SimpleNamespace
import time

import pytest
from PySide6.QtCore import QAbstractAnimation, QPoint, QSettings, QUrl, Qt
from PySide6.QtMultimedia import QMediaRecorder
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox

from sp.app import config
from sp.app.ui.attachments_panel import AttachmentsPanel
from sp.app.ui.screen_record_dialog import ScreenRecordDialog


@pytest.fixture(autouse=True)
def isolated_preferences(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "GLOBAL_CONFIG", tmp_path / "preferences.json")
    monkeypatch.setattr("sp.app.ui.screen_record_dialog.QSettings",
                        lambda *args: QSettings(str(tmp_path / "recorder.ini"), QSettings.IniFormat))


def wait_for(predicate):
    deadline = time.monotonic() + 5
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(10)
    assert predicate()


def draft(dialog, tmp_path):
    directory = tmp_path / "draft"
    directory.mkdir()
    dialog._draft_dir = directory
    dialog._path = directory / "screen.mp4"
    dialog._path.write_bytes(b"video draft")
    dialog._state = "stopped"
    dialog.filename.setText(dialog._path.name)
    dialog._update_controls()
    return dialog._path


def test_open_does_not_start_capture_and_controls_are_icon_only(qtbot, monkeypatch):
    dialog = ScreenRecordDialog(None, "Projects:Demo", lambda *args: None)
    qtbot.addWidget(dialog)
    assert not dialog.capture.isActive()
    assert dialog.microphone.currentData() is None
    assert dialog.destination.text() == "Attach to: Projects:Demo"
    assert not dialog.save_button.isEnabled()
    for button in (dialog.record_button, dialog.pause_button, dialog.stop_button, dialog.play_button):
        assert button.text() == ""
        assert not button.icon().isNull()
        assert button.iconSize().width() == 32
        assert button.accessibleName()
    dialog.reject()


def test_countdown_waits_for_first_frame_and_can_be_cancelled(qtbot, monkeypatch):
    dialog = ScreenRecordDialog(None, "Demo", lambda *args: None)
    qtbot.addWidget(dialog)
    started = []
    monkeypatch.setattr(dialog.capture, "start", lambda: started.append(True))
    dialog._record()
    assert started == [True]
    assert dialog._state == "waiting"
    assert not dialog._countdown.isActive()
    dialog._frame_ready(SimpleNamespace(isValid=lambda: True))
    assert dialog._state == "countdown"
    assert dialog._count == 3
    dialog._stop()
    assert dialog._state == "idle"
    assert not dialog._countdown.isActive()
    dialog.reject()


def test_record_pause_resume_stop_and_review(qtbot, monkeypatch, tmp_path):
    dialog = ScreenRecordDialog(None, "Demo", lambda *args: None)
    qtbot.addWidget(dialog)
    monkeypatch.setattr(dialog.capture, "start", lambda: None)
    monkeypatch.setattr(dialog.capture, "stop", lambda: None)
    monkeypatch.setattr(dialog.player, "setSource", lambda url: None)
    monkeypatch.setattr(dialog.recorder, "actualLocation", lambda: QUrl())
    monkeypatch.setattr(dialog.recorder, "record", lambda: dialog._recorder_state_changed(QMediaRecorder.RecordingState))
    monkeypatch.setattr(dialog.recorder, "pause", lambda: dialog._recorder_state_changed(QMediaRecorder.PausedState))
    monkeypatch.setattr(dialog.recorder, "stop", lambda: dialog._recorder_state_changed(QMediaRecorder.StoppedState))
    dialog._record()
    dialog._frame_ready(SimpleNamespace(isValid=lambda: True))
    dialog._countdown_tick()
    dialog._countdown_tick()
    assert dialog._state == "countdown"
    dialog._countdown_tick()
    assert dialog._state == "recording"
    assert dialog.video.isHidden()
    assert dialog.actions.isHidden()
    dialog._path.write_bytes(b"test recording")
    dialog._pause_resume()
    assert dialog._state == "paused"
    assert dialog.pause_button.toolTip() == "Resume"
    dialog._pause_resume()
    assert dialog._state == "recording"
    dialog._stop()
    assert dialog._state == "stopped"
    assert dialog.video.isHidden()
    assert dialog.review_video is not None and not dialog.review_video.isHidden()
    assert dialog.player.videoOutput() is dialog.review_video
    assert dialog.session.videoOutput() is None
    dialog.stop_button.click()
    assert dialog.video.isHidden()
    assert not dialog.review_video.isHidden()
    assert dialog.save_button.isEnabled()
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Discard)
    dialog.reject()


def test_save_failure_keeps_draft_and_retry_succeeds(qtbot, monkeypatch, tmp_path):
    attempts, saved = [], []

    def save(path, name):
        attempts.append(path.read_bytes())
        if len(attempts) == 1:
            raise OSError("Disk full")
        return name

    dialog = ScreenRecordDialog(None, "Demo", save)
    qtbot.addWidget(dialog)
    monkeypatch.setattr(dialog.player, "setSource", lambda url: None)
    path = draft(dialog, tmp_path)
    dialog.attachmentSaved.connect(saved.append)
    dialog._save()
    wait_for(lambda: dialog._state == "stopped")
    assert path.exists()
    assert "Disk full" in dialog.error_label.text()
    assert dialog.save_button.isEnabled()
    dialog._save()
    wait_for(lambda: bool(saved))
    assert attempts == [b"video draft", b"video draft"]
    assert not path.exists()


def test_screen_attachment_stays_on_original_page(qtbot, monkeypatch, tmp_path):
    pages = []
    for name in ("First", "Second"):
        folder = tmp_path / name
        folder.mkdir()
        page = folder / f"{name}.md"
        page.write_text(name)
        pages.append(page)
    panel = AttachmentsPanel()
    qtbot.addWidget(panel)
    panel.set_vault_root(str(tmp_path))
    assert not panel.screen_record_button.isEnabled()
    panel.set_page(pages[0])
    assert panel.screen_record_button.isEnabled()
    panel.screen_record_button.click()
    dialog = panel._screen_dialog
    assert dialog.destination.text() == "Attach to: First"
    path = draft(dialog, tmp_path)
    existing = pages[0].parent / path.name
    existing.write_bytes(b"existing")
    panel.set_page(pages[1])
    dialog._save()
    wait_for(lambda: panel._screen_dialog is None)
    assert existing.read_bytes() == b"existing"
    assert (existing.parent / "screen (1).mp4").read_bytes() == b"video draft"
    assert not (pages[1].parent / "screen.mp4").exists()
    panel.set_page(None)
    assert not panel.screen_record_button.isEnabled()


def test_screen_recording_is_local_only(qtbot, tmp_path):
    page = tmp_path / "Page.md"
    page.write_text("Page")
    panel = AttachmentsPanel()
    qtbot.addWidget(panel)
    panel.set_page(page)
    panel.set_remote_mode(True, "https://example.test")
    assert not panel.screen_record_button.isEnabled()
    panel._record_screen()
    assert panel._screen_dialog is None


def test_capture_failure_resets_pending_record(qtbot, monkeypatch):
    dialog = ScreenRecordDialog(None, "Demo", lambda *args: None)
    qtbot.addWidget(dialog)
    monkeypatch.setattr(dialog.capture, "start", lambda: None)
    dialog._record()
    dialog._fail("Permission denied")
    assert dialog._state == "idle"
    assert not dialog._pending_record
    assert not dialog._start_timeout.isActive()
    assert "Permission denied" in dialog.error_label.text()
    dialog.reject()


def test_no_encoder_disables_recording(qtbot, monkeypatch):
    monkeypatch.setattr("sp.app.ui.screen_record_dialog.recording_format", lambda: None)
    dialog = ScreenRecordDialog(None, "Demo", lambda *args: None)
    qtbot.addWidget(dialog)
    assert not dialog.record_button.isEnabled()
    assert "No video encoder" in dialog.error_label.text()
    dialog.reject()


def test_video_player_uses_video_output(qtbot):
    from sp.app.ui.audio_player_dialog import AudioPlayerDialog
    dialog = AudioPlayerDialog(video=True)
    qtbot.addWidget(dialog)
    assert dialog.player.videoOutput() == dialog.video_widget
    assert dialog.transcribe_button.isHidden()
    dialog.reject()


def test_encoder_failure_tries_another_codec(qtbot, monkeypatch):
    from sp.app.ui.screen_record_dialog import recording_formats
    dialog = ScreenRecordDialog(None, "Demo", lambda *args: None)
    qtbot.addWidget(dialog)
    formats = recording_formats()
    if len(formats) < 2:
        pytest.skip("Only one encoder available")
    dialog._format_attempts.add((dialog._format.fileFormat(), dialog._format.videoCodec()))
    dialog._state = "starting"
    monkeypatch.setattr(dialog.recorder, "stop", lambda: None)
    attempts = []
    monkeypatch.setattr(dialog, "_start_recording", lambda: attempts.append(dialog._format.videoCodec()))
    dialog._encoder_error(None, "Hardware encoder unavailable")
    assert dialog._state == "retrying"
    wait_for(lambda: bool(attempts))
    assert (dialog._format.fileFormat(), dialog._format.videoCodec()) not in dialog._format_attempts
    dialog._state = "idle"
    dialog.reject()


def test_failed_encoder_does_not_offer_corrupt_draft_for_save(qtbot, monkeypatch, tmp_path):
    dialog = ScreenRecordDialog(None, "Demo", lambda *args: None)
    qtbot.addWidget(dialog)
    draft(dialog, tmp_path)
    dialog._state = "starting"
    monkeypatch.setattr("sp.app.ui.screen_record_dialog.recording_formats", lambda: [])
    monkeypatch.setattr(dialog.recorder, "stop", lambda: None)
    monkeypatch.setattr(dialog.recorder, "actualLocation", lambda: QUrl())
    dialog._encoder_error(None, "No encoder")
    assert dialog._recording_failed
    assert not dialog.save_button.isEnabled()
    assert "No encoder" in dialog.error_label.text()
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Discard)
    dialog.reject()



def test_compact_bar_is_icon_only_at_bottom_center_and_restores_window(qtbot):
    dialog = ScreenRecordDialog(None, "Demo", lambda *args: None)
    qtbot.addWidget(dialog)
    dialog.show()
    original = dialog.geometry()
    dialog._compact(True)
    assert dialog.size().width() == 164
    assert dialog.size().height() == 36
    for widget in (dialog.heading, dialog.destination, dialog.settings, dialog.video,
                   dialog.status, dialog.timer_label, dialog.filename, dialog.actions,
                   dialog.record_button, dialog.play_button, dialog.seek, dialog.error_label):
        assert widget.isHidden()
    assert not dialog.pause_button.isHidden()
    assert not dialog.stop_button.isHidden()
    available = dialog.screen.currentData().availableGeometry()
    assert abs(dialog.geometry().center().x() - available.center().x()) <= 1
    assert dialog.geometry().bottom() == available.bottom() - 12
    dialog._compact(False)
    assert dialog.geometry() == original
    assert not dialog.heading.isHidden()
    assert dialog.pause_button.width() == 52
    dialog.reject()


def test_dragged_recording_bar_position_is_reused(qtbot):
    dialog = ScreenRecordDialog(None, "Demo", lambda *args: None)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog._compact(True)
    moved = QPoint(80, 120)
    dialog.move(moved)
    moved = dialog.geometry().topLeft()
    wait_for(lambda: dialog._bar_settings.value("screenRecording/toolbarPosition") == moved)
    dialog._compact(False)
    dialog.reject()
    another = ScreenRecordDialog(None, "Demo", lambda *args: None)
    qtbot.addWidget(another)
    another.show()
    another._compact(True)
    assert another.geometry().topLeft() == moved
    another._compact(False)
    another.reject()


def test_recording_bar_returns_to_visible_screen_after_monitor_removed(qtbot):
    dialog = ScreenRecordDialog(None, "Demo", lambda *args: None)
    qtbot.addWidget(dialog)
    dialog._bar_settings.setValue("screenRecording/toolbarPosition", QPoint(-100000, -100000))
    dialog._compact(True)
    available = dialog.screen.currentData().availableGeometry()
    assert available.contains(dialog.geometry())
    dialog._compact(False)
    dialog.reject()


def test_countdown_shows_large_numbers_and_cancel_restores_preview(qtbot, monkeypatch):
    dialog = ScreenRecordDialog(None, "Demo", lambda *args: None)
    qtbot.addWidget(dialog)
    dialog._begin_countdown()
    assert dialog.countdown_label.text() == "3"
    assert not dialog.countdown_label.isHidden()
    assert dialog.video.isHidden()
    dialog._countdown_tick()
    assert dialog.countdown_label.text() == "2"
    dialog._countdown_tick()
    assert dialog.countdown_label.text() == "1"
    dialog._stop()
    assert dialog.countdown_label.isHidden()
    assert not dialog.video.isHidden()
    dialog.reject()


def test_recording_bar_animates_then_locks_size_and_shows_elapsed_time(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Discard)
    dialog = ScreenRecordDialog(None, "Demo", lambda *args: None)
    qtbot.addWidget(dialog)
    dialog.show()
    expanded = dialog.geometry()
    dialog._state = "starting"
    dialog._compact(True)
    assert dialog._bar_animation.state() == QAbstractAnimation.Running
    assert dialog.geometry() == expanded
    wait_for(lambda: dialog._bar_animation.state() == QAbstractAnimation.Stopped)
    assert dialog.size().width() == 164
    assert dialog.size().height() == 36
    dialog._state = "recording"
    dialog._duration_changed(12000)
    assert dialog.recording_indicator.text() == "● 00:12"
    assert not dialog.recording_indicator.isHidden()
    initial_color = dialog._recording_border_color
    dialog._pulse_recording()
    assert dialog._recording_border_color != initial_color
    dialog._state = "paused"
    dialog._update_controls()
    assert dialog.recording_indicator.text() == "Ⅱ 00:12"
    paused_color = dialog._recording_border_color
    dialog._pulse_recording()
    assert dialog._recording_border_color == paused_color
    dialog._state = "idle"
    dialog._compact(False)
    assert not dialog._pulse_timer.isActive()
    dialog.reject()


class FakeRecordingTray:
    supported = True

    def __init__(self, parent=None):
        from PySide6.QtGui import QIcon
        self._icon = QIcon()
        self._tooltip = "StillPoint"
        self._visible = False

    @classmethod
    def isSystemTrayAvailable(cls):
        return cls.supported

    def icon(self):
        return self._icon

    def setIcon(self, icon):
        self._icon = icon

    def toolTip(self):
        return self._tooltip

    def setToolTip(self, text):
        self._tooltip = text

    def isVisible(self):
        return self._visible

    def show(self):
        self._visible = True

    def hide(self):
        self._visible = False

    def setContextMenu(self, menu):
        self.menu = menu

    def deleteLater(self):
        pass


def test_recording_tray_reuses_app_icon_and_controls_without_changing_vault_menu(qtbot, monkeypatch):
    from PySide6.QtWidgets import QApplication, QMenu
    from sp.app.ui.screen_record_tray import ScreenRecordingTray
    monkeypatch.setattr("sp.app.ui.screen_record_tray.QSystemTrayIcon", FakeRecordingTray)
    tray = FakeRecordingTray()
    tray.setIcon(ScreenRecordingTray._colored_icon("#2266aa"))
    original_icon = tray.icon().cacheKey()
    tray.show()
    menu = QMenu()
    menu.addAction("Open vault")
    first = menu.addSeparator()
    menu.addAction("Show Vault")
    second = menu.addSeparator()
    quit_action = menu.addAction("Quit")
    monkeypatch.setattr(QApplication.instance(), "_stillpoint_tray_owner",
                        SimpleNamespace(_tray_icon=tray, _tray_menu=menu), raising=False)
    dialog = ScreenRecordDialog(None, "Demo", lambda *args: None)
    qtbot.addWidget(dialog)
    called = []
    monkeypatch.setattr(dialog, "_pause_resume", lambda: called.append("pause"))
    monkeypatch.setattr(dialog, "_stop", lambda: called.append("stop"))
    dialog._state = "recording"
    dialog._compact(True)
    controller = dialog._tray_controls
    assert controller.tray is tray
    assert tray.icon().cacheKey() != original_icon
    assert "Recording" in tray.toolTip()
    assert [action for action in menu.actions() if action.isSeparator()] == [first, second]
    controller.pause_action.trigger()
    controller.stop_action.trigger()
    assert called == ["pause", "stop"]
    dialog._state = "paused"
    dialog._duration_changed(12000)
    assert "Paused · 00:12" in tray.toolTip()
    assert controller.pause_action.text() == "Resume recording"
    assert tray.icon().cacheKey() == controller._paused_icon.cacheKey()
    dialog._state = "stopped"
    dialog._compact(False)
    assert tray.icon().cacheKey() == original_icon
    assert tray.toolTip() == "StillPoint"
    assert menu.actions()[-1] is quit_action
    assert len(menu.actions()) == 5
    dialog.reject()


def test_recording_bar_can_be_hidden_from_tray_and_preference_is_remembered(qtbot, monkeypatch):
    from PySide6.QtWidgets import QApplication
    monkeypatch.setattr("sp.app.ui.screen_record_tray.QSystemTrayIcon", FakeRecordingTray)
    monkeypatch.setattr(QApplication.instance(), "_stillpoint_tray_owner", None, raising=False)
    dialog = ScreenRecordDialog(None, "Demo", lambda *args: None)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog._state = "recording"
    dialog._compact(True)
    dialog._tray_controls.bar_action.trigger()
    assert not dialog.isVisible()
    assert not dialog._show_recording_bar
    dialog._state = "stopped"
    dialog._compact(False)
    assert dialog.isVisible()
    dialog.reject()
    another = ScreenRecordDialog(None, "Demo", lambda *args: None)
    qtbot.addWidget(another)
    another.show()
    another._state = "recording"
    another._compact(True)
    assert not another.isVisible()
    assert another._tray_controls.available
    another._tray_controls.bar_action.trigger()
    assert another.isVisible()
    assert another._show_recording_bar
    another._state = "stopped"
    another._compact(False)
    another.reject()


def test_recording_bar_stays_available_without_system_tray(qtbot, monkeypatch):
    monkeypatch.setattr("sp.app.ui.screen_record_tray.QSystemTrayIcon", FakeRecordingTray)
    monkeypatch.setattr(FakeRecordingTray, "supported", False)
    dialog = ScreenRecordDialog(None, "Demo", lambda *args: None)
    qtbot.addWidget(dialog)
    dialog._show_recording_bar = False
    dialog.show()
    dialog._state = "recording"
    dialog._compact(True)
    assert dialog.isVisible()
    dialog.set_recording_bar_visible(False)
    assert dialog.isVisible()
    dialog._state = "stopped"
    dialog._compact(False)
    dialog.reject()


def test_countdown_waits_for_global_shortcut_permission(qtbot, monkeypatch):
    dialog = ScreenRecordDialog(None, "Demo", lambda *args: None)
    qtbot.addWidget(dialog)
    monkeypatch.setattr(dialog._hotkeys, "start", lambda: None)
    dialog._begin_countdown()
    assert dialog._state == "shortcuts"
    assert not dialog._countdown.isActive()
    assert dialog.stop_button.isEnabled()
    dialog._hotkeys.ready.emit()
    assert dialog._state == "countdown"
    assert dialog.countdown_label.text() == "3"
    dialog._stop()
    assert dialog._state == "idle"
    dialog.reject()


def test_global_actions_control_a_hidden_screen_recording(qtbot, monkeypatch):
    dialog = ScreenRecordDialog(None, "Demo", lambda *args: None)
    qtbot.addWidget(dialog)
    calls = []
    monkeypatch.setattr(dialog, "_pause_resume", lambda: calls.append("pause"))
    monkeypatch.setattr(dialog, "_stop", lambda: calls.append("stop"))
    dialog.hide()
    dialog._state = "recording"
    dialog._global_recording_action("pause")
    dialog._state = "paused"
    dialog._global_recording_action("pause")
    dialog._global_recording_action("stop")
    dialog._state = "idle"
    dialog._global_recording_action("stop")
    assert calls == ["pause", "pause", "stop"]
    dialog.reject()


def test_screen_and_microphone_choices_are_remembered(qtbot, monkeypatch):
    from sp.app.ui import screen_record_dialog
    device = SimpleNamespace(id=lambda: b"test-mic", description=lambda: "Test microphone")
    monkeypatch.setattr(screen_record_dialog, "QMediaDevices", SimpleNamespace(audioInputs=lambda: [device]))
    first = ScreenRecordDialog(None, "Demo", lambda *args: None)
    qtbot.addWidget(first)
    first.microphone.setCurrentIndex(1)
    first._remember_capture_defaults()
    expected_screen = first.screen.currentData()
    first.reject()
    second = ScreenRecordDialog(None, "Demo", lambda *args: None)
    qtbot.addWidget(second)
    assert second.microphone.currentData() is device
    assert second.screen.currentData() is expected_screen
    second.reject()


def test_missing_saved_microphone_requires_explicit_choice(qtbot, monkeypatch):
    dialog = ScreenRecordDialog(None, "Demo", lambda *args: None)
    qtbot.addWidget(dialog)
    dialog._bar_settings.setValue("screenRecording/microphoneId", b"disconnected".hex())
    dialog._bar_settings.setValue("screenRecording/microphoneName", "Disconnected microphone")
    dialog.reject()
    second = ScreenRecordDialog(None, "Demo", lambda *args: None)
    qtbot.addWidget(second)
    assert second.microphone.currentData(Qt.UserRole + 1)
    second._record()
    assert second._state == "idle"
    assert "saved microphone is unavailable" in second.error_label.text()
    second.microphone.setCurrentIndex(0)
    assert second._bar_settings.value("screenRecording/microphoneId") == ""
    second.reject()


def test_missing_saved_screen_does_not_record_a_different_display(qtbot):
    first = ScreenRecordDialog(None, "Demo", lambda *args: None)
    qtbot.addWidget(first)
    first._bar_settings.setValue("screenRecording/screenName", "Disconnected-display")
    first.reject()
    dialog = ScreenRecordDialog(None, "Demo", lambda *args: None)
    qtbot.addWidget(dialog)
    dialog._record()
    assert dialog._state == "idle"
    assert not dialog._pending_record
    assert "saved screen is unavailable" in dialog.error_label.text()
    dialog.reject()


def test_hotkey_start_uses_current_page_and_starts_only_one_recorder(qtbot, monkeypatch, tmp_path):
    started = []
    monkeypatch.setattr(ScreenRecordDialog, "_record", lambda self: started.append(self))
    first_page = tmp_path / "First.md"
    second_page = tmp_path / "Second.md"
    first_page.write_text("First")
    second_page.write_text("Second")
    panel = AttachmentsPanel()
    qtbot.addWidget(panel)
    panel.set_page(first_page)
    panel._record_screen()
    first = panel._screen_dialog
    panel.set_page(second_page)
    panel._record_screen(auto_start=True)
    wait_for(lambda: bool(started))
    assert panel._screen_dialog is not first
    assert panel._screen_dialog._attachment_page == second_page
    assert panel._screen_dialog.destination.text() == "Attach to: Second"
    panel._screen_dialog._state = "recording"
    panel._record_screen(auto_start=True)
    assert len(started) == 1
    panel._screen_dialog._state = "idle"
    panel._screen_dialog.reject()


def test_video_defaults_round_trip_and_apply_to_recorder(qtbot, tmp_path):
    from sp.app.ui.video_preferences import VideoPreferences
    first = ScreenRecordDialog(None, "Demo", lambda *args: None)
    qtbot.addWidget(first)
    prefs = VideoPreferences(settings=first._bar_settings)
    qtbot.addWidget(prefs)
    assert prefs.use_timer.isChecked()
    assert prefs.countdown.value() == 3
    prefs.profile.setCurrentIndex(prefs.profile.findData("compact"))
    prefs.countdown.setValue(7)
    prefs.save()
    second = ScreenRecordDialog(None, "Demo", lambda *args: None)
    qtbot.addWidget(second)
    assert second.recorder.videoFrameRate() == 24
    assert (second._max_width, second._max_height) == (1280, 720)
    second._state = "shortcuts"
    second._hotkeys_ready()
    assert second._count == 7
    second._stop()
    first.reject()
    second.reject()


def test_countdown_can_be_disabled_after_shortcut_setup(qtbot, monkeypatch):
    dialog = ScreenRecordDialog(None, "Demo", lambda *args: None)
    qtbot.addWidget(dialog)
    dialog._bar_settings.setValue("screenRecording/useCountdown", False)
    started = []
    monkeypatch.setattr(dialog, "_start_recording", lambda: started.append(True))
    dialog._state = "shortcuts"
    dialog._hotkeys_ready()
    assert started == [True]
    assert not dialog._countdown.isActive()
    dialog._state = "idle"
    dialog.reject()


def test_tray_blinks_only_during_recording(qtbot, monkeypatch):
    from PySide6.QtWidgets import QApplication
    monkeypatch.setattr("sp.app.ui.screen_record_tray.QSystemTrayIcon", FakeRecordingTray)
    monkeypatch.setattr(QApplication.instance(), "_stillpoint_tray_owner", None, raising=False)
    dialog = ScreenRecordDialog(None, "Demo", lambda *args: None)
    qtbot.addWidget(dialog)
    dialog._state = "recording"
    tray = dialog._tray_controls
    tray.start()
    bright = tray.tray.icon().cacheKey()
    tray._blink()
    assert tray.tray.icon().cacheKey() != bright
    tray._blink()
    assert tray.tray.icon().cacheKey() == bright
    dialog._state = "paused"
    tray._blink()
    paused = tray.tray.icon().cacheKey()
    tray._blink()
    assert tray.tray.icon().cacheKey() == paused
    tray.stop()
    dialog._state = "idle"
    dialog.reject()
