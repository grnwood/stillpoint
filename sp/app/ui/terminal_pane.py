"""Lazy xterm.js terminal pane for the StillPoint main window."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Callable, Mapping, Optional, Sequence

from PySide6 import QtCore, QtGui, QtWidgets

from sp.app.terminal_session import (
    TerminalSession,
    available_shell_commands,
    command_display,
    create_terminal_session,
    default_shell_command,
)
from .theme import theme_value
from .webengine_env import configure_linux_webengine_env


def safe_terminal_external_url(raw_url: str) -> Optional[QtCore.QUrl]:
    """Return a browser-safe terminal hyperlink, rejecting local/custom schemes."""
    url = QtCore.QUrl(str(raw_url or "").strip())
    if not url.isValid() or url.scheme().casefold() not in {"http", "https"} or not url.host():
        return None
    return url


def terminal_theme_from_palette(palette: QtGui.QPalette) -> dict[str, str]:
    """Build a readable xterm theme from StillPoint's active Qt palette."""
    background = palette.color(QtGui.QPalette.ColorRole.Base)
    foreground = palette.color(QtGui.QPalette.ColorRole.Text)
    selection = palette.color(QtGui.QPalette.ColorRole.Highlight)
    selection_text = palette.color(QtGui.QPalette.ColorRole.HighlightedText)
    dark = background.lightness() < 128

    # Guard against incomplete/custom palettes that provide nearly identical
    # base and text colors.
    if abs(background.lightness() - foreground.lightness()) < 96:
        foreground = QtGui.QColor("#f8fafc" if dark else "#111827")

    ansi = (
        {
            "black": "#4b5563", "red": "#ef4444", "green": "#22c55e",
            "yellow": "#eab308", "blue": "#60a5fa", "magenta": "#d946ef",
            "cyan": "#22d3ee", "white": "#d1d5db", "brightBlack": "#6b7280",
            "brightRed": "#f87171", "brightGreen": "#4ade80",
            "brightYellow": "#facc15", "brightBlue": "#93c5fd",
            "brightMagenta": "#e879f9", "brightCyan": "#67e8f9",
            "brightWhite": "#ffffff",
        }
        if dark
        else {
            "black": "#111827", "red": "#b91c1c", "green": "#047857",
            "yellow": "#a16207", "blue": "#1d4ed8", "magenta": "#a21caf",
            "cyan": "#0e7490", "white": "#4b5563", "brightBlack": "#374151",
            "brightRed": "#dc2626", "brightGreen": "#059669",
            "brightYellow": "#ca8a04", "brightBlue": "#2563eb",
            "brightMagenta": "#c026d3", "brightCyan": "#0891b2",
            "brightWhite": "#111827",
        }
    )
    return {
        "background": background.name(QtGui.QColor.NameFormat.HexRgb),
        "foreground": foreground.name(QtGui.QColor.NameFormat.HexRgb),
        "cursor": foreground.name(QtGui.QColor.NameFormat.HexRgb),
        "cursorAccent": background.name(QtGui.QColor.NameFormat.HexRgb),
        "selectionBackground": selection.name(QtGui.QColor.NameFormat.HexRgb),
        "selectionForeground": selection_text.name(QtGui.QColor.NameFormat.HexRgb),
        **ansi,
    }


def terminal_theme_from_stillpoint() -> dict[str, str]:
    """Resolve colors from StillPoint's stylesheet theme, not the OS palette."""
    palette = QtGui.QPalette()
    palette.setColor(
        QtGui.QPalette.ColorRole.Base,
        QtGui.QColor(str(theme_value("markdown_editor.base.bg", "#0b0b0b"))),
    )
    palette.setColor(
        QtGui.QPalette.ColorRole.Text,
        QtGui.QColor(str(theme_value("markdown_editor.base.text", "#d6f5d6"))),
    )
    palette.setColor(
        QtGui.QPalette.ColorRole.Highlight,
        QtGui.QColor(str(theme_value("markdown_editor.base.selection_bg", "#2f4c74"))),
    )
    palette.setColor(
        QtGui.QPalette.ColorRole.HighlightedText,
        QtGui.QColor(str(theme_value("markdown_editor.base.selection_text", "#ffffff"))),
    )
    return terminal_theme_from_palette(palette)


class TerminalBridge(QtCore.QObject):
    outputData = QtCore.Signal(str)
    clearRequested = QtCore.Signal()
    focusRequested = QtCore.Signal()
    pasteData = QtCore.Signal(str)
    optionsChanged = QtCore.Signal(str)

    frontendReady = QtCore.Signal(int, int)
    inputReceived = QtCore.Signal(str)
    resized = QtCore.Signal(int, int)
    fontSizeAdjustmentRequested = QtCore.Signal(int)
    newTerminalRequested = QtCore.Signal()
    externalUrlRequested = QtCore.Signal(str)

    @QtCore.Slot(int, int)
    def ready(self, columns: int, rows: int) -> None:
        self.frontendReady.emit(max(1, columns), max(1, rows))

    @QtCore.Slot(str)
    def input(self, data: str) -> None:
        self.inputReceived.emit(data)

    @QtCore.Slot(int, int)
    def resize(self, columns: int, rows: int) -> None:
        self.resized.emit(max(1, columns), max(1, rows))

    @QtCore.Slot(str)
    def copyText(self, text: str) -> None:
        if text:
            QtWidgets.QApplication.clipboard().setText(text)

    @QtCore.Slot()
    def requestPaste(self) -> None:
        text = QtWidgets.QApplication.clipboard().text()
        if text:
            self.pasteData.emit(text)

    @QtCore.Slot(int)
    def adjustFontSize(self, delta: int) -> None:
        if delta:
            self.fontSizeAdjustmentRequested.emit(1 if delta > 0 else -1)

    @QtCore.Slot()
    def requestNewTerminal(self) -> None:
        self.newTerminalRequested.emit()

    @QtCore.Slot(str)
    def openExternalUrl(self, url: str) -> None:
        if safe_terminal_external_url(url) is not None:
            self.externalUrlRequested.emit(url)


class TerminalSessionPane(QtWidgets.QWidget):
    """One lazy terminal frontend and PTY session."""

    sessionStarted = QtCore.Signal()
    sessionStopped = QtCore.Signal()
    sessionFailed = QtCore.Signal(str)
    openExternallyRequested = QtCore.Signal()
    closePaneRequested = QtCore.Signal()
    vaultChanged = QtCore.Signal(str)
    backendOutput = QtCore.Signal(int, str)
    backendExited = QtCore.Signal(int, object, str)
    backendError = QtCore.Signal(int, str)
    shellCommandChanged = QtCore.Signal(str, object)
    fontSizeChanged = QtCore.Signal(int)
    sessionCredentialReleased = QtCore.Signal(str)
    sessionExited = QtCore.Signal(object, str)
    newTerminalRequested = QtCore.Signal()

    def __init__(
        self,
        *,
        vault_root_provider: Callable[[], Optional[Path]],
        seed_agents: Callable[[Path], None],
        environment_provider: Optional[Callable[[], Mapping[str, str]]] = None,
        shell_provider: Optional[Callable[[], Sequence[str]]] = None,
        scrollback_provider: Optional[Callable[[], int]] = None,
        settings_provider: Optional[Callable[[], Mapping[str, object]]] = None,
        initial_shell_command: Optional[Sequence[str]] = None,
        show_header: bool = True,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self._vault_root_provider = vault_root_provider
        self._seed_agents = seed_agents
        self._environment_provider = environment_provider or (lambda: {})
        self._shell_provider = shell_provider or default_shell_command
        self._shell_override = (
            [str(value) for value in initial_shell_command]
            if initial_shell_command is not None
            else None
        )
        self._scrollback_provider = scrollback_provider or (lambda: 10_000)
        self._settings_provider = settings_provider or (lambda: {})
        self._session: Optional[TerminalSession] = None
        self._bridge: Optional[TerminalBridge] = None
        self._web_view = None
        self._web_channel = None
        self._web_profile = None
        self._frontend_ready = False
        self._frontend_options: dict = {}
        self._start_requested = False
        self._last_dimensions = (80, 24)
        self._session_generation = 0
        self._session_mcp_token = ""
        self._active_argv: list[str] = []
        self._vault_observer = None
        self._pending_output: list[str] = []
        self._pending_output_size = 0
        self._applying_theme = False
        self._output_timer = QtCore.QTimer(self)
        self._output_timer.setSingleShot(True)
        self._output_timer.setInterval(16)
        self._output_timer.timeout.connect(self._flush_output)
        self._build_ui()
        self._header.setVisible(bool(show_header))
        self.backendOutput.connect(self._deliver_output)
        self.backendExited.connect(self._handle_backend_exit)
        self.backendError.connect(self._handle_backend_error)

    @property
    def initialized(self) -> bool:
        return self._web_view is not None

    @property
    def session_running(self) -> bool:
        return bool(self._session and self._session.running)

    @property
    def command_line(self) -> str:
        if self._active_argv:
            return command_display(self._active_argv)
        try:
            command = list(self._shell_override or self._shell_provider() or default_shell_command())
        except Exception:
            command = default_shell_command()
        return command_display(command)

    @property
    def running_command_line(self) -> str:
        session = self._session
        if session is not None:
            try:
                foreground = session.foreground_command_line()
                if foreground:
                    return foreground
            except Exception:
                pass
        return self.command_line

    def _build_ui(self) -> None:
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QtWidgets.QWidget(self)
        self._header = header
        header.setObjectName("terminalHeader")
        header_layout = QtWidgets.QVBoxLayout(header)
        header_layout.setContentsMargins(8, 3, 4, 3)
        header_layout.setSpacing(3)
        primary_row = QtWidgets.QHBoxLayout()
        primary_row.setSpacing(6)
        self._title = QtWidgets.QLabel("Terminal")
        self._status = QtWidgets.QLabel("Not started")
        self._status.setObjectName("terminalStatus")
        primary_row.addWidget(self._title)
        primary_row.addWidget(self._status)
        primary_row.addStretch(1)
        self._restart_button = QtWidgets.QToolButton()
        self._restart_button.setText("Restart")
        self._restart_button.setToolTip("Restart the embedded terminal session")
        self._restart_button.clicked.connect(self.restart_session)
        self._external_button = QtWidgets.QToolButton()
        self._external_button.setText("Open Externally")
        self._external_button.setToolTip("Open the vault in the system terminal")
        self._external_button.clicked.connect(self.openExternallyRequested)
        self._close_button = QtWidgets.QToolButton()
        self._close_button.setText("×")
        self._close_button.setToolTip("Hide terminal (session keeps running)")
        self._close_button.clicked.connect(self.closePaneRequested)
        primary_row.addWidget(self._restart_button)
        primary_row.addWidget(self._external_button)
        primary_row.addWidget(self._close_button)
        header_layout.addLayout(primary_row)

        controls_row = QtWidgets.QHBoxLayout()
        controls_row.setSpacing(4)
        controls_row.addWidget(QtWidgets.QLabel("Shell:"))
        self._shell_combo = QtWidgets.QComboBox(header)
        self._shell_combo.setSizeAdjustPolicy(QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self._shell_combo.setMinimumContentsLength(8)
        self._shell_combo.setAccessibleName("Terminal shell")
        self._shell_combo.setToolTip("Choose an installed shell; changing it restarts a running session")
        controls_row.addWidget(self._shell_combo, 1)
        self._font_smaller_button = QtWidgets.QToolButton(header)
        self._font_smaller_button.setText("−")
        self._font_smaller_button.setAccessibleName("Decrease terminal font size")
        self._font_smaller_button.setToolTip("Decrease terminal font size")
        self._font_size_label = QtWidgets.QLabel(header)
        self._font_size_label.setMinimumWidth(34)
        self._font_size_label.setAlignment(QtCore.Qt.AlignCenter)
        self._font_larger_button = QtWidgets.QToolButton(header)
        self._font_larger_button.setText("+")
        self._font_larger_button.setAccessibleName("Increase terminal font size")
        self._font_larger_button.setToolTip("Increase terminal font size")
        controls_row.addWidget(self._font_smaller_button)
        controls_row.addWidget(self._font_size_label)
        controls_row.addWidget(self._font_larger_button)
        header_layout.addLayout(controls_row)
        self._font_smaller_button.clicked.connect(lambda: self._adjust_font_size(-1))
        self._font_larger_button.clicked.connect(lambda: self._adjust_font_size(1))
        self._shell_combo.currentIndexChanged.connect(self._on_shell_combo_changed)
        self.refresh_preferences()
        root.addWidget(header)

        self._stack = QtWidgets.QStackedWidget(self)
        placeholder = QtWidgets.QWidget(self._stack)
        placeholder_layout = QtWidgets.QVBoxLayout(placeholder)
        placeholder_layout.addStretch(1)
        self._placeholder_text = QtWidgets.QLabel(
            "The terminal starts only when you ask for it.", placeholder
        )
        self._placeholder_text.setAlignment(QtCore.Qt.AlignCenter)
        self._start_button = QtWidgets.QPushButton("Start Terminal", placeholder)
        self._start_button.setToolTip("Start a shell in the current vault root")
        self._start_button.clicked.connect(self.start_session)
        placeholder_layout.addWidget(self._placeholder_text)
        placeholder_layout.addWidget(self._start_button, 0, QtCore.Qt.AlignHCenter)
        placeholder_layout.addStretch(1)
        self._stack.addWidget(placeholder)
        root.addWidget(self._stack, 1)
        self._placeholder = placeholder
        self._restart_button.setEnabled(False)
        self._apply_qt_theme()

    def _apply_qt_theme(self) -> None:
        if self._applying_theme:
            return
        self._applying_theme = True
        theme = terminal_theme_from_stillpoint()
        background = theme["background"]
        foreground = theme["foreground"]
        try:
            self.setStyleSheet(
                "QWidget#terminalHeader {"
                f" background: {background}; color: {foreground};"
                " border-bottom: 1px solid palette(mid);"
                "}"
                "QWidget#terminalHeader QLabel, QWidget#terminalHeader QToolButton {"
                f" color: {foreground};"
                "}"
            )
            self._placeholder.setStyleSheet(f"background: {background}; color: {foreground};")
            self._placeholder_text.setStyleSheet(f"color: {foreground};")
        finally:
            self._applying_theme = False

    @staticmethod
    def _asset_directory() -> Path:
        return Path(__file__).resolve().parents[2] / "assets" / "terminal"

    def _settings(self) -> dict[str, object]:
        try:
            return dict(self._settings_provider() or {})
        except Exception:
            return {}

    def _effective_font(self) -> tuple[str, int]:
        settings = self._settings()
        fixed_font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.SystemFont.FixedFont)
        family = str(settings.get("font_family") or fixed_font.family() or "monospace")
        try:
            configured_size = int(settings.get("font_size") or 0)
        except (TypeError, ValueError):
            configured_size = 0
        size = configured_size or fixed_font.pointSize() or 10
        return family, max(6, min(72, size))

    def refresh_preferences(self) -> None:
        """Reload shell/font preferences and apply frontend-safe changes live."""
        settings = self._settings()
        if self._shell_override is not None:
            configured_executable = self._shell_override[0] if self._shell_override else ""
            configured_arguments = self._shell_override[1:]
        else:
            configured_executable = str(settings.get("shell_executable") or "").strip()
            configured_arguments = [str(value) for value in settings.get("shell_arguments", [])]
        self._shell_combo.blockSignals(True)
        self._shell_combo.clear()
        self._shell_combo.addItem("Automatic", [])
        selected = 0
        for label, command in available_shell_commands():
            self._shell_combo.addItem(label, command)
            if configured_executable and command == [configured_executable, *configured_arguments]:
                selected = self._shell_combo.count() - 1
        if configured_executable and selected == 0:
            command = [configured_executable, *configured_arguments]
            self._shell_combo.addItem(f"Custom — {configured_executable}", command)
            selected = self._shell_combo.count() - 1
        self._shell_combo.setCurrentIndex(selected)
        self._shell_combo.blockSignals(False)

        family, size = self._effective_font()
        self._font_size_label.setText(f"{size} pt")
        if self._frontend_options:
            self._frontend_options["fontFamily"] = family
            self._frontend_options["fontSize"] = size
            if self._bridge:
                self._bridge.optionsChanged.emit(json.dumps(self._frontend_options))

    def _on_shell_combo_changed(self, index: int) -> None:
        command = self._shell_combo.itemData(index)
        if not isinstance(command, list):
            return
        executable = str(command[0]) if command else ""
        arguments = [str(value) for value in command[1:]] if command else []
        self._shell_override = [executable, *arguments] if executable else None
        self.shellCommandChanged.emit(executable, arguments)

    def _adjust_font_size(self, delta: int) -> None:
        _family, current = self._effective_font()
        if self._frontend_options:
            try:
                current = int(self._frontend_options.get("fontSize", current))
            except (TypeError, ValueError):
                pass
        size = max(6, min(72, current + int(delta)))
        self._font_size_label.setText(f"{size} pt")
        if self._frontend_options:
            self._frontend_options["fontSize"] = size
            if self._bridge:
                self._bridge.optionsChanged.emit(json.dumps(self._frontend_options))
        self.fontSizeChanged.emit(size)

    def _initialize_frontend(self) -> None:
        if self._web_view is not None:
            return
        configure_linux_webengine_env(disable_env_var="SP_DISABLE_TERMINAL_WEBENGINE")
        try:
            from PySide6.QtWebChannel import QWebChannel
            from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
            from PySide6.QtWebEngineWidgets import QWebEngineView
        except Exception as exc:
            raise RuntimeError(f"Qt WebEngine is unavailable: {exc}") from exc

        class LockedTerminalPage(QWebEnginePage):
            def acceptNavigationRequest(page_self, url, nav_type, is_main_frame):  # type: ignore[override]
                if url.isLocalFile() or url.scheme() in {"about", "qrc"}:
                    return True
                return False

            def createWindow(page_self, window_type):  # type: ignore[override]
                return None

        asset_dir = self._asset_directory()
        html_path = asset_dir / "terminal.html"
        if not html_path.is_file():
            raise RuntimeError(f"Bundled terminal asset is missing: {html_path}")
        view = QWebEngineView(self._stack)
        profile = QWebEngineProfile(view)
        profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.NoCache)
        profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies)
        page = LockedTerminalPage(profile, view)
        view.setPage(page)
        page.settings().setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, False)
        page.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
        page.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)

        bridge = TerminalBridge(view)
        bridge.frontendReady.connect(self._on_frontend_ready)
        bridge.inputReceived.connect(self._on_input)
        bridge.resized.connect(self._on_resize)
        bridge.fontSizeAdjustmentRequested.connect(self._adjust_font_size)
        bridge.newTerminalRequested.connect(self.newTerminalRequested)
        bridge.externalUrlRequested.connect(self._open_external_url)
        channel = QWebChannel(page)
        channel.registerObject("terminalBridge", bridge)
        page.setWebChannel(channel)

        self._web_view = view
        self._bridge = bridge
        self._web_channel = channel
        self._web_profile = profile
        self._stack.addWidget(view)
        self._stack.setCurrentWidget(view)
        font_family, font_size = self._effective_font()
        self._frontend_options = {
            "scrollback": max(100, min(200_000, int(self._scrollback_provider()))),
            "fontFamily": font_family,
            "fontSize": font_size,
            "cursorBlink": True,
            "cursorStyle": "block",
            "cursorInactiveStyle": "block",
            "minimumContrastRatio": 7,
            "theme": terminal_theme_from_stillpoint(),
        }
        page.loadFinished.connect(lambda ok: self._on_frontend_loaded(bool(ok)))
        page.renderProcessTerminated.connect(
            lambda _status, _code: self._fail("The terminal WebEngine process stopped unexpectedly.")
        )
        terminal_url = QtCore.QUrl.fromLocalFile(str(html_path))
        query = QtCore.QUrlQuery()
        query.addQueryItem("options", json.dumps(self._frontend_options, separators=(",", ":")))
        terminal_url.setQuery(query)
        view.setUrl(terminal_url)

    def _open_external_url(self, raw_url: str) -> None:
        url = safe_terminal_external_url(raw_url)
        if url is None:
            return
        # Leave the WebChannel callback before handing focus to another
        # application. Opening synchronously from WebEngine can re-enter Qt's
        # focus/style machinery on Linux.
        QtCore.QTimer.singleShot(0, lambda target=QtCore.QUrl(url): QtGui.QDesktopServices.openUrl(target))

    def _on_frontend_loaded(self, ok: bool) -> None:
        if not ok:
            self._fail("The embedded terminal page could not be loaded.")
            return
        if self._bridge:
            self._bridge.optionsChanged.emit(json.dumps(self._frontend_options))
        QtCore.QTimer.singleShot(5000, self._check_frontend_ready)

    def _check_frontend_ready(self) -> None:
        if self._start_requested and not self._frontend_ready:
            self._fail("The terminal frontend did not become ready.")

    @QtCore.Slot(int, int)
    def _on_frontend_ready(self, columns: int, rows: int) -> None:
        self._frontend_ready = True
        self._last_dimensions = (columns, rows)
        # loadFinished can run before JavaScript has connected the WebChannel
        # signals. Send options again now that the frontend has confirmed it is
        # listening.
        self.refresh_theme()
        if self._start_requested:
            self._start_backend()

    def refresh_theme(self) -> None:
        bridge = getattr(self, "_bridge", None)
        if not bridge:
            return
        options = getattr(self, "_frontend_options", {})
        options["theme"] = terminal_theme_from_stillpoint()
        self._frontend_options = options
        bridge.optionsChanged.emit(json.dumps(options))
        self._apply_qt_theme()

    def changeEvent(self, event) -> None:  # type: ignore[override]
        super().changeEvent(event)
        if event.type() in (
            QtCore.QEvent.Type.PaletteChange,
            QtCore.QEvent.Type.ApplicationPaletteChange,
        ):
            self.refresh_theme()

    @QtCore.Slot(str)
    def _on_input(self, data: str) -> None:
        if self._session:
            self._session.write(data)

    @QtCore.Slot(int, int)
    def _on_resize(self, columns: int, rows: int) -> None:
        self._last_dimensions = (columns, rows)
        if self._session:
            self._session.resize(rows, columns)

    @QtCore.Slot()
    def start_session(self) -> None:
        if self.session_running:
            self.focus_terminal()
            return
        self._start_requested = True
        self._status.setText("Starting…")
        self._start_button.setEnabled(False)
        try:
            self._initialize_frontend()
        except Exception as exc:
            self._fail(str(exc))
            return
        if self._frontend_ready:
            self._start_backend()

    def _start_backend(self) -> None:
        if self.session_running:
            return
        root = self._vault_root_provider()
        if root is None:
            self._fail("Open a local vault before starting the terminal.")
            return
        root = Path(root).expanduser().resolve()
        if not root.is_dir():
            self._fail(f"Vault directory is unavailable: {root}")
            return
        try:
            self._seed_agents(root)
            environment = dict(os.environ)
            environment.update({str(k): str(v) for k, v in self._environment_provider().items()})
            self._session_mcp_token = str(environment.get("STILLPOINT_MCP_TOKEN") or "")
            environment["STILLPOINT_VAULT_ROOT"] = str(root)
            argv = list(self._shell_override or self._shell_provider() or default_shell_command())
            self._active_argv = list(argv)
            columns, rows = self._last_dimensions
            self._session_generation += 1
            generation = self._session_generation
            session = create_terminal_session(
                on_output=lambda data: self.backendOutput.emit(generation, data),
                on_exit=lambda code, reason: self.backendExited.emit(generation, code, reason),
                on_error=lambda message: self.backendError.emit(generation, message),
            )
            session.start(
                cwd=root,
                argv=argv,
                environment=environment,
                rows=rows,
                columns=columns,
            )
            self._session = session
            self._start_vault_observer(root)
            self._start_requested = False
            self._status.setText(command_display(argv))
            self._restart_button.setEnabled(True)
            self.sessionStarted.emit()
            self.focus_terminal()
        except Exception as exc:
            self._fail(str(exc))

    @QtCore.Slot(int, str)
    def _deliver_output(self, generation: int, data: str) -> None:
        if generation != self._session_generation:
            return
        self._pending_output.append(data)
        self._pending_output_size += len(data)
        while self._pending_output_size > 2_000_000 and self._pending_output:
            removed = self._pending_output.pop(0)
            self._pending_output_size -= len(removed)
        if not self._output_timer.isActive():
            self._output_timer.start()

    def _flush_output(self) -> None:
        if not self._pending_output:
            return
        data = "".join(self._pending_output)
        self._pending_output.clear()
        self._pending_output_size = 0
        if self._bridge:
            self._bridge.outputData.emit(data)

    @QtCore.Slot(int, object, str)
    def _handle_backend_exit(self, generation: int, exit_code, reason: str) -> None:
        if generation != self._session_generation:
            return
        self._session = None
        self._release_session_credential()
        self._restart_button.setEnabled(True)
        code_text = "unknown" if exit_code is None else str(exit_code)
        self._status.setText(f"Exited ({code_text})")
        if self._bridge:
            self._bridge.outputData.emit(f"\r\n[StillPoint terminal {reason}; exit {code_text}]\r\n")
        self.sessionStopped.emit()
        self.sessionExited.emit(exit_code, reason)

    @QtCore.Slot(int, str)
    def _handle_backend_error(self, generation: int, message: str) -> None:
        if generation != self._session_generation:
            return
        if self._bridge:
            self._bridge.outputData.emit(f"\r\n[Terminal error: {message}]\r\n")
        self._status.setText("Terminal error")

    def _fail(self, message: str) -> None:
        self._release_session_credential()
        self._start_requested = False
        self._status.setText("Unavailable")
        self._placeholder_text.setText(f"Could not start the embedded terminal.\n{message}")
        self._start_button.setText("Try Again")
        self._start_button.setEnabled(True)
        self._stack.setCurrentWidget(self._placeholder)
        self.sessionFailed.emit(message)

    @QtCore.Slot()
    def restart_session(self) -> None:
        self.stop_session()
        if self._bridge:
            self._bridge.clearRequested.emit()
        QtCore.QTimer.singleShot(50, self.start_session)

    def stop_session(self) -> None:
        session, self._session = self._session, None
        self._session_generation += 1
        self._stop_vault_observer()
        if session:
            session.terminate()
            self._release_session_credential()
            self.sessionStopped.emit()
        self._restart_button.setEnabled(self.initialized)
        self._status.setText("Not started")

    def _release_session_credential(self) -> None:
        token, self._session_mcp_token = self._session_mcp_token, ""
        if token:
            self.sessionCredentialReleased.emit(token)

    def _start_vault_observer(self, root: Path) -> None:
        self._stop_vault_observer()
        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer
        except Exception:
            return
        pane = self

        class VaultEventHandler(FileSystemEventHandler):
            def on_any_event(self, event) -> None:  # type: ignore[override]
                if getattr(event, "event_type", "") == "opened":
                    return
                path = str(getattr(event, "dest_path", "") or getattr(event, "src_path", "") or "")
                if not path:
                    return
                candidate = Path(path)
                try:
                    relative = candidate.resolve().relative_to(root.resolve())
                except Exception:
                    return
                if ".stillpoint" in relative.parts or candidate.name == "AGENTS.md":
                    return
                if not getattr(event, "is_directory", False) and candidate.suffix.lower() not in {".md", ".txt"}:
                    return
                pane.vaultChanged.emit(path)

        try:
            observer = Observer()
            observer.schedule(VaultEventHandler(), str(root), recursive=True)
            observer.daemon = True
            observer.start()
            self._vault_observer = observer
        except Exception:
            self._vault_observer = None

    def _stop_vault_observer(self) -> None:
        observer, self._vault_observer = self._vault_observer, None
        if observer is None:
            return
        try:
            observer.stop()
            observer.join(timeout=0.75)
        except Exception:
            pass

    def focus_terminal(self) -> None:
        if self._web_view is not None:
            self._web_view.setFocus(QtCore.Qt.ShortcutFocusReason)
            if self._bridge:
                self._bridge.focusRequested.emit()

    def prepare_for_vault(self) -> None:
        """Stop the old vault session and return to a lazy start surface."""
        self.stop_session()
        self._start_requested = False
        self._status.setText("Not started")
        self._placeholder_text.setText("The terminal starts only when you ask for it.")
        self._start_button.setText("Start Terminal")
        self._start_button.setEnabled(True)
        self._stack.setCurrentWidget(self._placeholder)
        if self._bridge:
            self._bridge.clearRequested.emit()
        self._pending_output.clear()
        self._pending_output_size = 0

    def shutdown(self) -> None:
        self.stop_session()
        if self._web_view is not None:
            try:
                self._web_view.page().setWebChannel(None)
            except Exception:
                pass


class TerminalPane(QtWidgets.QWidget):
    """VS Code-style multi-terminal workspace shown as one right-panel tab."""

    sessionStarted = QtCore.Signal()
    sessionStopped = QtCore.Signal()
    sessionFailed = QtCore.Signal(str)
    openExternallyRequested = QtCore.Signal()
    closePaneRequested = QtCore.Signal()
    vaultChanged = QtCore.Signal(str)
    shellCommandChanged = QtCore.Signal(str, object)
    fontSizeChanged = QtCore.Signal(int)
    activeTitleChanged = QtCore.Signal(str)
    sessionCredentialReleased = QtCore.Signal(str)

    def __init__(
        self,
        *,
        vault_root_provider: Callable[[], Optional[Path]],
        seed_agents: Callable[[Path], None],
        environment_provider: Optional[Callable[[], Mapping[str, str]]] = None,
        shell_provider: Optional[Callable[[], Sequence[str]]] = None,
        scrollback_provider: Optional[Callable[[], int]] = None,
        settings_provider: Optional[Callable[[], Mapping[str, object]]] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._vault_root_provider = vault_root_provider
        self._seed_agents = seed_agents
        self._environment_provider = environment_provider
        self._shell_provider = shell_provider or default_shell_command
        self._scrollback_provider = scrollback_provider
        self._settings_provider = settings_provider or (lambda: {})
        self._sessions: list[TerminalSessionPane] = []
        self._session_titles: list[str] = []
        self._counter = 0
        self._terminal_switch_index = -1
        self._terminal_switch_timer = QtCore.QTimer(self)
        self._terminal_switch_timer.setInterval(25)
        self._terminal_switch_timer.timeout.connect(self._commit_terminal_switcher_on_control_release)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        selector_bar = QtWidgets.QWidget(self)
        selector_bar.setObjectName("terminalSelectorBar")
        selector_layout = QtWidgets.QHBoxLayout(selector_bar)
        selector_layout.setContentsMargins(6, 3, 4, 3)
        selector_layout.setSpacing(4)
        self._terminal_selector = QtWidgets.QToolButton(selector_bar)
        self._terminal_selector.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        self._terminal_selector.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
        self._terminal_selector.setAccessibleName("Active terminal and terminal actions")
        self._terminal_menu = QtWidgets.QMenu(self._terminal_selector)
        self._terminal_menu.aboutToShow.connect(self._rebuild_terminal_menu)
        self._terminal_selector.setMenu(self._terminal_menu)
        selector_layout.addWidget(self._terminal_selector, 1)
        self._font_smaller_button = QtWidgets.QToolButton(selector_bar)
        self._font_smaller_button.setText("−")
        self._font_smaller_button.setToolTip("Decrease terminal font size (Ctrl+-)")
        self._font_smaller_button.setAccessibleName("Decrease terminal font size")
        self._font_larger_button = QtWidgets.QToolButton(selector_bar)
        self._font_larger_button.setText("+")
        self._font_larger_button.setToolTip("Increase terminal font size (Ctrl+Shift++)")
        self._font_larger_button.setAccessibleName("Increase terminal font size")
        self._restart_button = QtWidgets.QToolButton(selector_bar)
        self._restart_button.setText("Restart")
        self._restart_button.setToolTip("Restart the active terminal")
        self._external_button = QtWidgets.QToolButton(selector_bar)
        self._external_button.setText("Open Externally")
        self._external_button.setToolTip("Open the vault in the system terminal")
        self._close_button = QtWidgets.QToolButton(selector_bar)
        self._close_button.setText("×")
        self._close_button.setToolTip("Hide terminal (sessions keep running)")
        selector_layout.addWidget(self._font_smaller_button)
        selector_layout.addWidget(self._font_larger_button)
        selector_layout.addWidget(self._restart_button)
        selector_layout.addWidget(self._external_button)
        selector_layout.addWidget(self._close_button)
        self._font_smaller_button.clicked.connect(lambda: self.active_session._adjust_font_size(-1))
        self._font_larger_button.clicked.connect(lambda: self.active_session._adjust_font_size(1))
        self._restart_button.clicked.connect(self.restart_session)
        self._external_button.clicked.connect(self.openExternallyRequested)
        self._close_button.clicked.connect(self.closePaneRequested)
        layout.addWidget(selector_bar)

        self._session_stack = QtWidgets.QStackedWidget(self)
        layout.addWidget(self._session_stack, 1)
        first = self._create_session()
        self._session_stack.setCurrentWidget(first)
        self._sync_active_title()
        self._build_terminal_switcher()

    @property
    def active_session(self) -> TerminalSessionPane:
        widget = self._session_stack.currentWidget()
        if isinstance(widget, TerminalSessionPane):
            return widget
        return self._sessions[0]

    @property
    def initialized(self) -> bool:
        return self.active_session.initialized

    @property
    def session_running(self) -> bool:
        return self.active_session.session_running

    @property
    def terminal_count(self) -> int:
        return len(self._sessions)

    @property
    def active_title(self) -> str:
        try:
            return self._session_titles[self._sessions.index(self.active_session)]
        except (ValueError, IndexError):
            return "Terminal"

    @property
    def terminal_switcher_active(self) -> bool:
        switcher = getattr(self, "_terminal_switcher", None)
        return bool(switcher is not None and not switcher.isHidden())

    def _build_terminal_switcher(self) -> None:
        overlay = QtWidgets.QWidget(self)
        overlay.setObjectName("terminalSwitcherOverlay")
        overlay.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        overlay.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        overlay.setStyleSheet(
            "QWidget#terminalSwitcherOverlay { background: "
            f"{theme_value('main_window.picker_popup.bg', 'rgba(32,32,32,245)')}; "
            "border: 1px solid "
            f"{theme_value('main_window.picker_popup.border', '#666666')}; "
            "border-radius: 6px; }"
            "QLabel { border: none; font-weight: bold; }"
            "QListWidget { background: transparent; color: "
            f"{theme_value('main_window.picker_popup.list_text', '#f5f5f5')}; border: none; }}"
            "QListWidget::item { padding: 6px 8px; }"
            "QListWidget::item:selected { background: "
            f"{theme_value('main_window.picker_popup.list_selected_bg', 'rgba(90,161,255,80)')}; }}"
        )
        overlay_layout = QtWidgets.QVBoxLayout(overlay)
        overlay_layout.setContentsMargins(12, 8, 12, 8)
        overlay_layout.addWidget(QtWidgets.QLabel("Open terminals", overlay))
        self._terminal_switch_list = QtWidgets.QListWidget(overlay)
        overlay_layout.addWidget(self._terminal_switch_list, 1)
        overlay.hide()
        self._terminal_switcher = overlay

    def _position_terminal_switcher(self) -> None:
        count = max(1, len(self._sessions))
        available_width = max(180, self.width() - 24)
        width = min(max(220, int(self.width() * 0.85)), available_width)
        available_height = max(120, self.height() - 40)
        height = min(max(150, 52 + count * 48), available_height)
        self._terminal_switcher.resize(width, height)
        self._terminal_switcher.move(
            max(0, (self.width() - width) // 2),
            max(0, (self.height() - height) // 2),
        )

    def _refresh_terminal_switcher_items(self) -> None:
        self._terminal_switch_list.clear()
        for session, title in zip(self._sessions, self._session_titles):
            state = "Running" if session.session_running else "Not running"
            self._terminal_switch_list.addItem(
                QtWidgets.QListWidgetItem(
                    f"{title}\n{state} · {session.running_command_line}"
                )
            )
        if 0 <= self._terminal_switch_index < self._terminal_switch_list.count():
            self._terminal_switch_list.setCurrentRow(self._terminal_switch_index)

    def cycle_terminal_switcher(self, *, reverse: bool = False) -> None:
        if not self._sessions:
            return
        if not self.terminal_switcher_active:
            try:
                active_index = self._sessions.index(self.active_session)
            except ValueError:
                active_index = 0
            delta = -1 if reverse else 1
            self._terminal_switch_index = (active_index + delta) % len(self._sessions)
            self._refresh_terminal_switcher_items()
            self._position_terminal_switcher()
            self._session_stack.hide()
            self._terminal_switcher.show()
            self._terminal_switcher.raise_()
            self.setFocus(QtCore.Qt.ShortcutFocusReason)
        else:
            delta = -1 if reverse else 1
            self._terminal_switch_index = (
                self._terminal_switch_index + delta
            ) % len(self._sessions)
            self._refresh_terminal_switcher_items()
        self._terminal_switch_timer.start()

    def _commit_terminal_switcher_on_control_release(self) -> None:
        if QtGui.QGuiApplication.queryKeyboardModifiers() & QtCore.Qt.ControlModifier:
            return
        self._commit_terminal_switcher()

    def _commit_terminal_switcher(self) -> None:
        self._terminal_switch_timer.stop()
        index = self._terminal_switch_index
        self._terminal_switch_index = -1
        self._terminal_switcher.hide()
        self._session_stack.show()
        if 0 <= index < len(self._sessions):
            self.set_active_terminal(index)
        else:
            self.focus_terminal()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        if self.terminal_switcher_active:
            self._position_terminal_switcher()

    @staticmethod
    def _command_name(command: Sequence[str]) -> str:
        if not command:
            return "terminal"
        return Path(str(command[0])).name or str(command[0])

    def _create_session(self, command: Optional[Sequence[str]] = None) -> TerminalSessionPane:
        override = [str(value) for value in command] if command is not None else None
        effective = override or list(self._shell_provider() or default_shell_command())
        self._counter += 1
        child = TerminalSessionPane(
            vault_root_provider=self._vault_root_provider,
            seed_agents=self._seed_agents,
            environment_provider=self._environment_provider,
            shell_provider=self._shell_provider,
            scrollback_provider=self._scrollback_provider,
            settings_provider=self._settings_provider,
            initial_shell_command=override,
            show_header=False,
            parent=self._session_stack,
        )
        title = f"{self._counter}: {self._command_name(effective)}"
        self._sessions.append(child)
        self._session_titles.append(title)
        self._session_stack.addWidget(child)
        child.sessionStarted.connect(self.sessionStarted)
        child.sessionStopped.connect(self._on_child_stopped)
        child.sessionExited.connect(
            lambda _code, _reason, pane=child: self._on_child_exited(pane)
        )
        child.sessionFailed.connect(self.sessionFailed)
        child.openExternallyRequested.connect(self.openExternallyRequested)
        child.closePaneRequested.connect(self.closePaneRequested)
        child.vaultChanged.connect(self.vaultChanged)
        child.fontSizeChanged.connect(self.fontSizeChanged)
        child.sessionCredentialReleased.connect(self.sessionCredentialReleased)
        child.newTerminalRequested.connect(lambda: self.new_terminal())
        child.shellCommandChanged.connect(
            lambda executable, arguments, pane=child: self._on_child_shell_changed(
                pane, executable, arguments
            )
        )
        return child

    def _on_child_stopped(self) -> None:
        if not any(session.session_running for session in self._sessions):
            self.sessionStopped.emit()

    def _on_child_exited(self, child: TerminalSessionPane) -> None:
        """Remove a naturally exited shell and keep an active live terminal visible."""
        try:
            old_index = self._sessions.index(child)
        except ValueError:
            return
        was_active = child is self.active_session
        ordered_others = self._sessions[old_index + 1 :] + self._sessions[:old_index]
        next_session = next(
            (session for session in ordered_others if session.session_running),
            ordered_others[0] if ordered_others else None,
        )

        self._session_stack.removeWidget(child)
        self._sessions.pop(old_index)
        self._session_titles.pop(old_index)
        child.deleteLater()

        if not self._sessions:
            next_session = self._create_session()
        if was_active and next_session is not None:
            self.set_active_terminal(self._sessions.index(next_session))
        elif not was_active:
            self._sync_active_title()

    def _on_child_shell_changed(
        self, child: TerminalSessionPane, executable: str, arguments: object
    ) -> None:
        try:
            index = self._sessions.index(child)
        except ValueError:
            return
        command = [str(executable), *[str(value) for value in arguments]] if executable else default_shell_command()
        number = self._session_titles[index].partition(":")[0]
        self._session_titles[index] = f"{number}: {self._command_name(command)}"
        if child is self.active_session:
            self._sync_active_title()
        self.shellCommandChanged.emit(executable, arguments)

    def _sync_active_title(self) -> None:
        title = self.active_title
        self._terminal_selector.setText(f"{title} ▾")
        self.activeTitleChanged.emit(title)

    def _rebuild_terminal_menu(self) -> None:
        self._terminal_menu.clear()
        active = self.active_session
        for index, (session, title) in enumerate(zip(self._sessions, self._session_titles)):
            action = self._terminal_menu.addAction(title)
            action.setCheckable(True)
            action.setChecked(session is active)
            action.triggered.connect(lambda _checked=False, value=index: self.set_active_terminal(value))
        self._terminal_menu.addSeparator()
        new_menu = self._terminal_menu.addMenu("New Terminal")
        automatic = new_menu.addAction("Automatic (configured default)")
        automatic.triggered.connect(lambda: self.new_terminal())
        for label, command in available_shell_commands():
            action = new_menu.addAction(label)
            action.triggered.connect(
                lambda _checked=False, values=list(command): self.new_terminal(values)
            )
        close_action = self._terminal_menu.addAction("Close Active Terminal")
        close_action.triggered.connect(self.close_active_terminal)

    def set_active_terminal(self, index: int) -> None:
        if not 0 <= index < len(self._sessions):
            return
        self._session_stack.setCurrentWidget(self._sessions[index])
        self._sync_active_title()
        self.focus_terminal()

    def cycle_active_terminal(self, *, reverse: bool = False) -> None:
        if len(self._sessions) < 2:
            self.focus_terminal()
            return
        try:
            index = self._sessions.index(self.active_session)
        except ValueError:
            index = 0
        delta = -1 if reverse else 1
        self.set_active_terminal((index + delta) % len(self._sessions))

    def has_terminal_focus(self) -> bool:
        if self.terminal_switcher_active:
            return True
        focused = QtWidgets.QApplication.focusWidget()
        return bool(focused is not None and (focused is self or self.isAncestorOf(focused)))

    def new_terminal(self, command: Optional[Sequence[str]] = None) -> None:
        child = self._create_session(command)
        self._session_stack.setCurrentWidget(child)
        self._sync_active_title()
        child.start_session()

    def close_active_terminal(self) -> None:
        child = self.active_session
        try:
            index = self._sessions.index(child)
        except ValueError:
            return
        child.shutdown()
        self._session_stack.removeWidget(child)
        self._sessions.pop(index)
        self._session_titles.pop(index)
        child.deleteLater()
        if not self._sessions:
            self._create_session()
        self.set_active_terminal(min(index, len(self._sessions) - 1))

    def start_session(self) -> None:
        self.active_session.start_session()

    def restart_session(self) -> None:
        self.active_session.restart_session()

    def stop_session(self) -> None:
        self.active_session.stop_session()

    def focus_terminal(self) -> None:
        self.active_session.focus_terminal()

    def refresh_preferences(self) -> None:
        for session in self._sessions:
            session.refresh_preferences()

    def prepare_for_vault(self) -> None:
        if self.terminal_switcher_active:
            self._commit_terminal_switcher()
        for session in self._sessions:
            session.prepare_for_vault()
        while len(self._sessions) > 1:
            child = self._sessions.pop()
            self._session_titles.pop()
            self._session_stack.removeWidget(child)
            child.deleteLater()
        self._session_stack.setCurrentWidget(self._sessions[0])
        self._sync_active_title()

    def shutdown(self) -> None:
        self._terminal_switch_timer.stop()
        for session in list(self._sessions):
            session.shutdown()
