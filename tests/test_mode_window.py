from __future__ import annotations

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
