from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication, QMenu

from sp.app import config

_THEME_CACHE: dict[str, Any] | None = None
_THEME_CACHE_PATH: Path | None = None


def _default_theme_path() -> Path:
    return Path(__file__).resolve().parents[1] / "theme-config.json"


def _bundled_theme_path(theme_name: str | None) -> Path:
    app_dir = Path(__file__).resolve().parents[1]
    name = (theme_name or "").strip()
    if not name or name == "default":
        return _default_theme_path()
    candidate = Path(name)
    if candidate.suffix.lower() != ".json":
        candidate = candidate.with_suffix(".json")
    bundled = app_dir / candidate.name
    if bundled.exists():
        return bundled
    return _default_theme_path()


def default_theme_path() -> Path:
    return _default_theme_path()


def _theme_dir() -> Path:
    return Path.home() / ".stillpoint" / "themes"


def _resolve_theme_path(theme_name: str | None = None) -> Path:
    if theme_name is None:
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
    theme_name = (
        os.environ.get("SP_FOLDER_NAVIGATOR_THEME_OVERRIDE")
        or config.load_effective_theme_preference()
    )
    path = _resolve_theme_path(theme_name)
    if _THEME_CACHE is not None and _THEME_CACHE_PATH == path:
        return _THEME_CACHE
    base_path = _bundled_theme_path(theme_name)
    base_theme = _load_json(base_path)
    if path == base_path:
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


def _rgba(color: QColor, alpha: int) -> str:
    """Return a QSS-safe rgba value while preserving the source hue."""
    return f"rgba({color.red()}, {color.green()}, {color.blue()}, {max(0, min(255, int(alpha)))})"


def _mixed_color(base: QColor, target: QColor, amount: float) -> str:
    """Blend two palette colors into an opaque tab surface."""
    return QColor(
        round(base.red() * (1 - amount) + target.red() * amount),
        round(base.green() * (1 - amount) + target.green() * amount),
        round(base.blue() * (1 - amount) + target.blue() * amount),
    ).name()


def chrome_colors(source: Any = None, accent_color: str | QColor | None = None) -> dict[str, str]:
    """Resolve the small semantic color set shared by app chrome.

    Content views retain their own theme-specific colors.  These values are for
    the framing around them: tabs, rails, utility headers, and selection states.
    """
    palette = _resolved_palette(source)
    accent = QColor(accent_color) if accent_color is not None else theme_color(
        "ui.chrome.accent",
        theme_value("main_window.utility_header.accent", palette.color(QPalette.Highlight).name()),
    )
    if not accent.isValid():
        accent = palette.color(QPalette.Highlight)
    border = theme_color("ui.chrome.border", palette.color(QPalette.Mid).name())
    if not border.isValid():
        border = palette.color(QPalette.Mid)
    base = palette.color(QPalette.Base)
    text = palette.color(QPalette.Text)
    dark = base.lightness() < 128
    return {
        "window": palette.color(QPalette.Window).name(),
        "base": palette.color(QPalette.Base).name(),
        "alternate": palette.color(QPalette.AlternateBase).name(),
        "text": palette.color(QPalette.Text).name(),
        "muted": palette.color(QPalette.Mid).name(),
        "border": border.name(),
        "accent": accent.name(),
        "hover": _rgba(accent, int(theme_value("ui.chrome.hover_alpha", 28))),
        "selected": _rgba(accent, int(theme_value("ui.chrome.selection_alpha", 72))),
        "rail_inactive": _mixed_color(base, text, 0.10 if dark else 0.14),
        "rail_active": _mixed_color(base, text, 0.55) if dark else base.name(),
        "rail_hover": _mixed_color(base, text, 0.24 if dark else 0.08),
        "rail_inactive_text": text.name(),
        "rail_active_text": "#101820" if dark else text.name(),
    }


def tab_widget_stylesheet(
    source: Any = None,
    *,
    object_name: str = "",
    accent_color: str | QColor | None = None,
    pane_border: str | QColor | None = None,
    readable_rail_tabs: bool = False,
) -> str:
    """Build the restrained tab treatment shared by StillPoint windows."""
    colors = chrome_colors(source, accent_color)
    pane_color = QColor(pane_border) if pane_border is not None else QColor(colors["border"])
    border = pane_color.name() if pane_color.isValid() else colors["border"]
    widget = f"QTabWidget#{object_name}" if object_name else "QTabWidget"
    tab = f"{widget} QTabBar::tab"
    radius = int(theme_value("ui.chrome.radius_px", 4))
    horizontal = int(theme_value("ui.tabs.horizontal_padding_px", 10))
    vertical = int(theme_value("ui.tabs.vertical_padding_px", 5))
    if readable_rail_tabs:
        return (
            f"{widget}::pane {{ border: 1px solid {border}; border-radius: {radius}px; "
            f"background: {colors['base']}; }}"
            f"{tab} {{ background: {colors['rail_inactive']}; color: {colors['rail_inactive_text']}; "
            f"border: 1px solid {colors['border']}; "
            f"border-bottom: 2px solid {colors['border']}; "
            f"border-radius: {radius}px {radius}px 0 0; "
            f"padding: {vertical}px {horizontal}px; margin-right: 1px; }}"
            f"{tab}:selected {{ background: {colors['rail_active']}; color: {colors['rail_active_text']}; "
            f"border: 1px solid {colors['accent']}; "
            f"border-bottom: 2px solid {colors['accent']}; font-weight: 600; }}"
            f"{tab}:!selected:hover {{ background: {colors['rail_hover']}; color: {colors['rail_inactive_text']}; }}"
        )
    return (
        f"{widget}::pane {{ border: 1px solid {border}; border-radius: {radius}px; "
        f"background: {colors['base']}; }}"
        f"{tab} {{ background: transparent; color: {colors['muted']}; border: 0; "
        f"border-bottom: 2px solid transparent; padding: {vertical}px {horizontal}px; "
        "margin-right: 1px; }"
        f"{tab}:selected {{ background: {colors['alternate']}; color: {colors['text']}; "
        f"border-bottom: 2px solid {colors['accent']}; font-weight: 600; }}"
        f"{tab}:!selected:hover {{ background: {colors['hover']}; color: {colors['text']}; }}"
    )


def tree_view_stylesheet(
    source: Any = None,
    *,
    accent_color: str | QColor | None = None,
    focused: bool = False,
) -> str:
    """Build a quiet, keyboard-friendly tree/list surface."""
    colors = chrome_colors(source, accent_color)
    border = colors["accent"] if focused else colors["border"]
    radius = int(theme_value("ui.chrome.radius_px", 4))
    horizontal = int(theme_value("ui.tree.horizontal_padding_px", 6))
    vertical = int(theme_value("ui.tree.vertical_padding_px", 3))
    return (
        f"QTreeView {{ border: 1px solid {border}; border-radius: {radius}px; "
        f"background: {colors['base']}; color: {colors['text']}; }}"
        f"QTreeView::viewport {{ background: {colors['base']}; }}"
        f"QTreeView::item {{ padding: {vertical}px {horizontal}px; border: 0; "
        f"border-radius: {radius}px; }}"
        f"QTreeView::item:hover {{ background: {colors['hover']}; }}"
        f"QTreeView::item:selected, QTreeView::item:selected:active, "
        f"QTreeView::item:selected:!active {{ background: {colors['selected']}; "
        f"color: {colors['text']}; }}"
    )


def status_bar_stylesheet(source: Any = None) -> str:
    """Build a low-noise status surface shared by both desktop windows."""
    colors = chrome_colors(source)
    horizontal = int(theme_value("ui.status_bar.horizontal_padding_px", 6))
    vertical = int(theme_value("ui.status_bar.vertical_padding_px", 2))
    return (
        f"QStatusBar {{ color: {colors['muted']}; background: {colors['window']}; "
        f"border-top: 1px solid {colors['border']}; padding: {vertical}px {horizontal}px; }}"
        "QStatusBar::item { border: 0; }"
    )


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
    # On macOS, native window chrome and toolbars follow Qt's color-scheme hint,
    # not just the application palette. Keep that hint aligned with the selected
    # StillPoint theme so an explicit light theme does not retain dark title-bar
    # and toolbar rendering from the host appearance (and vice versa).
    window_color = QColor(window_bg)
    if window_color.isValid():
        scheme = Qt.ColorScheme.Light if window_color.lightness() >= 128 else Qt.ColorScheme.Dark
        try:
            app.styleHints().setColorScheme(scheme)
        except (AttributeError, RuntimeError):
            # setColorScheme is unavailable on older Qt versions.
            pass
    pal = app.palette()
    pal.setColor(QPalette.ColorRole.Window, window_color)
    pal.setColor(QPalette.ColorRole.Base, QColor(base_bg))
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor(base_bg))
    pal.setColor(QPalette.ColorRole.Button, QColor(window_bg))
    pal.setColor(QPalette.ColorRole.WindowText, QColor(base_text))
    pal.setColor(QPalette.ColorRole.Text, QColor(base_text))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor(base_text))
    pal.setColor(QPalette.ColorRole.Highlight, QColor(selection_bg))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor(selection_text))
    app.setPalette(pal)
