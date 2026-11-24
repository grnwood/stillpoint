from __future__ import annotations

import time
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest


def wait_for(predicate):
    deadline = time.monotonic() + 3
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(10)
    assert predicate()


def open_page(window, path):
    window._open_file(path)
    QTest.qWait(30)


@pytest.mark.parametrize("reading", [False, True])
def test_attachment_shortcut_opens_hidden_right_panel(main_window, reading):
    window = main_window
    page_folder = Path(window.vault_root) / "PageA"
    (page_folder / "diagram.pdf").write_bytes(b"attachment")
    window._set_right_panel_collapsed(True)
    open_page(window, "/PageA/PageA.md")
    window._set_reading_view_enabled(reading)
    button = window._attachment_indicator
    wait_for(button.isVisible)
    tool_button = window.toolbar.widgetForAction(button)
    wait_for(tool_button.isVisible)
    actions = window.toolbar.actions()
    assert actions[actions.index(button) + 1] is window._toolbar_prefs_action
    assert not button.icon().isNull()
    assert not window._is_right_panel_expanded()
    QTest.mouseClick(tool_button, Qt.LeftButton)
    assert window._is_right_panel_expanded()
    assert window.right_panel.tabs.currentWidget() is window.right_panel.attachments_panel
    assert window._reading_view_enabled == reading


def test_indicator_tracks_page_changes_and_attachment_add_remove(main_window):
    window = main_window
    window._set_right_panel_collapsed(True)
    open_page(window, "/PageB/PageB.md")
    button = window._attachment_indicator
    QTest.qWait(50)
    assert not button.isVisible()
    attachment = Path(window.vault_root) / "PageB" / "extra.pdf"
    attachment.write_bytes(b"attachment")
    window._on_image_saved(attachment.name)
    wait_for(button.isVisible)
    open_page(window, "/PageC/PageC.md")
    assert not button.isVisible()
    open_page(window, "/PageB/PageB.md")
    wait_for(button.isVisible)
    attachment.unlink()
    window._on_local_attachment_changed("attachments removed")
    wait_for(lambda: not button.isVisible())
    window.right_panel.set_current_page(None, None)
    assert not button.isVisible()



def test_slow_remote_result_does_not_leak_to_next_page(qapp):
    from threading import Event
    from types import SimpleNamespace
    from sp.app.ui.attachment_indicator import AttachmentIndicator

    release_old = Event()
    old_started = Event()
    current_finished = Event()
    old_finished = Event()

    class Client:
        def get(self, path, params):
            assert path == "/files/"
            if params["page_path"] == "/Old.md":
                old_started.set()
                release_old.wait(3)
                entries = [{"attachment_path": "/Old/photo.png"}]
                old_finished.set()
            else:
                entries = []
                current_finished.set()
            return SimpleNamespace(raise_for_status=lambda: None,
                                   json=lambda: {"attachments": entries})

    action = AttachmentIndicator(qapp)
    client = Client()
    try:
        action.set_page(Path("/Old.md"), page_key="/Old.md", client=client)
        wait_for(old_started.is_set)
        action.set_page(Path("/New.md"), page_key="/New.md", client=client)
        wait_for(current_finished.is_set)
        QTest.qWait(20)
        release_old.set()
        wait_for(old_finished.is_set)
        QTest.qWait(20)
        assert not action.isVisible()
    finally:
        release_old.set()
        action.deleteLater()
