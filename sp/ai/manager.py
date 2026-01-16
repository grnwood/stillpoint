from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from sp.app import config


@dataclass
class Conversation:
    id: int
    title: str
    mode: str
    anchor_page_ref: Optional[str]
    created_at: Optional[float]
    updated_at: Optional[float]
    last_used_at: Optional[float]


@dataclass
class Message:
    id: int
    conversation_id: int
    role: str
    content: str
    created_at: Optional[float]


@dataclass
class ContextItem:
    id: int
    conversation_id: int
    kind: str
    page_ref: str
    attachment_name: Optional[str]
    created_at: Optional[float]


class AIManager:
    """Lightweight manager for AI conversations stored in the vault database."""

    def __init__(self, conn: Optional[sqlite3.Connection] = None) -> None:
        self._conn = conn or config._get_conn()
        if not self._conn:
            raise RuntimeError("AIManager requires an active vault connection")
        self._ensure_schema()

    # --- Public API -------------------------------------------------
    def get_or_create_page_chat(self, page_ref: str, title: str | None = None) -> Conversation:
        self._ensure_open()
        existing = self.find_page_chat(page_ref)
        if existing:
            self._touch(existing.id)
            return existing
        conv_title = title or self._derive_title(page_ref) or "Page chat"
        return self._create_conversation(conv_title, "page", page_ref)

    def create_global_chat(self, title: str = "New chat") -> Conversation:
        self._ensure_open()
        return self._create_conversation(title or "New chat", "global", None)

    def get_conversation(self, conv_id: int) -> Conversation | None:
        self._ensure_open()
        cur = self._conn.execute(
            """
            SELECT id, title, mode, anchor_page_ref, created_at, updated_at, last_used_at
            FROM ai_conversations WHERE id = ?
            """,
            (conv_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return Conversation(*row)

    def list_conversations(self) -> list[Conversation]:
        self._ensure_open()
        cur = self._conn.execute(
            """
            SELECT id, title, mode, anchor_page_ref, created_at, updated_at, last_used_at
            FROM ai_conversations
            ORDER BY COALESCE(last_used_at, updated_at, created_at) DESC, id DESC
            """
        )
        return [Conversation(*row) for row in cur.fetchall()]

    def list_messages(self, conv_id: int) -> list[Message]:
        self._ensure_open()
        cur = self._conn.execute(
            """
            SELECT id, conversation_id, role, content, created_at
            FROM ai_messages
            WHERE conversation_id = ?
            ORDER BY created_at ASC, id ASC
            """,
            (conv_id,),
        )
        return [Message(*row) for row in cur.fetchall()]

    def list_context_items(self, conv_id: int) -> list[ContextItem]:
        self._ensure_open()
        cur = self._conn.execute(
            """
            SELECT id, conversation_id, kind, page_ref, attachment_name, created_at
            FROM ai_context_items
            WHERE conversation_id = ?
            ORDER BY created_at ASC, id ASC
            """,
            (conv_id,),
        )
        return [ContextItem(*row) for row in cur.fetchall()]

    def delete_context_item(self, item_id: int) -> None:
        self._ensure_open()
        self._conn.execute("DELETE FROM ai_context_items WHERE id = ?", (item_id,))
        self._conn.commit()

    def send_user_message(self, conv_id: int, text: str) -> Message:
        self._ensure_open()
        return self._insert_message(conv_id, "user", text)

    def add_assistant_message(self, conv_id: int, text: str) -> Message:
        self._ensure_open()
        return self._insert_message(conv_id, "assistant", text)

    def add_context_page(self, conv_id: int, page_ref: str) -> None:
        self._ensure_open()
        self._ensure_collection_mode(conv_id)
        self._insert_context_item(conv_id, "page", page_ref, None)

    def add_context_page_tree(self, conv_id: int, page_ref: str) -> None:
        self._ensure_open()
        self._ensure_collection_mode(conv_id)
        self._insert_context_item(conv_id, "page-tree", page_ref, None)

    def add_context_attachment(self, conv_id: int, page_ref: str, attachment_name: str) -> None:
        self._ensure_open()
        self._ensure_collection_mode(conv_id)
        self._insert_context_item(conv_id, "attachment", page_ref, attachment_name)

    def clear_context_items(self, conv_id: int) -> None:
        self._ensure_open()
        self._conn.execute("DELETE FROM ai_context_items WHERE conversation_id = ?", (conv_id,))
        self._conn.commit()

    def find_page_chat(self, page_ref: str) -> Conversation | None:
        self._ensure_open()
        cur = self._conn.execute(
            """
            SELECT id, title, mode, anchor_page_ref, created_at, updated_at, last_used_at
            FROM ai_conversations
            WHERE mode = 'page' AND anchor_page_ref = ?
            LIMIT 1
            """,
            (page_ref,),
        )
        row = cur.fetchone()
        return Conversation(*row) if row else None

    def find_collections_containing_page(self, page_ref: str) -> list[Conversation]:
        self._ensure_open()
        collections = [
            conv for conv in self.list_conversations() if conv.mode == "collection"
        ]
        matched: list[Conversation] = []
        for conv in collections:
            items = self.list_context_items(conv.id)
            if self._context_covers_page(items, page_ref):
                matched.append(conv)
        return matched

    def delete_conversation(self, conv_id: int) -> None:
        """Remove a conversation and its related rows."""
        self._ensure_open()
        self._conn.execute("DELETE FROM ai_messages WHERE conversation_id = ?", (conv_id,))
        self._conn.execute("DELETE FROM ai_context_items WHERE conversation_id = ?", (conv_id,))
        self._conn.execute("DELETE FROM ai_conversations WHERE id = ?", (conv_id,))
        self._conn.commit()

    # --- Internal helpers -------------------------------------------
    def _ensure_open(self) -> None:
        try:
            self._conn.execute("SELECT 1")
        except sqlite3.ProgrammingError as exc:
            if "closed" not in str(exc).lower():
                raise
            self._conn = config._get_conn()
            if not self._conn:
                raise RuntimeError("AIManager requires an active vault connection") from exc
            self._ensure_schema()

    def _ensure_schema(self) -> None:
        """Guard against older vaults that predate ai tables."""
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS ai_conversations (
                id INTEGER PRIMARY KEY,
                title TEXT NOT NULL,
                mode TEXT NOT NULL,
                anchor_page_ref TEXT,
                created_at REAL,
                updated_at REAL,
                last_used_at REAL
            );
            CREATE INDEX IF NOT EXISTS idx_ai_conversations_mode_anchor
                ON ai_conversations(mode, anchor_page_ref);
            CREATE INDEX IF NOT EXISTS idx_ai_conversations_last_used
                ON ai_conversations(last_used_at);
            CREATE TABLE IF NOT EXISTS ai_messages (
                id INTEGER PRIMARY KEY,
                conversation_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at REAL
            );
            CREATE INDEX IF NOT EXISTS idx_ai_messages_conversation
                ON ai_messages(conversation_id);
            CREATE TABLE IF NOT EXISTS ai_context_items (
                id INTEGER PRIMARY KEY,
                conversation_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                page_ref TEXT NOT NULL,
                attachment_name TEXT,
                created_at REAL
            );
            CREATE INDEX IF NOT EXISTS idx_ai_context_items_conversation
                ON ai_context_items(conversation_id);
            CREATE INDEX IF NOT EXISTS idx_ai_context_items_page_ref
                ON ai_context_items(page_ref);
            """
        )
        self._conn.commit()

    def _create_conversation(self, title: str, mode: str, anchor: Optional[str]) -> Conversation:
        now = time.time()
        cur = self._conn.execute(
            """
            INSERT INTO ai_conversations(title, mode, anchor_page_ref, created_at, updated_at, last_used_at)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            (title, mode, anchor, now, now, now),
        )
        self._conn.commit()
        conv_id = int(cur.lastrowid)
        return self.get_conversation(conv_id)  # type: ignore[return-value]

    def _insert_message(self, conv_id: int, role: str, content: str) -> Message:
        now = time.time()
        cur = self._conn.execute(
            """
            INSERT INTO ai_messages(conversation_id, role, content, created_at)
            VALUES(?, ?, ?, ?)
            """,
            (conv_id, role, content, now),
        )
        self._conn.execute(
            "UPDATE ai_conversations SET updated_at = ?, last_used_at = ? WHERE id = ?",
            (now, now, conv_id),
        )
        self._conn.commit()
        message_id = int(cur.lastrowid)
        return Message(message_id, conv_id, role, content, now)

    def _insert_context_item(
        self, conv_id: int, kind: str, page_ref: str, attachment_name: Optional[str]
    ) -> None:
        now = time.time()
        self._conn.execute(
            """
            INSERT INTO ai_context_items(conversation_id, kind, page_ref, attachment_name, created_at)
            VALUES(?, ?, ?, ?, ?)
            """,
            (conv_id, kind, page_ref, attachment_name, now),
        )
        self._conn.execute(
            "UPDATE ai_conversations SET updated_at = ?, last_used_at = ? WHERE id = ?",
            (now, now, conv_id),
        )
        self._conn.commit()

    def _ensure_collection_mode(self, conv_id: int) -> None:
        conv = self.get_conversation(conv_id)
        if not conv:
            return
        if conv.mode == "collection":
            return
        self._conn.execute(
            "UPDATE ai_conversations SET mode = 'collection', updated_at = ?, last_used_at = ? WHERE id = ?",
            (time.time(), time.time(), conv_id),
        )
        self._conn.commit()

    def _touch(self, conv_id: int) -> None:
        now = time.time()
        self._conn.execute(
            "UPDATE ai_conversations SET last_used_at = ?, updated_at = COALESCE(updated_at, ?) WHERE id = ?",
            (now, now, conv_id),
        )
        self._conn.commit()

    @staticmethod
    def _context_covers_page(items: Iterable[ContextItem], page_ref: str) -> bool:
        normalized = page_ref or ""
        for item in items:
            if item.kind in ("page", "attachment") and item.page_ref == normalized:
                return True
            if item.kind == "page-tree":
                root = (item.page_ref or "").rstrip("/")
                if normalized == root or normalized.startswith(f"{root}/"):
                    return True
        return False

    @staticmethod
    def _derive_title(page_ref: str) -> str:
        try:
            leaf = Path(page_ref).stem or Path(page_ref).name
            return leaf or page_ref
        except Exception:
            return page_ref
