from __future__ import annotations

import httpx
import os
import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Signal, QTimer
from PySide6.QtWidgets import QApplication, QTabWidget, QWidget, QMenu, QVBoxLayout, QLabel, QPushButton
from PySide6.QtCore import Qt
from sp.app import config
from sp.logging_flags import log_enabled

from .ai_chat_panel import AIChatPanel
from .task_panel import TaskPanel
from .attachments_panel import AttachmentsPanel
from .link_navigator_panel import LinkNavigatorPanel
from .calendar_panel import CalendarPanel
from .map_panel import MapPanel
from .page_load_logger import PAGE_LOGGING_ENABLED
from .theme import apply_menu_theme


class TabbedRightPanel(QWidget):
    """Tabbed panel containing Tasks, Calendar, Attachments, and Link views."""
    
    # Forward signals from child panels
    taskActivated = Signal(str, int)  # path, line (from TaskPanel)
    dateActivated = Signal(int, int, int)  # year, month, day (from Calendar tab)
    linkActivated = Signal(str, bool)  # page path and whether Link Navigator keeps focus
    calendarPageActivated = Signal(str)  # page path from Calendar tab
    calendarTaskActivated = Signal(str, int)  # path, line from Calendar tab task list
    mapHeadingActivated = Signal(str, int)  # path, line from Map tab
    mapHeadingCreateRequested = Signal(str, int, int, str)  # path, after_line, level, text
    mapHeadingRenameRequested = Signal(str, int, int, str)  # path, line, level, text
    mapHeadingReorderRequested = Signal(str, str, str, int)  # path, base_text, new_text, focus_line
    mapStatusRequested = Signal(str, int)  # status text, timeout
    aiChatNavigateRequested = Signal(str)  # page path from AI Chat tab
    aiChatResponseCopied = Signal(str)  # status text when chat response copied
    aiOverlayRequested = Signal(str, object)  # text, anchor QPoint
    aiChatPageWritten = Signal(str)  # page path written by AI chat tools
    openInWindowRequested = Signal(str)  # page path to open in single-page editor
    openTaskWindowRequested = Signal()
    openLinkWindowRequested = Signal()
    openAiWindowRequested = Signal()
    openCalendarWindowRequested = Signal()
    openMapWindowRequested = Signal()
    openTerminalWindowRequested = Signal()
    terminalTabActivated = Signal()
    filterClearRequested = Signal()
    taskDatesWillApply = Signal(list)  # affected page paths
    taskDatesApplied = Signal(list)  # affected page paths
    taskStatusRequested = Signal(str, int)
    remoteRequestObserved = Signal(str, float, str)  # state, latency_ms, message
    pageAboutToBeDeleted = Signal(str)  # page about to be deleted (for editor unload)
    pageDeleted = Signal(str)  # page path deleted from calendar panel
    linkBackRequested = Signal()
    linkForwardRequested = Signal()
    linkHomeRequested = Signal()
    
    def __init__(
        self,
        parent=None,
        enable_tasks: bool = True,
        enable_calendar: bool = True,
        enable_link_navigator: bool = True,
        enable_map: bool = True,
        enable_ai_chats: bool = False,
        ai_chat_font_size: int = 13,
        http_client: Optional[httpx.Client] = None,
        auth_prompt=None,
    ) -> None:
        super().__init__(parent)
        
        # Create tab widget
        self.tabs = QTabWidget()
        self.terminal_panel = None
        self.terminal_index = None
        self.ai_chat_panel = None
        self.ai_chat_index = None
        self._ai_chat_font_size = self._clamp_ai_font(ai_chat_font_size)
        self._http_client = http_client
        self._remote_mode = False
        self._current_page_path: Optional[Path] = None
        self._current_relative_path: Optional[str] = None
        self._pending_task_refresh: bool = False
        self._pending_calendar_path: Optional[str] = None
        self._pending_calendar_date: Optional[tuple[int, int, int]] = None
        self._pending_calendar_vault_root: Optional[str] = None
        self._pending_calendar_refresh: bool = False
        self._pending_attachments_refresh: bool = False
        self._pending_link_refresh: bool = False
        self._pending_link_page: Optional[str] = None
        self._pending_ai_page: Optional[str] = None
        self._pending_map_refresh: bool = False
        self._vault_accent_color: Optional[str] = None
        self._page_text_provider = None
        self._calendar_sync_timer = QTimer(self)
        self._calendar_sync_timer.setSingleShot(True)
        self._calendar_sync_timer.setInterval(75)
        self._calendar_sync_timer.timeout.connect(self._sync_calendar_tab_state)
        
        # Create Tasks tab
        self.task_panel = None
        # Create Calendar tab
        self.calendar_panel = None
        if enable_tasks:
            self._add_task_tab()

        if enable_calendar:
            self._add_calendar_tab()
        
        # Create Attachments tab
        self.attachments_panel = AttachmentsPanel(api_client=http_client, auth_prompt=auth_prompt)
        self.tabs.addTab(self.attachments_panel, "Attachments")

        # Create Link Navigator tab
        self.link_panel = None
        if enable_link_navigator:
            self._add_link_tab()

        self.map_panel = None
        if enable_map:
            self._add_map_tab()

        # Create AI Chat tab if enabled
        if enable_ai_chats:
            self._add_ai_chat_tab()
        
        # Set first tab as default
        if self.tabs.count():
            self.tabs.setCurrentIndex(0)
        self.tabs.currentChanged.connect(self._focus_current_tab)
        self.tabs.tabBar().setContextMenuPolicy(Qt.CustomContextMenu)
        self.tabs.tabBar().customContextMenuRequested.connect(self._open_tab_context_menu)
        
        # Forward signals
        # Layout
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.tabs)
        self.setLayout(layout)
        self._focus_current_tab()

    def set_http_client(
        self,
        http_client: Optional[httpx.Client],
        api_base: Optional[str],
        remote_mode: bool,
        auth_prompt=None,
    ) -> None:
        self._http_client = http_client
        self._remote_mode = bool(remote_mode)
        if self.calendar_panel:
            self.calendar_panel.http = http_client
            if api_base:
                self.calendar_panel.api_base = api_base
            self.calendar_panel.set_remote_mode(bool(remote_mode))
        self.attachments_panel.set_http_client(http_client)
        self.attachments_panel.set_remote_mode(remote_mode, api_base)
        if auth_prompt is not None:
            self.attachments_panel.set_auth_prompt(auth_prompt)
        if self.task_panel:
            self.task_panel.set_http_client(http_client)
            self.task_panel.set_remote_mode(bool(remote_mode))
        if self.ai_chat_panel:
            self.ai_chat_panel.set_api_client(http_client)
    
    def refresh_tasks(self) -> None:
        """Refresh the task panel."""
        if self.task_panel:
            if self._is_panel_currently_visible(self.task_panel):
                self.task_panel.refresh()
                self._pending_task_refresh = False
            else:
                self._pending_task_refresh = True
    
    def clear_tasks(self) -> None:
        """Clear the task panel."""
        if self.task_panel:
            self.task_panel.clear()
    
    def set_vault_root(self, vault_root: Optional[str]) -> None:
        """Set vault root for calendar in task panel."""
        if vault_root:
            if self.task_panel:
                self.task_panel.set_vault_root(vault_root)
            if self.calendar_panel:
                if self._is_calendar_tab_active():
                    self.calendar_panel.set_vault_root(vault_root)
                else:
                    self._pending_calendar_vault_root = vault_root
        self.attachments_panel.set_vault_root(vault_root)
        if self.link_panel:
            try:
                self.link_panel.reload_mode_from_config()
                self.link_panel.reload_layout_from_config()
            except Exception:
                pass
        if self.ai_chat_panel:
            self.ai_chat_panel.set_vault_root(vault_root)
        if self._is_calendar_tab_active():
            self._sync_calendar_tab_state()


    def refresh_calendar(self) -> None:
        """Refresh the calendar to update bold dates."""
        if not self.calendar_panel:
            return
        if self._is_calendar_tab_active():
            self.calendar_panel.refresh()
        else:
            self._pending_calendar_refresh = True

    def notify_right_panel_resized(self) -> None:
        """Let the calendar tab recompute layout when the right panel resizes."""
        if self.calendar_panel and self._is_calendar_tab_active():
            try:
                self.calendar_panel.update_calendar_layout()
            except Exception:
                pass
        if self.map_panel and self._is_panel_currently_visible(self.map_panel):
            try:
                self.map_panel.fit_map()
            except Exception:
                pass
    
    def set_calendar_date(self, year: int, month: int, day: int) -> None:
        """Set the calendar to show a specific date."""
        if not self.calendar_panel:
            return
        if self._is_calendar_tab_active():
            self.calendar_panel.set_calendar_date(year, month, day)
        else:
            self._pending_calendar_date = (year, month, day)
    
    def set_current_page(self, page_path, relative_path=None, *, sync_calendar: bool = True) -> bool:
        """Update panels with the current page."""
        self._current_page_path = page_path
        self._current_relative_path = relative_path
        if self._is_panel_currently_visible(self.attachments_panel):
            self.attachments_panel.set_page(page_path)
            self._pending_attachments_refresh = False
        else:
            self._pending_attachments_refresh = True
        if self.link_panel:
            self._pending_link_page = relative_path
            if self._is_link_panel_active():
                self.link_panel.set_page(relative_path)
                self._pending_link_refresh = False
            else:
                self._pending_link_refresh = True
        try:
            if sync_calendar and self.calendar_panel and relative_path:
                if self._is_calendar_tab_active():
                    self.calendar_panel.set_current_page(relative_path)
                else:
                    self._pending_calendar_path = relative_path
        except Exception:
            pass
        if self.ai_chat_panel:
            if self._is_panel_currently_visible(self.ai_chat_panel):
                self.ai_chat_panel.set_current_page(relative_path)
                self._pending_ai_page = None
            else:
                self._pending_ai_page = relative_path
        if self.map_panel:
            if relative_path and self._is_panel_currently_visible(self.map_panel):
                self._sync_map_tab_state()
                self._pending_map_refresh = False
            else:
                self._pending_map_refresh = True
        self._update_attachments_tab_label()
        if self.ai_chat_panel and hasattr(self.ai_chat_panel, "has_chat_for_path"):
            return self.ai_chat_panel.has_chat_for_path(relative_path)
        return False

    def set_page_text_provider(self, provider) -> None:
        """Provide calendar panel with live editor text for AI summaries."""
        self._page_text_provider = provider
        try:
            if self.calendar_panel:
                self.calendar_panel.set_page_text_provider(provider)
        except Exception:
            pass

    def refresh_map(self, page_path=None) -> None:
        """Refresh the map tab from the live editor text provider."""
        if not self.map_panel:
            return
        if page_path is not None:
            self._current_relative_path = page_path
        if self._is_panel_currently_visible(self.map_panel):
            self._sync_map_tab_state()
            self._pending_map_refresh = False
        else:
            self._pending_map_refresh = True

    def defer_map_refresh(self, page_path=None) -> None:
        """Mark the map tab dirty without refreshing immediately."""
        if not self.map_panel:
            return
        if page_path is not None:
            self._current_relative_path = page_path
        self._pending_map_refresh = True

    def set_calendar_font_size(self, size: int) -> None:
        """Match calendar/journal/insights fonts to the editor."""
        try:
            if self.calendar_panel:
                self.calendar_panel.set_base_font_size(size)
        except Exception:
            pass

    def set_font_size(self, size: int) -> None:
        """Propagate font size changes to AI chat."""
        if self.ai_chat_panel:
            self.ai_chat_panel.set_font_size(size)
            self._ai_chat_font_size = self.ai_chat_panel.get_font_size()
        else:
            self._ai_chat_font_size = self._clamp_ai_font(size)
        try:
            config.save_ai_chat_font_size(self._ai_chat_font_size)
        except Exception:
            pass

    def set_vault_accent_color(self, color_hex: Optional[str]) -> None:
        self._vault_accent_color = (color_hex or "").strip() or None
        if self.ai_chat_panel:
            self.ai_chat_panel.set_vault_accent_color(self._vault_accent_color)
        if self.task_panel:
            self.task_panel.set_vault_accent_color(self._vault_accent_color)
        if self.calendar_panel:
            self.calendar_panel.set_vault_accent_color(self._vault_accent_color)
        if self.link_panel:
            self.link_panel.set_vault_accent_color(self._vault_accent_color)

    def apply_theme(self) -> None:
        """Refresh theme-sensitive child panels after the effective theme changes."""
        for panel in (
            self.ai_chat_panel,
            self.task_panel,
            self.calendar_panel,
            self.link_panel,
            self.map_panel,
            self.attachments_panel,
        ):
            if panel is None:
                continue
            for method_name in ("apply_theme", "apply_theme_style", "apply_theme_palette"):
                method = getattr(panel, method_name, None)
                if not callable(method):
                    continue
                try:
                    if method_name == "apply_theme_palette":
                        app = QApplication.instance()
                        if app is None:
                            break
                        method(app.palette())
                    else:
                        method()
                except Exception:
                    pass
                break
        try:
            self.tabs.update()
        except Exception:
            pass

    def get_ai_font_size(self) -> int:
        """Return current AI chat font size."""
        if self.ai_chat_panel:
            return self.ai_chat_panel.get_font_size()
        return self._ai_chat_font_size

    @staticmethod
    def _clamp_ai_font(size: int) -> int:
        return max(6, min(24, size))
    
    def refresh_attachments(self) -> None:
        """Refresh the attachments panel."""
        if self._is_panel_currently_visible(self.attachments_panel):
            self.attachments_panel.refresh()
            self._pending_attachments_refresh = False
            self._update_attachments_tab_label()
        else:
            self._pending_attachments_refresh = True

    def refresh_links(self, page_path=None) -> None:
        """Refresh the link navigator for the given page (or current)."""
        if not self.link_panel:
            return
        if page_path is not None:
            self._pending_link_page = page_path
        try:
            win = self.window()
            if getattr(win, "_mode_window_pending", False) or getattr(win, "_mode_window", None):
                QTimer.singleShot(100, lambda p=page_path: self.refresh_links(p))
                return
        except Exception:
            pass
        if self._is_link_panel_active():
            self.link_panel.refresh(page_path if page_path is not None else self._current_relative_path)
            self._pending_link_refresh = False
        else:
            self._pending_link_refresh = True

    def focus_link_tab(self, page_path=None) -> None:
        """Switch to the Link Navigator tab and optionally set its page."""
        if not self.link_panel:
            return
        target_page = page_path if page_path is not None else self._current_relative_path
        if page_path is not None:
            self.link_panel.set_page(page_path)
        for i in range(self.tabs.count()):
            if self.tabs.widget(i) == self.link_panel:
                self.tabs.setCurrentIndex(i)
                self.link_panel.refresh(target_page)
                self._pending_link_refresh = False
                self._pending_link_page = target_page
                QTimer.singleShot(0, self._focus_link_graph)
                break

    def _focus_link_graph(self) -> None:
        if not self.link_panel:
            return
        try:
            self.link_panel.graph_view.setFocus(Qt.OtherFocusReason)
        except Exception:
            self.link_panel.setFocus(Qt.OtherFocusReason)

    def _open_tab_context_menu(self, pos) -> None:
        """Offer 'Open in New Window' for select tabs."""
        bar = self.tabs.tabBar()
        index = bar.tabAt(pos)
        if index < 0:
            return
        widget = self.tabs.widget(index)
        menu = QMenu(self)
        apply_menu_theme(menu, bar)
        if self.task_panel and widget == self.task_panel:
            action = menu.addAction("Open in New Window")
            action.triggered.connect(self.openTaskWindowRequested.emit)
        elif self.calendar_panel and widget == self.calendar_panel:
            action = menu.addAction("Open in New Window")
            action.triggered.connect(self.openCalendarWindowRequested.emit)
        elif self.link_panel and widget == self.link_panel:
            action = menu.addAction("Open in New Window")
            action.triggered.connect(self.openLinkWindowRequested.emit)
        elif self.map_panel and widget == self.map_panel:
            action = menu.addAction("Open in New Window")
            action.triggered.connect(self.openMapWindowRequested.emit)
        elif widget == self.ai_chat_panel:
            action = menu.addAction("Open in New Window")
            action.triggered.connect(self.openAiWindowRequested.emit)
        elif self.terminal_panel and widget == self.terminal_panel:
            action = menu.addAction("Open in New Window")
            action.triggered.connect(self.openTerminalWindowRequested.emit)
        else:
            return
        global_pos = bar.mapToGlobal(pos)
        menu.exec(global_pos)

    def _focus_current_tab(self) -> None:
        """Ensure the active tab gains focus when selected."""
        self._sync_visible_panels()
        widget = self.tabs.currentWidget()
        if widget:
            if self.terminal_panel and widget == self.terminal_panel:
                self.terminalTabActivated.emit()
                return
            if self.calendar_panel and widget == self.calendar_panel:
                self._calendar_sync_timer.start()
                try:
                    self.calendar_panel.calendar.setFocus(Qt.OtherFocusReason)
                except Exception:
                    widget.setFocus(Qt.OtherFocusReason)
                return
            # For task panel, focus the search box specifically
            if self.task_panel and widget == self.task_panel:
                if hasattr(widget, "focus_search"):
                    widget.focus_search()
                else:
                    widget.setFocus(Qt.OtherFocusReason)
            elif self.link_panel and widget == self.link_panel:
                target_page = self._pending_link_page if self._pending_link_page is not None else self._current_relative_path
                self.link_panel.refresh(target_page)
                self._pending_link_refresh = False
                QTimer.singleShot(0, self._focus_link_graph)
            else:
                # For other tabs, just set widget focus
                widget.setFocus(Qt.OtherFocusReason)

    def set_terminal_panel(self, panel: QWidget) -> None:
        """Add the application-owned lazy terminal as a normal right-panel tab."""
        if self.terminal_panel is panel and self.tabs.indexOf(panel) >= 0:
            return
        self.terminal_panel = panel
        self.terminal_index = self.tabs.addTab(panel, "Terminal")

    def reattach_terminal_panel(self, panel: QWidget, index: int) -> None:
        """Restore the same terminal widget after its pop-out window closes."""
        self.terminal_panel = panel
        target = max(0, min(int(index), self.tabs.count()))
        self.terminal_index = self.tabs.insertTab(target, panel, "Terminal")
        self.tabs.setCurrentWidget(panel)

    def focus_ai_chat(self, page_path=None, create=False) -> None:
        """Switch to AI Chat tab and sync to the given page."""
        if not self.ai_chat_panel or self.ai_chat_index is None:
            return
        self.tabs.setCurrentIndex(self.ai_chat_index)
        if create:
            self.ai_chat_panel.open_chat_for_page(page_path)
        else:
            self.ai_chat_panel.set_current_page(page_path)

    def send_ai_action(self, action: str, prompt: str, text: str) -> None:
        """Forward external AI action to the chat panel."""
        if self.ai_chat_panel:
            self.ai_chat_panel.send_action_message(action, prompt, text)

    def send_text_to_chat(self, text: str) -> bool:
        """Send raw text into the active chat session (prefers the currently open AI tab)."""
        if not self.ai_chat_panel or self.ai_chat_index is None:
            return False
        if not text.strip():
            return False
        self.ai_chat_panel.send_text_message(text.strip())
        return True

    def get_active_chat_path(self) -> Optional[str]:
        """Folder path of the currently loaded chat session."""
        if not self.ai_chat_panel:
            return None
        return self.ai_chat_panel.get_active_chat_path()

    def is_active_chat_for_page(self, rel_path: Optional[str]) -> bool:
        """Return True if the active chat matches the given page's folder."""
        if not rel_path or not self.ai_chat_panel:
            return False
        active_path = self.get_active_chat_path() or ""
        folder_path = "/" + Path(rel_path.lstrip("/")).parent.as_posix()
        return folder_path == active_path

    def _is_calendar_tab_active(self) -> bool:
        """Return True if the calendar tab is currently selected."""
        if not self.calendar_panel:
            return False
        return self.tabs.currentWidget() == self.calendar_panel

    def _sync_calendar_tab_state(self) -> None:
        """Apply deferred calendar updates once the user explicitly opens the tab."""
        if not self._is_calendar_tab_active() or not self.calendar_panel:
            return
        should_refresh = False
        if self._pending_calendar_vault_root:
            self.calendar_panel.set_vault_root(self._pending_calendar_vault_root, defer_refresh=True)
            self._pending_calendar_vault_root = None
            should_refresh = True
        if self._pending_calendar_refresh:
            self._pending_calendar_refresh = False
            should_refresh = True
        if should_refresh:
            self.calendar_panel.schedule_refresh(0)
        pending_path = self._pending_calendar_path
        pending_date = self._pending_calendar_date
        self._pending_calendar_path = None
        self._pending_calendar_date = None
        if pending_path:
            self.calendar_panel.set_current_page(pending_path)
        elif pending_date:
            y, m, d = pending_date
            self.calendar_panel.set_calendar_date(y, m, d)
        try:
            self.calendar_panel.ensure_splitter_visible()
        except Exception:
            pass

    def _is_panel_currently_visible(self, panel: Optional[QWidget]) -> bool:
        return bool(panel) and self.isVisible() and self.tabs.currentWidget() == panel

    def _is_link_panel_active(self) -> bool:
        return bool(self.link_panel) and self._is_panel_currently_visible(self.link_panel)

    def _sync_visible_panels(self) -> None:
        current = self.tabs.currentWidget()
        if current == self.attachments_panel and (
            self._pending_attachments_refresh or self.attachments_panel.current_page_path != self._current_page_path
        ):
            self.attachments_panel.set_page(self._current_page_path)
            self._pending_attachments_refresh = False
            self._update_attachments_tab_label()
        if self.task_panel and current == self.task_panel and self._pending_task_refresh:
            self.task_panel.refresh()
            self._pending_task_refresh = False
        if self.link_panel and current == self.link_panel and (
            self._pending_link_refresh or self._pending_link_page != self.link_panel.current_page
        ):
            self.link_panel.set_page(self._pending_link_page if self._pending_link_page is not None else self._current_relative_path)
            self._pending_link_refresh = False
        if self.map_panel and current == self.map_panel and self._pending_map_refresh:
            self._sync_map_tab_state()
            self._pending_map_refresh = False
        if self.ai_chat_panel and current == self.ai_chat_panel and self._pending_ai_page is not None:
            self.ai_chat_panel.set_current_page(self._pending_ai_page)
            self._pending_ai_page = None

    def sync_visible_panels(self) -> None:
        self._sync_visible_panels()

    def focus_ai_chat_input(self) -> None:
        if not self.ai_chat_panel or self.ai_chat_index is None:
            return
        self.tabs.setCurrentIndex(self.ai_chat_index)
        QTimer.singleShot(0, self.ai_chat_panel.focus_input)

    def _emit_chat_navigation(self, path: str) -> None:
        """Forward AI chat navigation requests."""
        self.aiChatNavigateRequested.emit(path)

    def _emit_ai_overlay_request(self, text: str, anchor) -> None:
        """Forward AI overlay requests from the chat panel."""
        self.aiOverlayRequested.emit(text, anchor)

    def _emit_ai_page_written(self, path: str) -> None:
        """Forward AI chat page-write notifications."""
        self.aiChatPageWritten.emit(path)

    def _add_ai_chat_tab(self) -> None:
        if self.ai_chat_panel:
            return
        self.ai_chat_panel = AIChatPanel(font_size=self._ai_chat_font_size, api_client=self._http_client)
        self.tabs.addTab(self.ai_chat_panel, "AI Chat")
        self.ai_chat_index = self.tabs.indexOf(self.ai_chat_panel)
        self.ai_chat_panel.chatNavigateRequested.connect(self._emit_chat_navigation)
        self.ai_chat_panel.responseCopied.connect(self.aiChatResponseCopied)
        self.ai_chat_panel.aiOverlayRequested.connect(self._emit_ai_overlay_request)
        self.ai_chat_panel.pageWritten.connect(self._emit_ai_page_written)

    def _remove_ai_chat_tab(self) -> None:
        if not self.ai_chat_panel:
            return
        idx = self.tabs.indexOf(self.ai_chat_panel)
        if idx != -1:
            self.tabs.removeTab(idx)
        try:
            self.ai_chat_panel.chatNavigateRequested.disconnect(self._emit_chat_navigation)
        except Exception:
            pass
        try:
            self.ai_chat_panel.pageWritten.disconnect(self._emit_ai_page_written)
        except Exception:
            pass
        self.ai_chat_panel.deleteLater()
        self.ai_chat_panel = None
        self.ai_chat_index = None

    def set_ai_enabled(self, enabled: bool) -> None:
        """Enable or disable the AI Chat tab."""
        if enabled:
            self._add_ai_chat_tab()
        else:
            self._remove_ai_chat_tab()
        try:
            if self.task_panel:
                self.task_panel.set_ai_enabled(enabled)
        except Exception:
            pass

    def set_feature_flags(self, *, enable_tasks: bool, enable_calendar: bool, enable_link_navigator: bool, enable_map: bool) -> None:
        if enable_tasks:
            self._add_task_tab()
        else:
            self._remove_task_tab()
        if enable_calendar:
            self._add_calendar_tab()
        else:
            self._remove_calendar_tab()
        if enable_link_navigator:
            self._add_link_tab()
        else:
            self._remove_link_tab()
        if enable_map:
            self._add_map_tab()
        else:
            self._remove_map_tab()
        if self.task_panel:
            self.task_panel.set_calendar_feature_enabled(enable_calendar)

    def _tab_insert_index(self, after_widget: Optional[QWidget]) -> int:
        if not after_widget:
            return self.tabs.count()
        idx = self.tabs.indexOf(after_widget)
        if idx == -1:
            return self.tabs.count()
        return idx + 1

    def _add_task_tab(self) -> None:
        if self.task_panel:
            return
        self.task_panel = TaskPanel(font_size_key="task_font_size_tabbed", splitter_key="task_splitter_tabbed")
        self.tabs.insertTab(0, self.task_panel, "Tasks")
        if log_enabled("ui_state"):
            self.task_panel.taskActivated.connect(lambda path, line: print(f"[TABBED_PANEL] taskActivated received: {path}:{line}") or self.taskActivated.emit(path, line))
        else:
            self.task_panel.taskActivated.connect(self.taskActivated)
        self.task_panel.filterClearRequested.connect(self.filterClearRequested)
        self.task_panel.taskDatesWillApply.connect(self.taskDatesWillApply)
        self.task_panel.taskDatesApplied.connect(self.taskDatesApplied)
        self.task_panel.statusRequested.connect(self.taskStatusRequested)
        self.task_panel.remoteRequestObserved.connect(self.remoteRequestObserved, Qt.QueuedConnection)
        if self._http_client:
            self.task_panel.set_http_client(self._http_client)
        self.task_panel.set_remote_mode(self._remote_mode)
        self.task_panel.set_calendar_feature_enabled(self.calendar_panel is not None)
        self.task_panel.set_vault_accent_color(self._vault_accent_color)
        self._sync_calendar_task_filters()

    def _remove_task_tab(self) -> None:
        if not self.task_panel:
            return
        idx = self.tabs.indexOf(self.task_panel)
        if idx != -1:
            self.tabs.removeTab(idx)
        self.task_panel.deleteLater()
        self.task_panel = None
        self._sync_calendar_task_filters()

    def _add_calendar_tab(self) -> None:
        if self.calendar_panel:
            return
        self.calendar_panel = CalendarPanel(
            font_size_key="calendar_font_size_tabbed",
            splitter_key="calendar_splitter_tabbed",
            http_client=self._http_client,
            api_base=self._http_client.base_url if self._http_client else None,
        )
        insert_idx = self._tab_insert_index(self.task_panel)
        self.tabs.insertTab(insert_idx, self.calendar_panel, "Calendar")
        self.calendar_panel.dateActivated.connect(self.dateActivated)
        self.calendar_panel.pageActivated.connect(self.calendarPageActivated)
        self.calendar_panel.taskActivated.connect(self.calendarTaskActivated)
        self.calendar_panel.tasksUpdated.connect(self.refresh_tasks)
        self.calendar_panel.taskDatesWillApply.connect(self.taskDatesWillApply)
        self.calendar_panel.taskDatesApplied.connect(self.taskDatesApplied)
        self.calendar_panel.openInWindowRequested.connect(self.openInWindowRequested)
        self.calendar_panel.pageAboutToBeDeleted.connect(self.pageAboutToBeDeleted)
        self.calendar_panel.pageDeleted.connect(self.pageDeleted)
        self.calendar_panel.remoteRequestObserved.connect(self.remoteRequestObserved, Qt.QueuedConnection)
        self.calendar_panel.set_remote_mode(self._remote_mode)
        self.calendar_panel.set_vault_accent_color(self._vault_accent_color)
        self._sync_calendar_task_filters()

    def _remove_calendar_tab(self) -> None:
        if not self.calendar_panel:
            return
        idx = self.tabs.indexOf(self.calendar_panel)
        if idx != -1:
            self.tabs.removeTab(idx)
        self.calendar_panel.deleteLater()
        self.calendar_panel = None
        self._pending_calendar_vault_root = None
        self._pending_calendar_refresh = False
        self._pending_calendar_path = None
        self._pending_calendar_date = None

    def _add_link_tab(self) -> None:
        if self.link_panel:
            return
        self.link_panel = LinkNavigatorPanel()
        insert_idx = self._tab_insert_index(self.attachments_panel)
        self.tabs.insertTab(insert_idx, self.link_panel, "Link Navigator")
        self.link_panel.pageActivated.connect(self.linkActivated)
        self.link_panel.openInWindowRequested.connect(self.openInWindowRequested)
        self.link_panel.backRequested.connect(self.linkBackRequested)
        self.link_panel.forwardRequested.connect(self.linkForwardRequested)
        self.link_panel.homeRequested.connect(self.linkHomeRequested)
        self.link_panel.set_vault_accent_color(self._vault_accent_color)
        self.link_panel.apply_theme()

    def _remove_link_tab(self) -> None:
        if not self.link_panel:
            return
        idx = self.tabs.indexOf(self.link_panel)
        if idx != -1:
            self.tabs.removeTab(idx)
        self.link_panel.deleteLater()
        self.link_panel = None

    def _add_map_tab(self) -> None:
        if self.map_panel:
            return
        self.map_panel = MapPanel()
        self.map_panel.headingActivated.connect(self.mapHeadingActivated)
        self.map_panel.headingCreateRequested.connect(self.mapHeadingCreateRequested)
        self.map_panel.headingRenameRequested.connect(self.mapHeadingRenameRequested)
        self.map_panel.headingReorderRequested.connect(self.mapHeadingReorderRequested)
        self.map_panel.statusMessageRequested.connect(self.mapStatusRequested)
        self.map_panel.focusSyncRequested.connect(self._sync_map_tab_state)
        insert_idx = self._tab_insert_index(self.link_panel or self.attachments_panel)
        self.tabs.insertTab(insert_idx, self.map_panel, "Map")

    def _remove_map_tab(self) -> None:
        if not self.map_panel:
            return
        idx = self.tabs.indexOf(self.map_panel)
        if idx != -1:
            self.tabs.removeTab(idx)
        try:
            self.map_panel.headingActivated.disconnect(self.mapHeadingActivated)
        except Exception:
            pass
        try:
            self.map_panel.headingCreateRequested.disconnect(self.mapHeadingCreateRequested)
        except Exception:
            pass
        try:
            self.map_panel.headingRenameRequested.disconnect(self.mapHeadingRenameRequested)
        except Exception:
            pass
        try:
            self.map_panel.headingReorderRequested.disconnect(self.mapHeadingReorderRequested)
        except Exception:
            pass
        try:
            self.map_panel.statusMessageRequested.disconnect(self.mapStatusRequested)
        except Exception:
            pass
        try:
            self.map_panel.focusSyncRequested.disconnect(self._sync_map_tab_state)
        except Exception:
            pass
        self.map_panel.deleteLater()
        self.map_panel = None

    def focus_map_tab(self, page_path=None) -> None:
        if not self.map_panel:
            return
        if page_path is not None:
            self._current_relative_path = page_path
            self._sync_map_tab_state()
        for i in range(self.tabs.count()):
            if self.tabs.widget(i) == self.map_panel:
                self.tabs.setCurrentIndex(i)
                self.map_panel.setFocus(Qt.ShortcutFocusReason)
                break

    def is_map_panel_active(self) -> bool:
        return bool(self.map_panel) and self._is_panel_currently_visible(self.map_panel)

    def zoom_map_selected_node(self, delta: int) -> bool:
        if not self.is_map_panel_active() or not self.map_panel:
            return False
        try:
            return bool(self.map_panel.zoom_selected_node(delta))
        except Exception:
            return False

    def consume_activation_source(self) -> Optional[str]:
        if self.map_panel and hasattr(self.map_panel, "consume_activation_source"):
            try:
                return self.map_panel.consume_activation_source()
            except Exception:
                return None
        return None

    def _sync_map_tab_state(self) -> None:
        if not self.map_panel:
            return
        rel_path = self._current_relative_path
        if not rel_path:
            self.map_panel.clear_content()
            return
        text = ""
        if self._page_text_provider:
            try:
                text = self._page_text_provider(rel_path) or ""
            except Exception:
                text = ""
        self.map_panel.set_content(rel_path, text)

    def _sync_calendar_task_filters(self) -> None:
        if not self.calendar_panel:
            return
        opener = self.task_panel.open_date_filter_dialog if self.task_panel else None
        setter = self.task_panel.set_date_filter_range if self.task_panel else None
        try:
            self.calendar_panel.set_task_date_filter_opener(opener)
        except Exception:
            pass
        try:
            self.calendar_panel.set_task_date_filter_setter(setter)
        except Exception:
            pass
    
    def _update_attachments_tab_label(self) -> None:
        """Update the Attachments tab label with the count of attachments."""
        count = self.attachments_panel.attachments_list.count()
        # Find the attachments tab (should be index 1)
        for i in range(self.tabs.count()):
            if self.tabs.widget(i) == self.attachments_panel:
                self.tabs.setTabText(i, f"Attachments ({count})")
                break
