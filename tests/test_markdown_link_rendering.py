import pytest
from PySide6.QtCore import QMimeData

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
