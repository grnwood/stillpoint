from __future__ import annotations

from PySide6.QtWidgets import QComboBox


def test_quick_capture_vaults_include_homebase_profiles_and_resolve_saved_ref(qtbot, monkeypatch) -> None:
    from sp.app.ui.preferences_dialog import PreferencesDialog
    from sp.app import config

    homebase_ref = "homebase::https://server::vault123::/vaults/hybrid"
    homebase_profiles = [
        {
            "id": homebase_ref,
            "name": "Hybrid Vault",
            "path": "/vaults/hybrid",
            "server_url": "https://server",
            "vault_id": "vault123",
            "kind": "homebase",
        }
    ]
    monkeypatch.setattr(config, "load_known_vaults", lambda: [])
    monkeypatch.setattr(config, "load_homebase_vault_profiles", lambda: homebase_profiles)
    monkeypatch.setattr(config, "load_remote_servers", lambda: [])
    monkeypatch.setattr(config, "load_quick_capture_vault", lambda: homebase_ref)

    class _Dummy:
        def __init__(self) -> None:
            self.quick_capture_vault_combo = QComboBox()

    dummy = _Dummy()
    qtbot.addWidget(dummy.quick_capture_vault_combo)

    PreferencesDialog._populate_quick_capture_vaults(dummy)

    values = [dummy.quick_capture_vault_combo.itemData(i) for i in range(dummy.quick_capture_vault_combo.count())]
    labels = [dummy.quick_capture_vault_combo.itemText(i) for i in range(dummy.quick_capture_vault_combo.count())]

    assert "/vaults/hybrid" in values
    assert any(label.startswith("[Homebase] Hybrid Vault") for label in labels)
    assert dummy.quick_capture_vault_combo.currentData() == "/vaults/hybrid"
