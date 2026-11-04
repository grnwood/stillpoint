from __future__ import annotations

from PySide6.QtCore import QPoint
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest
from shiboken6 import Shiboken

from sp.app.ui.markdown_editor import MarkdownEditor
from sp.app.ui.mode_window import ModeWindow


def _drain_events(wait_ms: int = 25, rounds: int = 4) -> None:
    for _ in range(rounds):
        QTest.qWait(wait_ms)


def test_mode_window_close_preserves_edited_overlay_buffer_as_dirty(qtbot) -> None:
    base_editor = MarkdownEditor()
    qtbot.addWidget(base_editor)
    base_editor.set_markdown("# Page\n\nOriginal body\n")
    base_editor.document().setModified(False)

    window = ModeWindow(
        "focus",
        base_editor,
        vault_root=None,
        page_path="/Page.md",
        read_only=False,
        heading_provider=lambda: [],
    )
    qtbot.addWidget(window)

    try:
        window._mark_ready()
        window.editor.set_markdown("# Page\n\nEdited in overlay\n")
        window.close()
        _drain_events()

        assert base_editor.to_markdown() == "# Page\n\nEdited in overlay\n"
        assert base_editor.document().isModified() is True
    finally:
        try:
            if Shiboken.isValid(window):
                window.close()
        except Exception:
            pass
        try:
            if Shiboken.isValid(base_editor):
                base_editor.close()
        except Exception:
            pass


def test_audience_heading_picker_is_centered_near_top_and_ignores_cursor(qtbot) -> None:
    base_editor = MarkdownEditor()
    qtbot.addWidget(base_editor)
    base_editor.set_markdown("# First\n" + ("body\n" * 100) + "## Last\n")
    window = ModeWindow(
        "audience",
        base_editor,
        vault_root=None,
        page_path="/Page.md",
        read_only=False,
        heading_provider=lambda: [
            {"title": "First", "line": 1, "level": 1, "position": 0},
            {"title": "Last", "line": 102, "level": 2, "position": 500},
        ],
    )
    qtbot.addWidget(window)
    try:
        window.resize(1100, 760)
        window.show()
        _drain_events()

        window.editor.moveCursor(QTextCursor.MoveOperation.Start)
        window._request_heading_picker_popup()
        _drain_events()
        picker = window._heading_picker
        first_position = picker.pos()

        viewport = window.editor.viewport()
        viewport_center = viewport.mapToGlobal(viewport.rect().center())
        viewport_top = viewport.mapToGlobal(QPoint(0, 0)).y()
        assert abs(picker.frameGeometry().center().x() - viewport_center.x()) <= 1
        assert viewport_top + 20 <= picker.y() <= viewport_top + 80

        picker.close()
        window.editor.moveCursor(QTextCursor.MoveOperation.End)
        window._request_heading_picker_popup()
        _drain_events()

        assert window._heading_picker.pos() == first_position
    finally:
        window.close()
        try:
            if Shiboken.isValid(base_editor):
                base_editor.close()
        except Exception:
            pass
