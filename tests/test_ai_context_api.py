from __future__ import annotations

import pytest
from fastapi import HTTPException

from sp.server import api


def test_context_page_reads_existing_file_without_recreating_deleted_page(tmp_path, monkeypatch) -> None:
    page = tmp_path / "Notes" / "Notes.md"
    page.parent.mkdir()
    page.write_text("Current content", encoding="utf-8")
    monkeypatch.setattr(api.vault_state, "get_root", lambda: tmp_path)
    payload = api.FilePathPayload(path="/Notes/Notes.md")

    assert api.context_page(payload) == {"content": "Current content"}
    page.unlink()
    with pytest.raises(HTTPException) as exc:
        api.context_page(payload)
    assert exc.value.status_code == 404
    assert not page.exists()


def test_attachment_text_rejects_paths_outside_vault(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(api.vault_state, "get_root", lambda: tmp_path)
    with pytest.raises(HTTPException) as exc:
        api.attachment_text(api.FilePathPayload(path="/../secret.txt"))
    assert exc.value.status_code == 400


def test_attachment_text_accepts_plain_text_attachment(tmp_path, monkeypatch) -> None:
    folder = tmp_path / "Notes"
    folder.mkdir()
    attachment = folder / "sources.txt"
    attachment.write_text("Useful source material", encoding="utf-8")
    monkeypatch.setattr(api.vault_state, "get_root", lambda: tmp_path)

    assert api.attachment_text(api.FilePathPayload(path="/Notes/sources.txt")) == {
        "content": "Useful source material", "truncated": False,
    }


def test_attachment_image_returns_bounded_data_url(tmp_path, monkeypatch) -> None:
    folder = tmp_path / "Notes"
    folder.mkdir()
    image = folder / "diagram.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\ncontent")
    monkeypatch.setattr(api.vault_state, "get_root", lambda: tmp_path)

    result = api.attachment_image(api.FilePathPayload(path="/Notes/diagram.png"))
    assert result["data_url"].startswith("data:image/png;base64,")
    with pytest.raises(HTTPException) as exc:
        api.attachment_image(api.FilePathPayload(path="/../secret.png"))
    assert exc.value.status_code == 400


def test_legacy_vector_endpoints_are_disabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv("SP_ENABLE_LEGACY_VECTOR_CONTEXT", raising=False)
    with pytest.raises(HTTPException) as exc:
        api._require_legacy_vector_context()
    assert exc.value.status_code == 404
