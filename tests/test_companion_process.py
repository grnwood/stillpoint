from __future__ import annotations

import os
from pathlib import Path
import time

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMainWindow

from sp.app import config
from sp.app.companion_process import CompanionWindow, companion_command
from sp.app.ui.markdown_editor import MarkdownEditor


def wait_for(predicate, timeout=15000):
    deadline = time.monotonic() + timeout / 1000
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(20)
    assert predicate()


@pytest.fixture
def owner(qtbot, monkeypatch):
    owner = QMainWindow()
    owner.editor = MarkdownEditor()
    owner.setCentralWidget(owner.editor)
    owner.editor.set_markdown("# Heading\n\nOriginal body\n")
    owner.current_path = "/Page.md"
    owner.vault_root = None
    owner._read_only = False
    owner._toc_headings = []
    owner.errors = []
    owner.actions = []
    owner._alert = owner.errors.append
    owner._get_editor_text_for_path = lambda path: owner.editor.to_markdown()
    for name in ("_open_heading_from_map", "_insert_heading_from_map_request",
                 "_rename_heading_from_map_request", "_reorder_headings_from_map_request"):
        setattr(owner, name, lambda *args: owner.actions.append(args))
    monkeypatch.setattr(config, "get_active_vault", lambda: None)
    monkeypatch.setattr(config, "load_focus_mode_settings", lambda: {})
    monkeypatch.setattr(config, "load_audience_mode_settings", lambda: {})
    qtbot.addWidget(owner)
    yield owner
    for companion in list(getattr(owner, "_companion_windows", [])):
        companion.process.kill()
        companion.process.waitForFinished(3000)


@pytest.mark.parametrize("kind", ["mindmap", "focus", "audience"])
def test_real_child_has_separate_pid_and_closes_without_owner(owner, kind, monkeypatch):
    if os.environ.get("SP_TEST_NATIVE_COMPANIONS") == "1":
        monkeypatch.setenv("QT_QPA_PLATFORM", "cocoa")
    companion = CompanionWindow(owner, kind)
    ready, closed = [], []
    companion.ready.connect(lambda: ready.append(True))
    companion.closed.connect(lambda *args: closed.append(args))
    process = companion.process
    companion.show()
    wait_for(lambda: bool(ready) or bool(owner.errors))
    assert not owner.errors
    assert process.processId() != os.getpid()
    assert process.processId() > 0
    assert companion.application_name == {"mindmap": "StillPoint Map", "focus": "StillPoint Focus",
                                          "audience": "StillPoint Audience"}[kind]
    assert companion.has_application_icon
    if kind != "mindmap":
        # Change the actual child buffer, then close it through its native
        # window.close path. The final snapshot must precede process exit.
        companion.channel.send(dict(type="replace", text="# Edited in companion\n"))
    companion.close()
    wait_for(lambda: not owner._companion_windows)
    assert len(closed) == 1
    assert owner.isVisible()
    assert not owner.errors
    if kind != "mindmap":
        assert owner.editor.to_markdown() == "# Edited in companion\n"


def test_crashed_child_returns_last_received_edit(owner):
    companion = CompanionWindow(owner, "focus")
    ready = []
    companion.ready.connect(lambda: ready.append(True))
    companion.show()
    wait_for(lambda: bool(ready))
    companion._message(dict(type="edit", text="Recovered buffer\n", cursor=3))
    companion.process.kill()
    wait_for(lambda: not owner._companion_windows)
    assert owner.editor.to_markdown() == "Recovered buffer\n"
    assert owner.isVisible()


def test_conflicting_main_edits_are_preserved(owner):
    companion = CompanionWindow(owner, "audience")
    companion._message(dict(type="edit", text="Audience changes\n", cursor=3))
    owner.editor.set_markdown("Concurrent main edit\n")
    companion._process_finished()
    assert owner.editor.to_markdown() == "Concurrent main edit\n"
    assert len(owner.errors) == 1
    path = Path(owner.errors[0].split("preserved at ", 1)[1])
    try:
        assert path.read_text() == "Audience changes\n"
    finally:
        path.unlink()


def test_map_actions_return_to_main_process(owner):
    companion = CompanionWindow(owner, "mindmap")
    companion._message(dict(type="map_action", action="rename", args=["/Page.md", 1, 1, "New title"]))
    assert owner.actions == [("/Page.md", 1, 1, "New title")]
    companion._process_finished()


def test_read_only_mode_returns_cursor_without_changing_content(owner):
    owner._read_only = True
    original = owner.editor.to_markdown()
    companion = CompanionWindow(owner, "focus")
    closed = []
    companion.closed.connect(lambda mode, cursor: closed.append((mode, cursor)))
    companion._message(dict(type="closed", text="Must not be applied", cursor=8))
    companion._process_finished()
    assert closed == [("focus", 8)]
    assert owner.editor.to_markdown() == original


def test_command_supports_source_and_frozen(monkeypatch):
    monkeypatch.setattr("sys.frozen", False, raising=False)
    command = companion_command("focus", "/tmp/example")
    assert command[1:3] == ["-m", "sp.app.companion_process"]
    monkeypatch.setattr("sys.frozen", True, raising=False)
    command = companion_command("audience", "/tmp/example")
    assert command[1:] == ["--companion", "audience", "--endpoint", "/tmp/example"]
    with pytest.raises(ValueError):
        companion_command("unknown", "/tmp/example")


def test_macos_mode_switch_waits_for_final_edits(main_window, monkeypatch):
    monkeypatch.setattr("sp.app.ui.main_window.sys.platform", "darwin")
    monkeypatch.setattr(main_window, "_reload_page_preserve_cursor", lambda path: None)
    monkeypatch.setattr(main_window, "_save_current_file", lambda **kwargs: None)
    main_window.current_path = "/PageA/PageA.md"
    main_window.editor.set_markdown("# Mode switching\n")
    try:
        main_window._toggle_mode_overlay("focus")
        wait_for(lambda: main_window._mode_window is not None and not main_window._mode_window_pending)
        first = main_window._mode_window
        first.channel.send(dict(type="replace", text="# Final focus edit\n"))
        main_window._toggle_mode_overlay("audience")
        wait_for(lambda: main_window._mode_window is not None
                 and main_window._mode_window.mode == "audience"
                 and not main_window._mode_window_pending)
        assert main_window.editor.to_markdown() == "# Final focus edit\n"
        main_window._toggle_mode_overlay("audience")
        wait_for(lambda: not main_window._companion_windows)
        assert main_window._mode_window is None
        assert main_window.isVisible()
    finally:
        for child in list(getattr(main_window, "_companion_windows", [])):
            child.process.kill()
            child.process.waitForFinished(3000)


def test_main_shutdown_waits_for_companion_edits(main_window, monkeypatch):
    monkeypatch.setattr("sp.app.ui.main_window.sys.platform", "darwin")
    main_window.current_path = "/PageA/PageA.md"
    main_window.editor.set_markdown("# Before quit\n")
    saved = []
    monkeypatch.setattr(main_window, "_save_current_file",
                        lambda **kwargs: saved.append(main_window.editor.to_markdown()))
    try:
        main_window._toggle_mode_overlay("focus")
        wait_for(lambda: main_window._mode_window is not None and not main_window._mode_window_pending)
        main_window._mode_window.channel.send(dict(type="replace", text="# Last edit before quit\n"))
        main_window.close()
        wait_for(lambda: not main_window._companion_windows)
        wait_for(lambda: not main_window.isVisible())
        assert "# Last edit before quit\n" in saved
    finally:
        for child in list(getattr(main_window, "_companion_windows", [])):
            child.process.kill()
            child.process.waitForFinished(3000)
