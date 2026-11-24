from __future__ import annotations

import os
import subprocess
from types import SimpleNamespace

import httpx
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMainWindow, QPlainTextEdit

from sp.app import diagram_editor_process as diagrams


@pytest.mark.parametrize("editor", diagrams.EDITORS)
def test_reopening_diagram_restores_existing_window_without_losing_edits(qtbot, tmp_path, editor):
    source = tmp_path / "diagram"
    source.write_text("saved")
    window = QMainWindow()
    window.setCentralWidget(QPlainTextEdit("unsaved edits"))
    qtbot.addWidget(window)
    instance = diagrams.DiagramEditorInstance(editor, source)
    assert instance.claim()
    instance.bind(window)
    try:
        window.showMinimized()
        assert diagrams.activate_existing_diagram(editor, source)
        QTest.qWait(20)
        assert window.isVisible()
        assert not window.isMinimized()
        assert window.centralWidget().toPlainText() == "unsaved edits"
        assert source.read_text() == "saved"
    finally:
        instance.close()


@pytest.mark.parametrize("editor", diagrams.EDITORS)
def test_duplicate_child_exits_before_creating_another_editor(qtbot, tmp_path, editor):
    source = tmp_path / "diagram"
    source.write_text("diagram")
    window = QMainWindow()
    qtbot.addWidget(window)
    instance = diagrams.DiagramEditorInstance(editor, source)
    assert instance.claim()
    instance.bind(window)
    try:
        result = subprocess.run(
            diagrams.editor_process_command(editor, source),
            env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0, result.stderr
        QTest.qWait(20)
        assert window.isVisible()
    finally:
        instance.close()


def test_request_during_startup_is_applied_when_window_is_ready(qtbot, tmp_path):
    source = tmp_path / "diagram"
    instance = diagrams.DiagramEditorInstance("plantuml", source)
    duplicate = diagrams.DiagramEditorInstance("plantuml", source)
    assert instance.claim()
    try:
        assert not duplicate.claim()
        QTest.qWait(20)
        window = QMainWindow()
        qtbot.addWidget(window)
        instance.bind(window)
        assert window.isVisible()
    finally:
        duplicate.close()
        instance.close()


def test_different_files_open_independently_and_closed_diagram_can_reopen(tmp_path):
    first = diagrams.DiagramEditorInstance("mermaid", tmp_path / "one")
    second = diagrams.DiagramEditorInstance("mermaid", tmp_path / "two")
    reopened = diagrams.DiagramEditorInstance("mermaid", tmp_path / "one")
    try:
        assert first.claim()
        assert second.claim()
        first.close()
        assert reopened.claim()
    finally:
        first.close()
        second.close()
        reopened.close()


def test_same_file_via_symlink_has_same_identity(tmp_path):
    source = tmp_path / "diagram"
    source.touch()
    alias = tmp_path / "alias"
    alias.symlink_to(source)
    assert diagrams._diagram_endpoint("mermaid", alias) == diagrams._diagram_endpoint("mermaid", source)


def test_cancelled_close_keeps_reservation_and_accepted_close_releases_it(qtbot, tmp_path):
    class Window(QMainWindow):
        allow_close = False

        def closeEvent(self, event):
            if self.allow_close:
                event.accept()
            else:
                event.ignore()

    source = tmp_path / "diagram"
    window = Window()
    window.setAttribute(Qt.WA_DeleteOnClose, True)
    qtbot.addWidget(window)
    instance = diagrams.DiagramEditorInstance("plantuml", source)
    assert instance.claim()
    instance.bind(window)
    duplicate = diagrams.DiagramEditorInstance("plantuml", source)
    try:
        window.show()
        window.close()
        assert not duplicate.claim()
        QTest.qWait(20)
        window.allow_close = True
        window.close()
        QTest.qWait(20)
        assert duplicate.claim()
    finally:
        duplicate.close()


def test_launcher_does_not_spawn_when_diagram_is_already_open(monkeypatch, tmp_path):
    monkeypatch.setattr(diagrams, "activate_existing_diagram", lambda *args: True)
    monkeypatch.setattr(diagrams.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("duplicate process"))
    assert diagrams.launch_diagram_editor_process("plantuml", tmp_path / "diagram") is None


@pytest.mark.parametrize("editor", diagrams.EDITORS)
def test_remote_reopen_does_not_fetch_or_overwrite_cached_diagram(qtbot, tmp_path, editor):
    from sp.app.ui.main_window import MainWindow

    source = tmp_path / "attachments" / "Page" / "diagram"
    source.parent.mkdir(parents=True)
    source.write_text("cached edits")
    window = QMainWindow()
    qtbot.addWidget(window)
    instance = diagrams.DiagramEditorInstance(editor, source)
    assert instance.claim()
    instance.bind(window)
    owner = SimpleNamespace(
        _ensure_remote_cache_root=lambda: tmp_path,
        _alert=lambda message: pytest.fail(message),
        http=SimpleNamespace(get=lambda *args, **kwargs: pytest.fail("unnecessary remote reload")),
    )
    owner._reserve_remote_diagram_editor = lambda kind, path: MainWindow._reserve_remote_diagram_editor(owner, kind, path)
    try:
        getattr(MainWindow, f"_open_remote_{editor}_editor")(owner, "/Page/diagram", "/Page/Page.md")
        QTest.qWait(20)
        assert window.isVisible()
        assert source.read_text() == "cached edits"
    finally:
        instance.close()


@pytest.mark.parametrize("editor", diagrams.EDITORS)
def test_failed_remote_load_releases_diagram_reservation(tmp_path, editor):
    from sp.app.ui.main_window import MainWindow

    owner = SimpleNamespace(
        _ensure_remote_cache_root=lambda: tmp_path,
        _alert=lambda message: pytest.fail(message),
        _alert_api_error=lambda *args: None,
        _remote_mode=False,
        http=SimpleNamespace(get=lambda *args, **kwargs: httpx.Response(404, request=httpx.Request("GET", "https://example.test"))),
    )
    owner._reserve_remote_diagram_editor = lambda kind, path: MainWindow._reserve_remote_diagram_editor(owner, kind, path)
    getattr(MainWindow, f"_open_remote_{editor}_editor")(owner, "/Page/diagram", "/Page/Page.md")
    reopened = diagrams.DiagramEditorInstance(editor, tmp_path / "attachments/Page/diagram")
    try:
        assert reopened.claim()
    finally:
        reopened.close()
