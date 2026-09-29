from __future__ import annotations

from types import SimpleNamespace
import base64

import pytest

from sp.app.ui.ai_chat_panel import AIChatPanel
from sp.ai.context import MAX_PAGE_CHARS, build_context_prompt, image_data_url
from sp.ai.manager import ContextItem


def item(kind: str, path: str, attachment: str | None = None) -> ContextItem:
    return ContextItem(1, 1, kind, path, attachment, None)


def test_selected_page_is_sent_whole_without_vector_search() -> None:
    reads: list[str] = []

    def read_page(path: str) -> str:
        reads.append(path)
        return "start\n" + ("detail " * 1000) + "\nend"

    prompt = build_context_prompt(
        [item("page", "/Notes/Notes.md")],
        read_page=read_page,
        list_pages=lambda _path: [],
        read_attachment=lambda _path, _name: "",
    )

    assert reads == ["/Notes/Notes.md"]
    assert "start\n" in prompt and "\nend" in prompt
    assert "Page: /Notes/Notes.md" in prompt


def test_folder_manifest_only_for_agent_and_contents_for_plain_chat() -> None:
    pages = ["/Topic/Topic.md", "/Topic/Child/Child.md"]
    reads: list[str] = []

    def read_page(path: str) -> str:
        reads.append(path)
        return f"Contents of {path}"

    kwargs = dict(
        read_page=read_page,
        list_pages=lambda _path: pages,
        read_attachment=lambda _path, _name: "",
    )
    agent_prompt = build_context_prompt([item("page-tree", "/Topic")], agent_mode=True, **kwargs)
    assert reads == []
    assert "Use vault.read" in agent_prompt
    assert all(path in agent_prompt for path in pages)

    plain_prompt = build_context_prompt([item("page-tree", "/Topic")], **kwargs)
    assert reads == pages
    assert all(f"Contents of {path}" in plain_prompt for path in pages)


def test_large_page_reports_shortening() -> None:
    prompt = build_context_prompt(
        [item("page", "/Large/Large.md")],
        read_page=lambda _path: "x" * (MAX_PAGE_CHARS + 100),
        list_pages=lambda _path: [],
        read_attachment=lambda _path, _name: "",
    )
    assert "[Page shortened" in prompt


def test_attachment_text_is_included() -> None:
    prompt = build_context_prompt(
        [item("attachment", "/Notes/Notes.md", "brief.pdf")],
        read_page=lambda _path: "",
        list_pages=lambda _path: [],
        read_attachment=lambda _path, _name: "The briefing",
    )
    assert "Attachment: /Notes/brief.pdf" in prompt
    assert "The briefing" in prompt


def test_vision_context_supplies_image_without_ocr_text() -> None:
    prompt = build_context_prompt(
        [item("attachment", "/Notes/Notes.md", "diagram.png")],
        read_page=lambda _path: "",
        list_pages=lambda _path: [],
        read_attachment=lambda _path, _name: (_ for _ in ()).throw(AssertionError("OCR read")),
        vision_images=True,
    )
    assert "Image: /Notes/diagram.png" in prompt
    assert "Image supplied with this request" in prompt


def test_image_data_url_validates_type_and_size() -> None:
    png = b"\x89PNG\r\n\x1a\ncontent"
    assert image_data_url("diagram.png", png) == "data:image/png;base64," + base64.b64encode(png).decode()
    with pytest.raises(ValueError):
        image_data_url("diagram.png", b"not an image")
    with pytest.raises(ValueError):
        image_data_url("diagram.svg", b"<svg/>")


def test_live_editor_text_takes_precedence_over_server_page(monkeypatch) -> None:
    panel = AIChatPanel.__new__(AIChatPanel)
    panel._editor_context_provider = lambda _path: "unsaved edit"
    panel._api_client = SimpleNamespace(post=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("API read")))
    monkeypatch.setattr(panel, "_api_client_available", lambda: True)

    assert panel._read_context_page("/Notes/Notes.md") == "unsaved edit"


def test_panel_prepares_vision_and_ocr_retry_context(monkeypatch) -> None:
    panel = AIChatPanel.__new__(AIChatPanel)
    panel._context_items = [item("page", "/Notes/Notes.md"), item("attachment", "/Notes/Notes.md", "diagram.png")]
    monkeypatch.setattr(panel, "_should_use_agent_tools", lambda: False)
    monkeypatch.setattr(panel, "_read_context_page", lambda _path: "Current note")
    monkeypatch.setattr(panel, "_list_context_pages", lambda _path: [])
    monkeypatch.setattr(panel, "_read_context_attachment", lambda _path, _name: "OCR words")
    monkeypatch.setattr(panel, "_read_context_image", lambda _path, _name: "data:image/png;base64,AAAA")

    prompt, images, fallback = panel._build_context_payload("Describe the diagram")
    assert "Current note" in prompt
    assert "Image supplied with this request" in prompt
    assert "OCR words" not in prompt
    assert images == ["data:image/png;base64,AAAA"]
    assert "OCR words" in fallback


def test_vision_still_sends_when_ocr_is_unavailable(monkeypatch) -> None:
    panel = AIChatPanel.__new__(AIChatPanel)
    panel._context_items = [item("attachment", "/Notes/Notes.md", "diagram.png")]
    monkeypatch.setattr(panel, "_should_use_agent_tools", lambda: False)
    monkeypatch.setattr(panel, "_read_context_image", lambda _path, _name: "data:image/png;base64,AAAA")
    monkeypatch.setattr(panel, "_read_context_attachment", lambda _path, _name: (_ for _ in ()).throw(RuntimeError("OCR unavailable")))

    prompt, images, fallback = panel._build_context_payload("Describe")
    assert "Image supplied with this request" in prompt
    assert images
    assert "OCR text unavailable" in fallback


def test_local_image_context_cannot_escape_vault(tmp_path) -> None:
    panel = AIChatPanel.__new__(AIChatPanel)
    panel.vault_root = str(tmp_path / "Vault")
    (tmp_path / "Vault" / "Notes").mkdir(parents=True)
    (tmp_path / "outside.png").write_bytes(b"\x89PNG\r\n\x1a\ncontent")

    assert panel._attachment_path("/Notes/Notes.md", "../../outside.png") is None


def test_remote_tree_and_attachment_are_read_through_vault_api(monkeypatch) -> None:
    panel = AIChatPanel.__new__(AIChatPanel)
    calls: list[tuple[str, str]] = []

    def response(data):
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: data)

    panel._api_client = SimpleNamespace(
        get=lambda path, **kwargs: (
            calls.append(("GET", path)),
            response({"tree": [{"open_path": "/Topic/Topic.md", "children": [
                {"open_path": "/Topic/Child/Child.md", "children": []}
            ]}]})
        )[1],
        post=lambda path, **kwargs: (
            calls.append(("POST", path)), response({"content": "Remote attachment text"})
        )[1],
    )
    monkeypatch.setattr(panel, "_api_client_available", lambda: True)

    assert panel._list_context_pages("/Topic") == ["/Topic/Topic.md", "/Topic/Child/Child.md"]
    assert panel._read_context_attachment("/Topic/Topic.md", "brief.pdf") == "Remote attachment text"
    assert calls == [("GET", "/api/vault/tree"), ("POST", "/api/attachment/text")]


def test_chat_context_uses_selected_page_content(monkeypatch) -> None:
    panel = AIChatPanel.__new__(AIChatPanel)
    panel._context_items = [item("page", "/Notes/Notes.md")]
    monkeypatch.setattr(panel, "_should_use_agent_tools", lambda: False)
    monkeypatch.setattr(panel, "_read_context_page", lambda _path: "Current page")
    monkeypatch.setattr(panel, "_list_context_pages", lambda _path: [])
    monkeypatch.setattr(panel, "_read_context_attachment", lambda _path, _name: "")
    assert "Current page" in panel._build_context_prompt("Summarize")
