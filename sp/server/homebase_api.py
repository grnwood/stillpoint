from __future__ import annotations

import hashlib
import json
import re
import secrets
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Body, Depends, FastAPI, Header, HTTPException, Response
from pydantic import BaseModel

from sp.logging_flags import log_enabled


_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_HASH_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_LOG_HOMEBASE = log_enabled("homebase_sync")
_ANSI_GREEN = "\033[92m"
_ANSI_RED = "\033[91m"
_ANSI_RESET = "\033[0m"
_ACCESS_TTL_SECONDS = 3600
_REFRESH_TTL_SECONDS = 30 * 24 * 3600


class HomebaseBootstrapCreatePayload(BaseModel):
    username: str
    password: str
    vault_name: Optional[str] = None


class HomebaseBootstrapConnectPayload(BaseModel):
    vault_id: str
    username: str
    password: str


class HomebaseBootstrapRefreshPayload(BaseModel):
    vault_id: str
    refresh_token: str


class HomebaseUserCreatePayload(BaseModel):
    username: str
    password: str
    role: Optional[str] = None
    perm: Optional[str] = None


class HomebaseUserUpdatePayload(BaseModel):
    username: Optional[str] = None
    role: Optional[str] = None
    perm: Optional[str] = None
    password: Optional[str] = None


class HomebaseChangePasswordPayload(BaseModel):
    old_password: str
    new_password: str


def _log_server(message: str) -> None:
    if _LOG_HOMEBASE:
        color = _ANSI_RED if "conflict" in str(message).lower() else _ANSI_GREEN
        print(f"{color}[HomebaseServer] {message}{_ANSI_RESET}")


def _validate_id(name: str, value: str) -> str:
    cleaned = (value or "").strip()
    if not _ID_PATTERN.match(cleaned):
        raise HTTPException(status_code=400, detail=f"Invalid {name}")
    return cleaned


def _validate_hash(name: str, value: str) -> str:
    cleaned = (value or "").strip().lower()
    if not _HASH_PATTERN.match(cleaned):
        raise HTTPException(status_code=400, detail=f"Invalid {name}")
    return cleaned


def _utc_now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _utc_now_epoch() -> int:
    return int(time.time())


def _write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f"{path.suffix}.tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
    tmp.replace(path)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise HTTPException(status_code=404, detail="Not found")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Corrupt JSON: {exc}") from exc


def _read_json_default(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return dict(default)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else dict(default)
    except Exception:
        return dict(default)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    _write_bytes(path, json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _parse_bearer_token(header_value: Optional[str]) -> str:
    raw = (header_value or "").strip()
    if not raw:
        return ""
    if raw.lower().startswith("bearer "):
        return raw[7:].strip()
    return ""


def register_homebase_routes(
    app: FastAPI,
    *,
    ensure_vaults_root: Callable[[], Path],
    admin_dependency,
) -> None:
    _log_server("routes registered")
    ph = PasswordHasher()

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

    def _normalize_auth_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        if not isinstance(payload, dict):
            return {}, False
        changed = False
        if "users" not in payload:
            username = str(payload.get("username") or "").strip()
            password_hash = str(payload.get("password_hash") or "")
            if username and password_hash:
                created_at = str(payload.get("created_at") or _utc_now_iso())
                payload = {
                    "schema_version": 2,
                    "created_at": created_at,
                    "users": {
                        username: {
                            "username": username,
                            "password_hash": password_hash,
                            "role": "admin",
                            "perm": "read_write",
                            "created_at": created_at,
                            "last_login_at": None,
                            "last_password_change_at": created_at,
                        }
                    },
                }
                changed = True
            else:
                return {}, False

        if payload.get("schema_version") != 2:
            payload["schema_version"] = 2
            changed = True

        users = payload.get("users")
        if not isinstance(users, dict):
            payload["users"] = {}
            users = payload["users"]
            changed = True

        created_at = str(payload.get("created_at") or _utc_now_iso())
        payload["created_at"] = created_at
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
                record["created_at"] = created_at
                changed = True
            if "last_login_at" not in record:
                record["last_login_at"] = None
                changed = True
            if not record.get("last_password_change_at"):
                record["last_password_change_at"] = record.get("created_at") or created_at
                changed = True
        return payload, changed

    def _load_auth(base: Path) -> dict[str, Any]:
        payload = _read_json_default(_auth_path(base), {})
        normalized, changed = _normalize_auth_payload(payload)
        if normalized and changed:
            _write_json(_auth_path(base), normalized)
        return normalized

    def _get_user_record(base: Path, username: str) -> Optional[dict[str, Any]]:
        payload = _load_auth(base)
        users = payload.get("users")
        if not isinstance(users, dict):
            return None
        record = users.get(username)
        return record if isinstance(record, dict) else None

    def _vault_base(vault_id: str) -> Path:
        validated = _validate_id("vault_id", vault_id)
        return ensure_vaults_root() / "homebase" / validated

    def _auth_path(base: Path) -> Path:
        return base / "auth" / "auth.json"

    def _tokens_path(base: Path) -> Path:
        return base / "auth" / "tokens.json"

    def _meta_path(base: Path) -> Path:
        return base / "meta.json"

    def _load_tokens(base: Path) -> dict[str, Any]:
        return _read_json_default(
            _tokens_path(base),
            {
                "schema_version": 1,
                "access_tokens": {},
                "refresh_tokens": {},
            },
        )

    def _save_tokens(base: Path, tokens: dict[str, Any]) -> None:
        _write_json(_tokens_path(base), tokens)

    def _cleanup_tokens(tokens: dict[str, Any]) -> None:
        now = _utc_now_epoch()
        for key in ("access_tokens", "refresh_tokens"):
            bucket = tokens.get(key)
            if not isinstance(bucket, dict):
                tokens[key] = {}
                continue
            expired = []
            for token, meta in bucket.items():
                exp = int(meta.get("exp", 0)) if isinstance(meta, dict) else 0
                if exp <= now:
                    expired.append(token)
            for token in expired:
                bucket.pop(token, None)

    def _issue_tokens(base: Path, username: str) -> dict[str, str]:
        tokens = _load_tokens(base)
        _cleanup_tokens(tokens)
        now = _utc_now_epoch()
        access = secrets.token_urlsafe(32)
        refresh = secrets.token_urlsafe(40)
        access_bucket = tokens.setdefault("access_tokens", {})
        refresh_bucket = tokens.setdefault("refresh_tokens", {})
        access_bucket[access] = {
            "username": username,
            "exp": now + _ACCESS_TTL_SECONDS,
            "created_at": _utc_now_iso(),
        }
        refresh_bucket[refresh] = {
            "username": username,
            "exp": now + _REFRESH_TTL_SECONDS,
            "created_at": _utc_now_iso(),
        }
        _save_tokens(base, tokens)
        return {
            "access_token": access,
            "refresh_token": refresh,
            "token_type": "bearer",
        }

    def _verify_access_token(base: Path, token: str) -> Optional[str]:
        if not token:
            return None
        tokens = _load_tokens(base)
        _cleanup_tokens(tokens)
        bucket = tokens.get("access_tokens", {})
        meta = bucket.get(token) if isinstance(bucket, dict) else None
        username = None
        if isinstance(meta, dict):
            username = str(meta.get("username") or "").strip() or None
        _save_tokens(base, tokens)
        return username

    def _require_homebase_auth(vault_id: str, authorization: Optional[str] = Header(default=None)) -> dict[str, str]:
        base = _vault_base(vault_id)
        auth_file = _auth_path(base)
        if not auth_file.exists():
            return {"username": "anonymous", "role": "admin", "perm": "read_write", "can_write": True}
        token = _parse_bearer_token(authorization)
        username = _verify_access_token(base, token)
        if not username:
            raise HTTPException(status_code=401, detail="Not authenticated")
        record = _get_user_record(base, username)
        if not record:
            raise HTTPException(status_code=401, detail="Not authenticated")
        role = _normalize_role(record.get("role"))
        perm = _normalize_perm(record.get("perm"), role)
        can_write = role == "admin" or perm == "read_write"
        return {"username": username, "role": role, "perm": perm, "can_write": can_write}

    def _require_homebase_admin(vault_id: str, authorization: Optional[str] = Header(default=None)) -> dict[str, str]:
        user = _require_homebase_auth(vault_id, authorization)
        if user.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Admin access required")
        return user

    def _require_homebase_write(vault_id: str, authorization: Optional[str] = Header(default=None)) -> dict[str, str]:
        user = _require_homebase_auth(vault_id, authorization)
        if not user.get("can_write"):
            raise HTTPException(status_code=403, detail="User does not have write permission")
        return user

    @app.get("/v1/homebase/bootstrap/vaults")
    def homebase_bootstrap_list(_admin=Depends(admin_dependency)) -> dict[str, Any]:
        root = ensure_vaults_root() / "homebase"
        root.mkdir(parents=True, exist_ok=True)
        vaults = []
        for entry in sorted(root.iterdir(), key=lambda p: p.name.lower()):
            if not entry.is_dir():
                continue
            vault_id = entry.name
            try:
                _validate_id("vault_id", vault_id)
            except HTTPException:
                continue
            meta = _read_json_default(_meta_path(entry), {})
            vaults.append(
                {
                    "vault_id": vault_id,
                    "vault_name": str(meta.get("vault_name") or ""),
                    "created_at": str(meta.get("created_at") or ""),
                }
            )
        _log_server(f"GET /bootstrap/vaults -> {len(vaults)} vault(s)")
        return {"vaults": vaults}

    @app.post("/v1/homebase/bootstrap/create")
    def homebase_bootstrap_create(payload: HomebaseBootstrapCreatePayload, _admin=Depends(admin_dependency)) -> dict[str, Any]:
        username = payload.username.strip()
        password = payload.password
        if not username or not password:
            raise HTTPException(status_code=400, detail="Username and password are required")
        vault_id = str(uuid.uuid4())
        base = _vault_base(vault_id)
        base.mkdir(parents=True, exist_ok=True)
        now = _utc_now_iso()
        auth_payload = {
            "schema_version": 2,
            "created_at": now,
            "users": {
                username: {
                    "username": username,
                    "password_hash": ph.hash(password),
                    "role": "admin",
                    "perm": "read_write",
                    "created_at": now,
                    "last_login_at": None,
                    "last_password_change_at": now,
                }
            },
        }
        _write_json(_auth_path(base), auth_payload)
        _write_json(
            _meta_path(base),
            {
                "schema_version": 1,
                "vault_id": vault_id,
                "vault_name": (payload.vault_name or "").strip(),
                "created_at": _utc_now_iso(),
            },
        )
        tokens = _issue_tokens(base, username)
        _log_server(f"POST /bootstrap/create vault_id={vault_id} username={username}")
        return {
            "vault_id": vault_id,
            **tokens,
        }

    @app.post("/v1/homebase/bootstrap/connect")
    def homebase_bootstrap_connect(payload: HomebaseBootstrapConnectPayload) -> dict[str, Any]:
        vault_id = _validate_id("vault_id", payload.vault_id)
        username = payload.username.strip()
        password = payload.password
        if not username or not password:
            raise HTTPException(status_code=400, detail="Username and password are required")
        base = _vault_base(vault_id)
        auth_payload = _load_auth(base)
        if not auth_payload:
            raise HTTPException(status_code=404, detail="Homebase vault auth is not configured")
        users = auth_payload.get("users", {})
        record = users.get(username) if isinstance(users, dict) else None
        if not isinstance(record, dict):
            raise HTTPException(status_code=401, detail="Invalid credentials")
        password_hash = str(record.get("password_hash") or "")
        if not password_hash:
            raise HTTPException(status_code=401, detail="Invalid credentials")
        try:
            ph.verify(password_hash, password)
        except VerifyMismatchError:
            raise HTTPException(status_code=401, detail="Invalid credentials")
        record["last_login_at"] = _utc_now_iso()
        _write_json(_auth_path(base), auth_payload)
        tokens = _issue_tokens(base, username)
        _log_server(f"POST /bootstrap/connect vault_id={vault_id} username={username}")
        return {
            "vault_id": vault_id,
            **tokens,
        }

    @app.post("/v1/homebase/bootstrap/refresh")
    def homebase_bootstrap_refresh(payload: HomebaseBootstrapRefreshPayload) -> dict[str, Any]:
        vault_id = _validate_id("vault_id", payload.vault_id)
        refresh_token = payload.refresh_token.strip()
        if not refresh_token:
            raise HTTPException(status_code=400, detail="refresh_token is required")
        base = _vault_base(vault_id)
        tokens = _load_tokens(base)
        _cleanup_tokens(tokens)
        refresh_bucket = tokens.get("refresh_tokens", {})
        meta = refresh_bucket.get(refresh_token) if isinstance(refresh_bucket, dict) else None
        username = str(meta.get("username") or "").strip() if isinstance(meta, dict) else ""
        if not username:
            _save_tokens(base, tokens)
            raise HTTPException(status_code=401, detail="Invalid refresh token")
        if not _get_user_record(base, username):
            refresh_bucket.pop(refresh_token, None)
            _save_tokens(base, tokens)
            raise HTTPException(status_code=401, detail="Invalid refresh token")
        # Keep refresh tokens reusable for their full TTL so auth can recover
        # after restarts or concurrent refresh attempts. Extend the token window
        # on successful use to preserve a 30-day inactivity timeout.
        now = _utc_now_epoch()
        refresh_bucket[refresh_token] = {
            "username": username,
            "exp": now + _REFRESH_TTL_SECONDS,
            "created_at": _utc_now_iso(),
        }
        _save_tokens(base, tokens)
        fresh = _issue_tokens(base, username)
        fresh["refresh_token"] = refresh_token
        _log_server(f"POST /bootstrap/refresh vault_id={vault_id} username={username}")
        return {
            "vault_id": vault_id,
            **fresh,
        }

    @app.get("/v1/homebase/{vault_id}/users")
    def homebase_list_users(vault_id: str, _user=Depends(_require_homebase_admin)) -> dict[str, Any]:
        base = _vault_base(vault_id)
        auth_payload = _load_auth(base)
        users = auth_payload.get("users", {})
        if not isinstance(users, dict):
            users = {}
        results: list[dict[str, Any]] = []
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

    @app.post("/v1/homebase/{vault_id}/users")
    def homebase_create_user(
        vault_id: str,
        payload: HomebaseUserCreatePayload,
        _user=Depends(_require_homebase_admin),
    ) -> dict[str, Any]:
        username = payload.username.strip()
        password = payload.password
        if not username or not password:
            raise HTTPException(status_code=400, detail="Username and password are required")
        base = _vault_base(vault_id)
        auth_payload = _load_auth(base)
        users = auth_payload.get("users")
        if not isinstance(users, dict):
            users = {}
            auth_payload["users"] = users
        if username in users:
            raise HTTPException(status_code=409, detail="User already exists")
        now = _utc_now_iso()
        role = _normalize_role(payload.role)
        perm = _normalize_perm(payload.perm, role)
        users[username] = {
            "username": username,
            "password_hash": ph.hash(password),
            "role": role,
            "perm": perm,
            "created_at": now,
            "last_login_at": None,
            "last_password_change_at": now,
        }
        _write_json(_auth_path(base), auth_payload)
        _log_server(f"POST /users vault_id={vault_id} username={username}")
        return {"ok": True}

    @app.patch("/v1/homebase/{vault_id}/users/{username}")
    def homebase_update_user(
        vault_id: str,
        username: str,
        payload: HomebaseUserUpdatePayload,
        _user=Depends(_require_homebase_admin),
    ) -> dict[str, Any]:
        base = _vault_base(vault_id)
        auth_payload = _load_auth(base)
        users = auth_payload.get("users")
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
            record["password_hash"] = ph.hash(payload.password)
            record["last_password_change_at"] = _utc_now_iso()
        _write_json(_auth_path(base), auth_payload)
        return {"ok": True}

    @app.delete("/v1/homebase/{vault_id}/users/{username}")
    def homebase_delete_user(vault_id: str, username: str, _user=Depends(_require_homebase_admin)) -> dict[str, Any]:
        base = _vault_base(vault_id)
        auth_payload = _load_auth(base)
        users = auth_payload.get("users")
        if not isinstance(users, dict):
            raise HTTPException(status_code=404, detail="User not found")
        if username not in users:
            raise HTTPException(status_code=404, detail="User not found")
        current_user = str((_user or {}).get("username") or "")
        if username == current_user:
            raise HTTPException(status_code=400, detail="Cannot delete the currently logged-in user")
        remaining_admins = [
            name
            for name, record in users.items()
            if name != username and _normalize_role((record or {}).get("role")) == "admin"
        ]
        if not remaining_admins:
            raise HTTPException(status_code=400, detail="Cannot delete the last admin user")
        users.pop(username, None)
        _write_json(_auth_path(base), auth_payload)
        _log_server(f"DELETE /users vault_id={vault_id} username={username}")
        return {"ok": True}

    @app.get("/v1/homebase/{vault_id}/auth/me")
    def homebase_auth_me(vault_id: str, _user=Depends(_require_homebase_auth)) -> dict[str, Any]:
        return dict(_user or {})

    @app.post("/v1/homebase/{vault_id}/auth/change")
    def homebase_auth_change(
        vault_id: str,
        payload: HomebaseChangePasswordPayload,
        _user=Depends(_require_homebase_auth),
    ) -> dict[str, Any]:
        base = _vault_base(vault_id)
        auth_payload = _load_auth(base)
        users = auth_payload.get("users")
        if not isinstance(users, dict):
            raise HTTPException(status_code=404, detail="User not found")
        username = str((_user or {}).get("username") or "").strip()
        if not username:
            raise HTTPException(status_code=401, detail="Not authenticated")
        record = users.get(username)
        if not isinstance(record, dict):
            raise HTTPException(status_code=404, detail="User not found")
        password_hash = str(record.get("password_hash") or "")
        if not password_hash:
            raise HTTPException(status_code=401, detail="Invalid credentials")
        try:
            ph.verify(password_hash, payload.old_password)
        except VerifyMismatchError:
            raise HTTPException(status_code=401, detail="Invalid credentials")
        record["password_hash"] = ph.hash(payload.new_password)
        record["last_password_change_at"] = _utc_now_iso()
        _write_json(_auth_path(base), auth_payload)
        _log_server(f"POST /auth/change vault_id={vault_id} username={username}")
        return {"ok": True}

    @app.get("/v1/homebase/{vault_id}/changes")
    def homebase_changes(
        vault_id: str,
        since: str | None = None,
        _user=Depends(_require_homebase_auth),
    ) -> dict[str, Any]:
        base = _vault_base(vault_id)
        _log_server(f"GET /changes vault_id={vault_id} since={since}")
        latest_path = base / "refs" / "latest.json"
        latest_checkpoint_id = None
        cursor = since or ""
        if latest_path.exists():
            payload = _read_json(latest_path)
            latest_checkpoint_id = payload.get("checkpoint_id")
            cursor = str(payload.get("updated_at") or _utc_now_iso())
        return {
            "cursor": cursor,
            "latest_checkpoint_id": latest_checkpoint_id,
            "required_objects": [],
            "tombstones": [],
        }

    @app.get("/v1/homebase/{vault_id}/latest")
    def homebase_get_latest(vault_id: str, _user=Depends(_require_homebase_auth)) -> dict[str, Any]:
        base = _vault_base(vault_id)
        _log_server(f"GET /latest vault_id={vault_id}")
        path = base / "refs" / "latest.json"
        if not path.exists():
            _log_server(f"GET /latest vault_id={vault_id} -> 404 (no latest)")
            raise HTTPException(status_code=404, detail="No latest checkpoint")
        _log_server(f"GET /latest vault_id={vault_id} -> 200")
        return _read_json(path)

    @app.put("/v1/homebase/{vault_id}/latest")
    def homebase_put_latest(vault_id: str, payload: dict[str, Any], _user=Depends(_require_homebase_write)) -> dict[str, Any]:
        base = _vault_base(vault_id)
        checkpoint_id = _validate_hash("checkpoint_id", str(payload.get("checkpoint_id") or ""))
        username = str((_user or {}).get("username") or "")
        _log_server(f"PUT /latest vault_id={vault_id} checkpoint_id={checkpoint_id} user={username or 'unknown'}")
        path = base / "refs" / "latest.json"
        previous_checkpoint_id = ""
        if path.exists():
            try:
                existing = _read_json(path)
                previous_checkpoint_id = str(existing.get("checkpoint_id") or "")
            except Exception:
                previous_checkpoint_id = ""
        out = {
            "schema_version": 1,
            "vault_id": _validate_id("vault_id", vault_id),
            "checkpoint_id": checkpoint_id,
            "updated_at": _utc_now_iso(),
        }
        _write_bytes(path, json.dumps(out, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        if previous_checkpoint_id and previous_checkpoint_id != checkpoint_id:
            _log_server(
                f"CONFLICT_HINT latest_pointer_changed vault_id={vault_id} "
                f"previous={previous_checkpoint_id} new={checkpoint_id} user={username or 'unknown'}"
            )
        elif previous_checkpoint_id == checkpoint_id:
            _log_server(
                f"PUT /latest vault_id={vault_id} checkpoint_id={checkpoint_id} "
                f"note=idempotent"
            )
        _log_server(f"PUT /latest vault_id={vault_id} -> 200")
        return {"ok": True, "checkpoint_id": checkpoint_id}

    @app.get("/v1/homebase/{vault_id}/manifests/{manifest_id}")
    def homebase_get_manifest(vault_id: str, manifest_id: str, _user=Depends(_require_homebase_auth)) -> Response:
        base = _vault_base(vault_id)
        mid = _validate_hash("manifest_id", manifest_id)
        _log_server(f"GET /manifests vault_id={vault_id} manifest_id={mid}")
        path = base / "manifests" / mid[:2] / mid
        if not path.exists():
            _log_server(f"GET /manifests vault_id={vault_id} manifest_id={mid} -> 404")
            raise HTTPException(status_code=404, detail="Manifest not found")
        _log_server(f"GET /manifests vault_id={vault_id} manifest_id={mid} -> 200 bytes={path.stat().st_size}")
        return Response(content=path.read_bytes(), media_type="application/json")

    @app.put("/v1/homebase/{vault_id}/manifests/{manifest_id}")
    def homebase_put_manifest(
        vault_id: str,
        manifest_id: str,
        body: bytes = Body(..., media_type="application/octet-stream"),
        _user=Depends(_require_homebase_write),
    ) -> dict[str, Any]:
        base = _vault_base(vault_id)
        mid = _validate_hash("manifest_id", manifest_id)
        _log_server(f"PUT /manifests vault_id={vault_id} manifest_id={mid} bytes={len(body)}")
        expected = hashlib.sha256(body).hexdigest()
        if expected != mid:
            _log_server(
                f"PUT /manifests vault_id={vault_id} manifest_id={mid} -> 400 hash_mismatch expected={expected}"
            )
            raise HTTPException(status_code=400, detail="Manifest hash does not match manifest_id")
        path = base / "manifests" / mid[:2] / mid
        _write_bytes(path, body)
        device_id = ""
        entries_count = 0
        try:
            parsed = json.loads(body.decode("utf-8"))
            if isinstance(parsed, dict):
                device_id = str(parsed.get("device_id") or "")
                entries = parsed.get("entries")
                if isinstance(entries, dict):
                    entries_count = len(entries)
        except Exception:
            pass
        checkpoint_meta = {
            "schema_version": 1,
            "vault_id": _validate_id("vault_id", vault_id),
            "checkpoint_id": mid,
            "manifest_id": mid,
            "created_at": _utc_now_iso(),
            "device_id": device_id,
            "parent_checkpoint_id": None,
        }
        _write_bytes(
            base / "checkpoints" / f"{mid}.json",
            json.dumps(checkpoint_meta, sort_keys=True, separators=(",", ":")).encode("utf-8"),
        )
        _log_server(
            f"PUT /manifests vault_id={vault_id} manifest_id={mid} "
            f"device_id={device_id or 'unknown'} entries={entries_count} -> 200"
        )
        return {"ok": True, "manifest_id": mid}

    @app.head("/v1/homebase/{vault_id}/objects/{object_id}")
    def homebase_head_object(vault_id: str, object_id: str, _user=Depends(_require_homebase_auth)) -> Response:
        base = _vault_base(vault_id)
        oid = _validate_hash("object_id", object_id)
        _log_server(f"HEAD /objects vault_id={vault_id} object_id={oid}")
        path = base / "objects" / oid[:2] / oid
        if not path.exists():
            _log_server(f"HEAD /objects vault_id={vault_id} object_id={oid} -> 404")
            raise HTTPException(status_code=404, detail="Object not found")
        _log_server(f"HEAD /objects vault_id={vault_id} object_id={oid} -> 200")
        return Response(status_code=200)

    @app.get("/v1/homebase/{vault_id}/objects/{object_id}")
    def homebase_get_object(vault_id: str, object_id: str, _user=Depends(_require_homebase_auth)) -> Response:
        base = _vault_base(vault_id)
        oid = _validate_hash("object_id", object_id)
        _log_server(f"GET /objects vault_id={vault_id} object_id={oid}")
        path = base / "objects" / oid[:2] / oid
        if not path.exists():
            _log_server(f"GET /objects vault_id={vault_id} object_id={oid} -> 404")
            raise HTTPException(status_code=404, detail="Object not found")
        _log_server(f"GET /objects vault_id={vault_id} object_id={oid} -> 200 bytes={path.stat().st_size}")
        return Response(content=path.read_bytes(), media_type="application/octet-stream")

    @app.put("/v1/homebase/{vault_id}/objects/{object_id}")
    def homebase_put_object(
        vault_id: str,
        object_id: str,
        body: bytes = Body(..., media_type="application/octet-stream"),
        _user=Depends(_require_homebase_write),
    ) -> dict[str, Any]:
        base = _vault_base(vault_id)
        oid = _validate_hash("object_id", object_id)
        _log_server(f"PUT /objects vault_id={vault_id} object_id={oid} bytes={len(body)}")
        expected = hashlib.sha256(body).hexdigest()
        if expected != oid:
            _log_server(
                f"PUT /objects vault_id={vault_id} object_id={oid} -> 400 hash_mismatch expected={expected}"
            )
            raise HTTPException(status_code=400, detail="Object hash does not match object_id")
        path = base / "objects" / oid[:2] / oid
        if not path.exists():
            _write_bytes(path, body)
            _log_server(f"PUT /objects vault_id={vault_id} object_id={oid} stored=new")
        else:
            _log_server(f"PUT /objects vault_id={vault_id} object_id={oid} stored=existing")
        _log_server(f"PUT /objects vault_id={vault_id} object_id={oid} -> 200")
        return {"ok": True, "object_id": oid}
