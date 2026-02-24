import pytest
from PySide6.QtCore import QMimeData, Qt
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
    assert f"[{url}|]" in editor.to_markdown()


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


def test_copy_inserted_external_link_never_leaks_sentinels(editor):
    url = "https://example.com/path?q=1"
    editor.setPlainText("")
    editor.insert_link(url, None)
    cursor = editor.textCursor()
    cursor.select(QTextCursor.SelectionType.Document)
    editor.setTextCursor(cursor)

    editor.copy()

    mime = QGuiApplication.clipboard().mimeData()
    plain = mime.text()
    assert LINK_SENTINEL not in plain
    assert mime.hasFormat("application/x-stillpoint-markdown")
    md_payload = bytes(mime.data("application/x-stillpoint-markdown")).decode("utf-8")
    assert LINK_SENTINEL not in md_payload
    assert f"[{url}|]" in md_payload


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


def test_paste_teams_jira_artifact_links_stabilizes_markdown(editor):
    url_282 = "https://redacted.example/browse/OMSFCC-282"
    url_286 = "https://redacted.example/browse/OMSFCC-286"
    sample = (
        f"* Good morning update [{url_282}|]"
        f"{url_282}"
        f"[ RMA APIs. We captured info on this ticket |{url_282}]"
        f"[{url_282}|]\n"
        f"* then ticket [{url_286}|[{url_286}|]]"
    )
    mime = QMimeData()
    mime.setText(sample)
    editor.insertFromMimeData(mime)

    first = editor.to_markdown()
    editor._refresh_display()
    second = editor.to_markdown()
    editor._refresh_display()
    third = editor.to_markdown()

    assert second == third
    assert len(third) <= len(first) + 20
    assert f"[{url_286}|[{url_286}|]]" not in third
    assert LINK_SENTINEL not in third


def test_html_paste_prefers_anchor_conversion(editor):
    mime = QMimeData()
    mime.setHtml(
        "<p>Ticket <a href='https://example.com/task/123'>Task 123</a> and "
        "<a href='https://example.com/task/456'>https://example.com/task/456</a></p>"
    )
    mime.setText("Ticket Task 123 and https://example.com/task/456")
    editor.insertFromMimeData(mime)

    markdown = editor.to_markdown()
    assert "[https://example.com/task/123|Task 123]" in markdown
    assert "[https://example.com/task/456|https://example.com/task/456]" in markdown


def test_paste_markdown_link_with_control_chars_normalizes_to_wiki(editor):
    mime = QMimeData()
    mime.setText("[Task\u2060 123](https://example.com/task/123\u200b)")
    editor.insertFromMimeData(mime)

    markdown = editor.to_markdown()
    assert "[https://example.com/task/123|Task 123]" in markdown
    assert "\u2060" not in markdown
    assert "\u200b" not in markdown


def test_paste_wiki_link_with_invisible_controls_strips_controls(editor):
    mime = QMimeData()
    mime.setText("[https://example.com/task/999\u200e| Smart\u2060 Label ]")
    editor.insertFromMimeData(mime)

    markdown = editor.to_markdown()
    assert "[https://example.com/task/999|Smart Label]" in markdown
    assert "\u200e" not in markdown
    assert "\u2060" not in markdown


def test_paste_trailing_pipe_url_does_not_create_double_delimiter(editor):
    mime = QMimeData()
    url = "http://www.foxnews.com"
    mime.setText(f"{url}|")
    editor.insertFromMimeData(mime)

    markdown = editor.to_markdown()
    assert f"[{url}|]" in markdown
    assert f"[{url}||]" not in markdown


def test_paste_inserts_at_cursor_not_document_end(editor):
    editor.setPlainText("first\n second")
    cursor = editor.textCursor()
    cursor.setPosition(len("first\n"))
    editor.setTextCursor(cursor)

    mime = QMimeData()
    url = "https://example.com/path?q=1"
    mime.setText(url)
    editor.insertFromMimeData(mime)

    markdown = editor.to_markdown()
    assert markdown.startswith(f"first\n[{url}|] second")


def test_html_paste_ignores_style_blocks(editor):
    mime = QMimeData()
    mime.setHtml(
        "<style>p, li { white-space: pre-wrap; } hr { height: 1px; border-width: 0; }</style>"
        "<p><a href='http://www.google.com'>http://www.google.com</a></p>"
    )
    mime.setText("http://www.google.com")
    editor.insertFromMimeData(mime)

    markdown = editor.to_markdown()
    assert "white-space: pre-wrap" not in markdown
    assert "border-width: 0" not in markdown
    assert "http://www.google.com" in markdown


def test_plain_url_paste_leaves_caret_outside_link(editor):
    mime = QMimeData()
    mime.setText("http://www.google.com")
    editor.insertFromMimeData(mime)

    # If this returns True, the caret was on a link boundary and had to be moved.
    assert editor._move_cursor_to_link_end_on_enter(editor.textCursor()) is False


def test_arrow_right_from_before_link_enters_visible_text_in_one_press(editor):
    url = "http://www.google.com"
    editor.setPlainText(f"A {url} Z")
    editor._refresh_display()

    block = editor.document().firstBlock()
    text = block.text()
    open_idx = text.index(LINK_SENTINEL)
    first_sep = text.find(LINK_SENTINEL, open_idx + 1)
    second_sep = text.find(LINK_SENTINEL, first_sep + 1)
    visible_start = open_idx + 1
    visible_end = first_sep
    if second_sep > first_sep + 1:
        visible_start = first_sep + 1
        visible_end = second_sep

    cursor = editor.textCursor()
    cursor.setPosition(block.position() + open_idx - 1)
    editor.setTextCursor(cursor)

    assert editor._handle_link_boundary_navigation(Qt.Key_Right) is True
    assert editor.textCursor().position() == block.position() + visible_start


def test_arrow_left_from_after_link_enters_visible_text_in_one_press(editor):
    url = "http://www.google.com"
    editor.setPlainText(f"A {url} Z")
    editor._refresh_display()

    block = editor.document().firstBlock()
    text = block.text()
    open_idx = text.index(LINK_SENTINEL)
    first_sep = text.find(LINK_SENTINEL, open_idx + 1)
    second_sep = text.find(LINK_SENTINEL, first_sep + 1)
    visible_start = open_idx + 1
    visible_end = first_sep
    if second_sep > first_sep + 1:
        visible_start = first_sep + 1
        visible_end = second_sep

    cursor = editor.textCursor()
    cursor.setPosition(block.position() + second_sep + 1)
    editor.setTextCursor(cursor)

    assert editor._handle_link_boundary_navigation(Qt.Key_Left) is True
    assert editor.textCursor().position() == block.position() + visible_end

    # Once inside visible text, Left should move normally (not jump to link start).
    assert editor._handle_link_boundary_navigation(Qt.Key_Left) is False


def test_copy_selected_rendered_internal_link_preserves_target_and_label(editor):
    original = "[:pickles:even_bigger_diggus|even bigger diggus]"
    editor.set_markdown(original)

    block = editor.document().firstBlock()
    text = block.text()
    first = text.find(LINK_SENTINEL)
    second = text.find(LINK_SENTINEL, first + 1)
    third = text.find(LINK_SENTINEL, second + 1)
    assert first >= 0 and second > first and third > second
    visible_start = second + 1
    visible_end = third

    cursor = editor.textCursor()
    cursor.setPosition(block.position() + visible_start)
    cursor.setPosition(block.position() + visible_end, QTextCursor.KeepAnchor)
    editor.setTextCursor(cursor)
    editor.copy()

    mime = QGuiApplication.clipboard().mimeData()
    assert mime.hasFormat("application/x-stillpoint-markdown")
    md_payload = bytes(mime.data("application/x-stillpoint-markdown")).decode("utf-8")
    assert md_payload == original

    target = MarkdownEditor()
    target.show()
    try:
        paste_mime = QMimeData()
        paste_mime.setText(mime.text())
        paste_mime.setData("application/x-stillpoint-markdown", mime.data("application/x-stillpoint-markdown"))
        target.insertFromMimeData(paste_mime)
        assert target.to_markdown().startswith(original)
    finally:
        target.close()


def test_copy_selection_spanning_hidden_target_and_label_preserves_link(editor):
    original = "[:pickles:even_bigger_diggus|even bigger diggus]"
    editor.set_markdown(original)

    block = editor.document().firstBlock()
    text = block.text()
    first = text.find(LINK_SENTINEL)
    second = text.find(LINK_SENTINEL, first + 1)
    third = text.find(LINK_SENTINEL, second + 1)
    assert first >= 0 and second > first and third > second

    # Simulate selection that includes target+label content but excludes sentinels.
    cursor = editor.textCursor()
    cursor.setPosition(block.position() + first + 1)
    cursor.setPosition(block.position() + third, QTextCursor.KeepAnchor)
    editor.setTextCursor(cursor)
    editor.copy()

    mime = QGuiApplication.clipboard().mimeData()
    assert mime.hasFormat("application/x-stillpoint-markdown")
    md_payload = bytes(mime.data("application/x-stillpoint-markdown")).decode("utf-8")
    assert md_payload == original


def test_copy_line_under_cursor_preserves_internal_link_markdown(editor):
    original = "[:pickles:even_bigger_diggus|even bigger diggus]"
    editor.set_markdown(original)
    cursor = editor.textCursor()
    cursor.movePosition(QTextCursor.Start)
    cursor.select(QTextCursor.LineUnderCursor)
    editor.setTextCursor(cursor)

    editor.copy()

    mime = QGuiApplication.clipboard().mimeData()
    assert mime.hasFormat("application/x-stillpoint-markdown")
    md_payload = bytes(mime.data("application/x-stillpoint-markdown")).decode("utf-8")
    assert md_payload == original


def test_vi_selected_text_or_line_preserves_internal_link_markdown(editor):
    original = "[:pickles:even_bigger_diggus|even bigger diggus]"
    editor.set_markdown(original)
    cursor = editor.textCursor()
    cursor.movePosition(QTextCursor.Start)
    editor.setTextCursor(cursor)

    copied = editor._vi_selected_text_or_line()
    assert copied == original
