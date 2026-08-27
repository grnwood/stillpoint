from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

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

    tokens_path = tmp_path / "homebase" / vault_id / "auth" / "tokens.json"
    stored = json.loads(tokens_path.read_text(encoding="utf-8"))
    # Refresh reuses the caller's refresh token; it must not accumulate an
    # additional unreachable refresh token on every access-token renewal.
    assert list(stored["refresh_tokens"]) == [refresh_token]


def test_parallel_authenticated_requests_do_not_contend_on_token_temp_file(tmp_path) -> None:
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
    url = f"/v1/homebase/{payload['vault_id']}/latest"
    headers = {"Authorization": f"Bearer {payload['access_token']}"}

    # Authenticated reads should not rewrite tokens.json at all.  This is the
    # request pattern that previously raced under parallel object HEAD/GETs.
    def authenticated_read(_index: int):
        # TestClient itself is not guaranteed to serialize concurrent calls on
        # one portal; use independent clients to model independent devices.
        with TestClient(app) as request_client:
            return request_client.get(url, headers=headers)

    with ThreadPoolExecutor(max_workers=6) as executor:
        responses = list(executor.map(authenticated_read, range(24)))

    assert all(response.status_code == 404 for response in responses)


def test_parallel_client_logins_keep_every_issued_access_token_valid(tmp_path) -> None:
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
    vault_id = create.json()["vault_id"]

    def connect(_index: int):
        with TestClient(app) as request_client:
            return request_client.post(
                "/v1/homebase/bootstrap/connect",
                json={"vault_id": vault_id, "username": "alice", "password": "secret"},
            )

    # Argon2 login verification is deliberately expensive; four overlapping
    # clients are enough to exercise token persistence without making the test
    # consume excessive memory on smaller CI workers.
    with ThreadPoolExecutor(max_workers=2) as executor:
        logins = list(executor.map(connect, range(4)))

    assert all(response.status_code == 200 for response in logins)
    for login in logins:
        access_token = login.json()["access_token"]
        response = client.get(
            f"/v1/homebase/{vault_id}/latest",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 404
