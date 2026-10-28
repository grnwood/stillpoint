from __future__ import annotations

from sp.app import config


def test_git_mode_adds_vault_rail_and_command_palette_actions(main_window, monkeypatch) -> None:
    monkeypatch.setattr(config, "load_vault_remote_mode", lambda default="none": "git")

    main_window._refresh_git_mode_ui()

    assert main_window.git_panel is not None
    assert main_window.left_tab_widget.tabText(
        main_window.left_tab_widget.indexOf(main_window.git_panel)
    ) == "Git"
    labels = [label for label, _action in main_window._collect_menu_actions()]
    assert "Vault / Save Changes…" in labels
    assert "Vault / Save Version…" in labels


def test_non_git_mode_removes_vault_rail_and_git_commands(main_window, monkeypatch) -> None:
    mode = {"value": "git"}
    monkeypatch.setattr(
        config,
        "load_vault_remote_mode",
        lambda default="none": mode["value"],
    )
    main_window._refresh_git_mode_ui()
    assert main_window.git_panel is not None

    mode["value"] = "none"
    main_window._refresh_git_mode_ui()

    assert main_window.git_panel is None
    labels = [label for label, _action in main_window._collect_menu_actions()]
    assert "Vault / Save Version…" not in labels
