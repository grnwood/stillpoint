from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from sp.server.homebase_api import register_homebase_routes


def test_homebase_refresh_token_remains_valid_for_repeated_refresh(tmp_path) -> None:
    app = FastAPI()
    register_homebase_routes(
        app,
        ensure_vaults_root=lambda: tmp_path,
        admin_dependency=lambda: {"username": "admin"},
    )
    client = TestClient(app)

    create = client.post(
        "/v1/homebase/bootstrap/create",
        json={"username": "alice", "password": "secret", "vault_name": "Test Vault"},
    )
    assert create.status_code == 200
    payload = create.json()
    vault_id = payload["vault_id"]
    refresh_token = payload["refresh_token"]

    first = client.post(
        "/v1/homebase/bootstrap/refresh",
        json={"vault_id": vault_id, "refresh_token": refresh_token},
    )
    assert first.status_code == 200
    first_payload = first.json()
    assert first_payload["refresh_token"] == refresh_token
    assert first_payload["access_token"]

    second = client.post(
        "/v1/homebase/bootstrap/refresh",
        json={"vault_id": vault_id, "refresh_token": refresh_token},
    )
    assert second.status_code == 200
    second_payload = second.json()
    assert second_payload["refresh_token"] == refresh_token
    assert second_payload["access_token"]
    assert second_payload["access_token"] != first_payload["access_token"]
