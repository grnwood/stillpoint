from PySide6.QtCore import Qt

from sp.app.ui import keyboard_shortcuts


def test_vi_navigation_chord_uses_command_without_shift_on_macos(monkeypatch) -> None:
    monkeypatch.setattr(keyboard_shortcuts.platform, "system", lambda: "Darwin")

    assert keyboard_shortcuts.vi_navigation_sequences() == ("Meta+J", "Meta+K")
    assert keyboard_shortcuts.is_vi_navigation_chord(Qt.MetaModifier)
    # An incidental Shift (Windows/Linux muscle memory) is tolerated on macOS.
    assert keyboard_shortcuts.is_vi_navigation_chord(Qt.MetaModifier | Qt.ShiftModifier)
    # Physical Cmd (Qt.ControlModifier on macOS) must never match, to avoid
    # colliding with the Cmd+J "Jump to Page" shortcut.
    assert not keyboard_shortcuts.is_vi_navigation_chord(Qt.ControlModifier)


def test_vi_navigation_chord_uses_control_shift_on_windows_linux(monkeypatch) -> None:
    monkeypatch.setattr(keyboard_shortcuts.platform, "system", lambda: "Linux")

    assert keyboard_shortcuts.vi_navigation_sequences() == ("Ctrl+Shift+J", "Ctrl+Shift+K")
    assert keyboard_shortcuts.is_vi_navigation_chord(Qt.ControlModifier | Qt.ShiftModifier)
    assert not keyboard_shortcuts.is_vi_navigation_chord(Qt.MetaModifier)
