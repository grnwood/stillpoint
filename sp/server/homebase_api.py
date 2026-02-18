from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, Callable

from fastapi import Body, Depends, FastAPI, HTTPException, Response

from sp.logging_flags import log_enabled


_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_HASH_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_LOG_HOMEBASE = log_enabled("homebase_sync")
_ANSI_GREEN = "\033[92m"
_ANSI_RESET = "\033[0m"


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


def register_homebase_routes(
    app: FastAPI,
    *,
    user_dependency,
    ensure_vaults_root: Callable[[], Path],
) -> None:
    _log_server("routes registered")

    def _vault_base(vault_id: str) -> Path:
        validated = _validate_id("vault_id", vault_id)
        return ensure_vaults_root() / "homebase" / validated

    @app.get("/v1/homebase/{vault_id}/changes")
    def homebase_changes(
        vault_id: str,
        since: str | None = None,
        _user=Depends(user_dependency),
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
    def homebase_get_latest(vault_id: str, _user=Depends(user_dependency)) -> dict[str, Any]:
        base = _vault_base(vault_id)
        _log_server(f"GET /latest vault_id={vault_id}")
        path = base / "refs" / "latest.json"
        if not path.exists():
            _log_server(f"GET /latest vault_id={vault_id} -> 404 (no latest)")
            raise HTTPException(status_code=404, detail="No latest checkpoint")
        _log_server(f"GET /latest vault_id={vault_id} -> 200")
        return _read_json(path)

    @app.put("/v1/homebase/{vault_id}/latest")
    def homebase_put_latest(vault_id: str, payload: dict[str, Any], _user=Depends(user_dependency)) -> dict[str, Any]:
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
    def homebase_get_manifest(vault_id: str, manifest_id: str, _user=Depends(user_dependency)) -> Response:
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
        _user=Depends(user_dependency),
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
    def homebase_head_object(vault_id: str, object_id: str, _user=Depends(user_dependency)) -> Response:
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
    def homebase_get_object(vault_id: str, object_id: str, _user=Depends(user_dependency)) -> Response:
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
        _user=Depends(user_dependency),
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
