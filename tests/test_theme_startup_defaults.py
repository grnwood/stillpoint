from __future__ import annotations

from sp.app import main as app_main


def test_ensure_user_theme_files_seeds_missing_files_in_existing_dir(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(app_main.Path, "home", lambda: tmp_path)
    theme_dir = tmp_path / ".stillpoint" / "themes"
    theme_dir.mkdir(parents=True, exist_ok=True)

    sentinel = '{"name":"custom-dark"}'
    dark_path = theme_dir / "dark-theme.json"
    dark_path.write_text(sentinel, encoding="utf-8")

    copied = app_main._ensure_user_theme_files()
    assert copied is True
    assert dark_path.read_text(encoding="utf-8") == sentinel
    assert (theme_dir / "light-theme.json").exists()

    copied_again = app_main._ensure_user_theme_files()
    assert copied_again is False


def test_apply_startup_theme_defaults_sets_os_theme_when_preference_is_default(qapp, monkeypatch) -> None:
    monkeypatch.setattr(app_main, "_detect_light_color_scheme", lambda _app: (True, "test"))
    monkeypatch.setattr(app_main, "_ensure_user_theme_files", lambda: True)
    monkeypatch.setattr(app_main.config, "load_theme_preference", lambda: "default")

    saved: list[str] = []
    monkeypatch.setattr(app_main.config, "save_theme_preference", lambda value: saved.append(value))
    monkeypatch.setattr(app_main, "_startup", lambda _msg: None)

    app_main._apply_startup_theme_defaults(qapp)
    assert saved == ["light-theme.json"]


def test_apply_startup_theme_defaults_preserves_explicit_selection(qapp, monkeypatch) -> None:
    monkeypatch.setattr(app_main, "_detect_light_color_scheme", lambda _app: (False, "test"))
    monkeypatch.setattr(app_main, "_ensure_user_theme_files", lambda: True)
    monkeypatch.setattr(app_main.config, "load_theme_preference", lambda: "light-theme.json")

    saved: list[str] = []
    monkeypatch.setattr(app_main.config, "save_theme_preference", lambda value: saved.append(value))
    monkeypatch.setattr(app_main, "_startup", lambda _msg: None)

    app_main._apply_startup_theme_defaults(qapp)
    assert saved == []
