from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
import time
import tomllib
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from starlette.requests import Request
from PySide6 import QtGui
from PySide6.QtWidgets import QApplication

from sp.app import config
from sp.app.mcp_bridge import ensure_bridge_launcher
from sp.app.terminal_session import PosixPtySession, default_shell_command, parse_shell_command
from sp.server import api
from sp.server.state import vault_state


def test_terminal_theme_contrasts_with_dark_and_light_palettes() -> None:
    from sp.app.ui.terminal_pane import terminal_theme_from_palette

    dark = QtGui.QPalette()
    dark.setColor(QtGui.QPalette.ColorRole.Base, QtGui.QColor("#101218"))
    dark.setColor(QtGui.QPalette.ColorRole.Text, QtGui.QColor("#f2f4f8"))
    dark_theme = terminal_theme_from_palette(dark)
    assert dark_theme["background"] == "#101218"
    assert dark_theme["foreground"] == "#f2f4f8"
    assert QtGui.QColor(dark_theme["black"]).lightness() > QtGui.QColor("#101218").lightness()

    light = QtGui.QPalette()
    light.setColor(QtGui.QPalette.ColorRole.Base, QtGui.QColor("#fafafa"))
    light.setColor(QtGui.QPalette.ColorRole.Text, QtGui.QColor("#171717"))
    light_theme = terminal_theme_from_palette(light)
    assert light_theme["background"] == "#fafafa"
    assert light_theme["foreground"] == "#171717"
    assert QtGui.QColor(light_theme["white"]).lightness() < QtGui.QColor("#fafafa").lightness()


def test_terminal_uses_stillpoint_theme_values(monkeypatch) -> None:
    from sp.app.ui import terminal_pane

    colors = {
        "markdown_editor.base.bg": "#20242b",
        "markdown_editor.base.text": "#f7f8fa",
        "markdown_editor.base.selection_bg": "#345678",
        "markdown_editor.base.selection_text": "#ffffff",
    }
    monkeypatch.setattr(terminal_pane, "theme_value", lambda path, default=None: colors.get(path, default))
    theme = terminal_pane.terminal_theme_from_stillpoint()
    assert theme["background"] == "#20242b"
    assert theme["foreground"] == "#f7f8fa"
    assert theme["cursor"] == "#f7f8fa"


def test_terminal_shell_configuration_is_structured(monkeypatch) -> None:
    assert parse_shell_command("/bin/fish", ["--login", "--no-config"]) == [
        "/bin/fish",
        "--login",
        "--no-config",
    ]
    monkeypatch.setenv("SHELL", "/bin/sh")
    assert default_shell_command("Linux") == ["/bin/sh", "-l"]


def test_terminal_settings_round_trip_as_structured_arguments(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(config, "GLOBAL_CONFIG", tmp_path / "config.json")
    config.save_terminal_settings(
        {
            "shell_executable": "/bin/fish",
            "shell_arguments": ["--login", "argument with spaces"],
            "scrollback": 12345,
            "font_family": "DejaVu Sans Mono",
            "font_size": 14,
        }
    )
    assert config.load_terminal_settings() == {
        "shell_executable": "/bin/fish",
        "shell_arguments": ["--login", "argument with spaces"],
        "scrollback": 12345,
        "font_family": "DejaVu Sans Mono",
        "font_size": 14,
    }


def test_terminal_pane_is_still_lazy_when_merely_shown(qtbot, tmp_path) -> None:
    from sp.app.ui.terminal_pane import TerminalPane

    pane = TerminalPane(vault_root_provider=lambda: tmp_path, seed_agents=lambda _root: None)
    qtbot.addWidget(pane)
    assert pane.initialized is False
    assert pane.session_running is False


def test_terminal_assets_resolve_from_pyinstaller_internal_layout(tmp_path, monkeypatch) -> None:
    from sp.app.ui.terminal_pane import TerminalSessionPane

    asset_dir = tmp_path / "_internal" / "sp" / "assets" / "terminal"
    asset_dir.mkdir(parents=True)
    (asset_dir / "terminal.html").write_text("<!doctype html>", encoding="utf-8")
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

    assert TerminalSessionPane._asset_directory() == asset_dir


def test_vault_agent_seeding_adds_missing_client_configs_without_secrets(
    main_window, tmp_path, monkeypatch
) -> None:
    workspace = tmp_path / "Vault"
    workspace.mkdir()
    agents = workspace / "AGENTS.md"
    agents.write_text("# Existing user guidance\n", encoding="utf-8")
    monkeypatch.setattr(config, "load_seed_agents_workspace", lambda: True)
    monkeypatch.setenv("STILLPOINT_MCP_TOKEN", "do-not-write-this-token")

    main_window._seed_agents_file_if_needed(workspace)

    codex_path = workspace / ".codex" / "config.toml"
    copilot_path = workspace / ".mcp.json"
    codex_text = codex_path.read_text(encoding="utf-8")
    copilot_text = copilot_path.read_text(encoding="utf-8")
    codex = tomllib.loads(codex_text)
    copilot = json.loads(copilot_text)
    assert agents.read_text(encoding="utf-8") == "# Existing user guidance\n"
    assert codex["mcp_servers"]["stillpoint"]["command"] == "stillpoint-mcp"
    assert "STILLPOINT_MCP_TOKEN" in codex["mcp_servers"]["stillpoint"]["env_vars"]
    assert copilot["mcpServers"]["stillpoint"]["command"] == "stillpoint-mcp"
    assert copilot["mcpServers"]["stillpoint"]["env"]["STILLPOINT_MCP_TOKEN"] == "${STILLPOINT_MCP_TOKEN}"
    assert "do-not-write-this-token" not in codex_text
    assert "do-not-write-this-token" not in copilot_text

    codex_path.write_text("# Keep my Codex config\n", encoding="utf-8")
    copilot_path.write_text('{"keep": true}\n', encoding="utf-8")
    main_window._seed_agents_file_if_needed(workspace)
    assert codex_path.read_text(encoding="utf-8") == "# Keep my Codex config\n"
    assert json.loads(copilot_path.read_text(encoding="utf-8")) == {"keep": True}


def test_terminal_pane_uses_compact_selector_header_and_live_font_controls(qtbot, tmp_path) -> None:
    from sp.app.ui.terminal_pane import TerminalPane

    pane = TerminalPane(
        vault_root_provider=lambda: tmp_path,
        seed_agents=lambda _root: None,
        settings_provider=lambda: {"font_family": "monospace", "font_size": 12},
    )
    qtbot.addWidget(pane)
    sizes: list[int] = []
    pane.fontSizeChanged.connect(sizes.append)

    assert not hasattr(pane, "_shell_combo")
    assert not hasattr(pane, "_font_size_label")
    assert pane.active_session._header.isHidden()
    pane._font_larger_button.click()

    assert sizes == [13]


def test_terminal_frontend_routes_keyboard_and_mouse_zoom_to_python() -> None:
    javascript = (
        Path(__file__).resolve().parents[1]
        / "sp"
        / "assets"
        / "terminal"
        / "terminal.js"
    ).read_text(encoding="utf-8")

    assert 'event.key === "-"' in javascript
    assert 'event.key === "+"' in javascript
    assert 'addEventListener("wheel"' in javascript
    assert "bridge.adjustFontSize" in javascript
    assert 'event.key.toLowerCase() === "t"' in javascript
    assert "bridge.requestNewTerminal" in javascript
    assert "linkHandler" in javascript
    assert "bridge.openExternalUrl(uri)" in javascript


def test_terminal_external_links_are_limited_to_http_and_https(qtbot) -> None:
    from sp.app.ui.terminal_pane import TerminalBridge, safe_terminal_external_url

    assert safe_terminal_external_url("https://example.com/path").toString() == "https://example.com/path"
    assert safe_terminal_external_url("http://localhost:8000/") is not None
    assert safe_terminal_external_url("javascript:alert(1)") is None
    assert safe_terminal_external_url("file:///tmp/private") is None
    assert safe_terminal_external_url("stillpoint://page/Notes") is None

    bridge = TerminalBridge()
    emitted: list[str] = []
    bridge.externalUrlRequested.connect(emitted.append)
    bridge.openExternalUrl("https://example.com/docs")
    bridge.openExternalUrl("file:///tmp/private")
    assert emitted == ["https://example.com/docs"]


def test_terminal_shortcut_request_creates_and_focuses_new_session(qtbot, tmp_path, monkeypatch) -> None:
    from sp.app.ui.terminal_pane import TerminalPane, TerminalSessionPane

    monkeypatch.setattr(TerminalSessionPane, "start_session", lambda _self: None)
    pane = TerminalPane(vault_root_provider=lambda: tmp_path, seed_agents=lambda _root: None)
    qtbot.addWidget(pane)
    first = pane.active_session

    first.newTerminalRequested.emit()

    assert pane.terminal_count == 2
    assert pane.active_session is not first
    assert pane._session_stack.currentWidget() is pane.active_session


def test_terminal_frontend_csp_allows_xterm_styles_but_not_inline_scripts() -> None:
    html = (
        Path(__file__).resolve().parents[1]
        / "sp"
        / "assets"
        / "terminal"
        / "terminal.html"
    ).read_text(encoding="utf-8")
    assert "style-src 'self' 'unsafe-inline'" in html
    assert "script-src 'self' qrc:" in html
    assert "script-src 'self' 'unsafe-inline'" not in html


def test_terminal_is_a_lazy_right_panel_tab(main_window) -> None:
    index = main_window.right_panel.tabs.indexOf(main_window._terminal_pane)
    assert index >= 0
    assert main_window.right_panel.tabs.tabText(index).startswith("Terminal · 1:")
    assert main_window._terminal_pane.initialized is False
    assert not hasattr(main_window, "_terminal_dock")


def test_terminal_toggle_uses_ctrl_shift_enter(main_window) -> None:
    shortcuts = {
        shortcut.toString()
        for shortcut in main_window._action_terminal.shortcuts()
    }

    assert "Ctrl+Shift+Return" in shortcuts
    assert "Ctrl+Shift+Enter" in shortcuts
    assert all("`" not in shortcut for shortcut in shortcuts)


def test_terminal_toggle_preserves_visible_right_panel_width(main_window, monkeypatch) -> None:
    pane = main_window._terminal_pane
    monkeypatch.setattr(main_window, "_prepare_terminal_activation", lambda: True)
    monkeypatch.setattr(pane, "start_session", lambda: None)
    monkeypatch.setattr(pane, "focus_terminal", lambda: None)
    main_window._set_right_panel_collapsed(False)
    for index in range(main_window.right_panel.tabs.count()):
        if main_window.right_panel.tabs.widget(index) is not pane:
            main_window.right_panel.tabs.setCurrentIndex(index)
            break
    main_window.editor_split.setSizes([620, 480])
    QApplication.processEvents()
    original_sizes = main_window.editor_split.sizes()

    main_window._toggle_terminal_tab()

    assert main_window.right_panel.tabs.currentWidget() is pane
    assert main_window.editor_split.sizes() == original_sizes


def test_terminal_shortcut_collapses_focused_docked_terminal(main_window, monkeypatch) -> None:
    pane = main_window._terminal_pane
    monkeypatch.setattr(main_window, "_prepare_terminal_activation", lambda: True)
    monkeypatch.setattr(main_window, "_terminal_has_focus", lambda: True)
    monkeypatch.setattr(pane, "start_session", lambda: None)
    monkeypatch.setattr(pane, "focus_terminal", lambda: None)
    main_window._set_right_panel_collapsed(False)
    main_window.right_panel.tabs.setCurrentWidget(pane)

    main_window._toggle_terminal_tab()

    assert main_window._is_right_panel_expanded() is False
    assert main_window.right_panel.tabs.currentWidget() is pane


def test_terminal_shortcut_focuses_selected_terminal_before_collapsing(main_window, monkeypatch) -> None:
    pane = main_window._terminal_pane
    focused: list[bool] = []
    monkeypatch.setattr(main_window, "_prepare_terminal_activation", lambda: True)
    monkeypatch.setattr(main_window, "_terminal_has_focus", lambda: False)
    monkeypatch.setattr(pane, "start_session", lambda: None)
    monkeypatch.setattr(pane, "focus_terminal", lambda: focused.append(True))
    main_window._set_right_panel_collapsed(False)
    main_window.right_panel.tabs.setCurrentWidget(pane)

    main_window._toggle_terminal_tab()

    assert main_window._is_right_panel_expanded() is True
    assert focused


def test_focus_border_styling_ignores_reentrant_focus_signal(main_window, monkeypatch) -> None:
    applications: list[str] = []

    def reentrant_style(style: str) -> None:
        applications.append(style)
        main_window._apply_focus_borders()

    monkeypatch.setattr(main_window.right_panel.tabs, "setStyleSheet", reentrant_style)

    main_window._apply_focus_borders()

    assert len(applications) == 1
    assert main_window._applying_focus_borders is False


def test_terminal_dropdown_switches_the_visible_active_terminal(main_window, monkeypatch) -> None:
    from sp.app.ui.terminal_pane import TerminalSessionPane

    pane = main_window._terminal_pane
    monkeypatch.setattr(TerminalSessionPane, "start_session", lambda _self: None)

    first = pane.active_session
    pane.new_terminal(["/bin/fish", "-l"])
    second = pane.active_session

    class _ForegroundSession:
        running = True

        @staticmethod
        def foreground_command_line():
            return "htop"

        def terminate(self):
            self.running = False

    second._session = _ForegroundSession()

    assert pane.terminal_count == 2
    assert second is not first
    assert pane._session_stack.currentWidget() is second
    assert "2: fish" in pane._terminal_selector.text()
    terminal_index = main_window.right_panel.tabs.indexOf(pane)
    assert main_window.right_panel.tabs.tabText(terminal_index) == "Terminal · 2: fish"

    pane.cycle_terminal_switcher(reverse=False)
    assert pane.active_session is second
    assert pane.terminal_switcher_active is True
    assert "Running · htop" in pane._terminal_switch_list.item(1).text()
    pane._commit_terminal_switcher()
    assert pane.active_session is first
    pane.cycle_terminal_switcher(reverse=True)
    assert pane.active_session is first
    pane._commit_terminal_switcher()
    assert pane.active_session is second

    pane.set_active_terminal(0)

    assert pane.active_session is first
    assert pane._session_stack.currentWidget() is first
    assert main_window.right_panel.tabs.tabText(terminal_index).startswith("Terminal · 1:")

    first.sessionExited.emit(0, "exited")

    assert pane.terminal_count == 1
    assert pane.active_session is second
    assert pane._session_stack.currentWidget() is second
    assert main_window.right_panel.tabs.tabText(terminal_index) == "Terminal · 2: fish"

    pane._rebuild_terminal_menu()
    menu_labels = [action.text() for action in pane._terminal_menu.actions()]
    assert any(label.startswith("2: fish") for label in menu_labels)
    assert "New Terminal" in menu_labels


def test_terminal_is_available_from_go_menu_and_command_palette(main_window) -> None:
    labels_and_actions = main_window._collect_menu_actions()
    go_actions = [action for label, action in labels_and_actions if label == "Go / Terminal"]

    assert go_actions == [main_window._action_go_terminal]
    assert main_window._action_go_terminal is not main_window._action_terminal
    assert main_window._action_go_terminal in [
        action
        for menu_action in main_window.menuBar().actions()
        if menu_action.menu() and menu_action.menu().title().replace("&", "") == "Go"
        for action in menu_action.menu().actions()
    ]


def test_open_terminal_window_is_available_from_view_and_command_palette(main_window) -> None:
    labels_and_actions = main_window._collect_menu_actions()
    actions = [
        action
        for label, action in labels_and_actions
        if label == "View / Open Terminal Window"
    ]

    assert actions == [main_window._action_terminal_window]


def test_go_terminal_always_focuses_instead_of_toggling_to_editor(main_window, monkeypatch) -> None:
    pane = main_window._terminal_pane
    started: list[bool] = []
    focused: list[bool] = []
    monkeypatch.setattr(main_window, "_prepare_terminal_activation", lambda: True)
    monkeypatch.setattr(pane, "start_session", lambda: started.append(True))
    monkeypatch.setattr(pane, "focus_terminal", lambda: focused.append(True))
    main_window._set_right_panel_collapsed(False)
    main_window.right_panel.tabs.setCurrentWidget(pane)
    main_window.editor_split.setSizes([620, 480])
    QApplication.processEvents()
    original_sizes = main_window.editor_split.sizes()

    main_window._run_command_bar_action(main_window._action_go_terminal)

    assert started
    assert focused
    assert main_window.right_panel.tabs.currentWidget() is pane
    assert main_window.editor_split.sizes() == original_sizes


def test_terminal_activation_does_not_rewrite_a_clean_editor(main_window, monkeypatch) -> None:
    writes: list[bool] = []
    monkeypatch.setattr(main_window, "_terminal_vault_root", lambda: Path("/tmp"))
    monkeypatch.setattr(main_window, "_is_editor_dirty", lambda: False)
    monkeypatch.setattr(
        main_window,
        "_save_current_file",
        lambda *args, **kwargs: writes.append(True),
    )
    main_window._dirty_flag = False
    main_window.editor.document().setModified(False)

    assert main_window._prepare_terminal_activation() is True
    assert writes == []


def test_terminal_popout_moves_and_reattaches_same_panel(main_window, monkeypatch, qtbot) -> None:
    pane = main_window._terminal_pane
    original_index = main_window.right_panel.tabs.indexOf(pane)
    monkeypatch.setattr(main_window, "_prepare_terminal_activation", lambda: True)
    monkeypatch.setattr(pane, "start_session", lambda: None)
    monkeypatch.setattr(pane, "focus_terminal", lambda: None)

    main_window._open_terminal_window()
    window = main_window._terminal_detached_window
    assert window is not None
    assert window.centralWidget() is pane
    assert main_window.right_panel.tabs.indexOf(pane) == -1
    assert pane.isVisible()
    assert pane._terminal_selector.isVisible()
    assert pane._session_stack.isVisible()
    assert pane._terminal_switcher.parentWidget() is pane

    window.close()
    QApplication.processEvents()
    assert main_window.right_panel.tabs.indexOf(pane) == original_index
    assert main_window._terminal_detached_window is None


def test_terminal_popout_restores_and_saves_window_geometry(main_window, monkeypatch) -> None:
    from PySide6.QtWidgets import QMainWindow

    pane = main_window._terminal_pane
    reference = QMainWindow()
    reference.resize(600, 450)
    reference.move(83, 97)
    geometry = reference.saveGeometry().toBase64().data().decode("ascii")
    saved: list[tuple[str, str]] = []
    monkeypatch.setattr(main_window, "_prepare_terminal_activation", lambda: True)
    monkeypatch.setattr(pane, "start_session", lambda: None)
    monkeypatch.setattr(pane, "focus_terminal", lambda: None)
    monkeypatch.setattr(
        config,
        "load_dialog_geometry",
        lambda key: geometry if key == "terminal_window" else None,
    )
    monkeypatch.setattr(config, "save_dialog_geometry", lambda key, value: saved.append((key, value)))

    main_window._open_terminal_window()
    window = main_window._terminal_detached_window
    assert window is not None
    assert window.size() == reference.size()
    # Window managers may translate saved client coordinates by the frame
    # border while preserving the requested screen location.
    assert (window.pos() - reference.pos()).manhattanLength() <= 4

    window.move(121, 143)
    window.resize(811, 577)
    final_geometry = window.saveGeometry().toBase64().data().decode("ascii")
    window.close()
    QApplication.processEvents()

    assert saved[-1] == ("terminal_window", final_geometry)
    reference.close()


def test_terminal_shortcut_closes_detached_window_and_reattaches(main_window, monkeypatch) -> None:
    pane = main_window._terminal_pane
    original_index = main_window.right_panel.tabs.indexOf(pane)
    monkeypatch.setattr(main_window, "_prepare_terminal_activation", lambda: True)
    monkeypatch.setattr(main_window, "_terminal_detached_has_focus", lambda _window: True)
    monkeypatch.setattr(pane, "start_session", lambda: None)
    monkeypatch.setattr(pane, "focus_terminal", lambda: None)
    main_window._open_terminal_window()
    window = main_window._terminal_detached_window
    assert window is not None and window.isVisible()

    main_window._toggle_terminal_tab()
    QApplication.processEvents()

    assert main_window._terminal_detached_window is None
    assert main_window.right_panel.tabs.indexOf(pane) == original_index


def test_terminal_shortcut_restores_inactive_detached_window(main_window, monkeypatch) -> None:
    pane = main_window._terminal_pane
    focused: list[bool] = []
    monkeypatch.setattr(main_window, "_prepare_terminal_activation", lambda: True)
    monkeypatch.setattr(pane, "start_session", lambda: None)
    monkeypatch.setattr(pane, "focus_terminal", lambda: focused.append(True))
    main_window._open_terminal_window()
    window = main_window._terminal_detached_window
    assert window is not None
    focused.clear()
    window.showMinimized()
    QApplication.processEvents()
    monkeypatch.setattr(main_window, "_terminal_detached_has_focus", lambda _window: False)

    main_window._toggle_terminal_tab()
    QApplication.processEvents()

    assert main_window._terminal_detached_window is window
    assert main_window.right_panel.tabs.indexOf(pane) == -1
    assert window.isVisible()
    assert not window.isMinimized()
    assert focused

    window.close()
    QApplication.processEvents()


@pytest.mark.skipif(os.name == "nt", reason="POSIX PTY test")
def test_posix_terminal_session_runs_in_requested_directory(tmp_path) -> None:
    output: list[str] = []
    finished = threading.Event()
    session = PosixPtySession(
        on_output=output.append,
        on_exit=lambda code, reason: (output.append(f"EXIT:{code}:{reason}"), finished.set()),
        on_error=lambda message: output.append(f"ERROR:{message}"),
    )
    session.start(
        cwd=tmp_path,
        argv=["/bin/sh"],
        environment={},
        rows=24,
        columns=80,
    )
    session.resize(30, 100)
    session.write("printf 'stillpoint-pty-ok\\n'; pwd; exit 7\n")
    assert finished.wait(5), "PTY child did not exit"
    rendered = "".join(output)
    assert "stillpoint-pty-ok" in rendered
    assert str(tmp_path) in rendered
    assert "EXIT:7" in rendered


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux /proc foreground-process test")
def test_posix_terminal_reports_foreground_command(tmp_path) -> None:
    session = PosixPtySession(
        on_output=lambda _data: None,
        on_exit=lambda _code, _reason: None,
        on_error=lambda _message: None,
    )
    session.start(
        cwd=tmp_path,
        argv=["/bin/sh"],
        environment={},
        rows=24,
        columns=80,
    )
    try:
        session.write("sleep 2\n")
        deadline = time.monotonic() + 1.5
        foreground = ""
        while time.monotonic() < deadline:
            foreground = session.foreground_command_line() or ""
            if "sleep" in foreground:
                break
            time.sleep(0.03)
        assert "sleep" in foreground
    finally:
        session.terminate()


def test_bridge_launcher_contains_no_terminal_credential(monkeypatch) -> None:
    monkeypatch.setenv("STILLPOINT_MCP_TOKEN", "super-secret-session-token")
    launcher = ensure_bridge_launcher()
    assert launcher.is_file()
    assert "super-secret-session-token" not in launcher.read_text(encoding="utf-8")


def test_bridge_launcher_starts_outside_the_stillpoint_checkout(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("STILLPOINT_MCP_URL", raising=False)
    monkeypatch.delenv("STILLPOINT_MCP_TOKEN", raising=False)
    launcher = ensure_bridge_launcher()

    result = subprocess.run(
        [str(launcher)],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
        timeout=5,
    )

    assert result.returncode == 2
    assert "requires STILLPOINT_MCP_URL and STILLPOINT_MCP_TOKEN" in result.stderr
    assert "No module named 'sp'" not in result.stderr


def test_mcp_token_is_vault_scoped_and_revocable(tmp_path, monkeypatch) -> None:
    vault = tmp_path / "Vault"
    page = vault / "Notes" / "Notes.md"
    page.parent.mkdir(parents=True)
    page.write_text("# Notes\n\nMCP terminal content.\n", encoding="utf-8")
    vault_state.set_root(str(vault))
    config.set_active_vault(str(vault))
    monkeypatch.setattr(api, "AUTH_ENABLED", False)
    with api._MCP_TOKEN_LOCK:
        api._MCP_ACTIVE_TOKENS.clear()

    user = api.AuthModels.UserInfo(username="admin", is_admin=True, can_write=True)
    issued = api.auth_mcp_token(
        api.AuthModels.McpTokenRequest(ttl_seconds=300, session_id="test-window"),
        user,
    )
    token = issued["token"]
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    claims = api._require_mcp_claims(credentials, "test-window")
    assert claims["vault"] == api._mcp_vault_id(vault)
    assert {tool["name"] for tool in api._MCP_TOOLS} >= {
        "vault.read",
        "vault.search",
        "vault.write",
        "page.context",
        "page.patch",
        "tasks.create",
        "tasks.update",
        "tasks.complete",
        "journal.open",
        "page.move",
    }
    read = api._mcp_call_tool(
        "vault.read",
        {"path": "/Notes/Notes.md"},
        claims,
    )
    assert "MCP terminal content" in read["content"]

    def mcp_request(message: dict) -> dict:
        body = json.dumps(message).encode("utf-8")
        sent = False

        async def receive() -> dict:
            nonlocal sent
            if sent:
                return {"type": "http.disconnect"}
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}

        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/mcp",
                "headers": [(b"x-stillpoint-window-id", b"test-window")],
            },
            receive,
        )
        return asyncio.run(api.mcp_endpoint(request, credentials))

    initialized = mcp_request(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}}
    )
    init_result = initialized["result"]
    assert "prefer the StillPoint MCP tools" in init_result["instructions"]
    assert "resources" in init_result["capabilities"]
    resources = mcp_request({"jsonrpc": "2.0", "id": 2, "method": "resources/list"})
    assert {item["uri"] for item in resources["result"]["resources"]} >= {
        "stillpoint://tasks/open",
        "stillpoint://recent/changes",
    }

    api.auth_mcp_token_revoke(api.AuthModels.McpTokenRevokeRequest(token=token), user)
    with pytest.raises(HTTPException) as revoked:
        api._require_mcp_claims(credentials, "test-window")
    assert revoked.value.status_code == 401
    config.set_active_vault(None)


def test_mcp_context_safe_mutations_journals_and_resources(tmp_path) -> None:
    vault = tmp_path / "Vault"
    notes = vault / "Notes" / "Notes.md"
    child = vault / "Notes" / "Child" / "Child.md"
    notes.parent.mkdir(parents=True)
    child.parent.mkdir(parents=True)
    notes.write_text("# Notes\n\nSee [:Notes:Child|Child].\n\n## Tasks\n", encoding="utf-8")
    child.write_text("# Child\n\nChild context.\n", encoding="utf-8")
    vault_state.set_root(str(vault))
    config.set_active_vault(str(vault))
    claims = {"sub": "test", "perm": "read_write"}
    read_only = {"sub": "test", "perm": "read"}
    try:
        api.app_indexer.index_page("/Notes/Notes.md", notes.read_text(encoding="utf-8"))
        api.app_indexer.index_page("/Notes/Child/Child.md", child.read_text(encoding="utf-8"))

        context = api._mcp_call_tool("page.context", {"path": ":Notes"}, read_only)
        assert context["path"] == "/Notes/Notes.md"
        assert context["headings"][0]["text"] == "Notes"
        assert context["children"][0]["path"] == "/Notes/Child/Child.md"
        assert "/Notes/Child/Child.md" in context["links"]

        preview = api._mcp_call_tool(
            "page.patch",
            {
                "path": ":Notes",
                "operation": "insert_after_heading",
                "heading": "Notes",
                "content": "A safely inserted summary.",
                "expected_mtime_ns": context["mtime_ns"],
                "dry_run": True,
            },
            read_only,
        )
        assert preview["changed"] is True
        assert "safely inserted" not in notes.read_text(encoding="utf-8")
        applied = api._mcp_call_tool(
            "page.patch",
            {
                "path": ":Notes",
                "operation": "insert_after_heading",
                "heading": "Notes",
                "content": "A safely inserted summary.",
                "expected_mtime_ns": context["mtime_ns"],
            },
            claims,
        )
        assert applied["dry_run"] is False
        assert "safely inserted" in notes.read_text(encoding="utf-8")
        search = api._mcp_call_tool(
            "vault.search", {"query": "safely inserted", "path_prefix": ":Notes"}, read_only
        )
        assert search["results"][0]["path"] == "/Notes/Notes.md"
        assert search["results"][0]["colon_link"] == "[:Notes|Notes]"
        assert {"score", "snippet", "modified", "revision", "tags"} <= search["results"][0].keys()

        created = api._mcp_call_tool(
            "tasks.create",
            {"path": ":Notes", "text": "Ship MCP support", "priority": 2, "tags": ["agent"]},
            claims,
        )
        assert created["task"] == "☐ Ship MCP support !! @agent"
        task = api.app_indexer.extract_tasks("/Notes/Notes.md", notes.read_text(encoding="utf-8"))[-1]
        completed = api._mcp_call_tool(
            "tasks.complete",
            {
                "path": ":Notes",
                "line": task["line"],
                "expected_text": task["text"],
                "expected_status": "todo",
            },
            claims,
        )
        assert completed["ok"] is True
        assert "☑ Ship MCP support" in notes.read_text(encoding="utf-8")

        missing = api._mcp_call_tool(
            "journal.open", {"date": "2034-05-06", "create": False}, read_only
        )
        assert missing["exists"] is False
        journal = api._mcp_call_tool(
            "journal.open", {"date": "2034-05-06", "create": True}, claims
        )
        assert journal["created"] is True
        assert journal["date"] == "2034-05-06"

        resource = api._mcp_read_resource("stillpoint://page/Notes/Notes.md")
        assert resource["contents"][0]["mimeType"] == "text/markdown"
        assert "Ship MCP support" in resource["contents"][0]["text"]
        assert len(api._mcp_resource_templates()) == 3

        move = api._mcp_call_tool(
            "page.move",
            {"from_path": ":Notes:Child", "to_path": "/Notes/Renamed", "dry_run": True},
            read_only,
        )
        assert move["can_apply"] is True
        assert move["page_map"]["/Notes/Child/Child.md"] == "/Notes/Renamed/Renamed.md"
        assert child.is_file()
        moved = api._mcp_call_tool(
            "page.move",
            {
                "from_path": ":Notes:Child",
                "to_path": "/Notes/Renamed",
                "expected_tree_version": move["tree_version"],
                "dry_run": False,
            },
            claims,
        )
        assert moved["dry_run"] is False
        assert not child.exists()
        assert (vault / "Notes" / "Renamed" / "Renamed.md").is_file()
        assert ":Notes:Renamed" in notes.read_text(encoding="utf-8")
    finally:
        config.set_active_vault(None)
