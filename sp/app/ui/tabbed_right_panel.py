from __future__ import annotations

import httpx
import os
import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Signal, QTimer
from PySide6.QtWidgets import QTabWidget, QWidget, QMenu
from PySide6.QtCore import Qt
from sp.app import config

from .ai_chat_panel import AIChatPanel
from .task_panel import TaskPanel
from .attachments_panel import AttachmentsPanel
from .link_navigator_panel import LinkNavigatorPanel
from .calendar_panel import CalendarPanel
from .page_load_logger import PAGE_LOGGING_ENABLED


class TabbedRightPanel(QWidget):
    """Tabbed panel containing Tasks, Calendar, Attachments, and Link views."""
    
    # Forward signals from child panels
    taskActivated = Signal(str, int)  # path, line (from TaskPanel)
    dateActivated = Signal(int, int, int)  # year, month, day (from Calendar tab)
    linkActivated = Signal(str)  # page path from Link Navigator
    calendarPageActivated = Signal(str)  # page path from Calendar tab
    calendarTaskActivated = Signal(str, int)  # path, line from Calendar tab task list
    aiChatNavigateRequested = Signal(str)  # page path from AI Chat tab
    aiChatResponseCopied = Signal(str)  # status text when chat response copied
    aiOverlayRequested = Signal(str, object)  # text, anchor QPoint
    openInWindowRequested = Signal(str)  # page path to open in single-page editor
    openTaskWindowRequested = Signal()
    openLinkWindowRequested = Signal()
    openAiWindowRequested = Signal()
    openCalendarWindowRequested = Signal()
    filterClearRequested = Signal()
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
        enable_ai_chats: bool = False,
        ai_chat_font_size: int = 13,
        http_client: Optional[httpx.Client] = None,
        auth_prompt=None,
    ) -> None:
        super().__init__(parent)
        
        # Create tab widget
        self.tabs = QTabWidget()
        self.ai_chat_panel = None
        self.ai_chat_index = None
        self._ai_chat_font_size = self._clamp_ai_font(ai_chat_font_size)
        self._http_client = http_client
        self._pending_calendar_path: Optional[str] = None
        self._pending_calendar_date: Optional[tuple[int, int, int]] = None
        self._pending_calendar_vault_root: Optional[str] = None
        self._pending_calendar_refresh: bool = False
        
        # Create Tasks tab
        self.task_panel = None
        if enable_tasks:
            self._add_task_tab()

        # Create Calendar tab
        self.calendar_panel = None
        if enable_calendar:
            self._add_calendar_tab()
        
        # Create Attachments tab
        self.attachments_panel = AttachmentsPanel(api_client=http_client, auth_prompt=auth_prompt)
        self.tabs.addTab(self.attachments_panel, "Attachments")

        # Create Link Navigator tab
        self.link_panel = None
        if enable_link_navigator:
            self._add_link_tab()

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
        from PySide6.QtWidgets import QVBoxLayout
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
        if self.calendar_panel:
            self.calendar_panel.http = http_client
            if api_base:
                self.calendar_panel.api_base = api_base
        self.attachments_panel.set_http_client(http_client)
        self.attachments_panel.set_remote_mode(remote_mode, api_base)
        if auth_prompt is not None:
            self.attachments_panel.set_auth_prompt(auth_prompt)
        if self.task_panel:
            self.task_panel.set_http_client(http_client)
        if self.ai_chat_panel:
            self.ai_chat_panel.set_api_client(http_client)
    
    def refresh_tasks(self) -> None:
        """Refresh the task panel."""
        if self.task_panel:
            self.task_panel.refresh()
    
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
    
    def set_calendar_date(self, year: int, month: int, day: int) -> None:
        """Set the calendar to show a specific date."""
        if not self.calendar_panel:
            return
        if self._is_calendar_tab_active():
            self.calendar_panel.set_calendar_date(year, month, day)
        else:
            self._pending_calendar_date = (year, month, day)
    
    def set_current_page(self, page_path, relative_path=None) -> bool:
        """Update panels with the current page."""
        t0 = time.perf_counter()
        self.attachments_panel.set_page(page_path)
        t1 = time.perf_counter()
        if self.link_panel:
            self.link_panel.set_page(relative_path)
        t2 = time.perf_counter()
        try:
            if self.calendar_panel and relative_path:
                if self._is_calendar_tab_active():
                    self.calendar_panel.set_current_page(relative_path)
                else:
                    self._pending_calendar_path = relative_path
        except Exception:
            pass
        if PAGE_LOGGING_ENABLED:
            print(
                f"[PageLoadAndRender] right panel update attachments={(t1-t0)*1000:.1f}ms links={(t2-t1)*1000:.1f}ms"
            )
        if self.ai_chat_panel:
            self.ai_chat_panel.set_current_page(relative_path)
        t3 = time.perf_counter()
        if PAGE_LOGGING_ENABLED:
            print(
                f"[PageLoadAndRender] right panel ai chat {(t3-t2)*1000:.1f}ms"
            )
        self._update_attachments_tab_label()
        if self.ai_chat_panel and hasattr(self.ai_chat_panel, "has_chat_for_path"):
            return self.ai_chat_panel.has_chat_for_path(relative_path)
        return False

    def set_page_text_provider(self, provider) -> None:
        """Provide calendar panel with live editor text for AI summaries."""
        try:
            if self.calendar_panel:
                self.calendar_panel.set_page_text_provider(provider)
        except Exception:
            pass

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
        self.attachments_panel.refresh()
        self._update_attachments_tab_label()

    def refresh_links(self, page_path=None) -> None:
        """Refresh the link navigator for the given page (or current)."""
        if not self.link_panel:
            return
        try:
            win = self.window()
            if getattr(win, "_mode_window_pending", False) or getattr(win, "_mode_window", None):
                QTimer.singleShot(100, lambda p=page_path: self.refresh_links(p))
                return
        except Exception:
            pass
        self.link_panel.refresh(page_path)

    def focus_link_tab(self, page_path=None) -> None:
        """Switch to the Link Navigator tab and optionally set its page."""
        if not self.link_panel:
            return
        if page_path is not None:
            self.link_panel.set_page(page_path)
        for i in range(self.tabs.count()):
            if self.tabs.widget(i) == self.link_panel:
                self.tabs.setCurrentIndex(i)
                # Ensure content is fresh whenever the tab gains focus
                self.link_panel.refresh(page_path)
                break

    def _open_tab_context_menu(self, pos) -> None:
        """Offer 'Open in New Window' for select tabs."""
        bar = self.tabs.tabBar()
        index = bar.tabAt(pos)
        if index < 0:
            return
        widget = self.tabs.widget(index)
        menu = QMenu(self)
        if self.task_panel and widget == self.task_panel:
            action = menu.addAction("Open in New Window")
            action.triggered.connect(self.openTaskWindowRequested.emit)
        elif self.calendar_panel and widget == self.calendar_panel:
            action = menu.addAction("Open in New Window")
            action.triggered.connect(self.openCalendarWindowRequested.emit)
        elif self.link_panel and widget == self.link_panel:
            action = menu.addAction("Open in New Window")
            action.triggered.connect(self.openLinkWindowRequested.emit)
        elif widget == self.ai_chat_panel:
            action = menu.addAction("Open in New Window")
            action.triggered.connect(self.openAiWindowRequested.emit)
        else:
            return
        global_pos = bar.mapToGlobal(pos)
        menu.exec(global_pos)

    def _focus_current_tab(self) -> None:
        """Ensure the active tab gains focus when selected."""
        widget = self.tabs.currentWidget()
        if widget:
            if self.calendar_panel and widget == self.calendar_panel:
                self._sync_calendar_tab_state()
            # For task panel, focus the search box specifically
            if self.task_panel and widget == self.task_panel:
                if hasattr(widget, "focus_search"):
                    widget.focus_search()
                else:
                    widget.setFocus(Qt.OtherFocusReason)
            else:
                # For other tabs, just set widget focus
                widget.setFocus(Qt.OtherFocusReason)

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
        if self._pending_calendar_vault_root:
            self.calendar_panel.set_vault_root(self._pending_calendar_vault_root)
            self._pending_calendar_vault_root = None
        if self._pending_calendar_refresh:
            self.calendar_panel.refresh()
            self._pending_calendar_refresh = False
        pending_path = self._pending_calendar_path
        pending_date = self._pending_calendar_date
        self._pending_calendar_path = None
        self._pending_calendar_date = None
        if pending_path:
            self.calendar_panel.set_current_page(pending_path)
        elif pending_date:
            y, m, d = pending_date
            self.calendar_panel.set_calendar_date(y, m, d)

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

    def _add_ai_chat_tab(self) -> None:
        if self.ai_chat_panel:
            return
        self.ai_chat_panel = AIChatPanel(font_size=self._ai_chat_font_size, api_client=self._http_client)
        self.tabs.addTab(self.ai_chat_panel, "AI Chat")
        self.ai_chat_index = self.tabs.indexOf(self.ai_chat_panel)
        self.ai_chat_panel.chatNavigateRequested.connect(self._emit_chat_navigation)
        self.ai_chat_panel.responseCopied.connect(self.aiChatResponseCopied)
        self.ai_chat_panel.aiOverlayRequested.connect(self._emit_ai_overlay_request)

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

    def set_feature_flags(self, *, enable_tasks: bool, enable_calendar: bool, enable_link_navigator: bool) -> None:
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
        if os.getenv("ZIMX_DEBUG_PANELS", "0") not in ("0", "false", "False", ""):
            self.task_panel.taskActivated.connect(lambda path, line: print(f"[TABBED_PANEL] taskActivated received: {path}:{line}") or self.taskActivated.emit(path, line))
        else:
            self.task_panel.taskActivated.connect(self.taskActivated)
        self.task_panel.filterClearRequested.connect(self.filterClearRequested)
        if self._http_client:
            self.task_panel.set_http_client(self._http_client)

    def _remove_task_tab(self) -> None:
        if not self.task_panel:
            return
        idx = self.tabs.indexOf(self.task_panel)
        if idx != -1:
            self.tabs.removeTab(idx)
        self.task_panel.deleteLater()
        self.task_panel = None

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
        self.calendar_panel.openInWindowRequested.connect(self.openInWindowRequested)
        self.calendar_panel.pageAboutToBeDeleted.connect(self.pageAboutToBeDeleted)
        self.calendar_panel.pageDeleted.connect(self.pageDeleted)

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

    def _remove_link_tab(self) -> None:
        if not self.link_panel:
            return
        idx = self.tabs.indexOf(self.link_panel)
        if idx != -1:
            self.tabs.removeTab(idx)
        self.link_panel.deleteLater()
        self.link_panel = None
    
    def _update_attachments_tab_label(self) -> None:
        """Update the Attachments tab label with the count of attachments."""
        count = self.attachments_panel.attachments_list.count()
        # Find the attachments tab (should be index 1)
        for i in range(self.tabs.count()):
            if self.tabs.widget(i) == self.attachments_panel:
                self.tabs.setTabText(i, f"Attachments ({count})")
                break
