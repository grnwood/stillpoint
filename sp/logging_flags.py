from __future__ import annotations

import os
from typing import Dict, Iterable

# Area flags exposed as SP_LOG_<AREA>.
# All default to False to keep stdout quiet unless explicitly enabled.
AREA_DEFAULTS: Dict[str, bool] = {
    "startup": False,
    "api_client": False,
    "api_server": False,
    "auth_security": False,
    "vault_io": False,
    "autosave": False,
    "navigation": False,
    "sorting_reorder": False,
    "editor_markdown": False,
    "editor_render": False,
    "attachments_media": False,
    "search_index": False,
    "tasks_calendar": False,
    "remote_vaults": False,
    "ai_chat": False,
    "rag_vector": False,
    "diagrams": False,
    "ui_state": False,
    "performance": False,
}

def _is_truthy(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() not in {"", "0", "false", "no", "off"}


def _area_key(area: str) -> str:
    return area.strip().lower().replace("-", "_")


def _area_env(area: str) -> str:
    return f"SP_LOG_{_area_key(area).upper()}"


def log_enabled(area: str, default: bool | None = None) -> bool:
    """Return whether detailed logging for an area is enabled.

    Precedence:
    1) SP_LOG_<AREA>
    2) SP_LOG_ALL (unless SP_LOG_<AREA> explicitly set false)
    3) Area default / supplied default
    """
    key = _area_key(area)
    area_env = _area_env(key)

    raw_area = os.getenv(area_env)
    if raw_area is not None:
        return _is_truthy(raw_area)

    raw_all = os.getenv("SP_LOG_ALL")
    if _is_truthy(raw_all):
        return True

    if default is not None:
        return default
    return AREA_DEFAULTS.get(key, False)


def area_env_names() -> Iterable[str]:
    for area in AREA_DEFAULTS:
        yield _area_env(area)
