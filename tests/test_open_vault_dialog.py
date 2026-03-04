from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication


def test_open_vault_dialog_uses_two_top_level_tabs(qtbot, monkeypatch) -> None:
    from sp.app.ui.open_vault_dialog import OpenVaultDialog
    from sp.app import config

    monkeypatch.setattr(config, "load_homebase_vault_profiles", lambda: [])
    monkeypatch.setattr(config, "load_default_vault", lambda: None)
    monkeypatch.setattr(config, "load_feature_remote_vaults_enabled", lambda: True)
    monkeypatch.setattr(config, "load_feature_homebase_vaults_enabled", lambda: True)

    dlg = OpenVaultDialog(vaults=[], on_load_remote=lambda: [])
    qtbot.addWidget(dlg)

    top_labels = [dlg.tabs.tabText(i) for i in range(dlg.tabs.count())]
    assert top_labels == ["Local", "Remote Vault"]


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
    monkeypatch.setattr(config, "load_feature_remote_vaults_enabled", lambda: False)
    monkeypatch.setattr(config, "load_feature_homebase_vaults_enabled", lambda: True)

    local_vaults = [
        {"name": "Hybrid Vault", "path": "/vaults/hybrid"},
        {"name": "Plain Local", "path": "/vaults/local"},
    ]
    dlg = OpenVaultDialog(vaults=local_vaults, on_load_remote=lambda: [])
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
    monkeypatch.setattr(config, "load_feature_remote_vaults_enabled", lambda: False)
    monkeypatch.setattr(config, "load_feature_homebase_vaults_enabled", lambda: True)

    local_vaults = [
        {"name": "Hybrid Vault", "path": "/vaults/hybrid"},
        {"name": "Plain Local", "path": "/vaults/local"},
    ]
    dlg = OpenVaultDialog(vaults=local_vaults, on_load_remote=lambda: [])
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
    monkeypatch.setattr(config, "load_feature_remote_vaults_enabled", lambda: False)
    monkeypatch.setattr(config, "load_feature_homebase_vaults_enabled", lambda: True)
    monkeypatch.setattr(config, "load_vault_last_opened", lambda _k: "2026-03-01T10:30:00+00:00")

    dlg = OpenVaultDialog(vaults=[], on_load_remote=lambda: [])
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
    monkeypatch.setattr(config, "load_feature_remote_vaults_enabled", lambda: False)
    monkeypatch.setattr(config, "load_feature_homebase_vaults_enabled", lambda: True)

    dlg = OpenVaultDialog(vaults=[], on_load_remote=lambda: [])
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
    # Badge stroke/fills remain unchanged.
    assert '<circle fill="url(#badgeFill)" stroke="#1C274C"/>' in themed
    # Only first outline fill token should be replaced.
    assert '<path d="M1" fill="#1C274C"/>' in themed


def test_open_vault_dialog_focuses_list_on_show(qtbot, monkeypatch) -> None:
    from sp.app.ui.open_vault_dialog import OpenVaultDialog
    from sp.app import config

    monkeypatch.setattr(config, "load_homebase_vault_profiles", lambda: [])
    monkeypatch.setattr(config, "load_default_vault", lambda: None)
    monkeypatch.setattr(config, "load_feature_remote_vaults_enabled", lambda: False)
    monkeypatch.setattr(config, "load_feature_homebase_vaults_enabled", lambda: False)

    dlg = OpenVaultDialog(vaults=[{"name": "Local Vault", "path": "/tmp/local-vault"}], on_load_remote=lambda: [])
    qtbot.addWidget(dlg)
    dlg.show()
    QApplication.processEvents()

    assert QApplication.focusWidget() is dlg.local_list_widget


def test_open_vault_dialog_alt_tab_cycles_tabs_and_focuses_active_list(qtbot, monkeypatch) -> None:
    from sp.app.ui.open_vault_dialog import OpenVaultDialog
    from sp.app import config

    monkeypatch.setattr(config, "load_homebase_vault_profiles", lambda: [])
    monkeypatch.setattr(config, "load_default_vault", lambda: None)
    monkeypatch.setattr(config, "load_feature_remote_vaults_enabled", lambda: True)
    monkeypatch.setattr(config, "load_feature_homebase_vaults_enabled", lambda: False)

    remote = [{"id": "remote::x::/v", "kind": "remote", "name": "Remote", "path": "/v", "server_url": "http://x"}]
    dlg = OpenVaultDialog(vaults=[{"name": "Local Vault", "path": "/tmp/local-vault"}], on_load_remote=lambda: remote)
    qtbot.addWidget(dlg)
    dlg.show()
    QApplication.processEvents()

    assert dlg.tabs.count() == 2
    assert dlg.tabs.currentIndex() == 0
    assert QApplication.focusWidget() is dlg.local_list_widget

    QTest.keyClick(dlg, Qt.Key_Tab, Qt.AltModifier)
    QApplication.processEvents()
    assert dlg.tabs.currentIndex() == 1
    assert QApplication.focusWidget() is dlg.remote_list_widget

    QTest.keyClick(dlg, Qt.Key_Tab, Qt.AltModifier | Qt.ShiftModifier)
    QApplication.processEvents()
    assert dlg.tabs.currentIndex() == 0
    assert QApplication.focusWidget() is dlg.local_list_widget


def test_open_vault_dialog_ctrl_tab_cycles_tabs_and_focuses_active_list(qtbot, monkeypatch) -> None:
    from sp.app.ui.open_vault_dialog import OpenVaultDialog
    from sp.app import config

    monkeypatch.setattr(config, "load_homebase_vault_profiles", lambda: [])
    monkeypatch.setattr(config, "load_default_vault", lambda: None)
    monkeypatch.setattr(config, "load_feature_remote_vaults_enabled", lambda: True)
    monkeypatch.setattr(config, "load_feature_homebase_vaults_enabled", lambda: False)

    remote = [{"id": "remote::x::/v", "kind": "remote", "name": "Remote", "path": "/v", "server_url": "http://x"}]
    dlg = OpenVaultDialog(vaults=[{"name": "Local Vault", "path": "/tmp/local-vault"}], on_load_remote=lambda: remote)
    qtbot.addWidget(dlg)
    dlg.show()
    QApplication.processEvents()

    assert dlg.tabs.count() == 2
    assert dlg.tabs.currentIndex() == 0
    assert QApplication.focusWidget() is dlg.local_list_widget

    QTest.keyClick(dlg.local_list_widget, Qt.Key_Tab, Qt.ControlModifier)
    QApplication.processEvents()
    assert dlg.tabs.currentIndex() == 1
    assert QApplication.focusWidget() is dlg.remote_list_widget

    QTest.keyClick(dlg.remote_list_widget, Qt.Key_Tab, Qt.ControlModifier)
    QApplication.processEvents()
    assert dlg.tabs.currentIndex() == 0
    assert QApplication.focusWidget() is dlg.local_list_widget
