
from __future__ import annotations

import json
import os
import sqlite3
import sys
from collections import Counter
import copy
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import html
import re
import traceback
import threading
from typing import Dict, List, Optional, Set, Tuple

import httpx
from PySide6 import QtCore, QtWidgets
from PySide6.QtCore import QUrl, QSize, Qt
from PySide6.QtGui import (
    QCursor,
    QDesktopServices,
    QFocusEvent,
    QIcon,
    QKeyEvent,
    QPalette,
    QPixmap,
    QPainter,
    QTextCursor,
    QShortcut,
    QKeySequence,
)
from PySide6.QtWidgets import QFrame, QLineEdit, QListWidget, QStyle, QLabel, QSizePolicy
from PySide6.QtSvg import QSvgRenderer
from sp.rag.attachment_text import extract_attachment_text
from markdown import markdown

# Use stillpoint_config for global config storage
from sp.app import config, config as stillpoint_config
from sp.ai.manager import AIManager, ContextItem
from sp.rag.index import RetrievedChunk
from .path_utils import path_to_colon
from sp.server.adapters.files import LEGACY_SUFFIX, PAGE_SUFFIX, PAGE_SUFFIXES

AI_CHAT_COLOR = "\033[34m"
CHROMA_COLOR = "\033[33m"
LLM_RESPONSE_COLOR = "\033[32m"
LOG_RESET = "\033[0m"

def _color_text(text: str, color: str) -> str:
    return f"{color}{text}{LOG_RESET}"

def _log_ai_chat(message: str) -> None:
    print(_color_text(message, AI_CHAT_COLOR))

def _log_vector(message: str) -> None:
    print(_color_text(f"[Vector] {message}", CHROMA_COLOR))


def _load_white_icon(path: Path, size: QSize | None = None) -> QIcon:
    if not path.exists():
        return QIcon()
    pixmap = QPixmap(str(path))
    if size:
        pixmap = pixmap.scaled(size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    mask = pixmap.createMaskFromColor(Qt.transparent)
    white = QPixmap(pixmap.size())
    white.fill(Qt.white)
    white.setMask(mask)
    return QIcon(white)

def _log_llm_response(message: str) -> None:
    print(_color_text(message, LLM_RESPONSE_COLOR))


class VectorAPIClient:
    """Light wrapper around the server's vector endpoints."""

    def __init__(self, client: Optional[httpx.Client]) -> None:
        self._client = client

    def available(self) -> bool:
        if self._client is None:
            return False
        try:
            return not self._client.is_closed
        except Exception:
            return True

    def index_text(
        self,
        page_ref: str,
        text: str,
        kind: str,
        attachment: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> bool:
        if not self.available():
            return False
        payload = {
            "page_ref": page_ref,
            "text": text,
            "kind": kind,
            "attachment_name": attachment,
        }
        try:
            resp = self._client.post("/vector/add", json=payload, timeout=timeout)
            resp.raise_for_status()
            return True
        except (httpx.HTTPError, RuntimeError) as exc:
            _log_vector(f"Failed to index {page_ref}: {exc}")
            return False

    def delete_text(self, page_ref: str, kind: str, attachment: Optional[str] = None, timeout: Optional[float] = None) -> bool:
        if not self.available():
            return False
        payload = {
            "page_ref": page_ref,
            "kind": kind,
            "attachment_name": attachment,
        }
        try:
            resp = self._client.post("/vector/remove", json=payload, timeout=timeout)
            resp.raise_for_status()
            return True
        except (httpx.HTTPError, RuntimeError) as exc:
            _log_vector(f"Failed to delete {page_ref}: {exc}")
            return False

    def query(self, query_text: str, page_refs: Optional[List[str]] = None, limit: int = 4) -> List[RetrievedChunk]:
        return self._query_internal(query_text, page_refs=page_refs, limit=limit, kind="page")

    def query_attachments(self, query_text: str, attachment_names: list[str], limit: int = 4) -> List[RetrievedChunk]:
        return self._query_internal(query_text, attachment_names=attachment_names, limit=limit, kind="attachment")

    def _query_internal(
        self,
        query_text: str,
        limit: int = 4,
        kind: str = "page",
        page_refs: Optional[List[str]] = None,
        attachment_names: Optional[list[str]] = None,
    ) -> List[RetrievedChunk]:
        if not self.available():
            return []
        payload = {"query_text": query_text, "kind": kind, "limit": limit}
        if page_refs:
            payload["page_refs"] = page_refs
        if attachment_names:
            payload["attachment_names"] = attachment_names
        try:
            resp = self._client.post("/vector/query", json=payload)
            resp.raise_for_status()
            data = resp.json()
            return [RetrievedChunk(**item) for item in data.get("chunks", [])]
        except (httpx.HTTPError, RuntimeError) as exc:
            _log_vector(f"Failed to query context: {exc}")
            return []

# Shared config (aligns with slipstream/ask-server/ask-client.py defaults)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
ASSETS_DIR = PROJECT_ROOT / "assets"
SERVER_CONFIG_FILE = PROJECT_ROOT / "slipstream" / "server_configs.json"
# Default local model endpoint (LM Studio OpenAI-compatible API)
DEFAULT_LM_STUDIO_URL = os.getenv("LM_STUDIO_URL", "http://localhost:1234")

@lru_cache(maxsize=1)
def _get_asset_directory() -> Path:
    """Return the first available assets directory from likely candidate locations."""
    rel_paths = (Path("assets"), Path("sp") / "assets")
    candidates: list[Path] = []
    base = getattr(sys, "_MEIPASS", None)
    if base:
        base_path = Path(base)
        candidates.extend(base_path / rel for rel in rel_paths)
        candidates.extend(base_path / "_internal" / rel for rel in rel_paths)
    try:
        exe_dir = Path(sys.argv[0]).resolve().parent
    except Exception:
        exe_dir = None
    if exe_dir:
        candidates.extend(exe_dir / rel for rel in rel_paths)
        candidates.extend(exe_dir / "_internal" / rel for rel in rel_paths)
    candidates.append(ASSETS_DIR)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return ASSETS_DIR


def _resolve_asset_path(name: str) -> Optional[Path]:
    """Return a usable Path to an asset, or None if it does not exist."""
    asset_dir = _get_asset_directory()
    path = asset_dir / name
    return path if path.exists() else None


def _asset_uri(name: str) -> str:
    path = _resolve_asset_path(name)
    if not path:
        return ""
    try:
        return path.resolve().as_uri()
    except Exception:
        return ""

def _load_icon(name: str, size: QSize = QSize(24, 24)) -> QIcon:
    path = _resolve_asset_path(name)
    if not path:
        return QIcon()
    ext = path.suffix.lower()
    if ext == ".svg":
        renderer = QSvgRenderer(str(path))
        pixmap = QPixmap(size)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        renderer.render(painter)
        # Make all non-transparent pixels white
        painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
        painter.fillRect(pixmap.rect(), Qt.white)
        painter.end()
        return QIcon(pixmap)
    else:
        pixmap = QPixmap()
        if pixmap.load(str(path)):
            return QIcon(pixmap.scaled(size, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        return QIcon()

def _icon_tag(name: str, tooltip: str, size: int = 10) -> str:
    """
    For HTML in QTextBrowser: convert SVG to PNG at runtime and use PNG path.
    PNGs are cached in /tmp/stillpoint_icons/.
    """
    import tempfile
    from pathlib import Path
    from PySide6.QtGui import QPixmap
    from PySide6.QtSvg import QSvgRenderer
    from PySide6.QtCore import QSize, Qt
    import hashlib

    asset_path = _resolve_asset_path(name)
    ext = asset_path.suffix.lower() if asset_path else ""
    ICON_SIZE = size
    if not asset_path:
        # Fallback: return a blank or placeholder icon
        return f"<span title='{tooltip}' style='display:inline-block;width:{ICON_SIZE}px;height:{ICON_SIZE}px;vertical-align:middle;margin:0 4px;'></span>"
    if ext == ".svg":
        # Cache PNG in /tmp/stillpoint_icons/
        cache_dir = Path(tempfile.gettempdir()) / "stillpoint_icons"
        cache_dir.mkdir(parents=True, exist_ok=True)
        hashval = hashlib.md5((str(asset_path) + "_white" + str(ICON_SIZE)).encode()).hexdigest()
        png_path = cache_dir / f"{asset_path.stem}_{hashval}.png"
        if not png_path.exists() or png_path.stat().st_mtime < asset_path.stat().st_mtime:
            renderer = QSvgRenderer(str(asset_path))
            pixmap = QPixmap(QSize(ICON_SIZE, ICON_SIZE))
            pixmap.fill(Qt.transparent)
            painter = QPainter(pixmap)
            renderer.render(painter)
            # Make all non-transparent pixels white
            painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
            painter.fillRect(pixmap.rect(), Qt.white)
            painter.end()
            pixmap.save(str(png_path), "PNG")
        img_uri = png_path.as_uri()
    else:
        img_uri = _asset_uri(name)
    return (
        f"<img src='{img_uri}' title='{tooltip}' "
        f"style='width:{ICON_SIZE}px;height:{ICON_SIZE}px;vertical-align:middle;margin:0 4px;'/>"
    )


@dataclass
class ContextCandidate:
    page_ref: str
    label: str
    attachment_name: Optional[str] = None


class ClickableLabel(QtWidgets.QLabel):
    clicked = QtCore.Signal()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setCursor(QtCore.Qt.PointingHandCursor)

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        self.clicked.emit()

def fetch_and_cache_models(server_config: dict) -> List[str]:
    """Fetch models from the server and cache them in the config file under 'server_models'."""
    server = server_config or {}
    fallback_model = server.get("default_model") or "gpt-3.5-turbo"
    base_url = server.get("base_url", "")
    if not base_url or not server.get("name"):
        return [fallback_model]
    models_path = server.get("models_path") or ("/mods" if server.get("auth_mode") == "proxy" else "/v1/models")
    url = compose_url(base_url, models_path)
    headers = build_auth_headers(server)
    verify = bool(server.get("verify_ssl", True))
    try:
        print("[AIChat][models request]", {"url": url, "headers": headers})
        with httpx.Client(timeout=10.0, verify=verify) as client:
            resp = client.get(url, headers=headers)
            resp.raise_for_status()
            print(f"[AIChat][models response] {resp.status_code} {resp.text[:500]}")
            payload = resp.json()
    except Exception as exc:
        print(f"Error fetching models from {url}: {exc}")
        payload = None
    model_ids: List[str] = []
    if isinstance(payload, dict):
        if "data" in payload and isinstance(payload["data"], list):
            data_list = payload["data"]
            if data_list and isinstance(data_list[0], dict):
                model_ids = [item.get("id") for item in data_list if item.get("id")]
            else:
                model_ids = [str(item) for item in data_list if item]
        elif "models" in payload and isinstance(payload["models"], list):
            model_ids = [str(item) for item in payload["models"] if item]
    elif isinstance(payload, list):
        if payload and isinstance(payload[0], dict):
            model_ids = [item.get("id") for item in payload if item.get("id")]
        else:
            model_ids = [str(item) for item in payload if item]
    cleaned = sorted({m for m in model_ids if m})
    if not cleaned:
        cleaned = [fallback_model]
    # Cache in config
    payload = stillpoint_config._read_global_config()
    server_models = payload.get("server_models", {})
    server_models[server["name"]] = cleaned
    stillpoint_config._update_global_config({"server_models": server_models})
    return cleaned


def normalize_base_url(url: str) -> str:
    if not url:
        return ""
    return url.rstrip("/")


def compose_url(base_url: str, path: str) -> str:
    base = normalize_base_url(base_url)
    if not path:
        return base
    return f"{base}/{path.lstrip('/')}"


def to_bool(value, default=False):
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "y", "on")
    return bool(value)


def build_default_server_configs():
    servers = [
        {
            "name": "LM Studio",
            "base_url": normalize_base_url(DEFAULT_LM_STUDIO_URL),
            "auth_mode": "proxy",
            "api_secret": "",
            "api_key": "",
            "custom_header_name": "",
            "custom_header_value": "",
            "models_path": "/v1/models",
            "chat_path": "/v1/chat/completions",
            "verify_ssl": True,
            "default_model": "gpt-3.5-turbo",
            "auto_summarize": True,
            "timeout": "",
        },
        {
            "name": "OpenAI Compatible (8000)",
            "base_url": "http://localhost:8000",
            "auth_mode": "proxy",
            "api_secret": "",
            "api_key": "",
            "custom_header_name": "",
            "custom_header_value": "",
            "models_path": "/v1/models",
            "chat_path": "/v1/chat/completions",
            "verify_ssl": True,
            "default_model": "gpt-3.5-turbo",
            "auto_summarize": True,
            "timeout": "",
        },
        {
            "name": "OpenAI Compatible (8080)",
            "base_url": "http://localhost:8080",
            "auth_mode": "proxy",
            "api_secret": "",
            "api_key": "",
            "custom_header_name": "",
            "custom_header_value": "",
            "models_path": "/v1/models",
            "chat_path": "/v1/chat/completions",
            "verify_ssl": True,
            "default_model": "gpt-3.5-turbo",
            "auto_summarize": True,
            "timeout": "",
        },
    ]
    return servers


class ServerManager:
    SERVERS_KEY = "servers"
    ACTIVE_SERVER_KEY = "active_server"

    def __init__(self, config_path: Optional[Path] = None):
        self._servers_cache: Optional[List[dict]] = None

    # No longer needed: _load_file and _save_file

    def _normalize_server(self, entry: dict) -> dict:
        entry = entry or {}
        auth_mode = entry.get("auth_mode") or ("proxy" if entry.get("api_secret") else "openai")
        base_url = normalize_base_url(entry.get("base_url", ""))
        models_path = entry.get("models_path") or ("/mods" if auth_mode == "proxy" else "/v1/models")
        chat_path = entry.get("chat_path") or "/v1/chat/completions"
        name = entry.get("name") or (base_url or "Server")
        default_model = entry.get("default_model") or "gpt-3.5-turbo"
        return {
            "name": name,
            "base_url": base_url,
            "auth_mode": auth_mode,
            "api_secret": entry.get("api_secret", ""),
            "api_key": entry.get("api_key", ""),
            "custom_header_name": entry.get("custom_header_name", ""),
            "custom_header_value": entry.get("custom_header_value", ""),
            "models_path": models_path,
            "chat_path": chat_path,
            "verify_ssl": bool(entry.get("verify_ssl", True)),
            "default_model": default_model,
            "auto_summarize": to_bool(entry.get("auto_summarize"), default=True),
            "timeout": entry.get("timeout", ""),
        }

    def load_servers(self) -> List[dict]:
        if self._servers_cache is not None:
            return list(self._servers_cache)
        payload = stillpoint_config._read_global_config()
        servers = payload.get(self.SERVERS_KEY, [])
        if not servers:
            servers = build_default_server_configs()
        normalized = [self._normalize_server(entry) for entry in servers if entry]
        self._servers_cache = normalized
        if not payload.get(self.SERVERS_KEY):
            stillpoint_config._update_global_config({self.SERVERS_KEY: normalized})
        return list(normalized)

    def list_server_names(self) -> List[str]:
        return [server["name"] for server in self.load_servers()]

    def get_server(self, name: Optional[str]) -> Optional[dict]:
        if not name:
            return None
        for server in self.load_servers():
            if server["name"] == name:
                return server
        return None

    def set_active_server(self, name: str) -> None:
        if name and self.get_server(name):
            stillpoint_config._update_global_config({self.ACTIVE_SERVER_KEY: name})
            self._servers_cache = None

    def get_active_server_name(self) -> Optional[str]:
        payload = stillpoint_config._read_global_config()
        active = payload.get(self.ACTIVE_SERVER_KEY)
        names = self.list_server_names()
        if active in names:
            return active
        if names:
            self.set_active_server(names[0])
            return names[0]
        return None

    def add_or_update_server(self, server: dict) -> dict:
        servers = self.load_servers()
        target_name = server.get("name")
        original_name = server.get("original_name")
        updated = False
        for idx, existing in enumerate(servers):
            if existing["name"] == target_name or (original_name and existing["name"] == original_name):
                servers[idx] = self._normalize_server(server)
                updated = True
                break
        if not updated:
            if any(existing["name"] == target_name for existing in servers):
                raise ValueError(f"A server named '{target_name}' already exists.")
            servers.append(self._normalize_server(server))
        payload = stillpoint_config._read_global_config()
        if original_name and payload.get(self.ACTIVE_SERVER_KEY) == original_name:
            stillpoint_config._update_global_config({self.ACTIVE_SERVER_KEY: target_name})
        stillpoint_config._update_global_config({self.SERVERS_KEY: servers})
        self._servers_cache = None
        return self.get_server(target_name) or self._normalize_server(server)

    def delete_server(self, name: str) -> List[dict]:
        servers = [srv for srv in self.load_servers() if srv["name"] != name]
        if not servers:
            servers = build_default_server_configs()
        stillpoint_config._update_global_config({self.SERVERS_KEY: servers})
        active_name = self.get_active_server_name()
        if active_name == name and servers:
            stillpoint_config._update_global_config({self.ACTIVE_SERVER_KEY: servers[0]["name"]})
        self._servers_cache = None
        return self.load_servers()


class AIChatStore:
    """Persist chats and messages in a per-vault .stillpoint/ai-chats.db."""

    def __init__(self, db_path: Optional[Path] = None, vault_root: Optional[str] = None):
        if db_path:
            self.db_path = Path(db_path)
        elif vault_root:
            self.db_path = Path(vault_root) / ".stillpoint" / "ai-chats.db"
        else:
            self.db_path = Path.home() / ".stillpoint" / "ai-chats.db"
        self._ensure_db()

    def _ensure_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                parent_id INTEGER,
                path TEXT,
                type TEXT DEFAULT 'chat',
                last_model TEXT,
                last_server TEXT,
                system_prompt TEXT,
                ai_conversation_id INTEGER
            )
            """
        )
        cur.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_path_type
            ON sessions(path, type)
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                session_id INTEGER,
                role TEXT,
                content TEXT
            )
            """
        )
        conn.commit()
        # Backfill missing columns for existing DBs
        try:
            cur.execute("PRAGMA table_info(sessions)")
            cols = [row[1] for row in cur.fetchall()]
            schema_updated = False
            if "last_model" not in cols:
                cur.execute("ALTER TABLE sessions ADD COLUMN last_model TEXT")
                schema_updated = True
            if "last_server" not in cols:
                cur.execute("ALTER TABLE sessions ADD COLUMN last_server TEXT")
                schema_updated = True
            if "system_prompt" not in cols:
                cur.execute("ALTER TABLE sessions ADD COLUMN system_prompt TEXT")
                schema_updated = True
            if "ai_conversation_id" not in cols:
                cur.execute("ALTER TABLE sessions ADD COLUMN ai_conversation_id INTEGER")
                schema_updated = True
            if schema_updated:
                conn.commit()
            if "ai_conversation_id" in cols:
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_sessions_ai_conversation
                    ON sessions(ai_conversation_id)
                    """
                )
        except Exception:
            pass
        conn.close()
        self._ensure_root_folder()

    def _ensure_root_folder(self) -> int:
        root = self.get_session_by_path("/", "folder")
        if root:
            return root["id"]
        return self._create_session("Chats", None, "/", "folder")

    def get_session_by_id(self, session_id: int) -> Optional[Dict]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            "SELECT id, name, parent_id, path, type, last_model, last_server, system_prompt, ai_conversation_id FROM sessions WHERE id = ?",
            (session_id,),
        )
        row = cur.fetchone()
        conn.close()
        return dict(row) if row else None

    def _create_session(self, name: str, parent_id: Optional[int], path: str, type_: str) -> int:
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(
            "INSERT OR REPLACE INTO sessions (name, parent_id, path, type, last_model, last_server, system_prompt) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (name, parent_id, path, type_, None, None, None),
        )
        conn.commit()
        new_id = cur.lastrowid
        conn.close()
        return new_id

    def get_session_by_path(self, path: str, type_: str) -> Optional[Dict]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            "SELECT id, name, parent_id, path, type, last_model, last_server, system_prompt, ai_conversation_id FROM sessions WHERE path = ? AND type = ?",
            (path, type_),
        )
        row = cur.fetchone()
        conn.close()
        return dict(row) if row else None

    def get_sessions(self) -> List[Dict]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            "SELECT id, name, parent_id, path, type, last_model, last_server, system_prompt, ai_conversation_id FROM sessions ORDER BY id"
        )
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows

    def _normalize_folder_path(self, folder_path: Optional[str]) -> str:
        if not folder_path:
            return "/"
        cleaned = folder_path.strip()
        if not cleaned or cleaned == "/":
            return "/"
        return "/" + Path(cleaned.lstrip("/")).as_posix()

    def _ensure_folder_chain(self, folder_path: str) -> Tuple[int, str]:
        folder_path = self._normalize_folder_path(folder_path)
        parts = [part for part in Path(folder_path.lstrip("/")).parts]
        parent_id = self._ensure_root_folder()
        current_path = "/"
        for part in parts:
            current_path = f"{current_path.rstrip('/')}/{part}"
            existing = self.get_session_by_path(current_path, "folder")
            if existing:
                parent_id = existing["id"]
                continue
            parent_id = self._create_session(part, parent_id, current_path, "folder")
        return parent_id, folder_path

    def _unique_chat_path(self, folder_path: str, base_name: str) -> str:
        base_path = self._normalize_folder_path(folder_path).rstrip("/")
        candidate = f"{base_path}/{base_name}".replace("//", "/")
        idx = 2
        while self.get_session_by_path(candidate, "chat"):
            candidate = f"{base_path}/{base_name}-{idx}"
            idx += 1
        return candidate

    def create_named_chat(self, folder_path: str, name: str) -> Dict:
        parent_id, folder_path = self._ensure_folder_chain(folder_path)
        chat_path = self._unique_chat_path(folder_path, name or "Chat")
        chat_id = self._create_session(name or "Chat", parent_id, chat_path, "chat")
        return {"id": chat_id, "name": name or "Chat", "parent_id": parent_id, "path": chat_path, "type": "chat"}

    def get_or_create_named_chat(self, folder_path: str, name: str) -> Dict:
        """Return a stable named chat for a folder, creating it if needed."""
        folder_path = self._normalize_folder_path(folder_path)
        parent_id, folder_path = self._ensure_folder_chain(folder_path)
        base_name = name or "Chat"
        if folder_path == "/":
            chat_path = f"/{base_name}"
        else:
            chat_path = f"{folder_path.rstrip('/')}/{base_name}".replace("//", "/")
        existing = self.get_session_by_path(chat_path, "chat")
        if existing:
            return existing
        chat_id = self._create_session(base_name, parent_id, chat_path, "chat")
        return {"id": chat_id, "name": base_name, "parent_id": parent_id, "path": chat_path, "type": "chat"}

    def get_or_create_chat_for_page(self, rel_path: Optional[str]) -> Optional[Dict]:
        if not rel_path:
            return self.get_session_by_path("/", "chat") or self._create_root_chat()
        path_obj = Path(rel_path.lstrip("/"))
        folder_path = self._normalize_folder_path("/" + path_obj.parent.as_posix())
        parent_id, folder_path = self._ensure_folder_chain(folder_path)
        chat_name = path_obj.stem or Path(folder_path).name or "Chat"
        existing_chat = self.get_session_by_path(folder_path, "chat")
        if existing_chat:
            return existing_chat
        chat_id = self._create_session(chat_name, parent_id, folder_path, "chat")
        return {"id": chat_id, "name": chat_name, "parent_id": parent_id, "path": folder_path, "type": "chat"}

    def _create_root_chat(self) -> Dict:
        root_id = self._ensure_root_folder()
        chat_id = self._create_session("Root Chat", root_id, "/", "chat")
        return {"id": chat_id, "name": "Root Chat", "parent_id": root_id, "path": "/", "type": "chat"}

    def get_messages(self, session_id: int) -> List[Tuple[str, str]]:
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("SELECT role, content FROM messages WHERE session_id = ?", (session_id,))
        rows = cur.fetchall()
        conn.close()
        return [(row[0], row[1]) for row in rows]

    def save_message(self, session_id: int, role: str, content: str) -> None:
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)", (session_id, role, content))
        conn.commit()
        conn.close()

    def update_session_last_model(self, session_id: int, model: str) -> None:
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("UPDATE sessions SET last_model = ? WHERE id = ?", (model, session_id))
        conn.commit()
        conn.close()

    def update_session_last_server(self, session_id: int, server_name: str) -> None:
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("UPDATE sessions SET last_server = ? WHERE id = ?", (server_name, session_id))
        conn.commit()
        conn.close()

    def update_session_system_prompt(self, session_id: int, prompt: Optional[str]) -> None:
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("UPDATE sessions SET system_prompt = ? WHERE id = ?", (prompt, session_id))
        conn.commit()
        conn.close()

    def update_session_ai_conversation(self, session_id: int, conv_id: Optional[int]) -> None:
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("UPDATE sessions SET ai_conversation_id = ? WHERE id = ?", (conv_id, session_id))
        conn.commit()
        conn.close()

    def clear_chat(self, session_id: int) -> None:
        """Delete all messages for a chat and reset last-used metadata."""
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        conn.commit()
        conn.close()

    def delete_session(self, session_id: int) -> None:
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        cur.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        conn.commit()
        conn.close()

    def delete_message(self, session_id: int, role: str, content: str) -> None:
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("DELETE FROM messages WHERE session_id = ? AND role = ? AND content = ?", (session_id, role, content))
        conn.commit()
        conn.close()

    def has_chat_for_path(self, rel_path: Optional[str]) -> bool:
        """Return True if a chat exists for the given page path."""
        if not rel_path:
            return bool(self.get_session_by_path("/", "chat"))
        path_obj = Path(rel_path.lstrip("/"))
        folder_path = self._normalize_folder_path("/" + path_obj.parent.as_posix())
        return bool(self.get_session_by_path(folder_path, "chat"))

    def has_chats_under(self, folder_path: Optional[str]) -> bool:
        """Return True if any chat exists at or under the given folder path."""
        if not folder_path:
            return False
        normalized = self._normalize_folder_path(folder_path)
        like_pattern = f"{normalized.rstrip('/')}/%"
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(
            "SELECT 1 FROM sessions WHERE type = 'chat' AND (path = ? OR path LIKE ?) LIMIT 1",
            (normalized, like_pattern),
        )
        row = cur.fetchone()
        conn.close()
        return bool(row)

    def delete_chats_under(self, folder_path: Optional[str]) -> None:
        """Delete chats and their messages at or under the given folder path."""
        if not folder_path:
            return
        normalized = self._normalize_folder_path(folder_path)
        like_pattern = f"{normalized.rstrip('/')}/%"
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(
            "SELECT id FROM sessions WHERE type = 'chat' AND (path = ? OR path LIKE ?)",
            (normalized, like_pattern),
        )
        ids = [row[0] for row in cur.fetchall()]
        if ids:
            cur.execute(f"DELETE FROM messages WHERE session_id IN ({','.join('?' for _ in ids)})", ids)
            cur.execute(f"DELETE FROM sessions WHERE id IN ({','.join('?' for _ in ids)})", ids)
            conn.commit()
        conn.close()


def build_auth_headers(server_config: dict) -> dict:
    headers = {}
    auth_mode = (server_config or {}).get("auth_mode", "proxy")
    if auth_mode == "proxy":
        token = (server_config or {}).get("api_secret")
        if token:
            headers["x-api-secret"] = token
    elif auth_mode == "openai":
        api_key = (server_config or {}).get("api_key")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
    else:
        header_name = (server_config or {}).get("custom_header_name")
        header_value = (server_config or {}).get("custom_header_value")
        if header_name and header_value:
            headers[header_name] = header_value
    return headers


def get_available_models(server_config: Optional[dict]) -> List[str]:
    server = server_config or {}
    fallback_model = server.get("default_model") or "gpt-3.5-turbo"
    if not server.get("name"):
        return [fallback_model]
    payload = stillpoint_config._read_global_config()
    server_models = payload.get("server_models", {})
    models = server_models.get(server["name"], [])
    if not models:
        return [fallback_model]
    return sorted(set(models))


def build_api_request(server_config: dict, messages: List[dict], model: str, stream: bool = True):
    server = server_config or {}
    base_url = server.get("base_url", "")
    chat_path = server.get("chat_path") or "/v1/chat/completions"
    if not base_url:
        raise ValueError("Selected server does not have a base URL configured.")
    url = compose_url(base_url, chat_path)
    headers = {"Content-Type": "application/json"}
    headers.update(build_auth_headers(server))
    verify = bool(server.get("verify_ssl", True))

    timeout_raw = server.get("timeout", "")
    try:
        timeout = float(timeout_raw) if str(timeout_raw).strip() else 120
    except (ValueError, TypeError):
        timeout = 120

    payload = {"model": model, "messages": messages, "stream": bool(stream)}
    return url, headers, verify, timeout, payload


class ApiWorker(QtCore.QThread):
    chunk = QtCore.Signal(str)
    finished = QtCore.Signal(str)
    failed = QtCore.Signal(str)

    def __init__(self, server_config: dict, messages: List[dict], model: str, stream: bool = True, parent=None):
        super().__init__(parent)
        self.server_config = server_config
        self.messages = messages
        self.model = model
        self.stream = stream
        self._cancel_requested = False

    def request_cancel(self) -> None:
        """Ask the worker to stop streaming as soon as possible."""
        self._cancel_requested = True

    def run(self) -> None:
        try:
            url, headers, verify, timeout, payload = build_api_request(
                self.server_config, self.messages, self.model, stream=self.stream
            )
            print("[AIChat][request]", {"url": url, "headers": headers, "payload": payload})
            if self.stream:
                with httpx.stream(
                    "POST", url, json=payload, headers=headers, timeout=timeout, verify=verify
                ) as resp:
                    resp.raise_for_status()
                    full = ""
                    for line in resp.iter_lines():
                        if self._cancel_requested:
                            self.failed.emit("Cancelled")
                            return
                        if not line:
                            continue
                        decoded = line.decode("utf-8") if isinstance(line, (bytes, bytearray)) else str(line)
                        #print(f"[AIChat][stream raw] {decoded}")
                        if decoded.startswith("data: "):
                            json_data = decoded[len("data: ") :]
                            if json_data.strip() == "[DONE]":
                                break
                            try:
                                data = json.loads(json_data)
                                if "choices" in data and data["choices"]:
                                    delta = data["choices"][0].get("delta", {})
                                    if "content" in delta:
                                        chunk = delta["content"]
                                        full += chunk
                                        self.chunk.emit(chunk)
                            except Exception:
                                continue
                    if full:
                        print("[AIChat][stream complete]", full)
                        self.finished.emit(full)
                        return
                # Fallback to non-stream if no chunks were handled
            with httpx.Client(timeout=timeout, verify=verify) as client:
                resp = client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                print(f"[AIChat][response status] {resp.status_code}")
                print("[AIChat][response body]", resp.text)
                data = resp.json()
                choice = (data.get("choices") or [{}])[0]
                content = ""
                if isinstance(choice, dict):
                    message = choice.get("message") or {}
                    if isinstance(message, dict):
                        content = message.get("content", "")
                self.finished.emit(content or str(data))
        except Exception as exc:
            self.failed.emit(str(exc))


class ServerConfigDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, server: Optional[dict] = None, existing_names: Optional[List[str]] = None):
        super().__init__(parent)
        self.setWindowTitle("Server Configuration")
        # If adding a new server, set default paths
        if server is None:
            self.server = {
                "models_path": "/v1/models",
                "chat_path": "/v1/chat/completions"
            }
        else:
            self.server = server
        self.original_name = self.server.get("name")
        self.result: Optional[dict] = None
        self.existing_names = existing_names or []
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QtWidgets.QFormLayout(self)
        self.name_edit = QtWidgets.QLineEdit(self.server.get("name", ""))
        self.base_edit = QtWidgets.QLineEdit(self.server.get("base_url", ""))
        self.auth_combo = QtWidgets.QComboBox()
        self.auth_combo.addItems(["proxy", "openai", "custom"])
        self.auth_combo.setCurrentText(self.server.get("auth_mode", "proxy"))
        self.api_secret_edit = QtWidgets.QLineEdit(self.server.get("api_secret", ""))
        self.api_key_edit = QtWidgets.QLineEdit(self.server.get("api_key", ""))
        self.custom_header_name_edit = QtWidgets.QLineEdit(self.server.get("custom_header_name", ""))
        self.custom_header_value_edit = QtWidgets.QLineEdit(self.server.get("custom_header_value", ""))
        self.models_path_edit = QtWidgets.QLineEdit(self.server.get("models_path", ""))
        self.chat_path_edit = QtWidgets.QLineEdit(self.server.get("chat_path", ""))
        self.timeout_edit = QtWidgets.QLineEdit(str(self.server.get("timeout") or ""))
        self.default_model_combo = QtWidgets.QComboBox()
        self.default_model_combo.setEnabled(False)
        self.default_model_combo.addItem(self.server.get("default_model", "gpt-3.5-turbo"))
        self.verify_ssl_check = QtWidgets.QCheckBox("Verify SSL certificates")
        self.verify_ssl_check.setChecked(bool(self.server.get("verify_ssl", True)))
        self.verify_button = QtWidgets.QPushButton("Verify")
        self.verify_button.clicked.connect(self._verify_server)
        self.verify_status_label = QtWidgets.QLabel("")
        self.verify_status_label.setWordWrap(True)
        self.duplicate_label = QtWidgets.QLabel("")
        self.duplicate_label.setStyleSheet("color: red; font-weight: bold;")
        self.duplicate_label.hide()
        layout.addRow("Name", self.name_edit)
        layout.addRow("Base URL", self.base_edit)
        layout.addRow("Auth Mode", self.auth_combo)
        layout.addRow("API Secret (proxy)", self.api_secret_edit)
        layout.addRow("API Key (OpenAI)", self.api_key_edit)
        layout.addRow("Custom Header Name", self.custom_header_name_edit)
        layout.addRow("Custom Header Value", self.custom_header_value_edit)
        layout.addRow("Models Path", self.models_path_edit)
        layout.addRow("Chat Path", self.chat_path_edit)
        layout.addRow("Timeout (seconds)", self.timeout_edit)
        layout.addRow("Default Model", self.default_model_combo)
        layout.addRow("Verify Server", self.verify_button)
        layout.addRow(self.verify_ssl_check)
        layout.addRow(self.duplicate_label)
        layout.addRow(self.verify_status_label)

        self.button_box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        self.button_box.accepted.connect(self._handle_accept)
        self.button_box.rejected.connect(self.reject)
        layout.addRow(self.button_box)
        self.ok_button = self.button_box.button(QtWidgets.QDialogButtonBox.Ok)
        if not self.original_name:
            self.ok_button.setText("Add")
        self.name_edit.textChanged.connect(self._validate_name)
        self._validate_name()

    def _verify_server(self) -> None:
        base_url = self.base_edit.text().strip()
        if not base_url:
            self._set_verify_status("Network error: Base URL is required.", success=False)
            self._set_models([])
            return
        auth_mode = self.auth_combo.currentText()
        models_path = self.models_path_edit.text().strip() or ("/mods" if auth_mode == "proxy" else "/v1/models")
        url = compose_url(base_url, models_path)
        headers = build_auth_headers(
            {
                "auth_mode": auth_mode,
                "api_secret": self.api_secret_edit.text().strip(),
                "api_key": self.api_key_edit.text().strip(),
                "custom_header_name": self.custom_header_name_edit.text().strip(),
                "custom_header_value": self.custom_header_value_edit.text().strip(),
            }
        )
        timeout = self._parse_timeout(default=10.0)
        verify_ssl = self.verify_ssl_check.isChecked()
        try:
            with httpx.Client(timeout=timeout, verify=verify_ssl) as client:
                resp = client.get(url, headers=headers)
                resp.raise_for_status()
                payload = resp.json()
        except Exception as exc:
            self._set_verify_status(f"Network error: {exc}", success=False)
            self._set_models([])
            return
        fallback_model = self.server.get("default_model", "gpt-3.5-turbo")
        models = self._parse_models(payload, fallback_model)
        self._set_models(models)
        self._set_verify_status("server is accessible.", success=True)

    def _set_verify_status(self, text: str, success: bool) -> None:
        self.verify_status_label.setText(text)
        color = "green" if success else "red"
        self.verify_status_label.setStyleSheet(f"color: {color}; font-weight: bold;")

    def _set_models(self, models: list[str]) -> None:
        self.default_model_combo.setEnabled(bool(models))
        current_default = self.server.get("default_model", "gpt-3.5-turbo")
        self.default_model_combo.blockSignals(True)
        self.default_model_combo.clear()
        if not models:
            self.default_model_combo.addItem(current_default)
            self.default_model_combo.setEnabled(False)
        else:
            self.default_model_combo.addItems(models)
            if current_default in models:
                self.default_model_combo.setCurrentText(current_default)
            else:
                self.default_model_combo.setCurrentIndex(0)
        self.default_model_combo.blockSignals(False)

    def _parse_models(self, payload: object, fallback_model: str) -> list[str]:
        model_ids: list[str] = []
        if isinstance(payload, dict):
            if "data" in payload and isinstance(payload["data"], list):
                data_list = payload["data"]
                if data_list and isinstance(data_list[0], dict):
                    model_ids = [item.get("id") for item in data_list if item.get("id")]
                else:
                    model_ids = [str(item) for item in data_list if item]
            elif "models" in payload and isinstance(payload["models"], list):
                model_ids = [str(item) for item in payload["models"] if item]
        elif isinstance(payload, list):
            if payload and isinstance(payload[0], dict):
                model_ids = [item.get("id") for item in payload if item.get("id")]
            else:
                model_ids = [str(item) for item in payload if item]
        cleaned = sorted({m for m in model_ids if m})
        return cleaned or [fallback_model]

    def _parse_timeout(self, default: float) -> float:
        timeout_raw = self.timeout_edit.text().strip()
        if not timeout_raw:
            return default
        try:
            return float(timeout_raw)
        except ValueError:
            return default

    def _handle_accept(self):
        name = self.name_edit.text().strip()
        base = self.base_edit.text().strip()
        if not name or not base:
            QtWidgets.QMessageBox.warning(self, "Validation", "Name and Base URL are required.")
            return
        if self._is_duplicate(name):
            self.duplicate_label.setText(f"Server name {name} already exists, choose a new name")
            self.duplicate_label.show()
            if self.ok_button:
                self.ok_button.setEnabled(False)
            return
        auth_mode = self.auth_combo.currentText()
        models_path = self.models_path_edit.text().strip() or ("/mods" if auth_mode == "proxy" else "/v1/models")
        chat_path = self.chat_path_edit.text().strip() or "/v1/chat/completions"

        timeout_raw = self.timeout_edit.text().strip()
        timeout_value = ""
        if timeout_raw:
            try:
                timeout_value = float(timeout_raw)
            except ValueError:
                timeout_value = timeout_raw

        self.result = {
            "name": name,
            "base_url": base,
            "auth_mode": auth_mode,
            "api_secret": self.api_secret_edit.text().strip(),
            "api_key": self.api_key_edit.text().strip(),
            "custom_header_name": self.custom_header_name_edit.text().strip(),
            "custom_header_value": self.custom_header_value_edit.text().strip(),
            "models_path": models_path,
            "chat_path": chat_path,
            "default_model": self.default_model_combo.currentText().strip() or "gpt-3.5-turbo",
            "verify_ssl": self.verify_ssl_check.isChecked(),
            "timeout": timeout_value,
            "original_name": self.original_name,
        }
        super().accept()

    def accept(self) -> None:
        name = self.name_edit.text().strip()
        base = self.base_edit.text().strip()
        if not name or not base:
            QtWidgets.QMessageBox.warning(self, "Validation", "Name and Base URL are required.")
            return
        if self._is_duplicate(name):
            self.duplicate_label.setText(f"Server name {name} already exists, choose a new name")
            self.duplicate_label.show()
            if self.ok_button:
                self.ok_button.setEnabled(False)
            return
        auth_mode = self.auth_combo.currentText()
        models_path = self.models_path_edit.text().strip() or ("/mods" if auth_mode == "proxy" else "/v1/models")
        chat_path = self.chat_path_edit.text().strip() or "/v1/chat/completions"

        timeout_raw = self.timeout_edit.text().strip()
        timeout_value = ""
        if timeout_raw:
            try:
                timeout_value = float(timeout_raw)
            except ValueError:
                timeout_value = timeout_raw

        self.result = {
            "name": name,
            "base_url": base,
            "auth_mode": auth_mode,
            "api_secret": self.api_secret_edit.text().strip(),
            "api_key": self.api_key_edit.text().strip(),
            "custom_header_name": self.custom_header_name_edit.text().strip(),
            "custom_header_value": self.custom_header_value_edit.text().strip(),
            "models_path": models_path,
            "chat_path": chat_path,
            "default_model": self.default_model_combo.currentText().strip() or "gpt-3.5-turbo",
            "verify_ssl": self.verify_ssl_check.isChecked(),
            "timeout": timeout_value,
            "original_name": self.original_name,
        }
        super().accept()

    def _is_duplicate(self, name: str) -> bool:
        name_clean = name.strip()
        if not name_clean:
            return False
        if name_clean == self.original_name:
            return False
        return name_clean in self.existing_names

    def _validate_name(self) -> None:
        name = self.name_edit.text().strip()
        if self._is_duplicate(name):
            self.duplicate_label.setText(f"Server name {name} already exists, choose a new name")
            self.duplicate_label.show()
            if self.ok_button:
                self.ok_button.setEnabled(False)
        else:
            self.duplicate_label.hide()
            if self.ok_button:
                self.ok_button.setEnabled(True)


class AIChatPanel(QtWidgets.QWidget):
    """Lightweight AI chat panel embedded in the right rail."""

    chatNavigateRequested = QtCore.Signal(str)
    responseCopied = QtCore.Signal(str)
    aiOverlayRequested = QtCore.Signal(str, object)

    def eventFilter(self, obj, event):  # type: ignore[override]
        if not hasattr(self, "input_edit"):
            return super().eventFilter(obj, event)
        if event.type() == QtCore.QEvent.KeyPress and event.key() == QtCore.Qt.Key_P:
            mods = event.modifiers()
            if mods & QtCore.Qt.ControlModifier and mods & QtCore.Qt.ShiftModifier:
                self._trigger_ai_overlay_from_chat()
                return True
        if obj is self.input_edit and event.type() == QtCore.QEvent.KeyPress:
            key_event = event  # type: ignore[assignment]
            if key_event.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
                modifiers = key_event.modifiers()
                if modifiers & QtCore.Qt.ControlModifier:
                    self._send_message()
                    return True
                if modifiers & QtCore.Qt.AltModifier:
                    self.input_edit.insertPlainText("\n")
                    return True
            if key_event.key() in (QtCore.Qt.Key_Up, QtCore.Qt.Key_Down) and not (
                key_event.modifiers() & ~(QtCore.Qt.KeypadModifier)
            ):
                cursor = self.input_edit.textCursor()
                doc = self.input_edit.document()
                if key_event.key() == QtCore.Qt.Key_Up and cursor.position() == 0:
                    if self._navigate_chat_history(-1):
                        return True
                if key_event.key() == QtCore.Qt.Key_Down and cursor.position() >= doc.characterCount() - 1:
                    if self._navigate_chat_history(1):
                        return True
            if key_event.key() == QtCore.Qt.Key_Space and not (key_event.modifiers() & ~QtCore.Qt.KeypadModifier):
                QtCore.QTimer.singleShot(0, self._maybe_open_context_picker)
        return super().eventFilter(obj, event)

    def __init__(self, parent=None, font_size=13, api_client: Optional[httpx.Client] = None):
        super().__init__(parent)
        self.vault_root = None
        self.store = AIChatStore()
        self.server_manager = ServerManager()
        self.current_server = self.server_manager.get_server(self.server_manager.get_active_server_name())
        self.messages = []
        self._api_worker = None
        self._condense_worker = None
        self.current_session_id = None
        self.current_page_path = None
        self.ai_manager: Optional[AIManager] = None
        self._current_ai_conversation_id: Optional[int] = None
        self._context_items: list[ContextItem] = []
        self._page_candidates: List[ContextCandidate] = []
        self._tree_candidates: List[ContextCandidate] = []
        self._attachment_candidates: List[ContextCandidate] = []
        self._context_reload_in_progress: bool = False
        self._context_reload_pending: bool = False
        self._context_overlay = ContextOverlay(self)
        self._context_overlay.selected.connect(self._handle_context_overlay_selected)
        self._context_popup = ContextListPopup(self)
        self._context_popup.activated.connect(self._open_context_item)
        self._context_popup.deleted.connect(self._context_popup_delete_handler)
        self._context_popup.refreshed.connect(self._context_popup_refresh_handler)
        self._context_popup_position: Optional[QtCore.QPoint] = None
        self._context_popup_width: Optional[int] = None
        self._vector_api = VectorAPIClient(api_client)
        self._api_client = api_client
        self._chat_history: List[str] = []
        self._chat_history_index: Optional[int] = None
        self._unsent_buffer: str = ""
        self._building_tree = False
        self._current_chat_path = None
        self.system_prompts: list[dict[str, str]] = []
        self.system_prompts_tree: dict = {}
        self.current_system_prompt = None
        self.font_size = config.load_panel_font_size("ai_chat_font_size", font_size)
        self.font_family = config.load_ai_chat_font_family() or self._default_chat_font_family()
        self.condense_prompt = self._load_condense_prompt()
        self._condense_buffer = ""
        self._summary_content = None
        self._cancel_pending_send = False
        self._cancel_pending_condense = False
        self._suppress_navigation = False
        # Stream coalescing to keep UI responsive
        self._pending_stream_chunks: dict[int, list[str]] = {}
        self._stream_flush_timer = QtCore.QTimer(self)
        self._stream_flush_timer.setInterval(40)
        self._stream_flush_timer.setSingleShot(True)
        self._stream_flush_timer.timeout.connect(self._flush_pending_stream_chunks)
        self._stream_think_state: dict[int, dict[str, str | bool]] = {}
        self._message_think: dict[int, str] = {}
        self._message_think_active: set[int] = set()
        self._message_think_expanded: set[int] = set()
        self._condense_think_state: dict[str, str | bool] = {
            "in_think": False,
            "pending": "",
            "visible": "",
        }
        self._condense_flush_timer = QtCore.QTimer(self)
        self._condense_flush_timer.setInterval(40)
        self._condense_flush_timer.setSingleShot(True)
        self._condense_flush_timer.timeout.connect(self._flush_condense_buffer)
        self._preserve_session_on_reset = False
        self._reset_keep_context = False
        self._build_ui()
        self._load_system_prompts()
        self._refresh_server_dropdown()
        self._refresh_model_dropdown(initial=True)
        self._load_chat_tree()
        self._select_default_chat()
        self._update_stop_button()
        self._reset_warning_shown = False

    def _config_default_server(self) -> Optional[str]:
        try:
            return stillpoint_config.load_default_ai_server()
        except Exception:
            return None

    def _config_default_model(self) -> Optional[str]:
        try:
            return stillpoint_config.load_default_ai_model()
        except Exception:
            return None

    def _build_ui(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)
        icon_row = QtWidgets.QHBoxLayout()
        icon_row.setSpacing(4)
        # Global chat button + context label
        global_icon_path = _get_asset_directory() / "global.svg"
        self.global_btn = QtWidgets.QToolButton()
        self.global_btn.setIcon(_load_white_icon(global_icon_path, QSize(18, 18)))
        self.global_btn.setToolTip("Switch to Global Chat")
        self.global_btn.setAutoRaise(True)
        self.global_btn.clicked.connect(lambda: self.open_chat_for_page(None))
        icon_row.addWidget(self.global_btn)
        self.context_label = QtWidgets.QLabel("")
        self.context_label.setStyleSheet("color: #e0e0e0; font-weight: bold;")
        icon_row.addWidget(self.context_label)
        icon_row.addStretch()
        # Move toggles to the right
        self.server_config_btn = QtWidgets.QToolButton()
        settings_path = _get_asset_directory() / "settings.svg"
        self.server_config_btn.setIcon(_load_white_icon(settings_path, QSize(18, 18)))
        self.server_config_btn.setToolTip("Server Configurations")
        self.server_config_btn.setCheckable(True)
        self.server_config_btn.setChecked(False)
        self.server_config_btn.toggled.connect(self._toggle_server_config)
        self.show_chats_btn = QtWidgets.QToolButton()
        binoculars_path = _get_asset_directory() / "binoculars.svg"
        self.show_chats_btn.setIcon(_load_white_icon(binoculars_path, QSize(20, 20)))
        self.show_chats_btn.setCheckable(True)
        self.show_chats_btn.setChecked(False)
        self.show_chats_btn.setToolTip("Show chats")
        self.show_chats_btn.toggled.connect(self._toggle_chat_list)
        self.zoom_out_btn = QtWidgets.QToolButton()
        self.zoom_out_btn.setText("−")
        self.zoom_out_btn.setToolTip("Decrease font size")
        self.zoom_out_btn.setAutoRaise(True)
        self.zoom_out_btn.setFixedSize(22, 22)
        self.zoom_out_btn.clicked.connect(lambda: self.set_font_size(self.font_size - 1))
        self.zoom_in_btn = QtWidgets.QToolButton()
        self.zoom_in_btn.setText("+")
        self.zoom_in_btn.setToolTip("Increase font size")
        self.zoom_in_btn.setAutoRaise(True)
        self.zoom_in_btn.setFixedSize(22, 22)
        self.zoom_in_btn.clicked.connect(lambda: self.set_font_size(self.font_size + 1))
        icon_row.addWidget(self.show_chats_btn)
        icon_row.addWidget(self.server_config_btn)
        icon_row.addWidget(self.zoom_out_btn)
        icon_row.addWidget(self.zoom_in_btn)
        self.load_page_chat_label = ClickableLabel("")
        self.load_page_chat_label.setStyleSheet("color:#cc2222; font-weight:bold;")
        self.load_page_chat_label.setVisible(False)
        self.load_page_chat_label.clicked.connect(self._load_current_page_chat)
        icon_row.addWidget(self.load_page_chat_label)
        layout.addLayout(icon_row)
        self.server_config_widget = QtWidgets.QWidget()
        self.server_config_widget.setVisible(False)
        cfg_layout = QtWidgets.QVBoxLayout(self.server_config_widget)
        cfg_layout.setContentsMargins(0, 0, 0, 0)
        cfg_layout.setSpacing(4)
        server_row = QtWidgets.QHBoxLayout()
        server_row.addWidget(QtWidgets.QLabel("Server:"))
        self.server_combo = QtWidgets.QComboBox()
        self.server_combo.currentIndexChanged.connect(self._on_server_selected)
        server_row.addWidget(self.server_combo, 1)
        cfg_layout.addLayout(server_row)
        model_row = QtWidgets.QHBoxLayout()
        model_row.addWidget(QtWidgets.QLabel("Model:"))
        self.model_combo = QtWidgets.QComboBox()
        self.model_combo.currentTextChanged.connect(self._on_model_selected)
        model_row.addWidget(self.model_combo, 1)
        refresh_models_btn = QtWidgets.QPushButton("Refresh Models")
        refresh_models_btn.clicked.connect(self._refresh_models_from_server)
        model_row.addWidget(refresh_models_btn)
        self.prompt_btn = QtWidgets.QPushButton("System Prompts")
        self.prompt_btn.clicked.connect(self._open_prompt_dialog)
        self.prompt_btn.setVisible(False)
        model_row.addWidget(self.prompt_btn)
        # Use a compact font size for model/server controls
        compact_css = "font-size: 12px;"
        self.server_config_widget.setStyleSheet(compact_css)
        self.prompt_btn.setStyleSheet(compact_css)
        refresh_models_btn.setStyleSheet(compact_css)
        model_row.addWidget(self.prompt_btn)
        cfg_layout.addLayout(model_row)
        layout.addWidget(self.server_config_widget)
        self.context_bar = QtWidgets.QWidget()
        context_layout = QtWidgets.QHBoxLayout(self.context_bar)
        context_layout.setContentsMargins(4, 2, 4, 2)
        self.context_refresh_btn = QtWidgets.QToolButton()
        self.context_refresh_btn.setText("↺")
        self.context_refresh_btn.setToolTip("Refresh current page context")
        self.context_refresh_btn.setAutoRaise(True)
        self.context_refresh_btn.clicked.connect(self._refresh_current_page_context)
        context_layout.addWidget(self.context_refresh_btn)
        self.context_summary_label = ClickableLabel("Context: —")
        self.context_summary_label.setStyleSheet("color: #007acc; text-decoration: underline;")
        self.context_summary_label.clicked.connect(self._show_context_popup)
        context_layout.addWidget(self.context_summary_label)
        context_layout.addStretch()
        layout.addWidget(self.context_bar)
        self.splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        left_container = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left_container)
        left_layout.setContentsMargins(4, 4, 4, 4)
        left_layout.setSpacing(4)
        header_row = QtWidgets.QHBoxLayout()
        header_row.addWidget(QtWidgets.QLabel("Chat Folders"))
        header_row.addStretch()
        left_layout.addLayout(header_row)
        self.chat_tree = QtWidgets.QTreeWidget()
        self.chat_tree.setHeaderHidden(True)
        self.chat_tree.itemSelectionChanged.connect(self._on_chat_selected)
        self.chat_tree.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.chat_tree.customContextMenuRequested.connect(self._on_chat_context_menu)
        left_layout.addWidget(self.chat_tree, 1)
        self.chat_tree_container = left_container
        self.splitter.addWidget(left_container)

        right_container = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right_container)
        right_layout.setContentsMargins(4, 4, 4, 4)
        right_layout.setSpacing(4)
        chat_split = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        right_layout.addWidget(chat_split, 1)
        self.chat_view = QtWidgets.QTextBrowser()
        self.chat_view.setOpenExternalLinks(False)
        self.chat_view.setOpenLinks(False)
        self.chat_view.anchorClicked.connect(self._on_anchor_clicked)
        self.chat_view.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.chat_view.customContextMenuRequested.connect(self._on_history_context_menu)
        self.chat_view.setReadOnly(True)
        self.chat_view.setStyleSheet(
            "QTextBrowser {"
            "  padding: 6px;"
            "  background: #0b0b0b;"
            "  color: #d6f5d6;"
            "  border: 1px solid #1f1f1f;"
            "}"
        )
        self.chat_view.installEventFilter(self)
        self._apply_font_size()
        chat_split.addWidget(self.chat_view)
        # Compact input row: two-line textbox with controls on the right
        input_container = QtWidgets.QWidget()
        input_layout = QtWidgets.QHBoxLayout(input_container)
        input_layout.setContentsMargins(0, 0, 0, 0)
        input_layout.setSpacing(6)
        self.input_edit = QtWidgets.QPlainTextEdit()
        self.input_edit.setPlaceholderText("Ask anything…")
        metrics = self.input_edit.fontMetrics()
        line_height = metrics.lineSpacing()
        two_line_height = line_height * 2 + 6
        self.input_edit.setFixedHeight(two_line_height)
        self.input_edit.installEventFilter(self)
        input_layout.addWidget(self.input_edit, 1)
        btn_col = QtWidgets.QVBoxLayout()
        btn_col.setSpacing(4)
        self.condense_btn = QtWidgets.QToolButton()
        self.condense_btn.setToolTip("Condense Chat")
        condense_icon = _load_icon("condense.svg", QSize(10, 10))
        self.condense_btn.setIcon(condense_icon)
        self.condense_btn.setIconSize(QSize(10, 10))
        self.condense_btn.clicked.connect(self._condense_chat)
        self.condense_btn.setFixedWidth(28)
        self.stop_btn = QtWidgets.QToolButton()
        self.stop_btn.setToolTip("Stop current request")
        stop_icon = _load_icon("cancel.svg", QSize(10, 10))
        self.stop_btn.setIcon(stop_icon)
        self.stop_btn.setIconSize(QSize(10, 10))
        self.stop_btn.clicked.connect(self._cancel_active_operation)
        self.stop_btn.setFixedWidth(28)
        self.stop_btn.setStyleSheet("background:#e53935; color:white; border:1px solid #c62828;")
        self.send_btn = QtWidgets.QToolButton()
        self.send_btn.setToolTip("Send message (Ctrl+Enter)")
        send_icon = _load_icon("send-message.svg", QSize(10, 10))
        self.send_btn.setIcon(send_icon)
        self.send_btn.setIconSize(QSize(10, 10))
        self.send_btn.clicked.connect(self._send_message)
        self.send_btn.setFixedWidth(28)
        self.reset_btn = QtWidgets.QToolButton()
        self.reset_btn.setText("↺")
        self.reset_btn.setToolTip("Reset chat history")
        self.reset_btn.clicked.connect(self._show_reset_warning)
        self.reset_btn.setFixedWidth(28)
        top_btn_row = QtWidgets.QHBoxLayout()
        top_btn_row.setSpacing(4)
        top_btn_row.addWidget(self.condense_btn)
        top_btn_row.addWidget(self.reset_btn)
        top_btn_row.addStretch()
        btn_col.addLayout(top_btn_row)
        for btn in (self.send_btn, self.stop_btn):
            btn_col.addWidget(btn)
        btn_col.addStretch()
        input_layout.addLayout(btn_col)
        self.status_label = QtWidgets.QLabel()
        right_layout.addWidget(self.status_label)
        self.model_status_label = QtWidgets.QLabel()
        self.model_status_label.setStyleSheet("font-size: 10px; color: #888;")
        self.model_status_label.setWordWrap(False)
        self.model_status_label.setMinimumWidth(0)
        self.model_status_label.setMaximumWidth(500)
        self.model_status_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        right_layout.addWidget(self.model_status_label)
        right_layout.addWidget(input_container)
        self.splitter.addWidget(right_container)
        layout.addWidget(self.splitter, 1)
        self._toggle_chat_list(False)
        self._update_context_summary()
        self._apply_font_size()

    def _reset_chat_history(self) -> None:
        print("[AIChat][reset] Starting chat reset.")
        if not self.current_session_id or not self.store:
            print("[AIChat][reset] No current_session_id or store; aborting.")
            return

        session_id = self.current_session_id
        if self._preserve_session_on_reset:
            if not self._reset_keep_context:
                if self._context_items:
                    for item in list(self._context_items):
                        self._delete_context_source(item)
                if self.ai_manager and self._current_ai_conversation_id:
                    try:
                        self.ai_manager.clear_context_items(self._current_ai_conversation_id)
                    except Exception:
                        import traceback
                        traceback.print_exc()
                self._context_items = []
                self._current_ai_conversation_id = None
                self._update_context_summary()
            self.messages = []
            self.chat_view.clear()
            self._context_overlay.hide()
            self._context_popup.hide()
            try:
                self.store.clear_chat(session_id)
            except Exception as exc:
                print(f"[AIChat][reset] Failed to clear chat {session_id}: {exc}")
            self._condense_buffer = ""
            self._summary_content = None
            self._message_think.clear()
            self._message_think_active.clear()
            self._message_think_expanded.clear()
            self._condense_think_state = {"in_think": False, "pending": "", "visible": ""}
            self._context_popup_position = None
            self._context_popup_width = None
            self._set_status("Chat history cleared.")
            print("[AIChat][reset] Finished _reset_chat_history")
            return

        if self._context_items:
            for item in list(self._context_items):
                self._delete_context_source(item)
        if self.ai_manager and self._current_ai_conversation_id:
            try:
                self.ai_manager.clear_context_items(self._current_ai_conversation_id)
            except Exception:
                import traceback
                traceback.print_exc()
        self._context_items = []
        self._current_ai_conversation_id = None
        self._update_context_summary()
        self.messages = []
        self.chat_view.clear()
        self._context_overlay.hide()
        self._context_popup.hide()
        try:
            self.store.delete_session(session_id)
        except Exception as exc:
            print(f"[AIChat][reset] Failed to delete session {session_id}: {exc}")
        self.current_session_id = None
        self._condense_buffer = ""
        self._summary_content = None
        self._message_think.clear()
        self._message_think_active.clear()
        self._message_think_expanded.clear()
        self._condense_think_state = {"in_think": False, "pending": "", "visible": ""}
        self._context_popup_position = None
        self._context_popup_width = None
        self._load_chat_tree()
        if self._ensure_active_chat() and self.current_session_id:
            self._load_chat_messages(self.current_session_id)
        self._set_status("Chat history cleared.")
        print("[AIChat][reset] Finished _reset_chat_history")

    def set_preserve_session_on_reset(self, preserve: bool, *, keep_context: bool = False) -> None:
        """Control whether reset clears messages but keeps the current chat session."""
        self._preserve_session_on_reset = bool(preserve)
        self._reset_keep_context = bool(keep_context)

    def _load_condense_prompt(self) -> str:
        """Load the condense prompt from file or fall back to a default."""
        # Prefer a vault-specific prompt if present, otherwise try bundled paths.
        candidates = []
        if self.vault_root:
            candidates.append(Path(self.vault_root) / ".stillpoint" / "condense_prompt.txt")
        candidates.append(PROJECT_ROOT / "sp" / "app" / "condense_prompt.txt")
        candidates.append(Path(__file__).resolve().parent.parent / "condense_prompt.txt")
        for path in candidates:
            try:
                if path.exists():
                    return path.read_text(encoding="utf-8").strip()
            except Exception:
                continue
        print("[AIChat][condense] Using fallback condense prompt; file not found.")
        return "You are a helpful assistant. Summarize the following chat history."

    def focusInEvent(self, event):  # type: ignore[override]
        # Update server dropdown to reflect latest config state
        self._refresh_server_dropdown()
        # Re-apply the current font size to avoid focus-triggered resets
        try:
            self._apply_font_size()
        except Exception:
            pass
        super().focusInEvent(event)
        # When the chat panel receives focus, ensure the send / input box actually receives focus
        try:
            if hasattr(self, "input_edit") and self.input_edit is not None:
                self.input_edit.setFocus(Qt.OtherFocusReason)
        except Exception:
            pass

    def _toggle_chat_list(self, checked: bool) -> None:
        self.chat_tree_container.setVisible(checked)
        self.show_chats_btn.setToolTip("Hide chats" if checked else "Show chats")
        if checked:
            self.splitter.setSizes([240, 760])
        else:
            self.splitter.setSizes([0, 1])

    def _on_chat_context_menu(self, pos: QtCore.QPoint) -> None:
        item = self.chat_tree.itemAt(pos)
        if not item:
            return
        data = item.data(0, QtCore.Qt.UserRole) or {}
        if data.get("type") != "chat":
            return
        menu = QtWidgets.QMenu(self)
        go_action = menu.addAction("Go To Page")
        go_action.triggered.connect(lambda: self._go_to_page_for_chat(data))
        delete_action = menu.addAction("Delete Chat")
        delete_action.triggered.connect(lambda: self._delete_chat_session(data))
        menu.exec(self.chat_tree.viewport().mapToGlobal(pos))

    def _toggle_server_config(self, checked: bool) -> None:
        self.server_config_widget.setVisible(checked)
        self.server_config_btn.setToolTip("Hide Config" if checked else "Server Config")
        self.prompt_btn.setVisible(checked)
        # Do not auto-refresh models on config toggle

    def _has_active_operation(self) -> bool:
        return bool(self._api_worker or self._condense_worker)

    def _update_stop_button(self) -> None:
        if hasattr(self, "stop_btn"):
            self.stop_btn.setVisible(self._has_active_operation())

    def _set_status(self, text: str, color: str | None = None) -> None:
        self.status_label.setText(text)
        if color:
            self.status_label.setStyleSheet(f"color: {color}; font-weight: bold;")
        else:
            self.status_label.setStyleSheet("")

    def _strip_think_blocks(self, text: str) -> str:
        if not text:
            return ""
        cleaned = re.sub(r"<think\b[^>]*>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
        cleaned = re.sub(r"<think\b[^>]*/>", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"<think\b[^>]*>.*", "", cleaned, flags=re.DOTALL | re.IGNORECASE)
        return cleaned

    def _extract_think_blocks(self, text: str) -> str:
        if not text:
            return ""
        matches = re.findall(r"<think\b[^>]*>(.*?)</think>", text, flags=re.DOTALL | re.IGNORECASE)
        if not matches:
            return ""
        return "\n\n".join(match.strip() for match in matches if match.strip())

    def _extract_think_pending(self, tail: str, marker: str) -> str:
        if not tail:
            return ""
        max_len = len(marker) - 1
        snippet = tail[-max_len:] if len(tail) > max_len else tail
        last_lt = snippet.rfind("<")
        if last_lt == -1:
            return snippet if marker.startswith(snippet) else ""
        candidate = snippet[last_lt:]
        return candidate if marker.startswith(candidate) else ""

    def _apply_stream_think_chunk(self, idx: int, chunk: str) -> None:
        state = self._stream_think_state.setdefault(
            idx, {"in_think": False, "pending": "", "visible": "", "think": ""}
        )
        text = f"{state['pending']}{chunk}"
        state["pending"] = ""
        visible_append = ""
        think_append = ""
        i = 0
        while i < len(text):
            if state["in_think"]:
                end = text.find("</think>", i)
                if end == -1:
                    trailing = text[i:]
                    pending = self._extract_think_pending(trailing, "</think>")
                    if pending:
                        think_append += trailing[: -len(pending)]
                        state["pending"] = pending
                    else:
                        think_append += trailing
                    break
                think_append += text[i:end]
                i = end + len("</think>")
                state["in_think"] = False
                continue
            start = text.find("<think", i)
            if start == -1:
                trailing = text[i:]
                pending = self._extract_think_pending(trailing, "<think")
                if pending:
                    visible_append += trailing[: -len(pending)]
                    state["pending"] = pending
                else:
                    visible_append += trailing
                break
            visible_append += text[i:start]
            tag_end = text.find(">", start)
            if tag_end == -1:
                state["pending"] = text[start:]
                break
            tag_text = text[start : tag_end + 1]
            if tag_text.endswith("/>"):
                i = tag_end + 1
                continue
            state["in_think"] = True
            i = tag_end + 1
        state["visible"] = f"{state['visible']}{visible_append}"
        state["think"] = f"{state['think']}{think_append}"
        display_text = state["visible"]
        if 0 <= idx < len(self.messages):
            role, _ = self.messages[idx]
            if role == "assistant":
                self.messages[idx] = (role, display_text)
        if state["think"]:
            self._message_think[idx] = state["think"]
        if state["in_think"]:
            self._message_think_active.add(idx)
        else:
            self._message_think_active.discard(idx)

    def _apply_condense_think_chunk(self, chunk: str) -> None:
        state = self._condense_think_state
        text = f"{state['pending']}{chunk}"
        state["pending"] = ""
        visible_append = ""
        i = 0
        while i < len(text):
            if state["in_think"]:
                end = text.find("</think>", i)
                if end == -1:
                    state["pending"] = self._extract_think_pending(text[i:], "</think>")
                    break
                i = end + len("</think>")
                state["in_think"] = False
                continue
            start = text.find("<think", i)
            if start == -1:
                trailing = text[i:]
                pending = self._extract_think_pending(trailing, "<think")
                if pending:
                    visible_append += trailing[: -len(pending)]
                    state["pending"] = pending
                else:
                    visible_append += trailing
                break
            visible_append += text[i:start]
            tag_end = text.find(">", start)
            if tag_end == -1:
                state["pending"] = text[start:]
                break
            tag_text = text[start : tag_end + 1]
            if tag_text.endswith("/>"):
                i = tag_end + 1
                continue
            state["in_think"] = True
            i = tag_end + 1
        state["visible"] = f"{state['visible']}{visible_append}"
        display_text = state["visible"]
        if state["in_think"]:
            display_text = f"{display_text}\n\nThinking..." if display_text.strip() else "Thinking..."
        self._condense_buffer = display_text

    def _render_messages(self) -> None:
        parts: List[str] = []
        base_color = "#0b0b0b"
        text_color = "#d6f5d6"
        accent = "#7fd4a7"
        parts.append(
            f"<style>body {{ background:{base_color}; color:{text_color}; font-family: \"Courier New\", monospace; }}"
            f".bubble {{ position:relative; border-radius:6px; padding:6px 8px 12px; margin-bottom:8px; }}"
            f".bubble:hover {{ background:rgba(255,255,255,0.04); }}"
            f".actions {{ text-align:left; display:none; margin-top:6px; margin-left:0; }}"
            f".bubble:hover .actions {{ display:block; }}"
            f".actions a {{ margin-right:12px; margin-left:0; text-decoration:none; color:{accent}; }}"
            f".user {{ color:#f2e7a1; background:transparent; }}"
            f".assistant {{ color:#8fe39b; background:transparent; }}"
            f".summary {{ border:1px solid #2f4f2f; }}"
            f".think-toggle {{ margin-top:6px; color:{accent}; text-decoration:none; display:inline-block; }}"
            f".think-active a {{ animation: thinkPulse 1.2s infinite; }}"
            f".think-body {{ margin-top:6px; padding:6px; border:2px solid #ffffff;"
            f" background:rgba(0,0,0,0.35); max-height:7em; overflow:auto; white-space:pre-wrap;"
            f" color:#bdbdbd; }}"
            f"@keyframes thinkPulse {{ 0% {{ opacity:0.4; }} 50% {{ opacity:1; }} 100% {{ opacity:0.4; }} }}"
            f".role {{ font-weight:bold; color:{accent}; }}</style>"
        )
        self._message_map = {}
        for idx, (role, content) in enumerate(self.messages):
            cls = "assistant" if role == "assistant" else "user"
            msg_id = f"msg-{idx}"
            linkified = self._linkify_vault_paths(content)
            rendered = markdown(linkified, extensions=["fenced_code", "tables"])
            if self._is_plain_markdown(rendered):
                if "<a href=" in linkified:
                    rendered = f"<p>{linkified.replace(chr(10), '<br>')}</p>"
                else:
                    safe = html.escape(linkified).replace("\n", "<br>")
                    rendered = f"<p>{safe}</p>"
            actions = [
                f"<a href='action:copy:{msg_id}'>{_icon_tag('copy.svg','Copy message', 20)}</a>",
                f"<a href='action:goto:{msg_id}'>{_icon_tag('go-to-top.svg','Go to start', 20)}</a>",
                f"<a href='action:delete:{msg_id}'>{_icon_tag('icons8-trash.svg','Delete message', 20)}</a>",
            ]
            think_html = ""
            if role == "assistant":
                think_text = self._message_think.get(idx, "")
                if think_text or idx in self._message_think_active:
                    label = "Hide thinking" if idx in self._message_think_expanded else "Thinking..."
                    safe_think = html.escape(think_text).replace("\n", "<br>")
                    if not safe_think:
                        safe_think = "<em>...</em>"
                    body = (
                        f"<div class='think-body'>{safe_think}</div>"
                        if idx in self._message_think_expanded
                        else ""
                    )
                    active_class = " think-active" if idx in self._message_think_active else ""
                    think_html = (
                        f"<div class='think-toggle{active_class}'>"
                        f"<a href='action:think:{msg_id}' title='Toggle thinking details'>{label}</a>"
                        f"{body}</div>"
                    )
            parts.append(
                f"<div class='bubble {cls}' id='{msg_id}'><a name='{msg_id}' href='msg:{msg_id}'></a>"
                f"<span class='role'>{role.title()}:</span><br>{think_html}{rendered}"
                f"<div class='actions'>{' | '.join(actions)}</div>"
                f"</div>"
            )
            self._message_map[msg_id] = (role, content)
        if self._condense_buffer or self._summary_content:
            summary_text = self._summary_content or self._condense_buffer
            actions = []
            if self._summary_content:
                actions = [
                    "<a href='action:summary:accept' title='Accept condensed chat'>Accept</a>",
                    "<a href='action:summary:reject' title='Reject condensed chat'>Reject</a>",
                ]
            summary_linkified = self._linkify_vault_paths(summary_text)
            summary_html = markdown(summary_linkified, extensions=["fenced_code", "tables"])
            if self._is_plain_markdown(summary_html):
                if "<a href=" in summary_linkified:
                    summary_html = f"<p>{summary_linkified.replace(chr(10), '<br>')}</p>"
                else:
                    safe = html.escape(summary_linkified).replace("\n", "<br>")
                    summary_html = f"<p>{safe}</p>"
            parts.append(
                f"<div class='bubble summary' id='summary'><a name='summary'></a>"
                f"<span class='role'>Summary:</span><br>{summary_html}"
                f"{'<div class=\"actions\">' + ' | '.join(actions) + '</div>' if actions else ''}"
                f"</div>"
            )
        self.chat_view.setHtml("".join(parts))
        cursor = self.chat_view.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.chat_view.setTextCursor(cursor)

    def _linkify_vault_paths(self, text: str) -> str:
        if not text:
            return text
        fenced = re.split(r"(```.*?```)", text, flags=re.DOTALL)
        for idx, chunk in enumerate(fenced):
            if chunk.startswith("```"):
                continue
            fenced[idx] = self._linkify_inline_paths(chunk)
        return "".join(fenced)

    def _linkify_inline_paths(self, text: str) -> str:
        segments = re.split(r"(`[^`]*`)", text)
        for idx, seg in enumerate(segments):
            if seg.startswith("`"):
                continue
            segments[idx] = self._replace_path_tokens(seg)
        return "".join(segments)

    def _replace_path_tokens(self, text: str) -> str:
        pattern = re.compile(
            rf"(^|[\s\(\[\"'])"
            rf"(/[^\s`<>\"'()\]]+(?:{re.escape(PAGE_SUFFIX)}|{re.escape(LEGACY_SUFFIX)})?)"
        )

        def _replace(match: re.Match[str]) -> str:
            prefix = match.group(1)
            raw_path = match.group(2)
            trimmed = raw_path.rstrip(",:;.!?")
            trailing = raw_path[len(trimmed):]
            if not trimmed:
                return f"{prefix}{raw_path}"
            colon = path_to_colon(trimmed)
            if not colon:
                return f"{prefix}{raw_path}"
            colon = f":{colon}"
            label = Path(trimmed).stem or colon.split(":")[-1]
            safe_label = html.escape(label, quote=False)
            safe_colon = html.escape(colon, quote=True)
            return f"{prefix}<a href=\"{safe_colon}\" title=\"{safe_colon}\">{safe_label}</a>{trailing}"

        return pattern.sub(_replace, text)

    def _refresh_context_items(self) -> None:
        if not self.ai_manager or not self._current_ai_conversation_id:
            self._context_items = []
            self._update_context_summary()
            return
        try:
            self._context_items = self.ai_manager.list_context_items(self._current_ai_conversation_id)
        except Exception:
            self._context_items = []
        self._update_context_summary()

    def _ensure_page_context_added(self) -> None:
        if not self.ai_manager or not self._current_ai_conversation_id or not self.current_page_path:
            return
        if any(item.kind == "page" and item.page_ref == self.current_page_path for item in self._context_items):
            return
        try:
            self.ai_manager.add_context_page(self._current_ai_conversation_id, self.current_page_path)
            self._index_context_item("@", ContextCandidate(page_ref=self.current_page_path, label=self.current_page_path))
        except Exception:
            return
        self._refresh_context_items()

    def _bind_ai_conversation(self) -> None:
        if not self.ai_manager or not self.current_session_id:
            self._current_ai_conversation_id = None
            self._refresh_context_items()
            return
        session = self.store.get_session_by_id(self.current_session_id)
        if not session:
            self._current_ai_conversation_id = None
            self._refresh_context_items()
            return
        conv_id = session.get("ai_conversation_id")
        conv = self.ai_manager.get_conversation(conv_id) if conv_id else None
        if not conv:
            title = session.get("name") or "Chat"
            if self.current_page_path:
                conv = self.ai_manager.get_or_create_page_chat(self.current_page_path, title=title)
            else:
                conv = self.ai_manager.create_global_chat(title=title)
            conv_id = conv.id
            try:
                self.store.update_session_ai_conversation(self.current_session_id, conv_id)
            except Exception:
                pass
        self._current_ai_conversation_id = conv_id
        self._refresh_context_items()
        self._ensure_page_context_added()

    def _update_context_summary(self) -> None:
        counts = Counter(item.kind for item in self._context_items)
        tooltip = "No context selected yet."
        page_items = [item for item in self._context_items if item.kind == "page"]
        primary_page = page_items[0].page_ref if page_items else None
        base_text = "None"
        if primary_page:
            display_primary = Path(primary_page).stem
            parent = Path(primary_page).parent
            if parent and parent.parts:
                base_text = f"...{display_primary}"
            else:
                base_text = display_primary
            tooltip = self._context_item_label(page_items[0])
        elif counts and any(counts.values()):
            parts: List[str] = []
            page_count = counts.get("page", 0)
            tree_count = counts.get("page-tree", 0)
            attachment_count = counts.get("attachment", 0)
            if page_count:
                suffix = "s" if page_count != 1 else ""
                parts.append(f"{page_count} page{suffix}")
            if tree_count:
                suffix = "s" if tree_count != 1 else ""
                parts.append(f"{tree_count} tree{suffix}")
            if attachment_count:
                suffix = "s" if attachment_count != 1 else ""
                parts.append(f"{attachment_count} attachment{suffix}")
            base_text = " • ".join(parts)
            tooltip = "\n".join(self._context_item_label(item) for item in self._context_items)
        counts_parts: List[str] = []
        page_count = counts.get("page", 0)
        tree_count = counts.get("page-tree", 0)
        attachment_count = counts.get("attachment", 0)
        other_page_count = max(page_count - (1 if primary_page else 0), 0)
        if other_page_count > 0:
            suffix = "s" if other_page_count != 1 else ""
            counts_parts.append(f"{other_page_count} other{suffix}")
        if tree_count:
            suffix = "s" if tree_count != 1 else ""
            counts_parts.append(f"{tree_count} tree{suffix}")
        if attachment_count:
            suffix = "s" if attachment_count != 1 else ""
            counts_parts.append(f"{attachment_count} attachment{suffix}")
        if counts_parts and base_text:
            summary = f"Context: {base_text} and ({', '.join(counts_parts)})"
        else:
            summary = f"Context: {base_text}"
        self.context_summary_label.setText(summary)
        self.context_summary_label.setToolTip(tooltip)

    def _page_label(self, page_ref: str) -> str:
        normalized = page_ref.lstrip("/")
        if ":" in normalized and "/" not in normalized:
            parts = [segment for segment in normalized.split(":") if segment]
            if parts:
                tail = parts[-1]
                return f"...{tail}" if len(parts) > 1 else tail
        candidate = Path(normalized)
        return candidate.stem or normalized

    def _context_item_label(self, item: ContextItem) -> str:
        if item.kind == "page":
            return f"Page: {self._page_label(item.page_ref)}"
        if item.kind == "page-tree":
            return f"Tree: {item.page_ref}"
        if item.kind == "attachment":
            if item.attachment_name:
                return f"Attachment: {self._page_label(item.page_ref)} • {item.attachment_name}"
            return f"Attachment: {item.page_ref}"
        return item.page_ref

    def _show_context_popup(self) -> None:
        if self._is_global_chat_path(self._current_chat_path):
            return
        if not self._context_items:
            QtWidgets.QMessageBox.information(self, "Context", "No context items are configured yet.")
            return
        target = getattr(self, "chat_view", self)
        offset = QtCore.QPoint(0, 6)
        point = target.mapToGlobal(QtCore.QPoint(0, 0)) + offset
        width_hint = target.width() or self.width() or 480
        width_hint = max(480, width_hint)
        self._context_popup_position = point
        self._context_popup.show_for(self._context_items, point, width_hint=width_hint)
        self._context_popup_width = self._context_popup.width()

    def _open_context_item(self, item: ContextItem) -> None:
        if item.page_ref:
            self.chatNavigateRequested.emit(item.page_ref)

    def _detect_context_trigger(self) -> Optional[str]:
        text = self.input_edit.toPlainText()
        cursor = self.input_edit.textCursor()
        pos = cursor.position()
        if pos < 2:
            return None
        snippet = text[pos - 2 : pos]
        if snippet in ("@ ", "# ", "! "):
            return snippet[0]
        return None

    def _maybe_open_context_picker(self) -> None:
        self._try_open_context_picker(attempt=0)

    def _try_open_context_picker(self, attempt: int) -> None:
        if self._is_global_chat_path(self._current_chat_path):
            return
        trigger = self._detect_context_trigger()
        if not trigger:
            return
        if not self._ensure_active_chat():
            QtWidgets.QMessageBox.information(self, "Context", "Open or create a chat before adding context.")
            return
        if not self.ai_manager:
            return
        candidates = self._candidates_for_trigger(trigger)
        if not candidates and trigger in ("@", "#") and self._api_client_available():
            pages, trees = self._fetch_context_candidates_from_api()
            if pages or trees:
                self._page_candidates = pages
                self._tree_candidates = trees
                candidates = self._candidates_for_trigger(trigger)
        if not candidates:
            if trigger in ("@", "#") and self.vault_root:
                if self._context_reload_in_progress:
                    self._set_status("Building context index…", "#f6c343")
                    if attempt < 6:
                        QtCore.QTimer.singleShot(250, lambda: self._try_open_context_picker(attempt + 1))
                    return
                self._reload_context_index()
                self._set_status("Building context index…", "#f6c343")
                if attempt < 6:
                    QtCore.QTimer.singleShot(250, lambda: self._try_open_context_picker(attempt + 1))
                return
            QtWidgets.QMessageBox.information(self, "Context", "No context items are available.")
            return
        cursor_rect = self.input_edit.cursorRect()
        point = self.input_edit.mapToGlobal(cursor_rect.bottomLeft())
        self._context_overlay.show_for(trigger, list(candidates), point)

    def _candidates_for_trigger(self, trigger: str) -> List[ContextCandidate]:
        if trigger == "@":
            return self._page_candidates
        if trigger == "#":
            return self._tree_candidates
        if trigger == "!":
            return self._attachment_candidates_for_trigger()
        return []

    def _attachment_candidates_for_trigger(self) -> List[ContextCandidate]:
        if not self.vault_root:
            return []
        pages = self._attachment_context_pages()
        seen: set[tuple[str, str]] = set()
        candidates: list[ContextCandidate] = []
        for page_ref in sorted(pages):
            for attachment in self._attachments_in_page(page_ref):
                key = (page_ref, attachment.name)
                if key in seen:
                    continue
                seen.add(key)
                display = f"{page_ref} · {attachment.name}"
                candidates.append(
                    ContextCandidate(
                        page_ref=page_ref,
                        label=display,
                        attachment_name=attachment.name,
                    )
                )
        return candidates

    def _attachment_context_pages(self) -> set[str]:
        pages: set[str] = set()
        if self.current_page_path:
            pages.add(self.current_page_path)
        for item in self._context_items:
            if item.kind == "page" and item.page_ref:
                pages.add(item.page_ref)
            elif item.kind == "page-tree" and item.page_ref:
                pages.update(self._pages_under_tree(item.page_ref))
        return pages

    def _pages_under_tree(self, tree_ref: str) -> set[str]:
        if not self.vault_root:
            return set()
        root = Path(self.vault_root)
        rel = tree_ref.lstrip("/")
        folder = root / rel
        if not folder.exists():
            return set()
        result: set[str] = set()
        for suffix in PAGE_SUFFIXES:
            for page_file in sorted(folder.rglob(f"*{suffix}")):
                if suffix == LEGACY_SUFFIX and page_file.with_suffix(PAGE_SUFFIX).exists():
                    continue
                if ".stillpoint" in page_file.parts:
                    continue
                result.add("/" + page_file.relative_to(root).as_posix())
        return result

    def _attachments_in_page(self, page_ref: str) -> list[Path]:
        if not self.vault_root:
            return []
        root = Path(self.vault_root)
        candidate_file = root / page_ref.lstrip("/")
        if candidate_file.is_dir():
            folder = candidate_file
        elif candidate_file.exists():
            folder = candidate_file.parent
        else:
            candidate_with_md = candidate_file.with_suffix(PAGE_SUFFIX)
            candidate_with_txt = candidate_file.with_suffix(LEGACY_SUFFIX)
            if candidate_with_md.exists():
                folder = candidate_with_md.parent
            elif candidate_with_txt.exists():
                folder = candidate_with_txt.parent
            else:
                folder = candidate_file.parent
        attachments: list[Path] = []
        for entry in sorted(folder.iterdir()):
            if not entry.is_file():
                continue
            if entry.name.startswith("."):
                continue
            if entry.suffix.lower() in PAGE_SUFFIXES:
                continue
            if ".stillpoint" in entry.parts:
                continue
            attachments.append(entry)
        return attachments

    def _rag_context_pages(self) -> List[str]:
        pages: Set[str] = set()
        if self.current_page_path:
            pages.add(self.current_page_path)
        for item in self._context_items:
            if item.kind == "page" and item.page_ref:
                pages.add(item.page_ref)
            elif item.kind == "page-tree" and item.page_ref:
                pages.update(self._pages_under_tree(item.page_ref))
            elif item.kind == "attachment" and item.page_ref:
                pages.add(item.page_ref)
        return sorted(pages)

    def _rag_context_chunks(self, query: str) -> List[RetrievedChunk]:
        if not self._vector_api.available():
            return []
        pages = self._rag_context_pages()
        if not pages:
            return []
        attachment_labels = [
            f"{item.page_ref}/{item.attachment_name}"
            for item in self._context_items
            if item.kind == "attachment" and item.attachment_name
        ]
        _log_vector(
            f"querying for query={query!r} "
            f"pages={pages} attachments={attachment_labels or 'none'}"
        )
        try:
            limit = 4
            attachment_names = [label.split("/")[-1] for label in attachment_labels]
            deduped: list[RetrievedChunk] = []
            seen: Set[tuple[str, Optional[str]]] = set()
            if attachment_names:
                attachment_chunks = self._vector_api.query_attachments(query, attachment_names, limit=limit)
                for chunk in attachment_chunks:
                    key = (chunk.page_ref, chunk.attachment_name)
                    if key in seen:
                        continue
                    seen.add(key)
                    deduped.append(chunk)
                    if len(deduped) >= limit:
                        break
            if len(deduped) < limit:
                general_chunks = self._vector_api.query(query, page_refs=pages, limit=limit * 2)
                for chunk in general_chunks:
                    key = (chunk.page_ref, chunk.attachment_name)
                    if key in seen:
                        continue
                    seen.add(key)
                    deduped.append(chunk)
                    if len(deduped) >= limit:
                        break
            if deduped:
                _log_vector(f"Retrieved {len(deduped)} context chunks for query.")
                for chunk in deduped:
                    score = f"{chunk.score:.3f}" if chunk.score is not None else "n/a"
                    attachment = chunk.attachment_name or "page"
                    _log_vector(f"chunk {chunk.page_ref} ({attachment}) score={score}")
            return deduped
        except Exception as exc:
            _log_vector(f"Failed to query context: {exc}")
            return []

    def _build_context_prompt(self, query: str) -> Optional[str]:
        chunks = self._rag_context_chunks(query)
        if not chunks:
            return None
        lines: List[str] = ["Vault context relevant to the query:"]
        for chunk in chunks:
            snippet = re.sub(r"\s+", " ", chunk.content.strip())
            if len(snippet) > 2000:
                snippet = snippet[:2000].rstrip() + "…"
            label = chunk.page_ref
            if chunk.attachment_name:
                label += f" ({chunk.attachment_name})"
            lines.append(f"{label}: {snippet}")
        prompt_text = "\n".join(lines)
        _log_vector(f"rag retrieved:\n{prompt_text}")
        return "\n".join(lines)

    def _record_chat_history(self, content: str) -> None:
        if not content:
            return
        if self._chat_history and self._chat_history[-1] == content:
            self._chat_history_index = None
            self._unsent_buffer = ""
            return
        self._chat_history.append(content)
        if len(self._chat_history) > 10:
            self._chat_history.pop(0)
        self._chat_history_index = None
        self._unsent_buffer = ""

    def _navigate_chat_history(self, direction: int) -> bool:
        if not self._chat_history:
            return False
        if direction < 0:
            if self._chat_history_index is None:
                self._unsent_buffer = self.input_edit.toPlainText()
                self._chat_history_index = len(self._chat_history) - 1
            elif self._chat_history_index > 0:
                self._chat_history_index -= 1
        else:
            if self._chat_history_index is None:
                return False
            if self._chat_history_index < len(self._chat_history) - 1:
                self._chat_history_index += 1
            else:
                self._chat_history_index = None
                self.input_edit.setPlainText(self._unsent_buffer)
                self.input_edit.moveCursor(QTextCursor.End)
                return True
        if 0 <= self._chat_history_index < len(self._chat_history):
            self.input_edit.setPlainText(self._chat_history[self._chat_history_index])
            self.input_edit.moveCursor(QTextCursor.End)
            return True
        return False

    def _apply_context_selection(self, trigger: str, candidate: ContextCandidate) -> None:
        self._remove_trigger_sequence(trigger)
        self._add_context_item(trigger, candidate)

    def _remove_trigger_sequence(self, trigger: str) -> None:
        cursor = self.input_edit.textCursor()
        pos = cursor.position()
        seq = f"{trigger} "
        if pos >= len(seq):
            text = self.input_edit.toPlainText()
            if text[pos - len(seq) : pos] == seq:
                cursor.setPosition(pos - len(seq))
                cursor.setPosition(pos, QTextCursor.KeepAnchor)
                cursor.removeSelectedText()
                self.input_edit.setTextCursor(cursor)

    def _add_context_item(self, trigger: str, candidate: ContextCandidate) -> None:
        if not self._ensure_context_conversation():
            return
        conv_id = self._current_ai_conversation_id
        if not conv_id or not self.ai_manager:
            return
        same = self._context_items
        kind = {"@": "page", "#": "page-tree"}.get(trigger, "attachment")
        already = any(
            item for item in same
            if item.kind == kind
            and item.page_ref == candidate.page_ref
            and (kind != "attachment" or item.attachment_name == candidate.attachment_name)
        )
        if already:
            return
        try:
            if trigger == "@":
                self.ai_manager.add_context_page(conv_id, candidate.page_ref)
            elif trigger == "#":
                self.ai_manager.add_context_page_tree(conv_id, candidate.page_ref)
            else:
                name = candidate.attachment_name or ""
                self.ai_manager.add_context_attachment(conv_id, candidate.page_ref, name)
            self._index_context_item(trigger, candidate)
        except Exception:
            pass
        self._refresh_context_items()

    def _ensure_context_conversation(self) -> bool:
        if not self.ai_manager or not self.current_session_id:
            return False
        if not self._current_ai_conversation_id:
            self._bind_ai_conversation()
        return bool(self._current_ai_conversation_id)

    def _handle_context_overlay_selected(self, candidate: ContextCandidate) -> None:
        trigger = self._context_overlay.current_trigger()
        if not trigger:
            return
        self._apply_context_selection(trigger, candidate)

    def _delete_context_item(self, item: ContextItem) -> None:
        if not self.ai_manager:
            return
        try:
            self.ai_manager.delete_context_item(item.id)
            self._delete_context_source(item)
        except Exception:
            pass
        self._refresh_context_items()
        if self._context_popup.isVisible() and self._context_popup_position:
            self._context_popup.show_for(
                self._context_items,
                self._context_popup_position,
                width_hint=self._context_popup_width,
            )
            self._context_popup_width = self._context_popup.width()

    def _context_popup_delete_handler(self, item: ContextItem) -> None:
        self._context_popup.remove_item(item)
        self._delete_context_item(item)

    def _context_popup_refresh_handler(self, item: ContextItem) -> None:
        self._refresh_context_item(item)

    def _refresh_context_item(self, item: ContextItem) -> None:
        if not self._vector_api.available():
            _log_vector(f"Skipping refresh for context {item.page_ref} ({item.kind}) — client unavailable.")
            return
        self._set_status("Refreshing context...", "#f6c343")
        try:
            self._delete_context_source(item)
        except Exception:
            pass
        trigger = {"page": "@", "page-tree": "#"}.get(item.kind, "^")
        candidate = ContextCandidate(
            page_ref=item.page_ref,
            label=item.page_ref,
            attachment_name=item.attachment_name,
        )
        self._index_context_item(trigger, candidate)

    def _refresh_current_page_context(self) -> None:
        if not self.current_page_path:
            return
        target = next(
            (
                item
                for item in self._context_items
                if item.kind == "page" and item.page_ref == self.current_page_path
            ),
            None,
        )
        if target:
            self._refresh_context_item(target)
            return
        self._ensure_page_context_added()

    def _index_context_item(self, trigger: str, candidate: ContextCandidate) -> None:
        if not self._vector_api.available():
            _log_vector(f"Skipping indexing for context {candidate.page_ref} ({trigger}) — client unavailable.")
            return
        kind = {"@": "page", "#": "page-tree"}.get(trigger, "attachment")
        _log_vector(f"Indexing {kind} context for {candidate.page_ref}")
        self._set_status(f"Indexing context: {kind}", "#f6c343")
        if trigger == "@":
            text = self._read_page_text(candidate.page_ref)
            self._vector_api.index_text(candidate.page_ref, text, kind="page")
        elif trigger == "#":
            for page in self._pages_under_tree(candidate.page_ref):
                text = self._read_page_text(page)
                self._vector_api.index_text(page, text, kind="page")
        else:
            if not candidate.attachment_name:
                return
            text = self._extract_attachment_text(candidate.page_ref, candidate.attachment_name)
            if not text.strip():
                return
            attachment_path = self._attachment_path(candidate.page_ref, candidate.attachment_name)
            _log_vector(
                f"Indexing attachment {candidate.attachment_name or '<unnamed>'} "
                f"for {candidate.page_ref} "
                f"({attachment_path or 'path unavailable'})"
            )
            self._vector_api.index_text(candidate.page_ref, text, kind="attachment", attachment=candidate.attachment_name)
        self._set_status("Context indexed.", "#2ecc71")

    def _delete_context_source(self, item: ContextItem) -> None:
        if not self._vector_api.available():
            _log_vector(f"Skipping delete for context {item.page_ref} ({item.kind}) — client unavailable.")
            return
        if item.kind == "page" and item.page_ref:
            _log_vector(f"Removing page context {item.page_ref}")
            self._vector_api.delete_text(item.page_ref, kind="page")
        elif item.kind == "page-tree" and item.page_ref:
            pages = list(self._pages_under_tree(item.page_ref))
            _log_vector(f"Removing tree context {item.page_ref} ({len(pages)} pages)")
            for page in pages:
                self._vector_api.delete_text(page, kind="page")
        elif item.kind == "attachment" and item.page_ref and item.attachment_name:
            _log_vector(f"Removing attachment context {item.page_ref} ({item.attachment_name})")
            self._vector_api.delete_text(item.page_ref, kind="attachment", attachment=item.attachment_name)

    def _read_page_text(self, page_ref: str) -> str:
        if not self.vault_root:
            return ""
        page_path = Path(self.vault_root) / page_ref.lstrip("/")
        if not page_path.exists():
            candidate_md = page_path.with_suffix(PAGE_SUFFIX)
            candidate_txt = page_path.with_suffix(LEGACY_SUFFIX)
            if candidate_md.exists():
                page_path = candidate_md
            elif candidate_txt.exists():
                page_path = candidate_txt
            else:
                return ""
        try:
            return page_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return ""

    def _extract_attachment_text(self, page_ref: str, attachment_name: str) -> str:
        path = self._attachment_path(page_ref, attachment_name)
        if not path or not path.exists():
            return ""
        _log_vector(f"Extracting text from attachment {path}")
        return extract_attachment_text(path)

    def _attachment_path(self, page_ref: str, attachment_name: str) -> Optional[Path]:
        if not self.vault_root:
            return None
        base = Path(self.vault_root) / page_ref.lstrip("/")
        if base.is_dir():
            folder = base
        else:
            folder = base.parent
        candidate = folder / attachment_name
        return candidate if candidate.exists() else None

    def _reload_context_index(self) -> None:
        if not self.vault_root and not self._api_client_available():
            self._page_candidates = []
            self._tree_candidates = []
            self._attachment_candidates = []
            self._context_reload_in_progress = False
            self._context_reload_pending = False
            return
        _log_vector(
            f"Reloading context index (vault_root={'set' if self.vault_root else 'none'}, "
            f"api_client={'ready' if self._api_client_available() else 'missing'})"
        )
        if self._context_reload_in_progress:
            self._context_reload_pending = True
            return
        self._context_reload_in_progress = True
        root = Path(self.vault_root) if self.vault_root else None

        def _scan_local_candidates() -> tuple[list[ContextCandidate], list[ContextCandidate], list[ContextCandidate]]:
            if not root:
                return [], [], []
            pages: list[ContextCandidate] = []
            trees: list[ContextCandidate] = []
            attachments: list[ContextCandidate] = []
            seen: set[str] = set()
            for suffix in PAGE_SUFFIXES:
                for page_file in sorted(root.rglob(f"*{suffix}")):
                    if suffix == LEGACY_SUFFIX and page_file.with_suffix(PAGE_SUFFIX).exists():
                        continue
                    if ".stillpoint" in page_file.parts:
                        continue
                    rel_page = "/" + page_file.relative_to(root).as_posix()
                    pages.append(ContextCandidate(page_ref=rel_page, label=rel_page))
                    folder = page_file.parent
                    if folder == root:
                        folder_rel = "/"
                    else:
                        folder_rel = "/" + folder.relative_to(root).as_posix()
                    if folder_rel not in seen:
                        seen.add(folder_rel)
                        trees.append(ContextCandidate(page_ref=folder_rel, label=folder_rel))
                    for attachment in sorted(folder.iterdir()):
                        if not attachment.is_file():
                            continue
                        if attachment.suffix.lower() in PAGE_SUFFIXES:
                            continue
                        if attachment.name.startswith("."):
                            continue
                        if ".stillpoint" in attachment.parts:
                            continue
                        display = f"{rel_page} · {attachment.name}"
                        attachments.append(
                            ContextCandidate(page_ref=rel_page, label=display, attachment_name=attachment.name)
                        )
            return pages, trees, attachments

        def _scan_api_candidates() -> tuple[list[ContextCandidate], list[ContextCandidate]]:
            return self._fetch_context_candidates_from_api()

        def scan() -> tuple[list[ContextCandidate], list[ContextCandidate], list[ContextCandidate]]:
            pages: list[ContextCandidate] = []
            trees: list[ContextCandidate] = []
            attachments: list[ContextCandidate] = []
            if self._api_client_available():
                pages, trees = _scan_api_candidates()
                if root:
                    _, _, attachments = _scan_local_candidates()
            elif root:
                pages, trees, attachments = _scan_local_candidates()
            return pages, trees, attachments

        def finish(result: tuple[list[ContextCandidate], list[ContextCandidate], list[ContextCandidate]]) -> None:
            pages, trees, attachments = result
            self._page_candidates = pages
            self._tree_candidates = trees
            self._attachment_candidates = attachments
            self._context_reload_in_progress = False
            if self._context_reload_pending:
                self._context_reload_pending = False
                self._reload_context_index()

        def worker() -> None:
            try:
                result = scan()
            except Exception as exc:
                _log_vector(f"Failed to build context index: {exc}")
                result = ([], [], [])
            QtCore.QTimer.singleShot(0, lambda r=result: finish(r))

        threading.Thread(target=worker, daemon=True).start()

    def _is_plain_markdown(self, rendered_html: str) -> bool:
        """Heuristic: detect if the markdown output is just a <p> block without rich tags."""
        if not rendered_html.startswith("<p>") or not rendered_html.endswith("</p>"):
            return False
        heavy_tags = ("<ul", "<ol", "<pre", "<code", "<h", "<table", "<blockquote", "<li", "<hr", "<img", "<a ")
        return not any(tag in rendered_html for tag in heavy_tags)

    def _on_history_context_menu(self, pos: QtCore.QPoint) -> None:
        cursor = self.chat_view.textCursor()
        if cursor.hasSelection():
            menu = QtWidgets.QMenu(self)
            copy_sel = menu.addAction("Copy")
            def _copy_selected():
                try:
                    txt = cursor.selection().toPlainText()
                    QtWidgets.QApplication.clipboard().setText(self._sanitize_for_clipboard(txt))
                except Exception:
                    QtWidgets.QApplication.clipboard().setText("")
            copy_sel.triggered.connect(_copy_selected)
            copy_md_sel = menu.addAction("Copy As Markdown")
            def _copy_selected_md():
                try:
                    txt = cursor.selection().toPlainText()
                    QtWidgets.QApplication.clipboard().setText(self._sanitize_for_clipboard(txt))
                except Exception:
                    QtWidgets.QApplication.clipboard().setText("")
            copy_md_sel.triggered.connect(_copy_selected_md)
            menu.exec(self.chat_view.mapToGlobal(pos))
            return
        anchor = self.chat_view.anchorAt(pos)
        msg_id = None
        if anchor:
            if anchor.startswith("msg:"):
                msg_id = anchor.split(":", 1)[1]
            elif anchor.startswith("action:") and ":" in anchor:
                msg_id = anchor.split(":")[-1]
        if not msg_id and self._message_map:
            msg_id = list(self._message_map.keys())[-1]
        self._show_history_context_menu_for(msg_id, pos)

    def _show_history_context_menu_for(self, msg_id: Optional[str], pos: QtCore.QPoint) -> None:
        if not msg_id or msg_id not in self._message_map:
            return
        _, content = self._message_map[msg_id]
        menu = QtWidgets.QMenu(self)
        copy_act = menu.addAction("Copy Message")
        def _copy_message():
            try:
                QtWidgets.QApplication.clipboard().setText(self._sanitize_for_clipboard(content))
            except Exception:
                QtWidgets.QApplication.clipboard().setText("")
        copy_act.triggered.connect(_copy_message)
        copy_md = menu.addAction("Copy As Markdown")
        def _copy_message_md():
            try:
                # content is stored as the original markdown/plain text for the message
                QtWidgets.QApplication.clipboard().setText(self._sanitize_for_clipboard(content))
            except Exception:
                QtWidgets.QApplication.clipboard().setText("")
        copy_md.triggered.connect(_copy_message_md)
        goto_act = menu.addAction("Go To Start")
        goto_act.triggered.connect(lambda: self.chat_view.scrollToAnchor(msg_id))
        del_act = menu.addAction("Delete Message")
        del_act.triggered.connect(lambda: self._delete_message(msg_id))
        menu.addSeparator()
        menu.exec(self.chat_view.mapToGlobal(pos))

    def _ai_overlay_payload_text(self) -> str:
        """Return selected text in chat view or full chat content."""
        try:
            cursor = self.chat_view.textCursor()
            if cursor.hasSelection():
                return cursor.selection().toPlainText().replace("\u2029", "\n")
            return self.chat_view.toPlainText().replace("\u2029", "\n")
        except Exception:
            return ""

    def _sanitize_for_clipboard(self, text: str) -> str:
        """Normalize text for copying to the system clipboard.

        Keep markdown characters but remove non-printable control characters and
        normalize paragraph separators.
        """
        if text is None:
            return ""
        text = text.replace("\u2029", "\n")
        try:
            # Remove low-control characters but keep common whitespace (\n,\r,\t)
            text = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]+", "", text)
            # Also remove BOM, object-replacement, zero-width and bidi control characters,
            # and private-use-area glyphs that can appear as weird symbols when pasted.
            text = re.sub(
                r"[\u200B-\u200F\u2028\u202A-\u202E\uFEFF\uFFFC\uE000-\uF8FF]",
                "",
                text,
            )
        except Exception:
            pass
        return text.strip()

    def _trigger_ai_overlay_from_chat(self) -> None:
        text = (self._ai_overlay_payload_text() or "").strip()
        if not text:
            return
        try:
            anchor = self.chat_view.mapToGlobal(self.chat_view.rect().center())
        except Exception:
            anchor = None
        self.aiOverlayRequested.emit(text, anchor)

    def _select_default_chat(self) -> None:
        chat = self.store.get_session_by_path("/", "chat")
        if not chat:
            chat = self.store._create_root_chat()  # type: ignore[attr-defined]
            if chat:
                default_server = self._config_default_server()
                if default_server:
                    self.store.update_session_last_server(chat["id"], default_server)
                default_model = self._config_default_model()
                if default_model:
                    self.store.update_session_last_model(chat["id"], default_model)
        if chat:
            self.current_session_id = chat["id"]
            self._load_chat_tree(select_id=chat["id"])
            self._load_chat_messages(chat["id"])

    def _load_chat_tree(self, select_id: Optional[int] = None) -> None:
        self._building_tree = True
        self.chat_tree.clear()
        sessions = self.store.get_sessions()
        items: Dict[int, QtWidgets.QTreeWidgetItem] = {}
        for sess in sessions:
            item = QtWidgets.QTreeWidgetItem([sess["name"]])
            item.setData(0, QtCore.Qt.UserRole, sess)
            if sess["type"] == "chat":
                font = item.font(0)
                font.setBold(True)
                item.setFont(0, font)
            items[sess["id"]] = item
        for sess in sessions:
            item = items[sess["id"]]
            parent_id = sess.get("parent_id")
            if parent_id and parent_id in items:
                items[parent_id].addChild(item)
            else:
                self.chat_tree.addTopLevelItem(item)
        self.chat_tree.expandAll()
        self._building_tree = False
        if select_id:
            self._select_chat_by_id(select_id)
        elif self.current_session_id:
            self._select_chat_by_id(self.current_session_id)

    def _select_chat_by_id(self, session_id: int) -> None:
        def walk(item: QtWidgets.QTreeWidgetItem) -> bool:
            data = item.data(0, QtCore.Qt.UserRole) or {}
            if data.get("id") == session_id:
                self.chat_tree.setCurrentItem(item)
                return True
            for i in range(item.childCount()):
                if walk(item.child(i)):
                    return True
            return False

        for i in range(self.chat_tree.topLevelItemCount()):
            if walk(self.chat_tree.topLevelItem(i)):
                return

    def _on_chat_selected(self) -> None:
        if self._building_tree:
            return
        item = self.chat_tree.currentItem()
        if not item:
            return
        data = item.data(0, QtCore.Qt.UserRole) or {}
        if data.get("type") != "chat":
            return
        self._load_chat_messages(data["id"])

    def _load_chat_messages(self, session_id: int) -> None:
        self.current_session_id = session_id
        self.messages = self.store.get_messages(session_id)
        self._message_think.clear()
        self._message_think_active.clear()
        self._message_think_expanded.clear()
        self._render_messages()
        session = self.store.get_session_by_id(session_id)
        if session:
            self._apply_session_defaults(session)
            self.current_system_prompt = session.get("system_prompt")
        else:
            self._update_model_status()
        self._bind_ai_conversation()

    def _apply_session_defaults(self, session: Dict) -> None:
        """Apply stored server/model defaults to UI for a chat session."""
        # Server
        last_server = session.get("last_server")
        if not last_server:
            last_server = self._config_default_server()
        if last_server:
            names = self.server_manager.list_server_names()
            if last_server in names:
                self.server_combo.blockSignals(True)
                self.server_combo.setCurrentText(last_server)
                self.server_combo.blockSignals(False)
                self.current_server = self.server_manager.get_server(last_server)
                if session.get("last_server") != last_server and self.current_session_id:
                    self.store.update_session_last_server(self.current_session_id, last_server)
        # Model
        server = self.current_server or self.server_manager.get_server(self.server_combo.currentText())
        # Only update model dropdown from cache, do not fetch
        models = get_available_models(server)
        if not models:
            models = [server.get("default_model") or "gpt-3.5-turbo"]
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        self.model_combo.addItems(models)
        desired_model = session.get("last_model")
        if not desired_model:
            cfg_model = self._config_default_model()
            if cfg_model in models:
                desired_model = cfg_model
        if desired_model in models:
            self.model_combo.setCurrentText(desired_model)
        elif server.get("default_model") in models:
            self.model_combo.setCurrentText(server.get("default_model"))
        else:
            self.model_combo.setCurrentIndex(0)
        self.model_combo.blockSignals(False)
        if self.current_session_id:
            chosen = self.model_combo.currentText()
            if session.get("last_model") != chosen:
                self.store.update_session_last_model(self.current_session_id, chosen)
        # System prompt
        self.current_system_prompt = session.get("system_prompt")
        self._update_model_status()

    def _refresh_server_dropdown(self, select_name: Optional[str] = None) -> None:
        names = self.server_manager.list_server_names()
        self.server_combo.blockSignals(True)
        self.server_combo.clear()
        self.server_combo.addItems(names)
        desired = select_name
        if not desired and self.current_session_id:
            session = self.store.get_session_by_id(self.current_session_id)
            if session and session.get("last_server") in names:
                desired = session.get("last_server")
        if not desired:
            desired = self.server_manager.get_active_server_name()
        if not desired:
            cfg_default = self._config_default_server()
            if cfg_default in names:
                desired = cfg_default
        if desired and desired in names:
            self.server_combo.setCurrentText(desired)
        elif names:
            self.server_combo.setCurrentIndex(0)
        self.server_combo.blockSignals(False)
        current_name = self.server_combo.currentText()
        self.current_server = self.server_manager.get_server(current_name)
        if current_name:
            self.server_manager.set_active_server(current_name)
            if self.current_session_id:
                session = self.store.get_session_by_id(self.current_session_id) or {}
                if session.get("last_server") != current_name:
                    self.store.update_session_last_server(self.current_session_id, current_name)

    def _refresh_model_dropdown(self, initial: bool) -> None:
        server = self.current_server or {}
        models = get_available_models(server)
        if not models:
            models = [server.get("default_model") or "gpt-3.5-turbo"]
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        self.model_combo.addItems(models)
        target = None
        if self.current_session_id:
            session = self.store.get_session_by_id(self.current_session_id)
            if session and session.get("last_model") in models:
                target = session.get("last_model")
        if not target:
            cfg_model = self._config_default_model()
            if cfg_model in models:
                target = cfg_model
        if not target:
            target = server.get("default_model") or models[0]
        if target in models:
            self.model_combo.setCurrentText(target)
        else:
            self.model_combo.setCurrentIndex(0)
        self.model_combo.blockSignals(False)
        if not initial:
            self.status_label.setText("Models refreshed.")
        self._update_model_status()

    def _refresh_models_from_server(self) -> None:
        if not self.current_server:
            QtWidgets.QMessageBox.warning(self, "No Server", "Please select a server to refresh models.")
            return
        models = fetch_and_cache_models(self.current_server)
        self._refresh_model_dropdown(initial=False)
        self.status_label.setText(f"Models refreshed from server. {len(models)} models cached.")

    def _on_server_selected(self) -> None:
        selected_name = self.server_combo.currentText()
        server = self.server_manager.get_server(selected_name)
        if not server:
            QtWidgets.QMessageBox.critical(self, "Server", "Selected server is unavailable.")
            return
        self.current_server = server
        self.server_manager.set_active_server(selected_name)
        if self.current_session_id:
            self.store.update_session_last_server(self.current_session_id, selected_name)
        self._refresh_model_dropdown(initial=False)
        self.status_label.setText(f"Switched to server: {selected_name}")
        self._update_model_status()

    def _on_model_selected(self) -> None:
        """Persist chosen model for the current chat."""
        if self.current_session_id:
            self.store.update_session_last_model(self.current_session_id, self.model_combo.currentText())
        self._update_model_status()

    def _manage_servers(self) -> None:
        dialog = ServerConfigDialog(self, self.current_server, existing_names=self.server_manager.list_server_names())
        if dialog.exec() == QtWidgets.QDialog.Accepted and dialog.result:
            try:
                new_server = self.server_manager.add_or_update_server(dialog.result)
                self._refresh_server_dropdown(select_name=new_server["name"])
                self.current_server = new_server
                self._refresh_model_dropdown(initial=True)
                dialog.accept()  # Ensure dialog closes
            except ValueError as exc:
                QtWidgets.QMessageBox.critical(self, "Server Exists", str(exc))
                dialog.reject()
                return

    def _add_server(self) -> None:
        dialog = ServerConfigDialog(self, None, existing_names=self.server_manager.list_server_names())
        if dialog.exec() == QtWidgets.QDialog.Accepted and dialog.result:
            try:
                new_server = self.server_manager.add_or_update_server(dialog.result)
                # Fetch and cache models for the new server
                fetch_and_cache_models(new_server)
                self._refresh_server_dropdown(select_name=new_server["name"])
                self.current_server = new_server
                self._refresh_model_dropdown(initial=True)
                self.status_label.setText(f"Added server: {new_server['name']} (models cached)")
                dialog.accept()  # Ensure dialog closes
            except ValueError as exc:
                QtWidgets.QMessageBox.critical(self, "Server Exists", str(exc))
                dialog.reject()
                return

    def _ensure_active_chat(self) -> bool:
        if self.current_session_id:
            return True
        chat = self.store.get_or_create_chat_for_page(self.current_page_path)
        if chat:
            self.current_session_id = chat["id"]
            self._load_chat_tree(select_id=chat["id"])
            return True
        return False

    def _cancel_active_operation(self) -> None:
        cancelled = False
        if self._api_worker:
            try:
                self._api_worker.request_cancel()
            except Exception:
                pass
            cancelled = True
            self._cancel_pending_send = True
            if self.messages and self.messages[-1][0] == "assistant":
                role, content = self.messages[-1]
                note = "[cancelled]"
                self.messages[-1] = (role, content + ("\n\n" if content else "") + note if content else note)
            self.send_btn.setEnabled(True)
        if self._condense_worker:
            try:
                self._condense_worker.request_cancel()
            except Exception:
                pass
            cancelled = True
            self._cancel_pending_condense = True
            if not self._summary_content:
                self._summary_content = "[condense cancelled]"
            self._condense_buffer = ""
            self._condense_think_state = {"in_think": False, "pending": "", "visible": ""}
            self.condense_btn.setEnabled(True)
        if cancelled:
            self._render_messages()
            self._set_status("Cancelled.", "#2ecc71")
        self._update_stop_button()

    def _send_message(self) -> None:
        content = self.input_edit.toPlainText().strip()
        self.input_edit.clear()
        self._start_send(content)

    def send_text_message(self, text: str) -> None:
        """Send arbitrary text as the next user message in the active chat."""
        content = (text or "").strip()
        if not content:
            return
        if not self.current_session_id and not self._ensure_active_chat():
            return
        self._start_send(content)

    def focus_input(self) -> None:
        if hasattr(self, "input_edit"):
            self.input_edit.setFocus(QtCore.Qt.FocusReason.ShortcutFocusReason)

    def send_action_message(self, action: str, prompt: str, text: str) -> None:
        """Send a structured action message into the chat."""
        content = f"[{action}] {prompt}\n\n{text}"
        extra_system = f"AI Action: {action}\nInstruction: {prompt}"
        self._start_send(content, extra_system=extra_system)

    def _condense_chat(self) -> None:
        """Send the full chat history through the condense prompt."""
        if self._condense_worker:
            return
        if not self.current_server:
            QtWidgets.QMessageBox.critical(self, "Server", "Please configure a server before condensing.")
            return
        if not self._ensure_active_chat():
            QtWidgets.QMessageBox.critical(self, "Chat", "Could not find or create a chat.")
            return
        if not self.messages:
            QtWidgets.QMessageBox.information(self, "Condense", "No messages to condense yet.")
            return
        # Refresh prompt at use-time so edits take effect
        self.condense_prompt = self._load_condense_prompt()
        history_source = self.messages
        if self.current_session_id:
            try:
                history_source = self.store.get_messages(self.current_session_id)
            except Exception:
                history_source = self.messages
        history_text = "\n\n".join(f"{role.upper()}: {content}" for role, content in history_source)
        blocks = [{"role": "system", "content": self.condense_prompt}, {"role": "user", "content": history_text}]
        self._condense_buffer = ""
        self._summary_content = None
        self._condense_think_state = {"in_think": False, "pending": "", "visible": ""}
        self._render_messages()
        try:
            self._condense_worker = ApiWorker(self.current_server, blocks, self.model_combo.currentText(), stream=True)
            self._condense_worker.chunk.connect(self._handle_condense_chunk)
            self._condense_worker.finished.connect(self._handle_condense_finished)
            self._condense_worker.failed.connect(self._handle_condense_error)
            self._condense_worker.start()
            self._set_status("Condensing chat…", "#f6c343")
            self.condense_btn.setEnabled(False)
            self._update_stop_button()
        except Exception as exc:
            self._condense_worker = None
            QtWidgets.QMessageBox.critical(self, "Condense", str(exc))

    def _start_send(self, content: str, extra_system: Optional[str] = None) -> None:
        content = (content or "").strip()
        if not content:
            return
        self._record_chat_history(content)
        _log_ai_chat(f"[AIChat]: prompt: {content}")
        if not self.current_server:
            QtWidgets.QMessageBox.critical(self, "Server", "Please configure a server before sending.")
            return
        if not self._ensure_active_chat():
            QtWidgets.QMessageBox.critical(self, "Chat", "Could not find or create a chat.")
            return
        self.messages.append(("user", content))
        if self.current_session_id:
            self.store.save_message(self.current_session_id, "user", content)
        self.messages.append(("assistant", ""))
        assistant_index = len(self.messages) - 1
        self._stream_think_state[assistant_index] = {
            "in_think": False,
            "pending": "",
            "visible": "",
            "think": "",
        }
        self._render_messages()
        try:
            blocks = [{"role": role, "content": text} for role, text in self.messages[:-1]]
            merged_systems: List[str] = []
            context_prompt = self._build_context_prompt(content)
            if context_prompt:
                merged_systems.append(context_prompt)
            if self.current_system_prompt:
                merged_systems.append(self.current_system_prompt)
            if extra_system:
                merged_systems.append(extra_system)
            if merged_systems:
                blocks.insert(0, {"role": "system", "content": "\n\n".join(merged_systems)})
                _log_ai_chat(f"[system prompt] sending {len(merged_systems)} system block(s); primary:\n{merged_systems[-1]}")
            self._api_worker = ApiWorker(self.current_server, blocks, self.model_combo.currentText(), stream=True)
            self._api_worker.chunk.connect(lambda chunk, idx=assistant_index: self._handle_chunk(idx, chunk))
            self._api_worker.finished.connect(lambda full, idx=assistant_index: self._handle_finished(idx, full))
            self._api_worker.failed.connect(self._handle_error)
            self._api_worker.start()
            self.send_btn.setEnabled(False)
            self._set_status("Waiting for response…", "#f6c343")
            self._update_stop_button()
        except Exception as exc:
            self.messages.pop()  # remove assistant placeholder
            QtWidgets.QMessageBox.critical(self, "Send failed", str(exc))
            self._render_messages()

    def _handle_chunk(self, idx: int, chunk: str) -> None:
        if self._cancel_pending_send:
            return
        self._pending_stream_chunks.setdefault(idx, []).append(chunk)
        if not self._stream_flush_timer.isActive():
            self._stream_flush_timer.start()

    def _handle_finished(self, idx: int, full: str) -> None:
        if self._cancel_pending_send:
            self._cancel_pending_send = False
            self._stream_think_state.pop(idx, None)
            self._message_think_active.discard(idx)
            self._api_worker = None
            self._update_stop_button()
            return
        pending = self._pending_stream_chunks.pop(idx, [])
        if pending:
            self._apply_stream_think_chunk(idx, "".join(pending))
        state = self._stream_think_state.pop(idx, None)
        fallback = ""
        if state:
            fallback = str(state.get("visible", ""))
        clean_full = self._strip_think_blocks(full or fallback)
        think_text = ""
        if state:
            think_text = str(state.get("think", ""))
        if not think_text and full:
            think_text = self._extract_think_blocks(full)
        if think_text:
            self._message_think[idx] = think_text
        else:
            self._message_think.pop(idx, None)
        self._message_think_active.discard(idx)
        if 0 <= idx < len(self.messages):
            role, _ = self.messages[idx]
            self.messages[idx] = (role, clean_full)
            if self.current_session_id:
                self.store.save_message(self.current_session_id, "assistant", clean_full)
                self.store.update_session_last_model(self.current_session_id, self.model_combo.currentText())
                self.store.update_session_last_server(self.current_session_id, self.current_server.get("name", ""))
        self._render_messages()
        self._set_status("Response received.", "#2ecc71")
        try:
            if clean_full:
                QtWidgets.QApplication.clipboard().setText(self._sanitize_for_clipboard(clean_full))
                self.responseCopied.emit("Last chat response copied to buffer.")
        except Exception:
            pass
        self._update_model_status()
        self.send_btn.setEnabled(True)
        self._api_worker = None
        self._update_stop_button()
        _log_llm_response(f"[AIChat][stream complete] response_len={len(full)}")

    def _handle_condense_chunk(self, chunk: str) -> None:
        if self._cancel_pending_condense:
            return
        self._apply_condense_think_chunk(chunk)
        if not self._condense_flush_timer.isActive():
            self._condense_flush_timer.start()

    def _handle_condense_finished(self, full: str) -> None:
        if self._cancel_pending_condense:
            self._cancel_pending_condense = False
            self._condense_worker = None
            self._condense_think_state = {"in_think": False, "pending": "", "visible": ""}
            self._update_stop_button()
            return
        fallback = str(self._condense_think_state.get("visible", ""))
        clean_full = self._strip_think_blocks(full or fallback)
        self._summary_content = clean_full or self._strip_think_blocks(self._condense_buffer)
        self._condense_buffer = ""
        self._condense_worker = None
        self._condense_think_state = {"in_think": False, "pending": "", "visible": ""}
        self.condense_btn.setEnabled(True)
        self._render_messages()
        self._set_status("Condensed chat ready.", "#2ecc71")
        self._update_stop_button()

    def _handle_condense_error(self, err: str) -> None:
        self._condense_buffer = ""
        if err != "Cancelled":
            self._summary_content = None
        self._condense_worker = None
        self._cancel_pending_condense = False
        self._condense_think_state = {"in_think": False, "pending": "", "visible": ""}
        self.condense_btn.setEnabled(True)
        self._render_messages()
        if err == "Cancelled":
            self._set_status("Condense cancelled.", "#2ecc71")
        else:
            self._set_status(f"Condense failed: {err}")
        self._update_stop_button()

    def _flush_pending_stream_chunks(self) -> None:
        if self._cancel_pending_send:
            self._pending_stream_chunks.clear()
            return
        updated = False
        for idx, chunks in list(self._pending_stream_chunks.items()):
            if not chunks:
                continue
            if 0 <= idx < len(self.messages):
                role, existing = self.messages[idx]
                chunk_text = "".join(chunks)
                if role == "assistant":
                    self._apply_stream_think_chunk(idx, chunk_text)
                    updated = True
                else:
                    self.messages[idx] = (role, existing + chunk_text)
                    updated = True
            self._pending_stream_chunks[idx] = []
        if updated:
            self._render_messages()

    def _flush_condense_buffer(self) -> None:
        if self._cancel_pending_condense:
            self._condense_buffer = ""
            return
        if self._condense_buffer:
            self._render_messages()

    def _handle_error(self, err: str) -> None:
        self._message_think_active.clear()
        if self.messages and self.messages[-1][0] == "assistant" and not self.messages[-1][1]:
            self.messages[-1] = ("assistant", f"[error] {err}")
        self._render_messages()
        if err == "Cancelled":
            self._set_status("Cancelled.", "#2ecc71")
        else:
            self._set_status(f"API error: {err}")
        self.send_btn.setEnabled(True)
        self._api_worker = None
        self._cancel_pending_send = False
        self._update_model_status()
        self._update_stop_button()

    def _show_reset_warning(self) -> None:
        """Insert a warning message before resetting chat history."""
        warning = (
            "Warning! Resetting chat history will wipe this chat. "
            "<a href='action:reset_chat:accept'>Accept</a>"
        )
        self.messages.append(("assistant", warning))
        self._render_messages()
        self.chat_view.ensureCursorVisible()

    def _delete_message(self, msg_id: str) -> None:
        role, content = self._message_map.get(msg_id, ("", ""))
        if not content or self.current_session_id is None:
            return
        try:
            self.store.delete_message(self.current_session_id, role, content)
        except Exception:
            pass
        try:
            idx = int(msg_id.split("-")[-1])
            if 0 <= idx < len(self.messages):
                self.messages.pop(idx)
                self._shift_think_indices(idx)
        except Exception:
            self.messages = [m for m in self.messages if not (m[0] == role and m[1] == content)]
        self._render_messages()

    def _shift_think_indices(self, removed_idx: int) -> None:
        if self._message_think:
            self._message_think = {
                (idx - 1 if idx > removed_idx else idx): text
                for idx, text in self._message_think.items()
                if idx != removed_idx
            }
        if self._message_think_active:
            self._message_think_active = {
                idx - 1 if idx > removed_idx else idx
                for idx in self._message_think_active
                if idx != removed_idx
            }
        if self._message_think_expanded:
            self._message_think_expanded = {
                idx - 1 if idx > removed_idx else idx
                for idx in self._message_think_expanded
                if idx != removed_idx
            }
        if self._stream_think_state:
            self._stream_think_state = {
                (idx - 1 if idx > removed_idx else idx): state
                for idx, state in self._stream_think_state.items()
                if idx != removed_idx
            }

    def _current_folder_path(self) -> str:
        if self.current_page_path:
            path_obj = Path(self.current_page_path.lstrip("/"))
            return "/" + path_obj.parent.as_posix()
        session = self.store.get_session_by_id(self.current_session_id or -1) if self.current_session_id else None
        if session:
            return session.get("path", "/")
        return "/"

    def _go_to_page_for_chat(self, data: Dict) -> None:
        """Emit signal to focus the editor on the page associated with this chat."""
        path = data.get("path") or self._current_chat_path
        if path:
            self.chatNavigateRequested.emit(path)

    def _delete_chat_session(self, data: Dict) -> None:
        session_id = data.get("id")
        if not session_id:
            return
        session = self.store.get_session_by_id(session_id) or data
        conv_id = session.get("ai_conversation_id")
        if self.ai_manager and conv_id:
            try:
                items = self.ai_manager.list_context_items(conv_id)
            except Exception:
                items = []
            for item in items:
                try:
                    self._delete_context_source(item)
                except Exception:
                    pass
            try:
                self.ai_manager.delete_conversation(conv_id)
            except Exception:
                pass
        try:
            self.store.delete_session(session_id)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Delete Chat", str(exc))
            return
        was_current = session_id == self.current_session_id
        if was_current:
            self.current_session_id = None
            self.messages = []
            self.chat_view.clear()
            self._context_items = []
            self._current_ai_conversation_id = None
            self._update_context_summary()
            self._condense_buffer = ""
            self._summary_content = None
            self._message_think.clear()
            self._message_think_active.clear()
            self._message_think_expanded.clear()
            self._condense_think_state = {"in_think": False, "pending": "", "visible": ""}
        if was_current:
            sessions = self.store.get_sessions()
            next_chat = next((s for s in sessions if s.get("type") == "chat"), None)
            if next_chat:
                self.current_session_id = next_chat["id"]
                self._load_chat_tree(select_id=next_chat["id"])
            else:
                self._load_chat_tree()
                self._select_default_chat()
        else:
            self._load_chat_tree()
        self._set_status("Chat deleted.", "#2ecc71")

    def _on_anchor_clicked(self, url: QUrl) -> None:
        href = url.toString()
        if href.startswith("action:"):
            parts = href.split(":")
            if len(parts) >= 3:
                action, msg_id = parts[1], parts[2]
                if action == "summary":
                    if msg_id == "accept":
                        self._accept_summary()
                    elif msg_id == "reject":
                        self._reject_summary()
                    return
                if action == "think":
                    try:
                        idx = int(msg_id.split("-")[-1])
                    except Exception:
                        return
                    if idx in self._message_think_expanded:
                        self._message_think_expanded.discard(idx)
                    else:
                        self._message_think_expanded.add(idx)
                    self._render_messages()
                    return
                if action == "copy":
                    content = self._message_map.get(msg_id, ("", ""))[1]
                    try:
                        QtWidgets.QApplication.clipboard().setText(self._sanitize_for_clipboard(content))
                    except Exception:
                        QtWidgets.QApplication.clipboard().setText("")
                elif action == "goto":
                    self.chat_view.scrollToAnchor(msg_id)
                elif action == "delete":
                    self._delete_message(msg_id)
                elif action == "reset_chat":
                    self._reset_chat_history()
            return
        if href.startswith("msg:"):
            msg_id = href.split(":", 1)[1]
            self._show_history_context_menu_for(msg_id, self.mapFromGlobal(QCursor.pos()))
            return
        if href.startswith("http://") or href.startswith("https://"):
            QDesktopServices.openUrl(QUrl(href))
            return
        # Internal vault links (absolute /path or colon :Vault:Page) — forward to app navigation
        if href.startswith("/") or href.startswith(":"):
            try:
                self.chatNavigateRequested.emit(href)
            except Exception:
                pass
            return

    def _accept_summary(self) -> None:
        """Replace history with condensed summary."""
        if not self.current_session_id or not self._summary_content:
            return
        summary_text = self._summary_content
        self.store.clear_chat(self.current_session_id)
        self.store.save_message(self.current_session_id, "assistant", summary_text)
        self.messages = [("assistant", summary_text)]
        self._condense_buffer = ""
        self._summary_content = None
        self._render_messages()
        self._set_status("Chat condensed.", "#2ecc71")

    def _reject_summary(self) -> None:
        self._condense_buffer = ""
        self._summary_content = None
        self._render_messages()

    def _update_model_status(self) -> None:
        current_model = self.model_combo.currentText()
        last_model = ""
        last_server = (self.current_server or {}).get("name", "")
        if self.current_session_id:
            session = self.store.get_session_by_id(self.current_session_id)
            if session:
                last_model = session.get("last_model", "") or current_model
                last_server = session.get("last_server", "") or last_server
        status_text = (
            f"current model: {current_model} | last model used: {last_model} | last server: {last_server}"
        )
        self.model_status_label.setText(status_text)
        self.model_status_label.setToolTip(status_text)
        # Track the page path associated with the current chat
        self._current_chat_path = None
        if self.current_session_id:
            session = self.store.get_session_by_id(self.current_session_id)
            if session:
                path_val = session.get("path")
                self._current_chat_path = None if self._is_global_chat_path(path_val) else path_val
        self._update_load_current_page_button()

    def set_font_size(self, size: int) -> None:
        """Apply font size to chat view and input."""
        self.font_size = max(6, min(24, size))
        self._apply_font_size()
        config.save_panel_font_size("ai_chat_font_size", self.font_size)

    def get_font_size(self) -> int:
        return self.font_size

    def set_font_family(self, family: str | None) -> None:
        self.font_family = family or ""
        self._apply_font_size()
        config.save_ai_chat_font_family(family)

    def _apply_font_size(self) -> None:
        from PySide6.QtGui import QFont

        font = QFont()
        if self.font_family:
            font.setFamily(self.font_family)
        font.setPointSize(self.font_size)
        if hasattr(self, "chat_view"):
            self.chat_view.document().setDefaultFont(font)
        if hasattr(self, "input_edit"):
            self.input_edit.setFont(font)
            try:
                metrics = self.input_edit.fontMetrics()
                line_height = metrics.lineSpacing()
                self.input_edit.setFixedHeight(line_height * 2 + 6)
            except Exception:
                pass

    def _default_chat_font_family(self) -> str:
        from PySide6.QtGui import QFontDatabase
        import platform

        if platform.system() != "Windows":
            return ""
        families = {f.lower(): f for f in QFontDatabase().families()}
        for candidate in ("Segoe UI Variable", "Segoe UI"):
            picked = families.get(candidate.lower())
            if picked:
                return picked
        return ""

    def _new_chat(self) -> None:
        folder_path = self._current_folder_path()
        name, ok = QtWidgets.QInputDialog.getText(self, "New Chat", "Chat context:", text="Chat")
        if not ok or not name.strip():
            return
        chat = self.store.create_named_chat(folder_path, name.strip())
        # Apply default server/model preferences to the new chat
        default_server = self._config_default_server()
        if default_server:
            self.store.update_session_last_server(chat["id"], default_server)
            if default_server in self.server_manager.list_server_names():
                self.server_combo.setCurrentText(default_server)
                self.current_server = self.server_manager.get_server(default_server)
        models = get_available_models(self.current_server or {})
        default_model = self._config_default_model()
        if default_model and default_model in models:
            self.store.update_session_last_model(chat["id"], default_model)
        elif models:
            self.store.update_session_last_model(chat["id"], models[0])
        self._load_chat_tree(select_id=chat["id"])
        self._load_chat_messages(chat["id"])
        self.status_label.setText(f"Created chat '{chat['name']}'")

    def _load_system_prompts(self):
        self.system_prompts = []
        self.system_prompts_tree = {}
        home_prompts = Path.home() / ".stillpoint" / "ai-system-prompts.json"
        vault_prompts: Optional[Path] = None
        if self.vault_root:
            vault_prompts = Path(self.vault_root) / ".stillpoint" / "ai-system-prompts.json"

        source_paths = [home_prompts]
        if vault_prompts and not home_prompts.exists():
            source_paths.append(vault_prompts)
        asset_path = _resolve_asset_path("ai-system-prompts.json")
        if asset_path and not home_prompts.exists():
            source_paths.append(asset_path)

        self._prompts_source_path: Optional[Path] = None
        for path in source_paths:
            if path and path.exists():
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    tree = self._normalize_prompt_tree(data)
                    if tree:
                        self.system_prompts_tree = tree
                        self._prompts_source_path = path
                        print(f"[AIChat][prompts] loaded system prompts from {path}")
                        break
                    print(f"[AIChat][prompts] {path} had no prompts after normalization, skipping.")
                except Exception:
                    print(f"[AIChat][prompts] failed to load {path}")
                    continue

        if not self.system_prompts_tree:
            self.system_prompts_tree = {"User Defined": [{"name": "Default", "text": "You are a helpful assistant."}]}
        self.system_prompts = self._flatten_prompt_tree(self.system_prompts_tree)
        self.prompts_path = home_prompts

    def _save_system_prompts(self):
        if not hasattr(self, "prompts_path"):
            return
        try:
            self.prompts_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.prompts_path, "w", encoding="utf-8") as f:
                json.dump(self.system_prompts_tree, f, indent=2)
        except Exception:
            pass

    @staticmethod
    def _normalize_prompt_tree(data: object) -> dict:
        """Convert legacy list or nested dict to a consistent {category: prompts|subdict} tree."""
        def _coerce_prompts(seq: object) -> list[dict]:
            prompts: list[dict] = []
            if isinstance(seq, list):
                for entry in seq:
                    if isinstance(entry, dict) and entry.get("text"):
                        prompts.append({"name": entry.get("name", "Prompt"), "text": entry.get("text", "")})
            return prompts

        if isinstance(data, list):
            return {"User Defined": _coerce_prompts(data)}
        if not isinstance(data, dict):
            return {}
        tree: dict = {}
        for key, val in data.items():
            if isinstance(val, dict):
                subtree = {}
                for sub_key, sub_val in val.items():
                    if isinstance(sub_val, dict):
                        nested = {}
                        for leaf_key, leaf_val in sub_val.items():
                            prompts = _coerce_prompts(leaf_val)
                            if prompts:
                                nested[leaf_key] = prompts
                        if nested:
                            subtree[sub_key] = nested
                    else:
                        prompts = _coerce_prompts(sub_val)
                        if prompts:
                            subtree[sub_key] = prompts
                if subtree:
                    tree[key] = subtree
            else:
                prompts = _coerce_prompts(val)
                if prompts:
                    tree[key] = prompts
        return tree

    @staticmethod
    def _flatten_prompt_tree(tree: dict) -> list[dict]:
        """Flatten hierarchical prompts for quick access in other UI."""
        flat: list[dict] = []

        def _walk(node: object, path: list[str]):
            if isinstance(node, list):
                for entry in node:
                    if isinstance(entry, dict) and entry.get("text"):
                        flat.append({"name": entry.get("name", "Prompt"), "text": entry.get("text", ""), "path": list(path)})
            elif isinstance(node, dict):
                for key, val in node.items():
                    _walk(val, path + [key])

        _walk(tree, [])
        return flat

    def _open_prompt_dialog(self):
        dialog = SystemPromptDialog(
            self,
            prompts_tree=copy.deepcopy(self.system_prompts_tree),
            current_prompt=self.current_system_prompt,
        )
        if dialog.exec() == QtWidgets.QDialog.Accepted and dialog.selected_prompt:
            self.current_system_prompt = dialog.selected_prompt
            if self.current_session_id:
                self.store.update_session_system_prompt(self.current_session_id, self.current_system_prompt)
            self.system_prompts_tree = dialog.prompt_tree
            self.system_prompts = dialog.flattened_prompts
            self._save_system_prompts()

    def set_current_page(self, rel_path: Optional[str]) -> None:
        """Track the currently open page when the editor changes without auto-switching chats."""
        self.current_page_path = rel_path
        self._update_load_current_page_button()

    def open_chat_for_page(self, rel_path: Optional[str]) -> None:
        """Explicitly open (and create if needed) chat for the given page."""
        self.current_page_path = rel_path
        # Avoid triggering navigation back to the page on initial focus switch
        self._suppress_navigation = True
        chat = self.store.get_or_create_chat_for_page(rel_path)
        if chat:
            self.current_session_id = chat["id"]
            self._load_chat_tree(select_id=chat["id"])
            self._load_chat_messages(chat["id"])
        self._update_model_status()
        self._update_load_current_page_button()

    def open_named_chat(self, name: str, folder_path: str = "/") -> None:
        """Open (and create if needed) a stable named chat under the given folder."""
        self.current_page_path = None
        self._suppress_navigation = True
        chat = self.store.get_or_create_named_chat(folder_path, name)
        if chat:
            self.current_session_id = chat["id"]
            self._load_chat_tree(select_id=chat["id"])
            self._load_chat_messages(chat["id"])
        self._update_model_status()
        self._update_load_current_page_button()

    def ensure_context_page_ref(self, page_ref: str, *, index: bool = True) -> None:
        """Ensure a context page is attached to the current conversation."""
        if not page_ref:
            return
        if not self.ai_manager:
            try:
                self.ai_manager = AIManager()
            except Exception:
                return
        if not self.current_session_id:
            return
        if not self._current_ai_conversation_id:
            self._bind_ai_conversation()
        if not self._current_ai_conversation_id:
            return
        self._refresh_context_items()
        if any(item.kind == "page" and item.page_ref == page_ref for item in self._context_items):
            return
        try:
            self.ai_manager.add_context_page(self._current_ai_conversation_id, page_ref)
            if index:
                self._index_context_item("@", ContextCandidate(page_ref=page_ref, label=page_ref))
        except Exception:
            return
        self._refresh_context_items()

    def _load_current_page_chat(self) -> None:
        if not self.current_page_path:
            return
        self.open_chat_for_page(self.current_page_path)

    def _page_has_existing_chat(self, rel_path: Optional[str]) -> bool:
        if not rel_path:
            return False
        folder_path = "/" + Path(rel_path.lstrip("/")).parent.as_posix()
        existing = self.store.get_session_by_path(folder_path, "chat")
        return bool(existing)

    @staticmethod
    def _is_global_chat_path(path: Optional[str]) -> bool:
        """Return True when the chat represents the global/root context."""
        if not path:
            return True
        return path.strip("/") == ""

    def _update_load_current_page_button(self) -> None:
        path = self.current_page_path
        show = bool(path and path != self._current_chat_path and self._page_has_existing_chat(path))
        if path and self._context_matches_page(path):
            show = False
        self.load_page_chat_label.setVisible(show)
        if path and show:
            display = self._page_label(path)
            self.load_page_chat_label.setText(f"Load chat for {display}")
        else:
            self.load_page_chat_label.setText("")
        # Update context label for header
        if not self._is_global_chat_path(self._current_chat_path):
            label = self._page_label(self._current_chat_path) or "Page Chat"
            self.context_label.setText(f"Page Chat: {label}")
            self.context_bar.setVisible(True)
        else:
            self.context_label.setText("Global Chat")
            self.context_bar.setVisible(False)

    def get_active_chat_path(self) -> Optional[str]:
        """Return the folder path for the currently loaded chat session."""
        return self._current_chat_path

    def _context_matches_page(self, page: str | None) -> bool:
        if not page:
            return False
        for item in self._context_items:
            if item.kind == "page" and item.page_ref == page:
                return True
        return False

    def has_chat_for_path(self, rel_path: Optional[str]) -> bool:
        """Expose store lookup for whether a chat exists for the given page path."""
        return self.store.has_chat_for_path(rel_path)

    def set_vault_root(self, vault_root: Optional[str]) -> None:
        """Switch backing store to the current vault's .stillpoint folder."""
        self.vault_root = vault_root
        if vault_root:
            try:
                self.ai_manager = AIManager()
            except Exception:
                self.ai_manager = None
        else:
            self.ai_manager = None
        self._current_ai_conversation_id = None
        self._context_items = []
        self._update_context_summary()
        self._reload_context_index()
        self.store = AIChatStore(vault_root=vault_root)
        self.current_session_id = None
        self.messages = []
        self.chat_view.clear()
        self._load_system_prompts()
        self._load_chat_tree()

    def set_api_client(self, api_client: Optional[httpx.Client]) -> None:
        """Update the shared HTTP client used for vector operations."""
        self._vector_api = VectorAPIClient(api_client)
        self._api_client = api_client

    def _api_client_available(self) -> bool:
        if self._api_client is None:
            return False
        try:
            return not self._api_client.is_closed
        except Exception:
            return True

    def _collect_candidates_from_tree(self, tree: list[dict]) -> tuple[list[ContextCandidate], list[ContextCandidate]]:
        pages: list[ContextCandidate] = []
        trees: list[ContextCandidate] = []
        seen_pages: set[str] = set()
        seen_trees: set[str] = set()

        def norm(path: str) -> str:
            return path if path.startswith("/") else f"/{path}"

        def walk(node: dict) -> None:
            path = node.get("path")
            if path:
                path = norm(str(path))
                if path not in seen_trees:
                    seen_trees.add(path)
                    trees.append(ContextCandidate(page_ref=path, label=path))
            open_path = node.get("open_path")
            if open_path:
                open_path = norm(str(open_path))
                if open_path not in seen_pages:
                    seen_pages.add(open_path)
                    pages.append(ContextCandidate(page_ref=open_path, label=open_path))
            for child in node.get("children") or []:
                if isinstance(child, dict):
                    walk(child)

        for node in tree:
            if isinstance(node, dict):
                walk(node)
        return pages, trees

    def _fetch_context_candidates_from_api(self) -> tuple[list[ContextCandidate], list[ContextCandidate]]:
        if not self._api_client_available():
            return [], []
        try:
            _log_vector("Fetching context tree from API for context picker.")
            resp = self._api_client.get("/api/vault/tree", params={"path": "/", "recursive": "true"})
            resp.raise_for_status()
            payload = resp.json() or {}
            tree = payload.get("tree") or []
            return self._collect_candidates_from_tree(tree)
        except Exception as exc:
            _log_vector(f"Failed to load context tree from API: {exc}")
            return [], []
        self._select_default_chat()
        self._update_load_current_page_button()

class ContextOverlay(QtWidgets.QFrame):
    selected = QtCore.Signal(ContextCandidate)

    def __init__(self, parent=None):
        super().__init__(parent, QtCore.Qt.Popup | QtCore.Qt.FramelessWindowHint)
        self.setFrameShape(QtWidgets.QFrame.Box)
        self.setFrameShadow(QtWidgets.QFrame.Plain)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filter context…")
        self.filter_edit.setStyleSheet("font-size: 9px;")
        self.filter_edit.textChanged.connect(self._filter_items)
        layout.addWidget(self.filter_edit)
        self.list_widget = QListWidget()
        self.list_widget.setStyleSheet("font-size: 9px;")
        # Accept selection via keyboard (Enter) or mouse click
        self.list_widget.itemActivated.connect(self._on_item_activated)
        self.list_widget.itemClicked.connect(self._on_item_activated)
        layout.addWidget(self.list_widget)
        self._candidates: list[ContextCandidate] = []
        self._trigger: Optional[str] = None

    def show_for(self, trigger: str, candidates: list[ContextCandidate], position: QtCore.QPoint) -> None:
        self._trigger = trigger
        self._candidates = candidates
        self.filter_edit.clear()
        self._filter_items()
        self.adjustSize()
        parent = self.parentWidget()
        if parent:
            width = max(520, parent.width() - 16)
            self.setFixedWidth(width)
            self.list_widget.setFixedWidth(width - 16)
        # Prefer placing above the anchor point so the popup doesn't get
        # obscured at the bottom of the screen. If there's not enough room
        # above, fall back to placing below.
        screen_geo = QtWidgets.QApplication.primaryScreen().availableGeometry()
        width = self.width()
        height = self.height() or self.sizeHint().height()
        margin = 6
        # Horizontal placement: keep the left edge at the provided x (clamped)
        left = max(screen_geo.left(), min(position.x(), screen_geo.right() - width))
        # Try above first
        above_top = position.y() - height - margin
        below_top = position.y() + margin
        if above_top >= screen_geo.top():
            top = max(screen_geo.top(), min(above_top, screen_geo.bottom() - height))
        else:
            top = max(screen_geo.top(), min(below_top, screen_geo.bottom() - height))
        self.move(QtCore.QPoint(left, top))
        super().show()
        self.filter_edit.setFocus(QtCore.Qt.FocusReason.ShortcutFocusReason)

    def current_trigger(self) -> Optional[str]:
        return self._trigger

    def _filter_items(self) -> None:
        pattern = self.filter_edit.text().strip().lower()
        self.list_widget.clear()
        for candidate in self._candidates:
            searchable = f"{candidate.label} {candidate.page_ref}".lower()
            if pattern and pattern not in searchable:
                continue
            display = self._candidate_display_label(candidate)
            item = QtWidgets.QListWidgetItem(display)
            item.setData(QtCore.Qt.UserRole, candidate)
            self.list_widget.addItem(item)
        if self.list_widget.count():
            self.list_widget.setCurrentRow(0)

    def _on_item_activated(self, item: QtWidgets.QListWidgetItem) -> None:
        candidate = item.data(QtCore.Qt.UserRole)
        if isinstance(candidate, ContextCandidate):
            self.selected.emit(candidate)
            self.hide()

    def focusOutEvent(self, event: QFocusEvent) -> None:
        super().focusOutEvent(event)
        self.hide()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == QtCore.Qt.Key_Escape:
            self.hide()
            return
        if event.key() in (QtCore.Qt.Key_Down, QtCore.Qt.Key_J) and (
            event.key() != QtCore.Qt.Key_J or event.modifiers() & QtCore.Qt.ShiftModifier
        ):
            self._move_selection(1)
            return
        if event.key() in (QtCore.Qt.Key_Up, QtCore.Qt.Key_K) and (
            event.key() != QtCore.Qt.Key_K or event.modifiers() & QtCore.Qt.ShiftModifier
        ):
            self._move_selection(-1)
            return
        if event.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            current = self.list_widget.currentItem()
            if current:
                candidate = current.data(QtCore.Qt.UserRole)
                if isinstance(candidate, ContextCandidate):
                    self.selected.emit(candidate)
                    self.hide()
                    return
        super().keyPressEvent(event)

    def _move_selection(self, delta: int) -> None:
        count = self.list_widget.count()
        if not count:
            return
        current = self.list_widget.currentRow()
        next_row = (current + delta) % count
        self.list_widget.setCurrentRow(next_row)

    def _candidate_display_label(self, candidate: ContextCandidate) -> str:
        return self._label(candidate.page_ref, candidate.attachment_name)

    def _label(self, page_ref: str, attachment: Optional[str]) -> str:
        colon = path_to_colon(page_ref) or page_ref
        parts = [segment for segment in colon.split(":") if segment]
        leaf = parts[-1] if parts else Path(page_ref.lstrip("/")).stem or page_ref
        parent = ":".join(parts[:-1]) if len(parts) > 1 else ""
        if attachment:
            return f"{attachment} ({leaf})"
        if parent:
            return f"{leaf} ({parent})"
        return leaf


class ContextListPopup(QtWidgets.QFrame):
    activated = QtCore.Signal(ContextItem)
    deleted = QtCore.Signal(ContextItem)
    refreshed = QtCore.Signal(ContextItem)

    def __init__(self, parent=None):
        super().__init__(parent, QtCore.Qt.Popup | QtCore.Qt.FramelessWindowHint)
        self.setFrameShape(QtWidgets.QFrame.Box)
        self.setFrameShadow(QtWidgets.QFrame.Plain)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)
        header = QtWidgets.QWidget()
        header_layout = QtWidgets.QHBoxLayout(header)
        header_layout.setContentsMargins(2, 2, 2, 2)
        header_layout.addWidget(QtWidgets.QLabel("Action"))
        header_layout.addWidget(QtWidgets.QLabel("Name"), 1)
        header_layout.addWidget(QtWidgets.QLabel("Type"))
        layout.addWidget(header)
        self.table = QtWidgets.QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Action", "Name", "Type"])
        self.table.setStyleSheet("font-size: 9px;")
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.table.cellActivated.connect(self._handle_cell_activated)
        # Accept selection via single mouse click as well as keyboard
        self.table.cellClicked.connect(self._handle_cell_activated)
        layout.addWidget(self.table)

    def show_for(self, items: list[ContextItem], position: QtCore.QPoint, width_hint: Optional[int] = None) -> None:
        self.table.setRowCount(0)
        for item in items:
            row = self.table.rowCount()
            self.table.insertRow(row)
            refresh_btn = QtWidgets.QToolButton()
            refresh_btn.setText("↺")
            refresh_btn.setToolTip("Refresh context")
            refresh_btn.setAutoRaise(True)
            refresh_btn.clicked.connect(lambda checked=False, tgt=item: self.refreshed.emit(tgt))
            delete_btn = QtWidgets.QToolButton()
            delete_btn.setText("✕")
            delete_btn.setToolTip("Remove context")
            delete_btn.setAutoRaise(True)
            delete_btn.clicked.connect(lambda checked=False, tgt=item: self.deleted.emit(tgt))
            actions = QtWidgets.QWidget()
            actions_layout = QtWidgets.QHBoxLayout(actions)
            actions_layout.setContentsMargins(0, 0, 0, 0)
            actions_layout.setSpacing(4)
            actions_layout.addWidget(refresh_btn)
            actions_layout.addWidget(delete_btn)
            self.table.setCellWidget(row, 0, actions)
            name_item = QtWidgets.QTableWidgetItem(self._context_label(item))
            name_item.setData(QtCore.Qt.UserRole, item)
            self.table.setItem(row, 1, name_item)
            self.table.setItem(row, 2, QtWidgets.QTableWidgetItem(self._kind_label(item.kind)))
            self.table.setRowHeight(row, 28)
        if self.table.rowCount():
            self.table.setCurrentCell(0, 1)
        self.adjustSize()
        parent = self.parentWidget()
        width = width_hint
        if width is None and parent:
            if hasattr(parent, "chat_view"):
                width = parent.chat_view.width()
            else:
                width = parent.width()
        if width is None or width <= 0:
            width = 480
        if parent:
            width = min(width, parent.width())
        self.setFixedWidth(width)
        # Prefer to show above the given position when possible (keeps popup
        # readable when input is at bottom of screen). Otherwise show below.
        screen_geo = QtWidgets.QApplication.primaryScreen().availableGeometry()
        self.adjustSize()
        height = self.height() or self.sizeHint().height()
        margin = 6
        left = max(screen_geo.left(), min(position.x(), screen_geo.right() - width))
        above_top = position.y() - height - margin
        below_top = position.y() + margin
        if above_top >= screen_geo.top():
            top = max(screen_geo.top(), min(above_top, screen_geo.bottom() - height))
        else:
            top = max(screen_geo.top(), min(below_top, screen_geo.bottom() - height))
        self.move(QtCore.QPoint(left, top))
        super().show()

    def _context_label(self, item: ContextItem) -> str:
        if item.kind == "attachment" and item.attachment_name:
            return f"{item.page_ref}/{item.attachment_name}"
        return item.page_ref

    def _kind_label(self, kind: str) -> str:
        return {"page": "Page", "page-tree": "Tree", "attachment": "File"}.get(kind, kind.title())

    def _handle_cell_activated(self, row: int, column: int) -> None:
        data = self.table.item(row, 1)
        if data:
            ctx = data.data(QtCore.Qt.UserRole)
            if isinstance(ctx, ContextItem):
                self.activated.emit(ctx)

    def focusOutEvent(self, event: QFocusEvent) -> None:
        super().focusOutEvent(event)
        self.hide()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == QtCore.Qt.Key_Escape:
            self.hide()
            return
        if event.key() in (QtCore.Qt.Key_Down, QtCore.Qt.Key_J) and (
            event.key() != QtCore.Qt.Key_J or event.modifiers() & QtCore.Qt.ShiftModifier
        ):
            self._move_selection(1)
            return
        if event.key() in (QtCore.Qt.Key_Up, QtCore.Qt.Key_K) and (
            event.key() != QtCore.Qt.Key_K or event.modifiers() & QtCore.Qt.ShiftModifier
        ):
            self._move_selection(-1)
            return
        super().keyPressEvent(event)

    def _move_selection(self, delta: int) -> None:
        count = self.table.rowCount()
        if not count:
            return
        current = self.table.currentRow()
        next_row = (current + delta) % count if current >= 0 else 0
        self.table.setCurrentCell(next_row, 1)

    def remove_item(self, target: ContextItem) -> None:
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 1)
            if item and item.data(QtCore.Qt.UserRole) == target:
                self.table.removeRow(row)
                return

class SystemPromptDialog(QtWidgets.QDialog):
    def __init__(
        self,
        parent=None,
        prompts_tree: Optional[Dict] = None,
        current_prompt: Optional[str] = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("System Prompts")
        self.prompt_tree = prompts_tree or {"User Defined": []}
        self.flattened_prompts: list[dict] = []
        self.selected_prompt: Optional[str] = current_prompt
        self.resize(520, 520)
        self._dirty = False
        self._build_ui()
        self._reload_tree()
        self._select_prompt_by_text(current_prompt)

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)

        row1 = QtWidgets.QHBoxLayout()
        row1.addWidget(QtWidgets.QLabel("Category:"))
        self.level1_combo = QtWidgets.QComboBox()
        self.level1_combo.currentIndexChanged.connect(self._on_level1_changed)
        row1.addWidget(self.level1_combo, 1)
        layout.addLayout(row1)

        row2 = QtWidgets.QHBoxLayout()
        self.level2_label = QtWidgets.QLabel("Subcategory:")
        row2.addWidget(self.level2_label)
        self.level2_combo = QtWidgets.QComboBox()
        self.level2_combo.currentIndexChanged.connect(self._on_level2_changed)
        row2.addWidget(self.level2_combo, 1)
        layout.addLayout(row2)

        row3 = QtWidgets.QHBoxLayout()
        row3.addWidget(QtWidgets.QLabel("Prompt:"))
        self.prompt_combo = QtWidgets.QComboBox()
        self.prompt_combo.currentIndexChanged.connect(self._on_prompt_select)
        row3.addWidget(self.prompt_combo, 1)
        del_btn = QtWidgets.QPushButton("Delete")
        del_btn.clicked.connect(self._delete_prompt)
        row3.addWidget(del_btn)
        layout.addLayout(row3)

        self.helper_label = QtWidgets.QLabel("Define how the AI should behave (role, tone, rules, formatting).")
        layout.addWidget(self.helper_label)

        self.text_edit = QtWidgets.QPlainTextEdit()
        self.text_edit.textChanged.connect(self._on_text_changed)
        layout.addWidget(self.text_edit, 1)

        btn_row = QtWidgets.QHBoxLayout()
        self.save_btn = QtWidgets.QPushButton("Save Prompt")
        self.save_btn.clicked.connect(self._save_prompt)
        import_btn = QtWidgets.QPushButton("Import")
        import_btn.clicked.connect(self._import_prompts)
        export_btn = QtWidgets.QPushButton("Export")
        export_btn.clicked.connect(self._export_prompts)
        ok_btn = QtWidgets.QPushButton("Use Prompt")
        ok_btn.clicked.connect(self.accept)
        btn_row.addWidget(self.save_btn)
        btn_row.addWidget(import_btn)
        btn_row.addWidget(export_btn)
        btn_row.addStretch()
        btn_row.addWidget(ok_btn)
        layout.addLayout(btn_row)

    def _reload_tree(self) -> None:
        if not self.prompt_tree:
            self.prompt_tree = {"User Defined": []}
        self.flattened_prompts = self._flatten_prompt_tree(self.prompt_tree)
        self.level1_combo.blockSignals(True)
        self.level1_combo.clear()
        for key in self.prompt_tree.keys():
            self.level1_combo.addItem(key)
        self.level1_combo.blockSignals(False)
        if self.level1_combo.count() == 0:
            self.prompt_combo.clear()
            return
        self._on_level1_changed(self.level1_combo.currentIndex())

    def _on_level1_changed(self, idx: int):
        key = self.level1_combo.currentText()
        node = self.prompt_tree.get(key) if key else None
        has_sub = isinstance(node, dict)
        self.level2_combo.blockSignals(True)
        self.level2_combo.clear()
        if has_sub:
            for sub in node.keys():
                self.level2_combo.addItem(sub)
            self.level2_combo.setVisible(True)
            self.level2_label.setVisible(True)
        else:
            self.level2_combo.setVisible(False)
            self.level2_label.setVisible(False)
        self.level2_combo.blockSignals(False)
        self._reload_prompt_combo()

    def _on_level2_changed(self, idx: int):
        self._reload_prompt_combo()

    def _prompt_list_for_selection(self) -> list[dict]:
        level1 = self.level1_combo.currentText()
        node = self.prompt_tree.get(level1) if level1 else None
        if isinstance(node, dict):
            sub = self.level2_combo.currentText()
            node = node.get(sub) if sub else None
        return node if isinstance(node, list) else []

    def _reload_prompt_combo(self):
        prompts = self._prompt_list_for_selection()
        self.prompt_combo.blockSignals(True)
        self.prompt_combo.clear()
        self.prompt_combo.addItem("Add New...")
        for prompt in prompts:
            self.prompt_combo.addItem(prompt.get("name", "Prompt"))
        self.prompt_combo.blockSignals(False)
        self._on_prompt_select(self.prompt_combo.currentIndex())

    def _on_prompt_select(self, idx: int):
        prompts = self._prompt_list_for_selection()
        if idx <= 0 or idx - 1 >= len(prompts):
            self.text_edit.clear()
            self.selected_prompt = None
            self._update_save_button_label()
            return
        prompt = prompts[idx - 1]
        self.text_edit.setPlainText(prompt.get("text", ""))
        self.selected_prompt = prompt.get("text", "")
        self._dirty = False
        self._update_save_button_label()

    def _save_prompt(self):
        text = self.text_edit.toPlainText()
        if not text.strip():
            QtWidgets.QMessageBox.warning(self, "Prompt", "Prompt text cannot be empty.")
            return
        idx = self.prompt_combo.currentIndex()
        if idx <= 0:
            name, ok = QtWidgets.QInputDialog.getText(self, "Prompt name", "Name:")
            if not ok or not name.strip():
                return
            target = self.prompt_tree.setdefault("User Defined", [])
            if not isinstance(target, list):
                target = []
                self.prompt_tree["User Defined"] = target
            target.append({"name": name.strip(), "text": text})
            self._reload_tree()
            self._select_prompt_by_text(text)
        else:
            prompts = self._prompt_list_for_selection()
            if idx - 1 < len(prompts):
                prompts[idx - 1]["text"] = text
            self._dirty = False
            self._update_save_button_label()

    def _delete_prompt(self):
        idx = self.prompt_combo.currentIndex()
        prompts = self._prompt_list_for_selection()
        if idx <= 0 or idx - 1 >= len(prompts):
            return
        del prompts[idx - 1]
        self._reload_tree()
        self.text_edit.clear()
        self.selected_prompt = None
        self._update_save_button_label()

    def _import_prompts(self):
        default_dir = str(Path.home())
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Import prompts", str(Path(default_dir) / "ai-system-prompts.json"), "JSON files (*.json)"
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.prompt_tree = AIChatPanel._normalize_prompt_tree(data)
            self._reload_tree()
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Import failed", str(exc))

    def _export_prompts(self):
        default_dir = str(Path.home())
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Export prompts",
            str(Path(default_dir) / "ai-system-prompts.json"),
            "JSON files (*.json)",
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.prompt_tree, f, indent=2)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Export failed", str(exc))

    def _select_prompt_by_text(self, prompt_text: Optional[str]) -> None:
        if not prompt_text:
            self.prompt_combo.setCurrentIndex(0)
            return
        for entry in self.flattened_prompts:
            if entry.get("text") == prompt_text:
                path = entry.get("path") or []
                if path:
                    level1 = path[0]
                    idx1 = self.level1_combo.findText(level1)
                    if idx1 >= 0:
                        self.level1_combo.setCurrentIndex(idx1)
                    if len(path) > 1:
                        level2 = path[1]
                        idx2 = self.level2_combo.findText(level2)
                        if idx2 >= 0:
                            self.level2_combo.setCurrentIndex(idx2)
                    prompts = self._prompt_list_for_selection()
                    for idx, prompt in enumerate(prompts, start=1):
                        if prompt.get("text") == prompt_text:
                            self.prompt_combo.setCurrentIndex(idx)
                            return
        self.text_edit.setPlainText("Prompt not found")
        self.prompt_combo.setCurrentIndex(0)

    def accept(self):
        idx = self.prompt_combo.currentIndex()
        prompts = self._prompt_list_for_selection()
        if idx > 0 and idx - 1 < len(prompts):
            self.selected_prompt = prompts[idx - 1].get("text", "")
        else:
            self.selected_prompt = self.text_edit.toPlainText().strip() or None
        super().accept()

    def _update_save_button_label(self):
        idx = self.prompt_combo.currentIndex()
        if idx <= 0:
            label = "Save Prompt"
            enabled = bool(self.text_edit.toPlainText().strip())
        else:
            label = "Update Prompt"
            enabled = self._dirty
        if hasattr(self, "save_btn"):
            self.save_btn.setText(label)
            self.save_btn.setEnabled(enabled)

    def _on_text_changed(self):
        idx = self.prompt_combo.currentIndex()
        if idx > 0:
            self._dirty = True
        self._update_save_button_label()

    @staticmethod
    def _flatten_prompt_tree(tree: dict) -> list[dict]:
        flat: list[dict] = []

        def _walk(node: object, path: list[str]):
            if isinstance(node, list):
                for entry in node:
                    if isinstance(entry, dict) and entry.get("text"):
                        flat.append({"name": entry.get("name", "Prompt"), "text": entry.get("text", ""), "path": list(path)})
            elif isinstance(node, dict):
                for key, val in node.items():
                    _walk(val, path + [key])

        _walk(tree, [])
        return flat
