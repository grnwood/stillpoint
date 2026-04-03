from sp.app.ui.markdown_editor import hr_overlay_disabled


def test_hr_overlay_disabled_by_default_on_linux(monkeypatch) -> None:
    monkeypatch.delenv("SP_DISABLE_HR_OVERLAY", raising=False)
    monkeypatch.setattr("sp.app.ui.markdown_editor.sys.platform", "linux")

    assert hr_overlay_disabled() is True


def test_hr_overlay_can_be_forced_on_with_env(monkeypatch) -> None:
    monkeypatch.setenv("SP_DISABLE_HR_OVERLAY", "0")
    monkeypatch.setattr("sp.app.ui.markdown_editor.sys.platform", "linux")

    assert hr_overlay_disabled() is False


def test_hr_overlay_can_be_forced_off_with_env(monkeypatch) -> None:
    monkeypatch.setenv("SP_DISABLE_HR_OVERLAY", "true")
    monkeypatch.setattr("sp.app.ui.markdown_editor.sys.platform", "darwin")

    assert hr_overlay_disabled() is True
