from __future__ import annotations

from pathlib import Path

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


def _write_template_tree(root: Path) -> None:
    (root / "Default.txt").write_text("default\n", encoding="utf-8")
    folder_template = root / "folders" / "Category" / "Template"
    folder_template.mkdir(parents=True, exist_ok=True)
    (folder_template / "template.txt").write_text("folder\n", encoding="utf-8")


def test_ensure_user_template_files_bootstraps_missing_directory(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(app_main.Path, "home", lambda: tmp_path)
    bundled = tmp_path / "bundled_templates"
    bundled.mkdir(parents=True, exist_ok=True)
    _write_template_tree(bundled)
    monkeypatch.setattr(app_main, "_bundled_user_templates_dir", lambda: bundled)

    copied = app_main._ensure_user_template_files()

    user_templates = tmp_path / ".stillpoint" / "templates"
    assert copied is True
    assert (user_templates / "Default.txt").read_text(encoding="utf-8") == "default\n"
    assert (user_templates / "folders" / "Category" / "Template" / "template.txt").exists()


def test_ensure_user_template_files_bootstraps_empty_directory(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(app_main.Path, "home", lambda: tmp_path)
    user_templates = tmp_path / ".stillpoint" / "templates"
    user_templates.mkdir(parents=True, exist_ok=True)
    bundled = tmp_path / "bundled_templates"
    bundled.mkdir(parents=True, exist_ok=True)
    _write_template_tree(bundled)
    monkeypatch.setattr(app_main, "_bundled_user_templates_dir", lambda: bundled)

    copied = app_main._ensure_user_template_files()

    assert copied is True
    assert (user_templates / "Default.txt").read_text(encoding="utf-8") == "default\n"


def test_ensure_user_template_files_preserves_non_empty_directory(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(app_main.Path, "home", lambda: tmp_path)
    user_templates = tmp_path / ".stillpoint" / "templates"
    user_templates.mkdir(parents=True, exist_ok=True)
    sentinel = user_templates / "Custom.txt"
    sentinel.write_text("custom\n", encoding="utf-8")
    bundled = tmp_path / "bundled_templates"
    bundled.mkdir(parents=True, exist_ok=True)
    _write_template_tree(bundled)
    monkeypatch.setattr(app_main, "_bundled_user_templates_dir", lambda: bundled)

    copied = app_main._ensure_user_template_files()

    assert copied is False
    assert sentinel.read_text(encoding="utf-8") == "custom\n"
    assert not (user_templates / "Default.txt").exists()
