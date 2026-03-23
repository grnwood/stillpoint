from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QComboBox


def test_markdown_image_max_width_defaults_to_900_and_persists(monkeypatch, tmp_path) -> None:
    from sp.app import config

    monkeypatch.setattr(config, "GLOBAL_CONFIG", tmp_path / "stillpoint_config.json")

    assert config.load_markdown_image_max_width() == 900

    config.save_markdown_image_max_width(1200)

    assert config.load_markdown_image_max_width() == 1200


def test_preferences_dialog_loads_markdown_image_max_width_default(qtbot, monkeypatch) -> None:
    from sp.app.ui.preferences_dialog import PreferencesDialog
    from sp.app import config

    monkeypatch.setattr(config, "load_markdown_image_max_width", lambda: 900)

    dialog = PreferencesDialog()
    qtbot.addWidget(dialog)

    assert dialog.markdown_image_max_width_combo.currentData() == 900


def test_effective_theme_preference_prefers_vault_override(monkeypatch) -> None:
    from sp.app import config

    monkeypatch.setattr(config, "load_theme_preference", lambda: "light-theme.json")
    monkeypatch.setattr(config, "load_vault_theme_override", lambda: "sunset-blaze.json")

    assert config.load_effective_theme_preference() == "sunset-blaze.json"


def test_vault_preferences_dialog_loads_theme_override(qtbot, monkeypatch) -> None:
    from sp.app.ui.vault_preferences_dialog import VaultPreferencesDialog
    from sp.app import config

    monkeypatch.setattr(
        VaultPreferencesDialog,
        "_list_theme_files",
        lambda self: [Path("/tmp/charcoal-copper.json")],
    )
    monkeypatch.setattr(config, "load_vault_theme_override", lambda: "charcoal-copper.json")
    monkeypatch.setattr(config, "load_vault_accent_color", lambda: None)
    monkeypatch.setattr(config, "load_vault_feature_tasks_override", lambda: None)
    monkeypatch.setattr(config, "load_vault_feature_calendar_override", lambda: None)
    monkeypatch.setattr(config, "load_vault_feature_link_navigator_override", lambda: None)
    monkeypatch.setattr(config, "load_vault_feature_tags_override", lambda: None)
    monkeypatch.setattr(config, "load_vault_feature_remember_cursor_position_override", lambda: None)
    monkeypatch.setattr(config, "load_vault_enable_ai_chats_override", lambda: None)
    monkeypatch.setattr(config, "load_vault_force_read_only", lambda: False)

    dialog = VaultPreferencesDialog()
    qtbot.addWidget(dialog)

    assert dialog.vault_theme_combo.currentData() == "charcoal-copper.json"


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
