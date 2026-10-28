from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QComboBox, QDialogButtonBox, QMessageBox, QScrollArea


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


def test_preferences_dialog_reveals_discovered_mermaid_cli(qtbot, monkeypatch, tmp_path) -> None:
    from sp.app import config
    from sp.app.mermaid_renderer import MermaidRenderer
    from sp.app.ui.preferences_dialog import PreferencesDialog

    mmdc = tmp_path / "bin" / "mmdc"
    monkeypatch.setattr(config, "load_mermaid_mmdc_path", lambda: None)
    monkeypatch.setattr(MermaidRenderer, "discover_mmdc", lambda self: mmdc)

    dialog = PreferencesDialog()
    qtbot.addWidget(dialog)

    assert dialog.mermaid_mmdc_edit.text() == str(mmdc)


def test_mermaid_cli_path_persists_in_global_preferences(monkeypatch, tmp_path) -> None:
    from sp.app import config

    monkeypatch.setattr(config, "GLOBAL_CONFIG", tmp_path / "stillpoint_config.json")

    assert config.load_mermaid_mmdc_path() is None
    config.save_mermaid_mmdc_path("  /opt/homebrew/bin/mmdc  ")
    assert config.load_mermaid_mmdc_path() == "/opt/homebrew/bin/mmdc"


def test_preferences_dialog_keeps_actions_visible_when_height_is_constrained(qtbot, qapp) -> None:
    from sp.app.ui.preferences_dialog import PreferencesDialog

    dialog = PreferencesDialog()
    qtbot.addWidget(dialog)
    dialog.resize(640, 320)
    dialog.show()
    qapp.processEvents()

    current_page = dialog.stack.currentWidget()
    ok_button = dialog.button_box.button(QDialogButtonBox.Ok)

    assert isinstance(current_page, QScrollArea)
    assert current_page.verticalScrollBar().maximum() > 0
    assert dialog.height() == 320
    assert dialog.button_box.isVisibleTo(dialog)
    assert ok_button is not None
    assert ok_button.isVisibleTo(dialog)
    assert dialog.button_box.geometry().bottom() <= dialog.button_box.parentWidget().height()


def test_ai_model_help_controls_explain_their_scope(qtbot) -> None:
    from sp.app.ui.preferences_dialog import PreferencesDialog

    dialog = PreferencesDialog()
    qtbot.addWidget(dialog)

    assert dialog.default_model_help_btn.text() == "?"
    assert dialog.default_model_help_btn.accessibleName() == "About the chats and agents model"
    assert "AI chats and agents" in dialog.default_model_help_btn.toolTip()
    assert dialog.default_model_help_btn.accessibleDescription() == dialog.default_model_help_btn.toolTip()

    assert dialog.operations_model_help_btn.text() == "?"
    assert dialog.operations_model_help_btn.accessibleName() == "About the StillPoint operations model"
    assert "calendar AI insights" in dialog.operations_model_help_btn.toolTip()
    assert "Rename Auto (AI)" in dialog.operations_model_help_btn.toolTip()
    assert dialog.operations_model_help_btn.accessibleDescription() == dialog.operations_model_help_btn.toolTip()


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


def test_vault_preferences_git_mode_is_exclusive_and_persists(qtbot, monkeypatch, tmp_path) -> None:
    from sp.app import config, git_support
    from sp.app.ui.vault_preferences_dialog import VaultPreferencesDialog

    config.set_active_vault(str(tmp_path))
    try:
        git_support.initialize_repository(tmp_path)
        dialog = VaultPreferencesDialog()
        qtbot.addWidget(dialog)

        dialog.git_mode_radio.setChecked(True)
        dialog.git_auto_backup_checkbox.setChecked(True)
        dialog.git_auto_backup_interval_combo.setCurrentIndex(
            dialog.git_auto_backup_interval_combo.findData(30)
        )
        dialog.git_auto_publish_checkbox.setChecked(False)
        dialog.git_backup_on_close_checkbox.setChecked(False)

        assert dialog.selected_version_mode() == "git"
        assert dialog.homebase_mode_radio.isChecked() is False
        assert "cannot operate" in dialog.version_mode_warning.text()

        monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: QMessageBox.Yes)
        dialog.accept()

        assert config.load_vault_remote_mode() == "git"
        assert config.load_git_auto_backup_enabled() is True
        assert config.load_git_auto_backup_interval_minutes() == 30
        assert config.load_git_auto_publish_enabled() is False
        assert config.load_git_backup_on_close() is False
    finally:
        config.set_active_vault(None)


def test_vault_preferences_git_setup_initializes_repository(qtbot, tmp_path) -> None:
    from sp.app import config, git_support
    from sp.app.ui.vault_preferences_dialog import VaultPreferencesDialog

    config.set_active_vault(str(tmp_path))
    try:
        dialog = VaultPreferencesDialog()
        qtbot.addWidget(dialog)
        assert git_support.repository_root(tmp_path) is None

        dialog._initialize_git_repository()

        assert git_support.is_exact_repository(tmp_path)
        assert dialog.git_init_button.isEnabled() is False
    finally:
        config.set_active_vault(None)


def test_vault_preferences_suggests_existing_git_remote(qtbot, tmp_path) -> None:
    from sp.app import config, git_support
    from sp.app.ui.vault_preferences_dialog import VaultPreferencesDialog

    config.set_active_vault(str(tmp_path))
    try:
        git_support.initialize_repository(tmp_path)
        git_support.set_remote(
            tmp_path,
            "ssh://git.example.test/team/vault.git",
            name="work",
        )

        dialog = VaultPreferencesDialog()
        qtbot.addWidget(dialog)
        dialog.git_mode_radio.setChecked(True)

        assert "Existing repository detected" in dialog.git_status_label.text()
        assert dialog.git_remote_label.text() == "Remote (work):"
        assert dialog.git_remote_edit.text() == "ssh://git.example.test/team/vault.git"
    finally:
        config.set_active_vault(None)


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
