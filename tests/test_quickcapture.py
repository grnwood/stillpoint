from __future__ import annotations

from pathlib import Path


def test_capture_to_files_custom_page_creates_missing_folder(tmp_path: Path, monkeypatch) -> None:
    from sp.app import quickcapture

    monkeypatch.setattr(quickcapture.config, "init_settings", lambda: None)
    monkeypatch.setattr(quickcapture.config, "set_active_vault", lambda _path: None)

    rel_path = quickcapture._capture_to_files(tmp_path, "custom", ":INBOX", "idea one")

    assert rel_path == "/INBOX/INBOX.md"
    target = tmp_path / "INBOX" / "INBOX.md"
    assert target.exists()
    content = target.read_text(encoding="utf-8")
    assert "## Inbox / Captures" in content
    assert "idea one" in content


def test_capture_to_files_custom_page_creates_missing_folder_lite(tmp_path: Path, monkeypatch) -> None:
    from sp.app import quickcapture_lite

    monkeypatch.setattr(quickcapture_lite.config, "init_settings", lambda: None)
    monkeypatch.setattr(quickcapture_lite.config, "set_active_vault", lambda _path: None)

    rel_path = quickcapture_lite._capture_to_files(tmp_path, "custom", ":INBOX", "idea two")

    assert rel_path == "/INBOX/INBOX.md"
    target = tmp_path / "INBOX" / "INBOX.md"
    assert target.exists()
    content = target.read_text(encoding="utf-8")
    assert "## Inbox / Captures" in content
    assert "idea two" in content
