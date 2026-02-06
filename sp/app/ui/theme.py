from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PySide6.QtGui import QColor

from sp.app import config

_THEME_CACHE: dict[str, Any] | None = None
_THEME_CACHE_PATH: Path | None = None


def _default_theme_path() -> Path:
    return Path(__file__).resolve().parents[1] / "theme-config.json"


def default_theme_path() -> Path:
    return _default_theme_path()


def _theme_dir() -> Path:
    return Path.home() / ".stillpoint" / "themes"


def _resolve_theme_path() -> Path:
    theme_name = config.load_theme_preference()
    if not theme_name or theme_name == "default":
        return _default_theme_path()
    candidate = Path(theme_name)
    if candidate.suffix.lower() != ".json":
        candidate = candidate.with_suffix(".json")
    if not candidate.is_absolute():
        candidate = _theme_dir() / candidate.name
    if candidate.exists():
        return candidate
    return _default_theme_path()


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _load_theme() -> dict[str, Any]:
    global _THEME_CACHE, _THEME_CACHE_PATH
    path = _resolve_theme_path()
    if _THEME_CACHE is not None and _THEME_CACHE_PATH == path:
        return _THEME_CACHE
    base_theme = _load_json(_default_theme_path())
    if path == _default_theme_path():
        _THEME_CACHE = base_theme
        _THEME_CACHE_PATH = path
        return _THEME_CACHE
    override = _load_json(path)
    _THEME_CACHE = _deep_merge(base_theme, override) if override else base_theme
    _THEME_CACHE_PATH = path
    return _THEME_CACHE


def theme_value(path: str, default: Any = None) -> Any:
    data = _load_theme()
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def theme_color(path: str, default: str | QColor | None = None) -> QColor:
    value = theme_value(path, default)
    if isinstance(value, QColor):
        return value
    if value is None:
        return QColor()
    return QColor(str(value))


def reload_theme() -> None:
    global _THEME_CACHE, _THEME_CACHE_PATH
    _THEME_CACHE = None
    _THEME_CACHE_PATH = None
