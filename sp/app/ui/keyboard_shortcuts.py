"""Platform-specific keyboard shortcut helpers."""

from __future__ import annotations

import platform

from PySide6.QtCore import Qt


def vi_navigation_sequences() -> tuple[str, str]:
    """Return the forward and backward vi navigation shortcut sequences."""
    if platform.system() == "Darwin":
        return "Meta+J", "Meta+K"
    return "Ctrl+Shift+J", "Ctrl+Shift+K"


def is_vi_navigation_chord(modifiers: Qt.KeyboardModifiers) -> bool:
    """Return whether modifiers invoke the platform's vi navigation chord."""
    if platform.system() == "Darwin":
        # Physical Control (Qt.MetaModifier, due to Qt's Ctrl/Cmd swap on macOS).
        # Tolerate an incidental Shift too, since Windows/Linux muscle memory is
        # Ctrl+Shift+J/K and physical Cmd (Qt.ControlModifier) must stay excluded
        # to avoid colliding with the Cmd+J "Jump to Page" shortcut.
        return bool(modifiers & Qt.MetaModifier) and not bool(modifiers & Qt.ControlModifier)
    return modifiers == (Qt.ControlModifier | Qt.ShiftModifier)


def history_cycle_sequences() -> tuple[str, str]:
    """Return QKeySequence text for forward/backward recent-page cycling.

    Registered as real QShortcut objects rather than detected ad-hoc in an
    event filter: on macOS, Cocoa's default text-view key bindings claim
    Control+Tab/Control+Shift+Tab for its own key-view navigation before a
    plain KeyPress ever reaches Qt's event filters. A QShortcut is matched
    through Qt's own shortcut/menu-equivalent mechanism, which takes
    priority over Cocoa's default key bindings.
    """
    if platform.system() == "Darwin":
        return "Meta+Tab", "Meta+Shift+Tab"
    return "Ctrl+Tab", "Ctrl+Shift+Tab"


def is_history_cycle_chord(modifiers: Qt.KeyboardModifiers) -> bool:
    """Return whether modifiers invoke the recent-page-cycle chord (Ctrl+Tab).

    On macOS, Qt.ControlModifier corresponds to physical Cmd, and Cmd+Tab is
    intercepted system-wide by the OS app switcher before it ever reaches Qt.
    Use physical Control (Qt.MetaModifier) instead so the shortcut is reachable.
    """
    if platform.system() == "Darwin":
        return bool(modifiers & Qt.MetaModifier) and not bool(modifiers & Qt.ControlModifier)
    return bool(modifiers & Qt.ControlModifier)


def history_cycle_modifier_release_key() -> int:
    """Return the Qt.Key released when the user lets go of the history-cycle modifier."""
    if platform.system() == "Darwin":
        # Physical Control releases Qt.Key_Meta on macOS (Ctrl/Cmd swap).
        return Qt.Key_Meta
    return Qt.Key_Control
