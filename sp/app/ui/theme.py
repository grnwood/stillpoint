from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PySide6.QtGui import QColor
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication, QMenu

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
    theme_name = config.load_effective_theme_preference()
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


def _resolved_palette(source: Any = None) -> QPalette:
    if source is not None:
        try:
            palette = source.palette()
            if isinstance(palette, QPalette):
                return QPalette(palette)
        except Exception:
            pass
    app = QApplication.instance()
    if app is not None:
        return QPalette(app.palette())
    return QPalette()


def apply_menu_theme(menu: QMenu, palette_source: Any = None) -> None:
    palette = _resolved_palette(palette_source if palette_source is not None else menu.parentWidget())
    menu.setPalette(palette)

    bg = str(theme_value("context_menu.bg", palette.color(QPalette.ColorRole.Window).name()))
    text = str(theme_value("context_menu.text", palette.color(QPalette.ColorRole.Text).name()))
    border = str(theme_value("context_menu.border", palette.color(QPalette.ColorRole.Mid).name()))
    separator = str(theme_value("context_menu.separator", palette.color(QPalette.ColorRole.Midlight).name()))
    selection_bg = str(theme_value("context_menu.selection_bg", palette.color(QPalette.ColorRole.Highlight).name()))
    selection_text = str(
        theme_value("context_menu.selection_text", palette.color(QPalette.ColorRole.HighlightedText).name())
    )
    disabled_text = str(
        theme_value(
            "context_menu.disabled_text",
            palette.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text).name(),
        )
    )
    section_text = str(theme_value("context_menu.section_text", disabled_text))
    section_border = str(theme_value("context_menu.section_border", separator))

    menu.setStyleSheet(
        "QMenu {"
        f" background: {bg};"
        f" color: {text};"
        f" border: 1px solid {border};"
        " padding: 4px 0px;"
        " }"
        "QMenu::item {"
        " background: transparent;"
        f" color: {text};"
        " padding: 6px 22px 6px 22px;"
        " margin: 1px 6px;"
        " border-radius: 4px;"
        " }"
        "QMenu::item:selected {"
        f" background: {selection_bg};"
        f" color: {selection_text};"
        " }"
        "QMenu::item:disabled {"
        f" color: {disabled_text};"
        " background: transparent;"
        " }"
        "QMenu::separator {"
        " height: 1px;"
        f" background: {separator};"
        " margin: 6px 12px;"
        " }"
        "QMenu::section {"
        f" color: {section_text};"
        " font-size: 9px;"
        " letter-spacing: 1px;"
        " padding: 8px 16px 4px 16px;"
        f" border-top: 1px solid {section_border};"
        " text-align: center;"
        " text-transform: uppercase;"
        " }"
    )


def apply_qt_palette(app: QApplication) -> None:
    """Apply a Qt palette derived from the currently effective StillPoint theme."""
    base_bg = str(theme_value("markdown_editor.base.bg", "#0b0b0b"))
    base_text = str(theme_value("markdown_editor.base.text", "#d6f5d6"))
    selection_bg = str(theme_value("markdown_editor.base.selection_bg", "#2f4c74"))
    selection_text = str(theme_value("markdown_editor.base.selection_text", "#ffffff"))
    window_bg = str(theme_value("page_editor_window.base.bg", base_bg))
    pal = app.palette()
    pal.setColor(QPalette.ColorRole.Window, QColor(window_bg))
    pal.setColor(QPalette.ColorRole.Base, QColor(base_bg))
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor(base_bg))
    pal.setColor(QPalette.ColorRole.Button, QColor(window_bg))
    pal.setColor(QPalette.ColorRole.WindowText, QColor(base_text))
    pal.setColor(QPalette.ColorRole.Text, QColor(base_text))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor(base_text))
    pal.setColor(QPalette.ColorRole.Highlight, QColor(selection_bg))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor(selection_text))
    app.setPalette(pal)
