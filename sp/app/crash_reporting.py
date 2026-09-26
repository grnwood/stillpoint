"""Shared native-crash logging for StillPoint GUI processes."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile


_FAULTHANDLER_FILE = None


def enable_faulthandler_log() -> Path | None:
    """Append fatal Python/native crash traces to StillPoint's shared log."""
    global _FAULTHANDLER_FILE
    if _FAULTHANDLER_FILE is not None:
        return Path(os.environ["STILLPOINT_FAULTHANDLER_LOG"])
    if os.getenv("SP_DISABLE_FAULTHANDLER", "0") not in ("0", "false", "False", ""):
        return None
    try:
        import faulthandler

        log_path = Path(
            os.getenv("STILLPOINT_FAULTHANDLER_LOG")
            or (Path(tempfile.gettempdir()) / "stillpoint-faulthandler.log")
        )
        log_path.parent.mkdir(parents=True, exist_ok=True)
        _FAULTHANDLER_FILE = open(log_path, "a", buffering=1)
        faulthandler.enable(_FAULTHANDLER_FILE)
        os.environ["STILLPOINT_FAULTHANDLER_LOG"] = str(log_path)
        return log_path
    except Exception:
        return None
