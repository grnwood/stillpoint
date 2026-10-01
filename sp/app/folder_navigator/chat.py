"""AI chat for an ordinary Folder Navigator root."""

from __future__ import annotations

import hashlib
import codecs
import os
import sqlite3
from pathlib import Path
from typing import Callable

from PySide6 import QtWidgets

from sp.ai.context import (
    MAX_FOLDER_PAGES, MAX_VISION_IMAGE_BYTES, VISION_IMAGE_SUFFIXES, image_data_url,
)
from sp.ai.manager import AIManager, ContextItem
from sp.app.ui.ai_chat_panel import AIChatPanel, AIChatStore, ContextCandidate
from sp.app import config
from sp.rag.attachment_text import MAX_OFFICE_CONTEXT_CHARS, extract_attachment_text
from .chat_tools import FOLDER_AGENT_PROMPT, FolderAgentChatWorker


OCR_IMAGE_SUFFIXES = frozenset({".bmp", ".tif", ".tiff"})
DOCUMENT_SUFFIXES = frozenset({
    ".pdf", ".docx", ".pptx", ".xls", ".xlsx", ".xlsm", ".xlsb", ".ods",
})
MAX_READ_BYTES = 256 * 1024
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024


def _decode_text(data: bytes, *, truncated: bool) -> str:
    encoding = "utf-8-sig"
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        encoding = "utf-16"
    elif b"\x00" in data[:8192]:
        raise ValueError("Binary content is not readable chat context")
    decoder = codecs.getincrementaldecoder(encoding)()
    text = decoder.decode(data, final=not truncated)
    if any(ord(char) < 32 and char not in "\t\n\r\f" for char in text[:8192]):
        raise ValueError("Binary control characters are not readable chat context")
    return text


def folder_chat_database(root: Path) -> Path:
    """Give each resolved folder its own chat history outside that folder."""
    key = hashlib.sha256(str(root.resolve()).encode("utf-8")).hexdigest()[:24]
    return Path.home() / ".stillpoint" / "folder-chats" / f"{key}.db"


class FolderChatPanel(AIChatPanel):
    def __init__(
        self,
        root: Path,
        file_candidates: Callable[[str, Path], list[Path]],
        directory_candidates: Callable[[str, Path], list[Path]],
        editor_text: Callable[[Path], str | None],
        image_candidates: Callable[[str, Path], list[Path]] | None = None,
        index_state: Callable[[], str] | None = None,
        dirty_paths: Callable[[], set[Path]] | None = None,
        parent=None,
    ) -> None:
        self.folder_root = root.resolve(strict=True)
        self._file_candidates = file_candidates
        self._image_candidates = image_candidates or file_candidates
        self._directory_candidates = directory_candidates
        self._editor_text = editor_text
        self._index_state = index_state
        self._dirty_paths = dirty_paths or (lambda: set())
        self._folder_chat_connection: sqlite3.Connection | None = None
        db_path = folder_chat_database(self.folder_root)
        super().__init__(parent=parent, api_client=None, store=AIChatStore(db_path=db_path))
        self._folder_chat_connection = sqlite3.connect(db_path, timeout=10)
        self._folder_chat_connection.execute("PRAGMA busy_timeout = 10000")
        with self._folder_chat_connection:
            self._folder_chat_connection.execute(
                "CREATE TABLE IF NOT EXISTS folder_chat_metadata "
                "(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            self._folder_chat_connection.execute(
                "INSERT OR REPLACE INTO folder_chat_metadata(key, value) VALUES('root_path', ?)",
                (str(self.folder_root),),
            )
        self.ai_manager = AIManager(conn=self._folder_chat_connection)
        self.current_session_id = None
        self._current_ai_conversation_id = None
        self._context_items = []
        self.messages = []
        self.chat_view.clear()
        self._load_chat_tree()
        self._select_default_chat()
        self._update_context_summary()
        self._context_popup.context_labeler = self._context_item_label
        self._context_popup.kind_labels = {
            "page": "File", "page-tree": "Folder", "attachment": "Image",
        }
        self.context_refresh_btn.setText("+ File")
        self.context_refresh_btn.setToolTip("Attach the current file to this chat")

    def _context_source_label(self) -> str:
        return "Folder Navigator"

    def _context_file_label(self) -> str:
        return "File"

    def _folder_listing_complete(self) -> bool:
        return False

    def _update_load_current_page_button(self) -> None:
        super()._update_load_current_page_button()
        if hasattr(self, "context_refresh_btn"):
            is_image = Path(self.current_page_path or "").suffix.casefold() in (
                VISION_IMAGE_SUFFIXES | OCR_IMAGE_SUFFIXES
            )
            self.context_refresh_btn.setText("+ Image" if is_image else "+ File")
            self.context_refresh_btn.setToolTip(
                "Attach the current image to this chat" if is_image
                else "Attach the current file to this chat"
            )

    def _refresh_current_page_context(self) -> None:
        if not self.current_page_path:
            self._set_status("Open a file before attaching it.")
            return
        try:
            path = self._resolve_ref(self.current_page_path)
        except (OSError, ValueError) as exc:
            self._set_status(str(exc))
            return
        if not self.add_path_to_context(path):
            self._set_status("Could not attach the current file.")

    def _context_added_message(self, trigger: str, candidate: ContextCandidate) -> str:
        if trigger == "@":
            return f"Added file context: {candidate.page_ref}"
        if trigger == "!":
            return f"Added image context: {candidate.label}"
        return super()._context_added_message(trigger, candidate)

    def _context_item_label(self, item: ContextItem) -> str:
        if item.kind == "page":
            return f"File: {item.page_ref}"
        if item.kind == "page-tree":
            return f"Folder: {item.page_ref}"
        if item.kind == "attachment" and item.attachment_name:
            return f"Image: {Path(item.page_ref).parent / item.attachment_name}"
        return super()._context_item_label(item)

    def _add_chat_context_menu_actions(self, menu: QtWidgets.QMenu, data: dict) -> None:
        action = menu.addAction("Promote to Vault Chat…")
        action.triggered.connect(lambda: self._promote_to_vault_chat(int(data["id"])))

    def _choose_promotion_vault(self) -> Path | None:
        choices: list[tuple[str, Path]] = []
        seen: set[Path] = set()
        active = os.environ.get("SP_FOLDER_NAVIGATOR_STILLPOINT_VAULT") or config.get_active_vault()
        entries = ([{"name": "Current vault", "path": active}] if active else []) + config.load_known_vaults()
        for entry in entries:
            try:
                path = Path(entry["path"]).expanduser().resolve(strict=True)
            except (KeyError, OSError):
                continue
            if path in seen or not (path / ".stillpoint" / "settings.db").is_file():
                continue
            seen.add(path)
            choices.append((f"{entry.get('name') or path.name} — {path}", path))
        labels = [label for label, _ in choices] + ["Browse for a local vault…"]
        label, accepted = QtWidgets.QInputDialog.getItem(
            self, "Promote to Vault Chat", "Destination vault:", labels, 0, False,
        )
        if not accepted:
            return None
        for choice_label, path in choices:
            if label == choice_label:
                return path
        selected = QtWidgets.QFileDialog.getExistingDirectory(self, "Choose StillPoint vault")
        return Path(selected) if selected else None

    def _selected_files_for_chat(self, session_id: int) -> list[Path]:
        session = self.store.get_session_by_id(session_id)
        if not session or not self.ai_manager:
            return []
        conversation_id = session.get("ai_conversation_id")
        if not conversation_id:
            return []
        selected: list[Path] = []
        seen: set[Path] = set()
        for item in self.ai_manager.list_context_items(int(conversation_id)):
            if item.kind == "page":
                refs = [item.page_ref]
            elif item.kind == "page-tree":
                refs = self._list_context_pages(item.page_ref)[:MAX_FOLDER_PAGES]
            elif item.kind == "attachment" and item.attachment_name:
                refs = []
                path = self._attachment_file(item.page_ref, item.attachment_name)
                if path not in seen:
                    seen.add(path)
                    selected.append(path)
            else:
                refs = []
            for ref in refs:
                path = self._resolve_ref(ref)
                if path not in seen:
                    seen.add(path)
                    selected.append(path)
        return selected

    def _promote_to_vault_chat(self, session_id: int) -> None:
        if self._has_active_operation():
            self._set_status("Wait for the active run before promoting this chat.")
            return
        destination = self._choose_promotion_vault()
        if destination is None:
            return
        from .promotion import promote_folder_chat

        try:
            files = self._selected_files_for_chat(session_id)
            overrides = {
                path: live for path in files
                if (live := self._editor_text(path)) is not None
            }
            result = promote_folder_chat(
                source_root=self.folder_root,
                source_store=self.store,
                source_session_id=session_id,
                selected_files=files,
                destination_vault=destination,
                text_overrides=overrides,
            )
        except (OSError, sqlite3.Error, ValueError, RuntimeError) as exc:
            QtWidgets.QMessageBox.warning(self, "Promotion failed", str(exc))
            return
        QtWidgets.QMessageBox.information(
            self, "Chat promoted",
            f"Copied the chat and {result.copied_files} selected file(s) to {result.vault.name}. "
            "Open that vault's AI Chats to continue it.",
        )

    def _should_use_agent_tools(self) -> bool:
        return config.load_global_enable_ai_agents()

    def _folder_agent_key(self) -> str:
        return f"folder::{self.folder_root}"

    def _start_agent_send(
        self, content: str, extra_system: str | None, record_user: bool,
        context_prompt: str | None = None,
        vision_images: list[str] | None = None,
        ocr_context_prompt: str | None = None,
    ) -> None:
        if self._agent_tool_worker:
            self._set_status("Agent tools already running…", "#f6c343")
            return
        history = self.messages
        if not record_user:
            for index in range(len(history) - 1, -1, -1):
                if history[index] == ("user", content):
                    history = history[:index]
                    break
        if not config.is_agent_tool_approved(self._folder_agent_key()):
            if record_user:
                self.messages.append(("user", content))
                if self.current_session_id:
                    self.store.save_message(self.current_session_id, "user", content)
                self._append_assistant_message(
                    "Agent tools are disabled for this folder. "
                    "<a href='action:agent-approve:accept'>Approve</a> to allow tool use."
                )
            self._pending_agent_prompt = content
            return

        if record_user:
            self.messages.append(("user", content))
            if self.current_session_id:
                self.store.save_message(self.current_session_id, "user", content)
        self.messages.append(("assistant", "Running agent tools..."))
        self._agent_placeholder_index = len(self.messages) - 1
        self._agent_progress_lines = []
        self._render_messages()

        if context_prompt is None:
            try:
                context_prompt, vision_images, ocr_context_prompt = self._build_context_payload(content)
            except (OSError, RuntimeError, ValueError) as exc:
                self._handle_agent_failed(f"Context unavailable: {exc}")
                return
        systems = [FOLDER_AGENT_PROMPT, context_prompt, self.current_system_prompt, extra_system]
        system_prompt = "\n\n".join(part for part in systems if part)
        ocr_system_prompt = None
        if vision_images and ocr_context_prompt:
            ocr_system_prompt = "\n\n".join(
                part for part in (FOLDER_AGENT_PROMPT, ocr_context_prompt,
                                  self.current_system_prompt, extra_system) if part
            )
        self._agent_tool_worker = FolderAgentChatWorker(
            server_config=self.current_server,
            model=self.model_combo.currentText(),
            system_prompt=system_prompt,
            user_prompt=content,
            root=self.folder_root,
            current_path=(self.current_page_path or "").lstrip("/"),
            dirty_paths=self._dirty_paths(),
            vision_images=vision_images,
            ocr_system_prompt=ocr_system_prompt,
            history=history,
        )
        self._agent_tool_worker.toolMessage.connect(self._handle_agent_tool_message)
        self._agent_tool_worker.visionFallback.connect(self._show_vision_fallback_notice)
        self._agent_tool_worker.fileChanged.connect(self.pageWritten.emit)
        self._agent_tool_worker.finalMessage.connect(self._handle_agent_final)
        self._agent_tool_worker.failed.connect(self._handle_agent_failed)
        self._agent_tool_worker.start()
        self.send_btn.setEnabled(False)
        self._set_status("Waiting for agent response…", "#f6c343")
        self._update_stop_button()

    def _approve_agent_tools(self) -> None:
        config.approve_agent_tool_for_vault(self._folder_agent_key())
        self._append_assistant_message("Agent tools approved for this folder.")
        if self._pending_agent_prompt:
            pending = self._pending_agent_prompt
            self._pending_agent_prompt = None
            self._start_agent_send(pending, extra_system=None, record_user=False)

    def _reload_context_index(self) -> None:
        # Picker candidates come from this folder's catalog on demand.
        return

    def _context_candidate_provider(self, trigger: str):
        return lambda query: self._folder_candidates(trigger, query)

    def _candidates_for_trigger(self, trigger: str) -> list[ContextCandidate]:
        return self._folder_candidates(trigger, "")

    def _folder_candidates(self, trigger: str, query: str) -> list[ContextCandidate]:
        if trigger == "#":
            paths = [self.folder_root] + self._directory_candidates(query, self.folder_root)
            result = []
            for path in paths:
                ref = self._relative_ref(path)
                if ref is None or (query and query.casefold() not in ref.casefold() and path == self.folder_root):
                    continue
                result.append(ContextCandidate(ref, ref, display_label=f"Folder: {ref}"))
            return result[:1200]

        result = []
        candidates = self._image_candidates if trigger == "!" else self._file_candidates
        for path in candidates(query, self.folder_root):
            ref = self._relative_ref(path)
            if ref is None or not path.is_file():
                continue
            suffix = path.suffix.casefold()
            is_image = suffix in VISION_IMAGE_SUFFIXES or suffix in OCR_IMAGE_SUFFIXES
            if trigger == "!" and is_image:
                parent_ref = self._relative_ref(path.parent)
                if parent_ref is None:
                    continue
                anchor = f"{parent_ref.rstrip('/')}/__folder_attachment__"
                result.append(ContextCandidate(
                    anchor, ref, attachment_name=path.name,
                    display_label=f"Image: {ref}",
                ))
            elif trigger == "@" and not is_image:
                result.append(ContextCandidate(ref, ref, display_label=f"File: {ref}"))
            if len(result) >= 1200:
                break
        return result

    def add_path_to_context(self, path: Path) -> bool:
        """Attach an item chosen directly from Folder Navigator's file tree."""
        ref = self._relative_ref(path)
        if ref is None or not self._ensure_active_chat():
            return False
        if path.is_dir():
            trigger = "#"
            candidate = ContextCandidate(ref, ref, display_label=f"Folder: {ref}")
        elif path.is_file() and path.suffix.casefold() in VISION_IMAGE_SUFFIXES | OCR_IMAGE_SUFFIXES:
            parent_ref = self._relative_ref(path.parent)
            if parent_ref is None:
                return False
            trigger = "!"
            candidate = ContextCandidate(
                f"{parent_ref.rstrip('/')}/__folder_attachment__", ref,
                attachment_name=path.name, display_label=f"Image: {ref}",
            )
        elif path.is_file():
            trigger = "@"
            candidate = ContextCandidate(ref, ref, display_label=f"File: {ref}")
        else:
            return False
        self._add_context_item(trigger, candidate)
        return True

    def _relative_ref(self, path: Path) -> str | None:
        try:
            relative = path.resolve(strict=True).relative_to(self.folder_root)
        except (OSError, ValueError):
            return None
        return "/" if relative == Path(".") else f"/{relative.as_posix()}"

    def _resolve_ref(self, ref: str) -> Path:
        target = (self.folder_root / ref.lstrip("/")).resolve(strict=True)
        if not target.is_relative_to(self.folder_root):
            raise ValueError("Selected context is outside this folder")
        return target

    def _read_context_page(self, page_ref: str) -> str:
        path = self._resolve_ref(page_ref)
        if not path.is_file():
            raise ValueError(f"Selected file is unavailable: {page_ref}")
        live = self._editor_text(path)
        if live is not None:
            return live
        if path.suffix.casefold() in DOCUMENT_SUFFIXES:
            if path.stat().st_size > MAX_ATTACHMENT_BYTES:
                raise ValueError(f"Selected document is too large: {page_ref}")
            content = extract_attachment_text(path, max_chars=MAX_OFFICE_CONTEXT_CHARS)
            if not content:
                raise ValueError(f"Could not extract text from {page_ref}")
            return content
        with path.open("rb") as source:
            data = source.read(MAX_READ_BYTES + 1)
        content = _decode_text(data[:MAX_READ_BYTES], truncated=len(data) > MAX_READ_BYTES)
        if len(data) > MAX_READ_BYTES:
            content += "\n[File shortened before sending.]"
        return content

    def _list_context_pages(self, tree_ref: str) -> list[str]:
        if self._index_state is not None and self._index_state() != "complete":
            raise RuntimeError(
                "The folder index is incomplete. Wait for indexing to finish or "
                "choose Continue Full Index before sending folder context."
            )
        folder = self._resolve_ref(tree_ref)
        if not folder.is_dir():
            raise ValueError(f"Selected folder is unavailable: {tree_ref}")
        result = []
        for path in self._file_candidates("", folder):
            if path.suffix.casefold() in VISION_IMAGE_SUFFIXES | OCR_IMAGE_SUFFIXES:
                continue  # Images enter context only through explicit ! selection.
            if path.suffix.casefold() not in DOCUMENT_SUFFIXES:
                try:
                    with path.open("rb") as source:
                        head = source.read(4096)
                    _decode_text(head, truncated=True)
                except (OSError, UnicodeDecodeError, ValueError):
                    continue
            ref = self._relative_ref(path)
            if ref:
                result.append(ref)
            if len(result) > MAX_FOLDER_PAGES:
                break
        return result

    def _attachment_file(self, page_ref: str, attachment_name: str) -> Path:
        anchor_parent = (self.folder_root / page_ref.lstrip("/")).parent.resolve(strict=True)
        target = (anchor_parent / attachment_name).resolve(strict=True)
        if (not anchor_parent.is_relative_to(self.folder_root)
                or target.parent != anchor_parent
                or not target.is_relative_to(self.folder_root)
                or not target.is_file()):
            raise ValueError("Selected image is unavailable")
        return target

    def _read_context_attachment(self, page_ref: str, attachment_name: str) -> str:
        target = self._attachment_file(page_ref, attachment_name)
        if target.stat().st_size > MAX_ATTACHMENT_BYTES:
            raise ValueError(f"Selected image is too large: {target.name}")
        content = extract_attachment_text(target)
        return content or "[No readable text found in this image.]"

    def _read_context_image(self, page_ref: str, attachment_name: str) -> str:
        target = self._attachment_file(page_ref, attachment_name)
        if target.stat().st_size > MAX_VISION_IMAGE_BYTES:
            raise ValueError(f"Selected image is too large: {target.name}")
        return image_data_url(target.name, target.read_bytes())

    def _open_context_item(self, item: ContextItem) -> None:
        try:
            if item.kind == "attachment" and item.attachment_name:
                path = self._attachment_file(item.page_ref, item.attachment_name)
            else:
                path = self._resolve_ref(item.page_ref)
        except (OSError, ValueError) as exc:
            self._set_status(str(exc))
            return
        self.chatNavigateRequested.emit(str(path))

    def closeEvent(self, event) -> None:  # type: ignore[override]
        super().closeEvent(event)
        if self._folder_chat_connection is not None:
            self._folder_chat_connection.close()
            self._folder_chat_connection = None
