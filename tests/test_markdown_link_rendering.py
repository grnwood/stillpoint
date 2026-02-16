import pytest
from PySide6.QtCore import QMimeData
from PySide6.QtGui import QGuiApplication, QTextCursor
from PySide6.QtGui import QImage

from sp.app.ui.markdown_editor import LINK_SENTINEL, MarkdownEditor


@pytest.fixture
def editor(qapp, monkeypatch):
    monkeypatch.setattr("sp.app.ui.markdown_editor.config.load_prefer_short_links", lambda: False)
    ed = MarkdownEditor()
    ed.show()
    yield ed
    ed.close()


def test_refresh_display_wraps_plain_http_link(editor):
    url = "https://example.com/path?q=1"
    editor.setPlainText(url)
    editor._refresh_display()
    assert f"[{url}|]" in editor.to_markdown()


def test_insert_internal_link_normalizes_to_root_colon(editor):
    editor.setPlainText("")
    editor.insert_link("PageA:PageB", "Custom Label")
    assert "[:PageA:PageB|Custom Label]" in editor.to_markdown()


def test_insert_external_link_keeps_full_url(editor):
    url = "https://lyonscg.atlassian.net/wiki/spaces/AC/pages/5814616065/SAP+2025+-+Launch+Checklist"
    editor.setPlainText("")
    editor.insert_link(url, None)
    assert f"[{url}|{url}]" in editor.to_markdown()


def test_normalize_external_link_strips_sentinels(editor):
    url = "https://sample.example/path+Part"
    raw = f"{url}{LINK_SENTINEL}extra label{LINK_SENTINEL}"
    assert editor._normalize_external_link(raw) == url


def test_insert_link_surround_with_spaces(editor):
    editor.setPlainText("alphabeta")
    cursor = editor.textCursor()
    cursor.setPosition(5)
    editor.setTextCursor(cursor)

    editor.insert_link("Page", surround_with_spaces=True)

    text = editor.toPlainText()
    first = text.index(LINK_SENTINEL)
    last = text.rindex(LINK_SENTINEL)
    assert text[first - 1] == " "
    assert text[last + 1] == " "


def test_camelcase_skips_http_urls(editor):
    url = "https://example.com/wiki/SAP+2025+-+Launch+Checklist"
    assert editor._convert_camelcase_links(url) == url


def test_camelcase_not_converted_in_existing_link_label(editor):
    text = "[PageA:PageB|+KeepLabel] and +ConvertMe"
    converted = editor._convert_camelcase_links(text)
    assert "[PageA:PageB|+KeepLabel]" in converted
    assert "[:ConvertMe|ConvertMe]" in converted


def test_camelcase_uses_current_page_parent(editor):
    editor.set_context("/vault", "/Journal/2025/11/22/Call/Topic/Topic.md")
    converted = editor._convert_camelcase_links("Discuss +NextThing")
    assert "[:Journal:2025:11:22:Call:Topic:NextThing|NextThing]" in converted


def test_plain_text_paste_renders_wiki_links_immediately(editor):
    mime = QMimeData()
    mime.setText("- [:Journal:2026:02:10:Decisions|Decisions] (Updated 11:28am CST)")
    editor.insertFromMimeData(mime)
    assert LINK_SENTINEL in editor.toPlainText()
    assert "[:Journal:2026:02:10:Decisions|Decisions]" in editor.to_markdown()


def test_pasting_http_link_preserves_existing_inline_images(editor, tmp_path):
    vault_root = tmp_path / "vault"
    page_dir = vault_root / "Playpage"
    page_dir.mkdir(parents=True, exist_ok=True)
    image_path = page_dir / "sample.png"
    image = QImage(4, 4, QImage.Format_ARGB32)
    image.fill(0xFF00FF00)
    assert image.save(str(image_path), "PNG")

    editor.set_context(str(vault_root), "/Playpage/Playpage.md")
    editor.set_markdown("![Sample](sample.png)\n")
    cursor = editor.textCursor()
    cursor.movePosition(cursor.MoveOperation.End)
    editor.setTextCursor(cursor)

    mime = QMimeData()
    url = "https://lyonscg.atlassian.net/browse/ASGC-36"
    mime.setText(url)
    editor.insertFromMimeData(mime)

    markdown = editor.to_markdown()
    # Image paths may be normalized to relative paths with ./
    assert ("![Sample](sample.png)" in markdown or "![Sample](./sample.png)" in markdown)
    assert f"[{url}|]" in markdown


def test_copy_strips_sentinels_for_plain_text_and_preserves_internal_markdown(editor):
    url = "https://example.com/path?q=1"
    editor.setPlainText(url)
    editor._refresh_display()
    cursor = editor.textCursor()
    cursor.select(QTextCursor.SelectionType.Document)
    editor.setTextCursor(cursor)

    editor.copy()

    mime = QGuiApplication.clipboard().mimeData()
    plain = mime.text()
    assert LINK_SENTINEL not in plain
    assert mime.hasFormat("application/x-stillpoint-markdown")
    md_payload = bytes(mime.data("application/x-stillpoint-markdown")).decode("utf-8")
    assert f"[{url}|]" in md_payload


def test_internal_markdown_clipboard_roundtrip_restores_link(editor):
    url = "https://example.com/path?q=1"
    src_mime = QMimeData()
    src_mime.setText(url)
    editor.insertFromMimeData(src_mime)
    cursor = editor.textCursor()
    cursor.select(QTextCursor.SelectionType.Document)
    editor.setTextCursor(cursor)
    editor.copy()

    target = MarkdownEditor()
    target.show()
    try:
        mime = QGuiApplication.clipboard().mimeData()
        paste_mime = QMimeData()
        paste_mime.setText(mime.text())
        if mime.hasFormat("application/x-stillpoint-markdown"):
            paste_mime.setData("application/x-stillpoint-markdown", mime.data("application/x-stillpoint-markdown"))
        target.insertFromMimeData(paste_mime)
        assert f"[{url}|]" in target.to_markdown()
        assert LINK_SENTINEL in target.toPlainText()
    finally:
        target.close()


def test_paste_heading_formats_trailing_character(editor, qapp):
    mime = QMimeData()
    mime.setText("# 🔁 What This Looks Like in StillPoint")
    editor.insertFromMimeData(mime)
    qapp.processEvents()

    block = editor.document().firstBlock()
    text = block.text()
    assert text.endswith("StillPoint")

    last_idx = len(text) - 1
    prev_idx = len(text) - 2

    last_cursor = QTextCursor(editor.document())
    last_cursor.setPosition(block.position() + last_idx)
    prev_cursor = QTextCursor(editor.document())
    prev_cursor.setPosition(block.position() + prev_idx)

    last_fmt = last_cursor.charFormat()
    prev_fmt = prev_cursor.charFormat()

    assert last_fmt.foreground().color() == prev_fmt.foreground().color()
    assert last_fmt.fontPointSize() == prev_fmt.fontPointSize()
