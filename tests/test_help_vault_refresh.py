from __future__ import annotations

from pathlib import Path


def _write_help_vault(root: Path, version: int, body: str) -> None:
    (root / ".stillpoint").mkdir(parents=True, exist_ok=True)
    (root / ".stillpoint" / "help_vault_version.txt").write_text(f"{version}\n", encoding="utf-8")
    (root / "Welcome").mkdir(parents=True, exist_ok=True)
    (root / "Welcome" / "Welcome.md").write_text(body, encoding="utf-8")
    (root / "help-vault.md").write_text("# help-vault\n", encoding="utf-8")


def test_help_vault_refreshes_when_embedded_version_is_newer(main_window, monkeypatch, tmp_path) -> None:
    src = tmp_path / "embedded-help"
    _write_help_vault(src, 2, "embedded")

    fake_home = tmp_path / "home"
    user_root = fake_home / ".stillpoint" / "help-vault"
    _write_help_vault(user_root, 1, "old-user-copy")

    monkeypatch.setattr(main_window, "_find_help_vault_template", lambda: src)
    monkeypatch.setattr("sp.app.ui.main_window.Path.home", lambda: fake_home)

    refreshed = main_window._ensure_user_help_vault()

    assert refreshed == user_root
    assert (user_root / ".stillpoint" / "help_vault_version.txt").read_text(encoding="utf-8").strip() == "2"
    assert (user_root / "Welcome" / "Welcome.md").read_text(encoding="utf-8") == "embedded"


def test_help_vault_populates_macos_shortcuts(main_window, monkeypatch, tmp_path) -> None:
    src = tmp_path / "embedded-help"
    _write_help_vault(src, 1, "embedded")
    shortcuts = src / "Shortcuts" / "Shortcuts.md"
    shortcuts.parent.mkdir()
    shortcuts.write_text(
        "\n".join(
            [
                "# Shortcuts",
                "<!-- StillPoint shortcut platform: template -->",
                "<!-- StillPoint shortcut platform notice -->",
                "- `Ctrl+S`",
                "- `Ctrl+Alt+D`",
                "- `Ctrl+Tab`",
                "- `Ctrl+Shift+Tab`",
                "- `Ctrl+Shift+J`",
                "- `Ctrl+Shift+K`",
                "<!-- StillPoint shortcut macos-only:start -->",
                "- On macOS: `Cmd+[`",
                "<!-- StillPoint shortcut macos-only:end -->",
            ]
        ),
        encoding="utf-8",
    )

    fake_home = tmp_path / "home"
    monkeypatch.setattr(main_window, "_find_help_vault_template", lambda: src)
    monkeypatch.setattr("sp.app.ui.main_window.Path.home", lambda: fake_home)
    monkeypatch.setattr("sp.app.ui.main_window.platform.system", lambda: "Darwin")

    user_root = main_window._ensure_user_help_vault()

    assert (user_root / "Shortcuts" / "Shortcuts.md").read_text(encoding="utf-8").splitlines() == [
        "# Shortcuts",
        "<!-- StillPoint shortcut platform: macos; revision: 5 -->",
        "> **macOS:** `Cmd` is the primary modifier and `Option` is the alternate modifier.",
        "- `Cmd+S`",
        "- `Cmd+Option+D`",
        "- `Ctrl+Tab`",
        "- `Ctrl+Shift+Tab`",
        "- `Cmd+J`",
        "- `Cmd+K`",
        "- On macOS: `Cmd+[`",
    ]


def test_help_vault_populates_windows_linux_shortcuts(main_window, monkeypatch, tmp_path) -> None:
    src = tmp_path / "embedded-help"
    _write_help_vault(src, 1, "embedded")
    shortcuts = src / "Shortcuts" / "Shortcuts.md"
    shortcuts.parent.mkdir()
    shortcuts.write_text(
        "\n".join(
            [
                "# Shortcuts",
                "<!-- StillPoint shortcut platform: template -->",
                "<!-- StillPoint shortcut platform notice -->",
                "- `Ctrl+S`",
                "<!-- StillPoint shortcut macos-only:start -->",
                "- On macOS: `Cmd+[`",
                "<!-- StillPoint shortcut macos-only:end -->",
            ]
        ),
        encoding="utf-8",
    )

    fake_home = tmp_path / "home"
    monkeypatch.setattr(main_window, "_find_help_vault_template", lambda: src)
    monkeypatch.setattr("sp.app.ui.main_window.Path.home", lambda: fake_home)
    monkeypatch.setattr("sp.app.ui.main_window.platform.system", lambda: "Linux")

    user_root = main_window._ensure_user_help_vault()

    assert (user_root / "Shortcuts" / "Shortcuts.md").read_text(encoding="utf-8").splitlines() == [
        "# Shortcuts",
        "<!-- StillPoint shortcut platform: windows-linux; revision: 5 -->",
        "> **Windows and Linux:** `Ctrl` is the primary modifier and `Alt` is the alternate modifier.",
        "- `Ctrl+S`",
    ]
