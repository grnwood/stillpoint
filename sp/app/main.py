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
from pathlib import Path

import uvicorn
from PySide6.QtCore import QtMsgType, qInstallMessageHandler
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QIcon

from sp.server import api as api_module
from sp.app import config
from sp.app.ui.main_window import MainWindow


# ============================================================================
# DEBUG CONFIGURATION - Environment Variables
# ============================================================================
# Set these environment variables to "1" or "true" to enable detailed logging
# By default, all are OFF for cleaner stdout (only startup and API calls shown)
#
# SP_DEBUG_EDITOR      - Editor operations (markdown save/load, cursor positioning)
# SP_DEBUG_NAV         - Navigation operations (tree selection, history)  
# SP_DEBUG_HISTORY     - Page history tracking
# SP_DEBUG_PANELS      - Right panel signal forwarding
# SP_DEBUG_TASKS       - Task panel mouse events and signal emission
# SP_DEBUG_PLANTUML    - PlantUML rendering operations
# SP_DETAILED_PAGE_LOG - Detailed page load timing and operations
# SP_DETAILED_LOGGING  - Additional low-level internal logging (various modules)
#
# Examples:
#   export SP_DEBUG_NAV=1        # Enable navigation debugging
#   export SP_DEBUG_TASKS=1      # Enable task panel debugging
#   SP_DEBUG_EDITOR=1 ./sv.sh   # Enable for single run
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


def _set_app_icon(app: QApplication) -> None:
    """Attempt to set the application/window icon if an asset is bundled.

    On Linux, PyInstaller does not embed a binary icon into the ELF. We set the
    window icon at runtime using a PNG. On Windows/macOS the EXE/App icon is
    handled by PyInstaller, but this also ensures the window/icon in the titlebar
    matches.
    """
    for path in _resource_candidates(os.path.join("assets", "icon.png")):
        if os.path.exists(path):
            try:
                app.setWindowIcon(QIcon(path))
            except Exception:
                pass
            break


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
    parser.add_argument("--port", type=int, help="Preferred API port (0 = auto-select).")
    parser.add_argument("--host", default=os.getenv("SP_HOST", "127.0.0.1"), help="Host/interface to bind the API server.")
    parser.add_argument("--webserver", nargs="?", const="127.0.0.1:0", help="Start web server mode [bind:port]. Default: 127.0.0.1:0")
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

    # Pick a small, common font without walking the whole tree.
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
    src = next((p for p in candidates if p.exists()), None)
    if not src:
        print("[StillPoint] SP_MINIMAL_FONT_SCAN set but no candidate font found; falling back to system fonts.", file=sys.stderr)
        return
    dest = font_dir / src.name
    try:
        if not dest.exists():
            shutil.copy2(src, dest)
    except Exception as exc:
        print(f"[StillPoint] Failed to copy minimal font {src}: {exc}", file=sys.stderr)
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
            print(f"[StillPoint] Failed to copy minimal monospace font {mono_src}: {exc}", file=sys.stderr)
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
            print(f"[StillPoint] Minimal font scan: bundled monospace font {mono_src} -> {mono_dest} (family {mono_family})", file=sys.stderr)
    else:
        print("[StillPoint] Minimal font scan: no monospace candidate found; tables/code may lack monospace.", file=sys.stderr)

    fonts_conf = cache_root / "fonts.conf"
    try:
        alias_block = ""
        if mono_family:
            alias_block = f"""
  <alias>
    <family>monospace</family>
    <prefer>
      <family>{mono_family}</family>
    </prefer>
  </alias>"""
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
        print(f"[StillPoint] Failed to write minimal fonts.conf: {exc}", file=sys.stderr)
        return

    os.environ["FONTCONFIG_FILE"] = str(fonts_conf)
    os.environ["FONTCONFIG_PATH"] = str(cache_root)
    os.environ["QT_QPA_FONTDIR"] = str(font_dir)
    print(f"[StillPoint] Minimal font scan enabled; using {dest} via {fonts_conf}", file=sys.stderr)


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


def _start_api_server(host: str, preferred_port: int | None) -> tuple[int, uvicorn.Server]:
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
        log_level=os.getenv("UVICORN_LOG_LEVEL", "debug"),
        log_config=log_config,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    # Give the event loop a moment to bind the socket before the UI fires requests.
    time.sleep(0.2)
    return port, server


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


def _parse_vault_arg(argv: list[str]) -> str | None:
    """Return a vault path passed via --vault flag, if present."""
    for idx, arg in enumerate(argv):
        if arg == "--vault" and idx + 1 < len(argv):
            return argv[idx + 1]
    return None


def _diag(msg: str) -> None:
    """Lightweight diagnostic logger for startup/teardown events."""
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[ZimxDiag {timestamp}] {msg}", file=sys.stderr)

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
        _diag(f"Faulthandler logging to {log_path}")
    except Exception as exc:
        try:
            _diag(f"Failed to enable faulthandler log: {exc}")
        except Exception:
            pass


def main() -> None:
    args = _parse_args(sys.argv[1:])
    
    # Handle webserver mode
    if args.webserver is not None:
        _run_webserver_mode(args)
        return
    
    start_ts = time.time()
    _enable_faulthandler_log()
    _diag("Application starting.")
    config.init_settings()
    _maybe_use_minimal_fonts()
    # Install custom message handler to suppress harmless Qt warnings
    qInstallMessageHandler(_qt_message_handler)
    local_ui_token = secrets.token_urlsafe(32)
    api_module.set_local_ui_token(local_ui_token)
    port, server = _start_api_server(args.host, args.port)
    _diag(f"API server started on {args.host}:{port}.")
    qt_app = QApplication(sys.argv)
    qt_app.aboutToQuit.connect(lambda: _diag("QApplication aboutToQuit emitted."))
    _apply_application_font(qt_app)
    # Set window/app icon if available (especially needed on Linux)
    _set_app_icon(qt_app)
    # Ensure server shutdown when the UI exits
    qt_app.aboutToQuit.connect(lambda: setattr(server, "should_exit", True))
    window = MainWindow(api_base=f"http://{args.host}:{port}", local_auth_token=local_ui_token)
    window.resize(1200, 800)
    windows = getattr(qt_app, "_stillpoint_windows", [])
    windows.append(window)
    qt_app._stillpoint_windows = windows
    vault_hint = args.vault_ref or args.vault or _parse_vault_arg(sys.argv[1:])
    try:
        if window.startup(vault_hint=vault_hint, force_select=args.select_vault):
            window.show()
            _diag("Main window shown; entering Qt event loop.")
            rc = qt_app.exec()
            uptime = time.time() - start_ts
            _diag(f"Qt event loop exited with code {rc} after {uptime:.2f}s.")
            sys.exit(rc)
        else:
            _diag("Startup cancelled by user; quitting.")
            qt_app.quit()
    except BaseException as exc:
        uptime = time.time() - start_ts
        _diag(f"Unhandled exception after {uptime:.2f}s: {exc}")
        traceback.print_exc()
        try:
            qt_app.quit()
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":  # pragma: no cover - manual entry point
    main()
