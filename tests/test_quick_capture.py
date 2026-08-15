from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QImage, QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QWidget

from sp.app.quickcapture_common import QUICK_CAPTURE_SECTION_TITLE, append_quick_capture_section
from sp.app.quickcapture import _build_quick_capture_entry as build_quick_capture_entry
from sp.app.quickcapture import _append_quick_capture_section as append_desktop_capture
from sp.app.quickcapture_lite import _build_quick_capture_entry as build_quick_capture_entry_lite
from sp.app.quickcapture_lite import _append_quick_capture_section as append_lite_capture
from sp.app.ui.quick_capture_overlay import QuickCaptureInput, QuickCaptureOverlay
from sp.server.api import _build_quick_capture_entry as build_api_quick_capture_entry
from sp.server.api import _append_quick_capture_section as append_api_capture


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


def test_quick_capture_input_adds_non_image_file_attachment(qtbot, tmp_path: Path) -> None:
    widget = QuickCaptureInput()
    qtbot.addWidget(widget)
    attachment = tmp_path / "meeting-notes.pdf"
    attachment.write_bytes(b"not-an-image")
    payloads: list[dict] = []
    widget.imageFileAdded.connect(payloads.append)

    assert widget.add_local_file(attachment) is True

    assert widget.toPlainText() == "<file-Attachment-1-meeting-notes>"
    assert payloads == [
        {
            "path": attachment,
            "placeholder": "<file-Attachment-1-meeting-notes>",
            "is_image": False,
        }
    ]


def test_quick_capture_overlay_tracks_non_image_file_attachment(qtbot, tmp_path: Path) -> None:
    overlay = QuickCaptureOverlay(parent=None, on_capture=lambda text, attachments, vault_path: None)
    qtbot.addWidget(overlay)
    attachment = tmp_path / "agenda.pdf"
    attachment.write_bytes(b"not-an-image")

    assert overlay.input.add_local_file(attachment) is True

    assert len(overlay._attachments) == 1
    assert overlay._attachments[0]["path"] == attachment
    assert overlay._attachments[0]["is_image"] is False


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


def test_quick_capture_overlay_captures_to_selected_destination_and_continues(qtbot) -> None:
    calls: list[tuple[str, dict]] = []

    def capture(text: str, attachments: list[dict], vault_path: str | None, destination: dict) -> dict:
        calls.append((text, destination))
        return {"ok": True, "id": "capture-1", "path": "/INBOX/INBOX.md"}

    custom = {"label": "Inbox", "page_mode": "custom", "page_ref": ":INBOX"}
    overlay = QuickCaptureOverlay(
        parent=None,
        on_capture=lambda text, attachments, vault_path: None,
        destination_options=[
            {"label": "Today's Journal", "page_mode": "today", "page_ref": None},
            custom,
        ],
        selected_destination=custom,
        on_capture_with_destination=capture,
    )
    qtbot.addWidget(overlay)
    overlay.input.setPlainText("capture this")

    overlay._capture(close_after=False)

    assert calls == [("capture this", custom)]
    assert overlay.input.toPlainText() == ""
    assert overlay.history_list.count() == 1
    assert overlay.status_label.text() == "Saved to Inbox."


def test_quick_capture_overlay_accepts_typed_custom_destination(qtbot) -> None:
    overlay = QuickCaptureOverlay(
        parent=None,
        on_capture=lambda text, attachments, vault_path: None,
        destination_options=[
            {"label": "Today's Journal", "page_mode": "today", "page_ref": None},
        ],
    )
    qtbot.addWidget(overlay)

    overlay.destination_combo.setEditText(":PROJECTS")

    assert overlay._current_destination() == {
        "label": ":PROJECTS",
        "page_mode": "custom",
        "page_ref": ":PROJECTS",
    }


def test_quick_capture_ctrl_p_cycles_destination_without_leaving_input(qtbot) -> None:
    parent = QWidget()
    qtbot.addWidget(parent)
    print_calls: list[str] = []
    print_action = QAction("Print", parent)
    print_action.setShortcut(QKeySequence.Print)
    print_action.triggered.connect(lambda: print_calls.append("print"))
    parent.addAction(print_action)
    overlay = QuickCaptureOverlay(
        parent=parent,
        on_capture=lambda text, attachments, vault_path: None,
        destination_options=[
            {"label": "Today's Journal", "page_mode": "today", "page_ref": None},
            {"label": "INBOX", "page_mode": "custom", "page_ref": ":INBOX"},
        ],
    )
    qtbot.addWidget(overlay)
    overlay.show()
    overlay.input.setFocus()
    overlay.input.setPlainText("keep typing here")
    cursor_position = overlay.input.textCursor().position()

    QTest.keyClick(overlay.input, Qt.Key_P, Qt.ControlModifier)

    assert overlay.destination_combo.currentData()["page_ref"] == ":INBOX"
    assert overlay.input.hasFocus()
    assert overlay.input.textCursor().position() == cursor_position
    assert print_calls == []


def test_quick_capture_pasted_image_has_thumbnail(qtbot) -> None:
    overlay = QuickCaptureOverlay(
        parent=None,
        on_capture=lambda text, attachments, vault_path: None,
    )
    qtbot.addWidget(overlay)
    image = QImage(320, 240, QImage.Format_ARGB32)
    image.fill(Qt.red)

    overlay.input.insertFromMimeData(
        type("Mime", (), {"hasImage": lambda self: True, "imageData": lambda self: image})()
    )

    assert overlay.attachments_widget.isHidden() is False
    assert len(overlay._attachment_preview_labels) == 1
    thumbnail = overlay._attachment_preview_labels[0].pixmap()
    assert thumbnail is not None
    assert thumbnail.isNull() is False
    assert thumbnail.width() <= 70
    assert thumbnail.height() <= 70


def test_quick_capture_image_file_has_thumbnail(qtbot, tmp_path: Path) -> None:
    overlay = QuickCaptureOverlay(
        parent=None,
        on_capture=lambda text, attachments, vault_path: None,
    )
    qtbot.addWidget(overlay)
    image_path = tmp_path / "diagram.png"
    image = QImage(180, 120, QImage.Format_ARGB32)
    image.fill(Qt.blue)
    assert image.save(str(image_path), "PNG") is True

    assert overlay.input.add_local_file(image_path) is True

    thumbnail = overlay._attachment_preview_labels[0].pixmap()
    assert thumbnail is not None
    assert thumbnail.isNull() is False


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


def test_quick_capture_entry_links_non_image_attachment() -> None:
    attachments = [
        {
            "name": "meeting-notes.pdf",
            "placeholder": "<file-Attachment-1-meeting-notes>",
            "is_image": False,
        }
    ]
    expected = [
        "- *2026-05-07: 07:01am*",
        "  See [meeting-notes.pdf](./meeting-notes.pdf)",
        "",
        "---",
    ]

    assert build_quick_capture_entry(
        "See <file-Attachment-1-meeting-notes>", "2026-05-07: 07:01am", attachments
    ) == expected
    assert build_quick_capture_entry_lite(
        "See <file-Attachment-1-meeting-notes>", "2026-05-07: 07:01am", attachments
    ) == expected


def test_quick_capture_section_title_constant_is_shared() -> None:
    assert QUICK_CAPTURE_SECTION_TITLE == "## QuickCaptures"


def test_capture_consolidates_duplicate_and_task_prefixed_headers() -> None:
    content = (
        "# Today\n\n## Follow-up\n☐ ## QuickCaptures\n"
        "- *08:08 am*\n  first\n\n---\n\n"
        "## QuickCaptures\n"
        "- *08:09 am*\n  second\n\n---\n---\n"
    )
    updated = append_quick_capture_section(
        content, ["- *08:10 am*", "  third", "", "---"], "Captured Notes"
    )

    assert updated.count("## Captured Notes") == 1
    assert "QuickCaptures" not in updated
    assert "☐ ##" not in updated
    assert all(value in updated for value in ("  first", "  second", "  third"))
    assert "---\n---" not in updated


def test_all_capture_clients_use_configured_header(monkeypatch) -> None:
    monkeypatch.setattr("sp.app.config.load_quick_capture_header", lambda: "Captured Notes")
    for append_capture in (append_desktop_capture, append_lite_capture, append_api_capture):
        first = append_capture("# Today\n", ["- *one*", "  first", "", "---"])
        second = append_capture(first, ["- *two*", "  second", "", "---"])

        assert second.count("## Captured Notes") == 1
        assert second.count("  first") == 1
        assert second.count("  second") == 1
