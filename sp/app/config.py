from __future__ import annotations

import json
import os
import platform
import sqlite3
import re
import time
from collections import OrderedDict
from pathlib import Path
from threading import RLock
from typing import Iterable, Optional, Sequence

from sp.server.adapters.files import PAGE_SUFFIX, PAGE_SUFFIXES, strip_page_suffix

GLOBAL_CONFIG = Path.home() / ".stillpoint_config.json"

_ACTIVE_CONN: Optional[sqlite3.Connection] = None
_ACTIVE_ROOT: Optional[Path] = None
_TASK_FETCH_CACHE: OrderedDict[tuple, list[dict]] = OrderedDict()

_TASK_FETCH_CACHE_SIZE = 32
_TASKS_FTS_ENABLED = False
_TASKS_FTS_THRESHOLD = 500
_TASK_INDEX_VERSION = 0
_TASK_VERSION_LOCK = RLock()

_PAGE_CACHE_ROWS: list[dict] = []
_PAGE_RESULT_CACHE: OrderedDict[str, list[dict]] = OrderedDict()
_PAGE_RESULT_CACHE_LIMIT = 64


def _invalidate_page_cache() -> None:
    _PAGE_CACHE_ROWS.clear()
    _PAGE_RESULT_CACHE.clear()


def _prime_page_cache() -> None:
    conn = _get_conn()
    if not conn:
        _invalidate_page_cache()
        return
    try:
        rows = conn.execute(
            "SELECT path, title, path_ci, title_ci, COALESCE(updated, 0) FROM pages"
        ).fetchall()
    except sqlite3.OperationalError:
        _invalidate_page_cache()
        return
    _PAGE_CACHE_ROWS[:] = [
        {
            "path": row[0],
            "title": row[1] or "",
            "path_ci": (row[2] or row[0] or "").lower(),
            "title_ci": (row[3] or row[1] or "").lower(),
            "updated": float(row[4] or 0),
        }
        for row in rows
    ]
    _PAGE_RESULT_CACHE.clear()


def _ensure_page_cache_loaded() -> None:
    if _PAGE_CACHE_ROWS:
        return
    _prime_page_cache()


def _remember_page_search_result(term_lower: str, results: list[dict]) -> None:
    if term_lower == "":
        return
    if len(_PAGE_RESULT_CACHE) >= _PAGE_RESULT_CACHE_LIMIT:
        _PAGE_RESULT_CACHE.popitem(last=False)
    _PAGE_RESULT_CACHE[term_lower] = [dict(row) for row in results]


def _page_sort_key(row: dict, term_lower: str) -> tuple[int, float]:
    path_ci = row.get("path_ci", "").lower()
    title_ci = row.get("title_ci", "").lower()
    exact_path = f"/{term_lower}"
    child_prefix = f"{exact_path}/"
    priority = 4
    if path_ci == exact_path:
        priority = 0
    elif path_ci.startswith(child_prefix):
        priority = 1
    elif title_ci == term_lower:
        priority = 2
    elif term_lower and term_lower in title_ci:
        priority = 3
    return priority, -(row.get("updated") or 0.0)


def _filter_pages(candidates: list[dict], term_lower: str) -> list[dict]:
    matches = []
    for row in candidates:
        path_ci = row.get("path_ci", "")
        title_ci = row.get("title_ci", "")
        if term_lower and term_lower not in path_ci and term_lower not in title_ci:
            continue
        matches.append(row)
    return sorted(matches, key=lambda row: _page_sort_key(row, term_lower))


def _search_cached_pages(term_lower: str, limit: int) -> list[dict] | None:
    if not _PAGE_CACHE_ROWS:
        return None
    if term_lower in _PAGE_RESULT_CACHE:
        cached = _PAGE_RESULT_CACHE[term_lower]
        _PAGE_RESULT_CACHE.move_to_end(term_lower)
        return [
            {"path": row.get("path"), "title": row.get("title")}
            for row in cached[:limit]
        ]
    base_candidates: list[dict] = _PAGE_CACHE_ROWS
    for i in range(len(term_lower), 0, -1):
        prefix = term_lower[:i]
        if prefix in _PAGE_RESULT_CACHE:
            base_candidates = _PAGE_RESULT_CACHE[prefix]
            break
    results = _filter_pages(base_candidates, term_lower)
    _remember_page_search_result(term_lower, results)
    return [
        {"path": row.get("path"), "title": row.get("title")}
        for row in results[:limit]
    ]


def init_settings() -> None:
    GLOBAL_CONFIG.parent.mkdir(parents=True, exist_ok=True)


def _read_global_config() -> dict:
    """Return the parsed global config, or an empty dict on error/missing."""
    if not GLOBAL_CONFIG.exists():
        return {}
    try:
        return json.loads(GLOBAL_CONFIG.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}

def load_non_actionable_task_tags() -> str:
    """Load the configured non-actionable task tags as a space-separated string (default: '@wait @wt')."""
    payload = _read_global_config()
    tags = payload.get("non_actionable_task_tags")
    if isinstance(tags, str) and tags.strip():
        return tags.strip()
    return "@wait @wt"

def load_non_actionable_task_tags_list() -> list[str]:
    """Return configured non-actionable tags as lower-case names without leading @ symbols."""
    raw = load_non_actionable_task_tags()
    tags: list[str] = []
    for token in raw.replace(",", " ").split():
        cleaned = token.lstrip("@").strip()
        if cleaned:
            tags.append(cleaned.lower())
    return tags

def save_non_actionable_task_tags(tags: str) -> None:
    """Save the non-actionable task tags as a space-separated string."""
    _update_global_config({"non_actionable_task_tags": tags.strip()})

def load_last_vault() -> Optional[str]:
    payload = _read_global_config()
    last = payload.get("last_vault")
    return last if isinstance(last, str) else None


def save_last_vault(path: str) -> None:
    _update_global_config({"last_vault": path})


def _is_help_vault_path(path: str) -> bool:
    try:
        return Path(path).name.lower() == "help-vault"
    except Exception:
        return False


def load_known_vaults() -> list[dict[str, str]]:
    """Load previously used vaults with display names."""
    payload = _read_global_config()
    vaults = payload.get("vaults", [])
    result: list[dict[str, str]] = []
    if isinstance(vaults, list):
        for entry in vaults:
            if not isinstance(entry, dict):
                continue
            path = entry.get("path")
            if not path:
                continue
            if _is_help_vault_path(path):
                continue
            name = entry.get("name") or Path(path).name
            result.append({"name": str(name), "path": str(path)})
    return result


def remember_vault(path: str, name: Optional[str] = None) -> None:
    """Add or move a vault to the top of the known vault list."""
    normalized_path = str(Path(path))
    if _is_help_vault_path(normalized_path):
        payload = _read_global_config()
        vaults = payload.get("vaults", [])
        if isinstance(vaults, list):
            filtered = [
                entry
                for entry in vaults
                if not _is_help_vault_path(str(entry.get("path", "")))
            ]
            if filtered != vaults:
                _update_global_config({"vaults": filtered})
        return
    display_name = name or Path(normalized_path).name
    vaults = [v for v in load_known_vaults() if v.get("path") != normalized_path]
    vaults.insert(0, {"name": display_name, "path": normalized_path})
    _update_global_config({"vaults": vaults})


def delete_known_vault(path: str) -> None:
    """Remove a vault from the known list and clear default if it matched."""
    normalized_path = str(Path(path))
    payload = _read_global_config()
    vaults = payload.get("vaults", [])
    filtered: list[dict[str, str]] = []
    if isinstance(vaults, list):
        for entry in vaults:
            if not isinstance(entry, dict):
                continue
            if str(entry.get("path")) == normalized_path:
                continue
            path_val = entry.get("path")
            name_val = entry.get("name") or (Path(path_val).name if path_val else None)
            if path_val:
                filtered.append({"name": str(name_val), "path": str(path_val)})
    updates: dict = {"vaults": filtered}
    if payload.get("default_vault") == normalized_path:
        updates["default_vault"] = None
    _update_global_config(updates)


def load_remote_auth(server_key: str) -> dict[str, str]:
    """Return stored remote auth metadata for a server key."""
    payload = _read_global_config()
    auth_map = payload.get("remote_auth", {})
    if not isinstance(auth_map, dict):
        return {}
    entry = auth_map.get(server_key)
    return entry if isinstance(entry, dict) else {}


def save_remote_auth(server_key: str, refresh_token: Optional[str], username: Optional[str] = None) -> None:
    """Persist or clear the refresh token for a remote server."""
    payload = _read_global_config()
    auth_map = payload.get("remote_auth")
    if not isinstance(auth_map, dict):
        auth_map = {}
    if refresh_token:
        entry = {"refresh_token": refresh_token}
        if username:
            entry["username"] = username
        auth_map[server_key] = entry
    else:
        auth_map.pop(server_key, None)
    _update_global_config({"remote_auth": auth_map})


def load_remote_servers() -> list[dict[str, str]]:
    """Load configured remote servers."""
    payload = _read_global_config()
    servers = payload.get("remote_servers", [])
    result: list[dict[str, str]] = []
    if isinstance(servers, list):
        for entry in servers:
            if not isinstance(entry, dict):
                continue
            host = entry.get("host")
            port = entry.get("port")
            scheme = entry.get("scheme") or "http"
            verify_ssl = entry.get("verify_ssl", True)
            selected_vaults = entry.get("selected_vaults", [])
            if not host or not port:
                continue
            try:
                port_val = int(port)
            except (TypeError, ValueError):
                continue
            if not isinstance(selected_vaults, list):
                selected_vaults = []
            result.append(
                {
                    "host": str(host),
                    "port": str(port_val),
                    "scheme": str(scheme),
                    "verify_ssl": bool(verify_ssl),
                    "selected_vaults": [str(p) for p in selected_vaults if p],
                }
            )
    return result


def save_remote_servers(servers: list[dict[str, str]]) -> None:
    """Persist the configured remote servers list."""
    _update_global_config({"remote_servers": servers})


def add_remote_server(
    host: str,
    port: int,
    scheme: str = "http",
    verify_ssl: bool = True,
    selected_vaults: Optional[list[str]] = None,
    server_password_hash: Optional[str] = None,
) -> None:
    """Add or update a remote server entry."""
    host = host.strip()
    scheme = scheme.strip() or "http"
    payload = _read_global_config()
    servers = payload.get("remote_servers", [])
    if not isinstance(servers, list):
        servers = []
    filtered: list[dict[str, str]] = []
    for entry in servers:
        if not isinstance(entry, dict):
            continue
        if (
            entry.get("host") == host
            and str(entry.get("port")) == str(port)
            and entry.get("scheme") == scheme
        ):
            continue
        filtered.append(entry)
    new_entry = {
        "host": host,
        "port": str(port),
        "scheme": scheme,
        "verify_ssl": bool(verify_ssl),
        "selected_vaults": selected_vaults or [],
    }
    if server_password_hash:
        new_entry["server_password_hash"] = server_password_hash
    filtered.append(new_entry)
    _update_global_config({"remote_servers": filtered})


def delete_remote_server(host: str, port: int, scheme: str = "http") -> None:
    """Remove a remote server entry."""
    payload = _read_global_config()
    servers = payload.get("remote_servers", [])
    if not isinstance(servers, list):
        return
    filtered: list[dict[str, str]] = []
    for entry in servers:
        if not isinstance(entry, dict):
            continue
        if (
            entry.get("host") == host
            and str(entry.get("port")) == str(port)
            and entry.get("scheme") == scheme
        ):
            continue
        filtered.append(entry)
    _update_global_config({"remote_servers": filtered})


def get_server_password_hash(host: str, port: int, scheme: str = "http") -> Optional[str]:
    """Get stored server admin password hash for a remote server."""
    for entry in load_remote_servers():
        if (
            entry.get("host") == host
            and str(entry.get("port")) == str(port)
            and entry.get("scheme") == scheme
        ):
            return entry.get("server_password_hash")
    return None


def load_default_vault() -> Optional[str]:
    payload = _read_global_config()
    default_path = payload.get("default_vault")
    return default_path if isinstance(default_path, str) else None


def save_default_vault(path: Optional[str]) -> None:
    _update_global_config({"default_vault": path})


def load_vi_block_cursor_enabled() -> bool:
    """Load app-level preference for vi-mode block cursor. Defaults to True on Windows, False elsewhere."""
    if not GLOBAL_CONFIG.exists():
        return platform.system() == "Windows"
    try:
        payload = json.loads(GLOBAL_CONFIG.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return platform.system() == "Windows"
    if "vi_block_cursor" in payload:
        return bool(payload["vi_block_cursor"])
    return platform.system() == "Windows"


def save_vi_block_cursor_enabled(enabled: bool) -> None:
    """Save app-level preference for vi-mode block cursor."""
    _update_global_config({"vi_block_cursor": enabled})


def load_vi_mode_enabled() -> bool:
    """Return whether vi-mode navigation/editing is enabled globally (default: False)."""
    payload = _read_global_config()
    return bool(payload.get("enable_vi_mode", False))




def load_minimal_font_scan_enabled() -> bool:
    """Return whether minimal font scanning is enabled globally (default: True)."""
    payload = _read_global_config()
    val = payload.get("minimal_font_scan")
    if val is None:
        return True
    if isinstance(val, bool):
        return val
    return bool(val)


def load_rewrite_backlinks_on_move() -> bool:
    """Return whether to rewrite backlinks immediately when a page is moved (default: True)."""
    payload = _read_global_config()
    val = payload.get("rewrite_backlinks_on_move")
    if val is None:
        return True
    return bool(val)


def save_rewrite_backlinks_on_move(enabled: bool) -> None:
    """Persist preference to rewrite backlinks on page moves."""
    _update_global_config({"rewrite_backlinks_on_move": bool(enabled)})


def save_minimal_font_scan_enabled(enabled: bool) -> None:
    """Persist preference for enabling minimal font scanning."""
    _update_global_config({"minimal_font_scan": bool(enabled)})


def load_application_font() -> Optional[str]:
    """Return preferred application font family (None for system default)."""
    payload = _read_global_config()
    font = payload.get("application_font")
    if isinstance(font, str) and font.strip():
        return font.strip()
    return None


def save_application_font(font: Optional[str]) -> None:
    """Persist preferred application font family."""
    value = font.strip() if isinstance(font, str) and font.strip() else None
    _update_global_config({"application_font": value})


def load_application_font_size() -> Optional[int]:
    """Return preferred application font size (None for system default)."""
    payload = _read_global_config()
    if "application_font_size" not in payload:
        return 11
    size = payload.get("application_font_size")
    if size is None:
        return None
    try:
        return max(6, int(size))
    except (TypeError, ValueError):
        return 11


def save_application_font_size(size: Optional[int]) -> None:
    """Persist preferred application font size (None to use system default)."""
    if size is None:
        _update_global_config({"application_font_size": None})
        return
    try:
        value = max(6, int(size))
    except (TypeError, ValueError):
        value = None
    _update_global_config({"application_font_size": value})


def load_default_markdown_font() -> Optional[str]:
    """Return preferred Markdown editor font family (None for default)."""
    payload = _read_global_config()
    font = payload.get("default_markdown_font")
    if isinstance(font, str) and font.strip():
        return font.strip()
    return None


def load_default_markdown_font_size(default: int = 12) -> int:
    """Return preferred Markdown editor font size."""
    payload = _read_global_config()
    size = payload.get("default_markdown_font_size")
    try:
        return max(6, int(size))
    except Exception:
        return max(6, int(default))


def save_default_markdown_font_size(size: int) -> None:
    """Persist preferred Markdown editor font size."""
    try:
        value = max(6, int(size))
    except Exception:
        value = 12
    _update_global_config({"default_markdown_font_size": value})


def save_default_markdown_font(font: Optional[str]) -> None:
    """Persist preferred Markdown editor font family."""
    value = font.strip() if isinstance(font, str) and font.strip() else None
    _update_global_config({"default_markdown_font": value})


def save_vi_mode_enabled(enabled: bool) -> None:
    """Persist the vi-mode enablement flag to the global config."""
    _update_global_config({"enable_vi_mode": bool(enabled)})


def load_ai_chat_font_size(default: int = 13) -> int:
    """Load preferred font size for AI chat panel."""
    if not GLOBAL_CONFIG.exists():
        return default
    try:
        payload = json.loads(GLOBAL_CONFIG.read_text(encoding="utf-8"))
        val = int(payload.get("ai_chat_font_size", default))
        # Clamp to sensible range to avoid invalid values (<=0) causing Qt warnings
        return max(6, min(24, val))
    except Exception:
        return default


def save_ai_chat_font_size(size: int) -> None:
    """Persist preferred font size for AI chat panel."""
    try:
        val = int(size)
    except Exception:
        val = 13
    val = max(6, min(24, val))
    _update_global_config({"ai_chat_font_size": val})


def load_ai_chat_font_family() -> Optional[str]:
    """Load preferred font family for AI chat panel (global)."""
    payload = _read_global_config()
    font = payload.get("ai_chat_font_family")
    if isinstance(font, str) and font.strip():
        return font.strip()
    return None


def save_ai_chat_font_family(font: Optional[str]) -> None:
    """Persist preferred font family for AI chat panel (global)."""
    value = font.strip() if isinstance(font, str) and font.strip() else None
    _update_global_config({"ai_chat_font_family": value})


def load_one_shot_font_size(default: int = 12) -> int:
    """Load preferred font size for the One-Shot overlay (global)."""
    payload = _read_global_config()
    try:
        size = int(payload.get("one_shot_font_size", default))
        return max(6, min(24, size))
    except Exception:
        return default


def save_one_shot_font_size(size: int) -> None:
    """Persist preferred font size for the One-Shot overlay (global)."""
    try:
        val = int(size)
    except Exception:
        val = 12
    val = max(6, min(24, val))
    _update_global_config({"one_shot_font_size": val})


def load_enable_ai_chats() -> bool:
    """Load preference for enabling AI Chats tab. Defaults to False."""
    if not GLOBAL_CONFIG.exists():
        return False
    try:
        payload = json.loads(GLOBAL_CONFIG.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    return bool(payload.get("enable_ai_chats", False))

def load_pygments_style(default: str = "monokai") -> str:
    """Load preferred Pygments style for code fences (global, not per-vault)."""
    payload = _read_global_config()
    style = payload.get("pygments_style")
    if isinstance(style, str) and style.strip():
        return style.strip()
    return default


# PlantUML Configuration


def load_plantuml_enabled() -> bool:
    """Load PlantUML rendering enabled flag (default: True)."""
    payload = _read_global_config()
    return payload.get("plantuml_enabled", True)


def save_plantuml_enabled(enabled: bool) -> None:
    """Save PlantUML rendering enabled flag."""
    _update_global_config({"plantuml_enabled": bool(enabled)})


def load_plantuml_jar_path() -> Optional[str]:
    """Load configured PlantUML JAR path (if any)."""
    payload = _read_global_config()
    path = payload.get("plantuml_jar_path")
    return path if isinstance(path, str) and path.strip() else None


def save_plantuml_jar_path(jar_path: str) -> None:
    """Save configured PlantUML JAR path."""
    _update_global_config({"plantuml_jar_path": jar_path.strip() if jar_path else ""})


def load_plantuml_java_path() -> Optional[str]:
    """Load configured Java executable path (if any)."""
    payload = _read_global_config()
    path = payload.get("plantuml_java_path")
    return path if isinstance(path, str) and path.strip() else None


def save_plantuml_java_path(java_path: str) -> None:
    """Save configured Java executable path."""
    _update_global_config({"plantuml_java_path": java_path.strip() if java_path else ""})


def load_plantuml_render_format() -> str:
    """Load PlantUML render format (currently only 'svg' supported)."""
    payload = _read_global_config()
    fmt = payload.get("plantuml_render_format", "svg")
    return fmt if isinstance(fmt, str) else "svg"


def load_plantuml_render_debounce_ms() -> int:
    """Load PlantUML render debounce delay in milliseconds (default: 500)."""
    payload = _read_global_config()
    try:
        ms = int(payload.get("plantuml_render_debounce_ms", 500))
        return max(100, min(5000, ms))  # Clamp to reasonable range
    except (TypeError, ValueError):
        return 500


def save_plantuml_render_debounce_ms(ms: int) -> None:
    """Save PlantUML render debounce delay."""
    try:
        val = int(ms)
        val = max(100, min(5000, val))
    except (TypeError, ValueError):
        val = 500
    _update_global_config({"plantuml_render_debounce_ms": val})


def _merge_mode_settings(payload: dict, defaults: dict) -> dict:
    """Merge persisted mode settings with defaults, dropping unexpected keys."""
    merged = defaults.copy()
    if not isinstance(payload, dict):
        return merged
    for key, default_val in defaults.items():
        val = payload.get(key, default_val)
        if isinstance(default_val, bool):
            merged[key] = bool(val)
        else:
            try:
                merged[key] = type(default_val)(val)
            except Exception:
                merged[key] = default_val
    return merged


def load_focus_mode_settings() -> dict:
    """Return focus mode preferences merged with defaults."""
    defaults = {
        "center_column": True,
        "max_column_width_chars": 80,
        "typewriter_scrolling": False,
        "paragraph_focus": False,
        "font_size": load_default_markdown_font_size(),
        "font_scale": 1.0,
    }
    payload = _read_global_config()
    merged = _merge_mode_settings(payload.get("focus_mode", {}), defaults)
    try:
        merged["max_column_width_chars"] = max(40, min(999, int(merged.get("max_column_width_chars", defaults["max_column_width_chars"]))))
    except Exception:
        merged["max_column_width_chars"] = defaults["max_column_width_chars"]
    try:
        merged["font_size"] = max(6, int(merged.get("font_size", defaults["font_size"])))
    except Exception:
        merged["font_size"] = defaults["font_size"]
    try:
        merged["font_scale"] = max(0.5, min(2.5, float(merged.get("font_scale", defaults["font_scale"]))))
    except Exception:
        merged["font_scale"] = defaults["font_scale"]
    return merged


def save_focus_mode_settings(settings: dict) -> None:
    """Persist focus mode preferences."""
    defaults = load_focus_mode_settings()
    merged = _merge_mode_settings(settings or {}, defaults)
    _update_global_config({"focus_mode": merged})


def load_audience_mode_settings() -> dict:
    """Return audience mode preferences merged with defaults."""
    defaults = {
        "font_size": load_default_markdown_font_size(),
        "font_scale": 1.15,
        "line_height_scale": 1.15,
        "cursor_spotlight": True,
        "paragraph_highlight": True,
        "soft_autoscroll": True,
        "show_floating_tools": True,
        "center_column": True,
        "max_column_width_chars": 120,
    }
    payload = _read_global_config()
    settings = payload.get("audience_mode", {})
    merged = _merge_mode_settings(settings if isinstance(settings, dict) else {}, defaults)
    # Clamp reasonable ranges for numeric settings
    try:
        merged["font_size"] = max(6, int(merged.get("font_size", defaults["font_size"])))
    except Exception:
        merged["font_size"] = defaults["font_size"]
    try:
        merged["font_scale"] = max(1.0, min(2.5, float(merged.get("font_scale", defaults["font_scale"]))))
    except Exception:
        merged["font_scale"] = defaults["font_scale"]
    try:
        merged["line_height_scale"] = max(1.0, min(2.5, float(merged.get("line_height_scale", defaults["line_height_scale"]))))
    except Exception:
        merged["line_height_scale"] = defaults["line_height_scale"]
    try:
        merged["max_column_width_chars"] = max(40, min(999, int(merged.get("max_column_width_chars", defaults["max_column_width_chars"]))))
    except Exception:
        merged["max_column_width_chars"] = defaults["max_column_width_chars"]
    return merged


def save_audience_mode_settings(settings: dict) -> None:
    """Persist audience mode preferences."""
    defaults = load_audience_mode_settings()
    merged = _merge_mode_settings(settings or {}, defaults)
    _update_global_config({"audience_mode": merged})

def save_pygments_style(style: str) -> None:
    """Persist preferred Pygments style for code fences (global, not per-vault)."""
    _update_global_config({"pygments_style": style})


def save_enable_ai_chats(enabled: bool) -> None:
    """Save preference for enabling AI Chats tab."""
    _update_global_config({"enable_ai_chats": bool(enabled)})


def load_default_ai_server() -> Optional[str]:
    """Load preferred default AI server for new chats."""
    payload = _read_global_config()
    server = payload.get("default_ai_server")
    return str(server) if server else None


def save_default_ai_server(name: Optional[str]) -> None:
    """Persist preferred default AI server for new chats."""
    _update_global_config({"default_ai_server": name})


def load_default_ai_model() -> Optional[str]:
    """Load preferred default AI model for new chats."""
    payload = _read_global_config()
    model = payload.get("default_ai_model")
    return str(model) if model else None


def load_enable_main_soft_scroll(default: bool = True) -> bool:
    """Return whether main editor soft auto-scroll is enabled."""
    payload = _read_global_config()
    val = payload.get("enable_main_soft_scroll")
    if val is None:
        return default
    return bool(val)


def save_enable_main_soft_scroll(enabled: bool) -> None:
    """Persist preference for main editor soft auto-scroll."""
    _update_global_config({"enable_main_soft_scroll": bool(enabled)})


def load_main_soft_scroll_lines(default: int = 5) -> int:
    """Return how many lines to scroll when soft auto-scroll triggers."""
    payload = _read_global_config()
    try:
        val = int(payload.get("main_soft_scroll_lines", default))
        return max(1, min(50, val))
    except Exception:
        return default


def save_main_soft_scroll_lines(lines: int) -> None:
    """Persist soft auto-scroll threshold (lines from edge)."""
    try:
        val = max(1, min(50, int(lines)))
    except Exception:
        val = 5
    _update_global_config({"main_soft_scroll_lines": val})


def save_default_ai_model(model: Optional[str]) -> None:
    """Persist preferred default AI model for new chats."""
    _update_global_config({"default_ai_model": model})


def load_toc_collapsed() -> bool:
    """Return whether the table-of-contents panel should start collapsed."""
    if not GLOBAL_CONFIG.exists():
        return False
    try:
        payload = json.loads(GLOBAL_CONFIG.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    return bool(payload.get("toc_collapsed", False))


def save_toc_collapsed(collapsed: bool) -> None:
    """Persist the collapsed state of the table-of-contents panel."""
    _update_global_config({"toc_collapsed": bool(collapsed)})


def load_toc_widget_enabled() -> bool:
    """Return whether the TOC widget should be enabled."""
    if not GLOBAL_CONFIG.exists():
        return True  # Default to enabled
    try:
        payload = json.loads(GLOBAL_CONFIG.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return True
    return bool(payload.get("toc_widget_enabled", True))


def save_toc_widget_enabled(enabled: bool) -> None:
    """Persist the TOC widget enabled state."""
    _update_global_config({"toc_widget_enabled": bool(enabled)})


def get_page_hash(path: str) -> Optional[str]:
    """Return last stored content hash for a page path, or None."""
    conn = _get_conn()
    if not conn:
        return None
    cur = conn.execute("SELECT value FROM kv WHERE key = ?", (f"hash:{path}",))
    row = cur.fetchone()
    return str(row[0]) if row else None


def set_page_hash(path: str, digest: str) -> None:
    """Persist content hash for a page path in kv."""
    conn = _get_conn()
    if not conn:
        return
    conn.execute("REPLACE INTO kv(key, value) VALUES(?, ?)", (f"hash:{path}", digest))
    conn.commit()


def load_bookmarks() -> list[str]:
    """Load bookmarked page paths. Returns list of paths."""
    conn = _get_conn()
    if not conn:
        return []
    try:
        cur = conn.execute("SELECT path FROM bookmarks ORDER BY position")
        return [row[0] for row in cur.fetchall()]
    except sqlite3.OperationalError:
        # Table doesn't exist yet
        return []


def save_bookmarks(paths: list[str]) -> None:
    """Save bookmarked page paths with their order."""
    conn = _get_conn()
    if not conn:
        return
    with conn:
        conn.execute("DELETE FROM bookmarks")
        conn.executemany(
            "INSERT INTO bookmarks(path, position) VALUES(?, ?)",
            ((path, idx) for idx, path in enumerate(paths))
        )


def load_show_journal() -> bool:
    """Load show_journal setting. Defaults to False (hidden)."""
    conn = _get_conn()
    if not conn:
        return False
    cur = conn.execute("SELECT value FROM kv WHERE key = ?", ("show_journal",))
    row = cur.fetchone()
    if not row:
        return False
    return str(row[0]).lower() == "true"


def save_show_journal(show: bool) -> None:
    """Save show_journal setting."""
    conn = _get_conn()
    if not conn:
        return
    conn.execute("REPLACE INTO kv(key, value) VALUES(?, ?)", ("show_journal", "true" if show else "false"))
    conn.commit()


def load_popup_editor_geometry() -> Optional[str]:
    """Return the saved geometry for popup editors, if any."""
    conn = _get_conn()
    if not conn:
        return None
    cur = conn.execute("SELECT value FROM kv WHERE key = ?", ("popup_editor_geometry",))
    row = cur.fetchone()
    return str(row[0]) if row else None


def save_popup_editor_geometry(geometry: str) -> None:
    """Persist geometry for popup editors (base64 of QByteArray)."""
    conn = _get_conn()
    if not conn:
        return
    conn.execute("REPLACE INTO kv(key, value) VALUES(?, ?)", ("popup_editor_geometry", geometry))
    conn.commit()


def load_popup_font_size(default: int = 14) -> int:
    """Load preferred font size for popup editors."""
    conn = _get_conn()
    if not conn:
        return default
    cur = conn.execute("SELECT value FROM kv WHERE key = ?", ("popup_font_size",))
    row = cur.fetchone()
    if not row:
        return default
    try:
        return max(8, int(row[0]))
    except ValueError:
        return default


def save_popup_font_size(size: int) -> None:
    """Persist preferred font size for popup editors."""
    conn = _get_conn()
    if not conn:
        return
    conn.execute("REPLACE INTO kv(key, value) VALUES(?, ?)", ("popup_font_size", str(int(size))))
    conn.commit()


def load_vault_force_read_only() -> bool:
    """Return True if this vault should be opened in read-only mode by default."""
    conn = _get_conn()
    if not conn:
        return False
    cur = conn.execute("SELECT value FROM kv WHERE key = ?", ("force_read_only",))
    row = cur.fetchone()
    if not row:
        return False
    return str(row[0]).lower() == "true"


def save_vault_force_read_only(force: bool) -> None:
    """Persist the per-vault read-only preference."""
    conn = _get_conn()
    if not conn:
        return
    conn.execute(
        "REPLACE INTO kv(key, value) VALUES(?, ?)",
        ("force_read_only", "true" if force else "false"),
    )
    conn.commit()


def load_show_future_tasks() -> bool:
    """Load preference for showing tasks that start in the future."""
    conn = _get_conn()
    if not conn:
        return False
    cur = conn.execute("SELECT value FROM kv WHERE key = ?", ("show_future_tasks",))
    row = cur.fetchone()
    if not row:
        return False
    return str(row[0]).lower() == "true"


def save_show_future_tasks(show: bool) -> None:
    """Persist preference for showing future tasks."""
    conn = _get_conn()
    if not conn:
        return
    conn.execute(
        "REPLACE INTO kv(key, value) VALUES(?, ?)",
        ("show_future_tasks", "true" if show else "false"),
    )
    conn.commit()


def load_show_task_start_date() -> bool:
    """Load preference for showing Start date in task lists."""
    conn = _get_conn()
    if not conn:
        return False
    cur = conn.execute("SELECT value FROM kv WHERE key = ?", ("show_task_start_date",))
    row = cur.fetchone()
    if not row:
        return False
    return str(row[0]).lower() == "true"


def save_show_task_start_date(show: bool) -> None:
    """Persist preference for showing Start date in task lists."""
    conn = _get_conn()
    if not conn:
        return
    conn.execute(
        "REPLACE INTO kv(key, value) VALUES(?, ?)",
        ("show_task_start_date", "true" if show else "false"),
    )
    conn.commit()


def load_show_task_page() -> bool:
    """Load preference for showing Page/Path in task lists."""
    conn = _get_conn()
    if not conn:
        return False
    cur = conn.execute("SELECT value FROM kv WHERE key = ?", ("show_task_page",))
    row = cur.fetchone()
    if not row:
        return False
    return str(row[0]).lower() == "true"


def save_show_task_page(show: bool) -> None:
    """Persist preference for showing Page/Path in task lists."""
    conn = _get_conn()
    if not conn:
        return
    conn.execute(
        "REPLACE INTO kv(key, value) VALUES(?, ?)",
        ("show_task_page", "true" if show else "false"),
    )
    conn.commit()

def load_link_navigator_mode(default: str = "graph") -> str:
    """Load preferred Link Navigator view mode (graph|raw) for the active vault."""
    conn = _get_conn()
    if not conn:
        return default
    cur = conn.execute("SELECT value FROM kv WHERE key = ?", ("link_navigator_mode",))
    row = cur.fetchone()
    if not row:
        return default
    mode = str(row[0]).strip().lower()
    return mode if mode in {"graph", "raw"} else default


def save_link_navigator_mode(mode: str) -> None:
    """Persist Link Navigator view mode (graph|raw) for the active vault."""
    conn = _get_conn()
    if not conn:
        return
    normalized = (mode or "").strip().lower()
    if normalized not in {"graph", "raw"}:
        normalized = "graph"
    conn.execute(
        "REPLACE INTO kv(key, value) VALUES(?, ?)",
        ("link_navigator_mode", normalized),
    )
    conn.commit()

def load_link_navigator_layout(default: str = "default") -> str:
    """Load preferred Link Navigator layout (default|layered|treemap|physics) for the active vault."""
    conn = _get_conn()
    if not conn:
        return default
    cur = conn.execute("SELECT value FROM kv WHERE key = ?", ("link_navigator_layout",))
    row = cur.fetchone()
    if not row:
        return default
    layout = str(row[0]).strip().lower()
    return layout if layout in {"default", "layered", "treemap", "physics"} else default


def save_link_navigator_layout(layout: str) -> None:
    """Persist Link Navigator layout (default|layered|treemap|physics) for the active vault."""
    conn = _get_conn()
    if not conn:
        return
    normalized = (layout or "").strip().lower()
    if normalized not in {"default", "layered", "treemap", "physics"}:
        normalized = "default"
    conn.execute(
        "REPLACE INTO kv(key, value) VALUES(?, ?)",
        ("link_navigator_layout", normalized),
    )
    conn.commit()


def load_last_file() -> Optional[str]:
    """Load the last opened file path. Returns None if no file was previously opened."""
    conn = _get_conn()
    if not conn:
        return None
    cur = conn.execute("SELECT value FROM kv WHERE key = ?", ("last_file",))
    row = cur.fetchone()
    return str(row[0]) if row else None


def save_last_file(path: str) -> None:
    """Save the last opened file path."""
    conn = _get_conn()
    if not conn:
        return
    conn.execute("REPLACE INTO kv(key, value) VALUES(?, ?)", ("last_file", path))
    conn.commit()


def load_recent_history() -> list[str]:
    """Load recent page history."""
    conn = _get_conn()
    if not conn:
        return []
    cur = conn.execute("SELECT value FROM kv WHERE key = ?", ("recent_history",))
    row = cur.fetchone()
    if not row:
        return []
    try:
        data = json.loads(row[0])
        if isinstance(data, list):
            return [str(p) for p in data if isinstance(p, str)]
    except Exception:
        pass
    return []


def save_recent_history(history: list[str]) -> None:
    """Persist recent page history (limited to last 50 entries)."""
    conn = _get_conn()
    if not conn:
        return
    try:
        payload = json.dumps(history[:50])
    except Exception:
        return
    conn.execute("REPLACE INTO kv(key, value) VALUES(?, ?)", ("recent_history", payload))
    conn.commit()


def load_recent_history_positions() -> dict[str, int]:
    """Load saved cursor positions for recent history."""
    conn = _get_conn()
    if not conn:
        return {}
    cur = conn.execute("SELECT value FROM kv WHERE key = ?", ("recent_history_positions",))
    row = cur.fetchone()
    if not row:
        return {}
    try:
        data = json.loads(row[0])
        if isinstance(data, dict):
            return {str(k): int(v) for k, v in data.items() if isinstance(k, str) and isinstance(v, int)}
    except Exception:
        pass
    return {}


def save_recent_history_positions(positions: dict[str, int]) -> None:
    """Persist cursor positions for recent history."""
    conn = _get_conn()
    if not conn:
        return
    try:
        payload = json.dumps(positions)
    except Exception:
        return
    conn.execute("REPLACE INTO kv(key, value) VALUES(?, ?)", ("recent_history_positions", payload))
    conn.commit()


def load_window_geometry() -> Optional[str]:
    """Load the saved window geometry (base64 encoded QByteArray)."""
    conn = _get_conn()
    if not conn:
        return None
    cur = conn.execute("SELECT value FROM kv WHERE key = ?", ("window_geometry",))
    row = cur.fetchone()
    return str(row[0]) if row else None


def save_window_geometry(geometry: str) -> None:
    """Save the window geometry (base64 encoded QByteArray)."""
    conn = _get_conn()
    if not conn:
        return
    conn.execute("REPLACE INTO kv(key, value) VALUES(?, ?)", ("window_geometry", geometry))
    conn.commit()


def load_splitter_state() -> Optional[str]:
    """Load the saved main splitter state (base64 encoded QByteArray)."""
    conn = _get_conn()
    if not conn:
        return None
    cur = conn.execute("SELECT value FROM kv WHERE key = ?", ("splitter_state",))
    row = cur.fetchone()
    return str(row[0]) if row else None


def save_splitter_state(state: str) -> None:
    """Save the main splitter state (base64 encoded QByteArray)."""
    conn = _get_conn()
    if not conn:
        return
    conn.execute("REPLACE INTO kv(key, value) VALUES(?, ?)", ("splitter_state", state))
    conn.commit()


def load_editor_splitter_state() -> Optional[str]:
    """Load the saved editor splitter state (base64 encoded QByteArray)."""
    conn = _get_conn()
    if not conn:
        return None
    cur = conn.execute("SELECT value FROM kv WHERE key = ?", ("editor_splitter_state",))
    row = cur.fetchone()
    return str(row[0]) if row else None


def save_editor_splitter_state(state: str) -> None:
    """Save the editor splitter state (base64 encoded QByteArray)."""
    conn = _get_conn()
    if not conn:
        return
    conn.execute("REPLACE INTO kv(key, value) VALUES(?, ?)", ("editor_splitter_state", state))
    conn.commit()

# --- PlantUML Editor window prefs -------------------------------------------

def load_puml_window_geometry() -> Optional[str]:
    """Load saved geometry for PlantUML editor window (base64 QByteArray)."""
    conn = _get_conn()
    if not conn:
        return None
    cur = conn.execute("SELECT value FROM kv WHERE key = ?", ("puml_window_geometry",))
    row = cur.fetchone()
    return str(row[0]) if row else None


def save_puml_window_geometry(geometry: str) -> None:
    """Persist geometry for PlantUML editor window (base64 QByteArray)."""
    conn = _get_conn()
    if not conn:
        return
    conn.execute("REPLACE INTO kv(key, value) VALUES(?, ?)", ("puml_window_geometry", geometry))
    conn.commit()


def load_puml_hsplit_state() -> Optional[str]:
    """Load horizontal split state (editor|preview) for PlantUML editor (base64 QByteArray)."""
    conn = _get_conn()
    if not conn:
        return None
    cur = conn.execute("SELECT value FROM kv WHERE key = ?", ("puml_hsplit_state",))
    row = cur.fetchone()
    return str(row[0]) if row else None


def save_puml_hsplit_state(state: str) -> None:
    """Persist horizontal splitter state for PlantUML editor (base64 QByteArray)."""
    conn = _get_conn()
    if not conn:
        return
    conn.execute("REPLACE INTO kv(key, value) VALUES(?, ?)", ("puml_hsplit_state", state))
    conn.commit()


def load_puml_vsplit_state() -> Optional[str]:
    """Load vertical split state (top|chat) for PlantUML editor (base64 QByteArray)."""
    conn = _get_conn()
    if not conn:
        return None
    cur = conn.execute("SELECT value FROM kv WHERE key = ?", ("puml_vsplit_state",))
    row = cur.fetchone()
    return str(row[0]) if row else None


def save_puml_vsplit_state(state: str) -> None:
    """Persist vertical splitter state for PlantUML editor (base64 QByteArray)."""
    conn = _get_conn()
    if not conn:
        return
    conn.execute("REPLACE INTO kv(key, value) VALUES(?, ?)", ("puml_vsplit_state", state))
    conn.commit()


def load_puml_editor_zoom(default: int = 0) -> int:
    """Load saved editor zoom level delta for PlantUML editor (int, relative to 11pt)."""
    conn = _get_conn()
    if not conn:
        return default
    cur = conn.execute("SELECT value FROM kv WHERE key = ?", ("puml_editor_zoom",))
    row = cur.fetchone()
    if not row:
        return default
    try:
        return int(row[0])
    except Exception:
        return default


def save_puml_editor_zoom(level: int) -> None:
    conn = _get_conn()
    if not conn:
        return
    conn.execute("REPLACE INTO kv(key, value) VALUES(?, ?)", ("puml_editor_zoom", str(int(level))))
    conn.commit()


def load_puml_preview_zoom(default: int = 0) -> int:
    """Load saved preview zoom level delta for PlantUML editor (int increments of 10%)."""
    conn = _get_conn()
    if not conn:
        return default
    cur = conn.execute("SELECT value FROM kv WHERE key = ?", ("puml_preview_zoom",))
    row = cur.fetchone()
    if not row:
        return default
    try:
        return int(row[0])
    except Exception:
        return default


def save_puml_preview_zoom(level: int) -> None:
    conn = _get_conn()
    if not conn:
        return
    conn.execute("REPLACE INTO kv(key, value) VALUES(?, ?)", ("puml_preview_zoom", str(int(level))))
    conn.commit()


def load_puml_auto_render(default: bool = False) -> bool:
    """Load PlantUML auto-render setting (default: False)."""
    conn = _get_conn()
    if not conn:
        return default
    cur = conn.execute("SELECT value FROM kv WHERE key = ?", ("puml_auto_render",))
    row = cur.fetchone()
    if not row:
        return default
    try:
        return row[0].lower() in ("1", "true", "yes")
    except Exception:
        return default


def save_puml_auto_render(enabled: bool) -> None:
    """Save PlantUML auto-render setting."""
    conn = _get_conn()
    if not conn:
        return
    conn.execute("REPLACE INTO kv(key, value) VALUES(?, ?)", ("puml_auto_render", "1" if enabled else "0"))
    conn.commit()


def load_panel_visibility() -> dict:
    """Load persisted panel visibility states for main/left/right rails."""
    conn = _get_conn()
    if not conn:
        return {}
    cur = conn.execute("SELECT value FROM kv WHERE key = ?", ("panel_visibility",))
    row = cur.fetchone()
    if not row:
        return {}
    try:
        return json.loads(row[0]) if row[0] else {}
    except Exception:
        return {}

def save_panel_visibility(left_visible: bool, right_visible: bool) -> None:
    """Persist panel visibility for the left (tree) and right (tabs) rails."""
    conn = _get_conn()
    if not conn:
        return
    payload = json.dumps({"left": bool(left_visible), "right": bool(right_visible)})
    conn.execute("REPLACE INTO kv(key, value) VALUES(?, ?)", ("panel_visibility", payload))
    conn.commit()

def load_default_page_template() -> str:
    """Load preferred template name (stem) for new pages."""
    conn = _get_conn()
    if not conn:
        return "Default"
    cur = conn.execute("SELECT value FROM kv WHERE key = ?", ("default_page_template",))
    row = cur.fetchone()
    return (row[0] or "Default") if row else "Default"

def save_default_page_template(name: str) -> None:
    """Persist preferred template name (stem) for new pages."""
    conn = _get_conn()
    if not conn:
        return
    conn.execute("REPLACE INTO kv(key, value) VALUES(?, ?)", ("default_page_template", name or "Default"))
    conn.commit()

def load_default_journal_template() -> str:
    """Load preferred template name (stem) for new journal entries."""
    conn = _get_conn()
    if not conn:
        return "JournalDay"
    cur = conn.execute("SELECT value FROM kv WHERE key = ?", ("default_journal_template",))
    row = cur.fetchone()
    return (row[0] or "JournalDay") if row else "JournalDay"

def save_default_journal_template(name: str) -> None:
    """Persist preferred template name (stem) for new journal entries."""
    conn = _get_conn()
    if not conn:
        return
    conn.execute(
        "REPLACE INTO kv(key, value) VALUES(?, ?)",
        ("default_journal_template", name or "JournalDay"),
    )
    conn.commit()


def load_dialog_geometry(dialog_name: str) -> Optional[str]:
    """Load the saved dialog geometry (base64 encoded QByteArray)."""
    conn = _get_conn()
    if not conn:
        return None
    cur = conn.execute("SELECT value FROM kv WHERE key = ?", (f"{dialog_name}_geometry",))
    row = cur.fetchone()
    return str(row[0]) if row else None


def save_dialog_geometry(dialog_name: str, geometry: str) -> None:
    """Save the dialog geometry (base64 encoded QByteArray)."""
    conn = _get_conn()
    if not conn:
        return
    conn.execute("REPLACE INTO kv(key, value) VALUES(?, ?)", (f"{dialog_name}_geometry", geometry))
    conn.commit()


def _update_global_config(updates: dict) -> None:
    """Merge updates into global config file."""
    existing = {}
    if GLOBAL_CONFIG.exists():
        try:
            existing = json.loads(GLOBAL_CONFIG.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    existing.update(updates)
    GLOBAL_CONFIG.write_text(json.dumps(existing, indent=2), encoding="utf-8")


def load_font_size(default: int = 14) -> int:
    conn = _get_conn()
    if not conn:
        return default
    cur = conn.execute("SELECT value FROM kv WHERE key = ?", ("font_size",))
    row = cur.fetchone()
    if not row:
        return default
    try:
        return max(8, int(row[0]))
    except ValueError:
        return default


def save_font_size(size: int) -> None:
    conn = _get_conn()
    if not conn:
        return
    conn.execute("REPLACE INTO kv(key, value) VALUES(?, ?)", ("font_size", str(size)))
    conn.commit()


# --- Global (application-level) panel/editor font sizes ----------------------

def load_panel_font_size(key: str, default: int = 12) -> int:
    """Load a panel font size from the global config JSON (not vault)."""
    payload = _read_global_config()
    try:
        size = int(payload.get(key, default))
        return max(6, min(32, size))
    except Exception:
        return default


def save_panel_font_size(key: str, size: int) -> None:
    """Persist a panel font size to the global config JSON (not vault)."""
    try:
        clamped = max(6, min(32, int(size)))
    except Exception:
        return
    _update_global_config({key: clamped})


def has_global_config_key(key: str) -> bool:
    """Return True if the global config JSON has a stored value for the key."""
    payload = _read_global_config()
    return key in payload


def load_global_editor_font_size(default: int = 12) -> int:
    """Load the main editor font size from the global config JSON."""
    return load_panel_font_size("editor_font_size", default)


def save_global_editor_font_size(size: int) -> None:
    """Persist the main editor font size to the global config JSON."""
    save_panel_font_size("editor_font_size", size)


# --- Global splitter sizes ---------------------------------------------------

def load_splitter_sizes(key: str) -> list[int] | None:
    """Load splitter sizes (list of ints) from the global config JSON."""
    payload = _read_global_config()
    sizes = payload.get(key)
    if isinstance(sizes, list) and all(isinstance(v, (int, float)) for v in sizes):
        return [int(v) for v in sizes]
    return None


def save_splitter_sizes(key: str, sizes: list[int]) -> None:
    """Persist splitter sizes to the global config JSON."""
    try:
        clean = [int(v) for v in sizes]
    except Exception:
        return
    _update_global_config({key: clean})


# --- Global header states (column order/width) -------------------------------

def load_header_state(key: str) -> Optional[str]:
    """Load a saved header state (base64 string) from global config."""
    payload = _read_global_config()
    state = payload.get(key)
    return str(state) if isinstance(state, str) and state else None


def save_header_state(key: str, state: str) -> None:
    """Persist a header state (base64 string) to global config."""
    if not isinstance(state, str) or not state:
        return
    _update_global_config({key: state})


def save_cursor_position(path: str, position: int) -> None:
    conn = _get_conn()
    if not conn:
        return
    conn.execute("REPLACE INTO cursor_positions(path, position) VALUES(?, ?)", (path, position))
    conn.commit()


def load_cursor_position(path: str) -> Optional[int]:
    conn = _get_conn()
    if not conn:
        return None
    cur = conn.execute("SELECT position FROM cursor_positions WHERE path = ?", (path,))
    row = cur.fetchone()
    return int(row[0]) if row else None


def _next_display_order(conn: sqlite3.Connection, parent_path: str) -> int:
    """Return the next display order slot for a parent folder."""
    try:
        cur = conn.execute("SELECT MAX(display_order) FROM pages WHERE parent_path = ?", (parent_path,))
        current = cur.fetchone()[0]
        return (current or 0) + 1
    except sqlite3.OperationalError:
        return 0


def update_page_index(
    path: str,
    title: str,
    tags: Iterable[str],
    links: Iterable[str],
    tasks: Sequence[dict],
    display_order: int | None = None,
    last_modified: float | None = None,
) -> None:
    conn = _get_conn()
    if not conn:
        return
    _invalidate_task_cache()
    now = time.time()
    modified_ts = last_modified if last_modified is not None else now
    parent_path = _parent_folder_for_page(path)
    existing_order = None
    try:
        row = conn.execute("SELECT display_order FROM pages WHERE path = ?", (path,)).fetchone()
        if row:
            existing_order = row[0]
    except sqlite3.OperationalError:
        existing_order = None
    if display_order is None:
        if existing_order is not None:
            display_order = existing_order
        else:
            display_order = _next_display_order(conn, parent_path)
    with conn:
        unique_tags = list(dict.fromkeys(tags))
        unique_links = list(dict.fromkeys(links))
        
        # Check if page exists and get current rev
        row = conn.execute("SELECT rev, page_id FROM pages WHERE path = ?", (path,)).fetchone()
        current_rev = row[0] if row and row[0] is not None else 0
        current_page_id = row[1] if row and row[1] else None
        new_rev = current_rev + 1
        
        # Generate page_id if creating new page
        if not current_page_id:
            import uuid
            current_page_id = str(uuid.uuid4())
        
        conn.execute(
            """
            INSERT INTO pages(path, title, updated, parent_path, display_order, path_ci, title_ci, page_id, rev)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                title = excluded.title,
                updated = excluded.updated,
                last_modified = excluded.updated,
                parent_path = excluded.parent_path,
                display_order = COALESCE(excluded.display_order, pages.display_order),
                path_ci = excluded.path_ci,
                title_ci = excluded.title_ci,
                rev = excluded.rev
            """,
            (
                path,
                title,
                modified_ts,
                parent_path,
                display_order,
                path.lower(),
                (title or "").lower(),
                current_page_id,
                new_rev,
            ),
        )
        
        # Bump global sync revision
        bump_sync_revision()
        
        conn.execute("DELETE FROM page_tags WHERE page = ?", (path,))
        conn.executemany(
            "INSERT INTO page_tags(page, tag) VALUES(?, ?)",
            ((path, tag) for tag in unique_tags),
        )
        conn.execute("DELETE FROM links WHERE from_path = ?", (path,))
        conn.executemany(
            "INSERT INTO links(from_path, to_path) VALUES(?, ?)",
            ((path, link) for link in unique_links),
        )
        conn.execute("DELETE FROM tasks WHERE path = ?", (path,))
        conn.execute("DELETE FROM task_tags WHERE task_id LIKE ?", (f"{path}:%",))
        if _TASKS_FTS_ENABLED:
            conn.execute("DELETE FROM tasks_fts WHERE task_id LIKE ?", (f"{path}:%",))
        conn.executemany(
            """
            INSERT INTO tasks(task_id, path, line, text, status, priority, due, starts, parent_id, level, actionable)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    task["id"],
                    path,
                    task.get("line"),
                    task.get("text"),
                    task.get("status"),
                    task.get("priority"),
                    task.get("due"),
                    task.get("start"),
                    task.get("parent"),
                    task.get("level"),
                    1
                    if task.get("actionable", task.get("status") != "done")
                    else 0,
                )
                for task in tasks
            ),
        )
        all_tags = []
        for task in tasks:
            for tag in task.get("tags", []):
                all_tags.append((task["id"], tag))
        if all_tags:
            conn.executemany("INSERT INTO task_tags(task_id, tag) VALUES(?, ?)", all_tags)
        if _TASKS_FTS_ENABLED and tasks:
            conn.executemany(
                "INSERT INTO tasks_fts(task_id, text) VALUES(?, ?)",
                ((task["id"], task.get("text") or "") for task in tasks),
            )
    _invalidate_page_cache()
    bump_task_index_version()


def delete_page_index(path: str) -> None:
    """Delete a single page from the index."""
    conn = _get_conn()
    if not conn:
        return
    _invalidate_task_cache()
    like = f"{path}:%"
    with conn:
        conn.execute("DELETE FROM pages WHERE path = ?", (path,))
        conn.execute("DELETE FROM page_tags WHERE page = ?", (path,))
        conn.execute("DELETE FROM links WHERE from_path = ? OR to_path = ?", (path, path))
        conn.execute("DELETE FROM tasks WHERE path = ?", (path,))
        conn.execute("DELETE FROM task_tags WHERE task_id LIKE ?", (like,))
        if _TASKS_FTS_ENABLED:
            conn.execute("DELETE FROM tasks_fts WHERE task_id LIKE ?", (like,))
    _invalidate_page_cache()
    bump_task_index_version()


def delete_folder_index(folder_path: str) -> None:
    """Delete all pages under a folder path (recursive) from the index.
    
    Args:
        folder_path: Folder path like /PageA/PageB (without .md)
    """
    conn = _get_conn()
    if not conn:
        return

    folder_prefix = _normalize_folder_prefix(folder_path)
    _delete_index_for_prefix(conn, folder_prefix)


def delete_tree_index(folder_path: str) -> None:
    """Thread-safe deletion helper for API callers."""
    conn = _connect_to_vault_db()
    try:
        prefix = _normalize_folder_prefix(folder_path)
        _delete_index_for_prefix(conn, prefix)
    finally:
        conn.close()


def _normalize_folder_prefix(folder_path: str) -> str:
    """Normalize folder path to a leading-slash prefix with no trailing slash (except root)."""
    cleaned = folder_path.strip().replace("\\", "/").rstrip("/")
    if not cleaned:
        return "/"
    if not cleaned.startswith("/"):
        cleaned = f"/{cleaned}"
    return cleaned


def _delete_index_for_prefix(conn: sqlite3.Connection, folder_prefix: str) -> None:
    """Mark pages as deleted (soft delete) and remove related data."""
    _invalidate_task_cache()
    like_pattern = f"{folder_prefix}/%"
    
    with conn:
        # Soft delete pages: mark as deleted and bump revision
        rows = conn.execute("SELECT path, rev FROM pages WHERE path LIKE ?", (like_pattern,)).fetchall()
        for path, current_rev in rows:
            new_rev = (current_rev or 0) + 1
            conn.execute(
                "UPDATE pages SET deleted = 1, rev = ? WHERE path = ?",
                (new_rev, path)
            )
        
        # Bump global sync revision for each deleted page (use same connection to avoid locks)
        if rows:
            _bump_sync_revision_in_conn(conn, count=len(rows))
        
        # Remove related data (tags, links, tasks, etc.) - these can be hard deleted
        conn.execute("DELETE FROM page_tags WHERE page LIKE ?", (like_pattern,))
        conn.execute("DELETE FROM links WHERE from_path LIKE ? OR to_path LIKE ?", (like_pattern, like_pattern))
        conn.execute("DELETE FROM tasks WHERE path LIKE ?", (like_pattern,))
        conn.execute("DELETE FROM task_tags WHERE task_id LIKE ?", (f"{folder_prefix}/%:%",))
        if _TASKS_FTS_ENABLED:
            conn.execute("DELETE FROM tasks_fts WHERE task_id LIKE ?", (f"{folder_prefix}/%:%",))
        conn.execute("DELETE FROM bookmarks WHERE path LIKE ?", (like_pattern,))
        conn.execute("DELETE FROM cursor_positions WHERE path LIKE ?", (like_pattern,))
        conn.execute("DELETE FROM attachments WHERE page_path LIKE ?", (like_pattern,))
        conn.execute("DELETE FROM attachments WHERE attachment_path LIKE ?", (like_pattern,))
        conn.execute("DELETE FROM kv WHERE key LIKE ?", (f"hash:{folder_prefix}/%",))
    
    _invalidate_page_cache()
    bump_task_index_version()


def _rebase_page_path(page_path: str, old_folder: str, new_folder: str) -> str:
    """Rebase a page path (with .md) from one folder prefix to another."""
    cleaned_old = old_folder.strip().lstrip("/")
    cleaned_new = new_folder.strip().lstrip("/")
    page_obj = Path(page_path.lstrip("/"))
    page_folder = page_obj.parent
    old_path = Path(cleaned_old)
    try:
        relative = page_folder.relative_to(old_path)
    except ValueError:
        old_parts = old_path.parts
        page_parts = page_folder.parts
        if len(old_parts) > len(page_parts):
            raise
        for idx, part in enumerate(old_parts):
            if page_parts[idx].lower() != part.lower():
                raise
        relative = Path(*page_parts[len(old_parts):])
    new_base = Path(cleaned_new) / relative
    # If this is the root page for the folder, rename the file to match the new folder leaf
    if page_folder == Path(cleaned_old):
        rebased = new_base / f"{new_base.name}{PAGE_SUFFIX}"
    else:
        rebased = new_base / page_obj.name
    return f"/{rebased.as_posix()}"


def _collapse_duplicate_leaf_path(page_path: str) -> str:
    """If a path ends with .../Leaf/Leaf.md, collapse to .../Leaf.md."""
    cleaned = (page_path or "").strip().lstrip("/")
    if not cleaned:
        return page_path
    p = Path(cleaned)
    if p.suffix.lower() not in PAGE_SUFFIXES:
        return page_path
    if len(p.parts) >= 2 and p.stem == p.parent.name:
        collapsed = p.parent.parent / f"{p.stem}{PAGE_SUFFIX}"
        return f"/{collapsed.as_posix()}"
    return page_path


def validate_move_tree_index(old_folder_path: str, new_folder_path: str) -> None:
    """Validate that a tree move would not create conflicts. Raises RuntimeError if invalid."""
    conn = _connect_to_vault_db()
    try:
        old_prefix = _normalize_folder_prefix(old_folder_path)
        new_prefix = _normalize_folder_prefix(new_folder_path)
        if old_prefix == "/":
            raise RuntimeError("Cannot move the vault root")
        old_page_path = folder_to_page_path(old_prefix)
        like_pattern = f"{old_prefix}/%"
        rows = conn.execute(
            "SELECT path FROM pages WHERE deleted = 0 AND (path = ? OR path LIKE ?)",
            (old_page_path, like_pattern),
        ).fetchall()
        if not rows:
            return  # Nothing to move, no conflicts
        
        # Build the path map to check for duplicates
        path_map: dict[str, str] = {}
        for (old_path,) in rows:
            new_path = _rebase_page_path(old_path, old_prefix, new_prefix)
            path_map[old_path] = new_path
        
        new_paths = list(path_map.values())
        # Check for duplicate new paths
        if len(new_paths) != len(set(new_paths)):
            dupes = sorted({p for p in new_paths if new_paths.count(p) > 1})
            raise RuntimeError(f"Move would create duplicate page paths: {', '.join(dupes)}")
        
        # Check for conflicts with existing pages (that aren't being moved)
        placeholders = ",".join("?" for _ in new_paths)
        if placeholders:
            existing = {
                row[0]
                for row in conn.execute(
                    f"SELECT path FROM pages WHERE deleted = 0 AND path IN ({placeholders})",
                    new_paths,
                ).fetchall()
            }
            allowed = set(path_map.keys())
            conflicts = [p for p in existing if p not in allowed]
            if conflicts:
                raise RuntimeError(f"Destination already contains page(s): {', '.join(sorted(conflicts))}")
    finally:
        conn.close()


def move_tree_index(old_folder_path: str, new_folder_path: str, root: Path, *, set_new_parent_order: bool = False) -> dict[str, dict]:
    """Move all indexed paths from old_folder_path to new_folder_path.

    Returns a dict with path_map (old->new) and orders (new_path->display_order).
    """
    conn = _connect_to_vault_db()
    try:
        now = time.time()
        old_prefix = _normalize_folder_prefix(old_folder_path)
        new_prefix = _normalize_folder_prefix(new_folder_path)
        if old_prefix == "/":
            raise RuntimeError("Cannot move the vault root")
        old_page_path = folder_to_page_path(old_prefix)
        like_pattern = f"{old_prefix}/%"
        rows = conn.execute(
            "SELECT path, display_order FROM pages WHERE deleted = 0 AND (path = ? OR path LIKE ?)",
            (old_page_path, like_pattern),
        ).fetchall()
        if not rows:
            return {"path_map": {}, "orders": {}}
        _invalidate_task_cache()
        path_map: dict[str, str] = {}
        orders: dict[str, int] = {}
        old_parent = _parent_folder_for_page(old_page_path)
        new_parent = _parent_folder_for_page(folder_to_page_path(new_prefix))
        parent_changed = old_parent != new_parent
        root_new_order = None
        if parent_changed and set_new_parent_order:
            root_new_order = _next_display_order(conn, new_parent)
        for old_path, existing_order in rows:
            new_path = _rebase_page_path(old_path, old_prefix, new_prefix)
            parent_path = _parent_folder_for_page(new_path)
            order = existing_order
            if order is None:
                order = _next_display_order(conn, parent_path)
            if old_path == old_page_path and parent_changed and set_new_parent_order and root_new_order is not None:
                order = root_new_order
            path_map[old_path] = new_path
            orders[new_path] = order
        new_paths = list(path_map.values())
        if len(new_paths) != len(set(new_paths)):
            dupes = sorted({p for p in new_paths if new_paths.count(p) > 1})
            raise RuntimeError(f"Move would create duplicate page paths: {', '.join(dupes)}")
        placeholders = ",".join("?" for _ in new_paths)
        if placeholders:
            existing = {
                row[0]
                for row in conn.execute(
                    f"SELECT path FROM pages WHERE deleted = 0 AND path IN ({placeholders})",
                    new_paths,
                ).fetchall()
            }
            allowed = set(path_map.keys())
            conflicts = [p for p in existing if p not in allowed]
            if conflicts:
                raise RuntimeError(f"Destination already contains page(s): {', '.join(sorted(conflicts))}")
        with conn:
            conn.executemany(
                """
                UPDATE pages
                SET path = ?,
                    parent_path = ?,
                    display_order = ?,
                    last_modified = ?,
                    path_ci = ?,
                    title_ci = lower(COALESCE(title, ''))
                WHERE path = ?
                """,
                (
                    (
                        new_path,
                        _parent_folder_for_page(new_path),
                        orders[new_path],
                        now,
                        new_path.lower(),
                        old_path,
                    )
                    for old_path, new_path in path_map.items()
                ),
            )
            for old_path, new_path in path_map.items():
                conn.execute("UPDATE page_tags SET page = ? WHERE page = ?", (new_path, old_path))
                conn.execute("UPDATE links SET from_path = ? WHERE from_path = ?", (new_path, old_path))
                conn.execute("UPDATE links SET to_path = ? WHERE to_path = ?", (new_path, old_path))
                try:
                    conn.execute("UPDATE pages_search_index SET path = ? WHERE path = ?", (new_path, old_path))
                except sqlite3.OperationalError:
                    pass
                conn.execute(
                    "UPDATE tasks SET path = ?, task_id = REPLACE(task_id, ?, ?) WHERE path = ?",
                    (new_path, f"{old_path}:", f"{new_path}:", old_path),
                )
                conn.execute(
                    "UPDATE task_tags SET task_id = REPLACE(task_id, ?, ?) WHERE task_id LIKE ?",
                    (f"{old_path}:", f"{new_path}:", f"{old_path}:%"),
                )
                if _TASKS_FTS_ENABLED:
                    conn.execute(
                        "UPDATE tasks_fts SET task_id = REPLACE(task_id, ?, ?) WHERE task_id LIKE ?",
                        (f"{old_path}:", f"{new_path}:", f"{old_path}:%"),
                    )
                conn.execute("UPDATE bookmarks SET path = ? WHERE path = ?", (new_path, old_path))
                conn.execute("UPDATE cursor_positions SET path = ? WHERE path = ?", (new_path, old_path))
                conn.execute("UPDATE attachments SET page_path = ? WHERE page_path = ?", (new_path, old_path))
            old_abs_prefix = str((root / old_prefix.lstrip("/")).resolve())
            new_abs_prefix = str((root / new_prefix.lstrip("/")).resolve())
            if not old_abs_prefix.endswith(os.sep):
                old_abs_prefix += os.sep
            if not new_abs_prefix.endswith(os.sep):
                new_abs_prefix += os.sep
            conn.execute(
                "UPDATE attachments SET attachment_path = REPLACE(attachment_path, ?, ?) WHERE attachment_path LIKE ?",
                (f"{old_prefix}/", f"{new_prefix}/", f"{old_prefix}/%"),
            )
            conn.execute(
                "UPDATE attachments SET stored_path = REPLACE(stored_path, ?, ?) WHERE stored_path LIKE ?",
                (old_abs_prefix, new_abs_prefix, f"{old_abs_prefix}%"),
            )
            conn.execute(
                "UPDATE kv SET key = REPLACE(key, ?, ?) WHERE key LIKE ?",
                (f"hash:{old_prefix}", f"hash:{new_prefix}", f"hash:{old_prefix}/%"),
            )
        _invalidate_page_cache()
        return {"path_map": path_map, "orders": orders}
    finally:
        conn.close()


def update_link_paths(path_map: dict[str, str]) -> None:
    """Rewrite link rows to point at new paths after a move/rename."""
    if not path_map:
        return
    conn = _connect_to_vault_db()
    try:
        with conn:
            for old_path, new_path in path_map.items():
                old_norm = _collapse_duplicate_leaf_path(old_path)
                new_norm = _collapse_duplicate_leaf_path(new_path)
                conn.execute("UPDATE links SET from_path = ? WHERE from_path = ?", (new_norm, old_norm))
                conn.execute("UPDATE links SET to_path = ? WHERE to_path = ?", (new_norm, old_norm))
                print(f"\033[94m[API] Link index path updated: {old_norm} -> {new_norm}\033[0m")
    finally:
        conn.close()


def rebuild_index_from_disk(root: Path, keep_tables: Optional[set[str]] = None) -> None:
    """Drop and recreate vault index tables, preserving selected tables.

    Keeps bookmarks, kv, and any ai* tables by default.
    """
    keep: set[str] = {t.lower() for t in (keep_tables or set())}
    keep.update({"bookmarks", "kv"})
    conn = _connect_to_vault_db()
    try:
        tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        # Always preserve ai* tables
        for name in tables:
            if name.lower().startswith("ai"):
                keep.add(name.lower())
        to_drop = [name for name in tables if name.lower() not in keep]
        with conn:
            for name in to_drop:
                conn.execute(f"DROP TABLE IF EXISTS {name}")
        _ensure_schema(conn)
    finally:
        conn.close()
        _invalidate_page_cache()


def ensure_page_entry(page_path: str, title: Optional[str] = None) -> None:
    """Ensure a page row exists with parent/display order set."""
    conn = _connect_to_vault_db()
    try:
        parent_path = _parent_folder_for_page(page_path)
        now = time.time()
        existing_order = None
        try:
            row = conn.execute("SELECT display_order FROM pages WHERE path = ?", (page_path,)).fetchone()
            if row:
                existing_order = row[0]
        except sqlite3.OperationalError:
            existing_order = None
        order = existing_order if existing_order is not None else _next_display_order(conn, parent_path)
        page_title = title or Path(page_path.lstrip("/")).stem
        
        # Check if page exists and get current rev and page_id
        row = conn.execute("SELECT rev, page_id FROM pages WHERE path = ?", (page_path,)).fetchone()
        current_rev = row[0] if row and row[0] is not None else 0
        current_page_id = row[1] if row and row[1] else None
        new_rev = current_rev + 1
        
        # Generate page_id if creating new page
        if not current_page_id:
            import uuid
            current_page_id = str(uuid.uuid4())
        
        with conn:
            conn.execute(
                """
                INSERT INTO pages(path, title, updated, parent_path, display_order, path_ci, title_ci, page_id, rev)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    parent_path = excluded.parent_path,
                    last_modified = excluded.updated,
                    display_order = COALESCE(pages.display_order, excluded.display_order),
                    path_ci = excluded.path_ci,
                    title_ci = lower(COALESCE(pages.title, excluded.title)),
                    rev = excluded.rev
                """,
            (
                page_path,
                page_title,
                now,
                parent_path,
                order,
                page_path.lower(),
                (page_title or "").lower(),
                current_page_id,
                new_rev,
            ),
        )
        
        # Bump global sync revision
        bump_sync_revision()
        
        _invalidate_page_cache()
    finally:
        conn.close()


def get_tree_version() -> int:
    """Get the current monotonic tree version from the vault kv store."""
    conn = _get_conn()
    if not conn:
        return 0
    try:
        row = conn.execute("SELECT value FROM kv WHERE key = 'tree_version'").fetchone()
        if row:
            return int(row[0])
    except Exception:
        pass
    return 0


def bump_tree_version() -> int:
    """Increment and return the new tree version."""
    conn = _get_conn()
    if not conn:
        return 0
    try:
        row = conn.execute("SELECT value FROM kv WHERE key = 'tree_version'").fetchone()
        current = int(row[0]) if row else 0
    except Exception:
        current = 0
    new_val = current + 1
    try:
        conn.execute("REPLACE INTO kv(key, value) VALUES(?, ?)", ("tree_version", str(new_val)))
        conn.commit()
    except Exception:
        pass
    return new_val


def get_sync_revision() -> int:
    """Get the current global sync revision counter."""
    conn = _get_conn()
    if not conn:
        return 0
    try:
        row = conn.execute("SELECT value FROM kv WHERE key = 'sync_revision'").fetchone()
        if row:
            return int(row[0])
    except Exception:
        pass
    return 0


def bump_sync_revision() -> int:
    """Increment and return the new global sync revision."""
    conn = _get_conn()
    if not conn:
        return 0
    try:
        row = conn.execute("SELECT value FROM kv WHERE key = 'sync_revision'").fetchone()
        current = int(row[0]) if row else 0
    except Exception:
        current = 0
    new_val = current + 1
    try:
        conn.execute("REPLACE INTO kv(key, value) VALUES(?, ?)", ("sync_revision", str(new_val)))
        conn.commit()
    except Exception:
        pass
    return new_val


def _bump_sync_revision_in_conn(conn: sqlite3.Connection, count: int = 1) -> int:
    """Increment sync_revision using an existing connection."""
    if count <= 0:
        return get_sync_revision()
    try:
        row = conn.execute("SELECT value FROM kv WHERE key = 'sync_revision'").fetchone()
        current = int(row[0]) if row else 0
    except Exception:
        current = 0
    new_val = current + count
    try:
        conn.execute("REPLACE INTO kv(key, value) VALUES(?, ?)", ("sync_revision", str(new_val)))
    except Exception:
        pass
    return new_val


def fetch_display_order_map() -> dict[str, int]:
    """Return mapping of page path -> display_order for tree sorting."""
    try:
        conn = _connect_to_vault_db()
    except Exception:
        return {}
    try:
        cur = conn.execute("SELECT path, display_order FROM pages")
        return {row[0]: row[1] for row in cur.fetchall() if row[1] is not None}
    except sqlite3.OperationalError:
        return {}
    finally:
        conn.close()


def get_home_page_path() -> Optional[str]:
    """Return the page path with the lowest display_order at the vault root."""
    conn = _get_conn()
    if not conn:
        return None
    try:
        row = conn.execute(
            "SELECT path FROM pages WHERE parent_path = ? "
            "ORDER BY display_order IS NULL, display_order, path LIMIT 1",
            ("/",),
        ).fetchone()
        return row[0] if row else None
    except sqlite3.OperationalError:
        return None


def page_exists(path: str) -> bool:
    """Return True if the given page path exists in the current vault index."""
    conn = _get_conn()
    if not conn:
        return False
    try:
        row = conn.execute("SELECT 1 FROM pages WHERE path = ? LIMIT 1", (path,)).fetchone()
        return row is not None
    except sqlite3.OperationalError:
        return False


def count_folders() -> int:
    """Count the total number of folder pages in the vault.
    
    A folder is identified by having a page file with the same name as its directory.
    Example: /Joe/Joe.md is a folder, /Joe/SubPage.md is not.
    """
    try:
        conn = _connect_to_vault_db()
    except Exception:
        return 0
    try:
        cur = conn.execute("SELECT path FROM pages")
        paths = [row[0] for row in cur.fetchall()]
        
        # Count paths where filename matches parent folder name
        folder_count = 0
        for path in paths:
            # Extract folder name and filename
            # /Joe/Joe.md -> folder=Joe, filename=Joe.md
            # /Joe/SubPage.md -> folder=Joe, filename=SubPage.md
            parts = path.split('/')
            if len(parts) >= 2:
                filename = strip_page_suffix(parts[-1])
                parent_folder = parts[-2]
                if filename == parent_folder:
                    folder_count += 1
        
        return folder_count
    except sqlite3.OperationalError:
        return 0
    finally:
        conn.close()


def reorder_pages(parent_path: str, page_order: list[str]) -> None:
    """Update display_order for pages under a parent to match the given order.
    
    Args:
        parent_path: The parent folder path (or "/" for root)
        page_order: List of page paths in desired display order
    """
    conn = _get_conn()
    if not conn:
        return
    
    print(f"[DB] Reordering {len(page_order)} pages under {parent_path}")
    
    # First pass: ensure all pages exist in database
    root = _vault_db_path()
    if root:
        root = Path(root).parent  # Get vault root from .stillpoint path
        for page_path in page_order:
            # Check if page exists in database
            existing = conn.execute("SELECT path FROM pages WHERE path = ?", (page_path,)).fetchone()
            if not existing:
                # Page not in database, try to index it
                print(f"[DB] Indexing missing page: {page_path}")
                try:
                    # Try to get the page file to index it
                    file_path = root / page_path.lstrip("/")
                    if file_path.exists():
                        # Index this page
                        index_page(page_path, indexlinks=False)
                except Exception as e:
                    print(f"[DB] Failed to index {page_path}: {e}")
    
    with conn:
        # Update display_order for each page to match its position in the list
        for idx, page_path in enumerate(page_order):
            print(f"[DB]   {idx}: {page_path}")
            conn.execute(
                "UPDATE pages SET display_order = ? WHERE path = ?",
                (idx, page_path)
            )
    conn.commit()
    print(f"[DB] Reorder committed")


def search_pages(term: str, limit: int = 50) -> list[dict]:
    conn = _get_conn()
    if not conn:
        return []
    # Navigation dialogs can be overwhelmed by auto-generated Journal day pages.
    # Filter out bare day pages that have no subpages (e.g. :Journal:2026:01:14),
    # while keeping day pages that *do* have subpages (and keeping the subpages).
    def is_bare_journal_day_page(page_path: str) -> bool:
        try:
            cleaned = (page_path or "").strip()
            if not cleaned.startswith("/"):
                return False
            parts = Path(cleaned.lstrip("/")).parts
            if len(parts) != 5:
                return False
            if parts[0].lower() != "journal":
                return False
            year, month, day, filename = parts[1], parts[2], parts[3], parts[4]
            if not (year.isdigit() and len(year) == 4):
                return False
            if not (month.isdigit() and 1 <= len(month) <= 2):
                return False
            if not (day.isdigit() and 1 <= len(day) <= 2):
                return False
            if Path(filename).suffix.lower() not in PAGE_SUFFIXES:
                return False
            return Path(filename).stem == day
        except Exception:
            return False

    def filter_bare_journal_days_without_children(rows: list[dict]) -> list[dict]:
        day_folders_by_path: dict[str, str] = {}
        for row in rows:
            page_path = row.get("path") if isinstance(row, dict) else None
            if not isinstance(page_path, str) or not is_bare_journal_day_page(page_path):
                continue
            day_folders_by_path[page_path] = _folder_path_for_page(page_path)
        if not day_folders_by_path:
            return rows

        # A "subpage" may be nested at any depth under the day folder (e.g.
        # /Journal/YYYY/MM/DD/Topic/Topic.md). Detect descendants by path prefix,
        # not just direct children.
        folders_with_children: set[str] = set()
        try:
            for day_page_path, folder in day_folders_by_path.items():
                prefix = (folder.rstrip("/") + "/") if folder != "/" else "/"
                cur = conn.execute(
                    "SELECT 1 FROM pages WHERE path LIKE ? AND path <> ? LIMIT 1",
                    (prefix + "%", day_page_path),
                )
                if cur.fetchone():
                    folders_with_children.add(folder)
        except sqlite3.OperationalError:
            return rows

        filtered: list[dict] = []
        for row in rows:
            page_path = row.get("path") if isinstance(row, dict) else None
            if isinstance(page_path, str) and page_path in day_folders_by_path:
                if day_folders_by_path[page_path] not in folders_with_children:
                    continue
            filtered.append(row)
        return filtered

    term_lower = term.lower()
    prefetch_limit = max(limit, min(limit * 5, 250))
    _ensure_page_cache_loaded()
    cached = _search_cached_pages(term_lower, prefetch_limit)
    if cached is not None:
        return filter_bare_journal_days_without_children(cached)[:limit]
    like = f"%{term_lower}%"
    exact_path = f"/{term_lower}"
    starts_path = f"{exact_path}/%"
    cur = conn.execute(
        """
        SELECT path, title, path_ci, title_ci, COALESCE(updated, 0)
        FROM pages
        WHERE path_ci LIKE ? OR title_ci LIKE ?
        ORDER BY
            CASE
                WHEN path_ci = ? THEN 0
                WHEN path_ci LIKE ? THEN 1
                WHEN title_ci = ? THEN 2
                WHEN title_ci LIKE ? THEN 3
                ELSE 4
            END,
            updated DESC
        LIMIT ?
        """,
        (like, like, exact_path, starts_path, term_lower, like, prefetch_limit),
    )
    rows = cur.fetchall()
    results = [
        {
            "path": row[0],
            "title": row[1],
            "path_ci": (row[2] or row[0] or "").lower(),
            "title_ci": (row[3] or row[1] or "").lower(),
            "updated": float(row[4] or 0),
        }
        for row in rows
    ]
    results = filter_bare_journal_days_without_children(results)
    _remember_page_search_result(term_lower, results)
    return [{"path": row["path"], "title": row.get("title")} for row in results[:limit]]


def fetch_tag_summary() -> list[tuple[str, int]]:
    conn = _get_conn()
    if not conn:
        return []
    cur = conn.execute("SELECT tag, COUNT(DISTINCT page) FROM page_tags GROUP BY tag ORDER BY tag")
    return [(row[0], row[1]) for row in cur.fetchall()]


def fetch_task_tags() -> list[tuple[str, int]]:
    conn = _get_conn()
    if not conn:
        return []
    cur = conn.execute("SELECT tag, COUNT(DISTINCT task_id) FROM task_tags GROUP BY tag ORDER BY tag")
    return [(row[0], row[1]) for row in cur.fetchall()]


def load_task_ai_summary() -> Optional[str]:
    """Load the last AI summary for tasks from the active vault."""
    conn = _get_conn()
    if not conn:
        return None
    try:
        row = conn.execute("SELECT content FROM task_ai_summary WHERE id = 1").fetchone()
    except sqlite3.OperationalError:
        return None
    return row[0] if row else None


def save_task_ai_summary(content: str) -> None:
    """Persist the last AI summary for tasks to the active vault."""
    conn = _get_conn()
    if not conn:
        return
    try:
        conn.execute(
            """
            INSERT INTO task_ai_summary (id, content, updated)
            VALUES (1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                content = excluded.content,
                updated = excluded.updated
            """,
            (content, time.time()),
        )
        conn.commit()
    except sqlite3.OperationalError:
        return


def delete_task_ai_summary() -> None:
    """Remove the stored task AI summary for the active vault."""
    conn = _get_conn()
    if not conn:
        return
    try:
        conn.execute("DELETE FROM task_ai_summary WHERE id = 1")
        conn.commit()
    except sqlite3.OperationalError:
        return


def bump_task_index_version() -> int:
    """Increment the in-memory task index version used for cache invalidation."""
    global _TASK_INDEX_VERSION
    with _TASK_VERSION_LOCK:
        _TASK_INDEX_VERSION += 1
        return _TASK_INDEX_VERSION


def get_task_index_version() -> int:
    """Return the current in-memory task index version."""
    with _TASK_VERSION_LOCK:
        return _TASK_INDEX_VERSION


def fetch_tasks(
    query: str = "",
    tags: Sequence[str] = (),
    include_done: bool = False,
    include_ancestors: bool = False,
    actionable_only: bool = False,
) -> list[dict]:
    conn = _get_conn()
    if not conn:
        return []
    non_actionable_tags_list = load_non_actionable_task_tags_list()
    non_actionable_tags = set(non_actionable_tags_list)
    cache_key = _task_cache_key(
        query,
        tags,
        include_done,
        include_ancestors,
        actionable_only,
        non_actionable_tags_list,
    )
    cached = _TASK_FETCH_CACHE.get(cache_key)
    if cached is not None:
        _TASK_FETCH_CACHE.move_to_end(cache_key)
        return _clone_cached_tasks(cached)
    select_cols = """
        SELECT
            t.task_id,
            t.path,
            t.line,
            t.text,
            t.status,
            t.priority,
            t.due,
            t.starts,
            t.parent_id,
            t.level,
            COALESCE(t.actionable, CASE WHEN t.status = 'done' THEN 0 ELSE 1 END) AS actionable
    """
    base = f"{select_cols} FROM tasks t LEFT JOIN task_tags tt ON tt.task_id = t.task_id"
    conditions = []
    params: list = []
    having_clause = ""
    total_tasks = None
    use_fts = False
    if query:
        try:
            total_tasks = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        except sqlite3.OperationalError:
            total_tasks = None
        fts_query = _normalize_fts_query(query)
        use_fts = bool(fts_query) and _should_use_task_fts(conn, total_tasks)
        if use_fts:
            conditions.append(
                "t.task_id IN (SELECT task_id FROM tasks_fts WHERE tasks_fts MATCH ?)"
            )
            params.append(fts_query)
        else:
            conditions.append("lower(t.text) LIKE ?")
            params.append(f"%{query.lower()}%")
    if tags:
        placeholders = ",".join("?" for _ in tags)
        conditions.append(f"tt.tag IN ({placeholders})")
        params.extend(tags)
        # Require that all selected tags are present on the task (AND semantics)
        having_clause = f"HAVING COUNT(DISTINCT tt.tag) = {len(tags)}"
    if not include_done:
        conditions.append("t.status != 'done'")
    if actionable_only:
        conditions.append("COALESCE(t.actionable, CASE WHEN t.status = 'done' THEN 0 ELSE 1 END) = 1")
    where = ""
    if conditions:
        where = "WHERE " + " AND ".join(conditions)
    sql = (
        f"{base} {where} GROUP BY t.task_id "
        f"{having_clause} "
        "ORDER BY t.path, COALESCE(t.line, 0), COALESCE(t.level, 0)"
    )
    cur = conn.execute(sql, params)
    rows = cur.fetchall()

    def _row_to_task(row: tuple) -> dict:
        (
            task_id,
            path,
            line,
            text,
            status,
            priority,
            due,
            starts,
            parent_id,
            level,
            actionable,
        ) = row
        return {
            "id": task_id,
            "path": path,
            "line": line,
            "text": text,
            "status": status,
            "priority": priority or 0,
            "due": due,
            "starts": starts,
            "parent": parent_id,
            "level": level or 0,
            "actionable": bool(actionable),
            "tags": [],
        }

    tasks: dict[str, dict] = {}
    for row in rows:
        task = _row_to_task(row)
        tasks[task["id"]] = task

    if include_ancestors:
        missing_parents = {task["parent"] for task in tasks.values() if task.get("parent")}
        ancestor_sql_template = f"{select_cols} FROM tasks t WHERE t.task_id IN {{}}"
        while missing_parents:
            fetch_ids = [pid for pid in missing_parents if pid and pid not in tasks]
            if not fetch_ids:
                break
            placeholders = ",".join("?" for _ in fetch_ids)
            ancestor_sql = ancestor_sql_template.format(f"({placeholders})")
            ancestor_rows = conn.execute(ancestor_sql, fetch_ids).fetchall()
            for row in ancestor_rows:
                task = _row_to_task(row)
                tasks[task["id"]] = task
                if task.get("parent"):
                    missing_parents.add(task["parent"])
            missing_parents = {pid for pid in missing_parents if pid not in tasks}

    if tasks:
        all_ids = list(tasks.keys())
        placeholders = ",".join("?" for _ in all_ids)
        tag_rows = conn.execute(
            f"SELECT task_id, tag FROM task_tags WHERE task_id IN ({placeholders})", all_ids
        ).fetchall()
        for task_id, tag in tag_rows:
            if task_id in tasks:
                tasks[task_id]["tags"].append(tag)

    if non_actionable_tags:
        for task in tasks.values():
            tag_set = {t.lower() for t in task.get("tags", [])}
            if tag_set & non_actionable_tags:
                task["actionable"] = False

    if actionable_only and tasks:
        actionable_ids = {task_id for task_id, task in tasks.items() if task.get("actionable")}
        if include_ancestors and actionable_ids:
            keep_ids = set(actionable_ids)
            for task_id in list(actionable_ids):
                current = tasks.get(task_id, {}).get("parent")
                while current and current not in keep_ids:
                    keep_ids.add(current)
                    current = tasks.get(current, {}).get("parent")
        else:
            keep_ids = actionable_ids
        tasks = {task_id: task for task_id, task in tasks.items() if task_id in keep_ids}

    result = sorted(
        tasks.values(),
        key=lambda t: (t.get("path") or "", t.get("line") or 0, t.get("level") or 0),
    )
    _save_task_cache(cache_key, result)
    return result


def fetch_link_relations(path: str) -> dict[str, list[str]]:
    """Return incoming and outgoing links for a page path."""
    conn = _get_conn()
    if not conn or not path:
        return {"incoming": [], "outgoing": []}
    outgoing = [
        row[0]
        for row in conn.execute("SELECT to_path FROM links WHERE from_path = ?", (path,)).fetchall()
    ]
    incoming = [
        row[0]
        for row in conn.execute("SELECT from_path FROM links WHERE to_path = ?", (path,)).fetchall()
    ]
    return {"incoming": incoming, "outgoing": outgoing}

def fetch_link_edges(from_paths: Iterable[str] = (), to_paths: Iterable[str] = ()) -> list[tuple[str, str]]:
    """Return link edges for the provided from/to path sets."""
    conn = _get_conn()
    if not conn:
        return []
    edges: list[tuple[str, str]] = []
    from_list = [p for p in set(from_paths) if p]
    if from_list:
        placeholders = ",".join("?" for _ in from_list)
        rows = conn.execute(
            f"SELECT from_path, to_path FROM links WHERE from_path IN ({placeholders})",
            from_list,
        ).fetchall()
        edges.extend((row[0], row[1]) for row in rows)
    to_list = [p for p in set(to_paths) if p]
    if to_list:
        placeholders = ",".join("?" for _ in to_list)
        rows = conn.execute(
            f"SELECT from_path, to_path FROM links WHERE to_path IN ({placeholders})",
            to_list,
        ).fetchall()
        edges.extend((row[0], row[1]) for row in rows)
    return edges


def fetch_page_tags(paths: Iterable[str]) -> dict[str, list[str]]:
    """Return a mapping of page path -> list of tags for the provided paths."""
    conn = _get_conn()
    if not conn:
        return {}
    unique = [p for p in set(paths) if p]
    if not unique:
        return {}
    placeholders = ",".join("?" for _ in unique)
    rows = conn.execute(
        f"SELECT page, tag FROM page_tags WHERE page IN ({placeholders})",
        unique,
    ).fetchall()
    tags: dict[str, list[str]] = {path: [] for path in unique}
    for page, tag in rows:
        if page not in tags:
            tags[page] = []
        tags[page].append(tag)
    return tags


def fetch_link_degrees(paths: Iterable[str]) -> dict[str, int]:
    """Return total link degree (incoming + outgoing) for provided paths."""
    conn = _get_conn()
    if not conn:
        return {}
    unique = [p for p in set(paths) if p]
    if not unique:
        return {}
    placeholders = ",".join("?" for _ in unique)
    degrees: dict[str, int] = {path: 0 for path in unique}
    rows = conn.execute(
        f"SELECT from_path, COUNT(*) FROM links WHERE from_path IN ({placeholders}) GROUP BY from_path",
        unique,
    ).fetchall()
    for path, count in rows:
        degrees[path] = degrees.get(path, 0) + int(count or 0)
    rows = conn.execute(
        f"SELECT to_path, COUNT(*) FROM links WHERE to_path IN ({placeholders}) GROUP BY to_path",
        unique,
    ).fetchall()
    for path, count in rows:
        degrees[path] = degrees.get(path, 0) + int(count or 0)
    return degrees


def fetch_page_titles(paths: Iterable[str]) -> dict[str, str]:
    """Return a mapping of page path -> title for the provided paths."""
    conn = _get_conn()
    if not conn:
        return {}
    unique = [p for p in set(paths) if p]
    if not unique:
        return {}
    placeholders = ",".join("?" for _ in unique)
    cur = conn.execute(
        f"SELECT path, title FROM pages WHERE path IN ({placeholders})",
        unique,
    )
    return {row[0]: row[1] for row in cur.fetchall()}


def set_active_vault(root: Optional[str]) -> None:
    global _ACTIVE_CONN, _ACTIVE_ROOT, _TASKS_FTS_ENABLED
    if _ACTIVE_CONN:
        _ACTIVE_CONN.close()
        _ACTIVE_CONN = None
    _TASKS_FTS_ENABLED = False
    _invalidate_task_cache()
    _invalidate_page_cache()
    if not root:
        _ACTIVE_ROOT = None
        return
    _ACTIVE_ROOT = Path(root)
    db_dir = _ACTIVE_ROOT / ".stillpoint"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "settings.db"
    _ACTIVE_CONN = sqlite3.connect(db_path, check_same_thread=False)
    _ensure_schema(_ACTIVE_CONN)
    _prime_page_cache()



def get_active_vault() -> Optional[str]:
    return str(_ACTIVE_ROOT) if _ACTIVE_ROOT else None


def has_active_vault() -> bool:
    return _ACTIVE_CONN is not None


def is_vault_index_empty() -> bool:
    """Check if the vault index is empty (no pages indexed)."""
    conn = _get_conn()
    if not conn:
        return True
    try:
        cur = conn.execute("SELECT COUNT(*) FROM pages")
        count = cur.fetchone()[0]
        return count == 0
    except sqlite3.OperationalError:
        return True


def _get_conn() -> Optional[sqlite3.Connection]:
    return _ACTIVE_CONN


def _vault_db_path() -> Optional[Path]:
    if _ACTIVE_ROOT is None:
        return None
    return _ACTIVE_ROOT / ".stillpoint" / "settings.db"


def _connect_to_vault_db() -> sqlite3.Connection:
    db_path = _vault_db_path()
    if not db_path or not db_path.exists():
        raise RuntimeError("Vault database is not initialized.")
    return sqlite3.connect(db_path, check_same_thread=False)


def _invalidate_task_cache() -> None:
    _TASK_FETCH_CACHE.clear()


def _clone_cached_tasks(tasks: list[dict]) -> list[dict]:
    return [
        {
            **task,
            "tags": list(task.get("tags", [])),
        }
        for task in tasks
    ]


def _task_cache_key(
    query: str,
    tags: Sequence[str],
    include_done: bool,
    include_ancestors: bool,
    actionable_only: bool,
    non_actionable_tags: Sequence[str],
) -> tuple:
    normalized_query = (query or "").strip().lower()
    normalized_tags = tuple(sorted({t.lower() for t in tags}))
    normalized_non_actionable = tuple(sorted({t.lower() for t in non_actionable_tags}))
    return (
        normalized_query,
        normalized_tags,
        bool(include_done),
        bool(include_ancestors),
        bool(actionable_only),
        normalized_non_actionable,
    )


def _save_task_cache(key: tuple, tasks: list[dict]) -> None:
    if not tasks:
        _TASK_FETCH_CACHE[key] = []
    else:
        _TASK_FETCH_CACHE[key] = _clone_cached_tasks(tasks)
    _TASK_FETCH_CACHE.move_to_end(key)
    while len(_TASK_FETCH_CACHE) > _TASK_FETCH_CACHE_SIZE:
        _TASK_FETCH_CACHE.popitem(last=False)


def _tasks_fts_exists(conn: sqlite3.Connection) -> bool:
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='tasks_fts'"
        ).fetchone()
        return bool(row)
    except sqlite3.OperationalError:
        return False


def _maybe_backfill_tasks_fts(conn: sqlite3.Connection) -> None:
    global _TASKS_FTS_ENABLED
    if not _TASKS_FTS_ENABLED:
        return
    try:
        cur = conn.execute("SELECT COUNT(*) FROM tasks_fts")
        current = cur.fetchone()[0]
        if current:
            return
        rows = [(task_id, text or "") for task_id, text in conn.execute("SELECT task_id, text FROM tasks").fetchall()]
        if rows:
            conn.executemany("INSERT INTO tasks_fts(task_id, text) VALUES(?, ?)", rows)
            conn.commit()
    except sqlite3.OperationalError:
        _TASKS_FTS_ENABLED = False


def _ensure_tasks_fts(conn: sqlite3.Connection) -> None:
    global _TASKS_FTS_ENABLED
    preexisting = _tasks_fts_exists(conn)
    try:
        # Try with UNINDEXED first (requires SQLite 3.35.0+)
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS tasks_fts USING fts5(task_id UNINDEXED, text)")
        _TASKS_FTS_ENABLED = True
        _maybe_backfill_tasks_fts(conn)
    except sqlite3.OperationalError as e:
        # If UNINDEXED fails, try without it (fallback for older SQLite)
        try:
            conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS tasks_fts USING fts5(task_id, text)")
            _TASKS_FTS_ENABLED = True
            _maybe_backfill_tasks_fts(conn)
        except sqlite3.OperationalError as e2:
            # FTS5 not available or other error - disable FTS
            print(f"[Index] FTS5 not available: {e2}")
            _TASKS_FTS_ENABLED = preexisting and _tasks_fts_exists(conn)


def _ensure_pages_search_fts(conn: sqlite3.Connection) -> None:
    """Create the pages_search_fts FTS5 virtual table for full-text search."""
    try:
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS pages_search_fts USING fts5(content, content_rowid='id')"
        )
    except sqlite3.OperationalError as e:
        print(f"[Index] FTS5 for pages search not available: {e}")


def _should_use_task_fts(conn: sqlite3.Connection, total_tasks: Optional[int] = None) -> bool:
    if not _TASKS_FTS_ENABLED:
        return False
    try:
        count = total_tasks
        if count is None:
            count = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        return (count or 0) >= _TASKS_FTS_THRESHOLD
    except sqlite3.OperationalError:
        return False


def _normalize_fts_query(query: str) -> str:
    tokens = [tok.strip() for tok in (query or "").split() if tok.strip()]
    if not tokens:
        return ""
    safe = []
    for tok in tokens:
        # FTS MATCH is picky; bail out to LIKE if the token has special chars (e.g., "@")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:+-]*", tok):
            return ""
        safe.append(f"{tok}*")
    return " ".join(safe)


def _ensure_task_columns(conn: sqlite3.Connection) -> None:
    """Add newly introduced task columns for existing vault databases."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
    if "parent_id" not in existing:
        conn.execute("ALTER TABLE tasks ADD COLUMN parent_id TEXT")
    if "level" not in existing:
        conn.execute("ALTER TABLE tasks ADD COLUMN level INTEGER")
    if "actionable" not in existing:
        conn.execute("ALTER TABLE tasks ADD COLUMN actionable INTEGER")


def _ensure_task_indexes(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_actionable ON tasks(actionable)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_path ON tasks(path)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_task_tags_task_id_tag ON task_tags(task_id, tag)"
        )
    except sqlite3.OperationalError:
        # Index creation is best-effort for partially upgraded schemas.
        pass


def _parent_folder_for_page(page_path: str) -> str:
    """Return parent folder path (leading slash) for a page path like /Foo/Bar/Bar.md."""
    cleaned = page_path.strip().lstrip("/")
    if not cleaned:
        return "/"
    path_obj = Path(cleaned)
    parent_folder = path_obj.parent.parent
    if parent_folder.as_posix() in ("", "."):
        return "/"
    return "/" + parent_folder.as_posix().rstrip("/")


def _folder_path_for_page(page_path: str) -> str:
    """Return the folder path (leading slash) containing this page file."""
    cleaned = page_path.strip().lstrip("/")
    if not cleaned:
        return "/"
    parent_dir = Path(cleaned).parent
    if parent_dir.as_posix() in ("", "."):
        return "/"
    return "/" + parent_dir.as_posix().rstrip("/")


def folder_to_page_path(folder_path: str) -> str:
    """Convert a folder path (/Foo/Bar) to the expected page file path (/Foo/Bar/Bar.md)."""
    cleaned = folder_path.strip().replace("\\", "/").strip("/")
    if not cleaned:
        return "/"
    rel = Path(cleaned)
    page = rel / f"{rel.name}{PAGE_SUFFIX}"
    return f"/{page.as_posix()}"


def _ensure_page_columns(conn: sqlite3.Connection) -> None:
    """Add newly introduced page columns and backfill defaults."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(pages)").fetchall()}
    added = False
    normalized_added = False
    
    # Original columns
    if "parent_path" not in existing:
        conn.execute("ALTER TABLE pages ADD COLUMN parent_path TEXT")
        added = True
    if "display_order" not in existing:
        conn.execute("ALTER TABLE pages ADD COLUMN display_order INTEGER")
        added = True
    if "last_modified" not in existing:
        conn.execute("ALTER TABLE pages ADD COLUMN last_modified REAL")
        added = True
    if "path_ci" not in existing:
        conn.execute("ALTER TABLE pages ADD COLUMN path_ci TEXT")
        normalized_added = True
    if "title_ci" not in existing:
        conn.execute("ALTER TABLE pages ADD COLUMN title_ci TEXT")
        normalized_added = True
    
    # Web sync columns
    if "page_id" not in existing:
        conn.execute("ALTER TABLE pages ADD COLUMN page_id TEXT")
        # Generate UUIDs for existing pages
        import uuid
        rows = conn.execute("SELECT path FROM pages WHERE page_id IS NULL").fetchall()
        for (path,) in rows:
            page_id = str(uuid.uuid4())
            conn.execute("UPDATE pages SET page_id = ? WHERE path = ?", (page_id, path))
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_pages_page_id ON pages(page_id)")
    
    if "rev" not in existing:
        conn.execute("ALTER TABLE pages ADD COLUMN rev INTEGER DEFAULT 0")
    
    if "deleted" not in existing:
        conn.execute("ALTER TABLE pages ADD COLUMN deleted INTEGER DEFAULT 0")
    
    if "pinned" not in existing:
        conn.execute("ALTER TABLE pages ADD COLUMN pinned INTEGER DEFAULT 0")
        normalized_added = True
    if added:
        _backfill_page_hierarchy(conn)
        try:
            now = time.time()
            conn.execute("UPDATE pages SET last_modified = COALESCE(last_modified, updated, ?) WHERE last_modified IS NULL", (now,))
        except Exception:
            pass
    if normalized_added or ("path_ci" in existing and "title_ci" in existing):
        _backfill_page_normalized_columns(conn)
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pages_parent_order ON pages(parent_path, display_order)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pages_path_ci ON pages(path_ci)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pages_title_ci ON pages(title_ci)")
    except sqlite3.OperationalError:
        # Older SQLite versions or partial schemas might cause this to fail; safe to skip.
        pass


def _backfill_page_hierarchy(conn: sqlite3.Connection) -> None:
    """Populate parent_path/display_order for existing pages."""
    rows = conn.execute("SELECT path FROM pages").fetchall()
    grouped: dict[str, list[str]] = {}
    for (path,) in rows:
        parent = _parent_folder_for_page(path)
        grouped.setdefault(parent, []).append(path)
    updates: list[tuple[str, int, str]] = []
    now = time.time()
    for parent, paths in grouped.items():
        for idx, path in enumerate(sorted(paths)):
            updates.append((parent, idx, now, path))
    if updates:
        conn.executemany(
            "UPDATE pages SET parent_path = ?, display_order = ?, last_modified = COALESCE(last_modified, ?) WHERE path = ?",
            updates,
        )
        conn.commit()


def _backfill_page_normalized_columns(conn: sqlite3.Connection) -> None:
    """Populate case-insensitive helper columns for existing rows."""
    try:
        conn.execute("UPDATE pages SET path_ci = lower(path) WHERE path_ci IS NULL OR path_ci = ''")
        conn.execute("UPDATE pages SET title_ci = lower(COALESCE(title, '')) WHERE title_ci IS NULL")
    except sqlite3.OperationalError:
        pass


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS kv (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS cursor_positions (
            path TEXT PRIMARY KEY,
            position INTEGER
        );
        CREATE TABLE IF NOT EXISTS pages (
            path TEXT PRIMARY KEY,
            title TEXT,
            updated REAL,
            last_modified REAL,
            parent_path TEXT,
            display_order INTEGER,
            path_ci TEXT,
            title_ci TEXT
        );
        CREATE TABLE IF NOT EXISTS page_tags (
            page TEXT,
            tag TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_page_tags_tag ON page_tags(tag);
        CREATE TABLE IF NOT EXISTS links (
            from_path TEXT,
            to_path TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_links_to ON links(to_path);
        CREATE TABLE IF NOT EXISTS tasks (
            task_id TEXT PRIMARY KEY,
            path TEXT,
            line INTEGER,
            text TEXT,
            status TEXT,
            priority INTEGER,
            due TEXT,
            starts TEXT,
            parent_id TEXT,
            level INTEGER,
            actionable INTEGER
        );
        CREATE TABLE IF NOT EXISTS task_tags (
            task_id TEXT,
            tag TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_task_tags_tag ON task_tags(tag);
        CREATE TABLE IF NOT EXISTS bookmarks (
            path TEXT PRIMARY KEY,
            position INTEGER
        );
        CREATE TABLE IF NOT EXISTS attachments (
            attachment_path TEXT PRIMARY KEY,
            page_path TEXT NOT NULL,
            stored_path TEXT NOT NULL,
            updated REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_attachments_page ON attachments(page_path);
        CREATE TABLE IF NOT EXISTS pages_search_index (
            id INTEGER PRIMARY KEY,
            path TEXT NOT NULL UNIQUE,
            mtime INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_pages_search_path ON pages_search_index(path);
        CREATE TABLE IF NOT EXISTS task_ai_summary (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            content TEXT,
            updated REAL
        );
        """
    )
    _ensure_task_columns(conn)
    _ensure_task_indexes(conn)
    _ensure_page_columns(conn)
    _ensure_tasks_fts(conn)
    _ensure_pages_search_fts(conn)
    conn.commit()


def _normalize_vault_relative_path(path: str) -> str:
    """Return a vault-relative path with a leading slash."""
    cleaned = path.strip().replace("\\", "/")
    cleaned = cleaned.lstrip("/")
    return f"/{cleaned}" if cleaned else "/"


def list_page_attachments(page_path: str) -> list[dict]:
    """Return index rows for attachments belonging to a page."""
    db_path = _vault_db_path()
    if not db_path:
        return []
    conn = sqlite3.connect(db_path, check_same_thread=False)
    try:
        page_key = _normalize_vault_relative_path(page_path)
        rows = conn.execute(
            "SELECT attachment_path, stored_path, updated FROM attachments WHERE page_path = ? ORDER BY attachment_path",
            (page_key,),
        ).fetchall()
        return [
            {"attachment_path": row[0], "stored_path": row[1], "updated": row[2]}
            for row in rows
        ]
    finally:
        conn.close()


def upsert_attachment_entry(page_path: str, attachment_path: str, stored_path: str, updated: float | None = None) -> None:
    """Insert or update an attachment index entry."""
    conn = _connect_to_vault_db()
    page_key = _normalize_vault_relative_path(page_path)
    attachment_key = _normalize_vault_relative_path(attachment_path)
    timestamp = updated if updated is not None else time.time()
    try:
        conn.execute(
            """
            INSERT INTO attachments (attachment_path, page_path, stored_path, updated)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(attachment_path) DO UPDATE SET
                page_path = excluded.page_path,
                stored_path = excluded.stored_path,
                updated = excluded.updated
            """,
            (attachment_key, page_key, stored_path, timestamp),
        )
        conn.commit()
    finally:
        conn.close()


def delete_attachment_entry(attachment_path: str) -> Optional[str]:
    """Remove an attachment entry from the index and return its page path."""
    conn = _connect_to_vault_db()
    try:
        attachment_key = _normalize_vault_relative_path(attachment_path)
        row = conn.execute("SELECT page_path FROM attachments WHERE attachment_path = ?", (attachment_key,)).fetchone()
        if not row:
            return None
        page_path = str(row[0])
        conn.execute("DELETE FROM attachments WHERE attachment_path = ?", (attachment_key,))
        conn.commit()
        return page_path
    finally:
        conn.close()
