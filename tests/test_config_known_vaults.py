from __future__ import annotations

from sp.app import config


def test_load_known_vaults_supports_legacy_string_entries(monkeypatch) -> None:
    monkeypatch.setattr(
        config,
        "_read_global_config",
        lambda: {
            "vaults": [
                "/tmp/LegacyVault",
                {"name": "Named Vault", "path": "/tmp/NamedVault", "last_opened_at": "2026-03-31T12:00:00+00:00"},
                123,
            ]
        },
    )

    assert config.load_known_vaults() == [
        {"name": "LegacyVault", "path": "/tmp/LegacyVault"},
        {"name": "Named Vault", "path": "/tmp/NamedVault", "last_opened_at": "2026-03-31T12:00:00+00:00"},
    ]
