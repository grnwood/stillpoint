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


def test_quickcapture_resolves_homebase_ref_to_local_path(monkeypatch) -> None:
    from sp.app import quickcapture

    homebase_ref = "homebase::https://server::vault123::/vaults/hybrid"
    monkeypatch.setattr(
        quickcapture.config,
        "load_homebase_vault_profiles",
        lambda: [{"id": homebase_ref, "path": "/vaults/hybrid"}],
    )
    monkeypatch.setattr(quickcapture.config, "load_quick_capture_vault", lambda: None)
    monkeypatch.setattr(quickcapture.config, "load_last_vault", lambda: None)

    resolved = quickcapture._resolve_vault_path(homebase_ref)
    assert resolved == Path("/vaults/hybrid").resolve()


def test_quickcapture_uses_configured_homebase_ref(monkeypatch) -> None:
    from sp.app import quickcapture

    homebase_ref = "homebase::https://server::vault123::/vaults/hybrid"
    monkeypatch.setattr(
        quickcapture.config,
        "load_homebase_vault_profiles",
        lambda: [{"id": homebase_ref, "path": "/vaults/hybrid"}],
    )
    monkeypatch.setattr(quickcapture.config, "load_quick_capture_vault", lambda: homebase_ref)
    monkeypatch.setattr(quickcapture.config, "load_last_vault", lambda: None)

    resolved = quickcapture._resolve_vault_path(None)
    assert resolved == Path("/vaults/hybrid").resolve()


def test_quickcapture_lite_resolves_configured_homebase_ref(monkeypatch) -> None:
    from sp.app import quickcapture_lite

    homebase_ref = "homebase::https://server::vault123::/vaults/hybrid"
    monkeypatch.setattr(
        quickcapture_lite.config,
        "load_homebase_vault_profiles",
        lambda: [{"id": homebase_ref, "path": "/vaults/hybrid"}],
    )
    monkeypatch.setattr(quickcapture_lite.config, "load_quick_capture_vault", lambda: homebase_ref)
    monkeypatch.setattr(quickcapture_lite.config, "load_last_vault", lambda: None)

    resolved = quickcapture_lite._resolve_local_vault_path(None)
    assert resolved == "/vaults/hybrid"


def test_quickcapture_lite_vault_options_include_homebase_profiles(monkeypatch) -> None:
    from sp.app import quickcapture_lite

    monkeypatch.setattr(quickcapture_lite.config, "load_quick_capture_vault", lambda: None)
    monkeypatch.setattr(quickcapture_lite.config, "load_last_vault", lambda: None)
    monkeypatch.setattr(quickcapture_lite.config, "load_known_vaults", lambda: [])
    monkeypatch.setattr(
        quickcapture_lite.config,
        "load_homebase_vault_profiles",
        lambda: [{"id": "homebase::x::v::/vaults/hybrid", "name": "Hybrid Vault", "path": "/vaults/hybrid"}],
    )

    options = quickcapture_lite._local_vault_options()
    assert any(opt.get("path") == "/vaults/hybrid" for opt in options)
