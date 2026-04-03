from __future__ import annotations

from sp.app import config


def test_load_hr_line_height_defaults_to_one_point(monkeypatch) -> None:
    monkeypatch.setattr(config, "_read_global_config", lambda: {})

    assert config.load_hr_line_height() == 1.0


def test_save_hr_line_height_falls_back_to_one_point(monkeypatch) -> None:
    updates: list[dict[str, float]] = []
    monkeypatch.setattr(config, "_update_global_config", lambda payload: updates.append(dict(payload)))

    config.save_hr_line_height("not-a-number")

    assert updates == [{"hr_line_height": 1.0}]
