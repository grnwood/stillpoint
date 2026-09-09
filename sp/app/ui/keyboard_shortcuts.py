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
        return modifiers == Qt.MetaModifier
    return modifiers == (Qt.ControlModifier | Qt.ShiftModifier)
