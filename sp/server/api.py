from __future__ import annotations

import argparse
import base64
import binascii
import concurrent.futures
import hashlib
import html
import importlib
from importlib import resources as importlib_resources
from importlib import metadata as importlib_metadata
import json
import os
import re
import secrets
import shutil
import sqlite3
import sys
import threading
import time
import traceback
import uuid
from contextlib import asynccontextmanager
from datetime import date as Date
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Literal, Optional
from urllib.parse import quote, unquote, urlparse

import httpx
import markdown as md
import uvicorn
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Depends, FastAPI, File as FastAPISingleFile, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jinja2 import Environment, FileSystemLoader, select_autoescape
from jose import JWTError, jwt
from markupsafe import Markup
from pydantic import BaseModel, ConfigDict, Field

# --- Fix for FastAPI + PyInstaller + python-multipart ---
try:
    multipart = importlib.import_module("multipart")
    # FastAPI checks for multipart.__version__ to verify python-multipart
    if not getattr(multipart, "__version__", None):
        try:
            # Try to get the real version from the installed dist
            multipart.__version__ = importlib_metadata.version("python-multipart")
        except Exception:
            # Fallback: any non-empty string will satisfy FastAPI's check
            multipart.__version__ = "0.0.0"
except ImportError:
    # If multipart truly isn't installed, FastAPI will still raise a clear error later
    pass
# --- end fix ---

from sp.server import indexer
from sp.server import file_ops
from sp.server import search_index
from sp.server import vault_reorg
from sp.server import homebase_api
from sp.server import homebase_gc
from sp.server.adapters import files
from sp.server.adapters.files import FileAccessError, LEGACY_SUFFIX, PAGE_SUFFIX, PAGE_SUFFIXES
from sp.server.state import vault_state
from sp.server.vector import vector_manager
from sp.rag.index import RetrievedChunk
from sp import VERSION as STILLPOINT_VERSION
from sp.app import config
from sp.app import indexer as app_indexer
from sp.app.quickcapture_common import append_quick_capture_section, resolve_attachment_placeholders
from sp.app.capture_triage import (
    capture_header,
    invalidate_triage_cache,
    list_quick_capture_chunks,
    list_triage_items,
    new_capture_id,
    process_quick_capture_chunk,
    process_triage_item,
)
from sp.app.task_mutations import TaskConflictError, TaskMutationTarget, mutate_task, undo_file_mutation
from sp.app.ui.ai_api import build_api_request
from sp.logging_flags import log_enabled
from tools.homebase_seed_lib import create_homebase_vault, seed_homebase_vault

_ANSI_BLUE = "\033[94m"
_ANSI_RESET = "\033[0m"


def _env_flag_enabled(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _homebase_permission_check_disabled() -> bool:
    return _env_flag_enabled("IGNORE__FILE_PERMISSION_CHECK", default=False)


def _embedded_server_permission_repair_enabled() -> bool:
    return _env_flag_enabled("STILLPOINT_EMBEDDED_SERVER", default=False)


def _raise_for_homebase_permission_faults(vaults_root: Path) -> None:
    if _homebase_permission_check_disabled():
        return
    if _embedded_server_permission_repair_enabled():
        repaired = homebase_api.repair_homebase_auth_file_permissions(vaults_root)
        if repaired:
            sys.stderr.write("Repaired Homebase auth file permissions for embedded server startup:\n")
            for item in repaired:
                sys.stderr.write(f"  - {item}\n")
            sys.stderr.flush()
    faults = homebase_api.collect_homebase_auth_file_permission_faults(vaults_root)
    if not faults:
        return
    lines = [
        "",
        f"{_ANSI_BLUE}{'=' * 80}{_ANSI_RESET}",
        f"{_ANSI_BLUE}SECURITY ERROR: Homebase auth file permissions are too open.{_ANSI_RESET}",
        f"{_ANSI_BLUE}{'=' * 80}{_ANSI_RESET}",
        f"{_ANSI_BLUE}The server refused to start because these files allow group or other access:{_ANSI_RESET}",
    ]
    lines.extend(f"  - {fault}" for fault in faults)
    lines.extend(
        [
            "",
            "Expected mode: 0600 for auth.json and tokens.json",
            "To bypass this check temporarily (NOT RECOMMENDED), set IGNORE__FILE_PERMISSION_CHECK=true",
        ]
    )
    message = "\n".join(lines)
    sys.stderr.write(f"{message}\n")
    sys.stderr.flush()
    raise RuntimeError("Homebase auth file permission check failed")


def _log_api(message: str) -> None:
    if log_enabled("api_server"):
        print(message)


def _log_sort(message: str) -> None:
    if log_enabled("sorting_reorder"):
        print(message)

_LOCAL_FILE_OPS_ENABLED = os.getenv("ATTACHMENTS_LOCAL_FILE_OPS", "0") not in (
    "0",
    "false",
    "False",
    "",
    None,
)

_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}
_REMOTE_CONTEXT_HEADER = "x-stillpoint-window-id"
_TASKS_CACHE: dict[tuple[str, tuple[str, ...], bool, bool, bool, Optional[str]], list[dict]] = {}
_TASKS_STALE_CACHE: dict[
    tuple[str, tuple[str, ...], bool, bool, bool, Optional[str]],
    tuple[float, list[dict]],
] = {}
_TASK_CACHE_VERSION: int = -1
_TASKS_QUERY_TIMEOUT_S = max(0.1, float(os.getenv("SP_TASKS_QUERY_TIMEOUT_S", "12.0")))
_TASKS_QUERY_WORKERS = max(1, int(os.getenv("SP_TASKS_QUERY_WORKERS", "4")))
_TASKS_STALE_MAX_AGE_S = max(1.0, float(os.getenv("SP_TASKS_STALE_MAX_AGE_S", "300.0")))
_TASKS_ALLOW_STALE_ON_TIMEOUT = os.getenv("SP_TASKS_ALLOW_STALE_ON_TIMEOUT", "1").lower() in (
    "1",
    "true",
    "yes",
    "on",
)
_TASKS_ALLOW_DEGRADED_FALLBACK = os.getenv("SP_TASKS_ALLOW_DEGRADED_FALLBACK", "1").lower() in (
    "1",
    "true",
    "yes",
    "on",
)
_TASKS_FALLBACK_TIMEOUT_S = max(0.1, float(os.getenv("SP_TASKS_FALLBACK_TIMEOUT_S", "4.0")))
_TASKS_QUERY_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=_TASKS_QUERY_WORKERS,
    thread_name_prefix="sp-tasks-query",
)
_TASKS_INFLIGHT: dict[
    tuple[str, tuple[str, ...], bool, bool, bool, Optional[str]],
    concurrent.futures.Future[list[dict]],
] = {}
_TASKS_INFLIGHT_LOCK = threading.Lock()

_TASK_DATE_PATTERN = re.compile(r"\s*[<>][0-9]{4}-[0-9]{2}-[0-9]{2}")
_TASK_START_PATTERN = re.compile(r">([0-9]{4}-[0-9]{2}-[0-9]{2})")
_TASK_DUE_PATTERN = re.compile(r"<([0-9]{4}-[0-9]{2}-[0-9]{2})")

_TREE_CACHE: dict[tuple[str, str, bool, bool], dict[str, object]] = {}
_LOCAL_UI_TOKEN: Optional[str] = None
_VAULTS_ROOT: Optional[str] = None
_UI_QUICK_CAPTURE_HOOK = None
_TASK_MUTATION_UNDO: dict[str, dict] = {}
_TASK_MUTATION_UNDO_LOCK = threading.Lock()
_EXCALIDRAW_OPEN_PAGE_REQUESTS: list[str] = []
_EXCALIDRAW_OPEN_PAGE_LOCK = threading.Lock()

_REINDEX_JOBS: dict[str, dict] = {}  # job_id -> {status, progress, message, total, current}
_REINDEX_LOCK = threading.Lock()
_TASK_AUTO_REINDEX_UNTIL: dict[str, float] = {}


def _normalize_tree_path(path: str) -> str:
    cleaned = (path or "/").strip().replace("\\", "/")
    if not cleaned.startswith("/"):
        cleaned = f"/{cleaned}"
    if cleaned != "/":
        cleaned = cleaned.rstrip("/") or "/"
    return cleaned or "/"


def _format_file_op_detail(context: str, exc: BaseException) -> dict:
    return {
        "message": f"{context}: {exc}",
        "exception": f"{exc.__class__.__name__}: {exc}",
        "traceback": traceback.format_exc(),
    }


def _raise_file_http(status_code: int, context: str, exc: BaseException) -> None:
    raise HTTPException(status_code=status_code, detail=_format_file_op_detail(context, exc)) from exc


def _colon_to_page_path(colon_path: str) -> str:
    cleaned = (colon_path or "").strip()
    if cleaned.startswith(":"):
        cleaned = cleaned.lstrip(":")
    if "#" in cleaned:
        cleaned = cleaned.split("#", 1)[0]
    cleaned = cleaned.strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="Custom capture page is required")
    parts = [part.strip() for part in cleaned.split(":") if part.strip()]
    if not parts:
        raise HTTPException(status_code=400, detail="Custom capture page is required")
    parts = [part.replace("_", " ") for part in parts]
    folder_path = "/".join(parts)
    file_name = f"{parts[-1]}{PAGE_SUFFIX}"
    return f"/{folder_path}/{file_name}"


def _build_quick_capture_entry(
    text: str,
    timestamp: str,
    attachments: Optional[list[dict]] = None,
    *,
    capture_id: Optional[str] = None,
    inbox: bool = False,
) -> list[str]:
    text, trailing_attachment_lines = resolve_attachment_placeholders(text, attachments)
    lines = [line.rstrip() for line in text.splitlines()]
    if not lines:
        return []
    first = capture_header(timestamp, capture_id=capture_id, inbox=inbox)
    note_lines = [f"  {line}" for line in lines]
    return [first] + note_lines + trailing_attachment_lines + ["", "---"]


def _append_quick_capture_section(content: str, entry_lines: list[str]) -> str:
    return append_quick_capture_section(content, entry_lines)


def _get_cached_tree(root: Path, path: str, recursive: bool, include_journal: bool, version: int) -> list[dict] | None:
    key = (str(root), path, recursive, include_journal)
    cached = _TREE_CACHE.get(key)
    if not cached:
        return None
    if cached.get("version") != version:
        _TREE_CACHE.pop(key, None)
        return None
    try:
        return json.loads(cached["tree"])
    except Exception:
        return None


def _set_cached_tree(
    root: Path, path: str, recursive: bool, include_journal: bool, version: int, tree: list[dict]
) -> None:
    _TREE_CACHE[(str(root), path, recursive, include_journal)] = {
        "version": version,
        "tree": json.dumps(tree),
    }


def _clear_tree_cache() -> None:
    _TREE_CACHE.clear()


def set_vaults_root(path: Optional[str]) -> None:
    """Set the base folder where server-managed vaults live."""
    global _VAULTS_ROOT
    _VAULTS_ROOT = path or None


def _get_vaults_root() -> Path:
    root = _VAULTS_ROOT or os.getenv("STILLPOINT_VAULTS_ROOT", "vaults")
    root_path = Path(root).expanduser()
    if not root_path.is_absolute():
        root_path = (Path.cwd() / root_path).resolve()
    return root_path.resolve()


def _ensure_vaults_root() -> Path:
    root = _get_vaults_root()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _resolve_vault_path(path: str) -> Path:
    raw = Path(path).expanduser()
    if raw.is_absolute():
        return raw.resolve()
    root = _ensure_vaults_root()
    candidate = (root / raw).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Vault path must be under vaults root") from exc
    return candidate


def _normalize_vault_name(name: str) -> str:
    cleaned = (name or "").strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="Vault name is required")
    if cleaned in (".", ".."):
        raise HTTPException(status_code=400, detail="Vault name is invalid")
    if "/" in cleaned or "\\" in cleaned:
        raise HTTPException(status_code=400, detail="Vault name must be a single folder name")
    return cleaned


# ===== JWT Authentication Configuration =====
JWT_SECRET = os.getenv("JWT_SECRET") or secrets.token_urlsafe(32)
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 15
REFRESH_TOKEN_EXPIRE_DAYS = 7
AUTH_ENABLED = os.getenv("AUTH_ENABLED", "true").lower() in ("true", "1", "yes")
SERVER_ADMIN_PASSWORD = os.getenv("SERVER_ADMIN_PASSWORD")

password_hasher = PasswordHasher()
security = HTTPBearer(auto_error=False)

_PRINT_TEMPLATES = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    autoescape=select_autoescape(["html", "xml"]),
)
_IMAGE_SRC_RE = re.compile(r'(<img[^>]+src=["\'])([^"\']+)(["\'])', re.IGNORECASE)
_IMAGE_MD_SIZE_RE = re.compile(
    r'!\[(?P<alt>[^\]]*)\]\((?P<path>[^)\s]+)(?:\s+"(?P<title>[^"]*)")?\)\{width=(?P<width>\d+)\}',
    re.MULTILINE,
)
_IMAGE_MD_ANY_RE = re.compile(
    r'!\[[^\]]*\]\([^)\s]+(?:\s+"[^"]*")?\)(?:\{width=\d+\})?',
    re.MULTILINE,
)
_ZIM_LINK_RE = re.compile(r"\[(?P<target>[^\]|]+)\|(?P<label>[^\]]*)\]")


class AuthModels:
    class SetupRequest(BaseModel):
        username: str = Field(..., min_length=3, max_length=50)
        password: str = Field(..., min_length=8)

    class LoginRequest(BaseModel):
        username: str
        password: str

    class ChangeRequest(BaseModel):
        username: str
        old_password: str = Field(..., min_length=8)
        new_password: str = Field(..., min_length=8)

    class UserCreateRequest(BaseModel):
        username: str = Field(..., min_length=3, max_length=50)
        password: str = Field(..., min_length=8)
        role: Literal["admin", "normal"] = "normal"
        perm: Optional[Literal["read", "read_write"]] = None

    class UserUpdateRequest(BaseModel):
        username: Optional[str] = None
        role: Optional[Literal["admin", "normal"]] = None
        perm: Optional[Literal["read", "read_write"]] = None
        password: Optional[str] = None

    class TokenResponse(BaseModel):
        access_token: str
        refresh_token: str
        token_type: str = "bearer"

    class UserInfo(BaseModel):
        username: str
        is_admin: bool = True
        can_write: bool = True
        role: str = "admin"
        perm: str = "read_write"

    class PrintTokenRequest(BaseModel):
        ttl_seconds: int = Field(default=900, ge=60, le=3600)


def _create_token(data: dict, expires_delta: timedelta) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + expires_delta
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALGORITHM)


def _verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        password_hasher.verify(hashed_password, plain_password)
        return True
    except VerifyMismatchError:
        return False


def _hash_password(password: str) -> str:
    return password_hasher.hash(password)


def _utc_now_iso() -> str:
    return datetime.utcnow().isoformat()


def _normalize_role(value: Optional[str]) -> str:
    role = str(value or "").strip().lower()
    if role == "admin":
        return "admin"
    return "normal"


def _normalize_perm(value: Optional[str], role: str) -> str:
    if role == "admin":
        return "read_write"
    perm = str(value or "").strip().lower()
    if perm in {"read+write", "read_write", "write", "readwrite"}:
        return "read_write"
    return "read"

def _server_password_hash() -> Optional[str]:
    if not SERVER_ADMIN_PASSWORD:
        return None
    return hashlib.sha256(SERVER_ADMIN_PASSWORD.encode()).hexdigest()


def _combined_vault_password(password: str) -> str:
    if not SERVER_ADMIN_PASSWORD:
        return password
    return f"{password}:{SERVER_ADMIN_PASSWORD}"


def _build_auth_config(username: str, password: str) -> dict:
    now = _utc_now_iso()
    user_record = {
        "username": username,
        "password_hash": _hash_password(_combined_vault_password(password)),
        "vault_password_hash": _hash_password(password),
        "server_password_hash": _server_password_hash(),
        "role": "admin",
        "perm": "read_write",
        "created_at": now,
        "last_login_at": None,
        "last_password_change_at": now,
    }
    return {
        "schema_version": 2,
        "configured_at": now,
        "users": {username: user_record},
    }


def _normalize_auth_config(payload: dict) -> tuple[dict, bool]:
    if not isinstance(payload, dict):
        return {}, False
    changed = False
    if "users" not in payload:
        username = str(payload.get("username") or "").strip()
        password_hash = payload.get("password_hash")
        if not username or not password_hash:
            return {}, False
        configured_at = str(payload.get("configured_at") or _utc_now_iso())
        user_record = {
            "username": username,
            "password_hash": password_hash,
            "vault_password_hash": payload.get("vault_password_hash"),
            "server_password_hash": payload.get("server_password_hash"),
            "role": "admin",
            "perm": "read_write",
            "created_at": configured_at,
            "last_login_at": None,
            "last_password_change_at": configured_at,
        }
        payload = {
            "schema_version": 2,
            "configured_at": configured_at,
            "users": {username: user_record},
        }
        changed = True

    if payload.get("schema_version") != 2:
        payload["schema_version"] = 2
        changed = True

    users = payload.get("users")
    if not isinstance(users, dict):
        payload["users"] = {}
        users = payload["users"]
        changed = True

    configured_at = str(payload.get("configured_at") or _utc_now_iso())
    payload["configured_at"] = configured_at
    for username, record in list(users.items()):
        if not isinstance(record, dict):
            users.pop(username, None)
            changed = True
            continue
        record.setdefault("username", username)
        role = _normalize_role(record.get("role"))
        perm = _normalize_perm(record.get("perm"), role)
        if record.get("role") != role:
            record["role"] = role
            changed = True
        if record.get("perm") != perm:
            record["perm"] = perm
            changed = True
        if not record.get("created_at"):
            record["created_at"] = configured_at
            changed = True
        if "last_login_at" not in record:
            record["last_login_at"] = None
            changed = True
        if not record.get("last_password_change_at"):
            record["last_password_change_at"] = record.get("created_at") or configured_at
            changed = True
    return payload, changed


def _store_auth_config_at_path(db_path: Path, config: dict) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)",
            ("auth_config", json.dumps(config))
        )
        conn.commit()
    finally:
        conn.close()


def _get_auth_config():
    """Get auth configuration from vault's kv store"""
    try:
        vault_root = vault_state.get_root()
    except Exception:
        return None
    if not vault_root:
        return None
    db_path = vault_root / ".stillpoint" / "settings.db"
    if not db_path.exists():
        return None
    
    conn = sqlite3.connect(str(db_path))
    try:
        cursor = conn.execute("SELECT value FROM kv WHERE key = 'auth_config'")
        row = cursor.fetchone()
        if row:
            payload = json.loads(row[0])
            normalized, changed = _normalize_auth_config(payload)
            if normalized and changed:
                try:
                    conn.execute(
                        "INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)",
                        ("auth_config", json.dumps(normalized)),
                    )
                    conn.commit()
                except Exception:
                    pass
            return normalized or None
    except Exception:
        pass
    finally:
        conn.close()
    return None


def _set_auth_config(username: str, password: str):
    """Store auth configuration in vault's kv store"""
    try:
        vault_root = vault_state.get_root()
    except Exception:
        raise HTTPException(status_code=500, detail="No vault selected")
    if not vault_root:
        raise HTTPException(status_code=500, detail="No vault selected")
    db_path = vault_root / ".stillpoint" / "settings.db"

    config_payload = _build_auth_config(username, password)
    _store_auth_config_at_path(db_path, config_payload)


def _init_vault_db(root: Path) -> None:
    """Ensure the vault settings DB exists with schema."""
    db_dir = root / ".stillpoint"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "settings.db"
    conn = sqlite3.connect(str(db_path))
    try:
        config._ensure_schema(conn)
    finally:
        conn.close()


def _set_auth_config_for_path(root: Path, username: str, password: str) -> None:
    """Store auth configuration for a specific vault path."""
    db_path = root / ".stillpoint" / "settings.db"

    config_payload = _build_auth_config(username, password)
    _store_auth_config_at_path(db_path, config_payload)


def _get_user_record(auth_config: dict, username: str) -> Optional[dict]:
    users = auth_config.get("users")
    if not isinstance(users, dict):
        return None
    return users.get(username)


def _verify_user_password(user: dict, password: str) -> tuple[bool, Optional[str]]:
    vault_hash = user.get("vault_password_hash")
    server_hash = user.get("server_password_hash")
    combined_ok = _verify_password(_combined_vault_password(password), user.get("password_hash", ""))
    if vault_hash:
        vault_ok = _verify_password(password, vault_hash)
        current_server_hash = _server_password_hash()
        if combined_ok:
            return True, None
        if vault_ok and current_server_hash and server_hash and server_hash != current_server_hash:
            return False, "Server password changed; update vault password"
        if vault_ok and current_server_hash and not server_hash:
            return False, "Server password required; update vault password"
        return False, "Invalid credentials"
    if combined_ok:
        return True, None
    return False, "Invalid credentials"


def set_local_ui_token(token: Optional[str]) -> None:
    """Register a shared local UI token for localhost auth bypass."""
    global _LOCAL_UI_TOKEN
    _LOCAL_UI_TOKEN = token or None


def set_ui_quick_capture_hook(callback) -> None:
    """Register a UI callback for Quick Capture overlay requests."""
    global _UI_QUICK_CAPTURE_HOOK
    _UI_QUICK_CAPTURE_HOOK = callback


def _is_localhost_request(request: Request) -> bool:
    """Check if request is from localhost."""
    return request.client and request.client.host in _LOCAL_HOSTS


def _verify_server_admin_password(password_hash: str) -> bool:
    """Verify hashed server admin password."""
    if not SERVER_ADMIN_PASSWORD:
        return False
    expected_hash = hashlib.sha256(SERVER_ADMIN_PASSWORD.encode()).hexdigest()
    return password_hash == expected_hash


async def verify_server_admin(request: Request) -> None:
    """Dependency to verify server admin access for vault operations."""
    # Localhost always has access
    if _is_localhost_request(request):
        return
    
    # Check for server admin password header
    password_hash = request.headers.get("x-server-admin-password")
    debug = log_enabled("remote_vaults")
    if debug:
        print(f"[verify_server_admin] Received hash: {password_hash[:16] + '...' if password_hash else 'None'}")
        if SERVER_ADMIN_PASSWORD:
            expected = hashlib.sha256(SERVER_ADMIN_PASSWORD.encode()).hexdigest()
            print(f"[verify_server_admin] Expected hash: {expected[:16]}...")
            print(f"[verify_server_admin] Match: {password_hash == expected if password_hash else False}")
    
    if not password_hash or not _verify_server_admin_password(password_hash):
        raise HTTPException(
            status_code=403,
            detail="Server admin password required for vault operations"
        )


async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Optional[AuthModels.UserInfo]:
    """Dependency to get current authenticated user."""
    if not AUTH_ENABLED:
        return AuthModels.UserInfo(username="admin", is_admin=True, can_write=True, role="admin", perm="read_write")

    local_token = _LOCAL_UI_TOKEN or os.getenv("ZIMX_LOCAL_UI_TOKEN")
    token_header = request.headers.get("x-local-ui-token")
    local_bypass = bool(local_token) and token_header == local_token
    if request.client and request.client.host in _LOCAL_HOSTS and local_bypass:
        return AuthModels.UserInfo(username="admin", is_admin=True, can_write=True, role="admin", perm="read_write")

    if not credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise HTTPException(status_code=401, detail="Invalid token")
        auth_config = _get_auth_config()
        user_record = _get_user_record(auth_config, username) if auth_config else None
        if user_record:
            role = _normalize_role(user_record.get("role"))
            perm = _normalize_perm(user_record.get("perm"), role)
            can_write = role == "admin" or perm == "read_write"
            return AuthModels.UserInfo(
                username=username,
                is_admin=role == "admin",
                can_write=can_write,
                role=role,
                perm=perm,
            )
        return AuthModels.UserInfo(username=username, is_admin=True, can_write=True, role="admin", perm="read_write")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")


def require_admin_user(user: AuthModels.UserInfo = Depends(get_current_user)) -> AuthModels.UserInfo:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def require_write_user(user: AuthModels.UserInfo = Depends(get_current_user)) -> AuthModels.UserInfo:
    if not user.can_write:
        raise HTTPException(status_code=403, detail="User does not have write permission")
    return user


def _filter_out_journal(tree: list[dict]) -> list[dict]:
    """Remove Journal folder/page from the top-level navigation tree."""
    filtered: list[dict] = []
    for node in tree:
        if node.get("name") == "Journal" or node.get("path") == "/Journal":
            continue
        if node.get("path") == "/":
            children = []
            for child in node.get("children") or []:
                if child.get("name") == "Journal" or child.get("path") == "/Journal":
                    continue
                children.append(child)
            node = {**node, "children": children}
        filtered.append(node)
    return filtered


def _should_use_local_file_ops(request: Request) -> bool:
    if not _LOCAL_FILE_OPS_ENABLED:
        return False
    client = request.client
    if not client:
        return False
    return client.host in _LOCAL_HOSTS


def _clear_task_cache() -> None:
    global _TASK_CACHE_VERSION
    _TASKS_CACHE.clear()
    _TASKS_STALE_CACHE.clear()
    with _TASKS_INFLIGHT_LOCK:
        _TASKS_INFLIGHT.clear()
    _TASK_CACHE_VERSION = -1


def _vault_needs_task_index(root: Path) -> bool:
    db_path = config._vault_db_path()
    if not db_path or not db_path.exists():
        return True
    try:
        conn = sqlite3.connect(db_path, check_same_thread=False)
        try:
            pages = conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
            tasks = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.OperationalError:
        return True
    if pages == 0:
        return True
    return tasks == 0


def _start_reindex_job(root: Path, rebuild_search: bool) -> str:
    job_id = str(uuid.uuid4())
    with _REINDEX_LOCK:
        _REINDEX_JOBS[job_id] = {
            "status": "queued",
            "progress": 0,
            "message": "Starting reindex...",
            "total": 0,
            "current": 0,
        }
    thread = threading.Thread(target=_do_reindex_vault, args=(job_id, root, rebuild_search), daemon=True)
    thread.start()
    return job_id


def _maybe_auto_reindex_for_task_error(root: Path, exc: BaseException) -> Optional[str]:
    """Attempt one background reindex when task queries fail due to DB/schema issues."""
    key = str(root)
    now = time.monotonic()
    cooldown_until = _TASK_AUTO_REINDEX_UNTIL.get(key, 0.0)
    if now < cooldown_until:
        return None
    _TASK_AUTO_REINDEX_UNTIL[key] = now + 60.0
    try:
        job_id = _start_reindex_job(root, rebuild_search=False)
        print(
            f"[API] /api/tasks auto-reindex started job_id={job_id} "
            f"root={root} reason={exc.__class__.__name__}: {exc}"
        )
        return job_id
    except Exception:
        return None


def _update_task_line_dates(
    line: str,
    *,
    start_value: Optional[str],
    due_value: Optional[str],
    apply_start: bool,
    apply_due: bool,
    clear_start: bool,
    clear_due: bool,
) -> str:
    newline = "\n" if line.endswith("\n") else ""
    base = line.rstrip("\n")
    existing_start = None
    existing_due = None
    start_match = _TASK_START_PATTERN.search(base)
    if start_match:
        existing_start = start_match.group(1)
    due_match = _TASK_DUE_PATTERN.search(base)
    if due_match:
        existing_due = due_match.group(1)
    final_start = existing_start
    final_due = existing_due
    if apply_start or clear_start:
        final_start = None if clear_start else start_value
    if apply_due or clear_due:
        final_due = None if clear_due else due_value
    cleaned = _TASK_DATE_PATTERN.sub("", base).rstrip()
    if final_start:
        cleaned += f" >{final_start}"
    if final_due:
        cleaned += f" <{final_due}"
    return cleaned + newline


def _normalize_tags(raw_tags: Optional[List[str]]) -> tuple[str, ...]:
    if not raw_tags:
        return ()
    seen: list[str] = []
    for raw in raw_tags:
        for chunk in raw.split(","):
            tag = chunk.strip()
            if tag and tag not in seen:
                seen.append(tag)
    return tuple(seen)


def _normalize_status(status: Optional[str]) -> Optional[str]:
    if status is None:
        return None
    normalized = status.strip().lower()
    if normalized in ("todo", "done"):
        return normalized
    if normalized == "all" or normalized == "":
        return None
    raise HTTPException(status_code=400, detail="Status must be one of: todo, done, all")


def _fetch_tasks(
    query: str,
    tags: tuple[str, ...],
    *,
    include_done: bool,
    include_ancestors: bool,
    actionable_only: bool,
    status: Optional[str],
) -> list[dict]:
    global _TASK_CACHE_VERSION
    current_version = config.get_task_index_version()
    if _TASK_CACHE_VERSION != current_version:
        _clear_task_cache()
        _TASK_CACHE_VERSION = current_version
    cache_key = (query, tags, include_done, include_ancestors, actionable_only, status)
    if cache_key in _TASKS_CACHE:
        cached = _TASKS_CACHE[cache_key]
        _TASKS_STALE_CACHE[cache_key] = (time.monotonic(), cached)
        return cached
    tasks_from_db = config.fetch_tasks(
        query=query,
        tags=tags,
        include_done=include_done,
        include_ancestors=include_ancestors,
        actionable_only=actionable_only,
    )
    if status == "done":
        tasks_from_db = [task for task in tasks_from_db if (task.get("status") or "").lower() == "done"]
    elif status == "todo":
        tasks_from_db = [task for task in tasks_from_db if (task.get("status") or "").lower() != "done"]
    _TASKS_CACHE[cache_key] = tasks_from_db
    _TASKS_STALE_CACHE[cache_key] = (time.monotonic(), tasks_from_db)
    return tasks_from_db


def _fetch_tasks_with_timeout(
    query: str,
    tags: tuple[str, ...],
    *,
    include_done: bool,
    include_ancestors: bool,
    actionable_only: bool,
    status: Optional[str],
) -> list[dict]:
    cache_key = (query, tags, include_done, include_ancestors, actionable_only, status)
    with _TASKS_INFLIGHT_LOCK:
        future = _TASKS_INFLIGHT.get(cache_key)
        if future is None:
            future = _TASKS_QUERY_EXECUTOR.submit(
                _fetch_tasks,
                query,
                tags,
                include_done=include_done,
                include_ancestors=include_ancestors,
                actionable_only=actionable_only,
                status=status,
            )
            _TASKS_INFLIGHT[cache_key] = future
    try:
        return future.result(timeout=_TASKS_QUERY_TIMEOUT_S)
    except concurrent.futures.TimeoutError as exc:
        if _TASKS_ALLOW_STALE_ON_TIMEOUT:
            stale_entry = _TASKS_STALE_CACHE.get(cache_key)
            if stale_entry:
                fetched_at, stale_items = stale_entry
                if (time.monotonic() - fetched_at) <= _TASKS_STALE_MAX_AGE_S:
                    print(
                        f"[API] /api/tasks timeout after {_TASKS_QUERY_TIMEOUT_S:.1f}s; "
                        f"returning stale cache ({len(stale_items)} items)"
                    )
                    return stale_items
        if _TASKS_ALLOW_DEGRADED_FALLBACK:
            fallback_timeout = min(_TASKS_FALLBACK_TIMEOUT_S, _TASKS_QUERY_TIMEOUT_S)
            fallback_variants: list[tuple[bool, bool, str]] = []
            if include_ancestors:
                fallback_variants.append((False, actionable_only, "include_ancestors=false"))
            if actionable_only:
                fallback_variants.append((False, False, "include_ancestors=false actionable_only=false"))
            for fallback_include_ancestors, fallback_actionable_only, fallback_label in fallback_variants:
                fallback_key = (
                    query,
                    tags,
                    include_done,
                    fallback_include_ancestors,
                    fallback_actionable_only,
                    status,
                )
                if fallback_key == cache_key:
                    continue
                try:
                    fallback_future = _TASKS_QUERY_EXECUTOR.submit(
                        _fetch_tasks,
                        query,
                        tags,
                        include_done=include_done,
                        include_ancestors=fallback_include_ancestors,
                        actionable_only=fallback_actionable_only,
                        status=status,
                    )
                    fallback_items = fallback_future.result(timeout=fallback_timeout)
                    print(
                        f"[API] /api/tasks timeout after {_TASKS_QUERY_TIMEOUT_S:.1f}s; "
                        f"using degraded fallback ({fallback_label}) with {len(fallback_items)} items"
                    )
                    return fallback_items
                except concurrent.futures.TimeoutError:
                    continue
                except sqlite3.OperationalError:
                    continue
        # Leave the worker running; respond quickly so remote clients don't hang UI.
        raise HTTPException(
            status_code=504,
            detail=f"Task query exceeded {_TASKS_QUERY_TIMEOUT_S:.1f}s timeout",
        ) from exc
    except sqlite3.OperationalError as exc:
        raise HTTPException(status_code=503, detail=f"Task query unavailable: {exc}") from exc
    finally:
        if future.done():
            with _TASKS_INFLIGHT_LOCK:
                existing = _TASKS_INFLIGHT.get(cache_key)
                if existing is future:
                    _TASKS_INFLIGHT.pop(cache_key, None)


def _serialize_task(task: dict) -> dict:
    status = (task.get("status") or "todo").lower()
    done = status == "done"
    return {
        "id": task.get("id"),
        "path": task.get("path"),
        "line": task.get("line"),
        "text": task.get("text") or "",
        "status": status,
        "done": done,
        "priority": task.get("priority") or 0,
        "due": task.get("due"),
        "starts": task.get("starts"),
        "parent": task.get("parent"),
        "level": task.get("level") or 0,
        "tags": task.get("tags") or [],
        "actionable": task.get("actionable", not done),
    }


class FilePathPayload(BaseModel):
    path: str = Field(..., description="Vault-relative path beginning with /")


class FileWritePayload(FilePathPayload):
    content: str


class ExcalidrawSavePayload(FilePathPayload):
    scene: dict


class ExcalidrawPreviewPayload(FilePathPayload):
    png_base64: str


class ExcalidrawAiRewritePayload(FilePathPayload):
    prompt: str
    scene: dict
    server: Optional[str] = None
    model: Optional[str] = None


class ExcalidrawSummaryPayload(FilePathPayload):
    scene: dict
    server: Optional[str] = None
    model: Optional[str] = None


class ExcalidrawChatPayload(FilePathPayload):
    prompt: str
    history: list[dict] = Field(default_factory=list)
    server: Optional[str] = None
    model: Optional[str] = None


class ExcalidrawOpenPagePayload(FilePathPayload):
    source_path: Optional[str] = None


class JournalPayload(BaseModel):
    template: Optional[str] = None


class QuickCapturePayload(BaseModel):
    vault_path: str
    page_mode: Literal["today", "custom"] = "today"
    page_ref: Optional[str] = None
    text: str
    triage: Optional[bool] = None


class VaultSelectPayload(BaseModel):
    path: str


class VaultCreatePayload(BaseModel):
    name: str = Field(..., min_length=1)
    auth_username: Optional[str] = None
    auth_password: Optional[str] = None


class CreatePathPayload(BaseModel):
    path: str
    is_dir: bool = False
    content: Optional[str] = ""


class DeletePathPayload(BaseModel):
    path: str


class FileDeletePayload(BaseModel):
    path: str
    version: Optional[int] = None


class RenameMovePayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    from_path: str = Field(..., alias="from")
    to_path: str = Field(..., alias="to")
    version: Optional[int] = None
    rewrite_links: bool = True  # Default to true for backwards compatibility


class UpdateLinksPayload(BaseModel):
    path_map: dict[str, str]


class ReorganizationOperationPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    from_path: str = Field(..., alias="from")
    destination_parent: str
    new_name: str
    operation_type: str = "move"


class ReorganizationPlanPayload(BaseModel):
    operations: List[ReorganizationOperationPayload]
    tree_version: Optional[int] = None
    plan_token: Optional[str] = None


class ReorderPayload(BaseModel):
    parent_path: str
    page_order: List[str]


class ModifiedRangePayload(BaseModel):
    start_date: str
    end_date: str


class ActivityRangePayload(BaseModel):
    start_date: str
    end_date: str
    mode: Literal["edited", "created", "both"] = "both"


class TaskDateTargetPayload(BaseModel):
    path: str
    line: int


class TaskDateUpdatePayload(BaseModel):
    targets: List[TaskDateTargetPayload]
    start_value: Optional[str] = None
    due_value: Optional[str] = None
    apply_start: bool = True
    apply_due: bool = True
    clear_start: bool = False
    clear_due: bool = False


class TaskMutationTargetPayload(BaseModel):
    path: str
    line: int
    expected_text: str
    expected_status: Literal["todo", "done"] = "todo"


class TaskMutationPayload(BaseModel):
    targets: List[TaskMutationTargetPayload]
    text: Optional[str] = None
    status: Optional[Literal["todo", "done"]] = None
    priority: Optional[int] = Field(default=None, ge=0, le=3)
    tags: Optional[List[str]] = None
    start: Optional[str] = None
    due: Optional[str] = None
    destination: Optional[str] = None
    delete: bool = False


class TriageProcessPayload(BaseModel):
    path: str
    item_id: str
    expected_hash: str
    action: Literal["task", "file", "note", "delete"]
    text: str = ""
    destination: Optional[str] = None
    priority: int = Field(default=0, ge=0, le=3)
    tags: List[str] = Field(default_factory=list)
    start: str = ""
    due: str = ""


class QuickCaptureProcessPayload(BaseModel):
    path: str
    start_line: int
    expected_hash: str
    action: Literal["task", "move"]
    destination: str
    text: str = ""
    status: Literal["todo", "done"] = "todo"
    priority: int = Field(default=0, ge=0, le=3)
    tags: List[str] = Field(default_factory=list)
    start: str = ""
    due: str = ""


class AttachmentDeletePayload(BaseModel):
    paths: List[str] = Field(..., description="Vault-relative attachment paths to delete")


class VectorAddPayload(BaseModel):
    page_ref: str
    text: str
    kind: Literal["page", "attachment"] = "page"
    attachment_name: Optional[str] = None


class VectorRemovePayload(BaseModel):
    page_ref: str
    kind: Literal["page", "attachment"] = "page"
    attachment_name: Optional[str] = None


class VectorQueryPayload(BaseModel):
    query_text: str
    kind: Literal["page", "attachment"] = "page"
    page_refs: Optional[List[str]] = None
    attachment_names: Optional[List[str]] = None
    limit: int = 4


class ChatPayload(BaseModel):
    messages: List[dict]
    max_tokens: Optional[int] = None
    temperature: Optional[float] = 0.2


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan event handler for startup checks."""
    _raise_for_homebase_permission_faults(_ensure_vaults_root())
    # Startup: Verify SERVER_ADMIN_PASSWORD is set unless started by embedded app
    # If started by sp.app.main, it will have set SERVER_ADMIN_PASSWORD
    # If started standalone without password, fail unless STILLPOINT_INSECURE is set
    if not SERVER_ADMIN_PASSWORD:
        if os.getenv("STILLPOINT_INSECURE"):
            # INSECURE mode - show massive warning
            print(f"\n{_ANSI_BLUE}{'🚨' * 40}{_ANSI_RESET}")
            print(f"{_ANSI_BLUE}{'=' * 80}{_ANSI_RESET}")
            print(f"{_ANSI_BLUE}🚨 🚨 🚨  DANGER: RUNNING IN INSECURE MODE  🚨 🚨 🚨{_ANSI_RESET}")
            print(f"{_ANSI_BLUE}{'=' * 80}{_ANSI_RESET}")
            print(f"{_ANSI_BLUE}STILLPOINT_INSECURE=1 is set - SERVER_ADMIN_PASSWORD is disabled!{_ANSI_RESET}")
            print(f"{_ANSI_BLUE}ANYONE can create/list/delete vaults on this server without authentication!{_ANSI_RESET}")
            print(f"{_ANSI_BLUE}This is EXTREMELY DANGEROUS and should NEVER be used in production!{_ANSI_RESET}")
            print(f"{_ANSI_BLUE}Only use this for local development/testing on trusted networks.{_ANSI_RESET}\n")
            print("To secure this server properly:")
            print(f"  1. Unset STILLPOINT_INSECURE")
            print(f"  2. Set SERVER_ADMIN_PASSWORD='your-secure-password'")
            print(f"  3. Restart the server\n")
            print(f"{_ANSI_BLUE}{'=' * 80}{_ANSI_RESET}")
            print(f"{_ANSI_BLUE}{'🚨' * 40}{_ANSI_RESET}\n")
        else:
            # No password and no insecure flag - fail
            sys.stderr.write(f"\n{_ANSI_BLUE}{'=' * 80}{_ANSI_RESET}\n")
            sys.stderr.write(f"{_ANSI_BLUE}⚠️  SECURITY ERROR: SERVER_ADMIN_PASSWORD not set!{_ANSI_RESET}\n")
            sys.stderr.write(f"{_ANSI_BLUE}{'=' * 80}{_ANSI_RESET}\n")
            sys.stderr.write(f"{_ANSI_BLUE}This server requires SERVER_ADMIN_PASSWORD for vault operations.{_ANSI_RESET}\n")
            sys.stderr.write(f"{_ANSI_BLUE}Without it, anyone can create/list vaults on this server.{_ANSI_RESET}\n\n")
            sys.stderr.write("To run standalone, set the password:\n")
            sys.stderr.write(f"  export SERVER_ADMIN_PASSWORD='your-secure-password'\n")
            sys.stderr.write(f"  python -m sp.server.api --host 127.0.0.1 --port 8000\n\n")
            sys.stderr.write("Or if using uvicorn directly:\n")
            sys.stderr.write(f"  export SERVER_ADMIN_PASSWORD='your-secure-password'\n")
            sys.stderr.write(f"  uvicorn sp.server.api:app --host 127.0.0.1 --port 8000\n\n")
            sys.stderr.write("To bypass (NOT RECOMMENDED), set:\n")
            sys.stderr.write(f"  export STILLPOINT_INSECURE=1\n")
            sys.stderr.write(f"  uvicorn sp.server.api:app --host 127.0.0.1 --port 8000\n\n")
            sys.stderr.write(f"{_ANSI_BLUE}{'=' * 80}{_ANSI_RESET}\n\n")
            sys.stderr.write("Server startup FAILED. Set SERVER_ADMIN_PASSWORD or STILLPOINT_INSECURE=1\n")
            sys.stderr.flush()
            raise RuntimeError("SERVER_ADMIN_PASSWORD not set and STILLPOINT_INSECURE not enabled")
    
    yield
    # Shutdown: nothing to clean up


app = FastAPI(title="StillPoint Local API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1",
        "http://localhost",
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "null",
        "https://monarchistic-unretractable-susanna.ngrok-free.dev",
        "https://pwa.stillpoint.info"
    ],
    allow_origin_regex=r"^https?://(127\.0\.0\.1|localhost|192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+|172\.(1[6-9]|2\d|3[0-1])\.\d+\.\d+)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)


@app.middleware("http")
async def bind_vault_context(request: Request, call_next):
    session_id = str(request.headers.get(_REMOTE_CONTEXT_HEADER) or "").strip()
    vault_token = None
    config_token = None
    if session_id:
        root = vault_state.get_session_root(session_id)
        if root is not None:
            vault_token = vault_state.push_context_root(root)
            config_token = config.push_active_vault_context(str(root))
    try:
        return await call_next(request)
    finally:
        if config_token is not None:
            config.reset_active_vault_context(config_token)
        if vault_token is not None:
            vault_state.reset_context_root(vault_token)

homebase_api.register_homebase_routes(
    app,
    ensure_vaults_root=_ensure_vaults_root,
    admin_dependency=verify_server_admin,
)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


# ===== Authentication Endpoints =====

@app.post("/auth/setup", response_model=AuthModels.TokenResponse)
def auth_setup(payload: AuthModels.SetupRequest) -> dict:
    """First-time password setup. Only works when no password is configured."""
    try:
        vault_root = vault_state.get_root()
    except Exception:
        raise HTTPException(status_code=400, detail="No vault selected. Select a vault first.")
    if not vault_root:
        raise HTTPException(status_code=400, detail="No vault selected. Select a vault first.")
    
    auth_config = _get_auth_config()
    if auth_config:
        raise HTTPException(status_code=400, detail="Authentication already configured")
    
    _set_auth_config(payload.username, payload.password)

    # Generate tokens
    access_token = _create_token(
        {"sub": payload.username},
        timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    refresh_token = _create_token(
        {"sub": payload.username, "type": "refresh"},
        timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    )
    
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer"
    }


@app.post("/auth/login", response_model=AuthModels.TokenResponse)
def auth_login(payload: AuthModels.LoginRequest) -> dict:
    """Login with username and password."""
    try:
        vault_root = vault_state.get_root()
    except Exception:
        raise HTTPException(status_code=400, detail="No vault selected")
    if not vault_root:
        raise HTTPException(status_code=400, detail="No vault selected")
    
    auth_config = _get_auth_config()
    if not auth_config:
        raise HTTPException(status_code=400, detail="Authentication not configured. Use /auth/setup first.")
    
    user_record = _get_user_record(auth_config, payload.username)
    if not user_record:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    ok, error_detail = _verify_user_password(user_record, payload.password)
    if not ok:
        raise HTTPException(status_code=401, detail=error_detail or "Invalid credentials")

    user_record["last_login_at"] = _utc_now_iso()
    try:
        _store_auth_config_at_path(vault_root / ".stillpoint" / "settings.db", auth_config)
    except Exception:
        pass
    
    # Generate tokens
    access_token = _create_token(
        {"sub": payload.username},
        timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    refresh_token = _create_token(
        {"sub": payload.username, "type": "refresh"},
        timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    )
    
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer"
    }


@app.post("/auth/change", response_model=AuthModels.TokenResponse)
def auth_change(payload: AuthModels.ChangeRequest) -> dict:
    """Change the vault password after validating the old password."""
    try:
        vault_root = vault_state.get_root()
    except Exception:
        raise HTTPException(status_code=400, detail="No vault selected")
    if not vault_root:
        raise HTTPException(status_code=400, detail="No vault selected")

    auth_config = _get_auth_config()
    if not auth_config:
        raise HTTPException(status_code=400, detail="Authentication not configured. Use /auth/setup first.")

    user_record = _get_user_record(auth_config, payload.username)
    if not user_record:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    ok, error_detail = _verify_user_password(user_record, payload.old_password)
    if not ok:
        raise HTTPException(status_code=401, detail=error_detail or "Invalid credentials")

    user_record["password_hash"] = _hash_password(_combined_vault_password(payload.new_password))
    user_record["vault_password_hash"] = _hash_password(payload.new_password)
    user_record["server_password_hash"] = _server_password_hash()
    user_record["last_password_change_at"] = _utc_now_iso()
    try:
        _store_auth_config_at_path(vault_root / ".stillpoint" / "settings.db", auth_config)
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to update password")

    access_token = _create_token(
        {"sub": payload.username},
        timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    refresh_token = _create_token(
        {"sub": payload.username, "type": "refresh"},
        timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    )

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer"
    }


@app.post("/auth/refresh", response_model=AuthModels.TokenResponse)
def auth_refresh(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    """Refresh access token using refresh token."""
    if not credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="Invalid refresh token")
        
        username = payload.get("sub")
        if not username:
            raise HTTPException(status_code=401, detail="Invalid token")

        auth_config = _get_auth_config()
        if auth_config and not _get_user_record(auth_config, username):
            raise HTTPException(status_code=401, detail="Invalid token")
        
        # Generate new tokens
        access_token = _create_token(
            {"sub": username},
            timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        )
        refresh_token = _create_token(
            {"sub": username, "type": "refresh"},
            timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
        )
        
        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer"
        }
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid refresh token")


@app.post("/auth/logout")
def auth_logout(user: AuthModels.UserInfo = Depends(get_current_user)) -> dict:
    """Logout (client should discard tokens)."""
    return {"ok": True, "message": "Logged out successfully"}


@app.get("/auth/me", response_model=AuthModels.UserInfo)
def auth_me(user: AuthModels.UserInfo = Depends(get_current_user)) -> dict:
    """Get current user info."""
    return user.model_dump()


@app.get("/auth/status")
def auth_status() -> dict:
    """Check if authentication is configured and enabled."""
    try:
        vault_root = vault_state.get_root()
    except Exception:
        return {"configured": False, "enabled": AUTH_ENABLED, "vault_selected": False}
    if not vault_root:
        return {"configured": False, "enabled": AUTH_ENABLED, "vault_selected": False}
    
    auth_config = _get_auth_config()
    return {
        "configured": auth_config is not None,
        "enabled": AUTH_ENABLED,
        "vault_selected": True
    }


@app.get("/auth/users")
def auth_list_users(user: AuthModels.UserInfo = Depends(require_admin_user)) -> dict:
    auth_config = _get_auth_config()
    if not auth_config:
        raise HTTPException(status_code=400, detail="Authentication not configured")
    users = auth_config.get("users", {})
    if not isinstance(users, dict):
        users = {}
    results: list[dict] = []
    for username, record in users.items():
        if not isinstance(record, dict):
            continue
        role = _normalize_role(record.get("role"))
        perm = _normalize_perm(record.get("perm"), role)
        last_login = record.get("last_login_at")
        results.append(
            {
                "username": username,
                "role": role,
                "perm": perm,
                "logged_in": bool(last_login),
                "last_login_at": last_login,
                "last_password_change_at": record.get("last_password_change_at"),
                "created_at": record.get("created_at"),
            }
        )
    results.sort(key=lambda item: str(item.get("username") or "").lower())
    return {"users": results}


@app.post("/auth/users")
def auth_create_user(payload: AuthModels.UserCreateRequest, user: AuthModels.UserInfo = Depends(require_admin_user)) -> dict:
    auth_config = _get_auth_config()
    if not auth_config:
        raise HTTPException(status_code=400, detail="Authentication not configured")
    username = payload.username.strip()
    users = auth_config.get("users")
    if not isinstance(users, dict):
        users = {}
        auth_config["users"] = users
    if username in users:
        raise HTTPException(status_code=409, detail="User already exists")
    role = _normalize_role(payload.role)
    perm = _normalize_perm(payload.perm, role)
    now = _utc_now_iso()
    users[username] = {
        "username": username,
        "password_hash": _hash_password(_combined_vault_password(payload.password)),
        "vault_password_hash": _hash_password(payload.password),
        "server_password_hash": _server_password_hash(),
        "role": role,
        "perm": perm,
        "created_at": now,
        "last_login_at": None,
        "last_password_change_at": now,
    }
    try:
        vault_root = vault_state.get_root()
        if not vault_root:
            raise HTTPException(status_code=400, detail="No vault selected")
        _store_auth_config_at_path(vault_root / ".stillpoint" / "settings.db", auth_config)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to create user: {exc}") from exc
    return {"ok": True}


@app.patch("/auth/users/{username}")
def auth_update_user(
    username: str,
    payload: AuthModels.UserUpdateRequest,
    user: AuthModels.UserInfo = Depends(require_admin_user),
) -> dict:
    auth_config = _get_auth_config()
    if not auth_config:
        raise HTTPException(status_code=400, detail="Authentication not configured")
    users = auth_config.get("users")
    if not isinstance(users, dict):
        raise HTTPException(status_code=404, detail="User not found")
    record = users.get(username)
    if not isinstance(record, dict):
        raise HTTPException(status_code=404, detail="User not found")
    new_username = str(payload.username or "").strip()
    if new_username and new_username != username:
        if new_username in users:
            raise HTTPException(status_code=409, detail="Username already exists")
        users[new_username] = record
        users.pop(username, None)
        record["username"] = new_username
        username = new_username
    role = _normalize_role(payload.role if payload.role is not None else record.get("role"))
    perm = _normalize_perm(payload.perm if payload.perm is not None else record.get("perm"), role)
    record["role"] = role
    record["perm"] = perm
    if payload.password:
        record["password_hash"] = _hash_password(_combined_vault_password(payload.password))
        record["vault_password_hash"] = _hash_password(payload.password)
        record["server_password_hash"] = _server_password_hash()
        record["last_password_change_at"] = _utc_now_iso()
    try:
        vault_root = vault_state.get_root()
        if not vault_root:
            raise HTTPException(status_code=400, detail="No vault selected")
        _store_auth_config_at_path(vault_root / ".stillpoint" / "settings.db", auth_config)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to update user: {exc}") from exc
    return {"ok": True}


@app.delete("/auth/users/{username}")
def auth_delete_user(username: str, user: AuthModels.UserInfo = Depends(require_admin_user)) -> dict:
    auth_config = _get_auth_config()
    if not auth_config:
        raise HTTPException(status_code=400, detail="Authentication not configured")
    users = auth_config.get("users")
    if not isinstance(users, dict):
        raise HTTPException(status_code=404, detail="User not found")
    if username not in users:
        raise HTTPException(status_code=404, detail="User not found")
    if username == user.username:
        raise HTTPException(status_code=400, detail="Cannot delete the currently logged-in user")
    remaining_admins = [
        name
        for name, record in users.items()
        if name != username and _normalize_role((record or {}).get("role")) == "admin"
    ]
    if not remaining_admins:
        raise HTTPException(status_code=400, detail="Cannot delete the last admin user")
    users.pop(username, None)
    try:
        vault_root = vault_state.get_root()
        if not vault_root:
            raise HTTPException(status_code=400, detail="No vault selected")
        _store_auth_config_at_path(vault_root / ".stillpoint" / "settings.db", auth_config)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to delete user: {exc}") from exc
    return {"ok": True}


@app.post("/auth/print-token")
def auth_print_token(
    payload: AuthModels.PrintTokenRequest,
    user: AuthModels.UserInfo = Depends(get_current_user),
) -> dict:
    """Issue a short-lived token for browser print access."""
    if not AUTH_ENABLED:
        return {"token": None, "expires_in": 0}
    ttl = int(payload.ttl_seconds or 900)
    token = _create_token(
        {"sub": user.username, "scope": "print"},
        timedelta(seconds=ttl),
    )
    return {"token": token, "expires_in": ttl}


@app.get("/api/vaults")
async def list_vaults(request: Request, _admin: None = Depends(verify_server_admin)) -> dict:
    root = _ensure_vaults_root()
    vaults: list[dict[str, str]] = []
    for entry in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if not entry.is_dir():
            continue
        if entry.name.startswith("."):
            continue
        vaults.append({"name": entry.name, "path": str(entry)})
    return {"root": str(root), "vaults": vaults}


@app.post("/api/vaults/create")
async def create_vault(request: Request, payload: VaultCreatePayload, _admin: None = Depends(verify_server_admin)) -> dict:
    root = _ensure_vaults_root()
    name = _normalize_vault_name(payload.name)
    target = root / name
    if target.exists():
        raise HTTPException(status_code=400, detail="Vault already exists")
    try:
        target.mkdir(parents=True)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to create vault: {exc}") from exc
    try:
        _init_vault_db(target)
        if payload.auth_username or payload.auth_password:
            if not payload.auth_username or not payload.auth_password:
                raise HTTPException(status_code=400, detail="Username and password are required to configure auth")
            _set_auth_config_for_path(target, payload.auth_username, payload.auth_password)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to initialize vault: {exc}") from exc
    return {"ok": True, "name": name, "path": str(target)}


@app.post("/api/vault/select")
def select_vault(request: Request, payload: VaultSelectPayload) -> dict:
    try:
        resolved = _resolve_vault_path(payload.path)
        session_id = str(request.headers.get(_REMOTE_CONTEXT_HEADER) or "").strip()
        if session_id:
            root = vault_state.bind_session_root(session_id, str(resolved))
            vault_token = vault_state.push_context_root(root)
            config_token = config.push_active_vault_context(str(root))
        else:
            root = vault_state.set_root(str(resolved))
            vault_token = None
            config_token = None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _clear_tree_cache()
    try:
        if not session_id:
            config.set_active_vault(str(root))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to initialize vault: {exc}") from exc
    try:
        _clear_task_cache()
        reindex_job_id = None
        if _vault_needs_task_index(root):
            reindex_job_id = _start_reindex_job(root, rebuild_search=False)
        return {"root": str(root), "reindex_job_id": reindex_job_id}
    finally:
        if config_token is not None:
            config.reset_active_vault_context(config_token)
        if vault_token is not None:
            vault_state.reset_context_root(vault_token)


def _do_reindex_vault(job_id: str, root: Path, rebuild_search: bool) -> None:
    """Background worker to reindex vault."""
    if log_enabled("search_index"):
        print(f"[Reindex] Job {job_id} started: rebuild_search={rebuild_search}")
    
    try:
        with _REINDEX_LOCK:
            if job_id not in _REINDEX_JOBS:
                return
            _REINDEX_JOBS[job_id]["status"] = "running"
            _REINDEX_JOBS[job_id]["message"] = "Scanning files..."
        
        # Find all pages
        txt_files = []
        for suffix in PAGE_SUFFIXES:
            for page_file in sorted(root.rglob(f"*{suffix}")):
                if page_file.name == "AGENTS.md":
                    continue
                if suffix == LEGACY_SUFFIX and page_file.with_suffix(PAGE_SUFFIX).exists():
                    continue
                txt_files.append(page_file)
        
        total = len(txt_files)
        if log_enabled("search_index"):
            print(f"[Reindex] Job {job_id}: found {total} files")
        with _REINDEX_LOCK:
            _REINDEX_JOBS[job_id]["total"] = total
            _REINDEX_JOBS[job_id]["current"] = 0
            _REINDEX_JOBS[job_id]["message"] = f"Indexing {total} pages..."
        
        # Index pages
        for idx, txt_file in enumerate(txt_files, start=1):
            rel_path = txt_file.relative_to(root)
            path_str = f"/{rel_path.as_posix()}"
            try:
                content = txt_file.read_text(encoding="utf-8")
                app_indexer.index_page(path_str, content)
            except Exception:
                pass
            
            with _REINDEX_LOCK:
                _REINDEX_JOBS[job_id]["current"] = idx
                _REINDEX_JOBS[job_id]["progress"] = int((idx / total) * (50 if rebuild_search else 100))
        
        # Rebuild search index if requested
        if rebuild_search:
            if log_enabled("search_index"):
                print(f"[Reindex] Job {job_id}: starting search index rebuild")
            with _REINDEX_LOCK:
                _REINDEX_JOBS[job_id]["message"] = "Rebuilding search index..."
            
            try:
                db_path = config._vault_db_path()
                if db_path:
                    if log_enabled("search_index"):
                        print(f"[Reindex] Job {job_id}: db_path={db_path}")
                    conn = sqlite3.connect(db_path, check_same_thread=False)
                    try:
                        conn.execute(
                            """
                            CREATE TABLE IF NOT EXISTS pages_search_index (
                                id INTEGER PRIMARY KEY,
                                path TEXT NOT NULL UNIQUE,
                                mtime INTEGER NOT NULL
                            )
                            """
                        )
                        conn.execute("CREATE INDEX IF NOT EXISTS idx_pages_search_path ON pages_search_index(path)")
                        
                        # Try to create FTS table if it doesn't exist
                        try:
                            conn.execute(
                                "CREATE VIRTUAL TABLE IF NOT EXISTS pages_search_fts USING fts5(content, content_rowid='id')"
                            )
                        except sqlite3.OperationalError:
                            pass
                        
                        # Clear existing search index
                        try:
                            conn.execute("DELETE FROM pages_search_fts")
                        except sqlite3.OperationalError:
                            # FTS table might not exist, ignore
                            pass
                        conn.execute("DELETE FROM pages_search_index")
                        conn.commit()
                        
                        for idx, txt_file in enumerate(txt_files, start=1):
                            rel_path = txt_file.relative_to(root)
                            path_str = f"/{rel_path.as_posix()}"
                            try:
                                content = txt_file.read_text(encoding="utf-8")
                                mtime = int(txt_file.stat().st_mtime)
                                search_index.upsert_page(conn, path_str, mtime, content)
                            except Exception:
                                pass
                            
                            with _REINDEX_LOCK:
                                _REINDEX_JOBS[job_id]["progress"] = 50 + int((idx / total) * 50)
                        
                        conn.commit()
                    finally:
                        conn.close()
                    if log_enabled("search_index"):
                        print(f"[Reindex] Job {job_id}: search index complete")
            except Exception as search_exc:
                if log_enabled("search_index"):
                    print(f"[Reindex] Job {job_id}: search index error: {search_exc}")
                # Don't fail the entire job if search indexing fails
                with _REINDEX_LOCK:
                    _REINDEX_JOBS[job_id]["message"] = f"Main index complete, search index error: {search_exc}"
        
        if log_enabled("search_index"):
            print(f"[Reindex] Job {job_id}: marking as completed")
        with _REINDEX_LOCK:
            _REINDEX_JOBS[job_id]["status"] = "completed"
            _REINDEX_JOBS[job_id]["progress"] = 100
            if rebuild_search and _REINDEX_JOBS[job_id].get("message", "").startswith("Main index complete"):
                # Keep the error message from search indexing
                pass
            else:
                _REINDEX_JOBS[job_id]["message"] = f"Complete: indexed {total} pages"
        if log_enabled("search_index"):
            print(f"[Reindex] Job {job_id}: finished successfully")
    
    except Exception as exc:
        print(f"[Reindex] Job {job_id}: fatal error: {exc}")
        with _REINDEX_LOCK:
            _REINDEX_JOBS[job_id]["status"] = "error"
            _REINDEX_JOBS[job_id]["message"] = str(exc)


class ReindexRequest(BaseModel):
    rebuild_search: bool = False


@app.post("/api/vault/reindex")
def start_reindex(payload: ReindexRequest) -> dict:
    """Start a background reindex job."""
    root = vault_state.get_root()
    if not root:
        raise HTTPException(status_code=400, detail="No vault selected")
    job_id = _start_reindex_job(root, payload.rebuild_search)
    return {"job_id": job_id, "status": "queued"}


@app.get("/api/vault/reindex/status/{job_id}")
def reindex_status(job_id: str) -> dict:
    """Get status of a reindex job."""
    with _REINDEX_LOCK:
        job = _REINDEX_JOBS.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        return {
            "job_id": job_id,
            "status": job["status"],
            "progress": job["progress"],
            "message": job["message"],
            "total": job["total"],
            "current": job["current"],
        }


@app.get("/api/vault/tree")
def vault_tree(path: str = "/", recursive: bool = True, include_journal: bool = False) -> dict:
    root = vault_state.get_root()
    version = config.get_tree_version()
    normalized_path = _normalize_tree_path(path)
    tree = _get_cached_tree(root, normalized_path, recursive, include_journal, version)
    cache_hit = tree is not None
    if not cache_hit:
        try:
            tree = files.list_dir(root, subpath=normalized_path, recursive=recursive)
        except FileNotFoundError as exc:
            _raise_file_http(404, f"List directory failed for {normalized_path}", exc)
        except FileAccessError as exc:
            _raise_file_http(400, f"List directory blocked for {normalized_path}", exc)
        except OSError as exc:
            _raise_file_http(500, f"List directory error for {normalized_path}", exc)
        except Exception as exc:
            _raise_file_http(500, f"List directory error for {normalized_path}", exc)
        if normalized_path in ("/", "") and not include_journal:
            tree = _filter_out_journal(tree)
        order_map = config.fetch_display_order_map()
        if normalized_path == "/":
            _log_api(f"{_ANSI_BLUE}[API] Root order_map sample: {list(order_map.items())[:5]}{_ANSI_RESET}")
        _sort_tree_nodes(tree, order_map)
        if normalized_path == "/" and tree:
            _log_api(f"{_ANSI_BLUE}[API] Root tree order after sort: {[n.get('name') for n in tree[:5]]}{_ANSI_RESET}")
        _set_cached_tree(root, normalized_path, recursive, include_journal, version, tree)
    _log_api(
        f"{_ANSI_BLUE}[API] GET /api/vault/tree path={normalized_path} recursive={recursive} "
        f"version={version} cached={cache_hit}{_ANSI_RESET}"
    )
    return {"root": str(root), "tree": tree, "version": version}


@app.get("/api/vault/tree/expand-path")
def vault_tree_expand_path(target: str, include_journal: bool = False) -> dict:
    """Return tree children for every ancestor segment of *target* in one call.

    This allows the client to pre-populate its local tree cache for lazy
    loading without issuing N sequential ``/api/vault/tree`` requests.
    """
    root = vault_state.get_root()
    version = config.get_tree_version()
    clean = _normalize_tree_path(target)
    parts = [p for p in clean.strip("/").split("/") if p]

    # Build the list of ancestor paths to fetch: /, /A, /A/B, …
    paths_to_fetch: list[str] = ["/"]
    current = "/"
    for part in parts:
        current = f"{current.rstrip('/')}/{part}" if current != "/" else f"/{part}"
        paths_to_fetch.append(current)

    order_map: dict[str, int] | None = None
    segments: dict[str, list[dict]] = {}

    for seg_path in paths_to_fetch:
        norm = _normalize_tree_path(seg_path)
        cached = _get_cached_tree(root, norm, False, include_journal, version)
        if cached is not None:
            children = cached[0].get("children") or [] if cached else []
            segments[norm] = children
            continue
        try:
            tree = files.list_dir(root, subpath=norm, recursive=False)
        except Exception:
            segments[norm] = []
            continue
        if norm in ("/", "") and not include_journal:
            tree = _filter_out_journal(tree)
        if order_map is None:
            order_map = config.fetch_display_order_map()
        _sort_tree_nodes(tree, order_map)
        _set_cached_tree(root, norm, False, include_journal, version, tree)
        children = tree[0].get("children") or [] if tree else []
        segments[norm] = children

    _log_api(
        f"{_ANSI_BLUE}[API] GET /api/vault/tree/expand-path target={clean} "
        f"segments={len(segments)} version={version}{_ANSI_RESET}"
    )
    return {"segments": segments, "version": version}


@app.get("/api/vault/stats")
def vault_stats() -> dict:
    """Get vault statistics including folder count for lazy loading decision."""
    root = vault_state.get_root()
    folder_count = config.count_folders()
    _log_api(f"{_ANSI_BLUE}[API] GET /api/vault/stats folder_count={folder_count}{_ANSI_RESET}")
    return {"folder_count": folder_count}


@app.post("/api/file/read")
def file_read(payload: FilePathPayload) -> dict:
    root = vault_state.get_root()
    file_path = root / payload.path.lstrip("/")
    try:
        content = files.read_file(root, payload.path)
    except FileNotFoundError as exc:
        _raise_file_http(404, f"Read file failed for {payload.path}", exc)
    except FileAccessError as exc:
        _raise_file_http(400, f"Read file blocked for {payload.path}", exc)
    except OSError as exc:
        _raise_file_http(500, f"Read file error for {payload.path}", exc)
    except Exception as exc:
        _raise_file_http(500, f"Read file error for {payload.path}", exc)
    mtime_ns = None
    try:
        mtime_ns = file_path.stat().st_mtime_ns
    except OSError:
        mtime_ns = None
    rev = None
    db_path = config._vault_db_path()
    if db_path:
        conn = sqlite3.connect(db_path, check_same_thread=False)
        try:
            row = conn.execute("SELECT rev FROM pages WHERE path = ?", (payload.path,)).fetchone()
            rev = row[0] if row else 0
        finally:
            conn.close()
    return {"content": content, "rev": rev, "mtime_ns": mtime_ns}


@app.get("/api/file/raw")
def file_raw(path: str) -> FileResponse:
    root = _get_vault_root()
    normalized = _vault_relative_path(path)
    target = (root / normalized.lstrip("/")).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid file path") from exc
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(target)


_EXCALIDRAW_MAX_JSON_BYTES = 20 * 1024 * 1024
_EXCALIDRAW_MAX_PNG_BYTES = 30 * 1024 * 1024
_EXCALIDRAW_AI_MAX_JSON_BYTES = 120 * 1024
_EXCALIDRAW_AI_MAX_ELEMENTS = 250
_EXCALIDRAW_AI_DEBUG_DIR = Path("/tmp") / "stillpoint_excalidraw_ai"
_EXCALIDRAW_ELEMENT_TYPES = {
    "rectangle",
    "diamond",
    "ellipse",
    "arrow",
    "line",
    "freedraw",
    "text",
    "image",
    "frame",
    "magicframe",
    "iframe",
    "embeddable",
}


def _log_excalidraw_ai(message: str) -> None:
    if log_enabled("excalidraw_ai"):
        print(f"[ExcalidrawAI] {message}")


def _write_excalidraw_ai_debug(name: str, payload: object) -> Optional[Path]:
    if not log_enabled("excalidraw_ai"):
        return None
    try:
        _EXCALIDRAW_AI_DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        target = _EXCALIDRAW_AI_DEBUG_DIR / name
        if isinstance(payload, str):
            target.write_text(payload, encoding="utf-8")
        else:
            target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return target
    except OSError as exc:
        _log_excalidraw_ai(f"Failed to write debug artifact {name}: {exc}")
        return None


def _empty_excalidraw_scene() -> dict:
    return {
        "type": "excalidraw",
        "version": 2,
        "source": "stillpoint",
        "elements": [],
        "appState": {},
        "files": {},
    }


def _normalize_ai_server(entry: dict) -> dict:
    entry = entry or {}
    auth_mode = entry.get("auth_mode") or ("proxy" if entry.get("api_secret") else "openai")
    base_url = str(entry.get("base_url") or "").rstrip("/")
    models_path = entry.get("models_path") or ("/mods" if auth_mode == "proxy" else "/v1/models")
    chat_path = entry.get("chat_path") or "/v1/chat/completions"
    default_model = entry.get("default_model") or "gpt-3.5-turbo"
    return {
        "name": entry.get("name") or (base_url or "Server"),
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
    }


def _default_ai_servers() -> list[dict]:
    default_model = "gpt-3.5-turbo"
    return [
        {
            "name": "LM Studio",
            "base_url": os.getenv("LM_STUDIO_URL", "http://localhost:1234"),
            "auth_mode": "proxy",
            "models_path": "/v1/models",
            "chat_path": "/v1/chat/completions",
            "default_model": default_model,
            "verify_ssl": True,
        },
        {
            "name": "OpenAI Compatible (8000)",
            "base_url": "http://localhost:8000",
            "auth_mode": "proxy",
            "models_path": "/v1/models",
            "chat_path": "/v1/chat/completions",
            "default_model": default_model,
            "verify_ssl": True,
        },
        {
            "name": "OpenAI Compatible (8080)",
            "base_url": "http://localhost:8080",
            "auth_mode": "proxy",
            "models_path": "/v1/models",
            "chat_path": "/v1/chat/completions",
            "default_model": default_model,
            "verify_ssl": True,
        },
    ]


def _load_ai_servers() -> list[dict]:
    try:
        payload = config._read_global_config()
        servers = payload.get("servers", [])
    except Exception:
        servers = []
    if not servers:
        servers = _default_ai_servers()
    return [_normalize_ai_server(server) for server in servers if server]


def _get_ai_server(name: Optional[str]) -> Optional[dict]:
    servers = _load_ai_servers()
    if name:
        for server in servers:
            if server.get("name") == name:
                return server
    preferred = config.load_default_ai_server()
    if preferred:
        for server in servers:
            if server.get("name") == preferred:
                return server
    try:
        active = config._read_global_config().get("active_server")
    except Exception:
        active = None
    if active:
        for server in servers:
            if server.get("name") == active:
                return server
    return servers[0] if servers else None


def _available_ai_models(server: Optional[dict]) -> list[str]:
    server = server or {}
    fallback = server.get("default_model") or "gpt-3.5-turbo"
    if not server.get("name"):
        return [fallback]
    try:
        payload = config._read_global_config()
        models = payload.get("server_models", {}).get(server["name"], [])
    except Exception:
        models = []
    cleaned = sorted({str(model) for model in models if model})
    return cleaned or [fallback]


def _load_excalidraw_ai_system_prompt() -> str:
    try:
        prompt = (Path(__file__).resolve().parents[1] / "app" / "excal-prompt.txt").read_text(encoding="utf-8")
    except OSError:
        prompt = "StillPoint diagrams are knowledge artifacts. Improve the diagram while preserving meaning."
    strict = """

You are editing an Excalidraw scene.
Return only one complete valid JSON object for the full updated Excalidraw scene.
Do not return markdown, commentary, explanations, JSON patches, or partial elements.
The JSON object must include: type, version, elements, appState, and files.
Preserve existing files entries unless the user explicitly asks to remove attached assets.
"""
    return prompt.rstrip() + strict


def _load_excalidraw_deconstruct_prompt() -> str:
    try:
        return (Path(__file__).resolve().parents[1] / "app" / "excal-deconstruct.txt").read_text(
            encoding="utf-8"
        ).rstrip()
    except OSError:
        return (
            "You are an enterprise architecture analyst. Analyze the Excalidraw scene as architecture "
            "knowledge and return only valid JSON with title, diagram_type, summary, and embedding_summary."
        )


def _normalize_excalidraw_scene(scene: dict) -> dict:
    if not isinstance(scene, dict):
        raise HTTPException(status_code=400, detail="Excalidraw scene must be a JSON object")
    normalized = dict(scene)
    normalized.setdefault("type", "excalidraw")
    normalized.setdefault("version", 2)
    normalized.setdefault("source", "stillpoint")
    elements = normalized.get("elements")
    if elements is None:
        elements = []
    if not isinstance(elements, list):
        raise HTTPException(status_code=400, detail="Excalidraw elements must be a list")
    app_state = normalized.get("appState")
    if app_state is None:
        app_state = {}
    if not isinstance(app_state, dict):
        raise HTTPException(status_code=400, detail="Excalidraw appState must be a JSON object")
    files = normalized.get("files")
    if files is None:
        files = {}
    if not isinstance(files, dict):
        raise HTTPException(status_code=400, detail="Excalidraw files must be a JSON object")
    normalized["elements"] = elements
    normalized["appState"] = app_state
    normalized["files"] = files
    return normalized


def _excalidraw_number(value: object, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return default


def _excalidraw_int(value: object, default: int = 0) -> int:
    return int(round(_excalidraw_number(value, float(default))))


def _excalidraw_string(value: object, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _excalidraw_bool(value: object, default: bool = False) -> bool:
    return value if isinstance(value, bool) else default


def _excalidraw_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _excalidraw_point(value: object, default: Optional[list[float]] = None) -> list[float]:
    fallback = default or [0.0, 0.0]
    if not isinstance(value, list) or len(value) < 2:
        return fallback
    return [_excalidraw_number(value[0]), _excalidraw_number(value[1])]


def _excalidraw_points(value: object, fallback: list[list[float]], *, min_points: int) -> list[list[float]]:
    if not isinstance(value, list):
        return fallback
    points = [_excalidraw_point(point) for point in value if isinstance(point, list)]
    return points if len(points) >= min_points else fallback


def _excalidraw_binding(value: object) -> Optional[dict]:
    if not isinstance(value, dict) or not isinstance(value.get("elementId"), str):
        return None
    binding = {
        "elementId": value["elementId"],
        "focus": _excalidraw_number(value.get("focus")),
        "gap": _excalidraw_number(value.get("gap")),
    }
    if isinstance(value.get("fixedPoint"), list):
        binding["fixedPoint"] = _excalidraw_point(value["fixedPoint"])
    return binding


def _excalidraw_bound_elements(value: object) -> Optional[list[dict]]:
    if value is None:
        return None
    if not isinstance(value, list):
        return None
    cleaned = []
    for item in value:
        if isinstance(item, dict) and isinstance(item.get("id"), str) and isinstance(item.get("type"), str):
            cleaned.append({"id": item["id"], "type": item["type"]})
    return cleaned or None


def _sanitize_excalidraw_element(element: dict, *, now_ms: int, ordinal: int) -> Optional[dict]:
    element_type = _excalidraw_string(element.get("type"), "rectangle")
    if element_type == "selection":
        element_type = "rectangle"
    if element_type not in _EXCALIDRAW_ELEMENT_TYPES:
        _log_excalidraw_ai(f"Dropped unsupported AI element type={element_type!r}")
        return None
    width = _excalidraw_number(element.get("width"), 100.0)
    height = _excalidraw_number(element.get("height"), 60.0)
    sanitized = dict(element)
    sanitized.update(
        {
            "id": _excalidraw_string(element.get("id"), f"ai-{now_ms}-{ordinal}"),
            "type": element_type,
            "x": _excalidraw_number(element.get("x")),
            "y": _excalidraw_number(element.get("y")),
            "width": width,
            "height": height,
            "angle": _excalidraw_number(element.get("angle")),
            "strokeColor": _excalidraw_string(element.get("strokeColor"), "#1e1e1e"),
            "backgroundColor": _excalidraw_string(element.get("backgroundColor"), "transparent"),
            "fillStyle": _excalidraw_string(element.get("fillStyle"), "hachure"),
            "strokeWidth": _excalidraw_number(element.get("strokeWidth"), 2.0),
            "strokeStyle": _excalidraw_string(element.get("strokeStyle"), "solid"),
            "roughness": _excalidraw_number(element.get("roughness"), 1.0),
            "opacity": _excalidraw_number(element.get("opacity"), 100.0),
            "groupIds": _excalidraw_string_list(element.get("groupIds")),
            "frameId": element.get("frameId") if isinstance(element.get("frameId"), str) else None,
            "roundness": element.get("roundness") if isinstance(element.get("roundness"), dict) else None,
            "seed": _excalidraw_int(element.get("seed"), ordinal + 1),
            "version": max(1, _excalidraw_int(element.get("version"), 1)),
            "versionNonce": _excalidraw_int(element.get("versionNonce"), now_ms % 2_147_483_647),
            "isDeleted": _excalidraw_bool(element.get("isDeleted"), False),
            "boundElements": _excalidraw_bound_elements(element.get("boundElements")),
            "updated": _excalidraw_int(element.get("updated"), now_ms),
            "link": element.get("link") if isinstance(element.get("link"), str) else None,
            "locked": _excalidraw_bool(element.get("locked"), False),
            "index": None,
        }
    )
    if element_type in {"line", "arrow"}:
        fallback_points = [[0.0, 0.0], [width if width else 100.0, height if height else 0.0]]
        sanitized["points"] = _excalidraw_points(element.get("points"), fallback_points, min_points=2)
        sanitized["lastCommittedPoint"] = (
            _excalidraw_point(element.get("lastCommittedPoint")) if isinstance(element.get("lastCommittedPoint"), list) else None
        )
        sanitized["startBinding"] = _excalidraw_binding(element.get("startBinding"))
        sanitized["endBinding"] = _excalidraw_binding(element.get("endBinding"))
        sanitized["startArrowhead"] = element.get("startArrowhead") if isinstance(element.get("startArrowhead"), str) else None
        sanitized["endArrowhead"] = element.get("endArrowhead") if isinstance(element.get("endArrowhead"), str) else ("arrow" if element_type == "arrow" else None)
        if element_type == "arrow":
            sanitized["elbowed"] = _excalidraw_bool(element.get("elbowed"), False)
            sanitized.setdefault("fixedSegments", None)
            sanitized.setdefault("startIsSpecial", None)
            sanitized.setdefault("endIsSpecial", None)
    elif element_type == "freedraw":
        sanitized["points"] = _excalidraw_points(element.get("points"), [[0.0, 0.0]], min_points=1)
        pressures = element.get("pressures")
        sanitized["pressures"] = (
            [_excalidraw_number(item) for item in pressures]
            if isinstance(pressures, list)
            else [0.5 for _ in sanitized["points"]]
        )
        sanitized["simulatePressure"] = _excalidraw_bool(element.get("simulatePressure"), True)
        sanitized["lastCommittedPoint"] = None
    elif element_type == "text":
        text = _excalidraw_string(element.get("text"), _excalidraw_string(element.get("originalText"), ""))
        sanitized["text"] = text
        sanitized["originalText"] = _excalidraw_string(element.get("originalText"), text)
        sanitized["fontSize"] = _excalidraw_number(element.get("fontSize"), 20.0)
        sanitized["fontFamily"] = _excalidraw_int(element.get("fontFamily"), 5)
        sanitized["textAlign"] = _excalidraw_string(element.get("textAlign"), "left")
        sanitized["verticalAlign"] = _excalidraw_string(element.get("verticalAlign"), "top")
        sanitized["containerId"] = element.get("containerId") if isinstance(element.get("containerId"), str) else None
        sanitized["autoResize"] = _excalidraw_bool(element.get("autoResize"), True)
        sanitized["lineHeight"] = _excalidraw_number(element.get("lineHeight"), 1.25)
    elif element_type == "image":
        sanitized["fileId"] = element.get("fileId") if isinstance(element.get("fileId"), str) else None
        sanitized["status"] = element.get("status") if element.get("status") in {"pending", "saved", "error"} else "saved"
        scale = element.get("scale")
        sanitized["scale"] = [_excalidraw_number(scale[0], 1.0), _excalidraw_number(scale[1], 1.0)] if isinstance(scale, list) and len(scale) >= 2 else [1.0, 1.0]
        sanitized["crop"] = element.get("crop") if isinstance(element.get("crop"), dict) else None
    elif element_type in {"frame", "magicframe"}:
        sanitized["name"] = element.get("name") if isinstance(element.get("name"), str) else None
    return sanitized


def _sanitize_excalidraw_ai_scene(scene: dict) -> dict:
    normalized = _normalize_excalidraw_scene(scene)
    sanitized_elements = []
    now_ms = int(time.time() * 1000)
    for ordinal, element in enumerate(normalized.get("elements", [])):
        if not isinstance(element, dict):
            _log_excalidraw_ai("Dropped non-object AI element")
            continue
        next_element = _sanitize_excalidraw_element(element, now_ms=now_ms, ordinal=ordinal)
        if next_element is not None:
            sanitized_elements.append(next_element)
    normalized["elements"] = sanitized_elements
    return normalized


def _excalidraw_ai_scene_size(scene: dict) -> tuple[int, int]:
    normalized = _normalize_excalidraw_scene(scene)
    encoded = json.dumps(normalized, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return len(encoded), len(normalized.get("elements", []))


def _strip_json_fence(content: str) -> str:
    text = (content or "").strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        return fenced.group(1).strip()
    return text.strip()


def _parse_excalidraw_ai_scene(content: str) -> dict:
    text = _strip_json_fence(content)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail=f"AI did not return valid Excalidraw JSON: {exc}") from exc
    if isinstance(parsed, dict) and isinstance(parsed.get("scene"), dict):
        parsed = parsed["scene"]
    return _sanitize_excalidraw_ai_scene(parsed)


def _parse_excalidraw_summary(content: str) -> dict:
    text = _strip_json_fence(content)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail=f"AI did not return valid summary JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=502, detail="AI summary response must be a JSON object")
    return parsed


def _normalize_excalidraw_chat_history(history: object) -> list[dict]:
    if not isinstance(history, list):
        return []
    normalized = []
    for item in history[-12:]:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        if role not in {"user", "assistant"}:
            continue
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        normalized.append({"role": role, "content": content[:6000]})
    return normalized


def _extract_ai_message_content(data: object) -> str:
    if not isinstance(data, dict):
        return ""
    choice = (data.get("choices") or [{}])[0]
    if not isinstance(choice, dict):
        return ""
    message = choice.get("message") or {}
    if isinstance(message, dict):
        return str(message.get("content") or "")
    return ""


def _read_streaming_ai_response(url: str, headers: dict, verify: bool, timeout: httpx.Timeout, payload: dict) -> str:
    full = ""
    with httpx.stream("POST", url, json=payload, headers=headers, timeout=timeout, verify=verify) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if not line:
                continue
            decoded = line.decode("utf-8") if isinstance(line, (bytes, bytearray)) else str(line)
            if not decoded.startswith("data: "):
                continue
            json_data = decoded[len("data: ") :].strip()
            if json_data == "[DONE]":
                break
            try:
                data = json.loads(json_data)
            except json.JSONDecodeError:
                continue
            choice = (data.get("choices") or [{}])[0]
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta") or {}
            if isinstance(delta, dict) and delta.get("content"):
                full += str(delta["content"])
    return full


def _request_excalidraw_ai_content(server: dict, messages: list[dict], model: str) -> str:
    url, headers, verify, timeout, payload = build_api_request(server, messages, model, stream=True)
    _log_excalidraw_ai(
        f"Requesting AI content server={server.get('name', '')!r} model={model!r} "
        f"stream=True url={url}"
    )
    try:
        streamed = _read_streaming_ai_response(url, headers, verify, timeout, payload)
        if streamed:
            _log_excalidraw_ai(f"Received streaming response chars={len(streamed)}")
            return streamed
    except httpx.ReadTimeout as exc:
        read_timeout = getattr(timeout, "read", None)
        suffix = f" after {read_timeout:g}s" if isinstance(read_timeout, (int, float)) else ""
        raise HTTPException(
            status_code=504,
            detail=f"AI server read timed out{suffix} while generating the Excalidraw response.",
        ) from exc
    except httpx.ConnectTimeout as exc:
        raise HTTPException(status_code=504, detail="AI server connection timed out.") from exc
    except httpx.ConnectError as exc:
        raise HTTPException(status_code=502, detail=f"Could not connect to AI server: {exc}") from exc
    except httpx.HTTPStatusError:
        raise
    except httpx.HTTPError:
        # Some OpenAI-compatible servers ignore stream=true; try one normal request below.
        pass

    url, headers, verify, timeout, payload = build_api_request(server, messages, model, stream=False)
    _log_excalidraw_ai(f"Falling back to non-streaming request server={server.get('name', '')!r} model={model!r}")
    try:
        with httpx.Client(timeout=timeout, verify=verify) as client:
            response = client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            content = _extract_ai_message_content(response.json())
            _log_excalidraw_ai(f"Received non-streaming response chars={len(content)}")
            return content
    except httpx.ReadTimeout as exc:
        read_timeout = getattr(timeout, "read", None)
        suffix = f" after {read_timeout:g}s" if isinstance(read_timeout, (int, float)) else ""
        raise HTTPException(
            status_code=504,
            detail=f"AI server read timed out{suffix}. Try a faster model or increase the AI chat read timeout.",
        ) from exc
    except httpx.ConnectTimeout as exc:
        raise HTTPException(status_code=504, detail="AI server connection timed out.") from exc
    except httpx.ConnectError as exc:
        raise HTTPException(status_code=502, detail=f"Could not connect to AI server: {exc}") from exc
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:500] if exc.response is not None else str(exc)
        raise HTTPException(status_code=502, detail=f"AI server returned an error: {detail}") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"AI request failed: {exc}") from exc


def _resolve_excalidraw_path(path: str, *, must_exist: bool) -> tuple[str, Path]:
    root = _get_vault_root().resolve()
    normalized = _vault_relative_path(path)
    if not normalized.lower().endswith(".excalidraw"):
        raise HTTPException(status_code=400, detail="Excalidraw path must end with .excalidraw")
    target = (root / normalized.lstrip("/")).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid Excalidraw path") from exc
    if must_exist and (not target.exists() or not target.is_file()):
        raise HTTPException(status_code=404, detail="Excalidraw file not found")
    return normalized, target


def _excalidraw_summary_path(target: Path) -> Path:
    name = target.name
    if name.lower().endswith(".excalidraw"):
        name = name[: -len(".excalidraw")]
    return target.with_name(f"{name}-summary.json")


def _excalidraw_relative_path(target: Path) -> str:
    return f"/{target.resolve().relative_to(_get_vault_root().resolve()).as_posix()}"


def _load_excalidraw_summary_sidecar(target: Path) -> Optional[dict]:
    summary_path = _excalidraw_summary_path(target)
    if not summary_path.exists() or not summary_path.is_file():
        return None
    try:
        payload = json.loads(summary_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid Excalidraw summary JSON: {exc}") from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read Excalidraw summary: {exc}") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Excalidraw summary must be a JSON object")
    return payload


def _excalidraw_updated_at(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _excalidraw_static_root() -> Path:
    return Path(__file__).resolve().parent / "static" / "excalidraw"


@app.get("/static/app-assets/{asset_path:path}")
def app_static_asset(asset_path: str) -> FileResponse:
    root = (Path(__file__).resolve().parents[1] / "assets").resolve()
    target = (root / asset_path).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid asset path") from exc
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Asset not found")
    return FileResponse(target)


@app.get("/static/excalidraw/{asset_path:path}")
def excalidraw_static(asset_path: str) -> FileResponse:
    root = _excalidraw_static_root().resolve()
    target = (root / asset_path).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid Excalidraw asset path") from exc
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Excalidraw asset not found")
    return FileResponse(target)


@app.get("/excalidraw/edit")
def excalidraw_edit(path: str = Query(...), token: Optional[str] = Query(None)) -> Response:
    normalized, _ = _resolve_excalidraw_path(path, must_exist=False)
    bundle_index = _excalidraw_static_root() / "index.html"
    if bundle_index.exists() and bundle_index.is_file():
        return FileResponse(bundle_index)

    token_json = json.dumps(token or "")
    path_json = json.dumps(normalized)
    html_doc = f"""<!doctype html>
<html>
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>StillPoint Excalidraw</title>
    <style>
      html, body, #app {{ width: 100%; height: 100%; margin: 0; }}
      body {{ font-family: system-ui, sans-serif; background: #f6f7f8; color: #172026; }}
      #app {{ display: grid; grid-template-rows: auto 1fr; }}
      header {{ display: flex; align-items: center; gap: 12px; padding: 10px 12px; background: #10251b; color: #f4fff8; }}
      header strong {{ font-size: 14px; }}
      header span {{ opacity: .75; font-size: 12px; }}
      main {{ display: grid; grid-template-columns: minmax(280px, 380px) 1fr; min-height: 0; }}
      textarea {{ width: 100%; height: 100%; box-sizing: border-box; border: 0; border-right: 1px solid #cfd6dc; padding: 14px; font: 13px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; resize: none; color: #172026; background: #fff; }}
      section {{ min-width: 0; min-height: 0; display: grid; place-items: center; padding: 24px; }}
      .panel {{ max-width: 720px; border: 1px solid #ccd4da; background: white; padding: 20px; }}
      .panel h1 {{ margin: 0 0 8px; font-size: 20px; }}
      .panel p {{ margin: 6px 0; line-height: 1.45; }}
      .actions {{ display: flex; gap: 8px; margin-top: 14px; }}
      button {{ border: 1px solid #9fb0bb; background: #eef3f6; color: #172026; padding: 6px 10px; border-radius: 4px; }}
      button:active {{ transform: translateY(1px); }}
      .error {{ color: #9d1c24; }}
    </style>
  </head>
  <body>
    <div id="app">
      <header><strong>StillPoint Excalidraw</strong><span id="status">Loading...</span></header>
      <main>
        <textarea id="scene" spellcheck="false"></textarea>
        <section>
          <div class="panel">
            <h1>Excalidraw persistence shell</h1>
            <p>This local editor route is wired to the StillPoint vault API.</p>
            <p>The real Excalidraw canvas can replace this shell once the frontend package is bundled.</p>
            <p><strong>Path:</strong> <code id="path"></code></p>
            <div class="actions">
              <button id="save">Save JSON</button>
              <button id="reload">Reload</button>
            </div>
          </div>
        </section>
      </main>
    </div>
    <script>
      const drawingPath = {path_json};
      const localToken = {token_json};
      const statusEl = document.getElementById('status');
      const pathEl = document.getElementById('path');
      const textEl = document.getElementById('scene');
      pathEl.textContent = drawingPath;
      function headers() {{
        const h = {{ 'content-type': 'application/json' }};
        if (localToken) h['x-local-ui-token'] = localToken;
        return h;
      }}
      async function load() {{
        statusEl.textContent = 'Loading...';
        const res = await fetch('/api/excalidraw?path=' + encodeURIComponent(drawingPath), {{ headers: headers() }});
        if (!res.ok) throw new Error(await res.text());
        const payload = await res.json();
        textEl.value = JSON.stringify(payload.scene, null, 2);
        statusEl.textContent = 'Loaded ' + payload.title;
      }}
      async function save() {{
        statusEl.textContent = 'Saving...';
        let scene;
        try {{ scene = JSON.parse(textEl.value); }}
        catch (err) {{ statusEl.innerHTML = '<span class="error">Invalid JSON</span>'; return; }}
        const res = await fetch('/api/excalidraw', {{
          method: 'PUT',
          headers: headers(),
          body: JSON.stringify({{ path: drawingPath, scene }})
        }});
        if (!res.ok) throw new Error(await res.text());
        statusEl.textContent = 'Saved';
      }}
      document.getElementById('save').addEventListener('click', () => save().catch(err => statusEl.textContent = 'Save failed: ' + err.message));
      document.getElementById('reload').addEventListener('click', () => load().catch(err => statusEl.textContent = 'Load failed: ' + err.message));
      load().catch(err => statusEl.textContent = 'Load failed: ' + err.message);
    </script>
  </body>
</html>
"""
    return HTMLResponse(content=html_doc, status_code=200)


@app.get("/api/excalidraw")
def excalidraw_load(path: str = Query(...)) -> dict:
    normalized, target = _resolve_excalidraw_path(path, must_exist=True)
    try:
        if target.stat().st_size > _EXCALIDRAW_MAX_JSON_BYTES:
            raise HTTPException(status_code=413, detail="Excalidraw file is too large")
        raw = target.read_text(encoding="utf-8")
        if not raw.strip():
            raise HTTPException(status_code=400, detail="Excalidraw file is empty")
        scene = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid Excalidraw JSON: {exc}") from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read Excalidraw file: {exc}") from exc
    if not isinstance(scene, dict):
        raise HTTPException(status_code=400, detail="Excalidraw scene must be a JSON object")
    scene = _sanitize_excalidraw_ai_scene(scene)
    return {
        "path": normalized,
        "title": target.name,
        "scene": scene,
        "updated_at": _excalidraw_updated_at(target),
    }


@app.put("/api/excalidraw")
def excalidraw_save(
    payload: ExcalidrawSavePayload,
    user: AuthModels.UserInfo = Depends(require_write_user),
) -> dict:
    normalized, target = _resolve_excalidraw_path(payload.path, must_exist=False)
    scene = payload.scene
    if not isinstance(scene, dict):
        raise HTTPException(status_code=400, detail="Excalidraw scene must be a JSON object")
    scene = _sanitize_excalidraw_ai_scene(scene)
    encoded = json.dumps(scene, ensure_ascii=False, indent=2).encode("utf-8")
    if len(encoded) > _EXCALIDRAW_MAX_JSON_BYTES:
        raise HTTPException(status_code=413, detail="Excalidraw scene is too large")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(encoded + b"\n")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to save Excalidraw file: {exc}") from exc
    return {"ok": True, "path": normalized, "updated_at": _excalidraw_updated_at(target)}


@app.put("/api/excalidraw/preview")
def excalidraw_save_preview(
    payload: ExcalidrawPreviewPayload,
    user: AuthModels.UserInfo = Depends(require_write_user),
) -> dict:
    normalized, target = _resolve_excalidraw_path(payload.path, must_exist=False)
    try:
        data = base64.b64decode(payload.png_base64.split(",", 1)[-1], validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Invalid PNG base64") from exc
    if len(data) > _EXCALIDRAW_MAX_PNG_BYTES:
        raise HTTPException(status_code=413, detail="Excalidraw preview PNG is too large")
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise HTTPException(status_code=400, detail="Preview data must be a PNG")
    preview_path = target.with_name(f"{target.name}.png")
    try:
        preview_path.parent.mkdir(parents=True, exist_ok=True)
        preview_path.write_bytes(data)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to save Excalidraw preview: {exc}") from exc
    preview_rel = f"/{preview_path.resolve().relative_to(_get_vault_root().resolve()).as_posix()}"
    return {"ok": True, "preview_path": preview_rel}


@app.get("/api/excalidraw/ai/config")
def excalidraw_ai_config(user: AuthModels.UserInfo = Depends(get_current_user)) -> dict:
    enabled = config.load_enable_ai_chats()
    servers = _load_ai_servers() if enabled else []
    selected = _get_ai_server(None) if enabled else None
    preferred_model = config.load_default_ai_model()
    server_payload = []
    for server in servers:
        models = _available_ai_models(server)
        selected_model = preferred_model if preferred_model in models else server.get("default_model")
        if selected_model not in models:
            selected_model = models[0] if models else ""
        server_payload.append(
            {
                "name": server.get("name", ""),
                "models": models,
                "default_model": selected_model,
            }
        )
    default_server = selected.get("name") if selected else ""
    default_models = _available_ai_models(selected) if selected else []
    default_model = preferred_model if preferred_model in default_models else (selected or {}).get("default_model", "")
    if default_model not in default_models and default_models:
        default_model = default_models[0]
    return {
        "enabled": enabled,
        "max_json_bytes": _EXCALIDRAW_AI_MAX_JSON_BYTES,
        "max_elements": _EXCALIDRAW_AI_MAX_ELEMENTS,
        "servers": server_payload,
        "default_server": default_server,
        "default_model": default_model,
    }


@app.post("/api/excalidraw/open-page")
def excalidraw_request_open_page(
    payload: ExcalidrawOpenPagePayload,
    user: AuthModels.UserInfo = Depends(get_current_user),
) -> dict:
    normalized = _vault_relative_path(payload.path)
    root = _get_vault_root().resolve()
    target = (root / normalized.lstrip("/")).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid page path") from exc
    if not target.exists():
        candidates = [target.with_suffix(suffix) for suffix in PAGE_SUFFIXES]
        if not any(candidate.exists() for candidate in candidates):
            raise HTTPException(status_code=404, detail="Page not found")
    with _EXCALIDRAW_OPEN_PAGE_LOCK:
        _EXCALIDRAW_OPEN_PAGE_REQUESTS.append(normalized)
        del _EXCALIDRAW_OPEN_PAGE_REQUESTS[:-20]
    return {"ok": True, "path": normalized}


@app.get("/api/excalidraw/open-page/next")
def excalidraw_next_open_page_request(
    user: AuthModels.UserInfo = Depends(get_current_user),
) -> dict:
    with _EXCALIDRAW_OPEN_PAGE_LOCK:
        path = _EXCALIDRAW_OPEN_PAGE_REQUESTS.pop(0) if _EXCALIDRAW_OPEN_PAGE_REQUESTS else None
    return {"path": path}


def _select_excalidraw_ai_server_model(server_name: Optional[str], model_name: Optional[str]) -> tuple[dict, str]:
    server = _get_ai_server(server_name)
    if not server:
        raise HTTPException(status_code=400, detail="No AI server configured")
    models = _available_ai_models(server)
    model = (model_name or "").strip() or config.load_default_ai_model() or server.get("default_model") or ""
    if not model:
        model = models[0] if models else "gpt-3.5-turbo"
    return server, model


@app.get("/api/excalidraw/summary")
def excalidraw_summary_load(
    path: str = Query(...),
    user: AuthModels.UserInfo = Depends(get_current_user),
) -> dict:
    normalized, target = _resolve_excalidraw_path(path, must_exist=True)
    summary_path = _excalidraw_summary_path(target)
    summary = _load_excalidraw_summary_sidecar(target)
    payload = {
        "exists": summary is not None,
        "path": normalized,
        "summary_path": _excalidraw_relative_path(summary_path),
        "source_updated_at": _excalidraw_updated_at(target),
    }
    if summary is not None:
        payload.update(
            {
                "summary": summary,
                "updated_at": _excalidraw_updated_at(summary_path),
            }
        )
    return payload


@app.post("/api/excalidraw/summary")
def excalidraw_summary_generate(
    payload: ExcalidrawSummaryPayload,
    user: AuthModels.UserInfo = Depends(require_write_user),
) -> dict:
    if not config.load_enable_ai_chats():
        raise HTTPException(status_code=403, detail="AI chats are disabled")
    normalized_path, target = _resolve_excalidraw_path(payload.path, must_exist=False)
    scene = _normalize_excalidraw_scene(payload.scene)
    size_bytes, element_count = _excalidraw_ai_scene_size(scene)
    if size_bytes > _EXCALIDRAW_AI_MAX_JSON_BYTES or element_count > _EXCALIDRAW_AI_MAX_ELEMENTS:
        raise HTTPException(
            status_code=413,
            detail=(
                "Diagram is too large to summarize with the current full-scene analyzer. "
                f"Limit is {_EXCALIDRAW_AI_MAX_ELEMENTS} elements and {_EXCALIDRAW_AI_MAX_JSON_BYTES} bytes."
            ),
        )

    server, model = _select_excalidraw_ai_server_model(payload.server, payload.model)
    scene_json = json.dumps(scene, ensure_ascii=False, indent=2)
    messages = [
        {"role": "system", "content": _load_excalidraw_deconstruct_prompt()},
        {
            "role": "user",
            "content": (
                f"File path: {normalized_path}\n\n"
                "Current Excalidraw scene JSON:\n"
                f"{scene_json}"
            ),
        },
    ]
    request_debug_path = _write_excalidraw_ai_debug(
        "last-summary-request.json",
        {
            "path": normalized_path,
            "server": server.get("name", ""),
            "model": model,
            "scene_bytes": size_bytes,
            "element_count": element_count,
            "messages": messages,
        },
    )
    if request_debug_path:
        _log_excalidraw_ai(f"Wrote summary request debug artifact: {request_debug_path}")
    content = _request_excalidraw_ai_content(server, messages, model)
    response_debug_path = _write_excalidraw_ai_debug("last-summary-response.txt", content)
    if response_debug_path:
        _log_excalidraw_ai(f"Wrote raw summary response debug artifact: {response_debug_path}")
    if not content:
        raise HTTPException(status_code=502, detail="AI response did not include message content")
    summary = _parse_excalidraw_summary(content)
    summary_path = _excalidraw_summary_path(target)
    try:
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to save Excalidraw summary: {exc}") from exc
    summary_debug_path = _write_excalidraw_ai_debug("last-summary.json", summary)
    if summary_debug_path:
        _log_excalidraw_ai(f"Wrote parsed summary debug artifact: {summary_debug_path}")
    return {
        "ok": True,
        "exists": True,
        "path": normalized_path,
        "summary_path": _excalidraw_relative_path(summary_path),
        "summary": summary,
        "updated_at": _excalidraw_updated_at(summary_path),
        "source_updated_at": _excalidraw_updated_at(target) if target.exists() else None,
        "server": server.get("name", ""),
        "model": model,
    }


@app.post("/api/excalidraw/chat")
def excalidraw_chat(
    payload: ExcalidrawChatPayload,
    user: AuthModels.UserInfo = Depends(get_current_user),
) -> dict:
    if not config.load_enable_ai_chats():
        raise HTTPException(status_code=403, detail="AI chats are disabled")
    prompt = (payload.prompt or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt is required")
    normalized_path, target = _resolve_excalidraw_path(payload.path, must_exist=True)
    summary = _load_excalidraw_summary_sidecar(target)
    if summary is None:
        raise HTTPException(status_code=404, detail="Diagram summary is missing. Analyze the diagram first.")

    server, model = _select_excalidraw_ai_server_model(payload.server, payload.model)
    summary_json = json.dumps(summary, ensure_ascii=False, indent=2)
    history = _normalize_excalidraw_chat_history(payload.history)
    messages = [
        {
            "role": "system",
            "content": (
                "You are an architecture assistant inside StillPoint. Answer using the provided "
                "Excalidraw architecture summary as context. If the summary does not contain enough "
                "information, say what is missing instead of inventing details."
            ),
        },
        {
            "role": "user",
            "content": (
                f"File path: {normalized_path}\n\n"
                "Excalidraw architecture summary JSON:\n"
                f"{summary_json}"
            ),
        },
    ] + history + [{"role": "user", "content": prompt}]
    request_debug_path = _write_excalidraw_ai_debug(
        "last-chat-request.json",
        {
            "path": normalized_path,
            "summary_path": _excalidraw_relative_path(_excalidraw_summary_path(target)),
            "server": server.get("name", ""),
            "model": model,
            "history_count": len(history),
            "messages": messages,
        },
    )
    if request_debug_path:
        _log_excalidraw_ai(f"Wrote chat request debug artifact: {request_debug_path}")
    content = _request_excalidraw_ai_content(server, messages, model)
    response_debug_path = _write_excalidraw_ai_debug("last-chat-response.txt", content)
    if response_debug_path:
        _log_excalidraw_ai(f"Wrote raw chat response debug artifact: {response_debug_path}")
    if not content:
        raise HTTPException(status_code=502, detail="AI response did not include message content")
    return {
        "ok": True,
        "path": normalized_path,
        "summary_path": _excalidraw_relative_path(_excalidraw_summary_path(target)),
        "reply": content,
        "server": server.get("name", ""),
        "model": model,
    }


@app.post("/api/excalidraw/ai")
def excalidraw_ai_rewrite(
    payload: ExcalidrawAiRewritePayload,
    user: AuthModels.UserInfo = Depends(require_write_user),
) -> dict:
    if not config.load_enable_ai_chats():
        raise HTTPException(status_code=403, detail="AI chats are disabled")
    prompt = (payload.prompt or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt is required")
    normalized_path, _ = _resolve_excalidraw_path(payload.path, must_exist=False)
    scene = _normalize_excalidraw_scene(payload.scene)
    size_bytes, element_count = _excalidraw_ai_scene_size(scene)
    if size_bytes > _EXCALIDRAW_AI_MAX_JSON_BYTES or element_count > _EXCALIDRAW_AI_MAX_ELEMENTS:
        raise HTTPException(
            status_code=413,
            detail=(
                "Diagram is too large for full-scene drawing AI. "
                f"Limit is {_EXCALIDRAW_AI_MAX_ELEMENTS} elements and {_EXCALIDRAW_AI_MAX_JSON_BYTES} bytes."
            ),
        )

    server, model = _select_excalidraw_ai_server_model(payload.server, payload.model)
    scene_json = json.dumps(scene, ensure_ascii=False, indent=2)
    messages = [
        {"role": "system", "content": _load_excalidraw_ai_system_prompt()},
        {
            "role": "user",
            "content": (
                f"File path: {normalized_path}\n\n"
                "Current Excalidraw scene JSON:\n"
                f"{scene_json}\n\n"
                f"User request: {prompt}"
            ),
        },
    ]
    request_debug_path = _write_excalidraw_ai_debug(
        "last-draw-request.json",
        {
            "path": normalized_path,
            "server": server.get("name", ""),
            "model": model,
            "scene_bytes": size_bytes,
            "element_count": element_count,
            "messages": messages,
        },
    )
    if request_debug_path:
        _log_excalidraw_ai(f"Wrote draw request debug artifact: {request_debug_path}")
    content = _request_excalidraw_ai_content(server, messages, model)
    response_debug_path = _write_excalidraw_ai_debug("last-draw-response.txt", content)
    if response_debug_path:
        _log_excalidraw_ai(f"Wrote raw draw response debug artifact: {response_debug_path}")
    if not content:
        raise HTTPException(status_code=502, detail="AI response did not include message content")
    scene = _parse_excalidraw_ai_scene(content)
    scene_debug_path = _write_excalidraw_ai_debug("last-draw-scene.json", scene)
    if scene_debug_path:
        _log_excalidraw_ai(f"Wrote parsed draw scene debug artifact: {scene_debug_path}")
    return {
        "ok": True,
        "path": normalized_path,
        "scene": scene,
        "server": server.get("name", ""),
        "model": model,
    }


@app.get("/excalidraw/poc")
def excalidraw_poc() -> HTMLResponse:
    html = """<!doctype html>
<html>
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>StillPoint Excalidraw POC</title>
    <style>
      :root { color-scheme: light dark; }
      body {
        margin: 0;
        min-height: 100vh;
        display: grid;
        place-items: center;
        font-family: system-ui, sans-serif;
        background: Canvas;
        color: CanvasText;
      }
      main {
        width: min(720px, calc(100vw - 48px));
        border: 1px solid color-mix(in srgb, CanvasText 22%, transparent);
        border-radius: 8px;
        padding: 24px;
      }
      h1 { margin: 0 0 10px; font-size: 24px; }
      p { margin: 0; line-height: 1.45; }
      code { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
    </style>
  </head>
  <body>
    <main>
      <h1>StillPoint Excalidraw POC</h1>
      <p>Local Qt WebEngine smoke page loaded from <code>/excalidraw/poc</code>.</p>
    </main>
  </body>
</html>
"""
    return HTMLResponse(content=html, status_code=200)


@app.get("/print/{path:path}")
async def print_page(
    request: Request,
    path: str,
    mode: Literal["page", "tree"] = "page",
    auto: int = 1,
    depth: Optional[int] = Query(default=None, ge=0, le=20),
    title: Optional[str] = None,
    header: int = 0,
    toc: int = 1,
    toc_title: Optional[str] = None,
    token: Optional[str] = None,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> HTMLResponse:
    await _require_print_user(request, token, credentials)
    root = _get_vault_root()
    if mode == "page":
        page_file = _resolve_page_file_for_print(root, path)
        html_body = _render_single_page_html(
            root,
            page_file,
            token,
            include_toc=bool(toc),
            toc_title=toc_title,
        )
        doc_title = title or page_file.stem
    else:
        tree_root = _resolve_tree_root(root, path)
        html_body, doc_title = _render_tree_html(
            root,
            tree_root,
            depth,
            token,
            title_override=title,
            include_toc=bool(toc),
            toc_title=toc_title,
        )
    html = _render_print_document(
        title=doc_title,
        body_html=html_body,
        auto_print=bool(auto),
        show_header=bool(header),
        path_label=path,
        token=token,
    )
    return HTMLResponse(content=html, status_code=200)


@app.get("/print.css")
async def print_css(
    request: Request,
    token: Optional[str] = None,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Response:
    await _require_print_user(request, token, credentials)
    root = _get_vault_root()
    css = _load_print_css(root)
    return Response(content=css, media_type="text/css")


@app.get("/asset/{path:path}")
async def asset_file(
    request: Request,
    path: str,
    token: Optional[str] = None,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> FileResponse:
    await _require_print_user(request, token, credentials)
    root = _get_vault_root()
    raw = (path or "").strip()
    root_resolved = root.resolve()
    normalized = raw
    if raw.startswith("/"):
        candidate = Path(raw)
        if candidate == root_resolved or root_resolved in candidate.parents:
            normalized = candidate.relative_to(root_resolved).as_posix()
        else:
            raise HTTPException(status_code=400, detail="Invalid file path")
    else:
        root_str = root_resolved.as_posix().lstrip("/")
        if root_str and raw.startswith(root_str + "/"):
            normalized = raw[len(root_str) + 1 :]
    normalized = _vault_relative_path(normalized)
    target = (root / normalized.lstrip("/")).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid file path") from exc
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(target)


@app.post("/api/file/write")
def file_write(
    payload: FileWritePayload,
    if_match: Optional[str] = Header(None),
    user: AuthModels.UserInfo = Depends(require_write_user)
) -> dict:
    root = vault_state.get_root()
    file_path = root / payload.path.lstrip("/")
    
    # Check If-Match header for conflict detection
    if if_match is not None:
        if if_match.startswith("mtime:"):
            try:
                expected_mtime = int(if_match.split(":", 1)[1])
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid If-Match mtime format")
            try:
                current_mtime = file_path.stat().st_mtime_ns
            except OSError:
                current_mtime = 0
            if current_mtime != expected_mtime:
                try:
                    current_content = files.read_file(root, payload.path)
                except FileAccessError:
                    current_content = ""
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": "Conflict",
                        "current_mtime_ns": current_mtime,
                        "current_content": current_content
                    }
                )
        elif if_match.startswith("rev:"):
            if_match = if_match.split(":", 1)[1]
            db_path = config._vault_db_path()
            if db_path:
                conn = sqlite3.connect(db_path, check_same_thread=False)
                try:
                    row = conn.execute(
                        "SELECT rev, title FROM pages WHERE path = ?",
                        (payload.path,)
                    ).fetchone()
                    
                    if row:
                        current_rev = row[0] or 0
                        try:
                            expected_rev = int(if_match)
                        except ValueError:
                            conn.close()
                            raise HTTPException(status_code=400, detail="Invalid If-Match header format")
                        
                        if current_rev != expected_rev:
                            # Conflict: return current state
                            try:
                                current_content = files.read_file(root, payload.path)
                            except FileAccessError:
                                current_content = ""
                            try:
                                current_mtime = file_path.stat().st_mtime_ns
                            except OSError:
                                current_mtime = 0
                            
                            conn.close()
                            raise HTTPException(
                                status_code=409,
                                detail={
                                    "error": "Conflict",
                                    "current_rev": current_rev,
                                    "current_mtime_ns": current_mtime,
                                    "current_content": current_content,
                                    "current_title": row[1]
                                }
                            )
                finally:
                    conn.close()
        else:
            raise HTTPException(status_code=400, detail="Invalid If-Match header format")
    
    try:
        files.write_file(root, payload.path, payload.content)
        try:
            mtime_ns = file_path.stat().st_mtime_ns
        except OSError:
            mtime_ns = None
        try:
            app_indexer.index_page(payload.path, payload.content)
        except Exception:
            pass
        if payload.path.startswith("/Journal/") or Path(payload.path).stem.casefold() == "inbox":
            invalidate_triage_cache(root)
        # Update search index
        db_path = config._vault_db_path()
        if db_path:
            conn = sqlite3.connect(db_path, check_same_thread=False)
            search_index.upsert_page(conn, payload.path, int(time.time()), payload.content)
            conn.close()
            
            # Get new revision
            conn = sqlite3.connect(db_path, check_same_thread=False)
            try:
                row = conn.execute("SELECT rev FROM pages WHERE path = ?", (payload.path,)).fetchone()
                new_rev = row[0] if row else 0
                return {"ok": True, "rev": new_rev, "mtime_ns": mtime_ns}
            finally:
                conn.close()
    except FileAccessError as exc:
        _raise_file_http(400, f"Write file blocked for {payload.path}", exc)
    except FileNotFoundError as exc:
        _raise_file_http(404, f"Write file failed for {payload.path}", exc)
    except OSError as exc:
        _raise_file_http(500, f"Write file error for {payload.path}", exc)
    except Exception as exc:
        _raise_file_http(500, f"Write file error for {payload.path}", exc)
    
    return {"ok": True, "mtime_ns": mtime_ns}


@app.post("/api/files/modified")
def files_modified(payload: ModifiedRangePayload) -> dict:
    try:
        start = Date.fromisoformat(payload.start_date)
        end = Date.fromisoformat(payload.end_date)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid date format: {exc}") from exc
    _log_api(f"{_ANSI_BLUE}[API] POST /api/files/modified {payload.start_date} -> {payload.end_date}{_ANSI_RESET}")
    root = vault_state.get_root()
    try:
        items = files.list_files_modified_between(root, start, end)
    except FileAccessError as exc:
        _raise_file_http(400, f"List modified files blocked for {payload.start_date} -> {payload.end_date}", exc)
    except OSError as exc:
        _raise_file_http(500, f"List modified files error for {payload.start_date} -> {payload.end_date}", exc)
    except Exception as exc:
        _raise_file_http(500, f"List modified files error for {payload.start_date} -> {payload.end_date}", exc)
    return {"items": items}


@app.post("/api/files/activity")
def files_activity(payload: ActivityRangePayload) -> dict:
    try:
        start = Date.fromisoformat(payload.start_date)
        end = Date.fromisoformat(payload.end_date)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid date format: {exc}") from exc
    _log_api(
        f"{_ANSI_BLUE}[API] POST /api/files/activity {payload.start_date} -> {payload.end_date} mode={payload.mode}{_ANSI_RESET}"
    )
    mode = (payload.mode or "both").strip().lower()
    if mode not in {"edited", "created", "both"}:
        mode = "both"
    items: list[dict] = []
    db_ok = False
    db_path = config._vault_db_path()
    if db_path:
        try:
            conn = sqlite3.connect(db_path, check_same_thread=False)
            try:
                rows = conn.execute(
                    """
                    SELECT path, COALESCE(updated, 0), COALESCE(created_at, updated, 0)
                    FROM pages
                    WHERE deleted = 0
                    """
                ).fetchall()
            finally:
                conn.close()
            for path, updated_ts, created_ts in rows:
                try:
                    updated_dt = datetime.fromtimestamp(float(updated_ts or 0))
                    created_dt = datetime.fromtimestamp(float(created_ts or 0))
                except Exception:
                    continue
                updated_in = start <= updated_dt.date() <= end
                created_in = start <= created_dt.date() <= end
                if mode == "edited":
                    include = updated_in
                    event = "updated"
                    event_dt = updated_dt
                elif mode == "created":
                    include = created_in
                    event = "created"
                    event_dt = created_dt
                else:
                    include = updated_in or created_in
                    if updated_in:
                        event = "updated"
                        event_dt = updated_dt
                    else:
                        event = "created"
                        event_dt = created_dt
                if not include:
                    continue
                items.append(
                    {
                        "path": str(path),
                        "modified": updated_dt.isoformat(),
                        "created": created_dt.isoformat(),
                        "event": event,
                        "event_time": event_dt.isoformat(),
                    }
                )
            db_ok = True
        except Exception:
            items = []
            db_ok = False
    if not db_ok:
        root = vault_state.get_root()
        try:
            items = files.list_files_activity_between(root, start, end, mode=mode)
        except FileAccessError as exc:
            _raise_file_http(400, f"List activity files blocked for {payload.start_date} -> {payload.end_date}", exc)
        except OSError as exc:
            _raise_file_http(500, f"List activity files error for {payload.start_date} -> {payload.end_date}", exc)
        except Exception as exc:
            _raise_file_http(500, f"List activity files error for {payload.start_date} -> {payload.end_date}", exc)
    return {"items": items}


@app.post("/api/journal/today")
def journal_today(payload: JournalPayload, user: AuthModels.UserInfo = Depends(require_write_user)) -> dict:
    root = vault_state.get_root()
    # Pass template through so the initial content becomes the user's day template
    try:
        target, created = files.ensure_journal_today(root, template=payload.template)
    except FileAccessError as exc:
        _raise_file_http(400, "Create journal entry blocked", exc)
    except FileNotFoundError as exc:
        _raise_file_http(404, "Create journal entry failed", exc)
    except OSError as exc:
        _raise_file_http(500, "Create journal entry error", exc)
    except Exception as exc:
        _raise_file_http(500, "Create journal entry error", exc)
    rel = f"/{target.relative_to(root).as_posix()}"
    return {"path": rel, "created": created}


@app.post("/api/ui/quick-capture")
def ui_quick_capture(user: AuthModels.UserInfo = Depends(get_current_user)) -> dict:
    if _UI_QUICK_CAPTURE_HOOK is None:
        raise HTTPException(status_code=409, detail="Quick capture UI not available")
    try:
        handled = bool(_UI_QUICK_CAPTURE_HOOK())
    except Exception:
        handled = False
    if not handled:
        raise HTTPException(status_code=409, detail="Quick capture UI not available")
    return {"ok": True}


@app.post("/api/quick-capture")
def quick_capture(
    payload: QuickCapturePayload,
    user: AuthModels.UserInfo = Depends(require_write_user),
) -> dict:
    text = (payload.text or "").strip()
    if not text:
        return {"ok": True, "skipped": True}
    try:
        root = _resolve_vault_path(payload.vault_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not root.exists() or not root.is_dir():
        raise HTTPException(status_code=404, detail="Vault not found")
    _init_vault_db(root)
    if payload.page_mode == "custom":
        if not payload.page_ref:
            raise HTTPException(status_code=400, detail="Custom capture page is required")
        rel_path = _colon_to_page_path(payload.page_ref)
    else:
        try:
            target, _created = files.ensure_journal_today(root, template=None)
        except FileAccessError as exc:
            _raise_file_http(400, "Quick capture journal blocked", exc)
        except FileNotFoundError as exc:
            _raise_file_http(404, "Quick capture journal failed", exc)
        except OSError as exc:
            _raise_file_http(500, "Quick capture journal error", exc)
        except Exception as exc:
            _raise_file_http(500, "Quick capture journal error", exc)
        rel_path = f"/{target.relative_to(root).as_posix()}"
    try:
        content = files.read_file(root, rel_path)
    except FileAccessError as exc:
        _raise_file_http(400, f"Quick capture read blocked for {rel_path}", exc)
    except FileNotFoundError as exc:
        _raise_file_http(404, f"Quick capture read failed for {rel_path}", exc)
    except OSError as exc:
        _raise_file_http(500, f"Quick capture read error for {rel_path}", exc)
    except Exception as exc:
        _raise_file_http(500, f"Quick capture read error for {rel_path}", exc)
    now = datetime.now()
    is_journal = rel_path.startswith("/Journal/")
    if is_journal:
        timestamp = now.strftime("%I:%M %p").lower()
    else:
        timestamp = f"{now:%Y-%m-%d}: {now.strftime('%I:%M%p').lower()}"
    triage_requested = (
        config.load_quick_capture_triage_enabled()
        if payload.triage is None
        else bool(payload.triage)
    )
    triage = triage_requested and is_journal
    capture_id = new_capture_id()
    entry_lines = _build_quick_capture_entry(
        text, timestamp, capture_id=capture_id if triage else None, inbox=triage
    )
    updated = _append_quick_capture_section(content, entry_lines)
    try:
        files.write_file(root, rel_path, updated)
        if triage:
            invalidate_triage_cache(root)
    except FileAccessError as exc:
        _raise_file_http(400, f"Quick capture write blocked for {rel_path}", exc)
    except FileNotFoundError as exc:
        _raise_file_http(404, f"Quick capture write failed for {rel_path}", exc)
    except OSError as exc:
        _raise_file_http(500, f"Quick capture write error for {rel_path}", exc)
    except Exception as exc:
        _raise_file_http(500, f"Quick capture write error for {rel_path}", exc)
    db_path = root / ".stillpoint" / "settings.db"
    try:
        conn = sqlite3.connect(db_path, check_same_thread=False)
        search_index.upsert_page(conn, rel_path, int(time.time()), updated)
        conn.close()
    except Exception:
        pass
    return {"ok": True, "id": capture_id, "path": rel_path}


@app.get("/api/tasks")
def api_tasks(
    query: Optional[str] = None,
    tags: Optional[List[str]] = Query(None),
    status: Optional[str] = None,
    include_done: Optional[bool] = None,
    include_ancestors: bool = False,
    actionable_only: bool = False,
) -> dict:
    root = _get_vault_root()
    normalized_query = (query or "").strip()
    normalized_tags = _normalize_tags(tags)
    normalized_status = _normalize_status(status)
    if normalized_status is not None:
        include_done_effective = normalized_status != "todo"
    elif include_done is None:
        include_done_effective = True
    else:
        include_done_effective = bool(include_done)
    try:
        task_rows = _fetch_tasks_with_timeout(
            normalized_query,
            normalized_tags,
            include_done=include_done_effective,
            include_ancestors=bool(include_ancestors),
            actionable_only=bool(actionable_only),
            status=normalized_status,
        )
    except sqlite3.Error as exc:
        _clear_task_cache()
        job_id = _maybe_auto_reindex_for_task_error(root, exc)
        detail = (
            f"Task index error: {exc}. "
            "Triggered automatic vault reindex."
            if job_id
            else f"Task index error: {exc}."
        )
        raise HTTPException(status_code=503, detail=detail) from exc
    if log_enabled("tasks_calendar"):
        print(
            f"[API] /api/tasks count={len(task_rows)} "
            f"query={normalized_query!r} tags={list(normalized_tags)} "
            f"include_done={include_done_effective} include_ancestors={include_ancestors} "
            f"actionable_only={actionable_only} status={normalized_status}"
        )
    return {"items": [_serialize_task(task) for task in task_rows]}


@app.post("/api/tasks/mutate")
def api_mutate_tasks(
    payload: TaskMutationPayload,
    user: AuthModels.UserInfo = Depends(require_write_user),
) -> dict:
    root = _get_vault_root()
    if not payload.targets:
        return {"ok": True, "updated": 0, "paths": []}
    paths: list[str] = []
    undo_receipt: dict[str, dict] = {"before": {}, "after": {}}
    for raw_target in payload.targets:
        target = TaskMutationTarget(
            path=raw_target.path,
            line=raw_target.line,
            expected_text=raw_target.expected_text,
            expected_status=raw_target.expected_status,
        )
        try:
            result = mutate_task(
                root,
                target,
                text=payload.text,
                status=payload.status,
                priority=payload.priority,
                tags=payload.tags,
                start=payload.start,
                due=payload.due,
                destination=payload.destination,
                delete=payload.delete,
            )
        except TaskConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileAccessError as exc:
            _raise_file_http(400, "Task mutation blocked", exc)
        paths.extend(result.get("paths") or [])
        for path, content in (result.get("before") or {}).items():
            undo_receipt["before"].setdefault(path, content)
        undo_receipt["after"].update(result.get("after") or {})
    _clear_task_cache()
    undo_id = uuid.uuid4().hex
    with _TASK_MUTATION_UNDO_LOCK:
        _TASK_MUTATION_UNDO[undo_id] = undo_receipt
        while len(_TASK_MUTATION_UNDO) > 100:
            _TASK_MUTATION_UNDO.pop(next(iter(_TASK_MUTATION_UNDO)))
    return {
        "ok": True,
        "updated": len(payload.targets),
        "paths": list(dict.fromkeys(paths)),
        "undo_id": undo_id,
    }


@app.get("/api/tasks/triage")
def api_task_triage(user: AuthModels.UserInfo = Depends(get_current_user)) -> dict:
    root = _get_vault_root()
    items = list_triage_items(root)
    return {"items": items, "count": len(items)}


@app.get("/api/quick-captures/process")
def api_quick_capture_chunks(
    user: AuthModels.UserInfo = Depends(get_current_user),
) -> dict:
    items = list_quick_capture_chunks(_get_vault_root())
    return {"items": items, "count": len(items)}


@app.post("/api/quick-captures/process")
def api_process_quick_capture_chunk(
    payload: QuickCaptureProcessPayload,
    user: AuthModels.UserInfo = Depends(require_write_user),
) -> dict:
    try:
        result = process_quick_capture_chunk(
            _get_vault_root(),
            path=payload.path,
            start_line=payload.start_line,
            expected_hash=payload.expected_hash,
            action=payload.action,
            destination=payload.destination,
            text=payload.text,
            status=payload.status,
            priority=payload.priority,
            tags=payload.tags,
            start=payload.start,
            due=payload.due,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except FileAccessError as exc:
        _raise_file_http(400, "Quick Capture processing blocked", exc)
    _clear_task_cache()
    receipt = {
        "before": result.pop("before", {}),
        "after": result.pop("after", {}),
        "attachment_moves": result.pop("attachment_moves", []),
    }
    undo_id = uuid.uuid4().hex
    with _TASK_MUTATION_UNDO_LOCK:
        _TASK_MUTATION_UNDO[undo_id] = receipt
        while len(_TASK_MUTATION_UNDO) > 100:
            _TASK_MUTATION_UNDO.pop(next(iter(_TASK_MUTATION_UNDO)))
    result["undo_id"] = undo_id
    return result


@app.post("/api/tasks/triage/process")
def api_process_task_triage(
    payload: TriageProcessPayload,
    user: AuthModels.UserInfo = Depends(require_write_user),
) -> dict:
    root = _get_vault_root()
    try:
        result = process_triage_item(
            root,
            path=payload.path,
            item_id=payload.item_id,
            expected_hash=payload.expected_hash,
            action=payload.action,
            text=payload.text,
            destination=payload.destination,
            priority=payload.priority,
            tags=payload.tags,
            start=payload.start,
            due=payload.due,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except FileAccessError as exc:
        _raise_file_http(400, "Triage processing blocked", exc)
    _clear_task_cache()
    receipt = {
        "before": result.pop("before", {}),
        "after": result.pop("after", {}),
        "attachment_moves": result.pop("attachment_moves", []),
    }
    undo_id = uuid.uuid4().hex
    with _TASK_MUTATION_UNDO_LOCK:
        _TASK_MUTATION_UNDO[undo_id] = receipt
        while len(_TASK_MUTATION_UNDO) > 100:
            _TASK_MUTATION_UNDO.pop(next(iter(_TASK_MUTATION_UNDO)))
    result["undo_id"] = undo_id
    return result


@app.post("/api/tasks/undo/{undo_id}")
def api_undo_task_mutation(
    undo_id: str,
    user: AuthModels.UserInfo = Depends(require_write_user),
) -> dict:
    with _TASK_MUTATION_UNDO_LOCK:
        receipt = _TASK_MUTATION_UNDO.get(undo_id)
    if not receipt:
        raise HTTPException(status_code=404, detail="That task action can no longer be undone.")
    try:
        result = undo_file_mutation(_get_vault_root(), receipt)
    except TaskConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    with _TASK_MUTATION_UNDO_LOCK:
        _TASK_MUTATION_UNDO.pop(undo_id, None)
    _clear_task_cache()
    invalidate_triage_cache(_get_vault_root())
    return result


@app.post("/api/tasks/update-dates")
def api_update_task_dates(
    payload: TaskDateUpdatePayload,
    user: AuthModels.UserInfo = Depends(require_write_user),
) -> dict:
    root = vault_state.get_root()
    if not payload.targets:
        return {"ok": True, "updated": 0, "paths": []}
    if payload.start_value:
        try:
            _ = Date.fromisoformat(payload.start_value)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid start_value date") from exc
    if payload.due_value:
        try:
            _ = Date.fromisoformat(payload.due_value)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid due_value date") from exc
    targets_by_path: dict[str, list[TaskDateTargetPayload]] = {}
    for target in payload.targets:
        normalized = _vault_relative_path(target.path)
        targets_by_path.setdefault(normalized, []).append(target)
    updated_paths: list[str] = []
    updated_count = 0
    for rel_path, items in targets_by_path.items():
        try:
            content = files.read_file(root, rel_path)
        except FileAccessError as exc:
            _raise_file_http(400, f"Update tasks read blocked for {rel_path}", exc)
        except FileNotFoundError as exc:
            _raise_file_http(404, f"Update tasks read failed for {rel_path}", exc)
        except OSError as exc:
            _raise_file_http(500, f"Update tasks read error for {rel_path}", exc)
        except Exception as exc:
            _raise_file_http(500, f"Update tasks read error for {rel_path}", exc)
        lines = content.splitlines(keepends=True)
        changed = False
        for target in items:
            line_idx = max(int(target.line or 1), 1) - 1
            if line_idx < 0 or line_idx >= len(lines):
                continue
            original = lines[line_idx]
            updated = _update_task_line_dates(
                original,
                start_value=payload.start_value,
                due_value=payload.due_value,
                apply_start=payload.apply_start,
                apply_due=payload.apply_due,
                clear_start=payload.clear_start,
                clear_due=payload.clear_due,
            )
            if updated != original:
                lines[line_idx] = updated
                changed = True
                updated_count += 1
        if not changed:
            continue
        new_content = "".join(lines)
        try:
            files.write_file(root, rel_path, new_content)
        except FileAccessError as exc:
            _raise_file_http(400, f"Update tasks write blocked for {rel_path}", exc)
        except FileNotFoundError as exc:
            _raise_file_http(404, f"Update tasks write failed for {rel_path}", exc)
        except OSError as exc:
            _raise_file_http(500, f"Update tasks write error for {rel_path}", exc)
        except Exception as exc:
            _raise_file_http(500, f"Update tasks write error for {rel_path}", exc)
        try:
            app_indexer.index_page(rel_path, new_content)
        except Exception:
            pass
        try:
            db_path = config._vault_db_path()
            if db_path:
                conn = sqlite3.connect(db_path, check_same_thread=False)
                try:
                    search_index.upsert_page(conn, rel_path, int(time.time()), new_content)
                finally:
                    conn.close()
        except Exception:
            pass
        updated_paths.append(rel_path)
    _clear_task_cache()
    return {"ok": True, "updated": updated_count, "paths": updated_paths}


@app.get("/api/search")
def api_search(
    q: Optional[str] = None,
    subtree: Optional[str] = None,
    limit: int = 50
) -> dict:
    """Full-text search across all pages using FTS5."""
    subtree_str = f" subtree={subtree}" if subtree else ""
    _log_api(f"{_ANSI_BLUE}[API] GET /api/search q={q}{subtree_str} limit={limit}{_ANSI_RESET}")
    
    if not q or not q.strip():
        return {"results": []}
    
    db_path = config._vault_db_path()
    if not db_path:
        return {"results": []}
    
    try:
        conn = sqlite3.connect(db_path, check_same_thread=False)
        results = search_index.search_pages(conn, q, subtree, limit)
        preview = [item.get("path") for item in results[:5]]
        _log_api(
            f"{_ANSI_BLUE}[API] /api/search q={q} results={len(results)} sample={preview}{_ANSI_RESET}"
        )
        conn.close()
        return {"results": results}
    except Exception as e:
        print(f"[API] Search error: {e}")
        return {"results": []}


@app.get("/api/pages/search")
def api_pages_search(
    q: str = "",
    limit: int = 100,
    filter_path: Optional[str] = None,
) -> dict:
    """Simple page search by path/title for navigation dialogs (Jump/Link).
    
    This is a lighter-weight search than /api/search, intended for autocomplete
    in dialogs. It does substring matching on page paths and titles.
    """
    _log_api(f"{_ANSI_BLUE}[API] GET /api/pages/search q={q} limit={limit} filter_path={filter_path}{_ANSI_RESET}")
    
    db_path = config._vault_db_path()
    if not db_path:
        return {"pages": []}
    
    try:
        conn = sqlite3.connect(db_path, check_same_thread=False)
        
        term_lower = q.lower()
        like = f"%{term_lower}%"
        exact_path = f"/{term_lower}"
        starts_path = f"{exact_path}/%"
        
        where_clauses = ["(LOWER(path) LIKE ? OR LOWER(COALESCE(title, '')) LIKE ?)"]
        query_params: list[object] = [like, like]
        normalized_filter = _vault_relative_path(filter_path or "")
        if normalized_filter != "/":
            filter_prefix = normalized_filter.rstrip("/")
            where_clauses.append("(path = ? OR path LIKE ?)")
            query_params.extend([filter_prefix, f"{filter_prefix}/%"])

        # Try to use path_ci/title_ci columns if they exist, otherwise fall back to LOWER()
        cur = conn.execute(
            f"""
            SELECT path, title
            FROM pages
            WHERE {" AND ".join(where_clauses)}
            ORDER BY
                CASE
                    WHEN LOWER(path) = ? THEN 0
                    WHEN LOWER(path) LIKE ? THEN 1
                    WHEN LOWER(COALESCE(title, '')) = ? THEN 2
                    WHEN LOWER(COALESCE(title, '')) LIKE ? THEN 3
                    ELSE 4
                END,
                COALESCE(updated, 0) DESC
            LIMIT ?
            """,
            (*query_params, exact_path, starts_path, term_lower, like, limit),
        )
        rows = cur.fetchall()
        conn.close()
        
        pages = [{"path": row[0], "title": row[1]} for row in rows]
        _log_api(f"{_ANSI_BLUE}[API] /api/pages/search q={q} found {len(pages)} pages{_ANSI_RESET}")
        return {"pages": pages}
    except Exception as e:
        print(f"[API] Pages search error: {e}")
        traceback.print_exc()
        return {"pages": []}


@app.get("/api/search/status")
def api_search_status() -> dict:
    """Check if the search index has been populated."""
    db_path = config._vault_db_path()
    if not db_path:
        return {"populated": False, "count": 0}
    
    try:
        conn = sqlite3.connect(db_path, check_same_thread=False)
        cursor = conn.execute("SELECT COUNT(*) FROM pages_search_index")
        count = cursor.fetchone()[0]
        conn.close()
        return {"populated": count > 0, "count": count}
    except Exception as e:
        print(f"[API] Search status error: {e}")
        return {"populated": False, "count": 0}


# ===== Web Sync API Endpoints =====

@app.get("/sync/changes")
def sync_changes(
    since_rev: int = 0,
    user: AuthModels.UserInfo = Depends(get_current_user)
) -> dict:
    """Get all pages changed since a given sync revision.
    
    Returns pages with rev > since_rev, including deleted pages.
    """
    db_path = config._vault_db_path()
    if not db_path:
        raise HTTPException(status_code=400, detail="No vault selected")
    conn = sqlite3.connect(db_path, check_same_thread=False)
    try:
        current_sync_rev = config.get_sync_revision()
        
        # Get changed pages (including deleted ones)
        rows = conn.execute(
            """
            SELECT page_id, path, title, updated, rev, deleted, pinned, parent_path
            FROM pages
            WHERE rev > ?
            ORDER BY rev ASC
            """,
            (since_rev,)
        ).fetchall()
        
        changes = []
        for row in rows:
            changes.append({
                "page_id": row[0],
                "path": row[1],
                "title": row[2],
                "updated": row[3],
                "rev": row[4],
                "deleted": bool(row[5]),
                "pinned": bool(row[6]),
                "parent_path": row[7]
            })
        
        return {
            "sync_revision": current_sync_rev,
            "changes": changes,
            "has_more": False
        }
    finally:
        conn.close()


@app.get("/recent")
def get_recent_pages(
    limit: int = 20,
    user: AuthModels.UserInfo = Depends(get_current_user)
) -> dict:
    """Get recently modified pages."""
    db_path = config._vault_db_path()
    if not db_path:
        raise HTTPException(status_code=400, detail="No vault selected")
    
    conn = sqlite3.connect(db_path, check_same_thread=False)
    try:
        rows = conn.execute(
            """
            SELECT page_id, path, title, updated, rev
            FROM pages
            WHERE deleted = 0
            ORDER BY updated DESC
            LIMIT ?
            """,
            (limit,)
        ).fetchall()
        
        pages = []
        for row in rows:
            pages.append({
                "page_id": row[0],
                "path": row[1],
                "title": row[2],
                "updated": row[3],
                "rev": row[4]
            })
        
        return {"pages": pages}
    finally:
        conn.close()


@app.get("/tags")
def get_all_tags(user: AuthModels.UserInfo = Depends(get_current_user)) -> dict:
    """Get all tags with page counts."""
    db_path = config._vault_db_path()
    if not db_path:
        raise HTTPException(status_code=400, detail="No vault selected")
    
    conn = sqlite3.connect(db_path, check_same_thread=False)
    try:
        rows = conn.execute(
            """
            SELECT tag, COUNT(DISTINCT page) as count
            FROM page_tags
            WHERE page IN (SELECT path FROM pages WHERE deleted = 0)
            GROUP BY tag
            ORDER BY tag
            """
        ).fetchall()
        
        tags = [{"tag": row[0], "count": row[1]} for row in rows]
        return {"tags": tags}
    finally:
        conn.close()


@app.get("/pages/{page_id}/links")
def get_page_links(
    page_id: str,
    user: AuthModels.UserInfo = Depends(get_current_user)
) -> dict:
    """Get outgoing links from a page."""
    db_path = config._vault_db_path()
    if not db_path:
        raise HTTPException(status_code=400, detail="No vault selected")
    
    conn = sqlite3.connect(db_path, check_same_thread=False)
    try:
        # Get page path from page_id
        row = conn.execute("SELECT path FROM pages WHERE page_id = ?", (page_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Page not found")
        
        from_path = row[0]
        
        # Get outgoing links
        rows = conn.execute(
            "SELECT to_path FROM links WHERE from_path = ?",
            (from_path,)
        ).fetchall()
        
        links = [row[0] for row in rows]
        return {"links": links}
    finally:
        conn.close()


@app.get("/pages/{page_id}/backlinks")
def get_page_backlinks(
    page_id: str,
    user: AuthModels.UserInfo = Depends(get_current_user)
) -> dict:
    """Get incoming links (backlinks) to a page."""
    db_path = config._vault_db_path()
    if not db_path:
        raise HTTPException(status_code=400, detail="No vault selected")
    
    conn = sqlite3.connect(db_path, check_same_thread=False)
    try:
        # Get page path from page_id
        row = conn.execute("SELECT path FROM pages WHERE page_id = ?", (page_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Page not found")
        
        to_path = row[0]
        
        # Get backlinks
        rows = conn.execute(
            "SELECT from_path FROM links WHERE to_path = ?",
            (to_path,)
        ).fetchall()
        
        backlinks = [row[0] for row in rows]
        return {"backlinks": backlinks}
    finally:
        conn.close()


@app.post("/api/ai/chat")
async def api_chat(payload: ChatPayload) -> dict:
    base_url = os.getenv("LMSTUDIO_BASE_URL")
    if not base_url:
        return {"choices": [{"message": {"role": "assistant", "content": "LM Studio base URL not configured."}}]}
    url = base_url.rstrip("/") + "/chat/completions"
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.post(
                url,
                json={
                    "model": "lmstudio",
                    "messages": payload.messages,
                    "max_tokens": payload.max_tokens,
                    "temperature": payload.temperature,
                },
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:  # pragma: no cover - network path
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    return resp.json()


@app.post("/api/path/create")
def create_path(payload: CreatePathPayload, user: AuthModels.UserInfo = Depends(require_write_user)) -> dict:
    root = vault_state.get_root()
    page_path: Optional[str] = None
    version = config.get_tree_version()
    try:
        if payload.is_dir:
            files.create_directory(root, payload.path)
            page_path = config.folder_to_page_path(payload.path)
        else:
            files.create_markdown_file(root, payload.path, payload.content or "")
            page_path = payload.path
        if page_path:
            config.ensure_page_entry(page_path)
            # Update search index for new page
            db_path = config._vault_db_path()
            if db_path:
                conn = sqlite3.connect(db_path, check_same_thread=False)
                content = payload.content or ""
                search_index.upsert_page(conn, page_path, int(time.time()), content)
                conn.close()
        version = config.bump_tree_version()
    except FileExistsError as exc:
        _raise_file_http(409, f"Create path failed for {payload.path}", exc)
    except FileAccessError as exc:
        _raise_file_http(400, f"Create path blocked for {payload.path}", exc)
    except OSError as exc:
        _raise_file_http(500, f"Create path error for {payload.path}", exc)
    except Exception as exc:
        _raise_file_http(500, f"Create path error for {payload.path}", exc)
    return {"ok": True, "version": version}


@app.post("/api/path/delete")
def delete_path(payload: DeletePathPayload, user: AuthModels.UserInfo = Depends(require_write_user)) -> dict:
    root = vault_state.get_root()
    _require_no_reorganization_recovery(root)
    try:
        with vault_reorg.structural_operation(root):
            result = file_ops.delete_folder(root, payload.path)
        # Remove from search index
        db_path = config._vault_db_path()
        if db_path:
            conn = sqlite3.connect(db_path, check_same_thread=False)
            search_index.delete_tree(conn, payload.path)
            conn.close()
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=_format_file_op_detail(f"Delete path failed for {payload.path}", exc),
        ) from exc
    except FileAccessError as exc:
        raise HTTPException(
            status_code=400,
            detail=_format_file_op_detail(f"Delete path blocked for {payload.path}", exc),
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=_format_file_op_detail(f"Delete path error for {payload.path}", exc),
        ) from exc
    return {"ok": True, **result}


@app.options("/api/file/operation")
def file_operation_options(path: str, op: Literal["rename", "move", "delete"], dest: Optional[str] = None) -> dict:
    root = vault_state.get_root()
    if vault_reorg.recovery_status(root).get("recovery_required"):
        return {"canOperate": False, "reason": "Vault reorganization recovery is required"}
    ok, reason = file_ops.preflight(root, op, path, dest)
    return {"canOperate": ok, "reason": reason}


def _require_no_reorganization_recovery(root: Path) -> None:
    if vault_reorg.recovery_status(root).get("recovery_required"):
        raise HTTPException(status_code=409, detail="Vault reorganization recovery is required.")


@app.post("/api/file/rename")
def file_rename(payload: RenameMovePayload, user: AuthModels.UserInfo = Depends(require_write_user)) -> dict:
    root = vault_state.get_root()
    _require_no_reorganization_recovery(root)
    with vault_reorg.structural_operation(root):
        ok, reason = file_ops.preflight(root, "rename", payload.from_path, payload.to_path)
        if not ok:
            exc = RuntimeError(reason or "Preflight failed")
            raise HTTPException(
                status_code=400,
                detail=_format_file_op_detail(f"Rename preflight failed for {payload.from_path}", exc),
            ) from exc
        try:
            result = file_ops.rename_folder(root, payload.from_path, payload.to_path)
        except FileNotFoundError as exc:
            _raise_file_http(404, f"Rename failed for {payload.from_path}", exc)
        except FileAccessError as exc:
            _raise_file_http(400, f"Rename blocked for {payload.from_path}", exc)
        except OSError as exc:
            _raise_file_http(500, f"Rename error for {payload.from_path}", exc)
        except Exception as exc:
            _raise_file_http(500, f"Rename error for {payload.from_path}", exc)
    return {"ok": True, **result}


@app.post("/api/file/move")
def file_move(payload: RenameMovePayload, user: AuthModels.UserInfo = Depends(require_write_user)) -> dict:
    _log_api(f"{_ANSI_BLUE}[API] POST /api/file/move from={payload.from_path} to={payload.to_path}{_ANSI_RESET}")
    root = vault_state.get_root()
    _require_no_reorganization_recovery(root)
    with vault_reorg.structural_operation(root):
        ok, reason = file_ops.preflight(root, "move", payload.from_path, payload.to_path)
        if not ok:
            _log_api(f"{_ANSI_BLUE}[API] /api/file/move preflight failed: {reason}{_ANSI_RESET}")
            exc = RuntimeError(reason or "Preflight failed")
            raise HTTPException(
                status_code=400,
                detail=_format_file_op_detail(f"Move preflight failed for {payload.from_path}", exc),
            ) from exc
        try:
            result = file_ops.move_folder(root, payload.from_path, payload.to_path, rewrite_links=payload.rewrite_links)
        except FileNotFoundError as exc:
            _log_api(f"{_ANSI_BLUE}[API] /api/file/move not found: {exc}{_ANSI_RESET}")
            _raise_file_http(404, f"Move failed for {payload.from_path}", exc)
        except FileAccessError as exc:
            _log_api(f"{_ANSI_BLUE}[API] /api/file/move error: {exc}{_ANSI_RESET}")
            _raise_file_http(400, f"Move blocked for {payload.from_path}", exc)
        except OSError as exc:
            _raise_file_http(500, f"Move error for {payload.from_path}", exc)
        except Exception as exc:
            _raise_file_http(500, f"Move error for {payload.from_path}", exc)
    return {"ok": True, **result}


@app.get("/api/vault/reorganize/candidates")
def vault_reorganize_candidates(
    q: str = "",
    include_content: bool = False,
    journal_only: bool = False,
    limit: int = 100,
) -> dict:
    return vault_reorg.search_candidates(
        q,
        include_content=include_content,
        journal_only=journal_only,
        limit=max(1, min(int(limit), 500)),
    )


@app.post("/api/vault/reorganize/preflight")
def vault_reorganize_preflight(
    payload: ReorganizationPlanPayload,
    user: AuthModels.UserInfo = Depends(require_write_user),
) -> dict:
    root = vault_state.get_root()
    operations = [item.model_dump(by_alias=True) for item in payload.operations]
    return vault_reorg.preflight_plan(root, operations, payload.tree_version)


@app.post("/api/vault/reorganize/commit")
def vault_reorganize_commit(
    payload: ReorganizationPlanPayload,
    user: AuthModels.UserInfo = Depends(require_write_user),
) -> dict:
    if payload.tree_version is None or not payload.plan_token:
        raise HTTPException(status_code=400, detail="A validated plan token is required.")
    root = vault_state.get_root()
    operations = [item.model_dump(by_alias=True) for item in payload.operations]
    try:
        return vault_reorg.commit_plan(
            root,
            operations,
            tree_version=int(payload.tree_version),
            plan_token=payload.plan_token,
        )
    except vault_reorg.ReorganizationError as exc:
        raise HTTPException(
            status_code=409,
            detail={"message": str(exc), "errors": exc.errors},
        ) from exc


@app.get("/api/vault/reorganize/recovery")
def vault_reorganize_recovery_status() -> dict:
    return vault_reorg.recovery_status(vault_state.get_root())


@app.post("/api/vault/reorganize/recover")
def vault_reorganize_recover(user: AuthModels.UserInfo = Depends(require_write_user)) -> dict:
    try:
        return vault_reorg.recover_incomplete(vault_state.get_root())
    except vault_reorg.ReorganizationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.delete("/api/file")
def file_delete(payload: FileDeletePayload, user: AuthModels.UserInfo = Depends(require_write_user)) -> dict:
    root = vault_state.get_root()
    _require_no_reorganization_recovery(root)
    with vault_reorg.structural_operation(root):
        ok, reason = file_ops.preflight(root, "delete", payload.path)
        if not ok:
            exc = RuntimeError(reason or "Preflight failed")
            raise HTTPException(
                status_code=400,
                detail=_format_file_op_detail(f"Delete preflight failed for {payload.path}", exc),
            ) from exc
        try:
            result = file_ops.delete_folder(root, payload.path)
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=404,
                detail=_format_file_op_detail(f"Delete file failed for {payload.path}", exc),
            ) from exc
        except FileAccessError as exc:
            raise HTTPException(
                status_code=400,
                detail=_format_file_op_detail(f"Delete file blocked for {payload.path}", exc),
            ) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=_format_file_op_detail(f"Delete file error for {payload.path}", exc),
            ) from exc
    return {"ok": True, **result}


@app.post("/api/tree/reorder")
def tree_reorder(payload: ReorderPayload, user: AuthModels.UserInfo = Depends(require_write_user)) -> dict:
    """Reorder pages within a parent folder without moving files."""
    _get_vault_root()
    _log_api(f"{_ANSI_BLUE}[API] POST /api/tree/reorder parent={payload.parent_path} count={len(payload.page_order)}{_ANSI_RESET}")
    try:
        config.reorder_pages(payload.parent_path, payload.page_order)
        version = config.bump_tree_version()
        _clear_tree_cache()
        _log_api(f"{_ANSI_BLUE}[API] Reordered {len(payload.page_order)} items, new version={version}{_ANSI_RESET}")
    except Exception as exc:
        _log_api(f"{_ANSI_BLUE}[API] Reorder failed: {exc}{_ANSI_RESET}")
        raise HTTPException(status_code=500, detail=f"Failed to reorder: {exc}") from exc
    return {"ok": True, "version": version}


@app.post("/api/vault/update-links")
def vault_update_links(payload: UpdateLinksPayload, user: AuthModels.UserInfo = Depends(require_write_user)) -> dict:
    root = vault_state.get_root()
    try:
        touched = file_ops.update_links_on_disk(root, payload.path_map)
    except FileAccessError as exc:
        _raise_file_http(400, "Update links blocked", exc)
    except FileNotFoundError as exc:
        _raise_file_http(404, "Update links failed", exc)
    except OSError as exc:
        _raise_file_http(500, "Update links error", exc)
    except Exception as exc:
        _raise_file_http(500, "Update links error", exc)
    return {"ok": True, "touched": touched}


_LINK_UPDATE_JOBS: dict[str, dict] = {}
_LINK_UPDATE_JOBS_LOCK = threading.Lock()
_LINK_UPDATE_RUN_LOCKS: dict[str, threading.Lock] = {}


def _run_link_update_job(job_id: str, root: Path, path_map: dict[str, str]) -> None:
    root_key = str(root.resolve())
    with _LINK_UPDATE_JOBS_LOCK:
        run_lock = _LINK_UPDATE_RUN_LOCKS.setdefault(root_key, threading.Lock())
        _LINK_UPDATE_JOBS[job_id].update(status="running", message="Updating links…")
    try:
        with run_lock:
            touched = file_ops.update_links_on_disk(root, path_map)
        with _LINK_UPDATE_JOBS_LOCK:
            _LINK_UPDATE_JOBS[job_id].update(
                status="completed",
                message=f"Updated links in {len(touched)} page(s)",
                touched=len(touched),
                touched_paths=touched,
            )
    except Exception as exc:
        with _LINK_UPDATE_JOBS_LOCK:
            _LINK_UPDATE_JOBS[job_id].update(status="error", message=str(exc))


@app.post("/api/vault/update-links/background")
def vault_update_links_background(
    payload: UpdateLinksPayload,
    user: AuthModels.UserInfo = Depends(require_write_user),
) -> dict:
    """Start a serialized backlink rewrite without blocking the requesting UI."""
    root = vault_state.get_root()
    job_id = str(uuid.uuid4())
    with _LINK_UPDATE_JOBS_LOCK:
        _LINK_UPDATE_JOBS[job_id] = {
            "status": "queued",
            "message": "Waiting to update links…",
            "touched": 0,
            "touched_paths": [],
        }
    threading.Thread(
        target=_run_link_update_job,
        args=(job_id, root, dict(payload.path_map)),
        daemon=True,
    ).start()
    return {"ok": True, "job_id": job_id}


@app.get("/api/vault/update-links/status/{job_id}")
def vault_update_links_status(
    job_id: str,
    user: AuthModels.UserInfo = Depends(require_write_user),
) -> dict:
    with _LINK_UPDATE_JOBS_LOCK:
        job = _LINK_UPDATE_JOBS.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Link update job not found")
        return {"job_id": job_id, **job}


@app.post("/files/attach")
def attach_files(
    request: Request,
    page_path: str = Form(...),
    files: List[UploadFile] = FastAPISingleFile(...),
    user: AuthModels.UserInfo = Depends(require_write_user),
) -> dict:
    root = _get_vault_root()
    normalized_page = _vault_relative_path(page_path)
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")
    stored_paths: list[str] = []
    use_local_ops = _should_use_local_file_ops(request)
    for upload in files:
        try:
            stored_paths.append(_store_attachment(root, normalized_page, upload, use_local_ops))
        except HTTPException:
            raise
        except FileAccessError as exc:
            _raise_file_http(400, f"Attach file blocked for {normalized_page}", exc)
        except FileNotFoundError as exc:
            _raise_file_http(404, f"Attach file failed for {normalized_page}", exc)
        except OSError as exc:
            _raise_file_http(500, f"Attach file error for {normalized_page}", exc)
        except Exception as exc:
            _raise_file_http(500, f"Attach file error for {normalized_page}", exc)
    _log_attachment(f"Attached {len(stored_paths)} file(s) for {normalized_page}")
    return {"ok": True, "page": normalized_page, "attachments": stored_paths}


@app.get("/files/")
def list_files(page_path: str) -> dict:
    _get_vault_root()
    normalized_page = _vault_relative_path(page_path)
    root = _get_vault_root()
    attachments = config.list_page_attachments(normalized_page)
    attachment_paths: set[str] = set()
    for entry in attachments:
        if isinstance(entry, dict):
            path = entry.get("attachment_path") or entry.get("stored_path")
            if path:
                attachment_paths.add(str(path))

    # Fallback: scan the page folder for unindexed attachments
    page_file = (root / normalized_page.lstrip("/")).resolve()
    page_folder = page_file.parent if page_file.exists() else None
    if page_folder and page_folder.exists() and page_folder.is_dir():
        try:
            for candidate in page_folder.iterdir():
                if not candidate.is_file():
                    continue
                if candidate == page_file:
                    continue
                rel = f"/{candidate.relative_to(root).as_posix()}"
                if rel in attachment_paths:
                    continue
                try:
                    updated = candidate.stat().st_mtime
                except OSError:
                    updated = None
                entry = {
                    "attachment_path": rel,
                    "stored_path": str(candidate),
                    "updated": updated,
                }
                attachments.append(entry)
                attachment_paths.add(rel)
                # Backfill index so future lists are complete
                try:
                    config.upsert_attachment_entry(normalized_page, rel, str(candidate), updated=updated)
                except Exception:
                    pass
        except OSError:
            pass

    _log_attachment(f"Listed {len(attachments)} attachment(s) for {normalized_page}")
    return {"attachments": attachments}


@app.post("/files/delete")
def delete_files(request: Request, payload: AttachmentDeletePayload) -> dict:
    root = _get_vault_root()
    deleted: list[str] = []
    use_local_ops = _should_use_local_file_ops(request)
    seen: set[str] = set()
    for path in payload.paths:
        normalized = _vault_relative_path(path)
        if normalized in seen:
            continue
        seen.add(normalized)
        _remove_attachment_copy(root, normalized, use_local_ops)
        if config.delete_attachment_entry(normalized):
            deleted.append(normalized)
    _log_attachment(f"Deleted {len(deleted)} attachment(s)")
    return {"ok": True, "deleted": deleted}


@app.post("/vector/add")
def vector_add(payload: VectorAddPayload) -> dict:
    root = _get_vault_root()
    if not payload.text.strip():
        raise HTTPException(status_code=400, detail="Text must not be empty")
    try:
        vector_manager.index_text(root, payload.page_ref, payload.text, payload.kind, payload.attachment_name)
        _log_vector(f"Added vector entry for {payload.page_ref} ({payload.kind})")
    except Exception as exc:
        _handle_vector_exception("indexing vector data", exc)
    return {"ok": True}


@app.post("/vector/remove")
def vector_remove(payload: VectorRemovePayload) -> dict:
    root = _get_vault_root()
    try:
        vector_manager.delete_text(root, payload.page_ref, payload.kind, payload.attachment_name)
        _log_vector(f"Removed vector entry for {payload.page_ref} ({payload.kind})")
    except Exception as exc:
        _handle_vector_exception("removing vector data", exc)
    return {"ok": True}


def _chunk_to_dict(chunk: RetrievedChunk) -> dict:
    return {
        "page_ref": chunk.page_ref,
        "content": chunk.content,
        "score": chunk.score,
        "attachment_name": chunk.attachment_name,
    }


@app.post("/vector/query")
def vector_query(payload: VectorQueryPayload) -> dict:
    root = _get_vault_root()
    try:
        if payload.kind == "attachment":
            if not payload.attachment_names:
                raise HTTPException(status_code=400, detail="Attachment names required for attachment query")
            chunks = vector_manager.query_attachments(
                root,
                payload.query_text,
                payload.attachment_names,
                limit=payload.limit,
                kind="attachment",
            )
        else:
            chunks = vector_manager.query(
                root,
                payload.query_text,
                page_refs=payload.page_refs,
                limit=payload.limit,
                kind="page",
            )
        _log_vector(
            f"Queried {payload.kind} context limit={payload.limit} "
            f"pages={payload.page_refs or 'any'} "
            f"attachments={payload.attachment_names or 'any'}"
        )
    except HTTPException:
        raise
    except Exception as exc:
        _handle_vector_exception("querying vector data", exc)
    if payload.kind != "attachment":
        chunks = _apply_exact_match_fallback(root, payload, chunks)
    return {"chunks": [_chunk_to_dict(chunk) for chunk in chunks]}


def _apply_exact_match_fallback(
    root: Path,
    payload: VectorQueryPayload,
    chunks: list[RetrievedChunk],
) -> list[RetrievedChunk]:
    query = (payload.query_text or "").strip()
    if not query or " " in query:
        return chunks
    lowered = query.lower()
    if any(lowered in (chunk.content or "").lower() for chunk in chunks):
        return chunks
    if not payload.page_refs:
        return chunks
    matches: list[RetrievedChunk] = []
    for page_ref in payload.page_refs:
        try:
            content = files.read_file(root, page_ref)
        except Exception:
            continue
        lines = []
        for line in content.splitlines():
            if lowered in line.lower():
                lines.append(line.strip())
        if not lines:
            continue
        snippet = "\n".join(lines[:6])
        matches.append(
            RetrievedChunk(
                page_ref=page_ref,
                content=snippet,
                score=0.0,
                attachment_name=None,
            )
        )
    if matches:
        _log_vector(f"Exact-match fallback added {len(matches)} chunk(s) for {query!r}")
        return matches + chunks
    return chunks


def _sort_tree_nodes(nodes: list[dict], order_map: dict[str, int]) -> None:
    """Sort tree nodes in-place using display order, defaulting to alpha."""
    for node in nodes:
        children = node.get("children") or []
        _sort_tree_nodes(children, order_map)
        node["children"] = children

    def _key(node: dict) -> tuple:
        open_path = node.get("open_path")
        order_val = order_map.get(open_path) if open_path else None
        node_name = (node.get("name") or "").lower()
        if order_val is not None:
            _log_sort(f"[SORT] {node_name}: open_path={open_path}, order={order_val}")
        else:
            _log_sort(f"[SORT] {node_name}: open_path={open_path}, order=None (will sort by name)")
        return (order_val if order_val is not None else float("inf"), node_name)

    nodes.sort(key=_key)


def _log_attachment(message: str) -> None:
    if log_enabled("attachments_media"):
        print(f"[Attachments] {message}")


def _log_vector(message: str) -> None:
    if log_enabled("rag_vector"):
        print(f"[Vector] {message}")


def _handle_vector_exception(context: str, exc: Exception) -> None:
    _log_vector(f"{context} failed: {exc}")
    traceback.print_exc()
    raise HTTPException(status_code=500, detail=str(exc)) from exc


def _print_override_dirs(root: Path) -> list[Path]:
    return [
        root / ".stillpoint" / "templates",
        Path.home() / ".stillpoint" / "templates",
    ]


def _builtin_print_templates_dir() -> Optional[Path]:
    candidates: list[Path] = []
    base = Path(__file__).resolve().parent
    candidates.append(base / "templates")
    candidates.append(base / "sp" / "server" / "templates")
    candidates.append(base.parent / "sp" / "server" / "templates")
    try:
        server_pkg = importlib.import_module("sp.server")
        pkg_path = Path(getattr(server_pkg, "__file__", "")).resolve().parent
        candidates.append(pkg_path / "templates")
    except Exception:
        pass
    try:
        resource_path = importlib_resources.files("sp.server").joinpath("templates")
        if resource_path.is_dir():
            candidates.append(Path(str(resource_path)))
    except Exception:
        pass
    seen: set[Path] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if candidate.exists() and candidate.is_dir():
            return candidate
    return None


def _find_print_override(root: Path, filename: str) -> Optional[Path]:
    for base in _print_override_dirs(root):
        candidate = base / filename
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def _load_print_template(root: Path):
    override = _find_print_override(root, "print.html")
    if not override:
        builtin_dir = _builtin_print_templates_dir()
        if builtin_dir:
            env = Environment(
                loader=FileSystemLoader(builtin_dir),
                autoescape=select_autoescape(["html", "xml"]),
            )
            return env.get_template("print.html")
        return _PRINT_TEMPLATES.get_template("print.html")
    env = Environment(
        loader=FileSystemLoader(override.parent),
        autoescape=select_autoescape(["html", "xml"]),
    )
    return env.get_template(override.name)


def _load_print_css(root: Path) -> str:
    override = _find_print_override(root, "print.css")
    if override:
        return override.read_text(encoding="utf-8")
    builtin_dir = _builtin_print_templates_dir()
    css_path = (builtin_dir / "print.css") if builtin_dir else (Path(__file__).parent / "templates" / "print.css")
    return css_path.read_text(encoding="utf-8")


def _print_css_url(token: Optional[str]) -> str:
    if token:
        return f"/print.css?token={quote(token)}"
    return "/print.css"


def _verify_print_token(token: str) -> AuthModels.UserInfo:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except JWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid print token") from exc
    if payload.get("scope") != "print":
        raise HTTPException(status_code=401, detail="Invalid print token scope")
    username = payload.get("sub") or "print"
    return AuthModels.UserInfo(username=username, is_admin=True)


async def _require_print_user(
    request: Request,
    token: Optional[str],
    credentials: Optional[HTTPAuthorizationCredentials],
) -> Optional[AuthModels.UserInfo]:
    if not AUTH_ENABLED:
        return AuthModels.UserInfo(username="admin", is_admin=True)
    if token:
        return _verify_print_token(token)
    return await get_current_user(request, credentials)


def _resolve_page_file_for_print(root: Path, path: str) -> Path:
    normalized = _vault_relative_path(path)
    target = (root / normalized.lstrip("/")).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid page path") from exc
    target = files._resolve_page_for_read(target)
    if not target.exists():
        raise HTTPException(status_code=404, detail="Page not found")
    return target


def _resolve_tree_root(root: Path, path: str) -> Path:
    normalized = _vault_relative_path(path)
    target = (root / normalized.lstrip("/")).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid page path") from exc
    if not target.exists():
        raise HTTPException(status_code=404, detail="Folder not found")
    if target.is_file():
        target = target.parent
    if not target.is_dir():
        raise HTTPException(status_code=404, detail="Folder not found")
    return target


def _render_markdown_html(text: str) -> str:
    renderer = md.Markdown(extensions=["fenced_code", "tables", "nl2br"])
    normalized = _rewrite_zim_links(text)
    normalized = _normalize_markdown_image_blocks(normalized)
    normalized = _rewrite_markdown_image_sizes(normalized)
    normalized = _rewrite_task_and_dash_markers(normalized)
    normalized = _normalize_markdown_lists(normalized)
    normalized = _rewrite_highlight(normalized)
    normalized = _rewrite_strikethrough(normalized)
    html = renderer.convert(normalized)
    html = _rewrite_task_checkboxes(html)
    return html


def _rewrite_task_and_dash_markers(text: str) -> str:
    """Preserve '-' lines as dashed text and render checkbox markers consistently."""
    lines = text.splitlines()
    normalized: list[str] = []
    in_fence = False
    fence_marker = ""
    checkbox_re = re.compile(r"^(?P<indent>[ \t]*)(?:(?:[-+*]|\d+\.)[ \t]+)?\[(?P<state>[ xX])\][ \t]+(?P<rest>.*)$")
    dash_re = re.compile(r"^(?P<indent>[ \t]*)-[ \t]+(?P<rest>.*)$")

    for line in lines:
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            marker = stripped[:3]
            if not in_fence:
                in_fence = True
                fence_marker = marker
            elif marker == fence_marker:
                in_fence = False
                fence_marker = ""
            normalized.append(line)
            continue

        if in_fence:
            normalized.append(line)
            continue

        checkbox_match = checkbox_re.match(line)
        if checkbox_match:
            state = (checkbox_match.group("state") or " ").lower()
            checked = state == "x"
            cls = "md-checkbox md-checkbox--checked" if checked else "md-checkbox md-checkbox--unchecked"
            indent = checkbox_match.group("indent") or ""
            rest = checkbox_match.group("rest") or ""
            normalized.append(f'{indent}<span class="{cls}" aria-hidden="true"></span>{rest}')
            continue

        dash_match = dash_re.match(line)
        if dash_match:
            indent = dash_match.group("indent") or ""
            rest = dash_match.group("rest") or ""
            normalized.append(f"{indent}\\- {rest}")
            continue

        normalized.append(line)

    return "\n".join(normalized)


def _rewrite_task_checkboxes(html_text: str) -> str:
    """Render markdown task markers as stylable checkbox spans."""
    pattern = re.compile(r"(<li>\s*)\[(?P<state>[ xX])\]\s+", re.IGNORECASE)

    def _replace(match: re.Match) -> str:
        state = (match.group("state") or " ").lower()
        checked = state == "x"
        cls = "md-checkbox md-checkbox--checked" if checked else "md-checkbox md-checkbox--unchecked"
        return f'{match.group(1)}<span class="{cls}" aria-hidden="true"></span>'

    return pattern.sub(_replace, html_text)


def _normalize_markdown_image_blocks(text: str) -> str:
    """De-indent image-only lines so they don't become code blocks in print."""
    lines = text.splitlines()
    normalized: list[str] = []
    in_fence = False
    fence_marker = ""

    def _is_image_line(value: str) -> bool:
        if not value:
            return False
        remaining = _IMAGE_MD_ANY_RE.sub("", value)
        return not remaining.strip()

    for line in lines:
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            marker = stripped[:3]
            if not in_fence:
                in_fence = True
                fence_marker = marker
            elif marker == fence_marker:
                in_fence = False
                fence_marker = ""
            normalized.append(line)
            continue

        if in_fence:
            normalized.append(line)
            continue

        if _is_image_line(stripped):
            normalized.append(stripped)
        else:
            normalized.append(line)

    return "\n".join(normalized)


def _normalize_markdown_lists(text: str) -> str:
    """Insert blank lines before list blocks when missing (improves list parsing)."""
    def _is_list_line(value: str) -> bool:
        trimmed = value.lstrip()
        if trimmed.startswith(("* ", "- ", "+ ")):
            return True
        digits = ""
        for ch in trimmed:
            if ch.isdigit():
                digits += ch
            else:
                break
        return bool(digits) and trimmed[len(digits):].startswith(". ")

    def _indent_cols(value: str) -> int:
        cols = 0
        for ch in value:
            if ch == " ":
                cols += 1
            elif ch == "\t":
                cols += 4
            else:
                break
        return cols

    def _strip_indent(value: str, cols: int) -> str:
        if cols <= 0:
            return value
        remaining = cols
        idx = 0
        while idx < len(value) and remaining > 0:
            ch = value[idx]
            if ch == " ":
                remaining -= 1
            elif ch == "\t":
                remaining -= 4
            else:
                break
            idx += 1
        return value[idx:]

    lines = text.splitlines()
    normalized: list[str] = []
    in_fence = False
    fence_marker = ""
    deindent_active = False
    deindent_cols = 0

    for line in lines:
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            marker = stripped[:3]
            if not in_fence:
                in_fence = True
                fence_marker = marker
            elif marker == fence_marker:
                in_fence = False
                fence_marker = ""
            normalized.append(line)
            continue

        if in_fence:
            normalized.append(line)
            continue

        if stripped == "":
            normalized.append(line)
            continue

        if not deindent_active and _is_list_line(line):
            indent = _indent_cols(line)
            if indent >= 4:
                deindent_active = True
                deindent_cols = 4

        if _is_list_line(line):
            if normalized:
                prev = normalized[-1].strip()
                if prev and not _is_list_line(prev):
                    normalized.append("")
            if deindent_active:
                normalized.append(_strip_indent(line, deindent_cols))
                continue
        else:
            if deindent_active:
                deindent_active = False
                deindent_cols = 0

        normalized.append(line)

    return "\n".join(normalized)


def _rewrite_strikethrough(text: str) -> str:
    """Convert ~~text~~ to <del>text</del> outside code fences/inline code."""
    lines = text.splitlines()
    normalized: list[str] = []
    in_fence = False
    fence_marker = ""

    def _replace_inline(value: str) -> str:
        parts = value.split("`")
        for idx in range(0, len(parts), 2):
            parts[idx] = re.sub(r"~~(.*?)~~", r"<del>\1</del>", parts[idx])
        return "`".join(parts)

    for line in lines:
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            marker = stripped[:3]
            if not in_fence:
                in_fence = True
                fence_marker = marker
            elif marker == fence_marker:
                in_fence = False
                fence_marker = ""
            normalized.append(line)
            continue

        if in_fence:
            normalized.append(line)
            continue

        normalized.append(_replace_inline(line))

    return "\n".join(normalized)


def _rewrite_highlight(text: str) -> str:
    """Convert ==text== to <mark>text</mark> outside code fences/inline code."""
    lines = text.splitlines()
    normalized: list[str] = []
    in_fence = False
    fence_marker = ""

    def _replace_inline(value: str) -> str:
        parts = value.split("`")
        for idx in range(0, len(parts), 2):
            parts[idx] = re.sub(r"==(.+?)==", r"<mark>\1</mark>", parts[idx])
        return "`".join(parts)

    for line in lines:
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            marker = stripped[:3]
            if not in_fence:
                in_fence = True
                fence_marker = marker
            elif marker == fence_marker:
                in_fence = False
                fence_marker = ""
            normalized.append(line)
            continue

        if in_fence:
            normalized.append(line)
            continue

        normalized.append(_replace_inline(line))

    return "\n".join(normalized)


def _rewrite_markdown_image_sizes(text: str) -> str:
    def _replace(match: re.Match) -> str:
        alt = html.escape(match.group("alt") or "", quote=True)
        path = html.escape(match.group("path") or "", quote=True)
        title = match.group("title")
        width = match.group("width")
        title_attr = f' title="{html.escape(title, quote=True)}"' if title else ""
        return f'<img src="{path}" alt="{alt}" style="width: {width}px;"{title_attr} />'

    return _IMAGE_MD_SIZE_RE.sub(_replace, text)


def _rewrite_zim_links(text: str) -> str:
    def _replace(match: re.Match) -> str:
        target = (match.group("target") or "").strip()
        label = (match.group("label") or "").strip()
        if target.startswith("http://") or target.startswith("https://"):
            link_label = label or target
            return f"[{link_label}]({target})"
        friendly = label or target.lstrip(":")
        return friendly

    return _ZIM_LINK_RE.sub(_replace, text)


def _slugify_anchor(text: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower())
    cleaned = cleaned.strip("-")
    return cleaned or "section"


def _asset_url(path: str, token: Optional[str]) -> str:
    rel = path.strip().lstrip("/")
    safe = quote(rel, safe="/")
    base = f"/asset/{safe}"
    if token:
        joiner = "&" if "?" in base else "?"
        return f"{base}{joiner}token={quote(token)}"
    return base


def _rewrite_image_src(html: str, root: Path, page_path: Path, token: Optional[str]) -> str:
    page_dir = page_path.parent
    root_resolved = root.resolve()

    def _normalize_src_path(src_value: str) -> tuple[Optional[str], bool, bool]:
        raw = src_value.strip()
        if not raw:
            return None, False, False
        if raw.startswith("file://"):
            parsed = urlparse(raw)
            raw = unquote(parsed.path or "")
        if raw.startswith(("http://", "https://", "data:")):
            return raw, True, False
        if raw.startswith("/asset/"):
            asset_path = raw.split("?", 1)[0]
            asset_rel = asset_path[len("/asset/") :]
            try:
                asset_candidate = Path("/" + asset_rel.lstrip("/"))
            except Exception:
                return raw, False, False
            root_parts = root_resolved.parts
            asset_parts = asset_candidate.parts
            if asset_parts[: len(root_parts)] == root_parts:
                rel = Path(*asset_parts[len(root_parts) :]).as_posix()
                return rel, False, True
            return raw, False, False
        try:
            src_path = Path(raw)
        except Exception:
            return raw, False, False
        if src_path.is_absolute():
            try:
                resolved = src_path.resolve()
            except Exception:
                return raw, False, False
            if resolved == root_resolved or root_resolved in resolved.parents:
                rel = resolved.relative_to(root_resolved).as_posix()
                return rel, False, True
            return raw, False, False
        return raw, False, False

    def _replacer(match: re.Match) -> str:
        prefix, src, suffix = match.groups()
        normalized, is_external, is_root_relative = _normalize_src_path(src)
        if normalized is None:
            return match.group(0)
        if is_external:
            return match.group(0)
        if normalized.startswith("/asset/"):
            if token and "token=" not in normalized:
                joiner = "&" if "?" in normalized else "?"
                normalized = f"{normalized}{joiner}token={quote(token)}"
            return f"{prefix}{normalized}{suffix}"
        if is_root_relative:
            rel = normalized.lstrip("/")
        elif normalized.startswith("/"):
            rel = normalized.lstrip("/")
        else:
            rel = (page_dir / normalized).as_posix()
        return f"{prefix}{_asset_url(rel, token)}{suffix}"

    return _IMAGE_SRC_RE.sub(_replacer, html)


def _render_print_document(
    *,
    title: str,
    body_html: str,
    auto_print: bool,
    show_header: bool,
    path_label: Optional[str],
    token: Optional[str],
) -> str:
    root = _get_vault_root()
    template = _load_print_template(root)
    return template.render(
        title=title,
        body_html=Markup(body_html),
        auto_print=auto_print,
        show_header=show_header,
        path_label=path_label,
        generated_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        print_css_url=_print_css_url(token),
    )


def _render_single_page_html(
    root: Path,
    page_file: Path,
    token: Optional[str],
    *,
    include_toc: bool = False,
    toc_title: Optional[str] = None,
) -> str:
    content = page_file.read_text(encoding="utf-8")
    html = _render_markdown_html(content)
    html = _rewrite_image_src(html, root, page_file, token)
    rel_path = page_file.relative_to(root).as_posix()
    anchor = _slugify_anchor(rel_path)
    section = f"<section class=\"stillpoint-section\" id=\"{anchor}\">{html}</section>"
    if not include_toc:
        return section
    heading = toc_title or "Table of Contents"
    toc_html = (
        "<nav class=\"stillpoint-toc\">"
        f"<h2>{heading}</h2>"
        f"<ul><li><a href=\"#{anchor}\">{page_file.stem}</a></li></ul>"
        "</nav>"
        "<div class=\"stillpoint-page-break\"></div>"
    )
    return toc_html + section


def _iter_tree_pages(root: Path, directory: Path, depth: int, max_depth: Optional[int]) -> list[tuple[Path, int, bool]]:
    pages: list[tuple[Path, int, bool]] = []
    index_page = files._resolve_page_for_read(directory)
    if index_page.exists():
        pages.append((index_page, depth, True))

    def _sort_key(path: Path) -> str:
        return path.stem.lower()

    child_pages = []
    for item in directory.iterdir():
        if not item.is_file():
            continue
        if not files.is_page_suffix(item.suffix):
            continue
        if index_page.exists() and item.resolve() == index_page.resolve():
            continue
        child_pages.append(item)
    for child in sorted(child_pages, key=_sort_key):
        pages.append((child, depth, False))

    if max_depth is not None and depth >= max_depth:
        return pages

    subfolders = []
    for item in directory.iterdir():
        if item.is_dir() and not item.name.startswith("."):
            subfolders.append(item)
    for sub in sorted(subfolders, key=lambda p: p.name.lower()):
        pages.extend(_iter_tree_pages(root, sub, depth + 1, max_depth))
    return pages


def _render_tree_html(
    root: Path,
    tree_root: Path,
    max_depth: Optional[int],
    token: Optional[str],
    *,
    title_override: Optional[str] = None,
    include_toc: bool = True,
    toc_title: Optional[str] = None,
) -> tuple[str, str]:
    pages = _iter_tree_pages(root, tree_root, depth=0, max_depth=max_depth)
    root_index_exists = False
    if pages:
        root_index_exists = pages[0][2] and pages[0][0].parent == tree_root

    base_level = 2 if root_index_exists else 1
    sections: list[str] = []
    toc_items: list[str] = []
    for idx, (page_file, depth, is_index) in enumerate(pages):
        try:
            content = page_file.read_text(encoding="utf-8")
        except OSError:
            continue
        html = _render_markdown_html(content)
        html = _rewrite_image_src(html, root, page_file, token)
        if root_index_exists and is_index and depth == 0:
            heading_level = 1
        else:
            heading_level = min(6, base_level + depth)
        title = page_file.stem
        rel_path = page_file.relative_to(root).as_posix()
        anchor = _slugify_anchor(rel_path)
        toc_indent = max(0, heading_level - 1) * 16
        toc_items.append(
            f"<li style=\"margin-left: {toc_indent}px;\"><a href=\"#{anchor}\">{title}</a></li>"
        )
        section_html = (
            f"<section class=\"stillpoint-section\" id=\"{anchor}\" data-path=\"{rel_path}\">"
            f"<h{heading_level}>{title}</h{heading_level}>"
            f"{html}"
            "</section>"
        )
        sections.append(section_html)
        if idx < len(pages) - 1:
            sections.append("<div class=\"stillpoint-page-break\"></div>")

    if include_toc and toc_items:
        heading = toc_title or "Table of Contents"
        toc_html = (
            "<nav class=\"stillpoint-toc\">"
            f"<h2>{heading}</h2>"
            "<ul>"
            + "".join(toc_items)
            + "</ul>"
            "</nav>"
            "<div class=\"stillpoint-page-break\"></div>"
        )
        sections.insert(0, toc_html)

    doc_title = title_override or tree_root.name or "StillPoint Print"
    return "".join(sections), doc_title


def _vault_relative_path(path: str) -> str:
    cleaned = path.strip().replace("\\", "/").lstrip("/")
    return f"/{cleaned}" if cleaned else "/"


def _get_vault_root() -> Path:
    try:
        return vault_state.get_root()
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _store_attachment(root: Path, page_path: str, upload: UploadFile, use_local_ops: bool) -> str:
    def _sha256_stream(stream) -> tuple[str, int]:
        hasher = hashlib.sha256()
        total = 0
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            hasher.update(chunk)
        return hasher.hexdigest(), total

    def _sha256_file(path: Path) -> tuple[str, int]:
        hasher = hashlib.sha256()
        total = 0
        with path.open("rb") as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                hasher.update(chunk)
        return hasher.hexdigest(), total

    filename = Path(upload.filename).name
    if not filename:
        raise HTTPException(status_code=400, detail="Attachment filename is required")
    page_parts = Path(page_path.lstrip("/"))
    attachment_rel = page_parts.parent / filename
    attachment_normalized = f"/{attachment_rel.as_posix()}" if attachment_rel.as_posix() else f"/{filename}"
    dest_path = root / attachment_rel
    try:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _log_attachment(f"Failed to create attachment folder {dest_path.parent}: {exc}")
        raise HTTPException(
            status_code=500,
            detail=_format_file_op_detail(f"Create attachment folder failed for {attachment_normalized}", exc),
        ) from exc
    unchanged = False
    incoming_hash = ""
    incoming_size = -1
    if dest_path.exists():
        try:
            upload.file.seek(0)
            incoming_hash, incoming_size = _sha256_stream(upload.file)
            upload.file.seek(0)
            existing_hash, existing_size = _sha256_file(dest_path)
            if existing_size == incoming_size and existing_hash == incoming_hash:
                unchanged = True
        except OSError as exc:
            _log_attachment(f"Failed to compare existing attachment {attachment_normalized}: {exc}")
        except Exception:
            pass
    log_msg = f"receive file {attachment_normalized} to vault {dest_path}"
    if unchanged:
        log_msg += " (unchanged)"
        if use_local_ops:
            log_msg += " (server==client)"
    else:
        try:
            upload.file.seek(0)
            with dest_path.open("wb") as dest:
                shutil.copyfileobj(upload.file, dest)
        except OSError as exc:
            _log_attachment(f"Failed to persist {attachment_normalized}: {exc}")
            raise HTTPException(
                status_code=500,
                detail=_format_file_op_detail(f"Persist attachment failed for {attachment_normalized}", exc),
            ) from exc
        if use_local_ops:
            log_msg += " (server==client)"
    _log_attachment(log_msg)
    config.upsert_attachment_entry(page_path, attachment_normalized, str(dest_path))
    return attachment_normalized


def _remove_attachment_copy(root: Path, attachment_path: str, use_local_ops: bool) -> None:
    target = root / attachment_path.lstrip("/")
    if not target.exists():
        _log_attachment(f"delete file {attachment_path} missing at {target}")
        return
    try:
        target.unlink()
        msg = f"delete file {attachment_path} from vault {target}"
        if use_local_ops:
            msg += " (server==client)"
        _log_attachment(msg)
    except OSError as exc:
        _log_attachment(f"Failed to delete file {attachment_path}: {exc}")


# Function to render the link
def render_link(label, target):
    # Display only the label as a hyperlink
    hyperlink = f'<a href="#" title="{target}">{label}</a>'
    return hyperlink


def get_app() -> FastAPI:
    return app


def run_server(
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    vaults_root: Optional[str] = None,
    insecure: bool = False,
) -> None:
    """Run the API server in standalone mode."""
    resolved_vaults_root = vaults_root if vaults_root is not None else os.getenv("STILLPOINT_VAULTS_ROOT", "vaults")
    if not resolved_vaults_root:
        print("Error: --vaults-root must be specified or STILLPOINT_VAULTS_ROOT environment variable set.")
        sys.exit(1)

    set_vaults_root(resolved_vaults_root)
    vaults_root_path = _ensure_vaults_root()
    try:
        _raise_for_homebase_permission_faults(vaults_root_path)
    except RuntimeError:
        sys.exit(1)

    # Set STILLPOINT_INSECURE environment variable if --insecure flag is used
    # This is checked by the lifespan handler during FastAPI startup
    if insecure:
        os.environ["STILLPOINT_INSECURE"] = "1"

    # Standalone server requires explicit password configuration or --insecure flag
    if not SERVER_ADMIN_PASSWORD and not insecure:
        print(f"\n{_ANSI_BLUE}{'=' * 80}{_ANSI_RESET}")
        print(f"{_ANSI_BLUE}⚠️  SECURITY WARNING: SERVER_ADMIN_PASSWORD not set!{_ANSI_RESET}")
        print(f"{_ANSI_BLUE}{'=' * 80}{_ANSI_RESET}")
        print(f"{_ANSI_BLUE}This server requires SERVER_ADMIN_PASSWORD for vault operations.{_ANSI_RESET}")
        print(f"{_ANSI_BLUE}Without it, anyone can create/list vaults on this server.{_ANSI_RESET}\n")
        print("Set it with:")
        print("  export SERVER_ADMIN_PASSWORD='your-secure-password'")
        print(f"  python -m sp.server.api --host {host} --port {port}\n")
        print("To run without password protection (NOT RECOMMENDED), use:")
        print(f"  python -m sp.server.api --host {host} --port {port} --insecure\n")
        print(f"{_ANSI_BLUE}{'=' * 80}{_ANSI_RESET}\n")
        print("Exiting. Set SERVER_ADMIN_PASSWORD or use --insecure flag.")
        sys.exit(1)

    # Show massive warning if running in insecure mode
    if insecure and not SERVER_ADMIN_PASSWORD:
        print(f"\n{_ANSI_BLUE}{'🚨' * 40}{_ANSI_RESET}")
        print(f"{_ANSI_BLUE}{'=' * 80}{_ANSI_RESET}")
        print(f"{_ANSI_BLUE}🚨 🚨 🚨  DANGER: RUNNING IN INSECURE MODE  🚨 🚨 🚨{_ANSI_RESET}")
        print(f"{_ANSI_BLUE}{'=' * 80}{_ANSI_RESET}")
        print(f"{_ANSI_BLUE}--insecure flag is set - SERVER_ADMIN_PASSWORD is disabled!{_ANSI_RESET}")
        print(f"{_ANSI_BLUE}ANYONE can create/list/delete vaults on this server without authentication!{_ANSI_RESET}")
        print(f"{_ANSI_BLUE}This is EXTREMELY DANGEROUS and should NEVER be used in production!{_ANSI_RESET}")
        print(f"{_ANSI_BLUE}Only use this for local development/testing on trusted networks.{_ANSI_RESET}\n")
        print("To secure this server properly:")
        print("  1. Remove --insecure flag")
        print("  2. Set SERVER_ADMIN_PASSWORD='your-secure-password'")
        print(f"  3. Restart: python -m sp.server.api --host {host} --port {port}\n")
        print(f"{_ANSI_BLUE}{'=' * 80}{_ANSI_RESET}")
        print(f"{_ANSI_BLUE}{'🚨' * 40}{_ANSI_RESET}\n")

    print(f"\n{_ANSI_BLUE}=== StillPoint API Server ==={_ANSI_RESET}")
    print(f"{_ANSI_BLUE}Version: {STILLPOINT_VERSION}{_ANSI_RESET}")
    print(f"{_ANSI_BLUE}Starting server on http://{host}:{port}{_ANSI_RESET}")
    print(f"{_ANSI_BLUE}API docs: http://{host}:{port}/docs{_ANSI_RESET}")
    print(f"{_ANSI_BLUE}Auth enabled: {AUTH_ENABLED}{_ANSI_RESET}")
    if SERVER_ADMIN_PASSWORD:
        print(f"{_ANSI_BLUE}Server admin password: SET ✓{_ANSI_RESET}")
    elif insecure:
        print(f"{_ANSI_BLUE}Server admin password: DISABLED (--insecure flag) ⚠️ ⚠️ ⚠️{_ANSI_RESET}")
    print(f"{_ANSI_BLUE}Vaults root: {vaults_root_path}{_ANSI_RESET}\n")

    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="StillPoint API Server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--vaults-root",
        default=os.getenv("STILLPOINT_VAULTS_ROOT", "vaults"),
        help="Base folder where vaults are stored",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Allow server to start without SERVER_ADMIN_PASSWORD (NOT RECOMMENDED for production)",
    )
    parser.add_argument(
        "--run-gc",
        action="store_true",
        help="Run the Home Base retention janitor instead of starting the API server.",
    )
    parser.add_argument(
        "--homebase-seed",
        action="store_true",
        help="Seed an existing Homebase vault from a plaintext staging folder on the server.",
    )
    parser.add_argument(
        "--homebase-create-and-seed",
        action="store_true",
        help="Create a Homebase vault on the server and seed it from a plaintext staging folder.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="With --run-gc or Homebase seed/create modes, log what would happen without writing.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="With --run-gc, bypass the janitor interval gate. With --homebase-create-and-seed, allow reusing a non-empty Homebase vault directory.",
    )
    parser.add_argument("--source", help="Plaintext staging folder for Homebase seed/create modes.")
    parser.add_argument("--vault-id", help="Existing or explicit Homebase vault id for Homebase seed/create modes.")
    parser.add_argument("--username", help="Admin username for --homebase-create-and-seed.")
    parser.add_argument("--password", help="Admin password for --homebase-create-and-seed.")
    parser.add_argument("--passphrase", help="Homebase encryption passphrase for seed/create modes.")
    parser.add_argument("--device-id", default="server-seed", help="Device id recorded in Homebase seeded manifests.")
    parser.add_argument("--vault-name", default="", help="Optional Homebase vault name for seed/create modes.")
    parser.add_argument(
        "--overwrite-latest",
        action="store_true",
        help="With --homebase-seed, allow replacing an existing latest checkpoint pointer.",
    )
    args = parser.parse_args()

    selected_modes = sum(
        1 for enabled in (args.run_gc, args.homebase_seed, args.homebase_create_and_seed) if enabled
    )
    if selected_modes > 1:
        parser.error("choose only one of --run-gc, --homebase-seed, or --homebase-create-and-seed")

    if (args.dry_run or args.force) and not (args.run_gc or args.homebase_create_and_seed or args.homebase_seed):
        parser.error("--dry-run and --force require --run-gc, --homebase-seed, or --homebase-create-and-seed")

    if args.run_gc:
        raise SystemExit(
            homebase_gc.run_homebase_gc(
                vaults_root=args.vaults_root,
                force=bool(args.force),
                dry_run=True if args.dry_run else None,
            )
        )

    if args.homebase_seed:
        if not args.vault_id:
            parser.error("--vault-id is required with --homebase-seed")
        if not args.source:
            parser.error("--source is required with --homebase-seed")
        if not args.passphrase:
            parser.error("--passphrase is required with --homebase-seed")
        try:
            result = seed_homebase_vault(
                vaults_root=Path(args.vaults_root).expanduser().resolve(),
                vault_id=str(args.vault_id).strip(),
                source_root=Path(str(args.source)).expanduser().resolve(),
                passphrase=args.passphrase,
                device_id=str(args.device_id or "server-seed").strip() or "server-seed",
                overwrite_latest=bool(args.overwrite_latest),
                vault_name=str(args.vault_name or "").strip() or None,
                dry_run=bool(args.dry_run),
            )
        except Exception as exc:
            print(f"Homebase seed failed: {exc}", file=sys.stderr)
            raise SystemExit(1)
        print(
            json.dumps(
                result,
                indent=2,
                sort_keys=True,
            )
        )
        raise SystemExit(0)

    if args.homebase_create_and_seed:
        if not args.source:
            parser.error("--source is required with --homebase-create-and-seed")
        if not args.username:
            parser.error("--username is required with --homebase-create-and-seed")
        if not args.password:
            parser.error("--password is required with --homebase-create-and-seed")
        if not args.passphrase:
            parser.error("--passphrase is required with --homebase-create-and-seed")
        vaults_root = Path(args.vaults_root).expanduser().resolve()
        source_root = Path(str(args.source)).expanduser().resolve()
        if args.dry_run:
            print(
                json.dumps(
                    {
                        "dry_run": True,
                        "vaults_root": str(vaults_root),
                        "source_root": str(source_root),
                        "vault_id": str(args.vault_id or "").strip() or "<generated>",
                        "username": str(args.username).strip(),
                        "vault_name": str(args.vault_name or "").strip(),
                        "device_id": str(args.device_id or "server-seed").strip() or "server-seed",
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            raise SystemExit(0)
        try:
            created = create_homebase_vault(
                vaults_root=vaults_root,
                username=str(args.username).strip(),
                password=args.password,
                vault_name=str(args.vault_name or "").strip(),
                vault_id=str(args.vault_id or "").strip(),
                force=bool(args.force),
            )
            seeded = seed_homebase_vault(
                vaults_root=vaults_root,
                vault_id=created["vault_id"],
                source_root=source_root,
                passphrase=args.passphrase,
                device_id=str(args.device_id or "server-seed").strip() or "server-seed",
                overwrite_latest=True,
                vault_name=str(args.vault_name or "").strip() or None,
                dry_run=False,
            )
        except Exception as exc:
            print(f"Homebase create+seed failed: {exc}", file=sys.stderr)
            raise SystemExit(1)
        print(json.dumps({"created": created, "seeded": seeded}, indent=2, sort_keys=True))
        raise SystemExit(0)

    run_server(
        host=args.host,
        port=args.port,
        vaults_root=args.vaults_root,
        insecure=args.insecure,
    )
