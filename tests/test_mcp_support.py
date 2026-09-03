from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from starlette.requests import Request

from sp.app import config, mcp_bridge
from sp.server import api
from sp.server.state import vault_state


def test_external_bridge_discovers_desktop_and_mints_scoped_token(monkeypatch) -> None:
    state = {
        "api-base": "http://127.0.0.1:4567",
        "local-ui-token": "local-secret",
    }
    calls: list[tuple[str, dict, dict]] = []

    monkeypatch.delenv("STILLPOINT_MCP_URL", raising=False)
    monkeypatch.delenv("STILLPOINT_MCP_TOKEN", raising=False)
    monkeypatch.delenv("STILLPOINT_MCP_SESSION", raising=False)
    monkeypatch.delenv("STILLPOINT_LOCAL_UI_TOKEN", raising=False)
    monkeypatch.setattr(mcp_bridge, "_read_state", lambda name: state.get(name, ""))

    def fake_http(url: str, payload: dict, **kwargs):
        calls.append((url, payload, kwargs))
        return 200, {"token": "scoped-token"}

    monkeypatch.setattr(mcp_bridge, "_http_json", fake_http)
    url, token, session_id, local_ui_token = mcp_bridge._discover_connection()

    assert url == "http://127.0.0.1:4567/mcp"
    assert token == "scoped-token"
    assert session_id.startswith("external-mcp-")
    assert local_ui_token == "local-secret"
    assert calls == [
        (
            "http://127.0.0.1:4567/auth/mcp-token",
            {
                "ttl_seconds": 43200,
                "session_id": session_id,
                "vault_path": str(Path.cwd().resolve()),
            },
            {"local_ui_token": "local-secret", "timeout": 10},
        )
    ]


def test_mcp_token_is_vault_scoped_and_revocable(tmp_path, monkeypatch) -> None:
    vault = tmp_path / "Vault"
    page = vault / "Notes" / "Notes.md"
    page.parent.mkdir(parents=True)
    page.write_text("# Notes\n\nExternal MCP content.\n", encoding="utf-8")
    vault_state.set_root(str(vault))
    config.set_active_vault(str(vault))
    monkeypatch.setattr(api, "AUTH_ENABLED", False)
    with api._MCP_TOKEN_LOCK:
        api._MCP_ACTIVE_TOKENS.clear()

    try:
        user = api.AuthModels.UserInfo(username="admin", is_admin=True, can_write=True)
        issued = api.auth_mcp_token(
            api.AuthModels.McpTokenRequest(
                ttl_seconds=300,
                session_id="external-test",
                vault_path=str(page.parent),
            ),
            user,
        )
        assert vault_state.get_session_root("external-test") == vault.resolve()
        token = issued["token"]
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
        claims = api._require_mcp_claims(credentials, "external-test")
        assert claims["vault"] == api._mcp_vault_id(vault)
        assert {tool["name"] for tool in api._MCP_TOOLS} >= {
            "vault.read",
            "vault.search",
            "page.context",
            "page.patch",
            "tasks.create",
            "tasks.complete",
            "journal.open",
            "page.move",
        }
        assert "External MCP content" in api._mcp_call_tool(
            "vault.read", {"path": "/Notes/Notes.md"}, claims
        )["content"]

        body = json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        ).encode("utf-8")
        sent = False

        async def receive() -> dict:
            nonlocal sent
            if sent:
                return {"type": "http.disconnect"}
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}

        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/mcp",
                "headers": [(b"x-stillpoint-window-id", b"external-test")],
            },
            receive,
        )
        initialized = asyncio.run(api.mcp_endpoint(request, credentials))
        assert initialized["result"]["serverInfo"]["name"] == "StillPoint"

        api.auth_mcp_token_revoke(api.AuthModels.McpTokenRevokeRequest(token=token), user)
        with pytest.raises(HTTPException) as revoked:
            api._require_mcp_claims(credentials, "external-test")
        assert revoked.value.status_code == 401
    finally:
        config.set_active_vault(None)


def test_mcp_writes_use_mtime_conflict_checks(tmp_path) -> None:
    vault = tmp_path / "Vault"
    notes = vault / "Notes" / "Notes.md"
    notes.parent.mkdir(parents=True)
    notes.write_text("# Notes\n", encoding="utf-8")
    vault_state.set_root(str(vault))
    config.set_active_vault(str(vault))
    claims = {"sub": "test", "perm": "read_write"}
    try:
        record = api._mcp_call_tool("vault.read", {"path": ":Notes"}, claims)
        notes.write_text("# Changed elsewhere\n", encoding="utf-8")

        with pytest.raises(ValueError, match="page changed: expected mtime"):
            api._mcp_call_tool(
                "page.patch",
                {
                    "path": ":Notes",
                    "operation": "replace",
                    "content": "# MCP overwrite\n",
                    "expected_mtime_ns": record["mtime_ns"],
                },
                claims,
            )

        assert notes.read_text(encoding="utf-8") == "# Changed elsewhere\n"
    finally:
        config.set_active_vault(None)
