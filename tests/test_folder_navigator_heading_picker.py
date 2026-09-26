from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint


def test_heading_picker_is_centered_in_editor_viewport(
        tmp_path, monkeypatch, qapp) -> None:
    from sp.app.folder_navigator.window import HeadingPicker, Window

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    page = tmp_path / "notes.md"
    page.write_text("# First\n\n## Second\n", encoding="utf-8")
    window = Window(tmp_path)
    window.resize(1100, 760)
    window.show()
    window.open_file(page)
    qapp.processEvents()
    tab = window.active_tab()
    picker = HeadingPicker([(1, "First", 1), (2, "Second", 3)], window)

    window._position_heading_picker(tab, picker)

    viewport = tab.editor.viewport()
    viewport_center = viewport.mapToGlobal(QPoint(0, 0)) + viewport.rect().center()
    available = qapp.screenAt(viewport_center).availableGeometry()
    margin = 12
    expected_x = max(
        available.left() + margin,
        min(
            viewport_center.x() - (picker.width() // 2),
            available.right() - margin - picker.width() + 1,
        ),
    )
    expected_y = max(
        available.top() + margin,
        min(
            viewport_center.y() - (picker.height() // 2),
            available.bottom() - margin - picker.height() + 1,
        ),
    )
    assert picker.pos() == QPoint(expected_x, expected_y)
    assert available.adjusted(margin, margin, -margin, -margin).contains(
        picker.frameGeometry()
    )
    tab.editor.document().setModified(False)
    picker.close()
    window.close()


def test_heading_picker_position_does_not_follow_text_cursor(
        tmp_path, monkeypatch, qapp) -> None:
    from PySide6.QtGui import QTextCursor
    from sp.app.folder_navigator.window import HeadingPicker, Window

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    page = tmp_path / "notes.md"
    page.write_text("# First\n" + ("text\n" * 100) + "## Last\n", encoding="utf-8")
    window = Window(tmp_path)
    window.show()
    window.open_file(page)
    qapp.processEvents()
    tab = window.active_tab()
    picker = HeadingPicker([(1, "First", 1), (2, "Last", 102)], window)

    tab.editor.moveCursor(QTextCursor.MoveOperation.Start)
    window._position_heading_picker(tab, picker)
    first_position = picker.pos()
    tab.editor.moveCursor(QTextCursor.MoveOperation.End)
    window._position_heading_picker(tab, picker)

    assert picker.pos() == first_position
    tab.editor.document().setModified(False)
    picker.close()
    window.close()
