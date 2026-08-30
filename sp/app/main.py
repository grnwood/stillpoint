from __future__ import annotations

import argparse
import os
import secrets
import socket
import sys
import threading
import time
import traceback
import shutil
import tempfile
import subprocess
from pathlib import Path

import uvicorn
from PySide6.QtCore import Qt, QtMsgType, qInstallMessageHandler
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QIcon, QPalette, QColor

from sp.app import config
from sp.app import eventloop_diag
from sp.logging_flags import log_enabled

from sp.app.ui.main_window import MainWindow
from sp.app.ui.webengine_env import configure_linux_webengine_env


# ============================================================================
# LOGGING CONFIGURATION - Environment Variables
# ============================================================================
# Set SP_LOG_<AREA>=1 (or true) to enable detailed logging per functional area.
# By default these are OFF for quieter stdout. Important startup/error messages
# remain visible.
#
# SP_LOG_API_CLIENT      - Desktop HTTP request/response tracing
# SP_LOG_API_SERVER      - FastAPI endpoint tracing
# SP_LOG_NAVIGATION      - Left nav and page history behavior
# SP_LOG_SORTING_REORDER - Tree sorting/reorder internals
# SP_LOG_EDITOR_MARKDOWN - Markdown editor read/write and cursor behavior
# SP_LOG_EDITOR_RENDER   - Render pipeline details (images/preview/etc)
# SP_LOG_ATTACHMENTS_MEDIA - Attachments and media operations
# SP_LOG_TASKS_CALENDAR  - Task and calendar data flow
# SP_LOG_REMOTE_VAULTS   - Remote vault auth/config diagnostics
# SP_LOG_HOMEBASE_SYNC   - Homebase sync scheduler, pull/push, conflict traces
# SP_LOG_AI_CHAT         - AI chat request/response diagnostics
# SP_LOG_RAG_VECTOR      - Vector/RAG indexing and query traces
# SP_LOG_DIAGRAMS        - Mermaid/PlantUML details
# SP_LOG_UI_STATE        - UI geometry/panel state details
# SP_LOG_PERFORMANCE     - Timing/performance traces
# SP_LOG_EVENT_LOOP      - Qt dispatcher/fd diagnostics for wakeup livelocks
# SP_LOG_ALL             - Enable all detailed areas
#
# Examples:
#   export SP_LOG_NAVIGATION=1
#   export SP_LOG_API_SERVER=1
#   SP_LOG_EDITOR_MARKDOWN=1 ./sv.sh
# ============================================================================

def _debug_enabled(var_name: str) -> bool:
    """Check if a debug flag is enabled."""
    return os.getenv(var_name, "0") not in ("0", "false", "False", "", None)


def _resource_candidates(rel_path: str) -> list[str]:
    """Return likely absolute paths for a bundled resource.

    Handles PyInstaller onedir/onefile via sys._MEIPASS, alongside the
    executable, and package-relative source layout. The first existing
    path from this list should be used.
    """
    candidates: list[str] = []
    # PyInstaller staging directory (onefile and onedir)
    base = getattr(sys, "_MEIPASS", None)
    if base:
        candidates.append(os.path.join(base, rel_path))
        # Some PyInstaller layouts stage package data under _internal
        candidates.append(os.path.join(base, "_internal", rel_path))
    # Next to the executable (dist root)
    try:
        exe_dir = os.path.abspath(os.path.dirname(sys.argv[0]))
        candidates.append(os.path.join(exe_dir, rel_path))
        candidates.append(os.path.join(exe_dir, "_internal", rel_path))
    except Exception:
        pass
    # Package-relative (developer mode)
    pkg_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    candidates.append(os.path.join(pkg_root, rel_path))
    candidates.append(os.path.join(pkg_root, "sp", rel_path))
    return candidates


def _set_windows_app_id() -> None:
    """Set Windows App User Model ID early for proper taskbar icon grouping.
    
    Must be called before QApplication is created to take effect.
    """
    if sys.platform == "win32":
        try:
            import ctypes
            # Set a unique App User Model ID for Windows taskbar grouping
            myappid = 'com.stillpoint.app'
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
        except Exception:
            pass


def get_app_icon() -> QIcon:
    """Get the application icon, preferring .ico on Windows.
    
    Returns a QIcon that can be used for window icons.
    Caches the result to avoid repeated file searches.
    """
    if not hasattr(get_app_icon, '_cached_icon'):
        icon_candidates = []
        if sys.platform == "win32":
            icon_candidates = [
                os.path.join("assets", "icons", "StillPoint.ico"),
                os.path.join("assets", "icons", "linux-png", "stillpoint-512x512.png"),
            ]
        elif sys.platform == "darwin":
            icon_candidates = [
                os.path.join("assets", "icons", "StillPoint.icns"),
                os.path.join("assets", "icons", "linux-png", "stillpoint-512x512.png"),
            ]
        else:
            icon_candidates = [
                os.path.join("assets", "icons", "linux-png", "stillpoint-512x512.png"),
                os.path.join("assets", "icons", "StillPoint.ico"),
            ]

        for rel_path in icon_candidates:
            for path in _resource_candidates(rel_path):
                if os.path.exists(path):
                    get_app_icon._cached_icon = QIcon(path)
                    return get_app_icon._cached_icon
        
        # Fallback to empty icon
        get_app_icon._cached_icon = QIcon()
    
    return get_app_icon._cached_icon


def _set_app_icon(app: QApplication) -> None:
    """Attempt to set the application/window icon if an asset is bundled.

    On Linux, PyInstaller does not embed a binary icon into the ELF. We set the
    window icon at runtime using a PNG. On Windows/macOS the EXE/App icon is
    handled by PyInstaller, but this also ensures the window/icon in the titlebar
    matches.
    """
    icon = get_app_icon()
    if not icon.isNull():
        try:
            app.setWindowIcon(icon)
        except Exception:
            pass


def _detect_light_color_scheme(app: QApplication) -> tuple[bool, str]:
    """Return (is_light, source) for the host OS/UI scheme."""
    try:
        scheme = app.styleHints().colorScheme()
        if scheme == Qt.ColorScheme.Light:
            return True, "qt-color-scheme"
        if scheme == Qt.ColorScheme.Dark:
            return False, "qt-color-scheme"
    except Exception as exc:
        _startup(f"Theme detect probe failed (qt-color-scheme): {exc}")

    # Windows theme signal (Settings > Personalization > Colors > App mode).
    try:
        if sys.platform == "win32":
            import winreg  # type: ignore

            key_path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                apps_use_light, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
                return bool(int(apps_use_light)), "windows-registry-apps"
    except Exception as exc:
        _startup(f"Theme detect probe failed (windows-registry-apps): {exc}")

    # macOS theme signal from Apple global preferences.
    try:
        if sys.platform == "darwin":
            proc = subprocess.run(
                ["defaults", "read", "-g", "AppleInterfaceStyle"],
                capture_output=True,
                text=True,
                timeout=1.0,
                check=False,
            )
            combined = f"{proc.stdout or ''} {proc.stderr or ''}".strip().lower()
            if proc.returncode == 0:
                # Value is typically "Dark" when dark mode is enabled.
                if "dark" in combined:
                    return False, "macos-apple-interface-style"
                return True, "macos-apple-interface-style"
            if "does not exist" in combined:
                # Missing key generally means Light appearance.
                return True, "macos-apple-interface-style-missing"
            _startup(
                f"Theme detect probe inconclusive (macos-apple-interface-style): "
                f"rc={proc.returncode}, out={combined!r}"
            )
    except Exception as exc:
        _startup(f"Theme detect probe failed (macos-apple-interface-style): {exc}")

    # Linux desktop themes are not always reflected in Qt colorScheme().
    # Check common environment hints before palette fallback.
    try:
        if sys.platform.startswith("linux"):
            gtk_theme = (os.getenv("GTK_THEME") or "").strip().lower()
            if "dark" in gtk_theme:
                return False, "linux-gtk-theme"
            if "light" in gtk_theme:
                return True, "linux-gtk-theme"
            colorfgbg = (os.getenv("COLORFGBG") or "").strip()
            if ";" in colorfgbg:
                parts = [p for p in colorfgbg.split(";") if p]
                try:
                    bg_code = int(parts[-1])
                    # 0-6 are generally dark terminal background codes; 7+ light.
                    return (bg_code >= 7), "linux-colorfgbg"
                except Exception:
                    pass
    except Exception as exc:
        _startup(f"Theme detect probe failed (linux-env): {exc}")

    try:
        pal = app.palette()
        window_l = pal.color(QPalette.ColorRole.Window).lightness()
        base_l = pal.color(QPalette.ColorRole.Base).lightness()
        avg_l = (window_l + base_l) / 2.0
        return (avg_l >= 128), "palette"
    except Exception as exc:
        _startup(f"Theme detect probe failed (palette): {exc}")
        # Prefer dark when unknown to avoid white-on-white regressions.
        return False, "fallback-dark"


def _ensure_user_theme_files() -> bool:
    """Ensure ~/.stillpoint/themes contains bundled dark/light theme files.

    Returns True when one or more theme files were copied in this run.
    """
    theme_dir = Path.home() / ".stillpoint" / "themes"
    copied_any = False
    try:
        theme_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        return False

    app_dir = Path(__file__).resolve().parent
    theme_sources: dict[str, Path] = {
        "dark-theme.json": app_dir / "theme-config.json",
        "light-theme.json": app_dir / "light-theme.json",
        "midnight-blue.json": app_dir / "midnight-blue.json",
        "deep-purple.json": app_dir / "deep-purple.json",
        "pickles-green.json": app_dir / "pickles-green.json",
        "sunset-blaze.json": app_dir / "sunset-blaze.json",
        "charcoal-copper.json": app_dir / "charcoal-copper.json",
        "arctic-night.json": app_dir / "arctic-night.json",
        "ember-rose.json": app_dir / "ember-rose.json",
    }
    if not theme_sources["light-theme.json"].exists():
        # Dev fallback: allow bootstrapping directly from the repository sample.
        fallback = app_dir.parents[2] / "dev-assets" / "theme" / "test-light-theme.json"
        if fallback.exists():
            theme_sources["light-theme.json"] = fallback

    for filename, source in theme_sources.items():
        dest = theme_dir / filename
        try:
            if source.exists() and not dest.exists():
                shutil.copy2(source, dest)
                copied_any = True
        except Exception:
            continue
    return copied_any


def _bundled_user_templates_dir() -> Path | None:
    """Return the bundled sp/templates directory when available."""
    rel_path = os.path.join("sp", "templates")
    for candidate in _resource_candidates(rel_path):
        path = Path(candidate)
        if path.exists() and path.is_dir():
            return path
    fallback = Path(__file__).resolve().parents[1] / "templates"
    if fallback.exists() and fallback.is_dir():
        return fallback
    return None


def _ensure_user_template_files() -> bool:
    """Ensure ~/.stillpoint/templates is seeded from bundled templates on first run.

    Copies bundled templates only when the user template directory is missing or
    contains no entries.
    """
    template_dir = Path.home() / ".stillpoint" / "templates"
    try:
        if template_dir.exists() and any(template_dir.iterdir()):
            return False
        template_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        return False

    source_dir = _bundled_user_templates_dir()
    if source_dir is None:
        return False

    copied_any = False
    try:
        for source in source_dir.iterdir():
            dest = template_dir / source.name
            if source.is_dir():
                shutil.copytree(source, dest, dirs_exist_ok=True)
            else:
                shutil.copy2(source, dest)
            copied_any = True
    except Exception:
        return False
    return copied_any


def _apply_startup_theme_defaults(app: QApplication) -> None:
    """Apply OS-based default theme selection for users without explicit preference."""
    seeded_theme_files = _ensure_user_theme_files()
    is_light, source = _detect_light_color_scheme(app)
    preferred = "light-theme.json" if is_light else "dark-theme.json"
    current_pref = (config.load_theme_preference() or "").strip().lower()
    if current_pref not in {"", "default"}:
        _startup(
            f"Theme preserved: {current_pref or '(empty)'} (detected mode={'light' if is_light else 'dark'}, source={source})."
        )
        return
    config.save_theme_preference(preferred)
    reason = "default-preference+seeded-theme-files" if seeded_theme_files else "default-preference"
    _startup(
        f"Theme auto-selected: {preferred} (mode={'light' if is_light else 'dark'}, source={source}, reason={reason})."
    )


def _apply_startup_theme_palette(app: QApplication) -> None:
    """Apply a Qt palette derived from the selected StillPoint theme."""
    try:
        from sp.app.ui.theme import apply_qt_palette
    except Exception as exc:
        _startup(f"Theme palette apply skipped (theme import failed): {exc}")
        return
    try:
        apply_qt_palette(app)
    except Exception as exc:
        _startup(f"Theme palette apply failed: {exc}")


def _write_local_ui_token(token: str) -> None:
    try:
        token_path = Path.home() / ".stillpoint" / "local-ui-token"
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(token, encoding="utf-8")
        try:
            os.chmod(token_path, 0o600)
        except Exception:
            pass
    except Exception:
        pass


def _write_local_api_base(host: str, port: int) -> None:
    try:
        base_path = Path.home() / ".stillpoint" / "api-base"
        base_path.parent.mkdir(parents=True, exist_ok=True)
        base_path.write_text(f"http://{host}:{port}", encoding="utf-8")
        try:
            os.chmod(base_path, 0o600)
        except Exception:
            pass
    except Exception:
        pass


def _qt_message_handler(mode: QtMsgType, context, message: str) -> None:
    """Custom Qt message handler to suppress known harmless warnings."""
    # Suppress DirectWrite font warning on Windows
    if "QWindowsFontEngineDirectWrite::recalcAdvances" in message:
        return
    # Suppress other known harmless warnings if needed
    if "GetDesignGlyphMetrics failed" in message:
        return
    if "QTextCursor::setPosition" in message:
        return
    if "Accessible invalid" in message or "Could not find accessible on path" in message:
        return
    if "GetApplicationBusAddress" in message:
        return
    if "Could not parse stylesheet" in message and not log_enabled("ui_state"):
        return
    if "QFont::setPointSize" in message and "Point size <= 0" in message and not log_enabled("ui_state"):
        return
    # Let other messages through to the default handler
    if mode == QtMsgType.QtDebugMsg:
        print(f"Qt Debug: {message}", file=sys.stderr)
    elif mode == QtMsgType.QtWarningMsg:
        print(f"Qt Warning: {message}", file=sys.stderr)
    elif mode == QtMsgType.QtCriticalMsg:
        print(f"Qt Critical: {message}", file=sys.stderr)
    elif mode == QtMsgType.QtFatalMsg:
        print(f"Qt Fatal: {message}", file=sys.stderr)
        sys.exit(1)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="StillPoint desktop entry point.")
    parser.add_argument(
        "--server",
        action="store_true",
        help="Run StillPoint in API server-only mode (no UI).",
    )
    parser.add_argument("--vault", help="Path to a vault to open at startup.")
    parser.add_argument(
        "--vault-ref",
        help="Encoded vault reference, e.g. remote::https://host:port::/vault/path",
    )
    parser.add_argument(
        "--select-vault",
        action="store_true",
        help="Show the vault picker on startup instead of auto-opening the default vault.",
    )
    parser.add_argument(
        "--quick-capture",
        action="store_true",
        help="Open Quick Capture or capture text via CLI.",
    )
    parser.add_argument("--text", help="Quick Capture text (omit to read from stdin).")
    parser.add_argument("--page", help="Quick Capture custom page (colon path or /path).")
    parser.add_argument("--port", type=int, help="Preferred API port (0 = auto-select).")
    parser.add_argument("--host", default=os.getenv("SP_HOST", "127.0.0.1"), help="Host/interface to bind the API server.")
    parser.add_argument("--vaults-root", help="Base folder where server-managed vaults live (server mode).")
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Allow server mode without SERVER_ADMIN_PASSWORD (NOT RECOMMENDED).",
    )
    parser.add_argument("--webserver", nargs="?", const="127.0.0.1:0", help="Start web server mode [bind:port]. Default: 127.0.0.1:0")
    parser.add_argument("--excalidraw-webview", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--mcp-bridge", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--excalidraw-webview-url", help=argparse.SUPPRESS)
    parser.add_argument("--excalidraw-webview-title", default="StillPoint Excalidraw", help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def _should_use_minimal_font_scan() -> bool:
    """Determine whether minimal font scanning should be used."""
    try:
        return config.load_minimal_font_scan_enabled()
    except Exception:
        return False


def _maybe_use_minimal_fonts() -> None:
    """Optionally force Qt to see only a small font set to avoid long font scans.

    Enable via the global preference or SP_MINIMAL_FONT_SCAN=1. This writes a tiny
    fontconfig file under ~/.cache/stillpoint/fonts-minimal and points
    FONTCONFIG_FILE/FONTCONFIG_PATH/QT_QPA_FONTDIR to it, copying a single known font
    if needed.
    """
    if not _should_use_minimal_font_scan():
        return
    cache_root = Path(os.getenv("XDG_CACHE_HOME", Path.home() / ".cache")) / "stillpoint" / "fonts-minimal"
    font_dir = cache_root / "fonts"
    cache_root.mkdir(parents=True, exist_ok=True)
    font_dir.mkdir(parents=True, exist_ok=True)

    # Pick a small, common font set without walking the whole tree.
    if os.name == "nt":
        win_fonts = Path(os.getenv("WINDIR", "C:\\Windows")) / "Fonts"
        candidates = [
            win_fonts / "segoeui.ttf",
            win_fonts / "arial.ttf",
            win_fonts / "tahoma.ttf",
        ]
        mono_candidates = [
            win_fonts / "consola.ttf",
            win_fonts / "cour.ttf",
            win_fonts / "lucon.ttf",
        ]
        emoji_candidates = [
            win_fonts / "seguiemj.ttf",  # Segoe UI Emoji
        ]
    elif sys.platform == "darwin":
        candidates = [
            Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
            Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
            Path("/System/Library/Fonts/SFNS.ttf"),
        ]
        mono_candidates = [
            Path("/System/Library/Fonts/SFMono-Regular.otf"),
            Path("/System/Library/Fonts/Supplemental/Courier New.ttf"),
            Path("/Library/Fonts/Courier New.ttf"),
        ]
        emoji_candidates = [
            Path("/System/Library/Fonts/Apple Color Emoji.ttc"),
        ]
    else:
        candidates = [
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf"),
            Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
            Path("/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"),
        ]
        mono_candidates = [
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"),
            Path("/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf"),
            Path("/usr/share/fonts/truetype/noto/NotoSansMono-Regular.ttf"),
            Path("/usr/share/fonts/truetype/ubuntu/UbuntuMono-R.ttf"),
        ]
        emoji_candidates = [
            Path("/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"),
            Path("/usr/share/fonts/noto/NotoColorEmoji.ttf"),
            Path("/usr/share/fonts/truetype/noto/NotoEmoji-Regular.ttf"),
        ]
    src = next((p for p in candidates if p.exists()), None)
    if not src:
        _startup("[StillPoint] SP_MINIMAL_FONT_SCAN set but no candidate font found; falling back to system fonts.")
        return
    dest = font_dir / src.name
    try:
        if not dest.exists():
            shutil.copy2(src, dest)
    except Exception as exc:
        _startup(f"[StillPoint] Failed to copy minimal font {src}: {exc}")
        return

    # Ensure a monospace font is available for code/tables
    mono_src = next((p for p in mono_candidates if p.exists()), None)
    mono_dest = None
    mono_family = None
    if mono_src:
        mono_dest = font_dir / mono_src.name
        try:
            if not mono_dest.exists():
                shutil.copy2(mono_src, mono_dest)
        except Exception as exc:
            _startup(f"[StillPoint] Failed to copy minimal monospace font {mono_src}: {exc}")
        else:
            family_lookup = {
                "consola.ttf": "Consolas",
                "cour.ttf": "Courier New",
                "lucon.ttf": "Lucida Console",
                "DejaVuSansMono.ttf": "DejaVu Sans Mono",
                "LiberationMono-Regular.ttf": "Liberation Mono",
                "NotoSansMono-Regular.ttf": "Noto Sans Mono",
                "UbuntuMono-R.ttf": "Ubuntu Mono",
            }
            mono_family = family_lookup.get(mono_src.name, mono_src.stem)
            _startup(f"[StillPoint] Minimal font scan: bundled monospace font {mono_src} -> {mono_dest} (family {mono_family})")
    else:
        _startup("[StillPoint] Minimal font scan: no monospace candidate found; tables/code may lack monospace.")

    # Ensure an emoji-capable fallback font is available.
    emoji_src = next((p for p in emoji_candidates if p.exists()), None)
    emoji_dest = None
    emoji_family = None
    if emoji_src:
        emoji_dest = font_dir / emoji_src.name
        try:
            if not emoji_dest.exists():
                shutil.copy2(emoji_src, emoji_dest)
        except Exception as exc:
            _startup(f"[StillPoint] Failed to copy minimal emoji font {emoji_src}: {exc}")
        else:
            emoji_family_lookup = {
                "seguiemj.ttf": "Segoe UI Emoji",
                "Apple Color Emoji.ttc": "Apple Color Emoji",
                "NotoColorEmoji.ttf": "Noto Color Emoji",
                "NotoEmoji-Regular.ttf": "Noto Emoji",
            }
            emoji_family = emoji_family_lookup.get(emoji_src.name, emoji_src.stem)
            _startup(
                f"[StillPoint] Minimal font scan: bundled emoji font {emoji_src} -> {emoji_dest} (family {emoji_family})"
            )
    else:
        _startup("[StillPoint] Minimal font scan: no emoji candidate found; emoji glyphs may render as tofu.")

    fonts_conf = cache_root / "fonts.conf"
    try:
        alias_blocks: list[str] = []
        if mono_family:
            alias_blocks.append(f"""
  <alias>
    <family>monospace</family>
    <prefer>
      <family>{mono_family}</family>
    </prefer>
  </alias>""")
        if emoji_family:
            alias_blocks.append(f"""
  <alias>
    <family>emoji</family>
    <prefer>
      <family>{emoji_family}</family>
    </prefer>
  </alias>""")
            alias_blocks.append(f"""
  <alias>
    <family>sans-serif</family>
    <prefer>
      <family>{emoji_family}</family>
    </prefer>
  </alias>""")
        alias_block = "".join(alias_blocks)
        fonts_conf.write_text(
            f"""<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "fonts.dtd">
<fontconfig>
  <dir>{font_dir}</dir>
  <config>{alias_block}
  </config>
</fontconfig>
""",
            encoding="utf-8",
        )
    except Exception as exc:
        _startup(f"[StillPoint] Failed to write minimal fonts.conf: {exc}")
        return

    os.environ["FONTCONFIG_FILE"] = str(fonts_conf)
    os.environ["FONTCONFIG_PATH"] = str(cache_root)
    os.environ["QT_QPA_FONTDIR"] = str(font_dir)
    _startup(f"[StillPoint] Minimal font scan enabled; using {dest} via {fonts_conf}")


def _apply_application_font(app: QApplication) -> None:
    """Apply user-preferred application font family/size, if configured."""
    try:
        family = config.load_application_font()
        size = config.load_application_font_size()
    except Exception:
        return
    if not family and size is None:
        return
    font = app.font()
    if family:
        font.setFamily(family)
    if size is not None:
        font.setPointSize(max(6, size))
        # Persist in case minimal font scan bypasses normal apply
        try:
            config.save_application_font_size(size)
        except Exception:
            pass
    app.setFont(font)


def _find_open_port(host: str, preferred: int) -> int:
    """Try preferred port, otherwise fall back to an ephemeral port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, preferred))
            return s.getsockname()[1]
        except OSError:
            pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return s.getsockname()[1]


def _start_api_server(host: str, preferred_port: int | None) -> tuple[int, uvicorn.Server, str]:
    """Start embedded API server with auto-generated server admin password."""
    # Generate secure password for embedded server
    server_admin_password = secrets.token_urlsafe(32)
    os.environ["SERVER_ADMIN_PASSWORD"] = server_admin_password
    os.environ["STILLPOINT_EMBEDDED_SERVER"] = "1"
    
    # Import api module AFTER setting password, since FastAPI app is created at import time
    from sp.server import api as api_module
    
    env_port = os.getenv("SP_PORT")
    preferred = preferred_port if preferred_port is not None else int(env_port or "8765")
    # Allow 0 to force ephemeral port selection
    preferred = 0 if preferred == 0 else preferred
    port = _find_open_port(host, preferred)
    # Disable uvicorn's logging config when bundled with PyInstaller
    # to avoid "Unable to configure formatter 'default'" errors
    log_config = None if getattr(sys, "frozen", False) else None
    config = uvicorn.Config(
        api_module.get_app(),
        host=host,
        port=port,
        loop="asyncio",
        http="h11",
        ws="none",
        log_level=os.getenv("UVICORN_LOG_LEVEL", "warning"),
        log_config=log_config,
    )
    server = uvicorn.Server(config)
    eventloop_diag.log(
        "embedded API server configured "
        f"host={host} port={port} loop={config.loop!r} http={config.http!r} ws={config.ws!r}"
    )
    
    # Track startup status
    startup_error = [None]
    
    def run_server():
        try:
            eventloop_diag.log("embedded API server thread starting")
            server.run()
            eventloop_diag.log("embedded API server thread stopped")
        except Exception as e:
            startup_error[0] = e
            eventloop_diag.log(f"embedded API server thread failed: {e!r}")
    
    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()
    
    # Wait for server to be ready by checking if it's listening on the port
    max_wait = 5.0
    start_time = time.time()
    server_ready = False
    
    while time.time() - start_time < max_wait:
        if startup_error[0] is not None:
            # Server failed to start
            print(f"\nERROR: API server startup failed: {startup_error[0]}", file=sys.stderr)
            sys.exit(1)
        
        # Check if server is listening
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.1)
                s.connect((host, port))
                server_ready = True
                break
        except (ConnectionRefusedError, OSError):
            time.sleep(0.05)
    
    if not server_ready:
        if startup_error[0] is not None:
            print(f"\nERROR: API server startup failed: {startup_error[0]}", file=sys.stderr)
        else:
            print(f"\nERROR: API server failed to start within {max_wait} seconds", file=sys.stderr)
        sys.exit(1)
    
    # Give the server a moment to fully initialize
    time.sleep(0.1)
    return port, server, server_admin_password


def _run_webserver_mode(args: argparse.Namespace) -> None:
    """Run in headless web server mode."""
    import signal
    from sp.webserver import WebServer
    
    # Parse bind:port from --webserver argument
    bind_str = args.webserver
    if ":" in bind_str:
        host, port_str = bind_str.rsplit(":", 1)
        try:
            port = int(port_str)
        except ValueError:
            port = 0
    else:
        host = bind_str
        port = 0
    
    # Get vault path
    vault_path = args.vault
    if not vault_path:
        # Try to get most recent vault from config
        config.init_settings()
        recent = config.get_recent_vaults()
        if recent:
            vault_path = recent[0]
        else:
            print("Error: No vault specified. Use --vault <path>", file=sys.stderr)
            sys.exit(1)
    
    vault_path = Path(vault_path).resolve()
    if not vault_path.exists():
        print(f"Error: Vault not found: {vault_path}", file=sys.stderr)
        sys.exit(1)
    
    # Initialize config with vault
    config.init_settings()
    config.set_active_vault(str(vault_path))
    
    # Create and start web server
    web_server = WebServer(str(vault_path), config=config)
    actual_host, actual_port = web_server.start(host, port)
    
    protocol = "https" if web_server.use_ssl else "http"
    url = f"{protocol}://{actual_host}:{actual_port}/"
    
    print(f"\n✓ StillPoint Web Server started")
    print(f"  Vault: {vault_path}")
    print(f"  URL:   {url}")
    print(f"\nPress Ctrl+C to stop.\n")
    
    # Setup signal handler for graceful shutdown
    def signal_handler(sig, frame):
        print("\n\nShutting down web server...")
        web_server.stop()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Keep running until interrupted
    try:
        while web_server.is_running:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n\nShutting down web server...")
        web_server.stop()


def _run_server_mode(args: argparse.Namespace) -> None:
    """Run in standalone API server mode."""
    from sp.server import api as api_module

    port = args.port if args.port is not None else 8000
    api_module.run_server(
        host=args.host,
        port=port,
        vaults_root=args.vaults_root,
        insecure=args.insecure,
    )


def _parse_vault_arg(argv: list[str]) -> str | None:
    """Return a vault path passed via --vault flag, if present."""
    for idx, arg in enumerate(argv):
        if arg == "--vault" and idx + 1 < len(argv):
            return argv[idx + 1]
    return None


def _sp(msg: str) -> None:
    """Always-on lifecycle logger."""
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[SP {timestamp}] {msg}", file=sys.stderr)


def _startup(msg: str) -> None:
    """Startup diagnostics behind SP_LOG_STARTUP."""
    if log_enabled("startup"):
        _sp(msg)

_FAULTHANDLER_FILE = None


def _enable_faulthandler_log() -> None:
    """Enable faulthandler to capture native/Python crashes to a temp log."""
    global _FAULTHANDLER_FILE
    if os.getenv("SP_DISABLE_FAULTHANDLER", "0") not in ("0", "false", "False", ""):
        return
    try:
        import faulthandler
    except Exception:
        return
    try:
        log_path = Path(tempfile.gettempdir()) / "stillpoint-faulthandler.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        _FAULTHANDLER_FILE = open(log_path, "a", buffering=1)
        faulthandler.enable(_FAULTHANDLER_FILE)
        os.environ["STILLPOINT_FAULTHANDLER_LOG"] = str(log_path)
        _startup(f"Faulthandler logging to {log_path}")
    except Exception as exc:
        try:
            _startup(f"Failed to enable faulthandler log: {exc}")
        except Exception:
            pass


def main() -> None:
    args = _parse_args(sys.argv[1:])

    if args.mcp_bridge:
        from sp.app.mcp_bridge import run_stdio_bridge

        raise SystemExit(run_stdio_bridge())

    if args.excalidraw_webview:
        if not args.excalidraw_webview_url:
            print("ERROR: --excalidraw-webview-url is required", file=sys.stderr)
            sys.exit(2)
        from sp.app.excalidraw_webview_process import main as excalidraw_webview_main

        sys.exit(
            excalidraw_webview_main(
                [
                    "--url",
                    args.excalidraw_webview_url,
                    "--title",
                    args.excalidraw_webview_title,
                ]
            )
        )

    if args.server:
        _run_server_mode(args)
        return

    if args.quick_capture:
        from sp.app.quickcapture import run_quick_capture
        rc = run_quick_capture(
            vault=args.vault,
            page=args.page,
            text=args.text,
            allow_overlay=True,
        )
        sys.exit(rc)
    
    # Handle webserver mode
    if args.webserver is not None:
        _run_webserver_mode(args)
        return

    configure_linux_webengine_env()
    try:
        QApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    except Exception:
        pass
    
    start_ts = time.time()
    _enable_faulthandler_log()
    _sp("Application starting.")
    config.init_settings()
    _ensure_user_template_files()
    _maybe_use_minimal_fonts()
    # Set Windows App User Model ID before creating QApplication
    _set_windows_app_id()
    # Install custom message handler to suppress harmless Qt warnings
    qInstallMessageHandler(_qt_message_handler)
    local_ui_token = secrets.token_urlsafe(32)
    _write_local_ui_token(local_ui_token)
    # Start API server (this will set password and import api module)
    port, server, server_admin_password = _start_api_server(args.host, args.port)
    _write_local_api_base(args.host, port)
    # Import api_module after server starts to set UI token
    from sp.server import api as api_module
    api_module.set_local_ui_token(local_ui_token)
    _sp(f"API server started on {args.host}:{port}.")
    eventloop_diag.install_qtimer_probe()
    qt_app = eventloop_diag.create_application(sys.argv)
    eventloop_diag.install_ui_method_probe()
    eventloop_diag.log_fd_target("after QApplication creation")
    eventloop_diag.install_qt_event_sampler(qt_app)
    qt_app.aboutToQuit.connect(lambda: _startup("QApplication aboutToQuit emitted."))
    _apply_application_font(qt_app)
    _apply_startup_theme_defaults(qt_app)
    _apply_startup_theme_palette(qt_app)
    # Set window/app icon if available (especially needed on Linux)
    _set_app_icon(qt_app)
    # Ensure server shutdown when the UI exits
    def _request_server_exit() -> None:
        eventloop_diag.log("QApplication aboutToQuit: requesting embedded API server exit")
        setattr(server, "should_exit", True)

    qt_app.aboutToQuit.connect(_request_server_exit)
    window = MainWindow(
        api_base=f"http://{args.host}:{port}",
        local_auth_token=local_ui_token,
        embedded_server_admin_password=server_admin_password
    )
    window.resize(1200, 800)
    windows = getattr(qt_app, "_stillpoint_windows", [])
    windows.append(window)
    qt_app._stillpoint_windows = windows
    vault_hint = args.vault_ref or args.vault or _parse_vault_arg(sys.argv[1:])
    try:
        if window.startup(vault_hint=vault_hint, force_select=args.select_vault):
            window.show()
            _sp("Main window open.")
            rc = qt_app.exec()
            uptime = time.time() - start_ts
            if rc == 0:
                _sp("Application exited.")
            else:
                _sp(f"Application exited with code {rc}.")
            _startup(f"Qt event loop exited with code {rc} after {uptime:.2f}s.")
            sys.exit(rc)
        else:
            _startup("Startup cancelled by user; quitting.")
            qt_app.quit()
    except Exception as exc:
        uptime = time.time() - start_ts
        _sp(f"Unhandled exception after {uptime:.2f}s: {exc}")
        traceback.print_exc()
        try:
            qt_app.quit()
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":  # pragma: no cover - manual entry point
    main()
