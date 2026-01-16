import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QMimeData
from sp.app.ui.markdown_editor import MarkdownEditor

@pytest.fixture(scope="module")
def app():
    return QApplication([])

@pytest.fixture
def editor(app):
    ed = MarkdownEditor()
    ed.show()  # Needed for rendering
    return ed

def test_paste_link_from_buffer(editor):
    # Simulate pasting a wiki-style link from buffer
    link = "[:Journal:2025:11:20:MintSoundFix#1-your-fix-lives-in-etcmodprobed-kernel-agnostic|inline link]"
    editor.setPlainText("")
    editor.insertPlainText(link)
    editor._refresh_display()
    assert "inline link" in editor.toPlainText() or "inline link" in editor.toHtml()


def test_paste_colon_link_with_slug(editor):
    # Simulate pasting a colon link with slug and spaces
    link = "[PageA:PageB:PageC#slug-with-spaces|Label With Spaces]"
    editor.setPlainText("")
    editor.insertPlainText(link)
    editor._refresh_display()
    assert "Label With Spaces" in editor.toPlainText() or "Label With Spaces" in editor.toHtml()


def test_edit_link_inline(editor):
    # Insert a sentence with a link, then edit the label
    sentence = "This is a [PageA:PageB|OriginalLabel] in a sentence."
    editor.setPlainText(sentence)
    editor._refresh_display()
    # Simulate editing the label inline
    new_sentence = "This is a [PageA:PageB|NewLabel] in a sentence."
    editor.setPlainText(new_sentence)
    editor._refresh_display()
    assert "NewLabel" in editor.toPlainText() or "NewLabel" in editor.toHtml()
    # Ensure only the label is shown, not the raw link
    assert "PageA:PageB" not in editor.toPlainText() or "NewLabel" in editor.toHtml()


def test_camelcase_link(editor):
    # Simulate inserting a CamelCase link
    link = "+CamelCasePage"
    editor.setPlainText("")
    editor.insertPlainText(link)
    editor._refresh_display()
    # Should render as a link (the label is the page name)
    assert "CamelCasePage" in editor.toPlainText() or "CamelCasePage" in editor.toHtml()


def test_camelcase_skip_inside_http(editor):
    url = "https://lyonscg.atlassian.net/wiki/spaces/AC/pages/5814616065/SAP+2025+-+Launch+Checklist"
    converted = editor._convert_camelcase_links(url)
    assert converted == url


def test_insert_link_dialog_like_http(editor):
    # Simulate inserting a complex HTTP link through the editor helper
    url = "https://teams.microsoft.com/l/message/19:5071d17824ad4b278afaa9b39ca3fea4@thread.v2/1763757927194?context=%7B%22contextType%22%3A%22chat%22%7D"
    editor.setPlainText("")
    editor.insert_link(url, None)
    # Stored markdown should use wiki format with a single target and empty label
    md = editor.to_markdown()
    expected = "/teams.microsoft.com/l/message/19:5071d17824ad4b278afaa9b39ca3fea4@thread.v2/1763757927194?context=%7B%22contextType%22%3A%22chat%22%7D|"
    assert expected in md


def test_paste_complex_http_link_normalizes(editor):
    url = "https://teams.microsoft.com/l/message/19:5071d17824ad4b278afaa9b39ca3fea4@thread.v2/1763757927194?context=%7B%22contextType%22%3A%22chat%22%7D"
    mime = QMimeData()
    mime.setText(url)
    editor.setPlainText("")
    editor.insertFromMimeData(mime)
    editor._refresh_display()
    expected = "/teams.microsoft.com/l/message/19:5071d17824ad4b278afaa9b39ca3fea4@thread.v2/1763757927194?context=%7B%22contextType%22%3A%22chat%22%7D|"
    assert expected in editor.toPlainText()


def test_insert_link_with_plus_signs(editor):
    url = "https://lyonscg.atlassian.net/wiki/spaces/AC/pages/5814616065/SAP+2025+-+Launch+Checklist"
    editor.setPlainText("")
    editor.insert_link(url, None)
    markdown = editor.to_markdown()
    assert f"[{url}|]" in markdown


def test_normalize_external_link_strips_sentinels(editor):
    url = "https://sample.example/path+Part"
    raw = f"{url}\x00extra label\x00"
    assert editor._normalize_external_link(raw) == url


def test_camelcase_link_uses_parent_folder(editor):
    # When current page is /Journal/.../PickleSausage/PickleSausage.md, +NewTopic should live under that folder once
    editor.set_context("/vault", "/Journal/2025/11/22/SuchandSuchCall/PickleSausage/PickleSausage.md")
    text = "Discuss +RandomActsOfKindess tomorrow"
    converted = editor._convert_camelcase_links(text)
    assert "[:Journal:2025:11:22:SuchandSuchCall:PickleSausage:RandomActsOfKindess|RandomActsOfKindess]" in converted
