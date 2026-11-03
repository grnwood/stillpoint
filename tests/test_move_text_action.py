"""Tests for the editor Move Text action (selection -> other page)."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from sp.app.ui.main_window import MainWindow
from sp.app.ui.markdown_editor import MarkdownEditor


def test_move_selected_text_replaces_with_link_and_sends_markdown(qapp, monkeypatch):
    monkeypatch.setattr("sp.app.ui.markdown_editor.config.load_prefer_short_links", lambda: False)

    editor = MarkdownEditor()
    editor.set_context(None, "/Source/Source.md")
    editor.setPlainText("Hello world")

    captured: dict[str, str] = {}

    def callback(dest_path: str, markdown_text: str) -> bool:
        captured["dest_path"] = dest_path
        captured["markdown_text"] = markdown_text
        return True

    editor.set_move_text_callback(callback)

    cursor = editor.textCursor()
    cursor.setPosition(6)
    cursor.setPosition(11, cursor.MoveMode.KeepAnchor)
    editor.setTextCursor(cursor)
    qapp.processEvents()

    ok = editor._move_selected_text_to_page("/Target/Target.md")
    assert ok is True

    assert captured["dest_path"] == "/Target/Target.md"
    assert captured["markdown_text"] == "world"

    markdown = editor.to_markdown()
    assert "world" not in markdown
    assert "[:Target|" in markdown

    editor.close()


def test_move_attachment_resolves_source_path_case_before_read_and_delete(tmp_path: Path):
    attachment = tmp_path / "Journal" / "2026" / "11" / "03" / "paste_image_007.png"
    attachment.parent.mkdir(parents=True)
    attachment.write_bytes(b"image data")

    requests: list[tuple[str, object]] = []

    class Response:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

    class Http:
        def post(self, path: str, **kwargs):
            requests.append((path, kwargs))
            return Response()

    window = SimpleNamespace(
        _remote_mode=False,
        vault_root=str(tmp_path),
        http=Http(),
        _resolve_case_insensitive_rel_path=lambda path: MainWindow._resolve_case_insensitive_rel_path(
            SimpleNamespace(vault_root=str(tmp_path)), path
        ),
        _alert=lambda _message: None,
        _alert_api_error=lambda _exc, _message: None,
    )

    moved = MainWindow._move_attachment_path(
        window,
        "/journal/2026/11/03/paste_image_007.png",
        "/Journal/2026/11/03/Follow-up/Follow-up.md",
        "paste_image_007.png",
    )

    assert moved is True
    attach_kwargs = requests[0][1]
    assert attach_kwargs["files"]["files"][1] == b"image data"
    assert requests[1] == (
        "/files/delete",
        {"json": {"paths": ["/Journal/2026/11/03/paste_image_007.png"]}},
    )


def test_missing_attachment_does_not_cancel_text_move(tmp_path: Path):
    messages: list[tuple[str, int]] = []
    http = SimpleNamespace(post=lambda *_args, **_kwargs: pytest.fail("should not upload a missing file"))
    window = SimpleNamespace(
        _remote_mode=False,
        vault_root=str(tmp_path),
        http=http,
        _resolve_case_insensitive_rel_path=lambda path: path,
        statusBar=lambda: SimpleNamespace(
            showMessage=lambda message, timeout: messages.append((message, timeout))
        ),
        _alert=lambda message: pytest.fail(message),
    )

    moved = MainWindow._move_attachment_path(
        window,
        "/Journal/2026/11/03/paste_image_007.png",
        "/Journal/2026/11/03/Follow-up/Follow-up.md",
        "paste_image_007.png",
    )

    assert moved is True
    assert messages == [
        ("Attachment not found; moved text without copying paste_image_007.png", 7000)
    ]
