from __future__ import annotations

import sqlite3

from sp.app import config


def test_vault_accent_color_persists_in_vault_db(tmp_path) -> None:
    config.set_active_vault(str(tmp_path))
    try:
        config.save_vault_accent_color("#3B82F6")
        assert config.load_vault_accent_color() == "#3B82F6"

        db_path = tmp_path / ".stillpoint" / "settings.db"
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT value FROM kv WHERE key = ?",
                ("vault_accent_color",),
            ).fetchone()
        assert row and row[0] == "#3B82F6"

        config.save_vault_accent_color(None)
        assert config.load_vault_accent_color() is None
    finally:
        config.set_active_vault(None)
