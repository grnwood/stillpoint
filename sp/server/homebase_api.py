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


def _log_server(message: str) -> None:
    if _LOG_HOMEBASE:
        print(f"{_ANSI_GREEN}[HomebaseServer] {message}{_ANSI_RESET}")


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
            return {"username": "anonymous"}
        token = _parse_bearer_token(authorization)
        username = _verify_access_token(base, token)
        if not username:
            raise HTTPException(status_code=401, detail="Not authenticated")
        return {"username": username}

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
        auth_payload = {
            "schema_version": 1,
            "username": username,
            "password_hash": ph.hash(password),
            "created_at": _utc_now_iso(),
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
        auth_payload = _read_json_default(_auth_path(base), {})
        expected_user = str(auth_payload.get("username") or "").strip()
        password_hash = str(auth_payload.get("password_hash") or "")
        if not expected_user or not password_hash:
            raise HTTPException(status_code=404, detail="Homebase vault auth is not configured")
        if username != expected_user:
            raise HTTPException(status_code=401, detail="Invalid credentials")
        try:
            ph.verify(password_hash, password)
        except VerifyMismatchError:
            raise HTTPException(status_code=401, detail="Invalid credentials")
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
        refresh_bucket.pop(refresh_token, None)
        _save_tokens(base, tokens)
        fresh = _issue_tokens(base, username)
        _log_server(f"POST /bootstrap/refresh vault_id={vault_id} username={username}")
        return {
            "vault_id": vault_id,
            **fresh,
        }

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
    def homebase_put_latest(vault_id: str, payload: dict[str, Any], _user=Depends(_require_homebase_auth)) -> dict[str, Any]:
        base = _vault_base(vault_id)
        checkpoint_id = _validate_hash("checkpoint_id", str(payload.get("checkpoint_id") or ""))
        _log_server(f"PUT /latest vault_id={vault_id} checkpoint_id={checkpoint_id}")
        out = {
            "schema_version": 1,
            "vault_id": _validate_id("vault_id", vault_id),
            "checkpoint_id": checkpoint_id,
            "updated_at": _utc_now_iso(),
        }
        path = base / "refs" / "latest.json"
        _write_bytes(path, json.dumps(out, sort_keys=True, separators=(",", ":")).encode("utf-8"))
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
        _user=Depends(_require_homebase_auth),
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
        checkpoint_meta = {
            "schema_version": 1,
            "vault_id": _validate_id("vault_id", vault_id),
            "checkpoint_id": mid,
            "manifest_id": mid,
            "created_at": _utc_now_iso(),
            "device_id": "",
            "parent_checkpoint_id": None,
        }
        _write_bytes(
            base / "checkpoints" / f"{mid}.json",
            json.dumps(checkpoint_meta, sort_keys=True, separators=(",", ":")).encode("utf-8"),
        )
        _log_server(f"PUT /manifests vault_id={vault_id} manifest_id={mid} -> 200")
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
        _user=Depends(_require_homebase_auth),
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
