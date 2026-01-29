from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional
import traceback
import httpx

from PySide6.QtCore import QTimer, Qt, QByteArray, QObject, QEvent, QPoint
from PySide6.QtGui import QAction, QKeySequence, QShortcut, QColor, QIcon, QTextCursor, QTextFormat
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
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

from .markdown_editor import MarkdownEditor
from .insert_link_dialog import InsertLinkDialog
from .page_load_logger import PageLoadLogger, PAGE_LOGGING_ENABLED
from sp.app import config
from sp.server.adapters.files import PAGE_SUFFIXES


_ONE_SHOT_PROMPT_CACHE: Optional[str] = None


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
        self._open_in_main = open_in_main_callback
        headers = {"X-Local-UI-Token": local_auth_token} if local_auth_token else None
        self.http = httpx.Client(base_url=self.api_base, timeout=10.0, headers=headers)
        self._badge_base_style = "border: 1px solid #666; padding: 2px 6px; border-radius: 3px;"
        self._font_size = config.load_popup_font_size(14)

        self.editor = MarkdownEditor()
        # Custom context menu with Edit operations only
        self.editor.setContextMenuPolicy(Qt.CustomContextMenu)
        self.editor.customContextMenuRequested.connect(self._show_editor_context_menu)
        self.editor.set_context(self.vault_root, self._source_path)
        self.editor.set_font_point_size(self._font_size)
        self.editor.set_vi_block_cursor_enabled(config.load_vi_block_cursor_enabled())
        self.editor.set_vi_mode_enabled(config.load_vi_mode_enabled())
        self.editor.set_read_only_mode(self._read_only)
        self.editor.set_ai_shortcuts_enabled(False)
        self.editor.linkActivated.connect(self._forward_link_to_main)
        self.editor.linkCopied.connect(
            lambda link: self.statusBar().showMessage(f"Copied link: {link}", 3000)
        )
        self.editor.focusLost.connect(lambda: self._save_current_file(auto=True, reason="focus lost"))
        self.editor.viInsertModeChanged.connect(self._on_vi_insert_state_changed)
        self.editor.aiInlinePromptRequested.connect(self._open_inline_ai_prompt)
        self.setCentralWidget(self.editor)
        
        # Vi mode state
        self._vi_enabled = config.load_vi_mode_enabled()
        self._vi_insert_active = False

        self._last_saved_content: Optional[str] = None
        self._inline_ai_worker = None
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setInterval(30_000)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.timeout.connect(lambda: self._save_current_file(auto=True, reason="autosave timer"))
        self.editor.textChanged.connect(lambda: self._autosave_timer.start())
        self.editor.document().modificationChanged.connect(lambda _: self._update_dirty_indicator())
        
        # Heading picker state
        self._toc_headings: list[dict] = []
        self._popup_items: list = []
        self._popup_index: int = -1
        self._popup_mode: Optional[str] = None
        self._heading_popup: Optional[QWidget] = None
        self._heading_popup_label: Optional[QLabel] = None
        self._heading_popup_list: Optional[QListWidget] = None
        self.editor.headingsChanged.connect(self._on_headings_changed)
        self.editor.headingPickerRequested.connect(self._handle_heading_picker_request)

        self._build_toolbar()
        self._load_content()
        self._update_title()
        self._size_and_center(parent)
        self._restore_geometry()
        self._geometry_timer = QTimer(self)
        self._geometry_timer.setInterval(400)
        self._geometry_timer.setSingleShot(True)

        # Status badges
        self._dirty_status_label = QLabel("")
        self._dirty_status_label.setObjectName("popupDirtyStatusLabel")
        self._dirty_status_label.setStyleSheet(self._badge_base_style + " background-color: transparent; margin-right: 6px;")
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
        heading_action.setShortcut(QKeySequence("Ctrl+Shift+Tab"))
        heading_action.setShortcutContext(Qt.WidgetWithChildrenShortcut)
        heading_action.triggered.connect(lambda: (print("[PageEditor] Heading action triggered"), self._cycle_popup("heading", reverse=False)))
        self.addAction(heading_action)

        link_action = QAction("Insert Link…", self)
        link_action.setShortcut(QKeySequence("Ctrl+L"))
        link_action.setShortcutContext(Qt.WidgetWithChildrenShortcut)
        link_action.triggered.connect(self._insert_link)
        self.addAction(link_action)
        
        # Install event filter to catch Control key release for popup navigation
        self.installEventFilter(self)

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
        save_action = QAction("Save", self)
        save_action.setShortcut(QKeySequence.Save)
        save_action.setShortcutContext(Qt.WindowShortcut)
        save_action.triggered.connect(lambda: self._save_current_file(auto=False, reason="manual save"))
        toolbar.addAction(save_action)
        self.addAction(save_action)  # Register shortcut with window
        insert_link_action = QAction("Insert Link…", self)
        insert_link_action.setToolTip("Insert a link to another page (Ctrl+L)")
        insert_link_action.triggered.connect(self._insert_link)
        toolbar.addAction(insert_link_action)
        toolbar.addSeparator()
        ai_action = QAction("AI Assist...", self)
        ai_action.setToolTip("AI assist (one-shot)")
        icon_path = Path(__file__).resolve().parents[2] / "assets" / "ai.svg"
        if icon_path.exists():
            ai_action.setIcon(QIcon(str(icon_path)))
        ai_action.triggered.connect(self._open_ai_assist)
        toolbar.addAction(ai_action)
        font_up = QAction("A+", self)
        font_up.setToolTip("Increase font size")
        font_up.triggered.connect(lambda: self._adjust_font_size(1))
        font_down = QAction("A-", self)
        font_down.setToolTip("Decrease font size")
        font_down.triggered.connect(lambda: self._adjust_font_size(-1))
        toolbar.addAction(font_down)
        toolbar.addAction(font_up)

        self.addToolBar(Qt.TopToolBarArea, toolbar)

    def _load_content(self) -> None:
        tracer = PageLoadLogger(self._source_path) if PAGE_LOGGING_ENABLED else None
        if tracer:
            tracer.mark("api read start")
        try:
            resp = self.http.post("/api/file/read", json={"path": self._source_path})
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
            self.editor.document().setModified(False)
            self._last_saved_content = content
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
        if self._read_only:
            self.setWindowTitle(f"{label} | Read-Only | {suffix}")
        else:
            self.setWindowTitle(f"{label} | {suffix}")
        self._update_dirty_indicator()

    def _is_dirty(self) -> bool:
        current = self.editor.to_markdown()
        return current != (self._last_saved_content or "")

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

    def _save_current_file(self, auto: bool = False, reason: str = "save") -> None:
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
                print(f"[StillPoint Popup] Invalid root write requested path={self._source_path} reason={reason_label}\n{trace}")
        except Exception:
            pass
        print(
            f"[StillPoint Popup] Write request reason={reason_label} mode={mode} path={self._source_path} "
            f"bytes={payload_bytes}"
        )
        try:
            resp = self.http.post("/api/file/write", json=payload)
            resp.raise_for_status()
            print(f"[StillPoint Popup] Write OK {self._source_path} status={resp.status_code}")
        except httpx.HTTPError as exc:
            try:
                body = exc.response.text if exc.response else str(exc)
                status = exc.response.status_code if exc.response else "n/a"
                print(f"[StillPoint Popup] Write FAILED {self._source_path} status={status} body={body}")
            except Exception:
                print(f"[StillPoint Popup] Write FAILED {self._source_path}: {exc}")
            if not auto:
                QMessageBox.critical(self, "Save Failed", f"Failed to save: {exc}")
            return
        self._last_saved_content = payload["content"]
        try:
            self.editor.document().setModified(False)
        except Exception:
            pass
        self.statusBar().showMessage("Saved", 2000)
        # Notify parent/main window to refresh if editing the same page
        try:
            if hasattr(self._open_in_main, "__call__"):
                self._open_in_main(self._source_path, force=True, refresh_only=True)
        except Exception:
            pass
        self._update_dirty_indicator()

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
                self._badge_base_style + " background-color: #9e9e9e; color: #f5f5f5; margin-right: 6px; text-decoration: line-through;"
            )
            self._dirty_status_label.setToolTip("Read-only: changes cannot be saved in this window")
            return
        dirty = self._is_dirty()
        if dirty:
            self._dirty_status_label.setText("●")
            self._dirty_status_label.setStyleSheet(
                self._badge_base_style + " background-color: #e57373; color: #000; margin-right: 6px;"
            )
            self._dirty_status_label.setToolTip("Unsaved changes")
        else:
            self._dirty_status_label.setText("●")
            self._dirty_status_label.setStyleSheet(
                self._badge_base_style + " background-color: #81c784; color: #000; margin-right: 6px;"
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
        
        # Get standard context menu (Undo, Redo, Cut, Copy, Paste, Delete, Select All)
        base_menu = self.editor.createStandardContextMenu()
        
        # Install custom copy actions (sanitized Copy + Copy As Markdown)
        try:
            if hasattr(self.editor, '_install_copy_actions'):
                self.editor._install_copy_actions(base_menu)
        except Exception:
            pass

        click_cursor = self.editor.cursorForPosition(pos)
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
        if hasattr(self, "_geometry_timer"):
            self._geometry_timer.start()

    def moveEvent(self, event) -> None:  # type: ignore[override]
        super().moveEvent(event)
        if hasattr(self, "_geometry_timer"):
            self._geometry_timer.start()

    def _on_headings_changed(self, headings: list[dict]) -> None:
        """Store headings when editor parses them."""
        self._toc_headings = list(headings or [])
        print(f"[PageEditor] Headings changed: {len(self._toc_headings)} headings")

    def _handle_heading_picker_request(self, global_point, prefer_above: bool) -> None:
        """Handle Ctrl+Alt+T heading picker request from editor - show filterable picker."""
        print(f"[PageEditor] _handle_heading_picker_request called")
        self._show_filterable_heading_picker(global_point, prefer_above)

    def _show_filterable_heading_picker(self, global_pos, prefer_above: bool = False) -> None:
        """Show a filterable heading picker near the cursor (vi 't')."""
        headings = self._toc_headings or []
        if not headings:
            print("[PageEditor] No headings to show")
            return
        # Dispose any existing picker
        if hasattr(self, "_heading_picker") and self._heading_picker:
            try:
                self._heading_picker.close()
            except Exception:
                pass
        popup = QWidget(self, Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        popup.setStyleSheet(
            "QWidget { background: rgba(32,32,32,240); border: 1px solid #666; border-radius: 6px; }"
            "QLineEdit { border: 1px solid #777; border-radius: 4px; padding: 4px 6px; }"
            "QListWidget { background: transparent; color: #f5f5f5; border: none; }"
            "QListWidget::item { padding: 4px 6px; }"
            "QListWidget::item:selected { background: rgba(90,161,255,80); }"
        )
        layout = QVBoxLayout(popup)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        filter_edit = QLineEdit(popup)
        filter_edit.setPlaceholderText("Filter headings…")
        list_widget = QListWidget(popup)
        layout.addWidget(filter_edit)
        layout.addWidget(list_widget, 1)

        def populate(query: str = "") -> None:
            list_widget.clear()
            needle = query.lower().strip()
            for h in headings:
                title = h.get("title") or "(heading)"
                if needle and needle not in title.lower():
                    continue
                line = h.get("line", 1)
                level = max(1, min(5, int(h.get("level", 1))))
                indent = "    " * (level - 1)
                item = QListWidgetItem(f"{indent}{title}  (line {line})")
                item.setData(Qt.UserRole, h)
                list_widget.addItem(item)
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
            def eventFilter(self, obj, ev):  # type: ignore[override]
                if ev.type() == QEvent.KeyPress:
                    if ev.key() in (Qt.Key_Return, Qt.Key_Enter):
                        activate_current()
                        return True
                    if ev.key() == Qt.Key_J and ev.modifiers() == (Qt.ControlModifier | Qt.ShiftModifier):
                        row = list_widget.currentRow()
                        if list_widget.count():
                            list_widget.setCurrentRow(min(list_widget.count() - 1, row + 1))
                        return True
                    if ev.key() == Qt.Key_K and ev.modifiers() == (Qt.ControlModifier | Qt.ShiftModifier):
                        row = list_widget.currentRow()
                        if list_widget.count():
                            list_widget.setCurrentRow(max(0, row - 1))
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

        # Position near cursor, above or below based on preference and space
        popup.resize(360, min(320, max(160, list_widget.sizeHintForRow(0) * min(8, list_widget.count()) + 64)))
        screen = QApplication.primaryScreen().availableGeometry()
        size = popup.size()
        x = max(screen.x(), min(global_pos.x(), screen.x() + screen.width() - size.width()))
        if prefer_above:
            y = global_pos.y() - size.height() - 8
            if y < screen.y():
                y = global_pos.y() + 12
        else:
            y = global_pos.y() + 12
            if y + size.height() > screen.y() + screen.height():
                y = global_pos.y() - size.height() - 8
        popup.move(x, y)
        popup.show()
        popup.raise_()
        filter_edit.setFocus()
        self._heading_picker = popup
        print(f"[PageEditor] Filterable picker shown with {list_widget.count()} headings")

    def _heading_popup_candidates(self) -> list[dict]:
        """Return headings for current page (excluding horizontal rules)."""
        return [h for h in self._toc_headings if h and h.get("type") != "hr"]

    def _ensure_heading_popup(self) -> None:
        """Create heading popup widget if it doesn't exist."""
        if self._heading_popup is None:
            popup = QWidget(self, Qt.Tool | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint | Qt.WindowStaysOnTopHint)
            popup.setStyleSheet(
                "background: rgba(30,30,30,220); border: 1px solid #888; border-radius: 6px; padding: 8px;"
            )
            layout = QVBoxLayout(popup)
            layout.setContentsMargins(12, 8, 12, 8)
            self._heading_popup_label = QLabel(popup)
            self._heading_popup_label.setStyleSheet("color: #f5f5f5; font-weight: bold;")
            layout.addWidget(self._heading_popup_label)
            self._heading_popup_list = QListWidget(popup)
            self._heading_popup_list.setStyleSheet(
                "QListWidget { background: transparent; color: #f5f5f5; border: none; }"
                "QListWidget::item { padding: 4px 6px; }"
                "QListWidget::item:selected { background: rgba(255,255,255,40); }"
            )
            layout.addWidget(self._heading_popup_list)
            self._heading_popup_list.itemActivated.connect(self._activate_heading_popup_selection)
            self._heading_popup = popup

    def _show_heading_popup(self) -> None:
        """Display the heading popup with current items and selection."""
        print(f"[PageEditor] _show_heading_popup called")
        self._ensure_heading_popup()
        if not self._heading_popup or not self._heading_popup_label or not self._heading_popup_list:
            print("[PageEditor] Popup widgets not initialized properly")
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
        print(f"[PageEditor] Popup shown and focused")

    def _cycle_popup(self, mode: str, reverse: bool = False) -> None:
        """Cycle through heading popup items."""
        print(f"[PageEditor] _cycle_popup called, mode={mode}")
        if mode == "heading":
            items = self._heading_popup_candidates()
            print(f"[PageEditor] Found {len(items)} heading candidates")
        else:
            return
        if not items:
            print("[PageEditor] No items to show, returning")
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
        print(f"[PageEditor] _activate_heading_popup_selection called")
        if not self._popup_items or self._popup_index < 0 or not self._popup_mode:
            print(f"[PageEditor] Invalid state: items={len(self._popup_items) if self._popup_items else 0}, index={self._popup_index}, mode={self._popup_mode}")
            self._hide_heading_popup()
            return
        target = self._popup_items[self._popup_index]
        mode = self._popup_mode
        print(f"[PageEditor] Navigating to heading: {target.get('title', 'unknown')} at line {target.get('line', '?')}")
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
            print(f"[PageEditor] Setting cursor to position {pos}")
            cursor = self.editor.textCursor()
            cursor.setPosition(max(0, pos))
            self.editor.setTextCursor(cursor)
            self.editor.ensureCursorVisible()
            self._flash_heading(cursor)
            self.editor.setFocus()
            print(f"[PageEditor] Navigation complete")

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
            sel.format.setBackground(QColor("#ffd54f"))
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
            style += " background-color: #ffd54d; color: #000;"
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
        if event.type() == QEvent.KeyPress:
            if event.key() == Qt.Key_Tab and (event.modifiers() & Qt.ControlModifier):
                if event.modifiers() & Qt.ShiftModifier:
                    reverse = event.key() == Qt.Key_Backtab
                    self._cycle_popup("heading", reverse=reverse)
                    return True
        elif event.type() == QEvent.KeyRelease:
            if event.key() == Qt.Key_Control and self._popup_items:
                self._activate_heading_popup_selection()
                return True
        return super().eventFilter(obj, event)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        # Autosave on close if dirty and writable
        self._save_current_file(auto=False, reason="window close")
        self._save_geometry()
        try:
            self.http.close()
        except Exception:
            pass
        super().closeEvent(event)
