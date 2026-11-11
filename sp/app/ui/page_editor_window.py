from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional
import re
import hashlib
import traceback
import httpx
import sys

from PySide6.QtCore import QTimer, Qt, QByteArray, QObject, QEvent, QPoint, QSize, QPropertyAnimation
from PySide6.QtGui import (
    QAction,
    QKeySequence,
    QShortcut,
    QColor,
    QIcon,
    QTextCursor,
    QTextFormat,
    QPalette,
    QPainter,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QToolBar,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import QSize

from .find_replace_bar import FindReplaceBar
from .markdown_editor import MarkdownEditor, MARKDOWN_IMAGE_WIDTH_OPTIONS
from .keyboard_shortcuts import is_vi_navigation_chord, primary_modifier_label
from .canvas_navigation import install_primary_wheel_zoom
from .insert_link_dialog import InsertLinkDialog
from .date_insert_dialog import DateInsertDialog
from .page_load_logger import PageLoadLogger, PAGE_LOGGING_ENABLED
from .path_utils import colon_to_path, path_to_colon, ensure_root_colon_link, should_use_full_target_label, trace_link_decision
from .toc_widget import TableOfContentsWidget
from .task_quick_editor import TaskQuickEditor
from sp.app import config, indexer
from sp.logging_flags import log_enabled
from .theme import apply_menu_theme, theme_color, theme_value
from .screen_positioning import popup_available_geometry, clamp_popup_top_left
from sp.server.adapters.files import PAGE_SUFFIXES


_ONE_SHOT_PROMPT_CACHE: Optional[str] = None
_PAGE_EDITOR_DEBUG = log_enabled("ui_state")


def _page_editor_log(message: str) -> None:
    if _PAGE_EDITOR_DEBUG:
        print(message)


def _load_one_shot_prompt() -> str:
    """Load the one-shot system prompt once and cache it."""
    global _ONE_SHOT_PROMPT_CACHE
    if _ONE_SHOT_PROMPT_CACHE is not None:
        return _ONE_SHOT_PROMPT_CACHE
    default_prompt = "you are a helpful assistent, you will respond with markdown formatting"
    try:
        prompt_path = Path(__file__).parent.parent / "one-shot-prompt.txt"
        if prompt_path.exists():
            content = prompt_path.read_text(encoding="utf-8").strip()
            if content:
                _ONE_SHOT_PROMPT_CACHE = content
                return content
    except Exception:
        pass
    _ONE_SHOT_PROMPT_CACHE = default_prompt
    return default_prompt


class PageEditorWindow(QMainWindow):
    """Lightweight single-page editor window (no navigation panes)."""

    def __init__(
        self,
        api_base: str,
        vault_root: str,
        page_path: str,
        read_only: bool,
        open_in_main_callback: Callable[[str], None],
        local_auth_token: Optional[str] = None,
        remote_mode: bool = False,
        auth_prompt: Optional[Callable[[], bool]] = None,
        http_headers: Optional[dict[str, str]] = None,
        http_auth: Optional[httpx.Auth] = None,
        verify_tls: bool = True,
        parent=None,
    ) -> None:
        super().__init__(parent)
        
        # Set window icon explicitly (especially important on Windows)
        from sp.app.main import get_app_icon
        self.setWindowIcon(get_app_icon())
        
        self.api_base = api_base.rstrip("/")
        self.vault_root = vault_root
        self._source_path = page_path  # lock the target path for all saves
        self.page_path = page_path
        self._read_only = read_only
        self._remote_mode = bool(remote_mode)
        self._auth_prompt = auth_prompt
        self._open_in_main = open_in_main_callback
        headers = dict(http_headers) if http_headers else None
        if headers is None and local_auth_token:
            headers = {"X-Local-UI-Token": local_auth_token}
        self.http = httpx.Client(
            base_url=self.api_base,
            timeout=10.0,
            headers=headers,
            auth=http_auth,
            verify=verify_tls,
        )
        self._badge_base_style = (
            "border: 1px solid "
            f"{theme_value('page_editor_window.badge.border', '#666666')}; "
            "padding: 2px 6px; border-radius: 3px;"
        )
        self._font_size = config.load_popup_font_size(14)

        self.editor = MarkdownEditor()
        install_primary_wheel_zoom(self.editor, self._adjust_font_size)
        # Custom context menu with Edit operations only
        self.editor.setContextMenuPolicy(Qt.CustomContextMenu)
        self.editor.customContextMenuRequested.connect(self._show_editor_context_menu)
        self.editor.task_context_menu_provider = self._task_context_menu
        self.editor.set_context(self.vault_root, self._source_path)
        self.editor.set_font_point_size(self._font_size)
        self.editor.set_vi_cursor_style(config.load_vi_cursor_style())
        self.editor.set_vi_mode_enabled(config.load_vi_mode_enabled())
        self.editor.set_task_hover_edit_enabled(True)
        self.editor.set_read_only_mode(self._read_only)
        self.editor.set_ai_shortcuts_enabled(False)
        self.editor.linkActivated.connect(self._forward_link_to_main)
        self.editor.linkCopied.connect(
            lambda link: self.statusBar().showMessage(f"Copied link: {link}", 3000)
        )
        self.editor.focusLost.connect(lambda: self._save_current_file(auto=True, reason="focus lost"))
        self.editor.viInsertModeChanged.connect(self._on_vi_insert_state_changed)
        self.editor.aiInlinePromptRequested.connect(self._open_inline_ai_prompt)
        self.editor.findBarRequested.connect(self._on_editor_find_requested)
        self.editor.taskEditRequested.connect(self._edit_task_from_page_editor)
        self._task_editor: Optional[TaskQuickEditor] = None
        self._task_undo_id: Optional[str] = None
        self._task_status_undo = None
        self._task_status_undo_content = None
        self._find_bar = FindReplaceBar(self)
        self._find_bar.findNextRequested.connect(self._on_find_next_requested)
        self._find_bar.replaceRequested.connect(self._on_replace_requested)
        self._find_bar.replaceAllRequested.connect(self._on_replace_all_requested)
        self._find_bar.closed.connect(lambda: self.editor.setFocus(Qt.ShortcutFocusReason))

        editor_host = QWidget(self)
        editor_layout = QVBoxLayout(editor_host)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.setSpacing(0)
        editor_layout.addWidget(self.editor, 1)
        editor_layout.addWidget(self._find_bar)
        self.setCentralWidget(editor_host)
        self._apply_theme_palette()
        
        # Vi mode state
        self._vi_enabled = config.load_vi_mode_enabled()
        self._vi_insert_active = False

        self._last_saved_content: Optional[str] = None
        self._saved_disk_content: Optional[str] = None
        self._saved_content_sha256: Optional[str] = None
        self._refreshing_saved_state = False
        self._app_state_changed_slot = None
        self._inline_ai_worker = None
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setInterval(30_000)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.timeout.connect(lambda: self._save_current_file(auto=True, reason="autosave timer"))
        self.editor.textChanged.connect(self._on_buffer_changed)
        self.editor.document().modificationChanged.connect(lambda _: self._update_dirty_indicator())
        app = QApplication.instance()
        if app is not None:
            try:
                self._app_state_changed_slot = self._on_application_state_changed
                app.applicationStateChanged.connect(self._app_state_changed_slot)
            except Exception:
                pass
        
        # Heading picker state
        self._toc_headings: list[dict] = []
        self._toc_scroll_anim = None
        self.toc_widget = TableOfContentsWidget(self)
        self.toc_widget.set_headings([])
        self.toc_widget.set_base_path(ensure_root_colon_link(path_to_colon(self._source_path)))
        self.toc_widget.headingActivated.connect(self._toc_jump_to_position)
        self.toc_widget.set_collapsed(config.load_page_editor_toc_collapsed())
        self.toc_widget.collapsedChanged.connect(self._on_toc_collapsed_changed)
        self.toc_widget.linkCopied.connect(lambda link: self.statusBar().showMessage(f"Copied link: {link}", 3000))
        self.toc_widget.hide()
        self.editor.viewportResized.connect(self._position_toc_widget)
        self.editor.verticalScrollBar().valueChanged.connect(self._position_toc_widget)
        self.editor.verticalScrollBar().rangeChanged.connect(self._position_toc_widget)
        self._refresh_toc_preferences()
        self._popup_items: list = []
        self._popup_index: int = -1
        self._popup_mode: Optional[str] = None
        self._heading_popup: Optional[QWidget] = None
        self._heading_popup_label: Optional[QLabel] = None
        self._heading_popup_list: Optional[QListWidget] = None
        self.editor.headingsChanged.connect(self._on_headings_changed)
        self.editor.headingPickerRequested.connect(self._handle_heading_picker_request)

        self._build_toolbar()
        self._install_close_window_action()
        self._load_content()
        self._update_title()
        self._size_and_center(parent)
        self._restore_geometry()
        self._geometry_timer = QTimer(self)
        self._geometry_timer.setInterval(400)
        self._geometry_timer.setSingleShot(True)
        self._geometry_timer.timeout.connect(self._save_geometry)

        # Status badges
        self._dirty_status_label = QLabel("")
        self._dirty_status_label.setObjectName("popupDirtyStatusLabel")
        self._dirty_status_label.setStyleSheet(
            self._badge_base_style
            + " background-color: transparent; margin-right: 6px;"
        )
        self._dirty_status_label.setToolTip("Unsaved changes")
        self.statusBar().addPermanentWidget(self._dirty_status_label, 0)
        self._update_dirty_indicator()
        
        # Vi mode indicator
        self._vi_status_label = QLabel("INS")
        self._vi_status_label.setObjectName("viStatusLabel")
        self._vi_status_label.setStyleSheet(self._badge_base_style)
        self._vi_status_label.setToolTip("Shows when vi insert mode is active")
        self.statusBar().addPermanentWidget(self._vi_status_label, 0)
        self._update_vi_badge_visibility()

        # Font shortcuts (popup-local)
        zoom_in = QShortcut(QKeySequence.ZoomIn, self)
        zoom_out = QShortcut(QKeySequence.ZoomOut, self)
        zoom_in.activated.connect(lambda: self._adjust_font_size(1))
        zoom_out.activated.connect(lambda: self._adjust_font_size(-1))
        plus_shortcut = QShortcut(QKeySequence("+"), self)
        minus_shortcut = QShortcut(QKeySequence("-"), self)
        plus_shortcut.activated.connect(lambda: self._adjust_font_size(1))
        minus_shortcut.activated.connect(lambda: self._adjust_font_size(-1))
        
        # Heading picker shortcuts (window-local to avoid conflicts)
        # Try using QAction instead of QShortcut
        heading_action = QAction("Show Heading Picker", self)
        heading_action.setShortcut(QKeySequence("Ctrl+Alt+T"))
        heading_action.setShortcutContext(Qt.WidgetWithChildrenShortcut)
        heading_action.triggered.connect(self._request_heading_picker_popup)
        self.addAction(heading_action)

        date_action = QAction("Insert Date…", self)
        date_action.setShortcut(QKeySequence("Ctrl+D"))
        date_action.setShortcutContext(Qt.WidgetWithChildrenShortcut)
        date_action.triggered.connect(self._insert_date)
        self.addAction(date_action)

        self._find_shortcut = QShortcut(QKeySequence.Find, self.editor)
        self._find_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self._find_shortcut.activated.connect(lambda: self._show_find_bar(replace=False, backwards=False))

        self._replace_shortcut = QShortcut(QKeySequence("Ctrl+H"), self.editor)
        self._replace_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self._replace_shortcut.activated.connect(lambda: self._show_find_bar(replace=True, backwards=False))
        
        # Install event filter to catch Control key release for popup navigation
        self.installEventFilter(self)
        self.editor.installEventFilter(self)

    def _install_close_window_action(self) -> None:
        """Expose a real per-window Close command, including the macOS menu."""
        action = QAction("Close Window", self)
        action.setShortcut(QKeySequence("Ctrl+W"))
        action.setShortcutContext(Qt.WindowShortcut)
        action.setMenuRole(QAction.MenuRole.NoRole)
        action.triggered.connect(self.close)
        self.addAction(action)
        if sys.platform == "darwin":
            file_menu = self.menuBar().addMenu("&File")
            file_menu.addAction(self._save_action)
            file_menu.addAction(self._reload_action)
            file_menu.addSeparator()
            file_menu.addAction(action)
            # In a detached single-page editor, Cmd+Q means close this editor,
            # not terminate the parent StillPoint application. NoRole prevents
            # macOS from promoting this window-local action to the app Quit role.
            close_editor_action = QAction("Close Editor Window", self)
            close_editor_action.setShortcut(QKeySequence("Ctrl+Q"))
            close_editor_action.setShortcutContext(Qt.WindowShortcut)
            close_editor_action.setMenuRole(QAction.MenuRole.NoRole)
            close_editor_action.triggered.connect(self.close)
            self.addAction(close_editor_action)
            file_menu.addAction(close_editor_action)
            self._close_editor_quit_action = close_editor_action
            edit_menu = self.menuBar().addMenu("&Edit")
            edit_menu.addAction(self._insert_link_action)
        self._close_window_action = action

    @staticmethod
    def _is_close_window_key(event) -> bool:
        is_window_close = event.key() == Qt.Key_W
        is_detached_quit = sys.platform == "darwin" and event.key() == Qt.Key_Q
        if not (is_window_close or is_detached_quit):
            return False
        modifiers = event.modifiers() & ~Qt.KeypadModifier
        if modifiers == Qt.ControlModifier:
            return True
        return sys.platform == "darwin" and modifiers == Qt.MetaModifier

    def _api_post(self, path: str, payload: dict, *, headers=None) -> httpx.Response:
        """POST with one remote re-auth retry on 401."""
        options = {"headers": headers} if headers else {}
        resp = self.http.post(path, json=payload, **options)
        if resp.status_code == 401 and self._remote_mode and self._auth_prompt:
            if self._auth_prompt():
                resp = self.http.post(path, json=payload, **options)
        return resp

    def _api_get(self, path: str, *, params: Optional[dict] = None) -> httpx.Response:
        """GET with one remote re-auth retry on 401."""
        resp = self.http.get(path, params=params)
        if resp.status_code == 401 and self._remote_mode and self._auth_prompt:
            if self._auth_prompt():
                resp = self.http.get(path, params=params)
        return resp

    def _task_destination_pages(self, query: str) -> list[str]:
        """Search destinations using this window's server connection."""
        term = str(query or "").strip().lstrip(":")
        if not term:
            return []
        try:
            response = self._api_get("/api/pages/search", params={"q": term, "limit": 40})
            response.raise_for_status()
            pages = response.json().get("pages", [])
        except Exception:
            return []
        results: list[str] = []
        for page in pages:
            raw_path = str(page.get("path") or "") if isinstance(page, dict) else str(page or "")
            colon_path = path_to_colon(raw_path)
            if colon_path:
                results.append(f":{colon_path.lstrip(':')}")
        return list(dict.fromkeys(results))

    def _task_editor_known_tags(self) -> list[str]:
        """Load tag completions without borrowing the main window's task panel."""
        try:
            response = self._api_get("/tags")
            response.raise_for_status()
            entries = response.json().get("tags", [])
        except Exception:
            return []
        tags: set[str] = set()
        for entry in entries:
            value = entry.get("tag") if isinstance(entry, dict) else entry
            cleaned = str(value or "").lstrip("@").strip()
            if cleaned:
                tags.add(cleaned)
        return sorted(tags, key=str.casefold)

    def _normalize_task_destination(self, destination: Optional[str]) -> Optional[str]:
        cleaned = str(destination or "").strip()
        if not cleaned:
            return None
        if cleaned.startswith("/"):
            return cleaned
        return colon_to_path(cleaned, Path(self.vault_root).name)

    @staticmethod
    def _task_mutation_error(exc: Exception) -> str:
        message = str(exc)
        response = getattr(exc, "response", None)
        if response is not None:
            try:
                message = str(response.json().get("detail") or message)
            except Exception:
                pass
        return message

    def _prepare_task_action(self) -> bool:
        if self._read_only or self.editor.isReadOnly():
            return False
        if self._is_dirty():
            self._save_current_file(auto=True, reason="task context action", notify_main=False)
        if self._is_dirty():
            QMessageBox.warning(self, "Task update blocked", "Save the page before changing this task.")
            return False
        return True

    def _task_context_menu(self, block_number: int, anchor_pos):
        from .task_context_menu import build_task_context_menu

        if self._read_only or self.editor.isReadOnly():
            return None
        lines = self.editor.to_markdown().splitlines()
        if not 0 <= block_number < len(lines):
            return None
        parsed = indexer.extract_tasks(self._source_path, lines[block_number])
        if not parsed:
            return None
        task = dict(parsed[0], path=self._source_path, line=block_number + 1,
                    id=f"{self._source_path}:{block_number + 1}")

        def dispatch(command):
            if not self._prepare_task_action():
                return
            if command in ("edit", "tags", "move"):
                self._edit_task_from_page_editor(
                    block_number, anchor_pos,
                    focus_field={"tags": "tags", "move": "destination"}.get(command),
                )
            elif command in ("due", "start"):
                self._show_task_date_menu(task, command, anchor_pos)
            elif command == "undo":
                self._undo_task_action()
            else:
                if command == "delete" and QMessageBox.question(
                    self, "Delete task?", "Delete the selected task?",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
                ) != QMessageBox.Yes:
                    return
                changes = {
                    "complete": {"status": "done"}, "reopen": {"status": "todo"},
                    "delete": {"delete": True}, "remove_indicators": {"remove_indicators": True},
                }[command]
                self._mutate_task_from_page_editor(task, changes)

        return build_task_context_menu(
            self, [{"task": task}], dispatch, undo_available=bool(self._task_undo_id),
            theme_anchor=self.editor,
        )

    def _show_task_date_menu(self, task: dict, role: str, anchor_pos) -> None:
        from .task_panel import TaskDateQuickMenu, TaskPanel

        menu = TaskDateQuickMenu(self, use_vi_keys=bool(self._vi_enabled))
        apply_menu_theme(menu, self.editor)
        for label in ("Today", "Tomorrow", "Yesterday", "This Week", "Next Week",
                      "End of Week", "This Weekend", "Next Weekend"):
            if label == "This Week":
                menu.addSeparator()
            menu.addAction(label).triggered.connect(
                lambda checked=False, value=label: self._mutate_task_from_page_editor(
                    task, {role: TaskPanel._resolve_quick_date(value, role)},
                )
            )
        menu.addSeparator()

        def pick_date():
            dialog = DateInsertDialog(self, anchor_pos=anchor_pos)
            dialog.setWindowModality(Qt.WindowModal)
            if dialog.exec() == QDialog.Accepted:
                self._mutate_task_from_page_editor(task, {role: dialog.selected_date_text()})

        menu.addAction("Date...").triggered.connect(pick_date)
        if task.get("starts" if role == "start" else "due") or task.get(role):
            menu.addSeparator()
            menu.addAction("Clear Date").triggered.connect(
                lambda: self._mutate_task_from_page_editor(task, {role: ""})
            )
        menu.exec(anchor_pos)

    def _finish_task_action(self, *, inline_status=None, persisted_content=None) -> None:
        if inline_status is not None and self.editor.apply_task_status(*inline_status):
            from sp.app.task_mutations import rewrite_task_line

            lines = (self._saved_disk_content or self._last_saved_content or "").splitlines(keepends=True)
            if persisted_content is not None:
                self._remember_saved_state(persisted_content)
            elif 0 <= inline_status[0] < len(lines):
                lines[inline_status[0]] = rewrite_task_line(lines[inline_status[0]], status=inline_status[1])
                self._remember_saved_state("".join(lines))
            else:
                self._remember_saved_state(self.editor.to_markdown())
        else:
            self._load_content()
            try:
                self._open_in_main(self._source_path, force=True, refresh_only=True)
            except Exception:
                pass
        self._update_dirty_indicator()
        self.statusBar().showMessage("Task updated", 3000)

    def _mutate_task_from_page_editor(self, task: dict, changes: dict) -> bool:
        if not self._prepare_task_action():
            return False
        payload = {"targets": [{
            "path": self._source_path, "line": task["line"],
            "expected_text": str(task.get("text") or ""),
            "expected_status": str(task.get("status") or "todo"),
        }], **changes}
        try:
            response = self._api_post("/api/tasks/mutate", payload)
            response.raise_for_status()
            result = response.json()
        except Exception as exc:
            QMessageBox.warning(self, "Task update blocked", self._task_mutation_error(exc))
            return False
        self._task_undo_id = result.get("undo_id")
        status_only = set(changes) == {"status"}
        self._task_status_undo = (task["line"] - 1, task["status"]) if status_only else None
        self._task_status_undo_content = (
            self._saved_disk_content if self._saved_disk_content is not None else self._last_saved_content
        ) if status_only else None
        self._finish_task_action(
            inline_status=(task["line"] - 1, changes["status"]) if status_only else None,
        )
        return True

    def _undo_task_action(self) -> None:
        if not self._task_undo_id or not self._prepare_task_action():
            return
        try:
            response = self._api_post(f"/api/tasks/undo/{self._task_undo_id}", {})
            response.raise_for_status()
        except Exception as exc:
            QMessageBox.warning(self, "Task update blocked", self._task_mutation_error(exc))
            return
        previous_status = self._task_status_undo
        previous_content = self._task_status_undo_content
        self._task_undo_id = None
        self._task_status_undo = None
        self._task_status_undo_content = None
        self._finish_task_action(inline_status=previous_status, persisted_content=previous_content)

    def _edit_task_from_page_editor(self, block_number: int, anchor_pos, *, focus_field=None) -> None:
        """Edit one task using state and network ownership local to this window."""
        if self._read_only:
            QMessageBox.information(self, "Edit Task", "Cannot edit tasks while this window is read-only.")
            return

        if self._is_dirty():
            self._save_current_file(auto=True, reason="edit inline task", notify_main=False)
            if self._is_dirty():
                QMessageBox.warning(self, "Edit Task", "Save the page before editing this task.")
                return

        try:
            lines = self.editor.to_markdown().splitlines()
            line_index = int(block_number)
            if line_index < 0 or line_index >= len(lines):
                raise ValueError("The selected task line is no longer available.")
            parsed = indexer.extract_tasks(self._source_path, lines[line_index])
            if not parsed:
                raise ValueError("The selected line is no longer a task.")
            task = dict(parsed[0])
            task["path"] = self._source_path
            task["line"] = line_index + 1
            task["id"] = f"{self._source_path}:{line_index + 1}"
        except Exception as exc:
            QMessageBox.warning(self, "Edit Task", str(exc))
            return

        dialog = TaskQuickEditor(
            task,
            self,
            focus_field=focus_field,
            known_tags=self._task_editor_known_tags(),
            page_search=self._task_destination_pages,
            anchor_pos=anchor_pos if isinstance(anchor_pos, QPoint) else None,
            vi_mode=bool(self._vi_enabled),
            vault_accent_color=getattr(self.editor, "_vault_accent_color", None),
        )
        # Keep this editor modal only to its detached page window. The main
        # window remains independently usable and cannot become the owner.
        dialog.setWindowModality(Qt.WindowModal)
        self._task_editor = dialog
        self.editor.push_focus_lost_suppression()
        try:
            if dialog.exec() != QDialog.Accepted:
                return
            values = dialog.values()
        except ValueError as exc:
            QMessageBox.warning(self, "Task update blocked", str(exc))
            return
        finally:
            self.editor.pop_focus_lost_suppression()
            self._task_editor = None

        changes = {
            "text": values["text"],
            "status": values["status"],
            "priority": values["priority"],
            "tags": values["tags"],
            "start": values["start"],
            "due": values["due"],
            "destination": self._normalize_task_destination(values.get("destination")),
        }
        if not self._mutate_task_from_page_editor(task, changes):
            return

        block = self.editor.document().findBlockByNumber(line_index)
        if block.isValid():
            self.editor.setTextCursor(QTextCursor(block))
            self.editor.ensureCursorVisible()
        self.statusBar().showMessage("Task updated", 3000)
        QTimer.singleShot(0, lambda: self.editor.setFocus(Qt.OtherFocusReason))

    def _apply_theme_palette(self) -> None:
        bg = theme_value("page_editor_window.base.bg", None)
        text = theme_value("page_editor_window.base.text", None)
        if bg is None and text is None:
            return
        pal = self.palette()
        base_color = pal.color(QPalette.Base)
        text_color = pal.color(QPalette.Text)
        if bg is not None:
            base_color = theme_color("page_editor_window.base.bg", bg)
            pal.setColor(QPalette.Window, base_color)
            pal.setColor(QPalette.Base, base_color)
            pal.setColor(QPalette.AlternateBase, base_color.lighter(112) if base_color.lightness() < 128 else base_color.darker(104))
            pal.setColor(QPalette.Button, base_color)
        if text is not None:
            text_color = theme_color("page_editor_window.base.text", text)
            pal.setColor(QPalette.WindowText, text_color)
            pal.setColor(QPalette.Text, text_color)
            pal.setColor(QPalette.ButtonText, text_color)
        border_color = QColor(base_color)
        border_color = border_color.lighter(170) if border_color.lightness() < 128 else border_color.darker(135)
        pal.setColor(QPalette.Mid, border_color)
        self.setPalette(pal)

    def find_replace_bar_palette(self) -> QPalette:
        editor = getattr(self, "editor", None)
        if editor is not None:
            palette_getter = getattr(editor, "find_replace_bar_palette", None)
            if callable(palette_getter):
                try:
                    palette = palette_getter()
                except Exception:
                    palette = None
                if isinstance(palette, QPalette):
                    return QPalette(palette)
        return QPalette(self.palette())

    def _selected_text_for_search(self) -> str:
        cursor = self.editor.textCursor()
        if cursor.hasSelection():
            return cursor.selectedText().replace("\u2029", "\n")
        return ""

    def _sanitize_find_query(self, text: Optional[str]) -> str:
        if not text:
            return ""
        cleaned = text.replace("\u2029", "\n")
        try:
            cleaned = re.sub(r"[\x00-\x1F\x7F]", "", cleaned)
            cleaned = re.sub(r"[\uE000-\uF8FF]", "", cleaned)
        except Exception:
            pass
        return cleaned.strip()

    def _show_find_bar(self, *, replace: bool, backwards: bool = False, seed: Optional[str] = None) -> None:
        query = seed if seed is not None else self._selected_text_for_search()
        query = self._sanitize_find_query(query)
        if not query:
            query = self.editor.last_search_query()
        query = self._sanitize_find_query(query)
        self._find_bar.show_bar(replace=replace, query=query or "", backwards=backwards)

    def _on_editor_find_requested(self, replace_mode: bool, backwards: bool, seed_query: str) -> None:
        self._show_find_bar(replace=replace_mode, backwards=backwards, seed=seed_query)

    def _on_find_next_requested(self, query: str, backwards: bool, case_sensitive: bool) -> None:
        search_query = query.strip() or self.editor.last_search_query() or self._selected_text_for_search()
        search_query = self._sanitize_find_query(search_query)
        if not search_query:
            self.statusBar().showMessage("Enter text to find.", 2000)
            self._find_bar.focus_query()
            return
        self._find_bar.query_edit.setText(search_query)
        self.editor.search_find_next(search_query, backwards=backwards, wrap=True, case_sensitive=case_sensitive)

    def _on_replace_requested(self, replacement: str) -> None:
        self.editor.search_replace_current(replacement)

    def _on_replace_all_requested(self, query: str, replacement: str, case_sensitive: bool) -> None:
        search_query = query.strip() or self.editor.last_search_query()
        if not search_query:
            self.statusBar().showMessage("Enter text to find.", 2000)
            self._find_bar.focus_query()
            return
        self.editor.search_replace_all(search_query, replacement, case_sensitive=case_sensitive)

    def _insert_date(self) -> None:
        """Show calendar/date dialog and insert selected date."""
        cursor = self.editor.textCursor()
        saved_cursor_pos = cursor.position()
        saved_anchor_pos = cursor.anchor()
        cursor_rect = self.editor.cursorRect()
        anchor = self.editor.viewport().mapToGlobal(cursor_rect.bottomRight() + QPoint(0, 4))
        dlg = DateInsertDialog(
            self,
            anchor_pos=anchor,
            accept_on_double_click=True,
            accept_on_enter=True,
            allow_nav_keys=True,
            use_vi_keys=bool(self._vi_enabled),
            keep_edit_focus=True,
        )
        result = dlg.exec()
        # Restore cursor/selection to where the user triggered the dialog
        doc_len = len(self.editor.toPlainText())
        anchor_pos = max(0, min(saved_anchor_pos, doc_len))
        cursor_pos = max(0, min(saved_cursor_pos, doc_len))
        restore_cursor = QTextCursor(self.editor.document())
        restore_cursor.setPosition(anchor_pos)
        restore_cursor.setPosition(
            cursor_pos,
            QTextCursor.KeepAnchor if anchor_pos != cursor_pos else QTextCursor.MoveAnchor,
        )
        self.editor.setTextCursor(restore_cursor)
        if result == QDialog.Accepted:
            text = dlg.selected_date_text()
            if text:
                restore_cursor.insertText(text)
                self.editor.setTextCursor(restore_cursor)
                self.statusBar().showMessage(f"Inserted date: {text}", 3000)

    def set_read_only(self, read_only: bool) -> None:
        """Toggle read-only state and refresh window badges/title."""
        self._read_only = bool(read_only)
        try:
            self.editor.set_read_only_mode(self._read_only)
        except Exception:
            try:
                self.editor.setReadOnly(self._read_only)
            except Exception:
                pass
        self._update_title()
        self._update_dirty_indicator()

    def _size_and_center(self, parent=None) -> None:
        """Size the popup similar to the parent editor and center it."""
        try:
            if parent and hasattr(parent, "size"):
                self.resize(parent.size())
        except Exception:
            pass
        screen = self.screen()
        if screen:
            geo = screen.availableGeometry()
            win_size = self.size()
            x = geo.x() + (geo.width() - win_size.width()) // 2
            y = geo.y() + (geo.height() - win_size.height()) // 2
            self.move(x, y)

    def _restore_geometry(self) -> None:
        """Restore saved geometry if available."""
        try:
            geom = config.load_popup_editor_geometry()
            if geom:
                self.restoreGeometry(QByteArray.fromBase64(geom.encode("ascii")))
        except Exception:
            pass

    def _save_geometry(self) -> None:
        """Persist current geometry to the vault config."""
        try:
            geom = self.saveGeometry().toBase64().data().decode("ascii")
            config.save_popup_editor_geometry(geom)
        except Exception:
            pass

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("Page")
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonIconOnly)
        toolbar.setIconSize(QSize(16, 16))
        assets_root = Path(__file__).resolve().parents[2] / "assets"
        modifier = primary_modifier_label()
        save_action = QAction("Save", self)
        save_action.setShortcut(QKeySequence.Save)
        save_action.setShortcutContext(Qt.WindowShortcut)
        save_action.setToolTip(f"Save ({modifier}+S)")
        self._save_action = save_action
        save_icon_path = assets_root / "save.svg"
        if save_icon_path.exists():
            icon = self._load_tinted_icon(save_icon_path, size=16)
            if icon:
                save_action.setIcon(icon)
        save_action.triggered.connect(lambda: self._save_current_file(auto=False, reason="manual save"))
        toolbar.addAction(save_action)
        self.addAction(save_action)  # Register shortcut with window

        reload_action = QAction("Reload", self)
        reload_action.setShortcut(QKeySequence("Ctrl+R"))
        reload_action.setShortcutContext(Qt.WindowShortcut)
        reload_action.setToolTip(f"Reload current page ({modifier}+R)")
        self._reload_action = reload_action
        reload_icon_path = assets_root / "reload.svg"
        if reload_icon_path.exists():
            icon = self._load_tinted_icon(reload_icon_path, size=16)
            if icon:
                reload_action.setIcon(icon)
        reload_action.triggered.connect(self._reload_current_page)
        toolbar.addAction(reload_action)
        self.addAction(reload_action)

        insert_link_action = QAction("Insert Link…", self)
        insert_link_action.setShortcut(QKeySequence("Ctrl+L"))
        insert_link_action.setShortcutContext(Qt.WindowShortcut)
        self._insert_link_action = insert_link_action
        insert_link_action.setToolTip(f"Insert a link to another page ({modifier}+L)")
        link_icon_path = assets_root / "link.svg"
        if link_icon_path.exists():
            icon = self._load_tinted_icon(link_icon_path, size=16)
            if icon:
                insert_link_action.setIcon(icon)
        insert_link_action.triggered.connect(self._insert_link)
        toolbar.addAction(insert_link_action)
        self.addAction(insert_link_action)
        toolbar.addSeparator()
        ai_action = QAction("AI Assist...", self)
        ai_action.setToolTip("AI assist (one-shot)")
        icon_path = assets_root / "ai.svg"
        if icon_path.exists():
            icon = self._load_tinted_icon(icon_path, size=16)
            if icon:
                ai_action.setIcon(icon)
        ai_action.triggered.connect(self._open_ai_assist)
        toolbar.addAction(ai_action)
        font_up = QAction("A+", self)
        font_up.setToolTip(f"Increase font size ({modifier}++ or {modifier}+mouse wheel)")
        font_up.triggered.connect(lambda: self._adjust_font_size(1))
        font_down = QAction("A-", self)
        font_down.setToolTip(f"Decrease font size ({modifier}+- or {modifier}+mouse wheel)")
        font_down.triggered.connect(lambda: self._adjust_font_size(-1))
        toolbar.addAction(font_down)
        toolbar.addAction(font_up)

        self.addToolBar(Qt.TopToolBarArea, toolbar)

    def _icon_tint_color(self) -> QColor:
        pal = QApplication.palette()
        bg = pal.color(QPalette.Window)
        if bg.lightness() > 128:
            return QColor(0, 0, 0)
        return QColor(255, 255, 255)

    def _load_tinted_icon(self, path: Path, size: int = 16) -> Optional[QIcon]:
        if not path.exists():
            return None
        icon = QIcon(str(path))
        pm = icon.pixmap(size, size)
        if pm.isNull():
            return icon
        colored = QPixmap(pm.size())
        colored.fill(Qt.transparent)
        painter = QPainter(colored)
        painter.drawPixmap(0, 0, pm)
        painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
        painter.fillRect(colored.rect(), self._icon_tint_color())
        painter.end()
        return QIcon(colored)

    def _load_content(self) -> None:
        tracer = PageLoadLogger(self._source_path) if PAGE_LOGGING_ENABLED else None
        if tracer:
            tracer.mark("api read start")
        _page_editor_log(f"[StillPoint Popup] Read request path={self._source_path}")
        try:
            resp = self._api_post("/api/file/read", {"path": self._source_path})
            resp.raise_for_status()
            content = resp.json().get("content", "")
            if tracer:
                try:
                    content_len = len(content.encode("utf-8"))
                except Exception:
                    content_len = len(content or "")
                tracer.mark(f"api read complete bytes={content_len}")
            try:
                self.editor.set_page_load_logger(tracer)
            except Exception:
                pass
            self.editor.set_markdown(content)
            if tracer:
                tracer.mark("editor content applied")
            self._remember_saved_state(content)
            self.statusBar().showMessage("Ready")
            if tracer:
                tracer.end("ready for edit (popup)")
        except httpx.HTTPError as exc:
            if tracer:
                tracer.mark(f"api read failed ({exc})")
            QMessageBox.critical(self, "Error", f"Failed to load page: {exc}")

    def _update_title(self) -> None:
        label = Path(self._source_path).name or self._source_path
        suffix = "StillPoint Editor"
        h1 = self._first_h1_title()
        if h1 and h1 != Path(label).stem:
            label = f"{label} | {h1}"
        if self._read_only:
            self.setWindowTitle(f"{label} | Read-Only | {suffix}")
        else:
            self.setWindowTitle(f"{label} | {suffix}")
        self._update_dirty_indicator()

    def _is_dirty(self) -> bool:
        current = self.editor.to_markdown()
        return current != (self._last_saved_content or "")

    def _on_buffer_changed(self) -> None:
        self._update_dirty_indicator()
        self._autosave_timer.start()

    def _remember_saved_state(self, content: str) -> None:
        self._saved_disk_content = content
        self._saved_content_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
        self._last_saved_content = self.editor.to_markdown()
        self.editor.document().setModified(False)
        self._autosave_timer.stop()
        self._update_dirty_indicator()

    def _refresh_saved_state(self) -> None:
        """Refresh a clean buffer on activation; preserve unsaved local edits."""
        if self._refreshing_saved_state or self._saved_content_sha256 is None or self.property("_sp_closing"):
            return
        self._refreshing_saved_state = True
        try:
            response = self._api_post("/api/file/read", {"path": self._source_path})
            response.raise_for_status()
            content = response.json()["content"]
            digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
            if digest == self._saved_content_sha256:
                self._update_dirty_indicator()
                return
            if self._is_dirty() and content != self.editor.to_markdown():
                self.statusBar().showMessage("This page changed in another window. Your unsaved edits are preserved; save to compare changes.")
                return
            cursor = self.editor.textCursor()
            position, anchor = cursor.position(), cursor.anchor()
            scroll = self.editor.verticalScrollBar().value()
            if content != self.editor.to_markdown():
                self.editor.set_markdown(content)
                cursor = self.editor.textCursor()
                limit = self.editor.document().characterCount() - 1
                cursor.setPosition(min(anchor, limit))
                cursor.setPosition(min(position, limit), QTextCursor.KeepAnchor)
                self.editor.setTextCursor(cursor)
                self.editor.verticalScrollBar().setValue(scroll)
            self._remember_saved_state(content)
            self.statusBar().showMessage("Up to date · All changes saved", 3000)
        except httpx.HTTPError as exc:
            self.statusBar().showMessage(f"Could not check the saved page: {exc}", 5000)
        finally:
            self._refreshing_saved_state = False

    def event(self, event):
        result = super().event(event)
        if event.type() == QEvent.WindowActivate and getattr(self, "_saved_content_sha256", None) is not None:
            QTimer.singleShot(0, self._refresh_saved_state)
        return result

    def _ensure_writable(self, auto: bool) -> bool:
        if not self._read_only:
            return True
        if auto:
            return False
        QMessageBox.warning(
            self,
            "Read-Only",
            "This page is open in read-only mode.\nTo save changes, enable write access in the main window.",
        )
        return False

    def _save_current_file(self, auto: bool = False, reason: str = "save", *, notify_main: bool = True) -> None:
        if not self._is_dirty():
            return
        if not self._ensure_writable(auto):
            return
        payload = {"path": self._source_path, "content": self.editor.to_markdown()}
        try:
            payload_bytes = len(payload["content"].encode("utf-8"))
        except Exception:
            payload_bytes = len(payload["content"] or "")
        mode = "auto" if auto else "manual"
        reason_label = reason or "save"
        try:
            rel = Path(self._source_path.lstrip("/"))
            if len(rel.parts) == 1 and rel.suffix.lower() in PAGE_SUFFIXES:
                trace = "".join(traceback.format_stack(limit=12))
                _page_editor_log(
                    f"[StillPoint Popup] Invalid root write requested path={self._source_path} reason={reason_label}\n{trace}"
                )
        except Exception:
            pass
        _page_editor_log(
            f"[StillPoint Popup] Write request reason={reason_label} mode={mode} path={self._source_path} "
            f"bytes={payload_bytes}"
        )
        try:
            headers = {"If-Match": f"sha256:{self._saved_content_sha256}"} if self._saved_content_sha256 else None
            resp = self._api_post("/api/file/write", payload, headers=headers) if headers else self._api_post("/api/file/write", payload)
            if resp.status_code == 409:
                detail = resp.json().get("detail", {})
                remote_content = detail.get("current_content") if isinstance(detail, dict) else None
                if remote_content == payload["content"]:
                    self._remember_saved_state(remote_content)
                    self.statusBar().showMessage("All changes saved", 3000)
                    return
                if auto or remote_content is None:
                    self.statusBar().showMessage("Save paused: the page changed in another window. Your edits are preserved; save to compare changes.")
                    return
                from .merge_conflict_dialog import MergeConflictDialog

                dialog = MergeConflictDialog(payload["content"], remote_content, self._source_path, parent=self)
                dialog.setWindowModality(Qt.WindowModal)
                if dialog.exec() != QDialog.Accepted or dialog.merged_text() is None:
                    return
                payload["content"] = dialog.merged_text()
                digest = hashlib.sha256(remote_content.encode("utf-8")).hexdigest()
                resp = self._api_post("/api/file/write", payload, headers={"If-Match": f"sha256:{digest}"})
            resp.raise_for_status()
            _page_editor_log(f"[StillPoint Popup] Write OK {self._source_path} status={resp.status_code}")
        except httpx.HTTPError as exc:
            try:
                body = exc.response.text if exc.response else str(exc)
                status = exc.response.status_code if exc.response else "n/a"
                _page_editor_log(f"[StillPoint Popup] Write FAILED {self._source_path} status={status} body={body}")
            except Exception:
                _page_editor_log(f"[StillPoint Popup] Write FAILED {self._source_path}: {exc}")
            if not auto:
                QMessageBox.critical(self, "Save Failed", f"Failed to save: {exc}")
            return
        if payload["content"] != self.editor.to_markdown():
            self.editor.set_markdown(payload["content"])
        self._remember_saved_state(payload["content"])
        self.statusBar().showMessage("Saved", 2000)
        # Notify parent/main window to refresh if editing the same page
        try:
            if notify_main and hasattr(self._open_in_main, "__call__"):
                self._open_in_main(self._source_path, force=True, refresh_only=True)
        except Exception:
            pass
        self._update_dirty_indicator()

    def _on_application_state_changed(self, state) -> None:
        """Persist detached editor content when the application deactivates."""
        try:
            inactive = state == Qt.ApplicationState.ApplicationInactive
        except Exception:
            inactive = False
        if not inactive:
            return
        if self._is_dirty():
            self._save_current_file(auto=True, reason="application deactivated")

    def _reload_current_page(self) -> None:
        """Reload the current page from disk."""
        if not self._source_path:
            self.statusBar().showMessage("No page to reload", 2000)
            return
        _page_editor_log(f"[StillPoint Popup] Reload request path={self._source_path}")
        if self._is_dirty():
            self._save_current_file(auto=True, reason="reload")
            if self._is_dirty():
                return
        self._load_content()
        self.statusBar().showMessage("Reloaded current page", 2000)

    def _insert_link(self) -> None:
        if not self.vault_root:
            QMessageBox.information(self, "StillPoint", "Select a vault before inserting links.")
            return
        if self._read_only:
            QMessageBox.information(self, "StillPoint", "Cannot insert links while read-only.")
            return

        editor_cursor = self.editor.textCursor()
        saved_cursor_pos = editor_cursor.position()
        saved_anchor_pos = editor_cursor.anchor()

        selection_range: tuple[int, int] | None = None
        selected_text = ""
        if editor_cursor.hasSelection():
            selection_range = (editor_cursor.selectionStart(), editor_cursor.selectionEnd())
            selected_text = editor_cursor.selectedText()
            selected_text = selected_text.replace('\u2029', ' ').replace('\n', ' ').replace('\r', ' ').strip()
        trace_link_decision(
            "sp/app/ui/page_editor_window.py:_insert_link:selection_state",
            has_selection=editor_cursor.hasSelection(),
            selection_range=selection_range,
            selected_text=selected_text,
            current_path=getattr(self.editor, "_current_path", None),
        )

        def _restore_cursor() -> QTextCursor:
            doc_len = len(self.editor.toPlainText())
            anchor = max(0, min(saved_anchor_pos, doc_len))
            pos = max(0, min(saved_cursor_pos, doc_len))
            cursor = QTextCursor(self.editor.document())
            cursor.setPosition(anchor)
            cursor.setPosition(
                pos,
                QTextCursor.KeepAnchor if anchor != pos else QTextCursor.MoveAnchor,
            )
            self.editor.setTextCursor(cursor)
            return cursor

        dlg = InsertLinkDialog(self, selected_text=selected_text)
        self.editor.begin_dialog_block()
        try:
            result = dlg.exec()
        finally:
            self.editor.end_dialog_block()
            _restore_cursor()
            QTimer.singleShot(0, self.editor.setFocus)

        if result == QDialog.Accepted:
            restore_cursor = _restore_cursor()
            colon_path = dlg.selected_colon_path()
            link_name = dlg.selected_link_name()
            trace_link_decision(
                "sp/app/ui/page_editor_window.py:_insert_link:dialog_result",
                colon_path=colon_path,
                link_name=link_name,
                selected_text=selected_text,
                current_path=getattr(self.editor, "_current_path", None),
            )
            if colon_path:
                if selection_range:
                    doc_len = len(self.editor.toPlainText())
                    start = max(0, min(selection_range[0], doc_len))
                    end = max(0, min(selection_range[1], doc_len))
                    restore_cursor.setPosition(start)
                    restore_cursor.setPosition(end, QTextCursor.KeepAnchor)
                    restore_cursor.removeSelectedText()
                self.editor.setTextCursor(restore_cursor)
                label = link_name or selected_text or colon_path
                if should_use_full_target_label(colon_path, label):
                    label = colon_path
                trace_link_decision(
                    "sp/app/ui/page_editor_window.py:_insert_link:before_insert",
                    colon_path=colon_path,
                    link_name=link_name,
                    selected_text=selected_text,
                    final_label=label,
                )
                self.editor.insert_link(
                    colon_path,
                    label,
                    surround_with_spaces=selection_range is None,
                )

    def _forward_link_to_main(self, link: str) -> None:
        if link:
            self._open_in_main(link)

    def _update_dirty_indicator(self) -> None:
        if not hasattr(self, "_dirty_status_label"):
            return
        if self._read_only:
            self._dirty_status_label.setText("O/")
            self._dirty_status_label.setStyleSheet(
                self._badge_base_style
                + " background-color: "
                f"{theme_value('page_editor_window.badge.readonly_bg', '#9e9e9e')}; "
                "color: "
                f"{theme_value('page_editor_window.badge.readonly_text', '#f5f5f5')}; "
                "margin-right: 6px; text-decoration: line-through;"
            )
            self._dirty_status_label.setToolTip("Read-only: changes cannot be saved in this window")
            return
        dirty = self._is_dirty()
        if dirty:
            self._dirty_status_label.setText("●")
            self._dirty_status_label.setStyleSheet(
                self._badge_base_style
                + " background-color: "
                f"{theme_value('page_editor_window.badge.dirty_bg', '#e57373')}; "
                "color: "
                f"{theme_value('page_editor_window.badge.dirty_text', '#000000')}; "
                "margin-right: 6px;"
            )
            self._dirty_status_label.setToolTip("Unsaved changes")
        else:
            self._dirty_status_label.setText("●")
            self._dirty_status_label.setStyleSheet(
                self._badge_base_style
                + " background-color: "
                f"{theme_value('page_editor_window.badge.clean_bg', '#81c784')}; "
                "color: "
                f"{theme_value('page_editor_window.badge.clean_text', '#000000')}; "
                "margin-right: 6px;"
            )
            self._dirty_status_label.setToolTip("All changes saved")

    def _adjust_font_size(self, delta: int) -> None:
        new_size = max(6, min(24, self._font_size + delta))
        if new_size == self._font_size:
            return
        self._font_size = new_size
        self.editor.set_font_point_size(self._font_size)
        try:
            config.save_popup_font_size(self._font_size)
        except Exception:
            pass

    def _open_ai_assist(self) -> None:
        if not config.load_enable_ai_chats():
            self.statusBar().showMessage("Enable AI Chats in Preferences to use AI assist.", 4000)
            return
        cursor = self.editor.textCursor()
        selection_text = ""
        has_selection = cursor.hasSelection()
        if has_selection:
            selection_text = cursor.selectedText().replace("\u2029", "\n").strip()
        try:
            from .ai_chat_panel import ServerManager
        except Exception:
            self.statusBar().showMessage("AI worker unavailable.", 4000)
            return
        try:
            from .one_shot_overlay import OneShotPromptOverlay
        except Exception:
            self.statusBar().showMessage("One-Shot overlay unavailable.", 4000)
            return

        server_config: dict = {}
        try:
            default_server_name = config.load_default_ai_server()
        except Exception:
            default_server_name = None
        try:
            server_mgr = ServerManager()
            if default_server_name:
                server_cfg = server_mgr.get_server(default_server_name)
                if server_cfg:
                    server_config = server_cfg
        except Exception:
            server_config = {}
        if not server_config:
            self.statusBar().showMessage("No AI server configured.", 4000)
            return

        try:
            default_model_name = config.load_default_ai_model()
        except Exception:
            default_model_name = None
        model = default_model_name or server_config.get("default_model") or "gpt-3.5-turbo"

        doc = self.editor.document()
        start_pos = cursor.selectionStart() if has_selection else cursor.position()
        end_pos = cursor.selectionEnd() if has_selection else cursor.position()

        def _accept_insert(assistant_text: str) -> None:
            try:
                replace_cursor = QTextCursor(doc)
                replace_cursor.setPosition(start_pos)
                replace_cursor.setPosition(end_pos, QTextCursor.KeepAnchor)
                replace_cursor.beginEditBlock()
                if start_pos != end_pos:
                    replace_cursor.removeSelectedText()
                replace_cursor.insertText(assistant_text)
                replace_cursor.endEditBlock()
                self.editor.setFocus()
            except Exception:
                pass

        system_prompt = _load_one_shot_prompt()
        overlay = OneShotPromptOverlay(
            parent=self,
            server_config=server_config,
            model=model,
            system_prompt=system_prompt,
            on_accept=_accept_insert,
        )
        try:
            self._one_shot_overlay = overlay
        except Exception:
            pass
        try:
            self.editor.push_focus_lost_suppression()
        except Exception:
            try:
                setattr(self.editor, "_suppress_focus_lost_once", True)
            except Exception:
                pass

        def _overlay_cleanup() -> None:
            try:
                self.editor.pop_focus_lost_suppression()
            except Exception:
                pass
            try:
                setattr(self, "_one_shot_overlay", None)
            except Exception:
                pass

        try:
            overlay.finished.connect(lambda *_: _overlay_cleanup())
        except Exception:
            pass
        try:
            geo = self.geometry()
            overlay.move(geo.center() - overlay.rect().center())
        except Exception:
            pass
        if selection_text:
            overlay.open_with_selection(selection_text)
        else:
            overlay.show()


    def _show_editor_context_menu(self, pos) -> None:
        """Show context menu with Edit operations matching main window."""
        from PySide6.QtWidgets import QMenu

        image_hit = None
        try:
            image_hit = self.editor._image_at_position(pos)
        except Exception:
            image_hit = None
        if image_hit:
            _, fmt = image_hit
            image_name = fmt.name()
            menu = QMenu(self)
            apply_menu_theme(menu, self.editor)
            for width in MARKDOWN_IMAGE_WIDTH_OPTIONS:
                action = menu.addAction(f"{width}px")
                action.triggered.connect(
                    lambda checked=False, w=width, name=image_name: self.editor._resize_image_by_name(name, w)
                )
            menu.addSeparator()
            reset_action = menu.addAction("Original Size")
            reset_action.triggered.connect(
                lambda checked=False, name=image_name: self.editor._resize_image_by_name(name, None)
            )
            menu.addSeparator()
            custom_action = menu.addAction("Custom…")
            custom_action.triggered.connect(
                lambda checked=False, name=image_name: self.editor._prompt_image_width_by_name(name)
            )
            menu.addSeparator()
            ocr_action = menu.addAction("OCR Text Extract...")
            ocr_action.triggered.connect(
                lambda checked=False, image_pos=image_hit[0].position(): self.editor.request_inline_image_ocr_at_position(image_pos)
            )
            menu.exec(self.editor.mapToGlobal(pos))
            return

        # Get standard context menu (Undo, Redo, Cut, Copy, Paste, Delete, Select All)
        base_menu = self.editor.createStandardContextMenu()
        
        # Install custom copy actions (sanitized Copy + Copy As Markdown)
        try:
            if hasattr(self.editor, '_install_copy_actions'):
                self.editor._install_copy_actions(base_menu)
        except Exception:
            pass

        click_cursor = self.editor.cursorForPosition(pos)
        self.editor._add_task_actions_menu(
            base_menu, click_cursor, self.editor.viewport().mapToGlobal(pos),
        )
        heading_text = None
        try:
            heading_text = self.editor._heading_text_for_cursor(click_cursor)
        except Exception:
            heading_text = None
        if heading_text:
            copy_action = QAction("Copy link to clipboard", self)
            copy_action.triggered.connect(
                lambda checked=False, text=heading_text: self.editor._copy_link_to_location(
                    link_text=None,
                    anchor_text=text,
                )
            )
            actions = base_menu.actions()
            if actions:
                base_menu.insertAction(actions[0], copy_action)
            else:
                base_menu.addAction(copy_action)

        insert_link_action = QAction("Insert link…", self)
        insert_link_action.triggered.connect(self._insert_link)
        base_menu.addSeparator()
        base_menu.addAction(insert_link_action)

        ai_action = QAction("AI assist...", self)
        ai_action.triggered.connect(self._open_ai_assist)
        base_menu.addAction(ai_action)
        
        # Show the menu
        base_menu.exec(self.editor.mapToGlobal(pos))

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._position_toc_widget()
        if hasattr(self, "_geometry_timer"):
            self._geometry_timer.start()

    def moveEvent(self, event) -> None:  # type: ignore[override]
        super().moveEvent(event)
        if hasattr(self, "_geometry_timer"):
            self._geometry_timer.start()

    def _on_headings_changed(self, headings: list[dict]) -> None:
        """Store headings when editor parses them."""
        self._toc_headings = list(headings or [])
        _page_editor_log(f"[PageEditor] Headings changed: {len(self._toc_headings)} headings")
        self._update_title()
        self._update_toc_visibility(force=True)
        self._position_toc_widget()

    def _refresh_toc_preferences(self) -> None:
        self.toc_widget.set_vault_accent_color(config.load_vault_accent_color())
        self._update_toc_visibility(force=True)
        self._position_toc_widget()

    def _on_toc_collapsed_changed(self, collapsed: bool) -> None:
        config.save_page_editor_toc_collapsed(collapsed)
        self._position_toc_widget()

    def _update_toc_visibility(self, force: bool = False) -> None:
        widget = getattr(self, "toc_widget", None)
        if widget is None:
            return
        enabled = config.load_toc_widget_enabled() and config.load_page_editor_toc_widget_enabled()
        scrollable = self.editor.verticalScrollBar().maximum() > 0
        if not enabled or not scrollable or len(self._toc_headings) <= 1:
            widget.hide()
            return
        if force or not widget.isVisible():
            widget.set_headings(self._toc_headings)
            widget.show()

    def _position_toc_widget(self, *args) -> None:
        widget = getattr(self, "toc_widget", None)
        if widget is None:
            return
        self._update_toc_visibility()
        viewport = self.editor.viewport()
        top_left = self.mapFromGlobal(viewport.mapToGlobal(viewport.rect().topLeft()))
        widget.move(max(12, top_left.x() + viewport.width() - widget.width() - 12), max(12, top_left.y() + 12))
        widget.raise_()

    def _toc_jump_to_position(self, position: int) -> None:
        if self._toc_scroll_anim is not None:
            self._toc_scroll_anim.stop()
            self._toc_scroll_anim.deleteLater()
            self._toc_scroll_anim = None
        cursor = QTextCursor(self.editor.document())
        cursor.setPosition(max(0, min(position, self.editor.document().characterCount() - 1)))
        self.editor.setTextCursor(cursor)
        scrollbar = self.editor.verticalScrollBar()
        target = scrollbar.value() + self.editor.cursorRect(cursor).top() - int(self.editor.viewport().height() * 0.25)
        target = max(0, min(target, scrollbar.maximum()))
        load_token = self.editor.current_load_token()

        def finish():
            if self.editor.current_load_token() != load_token:
                return
            self.editor.setTextCursor(cursor)
            self.editor.ensureCursorVisible()
            self._flash_heading(cursor)
            self.editor.setFocus(Qt.OtherFocusReason)

        delta = abs(target - scrollbar.value())
        if delta <= 2:
            scrollbar.setValue(target)
            finish()
            return
        animation = QPropertyAnimation(scrollbar, b"value", self)
        animation.setDuration(min(150, max(60, delta)))
        animation.setStartValue(scrollbar.value())
        animation.setEndValue(target)
        animation.finished.connect(finish)
        self._toc_scroll_anim = animation
        animation.start()

    def _first_h1_title(self) -> Optional[str]:
        for heading in self._toc_headings:
            if not heading:
                continue
            try:
                level = int(heading.get("level", 0))
            except Exception:
                level = 0
            if level == 1:
                title = (heading.get("title") or "").strip()
                if title:
                    return title
                break
        return None

    def _handle_heading_picker_request(self, global_point, prefer_above: bool) -> None:
        """Handle Ctrl+Alt+T heading picker request from editor - show filterable picker."""
        _page_editor_log("[PageEditor] _handle_heading_picker_request called")
        self._show_filterable_heading_picker(global_point, prefer_above)

    def _request_heading_picker_popup(self) -> None:
        _page_editor_log("[PageEditor] Explicit heading picker shortcut triggered")
        cursor_rect = self.editor.cursorRect()
        viewport = self.editor.viewport()
        prefer_above = False
        try:
            prefer_above = cursor_rect.center().y() > (viewport.height() // 2)
        except Exception:
            prefer_above = False
        global_point = viewport.mapToGlobal(cursor_rect.bottomLeft())
        self._show_filterable_heading_picker(global_point, prefer_above)

    def _show_filterable_heading_picker(self, global_pos, prefer_above: bool = False) -> None:
        """Show a filterable heading picker near the cursor (vi 't')."""
        headings = self._toc_headings or []
        if not headings:
            _page_editor_log("[PageEditor] No headings to show")
            return
        is_windows = sys.platform.startswith("win")
        line_edit_padding = "1px 5px" if is_windows else "4px 6px"
        item_padding = "1px 5px" if is_windows else "4px 6px"
        layout_margins = (8, 5, 8, 5) if is_windows else (12, 8, 12, 8)
        layout_spacing = 3 if is_windows else 6
        hr_height = 1 if is_windows else 3
        hr_margin = "0 5px" if is_windows else "0 8px"
        selected_bg = theme_value(
            "page_editor_window.picker_popup.list_selected_bg",
            "rgba(90,161,255,80)",
        )
        try:
            accent = config.load_vault_accent_color()
            if accent:
                color = QColor(accent)
                if color.isValid():
                    selected_bg = color.name()
        except Exception:
            pass
        # Dispose any existing picker
        if hasattr(self, "_heading_picker") and self._heading_picker:
            try:
                self._heading_picker.close()
            except Exception:
                pass
        popup = QWidget(self, Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        popup.setStyleSheet(
            "QWidget { background: "
            f"{theme_value('page_editor_window.picker_popup.bg', 'rgba(32,32,32,240)')}; "
            "border: 1px solid "
            f"{theme_value('page_editor_window.picker_popup.border', '#666666')}; "
            "border-radius: 6px; }"
            "QLabel { border: none; font-weight: bold; }"
            "QLineEdit { border: 1px solid "
            f"{theme_value('page_editor_window.picker_popup.input_border', '#777777')}; "
            f"border-radius: 4px; padding: {line_edit_padding}; min-height: 0px; }}"
            "QListWidget { background: transparent; color: "
            f"{theme_value('page_editor_window.picker_popup.list_text', '#f5f5f5')}; "
            "border: none; }}"
            f"QListWidget::item {{ padding: {item_padding}; }}"
            "QListWidget::item:selected { background: "
            f"{selected_bg}; }}"
            "QListWidget::item:selected:active { background: "
            f"{selected_bg}; }}"
            "QListWidget::item:selected:!active { background: "
            f"{selected_bg}; }}"
        )
        layout = QVBoxLayout(popup)
        layout.setContentsMargins(*layout_margins)
        layout.setSpacing(layout_spacing)
        title = QLabel("Headings", popup)
        if is_windows:
            title.setContentsMargins(0, 0, 0, 0)
            title.setStyleSheet("padding: 0px;")
        filter_edit = QLineEdit(popup)
        filter_edit.setPlaceholderText("Filter headings…")
        list_widget = QListWidget(popup)
        list_widget.setSpacing(0)
        layout.addWidget(title)
        layout.addWidget(filter_edit)
        layout.addWidget(list_widget, 1)

        hr_line_color = theme_value("markdown_editor.syntax.hr_line", "#60656f")

        def populate(query: str = "") -> None:
            list_widget.clear()
            needle = query.lower().strip()
            # Two-pass: collect visible items, then render with HR dedup
            visible: list[dict] = []
            for h in headings:
                if h.get("type") == "hr":
                    visible.append(h)
                    continue
                title = h.get("title") or "(heading)"
                if needle and needle not in title.lower():
                    continue
                visible.append(h)
            # Strip leading/trailing HRs and collapse consecutive HRs
            heading_since_last_hr = False
            pending_hr = False
            for h in visible:
                if h.get("type") == "hr":
                    if heading_since_last_hr:
                        pending_hr = True
                    continue
                # Flush a pending HR divider before this heading
                if pending_hr:
                    item = QListWidgetItem()
                    item.setFlags(Qt.NoItemFlags)
                    item.setSizeHint(QSize(0, hr_height))
                    list_widget.addItem(item)
                    line_frame = QFrame()
                    line_frame.setFrameShape(QFrame.HLine)
                    line_frame.setFixedHeight(hr_height)
                    line_frame.setStyleSheet(f"color: {hr_line_color}; margin: {hr_margin};")
                    list_widget.setItemWidget(item, line_frame)
                    pending_hr = False
                title = h.get("title") or "(heading)"
                line = h.get("line", 1)
                level = max(1, min(5, int(h.get("level", 1))))
                indent = "    " * (level - 1)
                item = QListWidgetItem(f"{indent}{title}  (line {line})")
                item.setData(Qt.UserRole, h)
                list_widget.addItem(item)
                heading_since_last_hr = True
            if list_widget.count():
                list_widget.setCurrentRow(0)

        def activate_current() -> None:
            item = list_widget.currentItem()
            if not item:
                popup.close()
                return
            data = item.data(Qt.UserRole) or {}
            try:
                pos = int(data.get("position", 0))
            except Exception:
                pos = 0
            if pos <= 0:
                try:
                    line = int(data.get("line", 1))
                except Exception:
                    line = 1
                block = self.editor.document().findBlockByNumber(max(0, line - 1))
                if block.isValid():
                    pos = block.position()
            cursor = self.editor.textCursor()
            cursor.setPosition(max(0, pos))
            self.editor.setTextCursor(cursor)
            self.editor.ensureCursorVisible()
            popup.close()
            QTimer.singleShot(0, lambda: self.editor.setFocus(Qt.OtherFocusReason))

        filter_edit.textChanged.connect(populate)
        list_widget.itemDoubleClicked.connect(lambda *_: activate_current())
        list_widget.itemActivated.connect(lambda *_: activate_current())

        editor_ref = self.editor

        class _PickerFilter(QObject):
            @staticmethod
            def _next_selectable(start: int, delta: int) -> int:
                """Find next selectable row, skipping HR dividers."""
                row = start + delta
                count = list_widget.count()
                while 0 <= row < count:
                    item = list_widget.item(row)
                    if item and item.flags() != Qt.NoItemFlags:
                        return row
                    row += delta
                return start

            def eventFilter(self, obj, ev):  # type: ignore[override]
                if ev.type() == QEvent.KeyPress:
                    modifiers = ev.modifiers() & ~Qt.KeypadModifier
                    if ev.key() in (Qt.Key_Return, Qt.Key_Enter):
                        activate_current()
                        return True
                    if ev.key() == Qt.Key_Down and modifiers == Qt.NoModifier:
                        row = self._next_selectable(list_widget.currentRow(), 1)
                        list_widget.setCurrentRow(row)
                        return True
                    if ev.key() == Qt.Key_Up and modifiers == Qt.NoModifier:
                        row = self._next_selectable(list_widget.currentRow(), -1)
                        list_widget.setCurrentRow(row)
                        return True
                    if is_vi_navigation_chord(modifiers):
                        if ev.key() == Qt.Key_J:
                            row = self._next_selectable(list_widget.currentRow(), 1)
                            list_widget.setCurrentRow(row)
                            return True
                        if ev.key() == Qt.Key_K:
                            row = self._next_selectable(list_widget.currentRow(), -1)
                            list_widget.setCurrentRow(row)
                            return True
                    if ev.key() == Qt.Key_Escape:
                        popup.close()
                        if editor_ref:
                            QTimer.singleShot(0, lambda: editor_ref.setFocus(Qt.OtherFocusReason))
                        return True
                return False

        filt = _PickerFilter(popup)
        filter_edit.installEventFilter(filt)
        list_widget.installEventFilter(filt)
        populate("")

        editor_rect = self.editor.rect()
        popup_width = min(max(420, int(editor_rect.width() * 0.65)), max(420, editor_rect.width() - 40))
        popup_height = max(260, int(editor_rect.height() * 0.65))
        center = self.editor.mapToGlobal(editor_rect.center())
        screen = popup_available_geometry(anchor=global_pos, parent=self.editor)
        desired = QPoint(center.x() - popup_width // 2, center.y() - popup_height // 2)
        top_left = clamp_popup_top_left(desired, QSize(popup_width, popup_height), screen)
        popup.resize(popup_width, popup_height)
        popup.move(top_left)
        popup.show()
        popup.raise_()
        popup.activateWindow()
        filter_edit.setFocus()
        self._heading_picker = popup
        _page_editor_log(f"[PageEditor] Filterable picker shown with {list_widget.count()} headings")

    def _heading_popup_candidates(self) -> list[dict]:
        """Return headings for current page (excluding horizontal rules)."""
        return [h for h in self._toc_headings if h and h.get("type") != "hr"]

    def _ensure_heading_popup(self) -> None:
        """Create heading popup widget if it doesn't exist."""
        if self._heading_popup is None:
            popup = QWidget(self, Qt.Tool | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint | Qt.WindowStaysOnTopHint)
            popup.setStyleSheet(
                "background: "
                f"{theme_value('page_editor_window.heading_popup.bg', 'rgba(30,30,30,220)')}; "
                "border: 1px solid "
                f"{theme_value('page_editor_window.heading_popup.border', '#888888')}; "
                "border-radius: 6px; padding: 8px;"
            )
            layout = QVBoxLayout(popup)
            layout.setContentsMargins(12, 8, 12, 8)
            self._heading_popup_label = QLabel(popup)
            self._heading_popup_label.setStyleSheet(
                "color: "
                f"{theme_value('page_editor_window.heading_popup.label_text', '#f5f5f5')}; "
                "font-weight: bold;"
            )
            layout.addWidget(self._heading_popup_label)
            self._heading_popup_list = QListWidget(popup)
            self._heading_popup_list.setStyleSheet(
                "QListWidget { background: transparent; color: "
                f"{theme_value('page_editor_window.heading_popup.list_text', '#f5f5f5')}; "
                "border: none; }}"
                "QListWidget::item { padding: 4px 6px; }"
                "QListWidget::item:selected { background: "
                f"{theme_value('page_editor_window.heading_popup.list_selected_bg', 'rgba(255,255,255,40)')}; }}"
            )
            layout.addWidget(self._heading_popup_list)
            self._heading_popup_list.itemActivated.connect(self._activate_heading_popup_selection)
            self._heading_popup = popup

    def _show_heading_popup(self) -> None:
        """Display the heading popup with current items and selection."""
        _page_editor_log("[PageEditor] _show_heading_popup called")
        self._ensure_heading_popup()
        if not self._heading_popup or not self._heading_popup_label or not self._heading_popup_list:
            _page_editor_log("[PageEditor] Popup widgets not initialized properly")
            return
        self._heading_popup_list.clear()
        if self._popup_mode == "heading":
            for heading in self._popup_items:
                title = heading.get("title") or "(heading)"
                line = heading.get("line", 1)
                level = max(1, min(5, int(heading.get("level", 1))))
                indent = "    " * (level - 1)
                item = QListWidgetItem(f"{indent}{title}  (line {line})")
                self._heading_popup_list.addItem(item)
            label = "Headings"
        else:
            return
        if 0 <= self._popup_index < self._heading_popup_list.count():
            self._heading_popup_list.setCurrentRow(self._popup_index)
        self._heading_popup_label.setText(label)
        editor_rect = self.editor.rect()
        top_left = self.editor.mapToGlobal(editor_rect.topLeft())
        popup_width = max(self._heading_popup.sizeHint().width(), editor_rect.width() // 3)
        min_height = int(editor_rect.height() * 0.5)
        popup_height = max(self._heading_popup.sizeHint().height(), min_height)
        x = top_left.x() + editor_rect.width() // 2 - popup_width // 2
        y = top_left.y() + 24
        self._heading_popup.resize(popup_width, popup_height)
        self._heading_popup.move(x, y)
        self._heading_popup.show()
        self._heading_popup.raise_()
        self._heading_popup_list.setFocus()
        _page_editor_log("[PageEditor] Popup shown and focused")

    def _cycle_popup(self, mode: str, reverse: bool = False) -> None:
        """Cycle through heading popup items."""
        _page_editor_log(f"[PageEditor] _cycle_popup called, mode={mode}")
        if mode == "heading":
            items = self._heading_popup_candidates()
            _page_editor_log(f"[PageEditor] Found {len(items)} heading candidates")
        else:
            return
        if not items:
            _page_editor_log("[PageEditor] No items to show, returning")
            return
        if self._popup_mode != mode:
            self._popup_items = items
            self._popup_mode = mode
            self._popup_index = 0
        else:
            self._popup_items = items
            if self._popup_index < 0 or self._popup_index >= len(items):
                self._popup_index = 0
            else:
                delta = -1 if reverse else 1
                self._popup_index = (self._popup_index + delta) % len(items)
        self._show_heading_popup()

    def _activate_heading_popup_selection(self) -> None:
        """Navigate to selected heading and hide popup."""
        _page_editor_log("[PageEditor] _activate_heading_popup_selection called")
        if not self._popup_items or self._popup_index < 0 or not self._popup_mode:
            _page_editor_log(
                f"[PageEditor] Invalid state: items={len(self._popup_items) if self._popup_items else 0}, "
                f"index={self._popup_index}, mode={self._popup_mode}"
            )
            self._hide_heading_popup()
            return
        target = self._popup_items[self._popup_index]
        mode = self._popup_mode
        _page_editor_log(
            f"[PageEditor] Navigating to heading: {target.get('title', 'unknown')} at line {target.get('line', '?')}"
        )
        self._hide_heading_popup()
        if mode == "heading" and target:
            try:
                pos = int(target.get("position", 0))
            except Exception:
                pos = 0
            if pos <= 0:
                try:
                    line = int(target.get("line", 1))
                except Exception:
                    line = 1
                block = self.editor.document().findBlockByNumber(max(0, line - 1))
                if block.isValid():
                    pos = block.position()
            _page_editor_log(f"[PageEditor] Setting cursor to position {pos}")
            cursor = self.editor.textCursor()
            cursor.setPosition(max(0, pos))
            self.editor.setTextCursor(cursor)
            self.editor.ensureCursorVisible()
            self._flash_heading(cursor)
            self.editor.setFocus()
            _page_editor_log("[PageEditor] Navigation complete")

    def _hide_heading_popup(self) -> None:
        """Hide the heading popup and reset state."""
        self._popup_items = []
        self._popup_index = -1
        self._popup_mode = None
        if self._heading_popup:
            self._heading_popup.hide()

    def _flash_heading(self, cursor: QTextCursor) -> None:
        """Briefly highlight the heading line."""
        try:
            sel = QTextEdit.ExtraSelection()
            sel.cursor = cursor
            sel.cursor.clearSelection()
            highlight = config.load_vault_accent_color() or theme_value("main_window.highlight.selection_bg", "#ffd54f")
            sel.format.setBackground(QColor(highlight))
            sel.format.setProperty(QTextFormat.FullWidthSelection, True)
            sel.format.setProperty(QTextFormat.UserProperty, 9991)
            current = self.editor.extraSelections()
            self.editor.setExtraSelections(current + [sel])

            def clear_flash() -> None:
                try:
                    keep = [
                        s
                        for s in self.editor.extraSelections()
                        if s.format.property(QTextFormat.UserProperty) != 9991
                    ]
                    self.editor.setExtraSelections(keep)
                except Exception:
                    pass

            QTimer.singleShot(220, clear_flash)
        except Exception:
            pass

    def _on_vi_insert_state_changed(self, insert_active: bool) -> None:
        """Update vi mode indicator when insert state changes."""
        self._vi_insert_active = insert_active
        self._update_vi_badge_style(insert_active)

    def _update_vi_badge_visibility(self) -> None:
        """Show or hide vi mode indicator based on whether vi mode is enabled."""
        if not hasattr(self, "_vi_status_label"):
            return
        if self._vi_enabled:
            self._vi_status_label.show()
            self._update_vi_badge_style(self._vi_insert_active)
        else:
            self._vi_status_label.hide()

    def _update_vi_badge_style(self, insert_active: bool) -> None:
        """Update vi mode indicator style based on insert mode state."""
        if not hasattr(self, "_vi_status_label"):
            return
        if not self._vi_enabled:
            self._vi_status_label.hide()
            return
        style = self._badge_base_style
        if insert_active:
            style += (
                " background-color: "
                f"{theme_value('page_editor_window.vi_badge.active_bg', '#ffd54d')}; "
                "color: "
                f"{theme_value('page_editor_window.vi_badge.active_text', '#000000')};"
            )
        else:
            style += " background-color: transparent;"
        self._vi_status_label.setStyleSheet(style)

    def _open_inline_ai_prompt(self, anchor: QPoint, insert_pos: int) -> None:
        if not config.load_enable_ai_chats():
            self.statusBar().showMessage("Enable AI Chats in Preferences to use inline prompts.", 4000)
            return
        if getattr(self, "_inline_ai_worker", None):
            self.statusBar().showMessage("Inline AI is already streaming.", 3000)
            return
        try:
            from .inline_ai_prompt import InlineAIPromptOverlay
        except Exception:
            self.statusBar().showMessage("Inline AI prompt unavailable.", 4000)
            return

        def _send(prompt: str) -> None:
            self._start_inline_ai_stream(prompt, insert_pos)

        overlay = InlineAIPromptOverlay(parent=self, on_send=_send, anchor=QPoint(anchor.x(), anchor.y() + 10))
        try:
            self._inline_ai_prompt_overlay = overlay
        except Exception:
            pass
        try:
            self.editor.push_focus_lost_suppression()
        except Exception:
            try:
                setattr(self.editor, "_suppress_focus_lost_once", True)
            except Exception:
                pass

        def _overlay_cleanup() -> None:
            try:
                self.editor.pop_focus_lost_suppression()
            except Exception:
                pass
            try:
                setattr(self, "_inline_ai_prompt_overlay", None)
            except Exception:
                pass

        try:
            overlay.finished.connect(lambda *_: _overlay_cleanup())
        except Exception:
            pass
        overlay.show()

    def _start_inline_ai_stream(self, prompt: str, insert_pos: int) -> None:
        if not prompt.strip():
            return
        if getattr(self, "_inline_ai_worker", None):
            return
        try:
            from .ai_chat_panel import ServerManager, ApiWorker
        except Exception:
            self.statusBar().showMessage("AI worker unavailable.", 4000)
            return

        server_config: dict = {}
        try:
            default_server_name = config.load_default_ai_server()
        except Exception:
            default_server_name = None
        try:
            server_mgr = ServerManager()
            if default_server_name:
                server_cfg = server_mgr.get_server(default_server_name)
                if server_cfg:
                    server_config = server_cfg
        except Exception:
            server_config = {}
        if not server_config:
            self.statusBar().showMessage("No AI server configured.", 4000)
            return

        try:
            default_model_name = config.load_default_ai_model()
        except Exception:
            default_model_name = None
        model = default_model_name or server_config.get("default_model") or "gpt-3.5-turbo"

        system_prompt = _load_one_shot_prompt()
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt.strip()},
        ]

        doc = self.editor.document()
        cursor = QTextCursor(doc)
        cursor.setPosition(max(0, insert_pos))
        cursor.beginEditBlock()
        cursor.setKeepPositionOnInsert(False)
        self._inline_ai_stream_cursor = cursor
        self._inline_ai_stream_used = False
        try:
            QApplication.setOverrideCursor(Qt.WaitCursor)
        except Exception:
            pass

        worker = ApiWorker(server_config, messages, model, stream=True, parent=self)
        worker.chunk.connect(self._append_inline_ai_chunk)
        worker.finished.connect(self._finalize_inline_ai_stream)
        worker.failed.connect(self._inline_ai_failed)
        self._inline_ai_worker = worker
        self.statusBar().showMessage("AI streaming...", 3000)
        worker.start()

    def _append_inline_ai_chunk(self, chunk: str) -> None:
        if not chunk:
            return
        cursor = getattr(self, "_inline_ai_stream_cursor", None)
        if cursor is None:
            return
        try:
            cursor.insertText(chunk)
            self._inline_ai_stream_used = True
        except Exception:
            pass

    def _finalize_inline_ai_stream(self, full: str) -> None:
        try:
            cursor = getattr(self, "_inline_ai_stream_cursor", None)
            used = getattr(self, "_inline_ai_stream_used", False)
            if cursor is not None and not used and full:
                cursor.insertText(full)
            if cursor is not None:
                try:
                    cursor.endEditBlock()
                except Exception:
                    pass
            if cursor is not None:
                try:
                    self.editor.setTextCursor(cursor)
                    self.editor.setFocus()
                except Exception:
                    pass
            self.statusBar().showMessage("Inline AI complete.", 2500)
        finally:
            try:
                QApplication.restoreOverrideCursor()
            except Exception:
                pass
            self._inline_ai_worker = None
            for attr in ("_inline_ai_stream_cursor", "_inline_ai_stream_used"):
                try:
                    delattr(self, attr)
                except Exception:
                    pass

    def _inline_ai_failed(self, err: str) -> None:
        self.statusBar().showMessage(f"Inline AI failed: {err}", 6000)
        try:
            cursor = getattr(self, "_inline_ai_stream_cursor", None)
            if cursor is not None:
                cursor.endEditBlock()
        except Exception:
            pass
        try:
            QApplication.restoreOverrideCursor()
        except Exception:
            pass
        self._inline_ai_worker = None
        for attr in ("_inline_ai_stream_cursor", "_inline_ai_stream_used"):
            try:
                delattr(self, attr)
            except Exception:
                pass

    def eventFilter(self, obj, event):  # type: ignore[override]
        """Handle Ctrl key release to activate heading popup selection."""
        if event.type() == QEvent.ShortcutOverride:
            if event.key() == Qt.Key_L and (event.modifiers() & ~Qt.KeypadModifier) == Qt.ControlModifier:
                event.accept()
                return True
            if event.matches(QKeySequence.Save):
                event.accept()
                return True
            if self._is_close_window_key(event):
                event.accept()
                return True
            if obj is self.editor and event.modifiers() == Qt.ControlModifier and event.key() == Qt.Key_H:
                event.accept()
                return True
        if event.type() == QEvent.KeyPress:
            if event.key() == Qt.Key_L and (event.modifiers() & ~Qt.KeypadModifier) == Qt.ControlModifier:
                self._insert_link_action.trigger()
                event.accept()
                return True
            if event.matches(QKeySequence.Save):
                self._save_action.trigger()
                event.accept()
                return True
            if self._is_close_window_key(event):
                self.close()
                event.accept()
                return True
            if obj is self.editor and event.modifiers() == Qt.ControlModifier and event.key() == Qt.Key_H:
                self._show_find_bar(replace=True, backwards=False)
                event.accept()
                return True
        elif event.type() == QEvent.KeyRelease:
            if event.key() == Qt.Key_Control and self._popup_items:
                self._activate_heading_popup_selection()
                return True
        return super().eventFilter(obj, event)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if not self._read_only:
            self._save_current_file(auto=False, reason="window close")
        if not self._read_only and self._is_dirty():
            event.ignore()
            return
        # Mark as closing before save/network work so child editor paintEvent can
        # bail out during shutdown transitions.
        self.setProperty("_sp_closing", True)
        if self._toc_scroll_anim is not None:
            self._toc_scroll_anim.stop()
        try:
            if getattr(self, "editor", None):
                self.editor.prepare_for_shutdown()
        except Exception:
            pass
        self._save_geometry()
        try:
            app = QApplication.instance()
            if app and self._app_state_changed_slot is not None:
                app.applicationStateChanged.disconnect(self._app_state_changed_slot)
        except Exception:
            pass
        self._app_state_changed_slot = None
        try:
            self.http.close()
        except Exception:
            pass
        super().closeEvent(event)
