from sp.app.feature_flags import terminal_integration_enabled


def test_terminal_integration_is_enabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv("SP_DISABLE_TERMINAL", raising=False)

    assert terminal_integration_enabled() is True


def test_terminal_integration_can_be_disabled_with_environment_flag(monkeypatch) -> None:
    monkeypatch.setenv("SP_DISABLE_TERMINAL", "true")

    assert terminal_integration_enabled() is False
