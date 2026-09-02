from __future__ import annotations

import os


def terminal_integration_enabled() -> bool:
    """Return whether the embedded terminal is enabled for this process."""
    value = os.getenv("SP_DISABLE_TERMINAL", "").strip().casefold()
    return value not in {"1", "true", "yes", "on"}
