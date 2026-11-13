from types import SimpleNamespace

import httpx
import pytest
from markdown import markdown
from PySide6.QtCore import QUrl
from PySide6.QtGui import QTextDocument

from sp.app.ui.ai_chat_panel import AIChatPanel
from sp.app.ui.agent_tool_loop import _normalize_agent_page_links, _tool_vault_search
from sp.app.ui.main_window import MainWindow
from sp.markdown_html import render_markdown_html, vault_link_href


def _chat_host():
    host = SimpleNamespace(_page_title_link_cache={})
    for name in (
        "_linkify_vault_paths", "_linkify_inline_paths", "_replace_path_tokens",
        "_replace_title_lines_with_links", "_resolve_page_title_path",
    ):
        setattr(host, name, getattr(AIChatPanel, name).__get__(host))
    return host


@pytest.mark.parametrize("source", [
    "[:2-Projects:DigiKey:ExpeditedOrders|Expedited Orders]",
    "[Expedited Orders](:2-Projects:DigiKey:ExpeditedOrders)",
    "[/2-Projects/DigiKey/ExpeditedOrders/ExpeditedOrders.md|Expedited Orders]",
    "[Expedited Orders](/2-Projects/DigiKey/ExpeditedOrders/ExpeditedOrders.md)",
])
def test_agent_reply_vault_links_render_and_navigate(qapp, source):
    host = _chat_host()
    doc = QTextDocument()
    doc.setHtml(markdown(host._linkify_vault_paths(source)))
    assert doc.toPlainText() == "Expedited Orders"
    href = doc.firstBlock().begin().fragment().charFormat().anchorHref()
    target = ":2-Projects:DigiKey:ExpeditedOrders"
    assert href == vault_link_href(target)
    opened = []
    host.chatNavigateRequested = SimpleNamespace(emit=opened.append)
    AIChatPanel._on_anchor_clicked(host, QUrl(href))
    assert opened == [target]


def test_chat_links_preserve_code_and_external_markdown(qapp):
    source = (
        "`[:Projects:Alpha|Alpha]`\n"
        "~~~markdown\n[:Projects:Alpha|Alpha]\n~~~\n"
        "```markdown\n[/Projects/Alpha/Alpha.md|Alpha]\n```\n"
        "[Example](https://example.com/path?q=1&x=2)"
    )
    rendered = _chat_host()._linkify_vault_paths(source)
    assert rendered.count('<a href=') == 1
    assert "`[:Projects:Alpha|Alpha]`" in rendered
    assert "~~~markdown\n[:Projects:Alpha|Alpha]\n~~~" in rendered
    assert "```markdown\n[/Projects/Alpha/Alpha.md|Alpha]\n```" in rendered
    assert 'href="https://example.com/path?q=1&amp;x=2"' in rendered


def test_reading_view_keeps_vault_link_target(qapp):
    rendered = render_markdown_html("[:2-Projects:DigiKey:ExpeditedOrders|Expedited Orders]")
    assert f'<a href="{vault_link_href(":2-Projects:DigiKey:ExpeditedOrders")}">Expedited Orders</a>' in rendered


def test_reading_view_link_examples_remain_code():
    source = "`[:Projects:Alpha|Alpha]`\n\n```markdown\n[:Projects:Alpha|Alpha]\n```"
    rendered = render_markdown_html(source)
    assert 'href=' not in rendered
    assert rendered.count("[:Projects:Alpha|Alpha]") == 2


def test_reading_view_link_click_preserves_anchor_and_percent_characters(qtbot):
    from sp.app.ui.markdown_preview import MarkdownReadingView

    target = ":Projects:100%25 Complete#status & next"
    view = MarkdownReadingView()
    qtbot.addWidget(view)
    view.set_markdown(f"[{target}|Status]")
    opened = []
    view.linkActivated.connect(opened.append)
    view.browser.anchorClicked.emit(QUrl(vault_link_href(target)))
    assert opened == [target]


def test_bare_path_and_title_links_use_valid_qt_urls():
    host = _chat_host()
    host._page_title_link_cache["expedited orders"] = "/Projects/ExpeditedOrders/ExpeditedOrders.md"
    for source in (
        "/Projects/ExpeditedOrders/ExpeditedOrders.md",
        "Expedited Orders — DPAS context",
    ):
        rendered = host._linkify_vault_paths(source)
        assert vault_link_href(":Projects:ExpeditedOrders") in rendered


def test_agent_markdown_colon_links_use_editor_native_storage():
    assert _normalize_agent_page_links(
        "[Expedited Orders](:2-Projects:DigiKey:ExpeditedOrders)"
    ) == "[:2-Projects:DigiKey:ExpeditedOrders|Expedited Orders]"


def test_search_returns_canonical_link_without_repeated_leaf():
    path = "/2-Projects/DigiKey/ExpeditedOrders/ExpeditedOrders.md"
    transport = httpx.MockTransport(lambda request: httpx.Response(
        200, json={"results": [{"path": path, "snippet": "DPAS"}]}
    ))
    with httpx.Client(transport=transport, base_url="http://test") as client:
        result = _tool_vault_search(client, {"query": "DPAS"}, {})
    assert result["output"]["matches"][0]["link"] == ":2-Projects:DigiKey:ExpeditedOrders"


@pytest.mark.parametrize("suffix", ["md", "txt"])
def test_relative_self_link_does_not_create_child(suffix):
    events = []
    host = SimpleNamespace(
        current_path=f"/Projects/ExpeditedAndPriorityOrders/ExpeditedAndPriorityOrders.{suffix}",
        _save_current_file=lambda **kwargs: None,
        _is_attachment_link=lambda value: False,
        _split_link_anchor=lambda value: (value.split("#")[0], "section"),
        _anchor_slug=lambda value: value,
        _scroll_to_anchor_slug=lambda value: events.append(("anchor", value)),
        _apply_navigation_focus=lambda value: events.append(("focus", value)),
    )
    # Any attempt to create/open a new page would fail on this minimal host.
    MainWindow._open_camel_link(host, "ExpeditedAndPriorityOrders#section", focus_target="editor")
    assert events == [("anchor", "section"), ("focus", "editor")]


def test_absolute_editor_link_opens_existing_page_without_creating_child(tmp_path):
    path = "/2-Projects/DigiKey/ExpeditedOrders/ExpeditedOrders.md"
    file = tmp_path / path.lstrip("/")
    file.parent.mkdir(parents=True)
    file.write_text("# DPAS\n")
    opened = []
    host = SimpleNamespace(
        current_path="/Journal/2026/11/13/13.md",
        vault_root=tmp_path, vault_root_name="Test",
        _save_current_file=lambda **kwargs: None,
        _is_attachment_link=lambda value: False,
        _split_link_anchor=lambda value: (value, None),
        _anchor_slug=lambda value: value,
        _vault_root_page_path=lambda: "/Test/Test.md",
        _resolve_case_insensitive_rel_path=lambda value: value,
        _file_path_to_folder=lambda value: str(file.parent),
        _populate_vault_tree=lambda: None,
        _open_file=lambda value, **kwargs: opened.append((value, kwargs["cursor_at_end"])),
        _scroll_to_anchor_slug=lambda value: None,
        _apply_navigation_focus=lambda value: None,
    )
    MainWindow._open_camel_link(host, ":2-Projects:DigiKey:ExpeditedOrders")
    assert opened == [(path, False)]
    assert list(file.parent.iterdir()) == [file]


def test_missing_chat_link_does_not_open_or_create_page():
    notices = []
    host = SimpleNamespace(
        _split_link_anchor=lambda value: (value, None),
        _normalize_editor_path=lambda value: "/Missing/Missing.md",
        _resolve_case_insensitive_rel_path=lambda value: value,
        _page_exists=lambda value: False,
        statusBar=lambda: SimpleNamespace(showMessage=lambda *args: notices.append(args)),
    )
    MainWindow._on_ai_chat_navigate(host, ":Missing")
    assert notices and "Linked page not found" in notices[0][0]
