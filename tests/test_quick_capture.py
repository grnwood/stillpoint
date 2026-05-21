from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QWidget

from sp.app.quickcapture_common import QUICK_CAPTURE_SECTION_TITLE
from sp.app.quickcapture import _build_quick_capture_entry as build_quick_capture_entry
from sp.app.quickcapture_lite import _build_quick_capture_entry as build_quick_capture_entry_lite
from sp.app.ui.quick_capture_overlay import QuickCaptureInput, QuickCaptureOverlay
from sp.server.api import _build_quick_capture_entry as build_api_quick_capture_entry


def test_quick_capture_input_uses_enter_for_newline_and_ctrl_enter_for_capture(qtbot) -> None:
    widget = QuickCaptureInput()
    qtbot.addWidget(widget)
    widget.show()
    widget.setFocus()

    captures: list[str] = []
    widget.captureRequested.connect(lambda: captures.append("capture"))

    QTest.keyClicks(widget, "first line")
    QTest.keyClick(widget, Qt.Key_Return)
    QTest.keyClicks(widget, "second line")
    assert captures == []
    assert widget.toPlainText() == "first line\nsecond line"

    QTest.keyClick(widget, Qt.Key_Return, Qt.ControlModifier)
    assert captures == ["capture"]
    assert widget.toPlainText() == "first line\nsecond line"


def test_quick_capture_input_expands_paren_task_shortcut_on_space(qtbot) -> None:
    widget = QuickCaptureInput()
    qtbot.addWidget(widget)
    widget.show()
    widget.setFocus()

    QTest.keyClicks(widget, "() ")
    assert widget.toPlainText() == "☐ "


def test_quick_capture_input_inserts_clipboard_image_placeholder_at_cursor(qtbot) -> None:
    widget = QuickCaptureInput()
    qtbot.addWidget(widget)
    widget.show()
    widget.setFocus()

    payloads: list[dict] = []
    widget.imageAdded.connect(lambda payload: payloads.append(payload))
    image = QImage(320, 240, QImage.Format_ARGB32)
    image.fill(0)

    widget.insertFromMimeData(type("Mime", (), {"hasImage": lambda self: True, "imageData": lambda self: image})())

    assert widget.toPlainText() == "<clipboard-Image-1-320x240>"
    assert payloads[0]["placeholder"] == "<clipboard-Image-1-320x240>"


def test_quick_capture_overlay_uses_persistent_tool_window_flags(qtbot) -> None:
    overlay = QuickCaptureOverlay(parent=None, on_capture=lambda text, attachments, vault_path: None)
    qtbot.addWidget(overlay)

    flags = overlay.windowFlags()
    assert overlay.windowType() == Qt.Tool
    assert bool(flags & Qt.Tool)
    assert bool(flags & Qt.WindowStaysOnTopHint)
    assert bool(flags & Qt.FramelessWindowHint)
    assert overlay.isModal() is False


def test_quick_capture_overlay_stays_visible_when_focus_moves(qtbot) -> None:
    overlay = QuickCaptureOverlay(parent=None, on_capture=lambda text, attachments, vault_path: None)
    other = QWidget()
    qtbot.addWidget(overlay)
    qtbot.addWidget(other)

    overlay.show()
    overlay.input.setFocus()
    QTest.qWait(50)

    other.show()
    other.setFocus()
    QTest.qWait(50)

    assert overlay.isVisible()


def test_quick_capture_entry_uses_timestamp_header_and_indented_note_body() -> None:
    expected = [
        "- *2026-05-07: 07:01am*",
        "  first line",
        "  second line",
        "",
        "---",
    ]

    assert build_quick_capture_entry("first line\nsecond line", "2026-05-07: 07:01am") == expected
    assert build_quick_capture_entry_lite("first line\nsecond line", "2026-05-07: 07:01am") == expected
    assert build_api_quick_capture_entry("first line\nsecond line", "2026-05-07: 07:01am") == expected


def test_quick_capture_entry_replaces_attachment_placeholder_in_place() -> None:
    images = [{"name": "paste_image_001.png", "width": 320, "placeholder": "<clipboard-Image-1-320x240>"}]
    expected = [
        "- *2026-05-07: 07:01am*",
        "  before ![](./paste_image_001.png) after",
        "",
        "---",
    ]

    assert build_quick_capture_entry("before <clipboard-Image-1-320x240> after", "2026-05-07: 07:01am", images) == expected
    assert build_quick_capture_entry_lite("before <clipboard-Image-1-320x240> after", "2026-05-07: 07:01am", images) == expected


def test_quick_capture_entry_strips_unresolved_placeholder_tokens() -> None:
    expected = [
        "- *2026-05-07: 07:01am*",
        "  before  after",
        "",
        "---",
    ]

    assert build_quick_capture_entry("before <clipboard-Image-1-320x240> after", "2026-05-07: 07:01am") == expected
    assert build_quick_capture_entry_lite("before <clipboard-Image-1-320x240> after", "2026-05-07: 07:01am") == expected


def test_quick_capture_section_title_constant_is_shared() -> None:
    assert QUICK_CAPTURE_SECTION_TITLE == "## QuickCaptures"
