from __future__ import annotations

from sp.app import config


def test_reading_view_theme_colors_default_off(monkeypatch) -> None:
    monkeypatch.setattr(config, "_read_global_config", lambda: {})

    assert config.load_reading_view_use_theme_colors() is False


def test_reading_view_theme_colors_loads_and_saves(monkeypatch) -> None:
    monkeypatch.setattr(
        config,
        "_read_global_config",
        lambda: {"reading_view_use_theme_colors": "true"},
    )
    updates: list[dict[str, bool]] = []
    monkeypatch.setattr(
        config,
        "_update_global_config",
        lambda payload: updates.append(dict(payload)),
    )

    assert config.load_reading_view_use_theme_colors() is True
    config.save_reading_view_use_theme_colors(False)

    assert updates == [{"reading_view_use_theme_colors": False}]
