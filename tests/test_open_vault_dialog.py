from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog


def test_open_vault_dialog_uses_single_vaults_tab(qtbot, monkeypatch) -> None:
    from sp.app.ui.open_vault_dialog import OpenVaultDialog
    from sp.app import config

    monkeypatch.setattr(config, "load_homebase_vault_profiles", lambda: [])
    monkeypatch.setattr(config, "load_default_vault", lambda: None)
    monkeypatch.setattr(config, "load_feature_homebase_vaults_enabled", lambda: True)

    dlg = OpenVaultDialog(vaults=[])
    qtbot.addWidget(dlg)

    top_labels = [dlg.tabs.tabText(i) for i in range(dlg.tabs.count())]
    assert top_labels == ["Vaults"]
    assert dlg.tabs.count() == 1


def test_open_vault_dialog_lists_local_and_homebase_together(qtbot, monkeypatch) -> None:
    from sp.app.ui.open_vault_dialog import OpenVaultDialog
    from sp.app import config

    homebase_profile = {
        "id": "homebase::https://server::vault123::/vaults/hybrid",
        "kind": "homebase",
        "name": "Hybrid Vault",
        "path": "/vaults/hybrid",
        "server_url": "https://server",
        "vault_id": "vault123",
    }
    monkeypatch.setattr(config, "load_homebase_vault_profiles", lambda: [homebase_profile])
    monkeypatch.setattr(config, "load_default_vault", lambda: None)
    monkeypatch.setattr(config, "load_feature_homebase_vaults_enabled", lambda: True)

    local_vaults = [
        {"name": "Hybrid Vault", "path": "/vaults/hybrid"},
        {"name": "Plain Local", "path": "/vaults/local"},
    ]
    dlg = OpenVaultDialog(vaults=local_vaults)
    qtbot.addWidget(dlg)

    entries = dlg._combined_local_vault_entries()
    kinds_by_path = {str(v.get("path") or ""): v.get("kind") for v in entries}
    assert kinds_by_path.get("/vaults/local") is None
    assert kinds_by_path.get("/vaults/hybrid") == "homebase"
    assert len(entries) == 2


def test_open_vault_dialog_dedupes_local_and_homebase_when_paths_differ_only_by_trailing_slash(
    qtbot, monkeypatch
) -> None:
    from sp.app.ui.open_vault_dialog import OpenVaultDialog
    from sp.app import config

    homebase_profile = {
        "id": "homebase::https://server::vault123::/vaults/hybrid/",
        "kind": "homebase",
        "name": "Hybrid Vault",
        "path": "/vaults/hybrid/",
        "server_url": "https://server",
        "vault_id": "vault123",
    }
    monkeypatch.setattr(config, "load_homebase_vault_profiles", lambda: [homebase_profile])
    monkeypatch.setattr(config, "load_default_vault", lambda: None)
    monkeypatch.setattr(config, "load_feature_homebase_vaults_enabled", lambda: True)

    local_vaults = [
        {"name": "Hybrid Vault", "path": "/vaults/hybrid"},
        {"name": "Plain Local", "path": "/vaults/local"},
    ]
    dlg = OpenVaultDialog(vaults=local_vaults)
    qtbot.addWidget(dlg)

    entries = dlg._combined_local_vault_entries()
    paths = {str(v.get("path") or "") for v in entries}
    kinds = [v.get("kind") for v in entries]

    assert "/vaults/local" in paths
    assert "/vaults/hybrid/" in paths
    assert "/vaults/hybrid" not in paths
    assert kinds.count("homebase") == 1
    assert len(entries) == 2


def test_homebase_row_hides_vault_id_in_line_but_shows_in_tooltip(qtbot, monkeypatch) -> None:
    from sp.app.ui.open_vault_dialog import OpenVaultDialog
    from sp.app import config

    homebase_profile = {
        "id": "homebase::https://server::vault123::/vaults/hybrid",
        "kind": "homebase",
        "name": "Hybrid Vault",
        "path": "/vaults/hybrid",
        "server_url": "https://server",
        "vault_id": "vault123",
    }
    monkeypatch.setattr(config, "load_homebase_vault_profiles", lambda: [homebase_profile])
    monkeypatch.setattr(config, "load_default_vault", lambda: None)
    monkeypatch.setattr(config, "load_feature_homebase_vaults_enabled", lambda: True)
    monkeypatch.setattr(config, "load_vault_last_opened", lambda _k: "2026-03-01T10:30:00+00:00")

    dlg = OpenVaultDialog(vaults=[])
    qtbot.addWidget(dlg)

    visible = dlg._format_vault_path(homebase_profile)
    assert "vault123" not in visible

    tooltip = dlg._vault_row_tooltip(homebase_profile)
    assert "<table>" in tooltip
    assert "Vault ID" in tooltip
    assert "vault123" in tooltip
    assert "Last Known Open" in tooltip


def test_icon_name_mapping_prefers_remote_icon_for_homebase(qtbot, monkeypatch) -> None:
    from sp.app.ui.open_vault_dialog import OpenVaultDialog
    from sp.app import config

    monkeypatch.setattr(config, "load_homebase_vault_profiles", lambda: [])
    monkeypatch.setattr(config, "load_default_vault", lambda: None)
    monkeypatch.setattr(config, "load_feature_homebase_vaults_enabled", lambda: True)

    dlg = OpenVaultDialog(vaults=[])
    qtbot.addWidget(dlg)

    assert dlg._vault_icon_name({"kind": "local"}) == "notebook.svg"
    assert dlg._vault_icon_name({"kind": "homebase"}) == "notebook_remote.svg"
    assert dlg._vault_icon_name({"id": "homebase::https://server::vid::/vault", "path": "/vault"}) == "notebook_remote.svg"
    assert dlg._vault_icon_name({"server_url": "https://server", "vault_id": "vid", "path": "/vault"}) == "notebook_remote.svg"
    assert dlg._vault_icon_name({"kind": "remote"}) is None


def test_homebase_svg_recolor_only_updates_notebook_outline() -> None:
    from sp.app.ui.open_vault_dialog import OpenVaultDialog

    source = """
    <svg>
      <path d="M0" fill="#1C274C"/>
      <circle fill="url(#badgeFill)" stroke="#1C274C"/>
      <path d="M1" fill="#1C274C"/>
    </svg>
    """
    themed = OpenVaultDialog._homebase_svg_with_outline_color(source, "#FFFFFF")
    assert '<path d="M0" fill="#FFFFFF"/>' in themed
    assert '<circle fill="url(#badgeFill)" stroke="#1C274C"/>' in themed
    assert '<path d="M1" fill="#1C274C"/>' in themed


def test_open_vault_dialog_focuses_list_on_show(qtbot, monkeypatch) -> None:
    from sp.app.ui.open_vault_dialog import OpenVaultDialog
    from sp.app import config

    monkeypatch.setattr(config, "load_homebase_vault_profiles", lambda: [])
    monkeypatch.setattr(config, "load_default_vault", lambda: None)
    monkeypatch.setattr(config, "load_feature_homebase_vaults_enabled", lambda: False)

    dlg = OpenVaultDialog(vaults=[{"name": "Local Vault", "path": "/tmp/local-vault"}])
    qtbot.addWidget(dlg)
    dlg.show()
    QApplication.processEvents()

    assert QApplication.focusWidget() is dlg.local_list_widget


def test_open_vault_dialog_close_without_remote_worker_does_not_crash(qtbot, monkeypatch) -> None:
    from sp.app.ui.open_vault_dialog import OpenVaultDialog
    from sp.app import config

    monkeypatch.setattr(config, "load_homebase_vault_profiles", lambda: [])
    monkeypatch.setattr(config, "load_default_vault", lambda: None)
    monkeypatch.setattr(config, "load_feature_homebase_vaults_enabled", lambda: False)

    dlg = OpenVaultDialog(vaults=[{"name": "Local Vault", "path": "/tmp/local-vault"}])
    qtbot.addWidget(dlg)

    dlg.close()


def test_add_homebase_dialog_prefills_from_detected_metadata(qtbot) -> None:
    from sp.app.ui.open_vault_dialog import AddHomebaseVaultDialog

    dlg = AddHomebaseVaultDialog()
    qtbot.addWidget(dlg)

    dlg._apply_detected_homebase_metadata(
        {
            "server_url": "https://server.example",
            "verify_ssl": False,
            "vault_id": "vault-123",
            "vault_name": "Recovered Vault",
        }
    )

    assert dlg.server_url_edit.text() == "https://server.example"
    assert dlg.ignore_invalid_ssl_checkbox.isChecked() is True
    assert dlg.mode_combo.currentData() == "connect"
    assert dlg.vault_id_edit.text() == "vault-123"
    assert dlg.name_edit.text() == "Recovered Vault"
    assert dlg.store_passphrase_checkbox.isChecked() is False


def test_add_vault_detects_homebase_metadata_and_routes_to_homebase_setup(qtbot, monkeypatch, tmp_path) -> None:
    from sp.app import config
    from sp.app.ui import open_vault_dialog as dialog_module

    vault_root = tmp_path / "RecoveredVault"
    vault_root.mkdir(parents=True, exist_ok=True)
    metadata = {
        "server_url": "https://server.example",
        "verify_ssl": True,
        "vault_id": "vault-123",
        "vault_name": "Recovered Vault",
    }
    profile = {
        "id": f"homebase::https://server.example::vault-123::{vault_root}",
        "kind": "homebase",
        "name": "Recovered Vault",
        "path": str(vault_root),
        "server_url": "https://server.example",
        "verify_ssl": True,
        "vault_id": "vault-123",
        "username": "casey",
        "access_token": "access-token",
        "refresh_token": "refresh-token",
        "passphrase": "session-passphrase",
        "store_passphrase": False,
    }
    profiles: list[dict[str, str]] = []
    upserted: list[dict[str, str]] = []
    deleted_known: list[str] = []
    written_metadata: list[tuple[str, dict[str, str]]] = []

    monkeypatch.setattr(config, "load_homebase_vault_profiles", lambda: list(profiles))
    monkeypatch.setattr(config, "load_default_vault", lambda: None)
    monkeypatch.setattr(config, "load_feature_homebase_vaults_enabled", lambda: True)
    monkeypatch.setattr(config, "load_homebase_vault_metadata", lambda path: metadata if Path(path) == vault_root else None)
    monkeypatch.setattr(config, "delete_known_vault", lambda path: deleted_known.append(path))
    monkeypatch.setattr(
        config,
        "save_homebase_vault_metadata",
        lambda path, entry: written_metadata.append((str(path), dict(entry))) or True,
    )

    def fake_upsert(entry: dict[str, str]) -> None:
        upserted.append(dict(entry))
        profiles[:] = [dict(entry)]

    monkeypatch.setattr(config, "upsert_homebase_vault_profile", fake_upsert)

    class FakeAddVaultDialog:
        def __init__(self, parent=None) -> None:
            self.parent = parent

        def exec(self) -> int:
            return QDialog.Accepted

        def selected_vault(self) -> dict[str, str]:
            return {"name": "Recovered Vault", "path": str(vault_root)}

    monkeypatch.setattr(dialog_module, "AddVaultDialog", FakeAddVaultDialog)
    monkeypatch.setattr(
        dialog_module.OpenVaultDialog,
        "_configure_homebase_vault_from_local_metadata",
        lambda self, vault, loaded_metadata: dict(profile),
    )

    dlg = dialog_module.OpenVaultDialog(vaults=[])
    qtbot.addWidget(dlg)
    monkeypatch.setattr(dlg, "_seed_new_vault", lambda _path: (_ for _ in ()).throw(AssertionError("should not seed local vault")))

    dlg._add_vault()

    assert upserted == [profile]
    assert deleted_known == [str(vault_root)]
    assert written_metadata[0][0] == str(vault_root)
    assert written_metadata[0][1]["vault_id"] == "vault-123"
    current = dlg.local_list_widget.currentItem().data(Qt.UserRole)
    assert current["id"] == profile["id"]
    assert current["passphrase"] == "session-passphrase"


def test_remove_selected_removes_local_vault_and_clears_default(qtbot, monkeypatch) -> None:
    from sp.app.ui.open_vault_dialog import OpenVaultDialog
    from sp.app import config

    removed_paths: list[str] = []
    saved_defaults: list[str | None] = []

    monkeypatch.setattr(config, "load_homebase_vault_profiles", lambda: [])
    monkeypatch.setattr(config, "load_default_vault", lambda: "/tmp/local-vault")
    monkeypatch.setattr(config, "load_feature_homebase_vaults_enabled", lambda: False)
    monkeypatch.setattr(config, "delete_known_vault", lambda path: removed_paths.append(path))
    monkeypatch.setattr(config, "save_default_vault", lambda path: saved_defaults.append(path))

    dlg = OpenVaultDialog(vaults=[{"name": "Local Vault", "path": "/tmp/local-vault"}])
    qtbot.addWidget(dlg)
    dlg.local_list_widget.setCurrentRow(0)

    dlg._remove_selected()

    assert removed_paths == ["/tmp/local-vault"]
    assert saved_defaults == [None]
    assert dlg.local_list_widget.count() == 0
    assert dlg.default_vault is None


def test_remove_selected_deletes_homebase_profile(qtbot, monkeypatch) -> None:
    from sp.app.ui.open_vault_dialog import OpenVaultDialog
    from sp.app import config

    homebase_profile = {
        "id": "homebase::https://server::vault123::/vaults/hybrid",
        "kind": "homebase",
        "name": "Hybrid Vault",
        "path": "/vaults/hybrid",
        "server_url": "https://server",
        "vault_id": "vault123",
    }
    deleted_ids: list[str] = []
    profiles = [homebase_profile]

    monkeypatch.setattr(config, "load_homebase_vault_profiles", lambda: list(profiles))
    monkeypatch.setattr(config, "load_default_vault", lambda: None)
    monkeypatch.setattr(config, "load_feature_homebase_vaults_enabled", lambda: True)

    def fake_delete_homebase_vault_profile(profile_id: str) -> None:
        deleted_ids.append(profile_id)
        profiles[:] = []

    monkeypatch.setattr(config, "delete_homebase_vault_profile", fake_delete_homebase_vault_profile)

    dlg = OpenVaultDialog(vaults=[])
    qtbot.addWidget(dlg)
    dlg.local_list_widget.setCurrentRow(0)

    dlg._remove_selected()

    assert deleted_ids == [homebase_profile["id"]]
    assert dlg.local_list_widget.count() == 0


def test_default_combo_allows_homebase_profile_id(qtbot, monkeypatch) -> None:
    from sp.app.ui.open_vault_dialog import OpenVaultDialog
    from sp.app import config

    homebase_profile = {
        "id": "homebase::https://server::vault123::/vaults/hybrid",
        "kind": "homebase",
        "name": "Hybrid Vault",
        "path": "/vaults/hybrid",
        "server_url": "https://server",
        "vault_id": "vault123",
    }
    saved_defaults: list[str | None] = []

    monkeypatch.setattr(config, "load_homebase_vault_profiles", lambda: [homebase_profile])
    monkeypatch.setattr(config, "load_default_vault", lambda: homebase_profile["id"])
    monkeypatch.setattr(config, "load_feature_homebase_vaults_enabled", lambda: True)
    monkeypatch.setattr(config, "save_default_vault", lambda value: saved_defaults.append(value))

    dlg = OpenVaultDialog(vaults=[{"name": "Local Vault", "path": "/vaults/local"}])
    qtbot.addWidget(dlg)

    assert dlg.default_combo.currentData() == homebase_profile["id"]
    assert dlg.local_list_widget.currentItem().data(Qt.UserRole).get("id") == homebase_profile["id"]

    local_index = dlg.default_combo.findData("/vaults/local")
    assert local_index != -1
    dlg.default_combo.setCurrentIndex(local_index)

    assert saved_defaults[-1] == "/vaults/local"


def test_remove_selected_clears_default_when_homebase_profile_was_default(qtbot, monkeypatch) -> None:
    from sp.app.ui.open_vault_dialog import OpenVaultDialog
    from sp.app import config

    homebase_profile = {
        "id": "homebase::https://server::vault123::/vaults/hybrid",
        "kind": "homebase",
        "name": "Hybrid Vault",
        "path": "/vaults/hybrid",
        "server_url": "https://server",
        "vault_id": "vault123",
    }
    profiles = [homebase_profile]
    saved_defaults: list[str | None] = []

    monkeypatch.setattr(config, "load_homebase_vault_profiles", lambda: list(profiles))
    monkeypatch.setattr(config, "load_default_vault", lambda: homebase_profile["id"])
    monkeypatch.setattr(config, "load_feature_homebase_vaults_enabled", lambda: True)
    monkeypatch.setattr(config, "save_default_vault", lambda value: saved_defaults.append(value))

    def fake_delete_homebase_vault_profile(profile_id: str) -> None:
        assert profile_id == homebase_profile["id"]
        profiles[:] = []

    monkeypatch.setattr(config, "delete_homebase_vault_profile", fake_delete_homebase_vault_profile)

    dlg = OpenVaultDialog(vaults=[])
    qtbot.addWidget(dlg)
    dlg.local_list_widget.setCurrentRow(0)

    dlg._remove_selected()

    assert saved_defaults == [None]
    assert dlg.default_vault is None

