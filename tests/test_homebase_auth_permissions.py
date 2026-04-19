from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from sp.server import api
from sp.server.homebase_api import collect_homebase_auth_file_permission_faults, register_homebase_routes


def test_homebase_auth_files_are_written_private(tmp_path) -> None:
    app = FastAPI()
    register_homebase_routes(
        app,
        ensure_vaults_root=lambda: tmp_path,
        admin_dependency=lambda: {"username": "admin"},
    )
    client = TestClient(app)

    response = client.post(
        "/v1/homebase/bootstrap/create",
        json={"username": "alice", "password": "secret", "vault_name": "Test Vault"},
    )

    assert response.status_code == 200
    vault_id = response.json()["vault_id"]
    auth_dir = tmp_path / "homebase" / vault_id / "auth"
    auth_mode = auth_dir.joinpath("auth.json").stat().st_mode & 0o777
    tokens_mode = auth_dir.joinpath("tokens.json").stat().st_mode & 0o777
    assert auth_mode == 0o600
    assert tokens_mode == 0o600


def test_collect_homebase_auth_file_permission_faults_reports_open_files(tmp_path) -> None:
    auth_dir = tmp_path / "homebase" / "vault-one" / "auth"
    auth_dir.mkdir(parents=True)
    auth_path = auth_dir / "auth.json"
    tokens_path = auth_dir / "tokens.json"
    auth_path.write_text("{}", encoding="utf-8")
    tokens_path.write_text("{}", encoding="utf-8")
    auth_path.chmod(0o644)
    tokens_path.chmod(0o640)

    faults = collect_homebase_auth_file_permission_faults(tmp_path)

    assert len(faults) == 2
    assert any(str(auth_path) in fault and "0644" in fault for fault in faults)
    assert any(str(tokens_path) in fault and "0640" in fault for fault in faults)


def test_run_server_exits_when_homebase_auth_files_are_too_open(tmp_path, monkeypatch, capsys) -> None:
    auth_dir = tmp_path / "homebase" / "vault-one" / "auth"
    auth_dir.mkdir(parents=True)
    tokens_path = auth_dir / "tokens.json"
    tokens_path.write_text("{}", encoding="utf-8")
    tokens_path.chmod(0o644)
    monkeypatch.setattr(api, "SERVER_ADMIN_PASSWORD", "configured")
    monkeypatch.delenv("IGNORE__FILE_PERMISSION_CHECK", raising=False)
    called = {"uvicorn": False}

    def _unexpected_run(*args, **kwargs) -> None:
        called["uvicorn"] = True

    monkeypatch.setattr(api.uvicorn, "run", _unexpected_run)

    try:
        api.run_server(vaults_root=str(tmp_path))
    except SystemExit as exc:
        assert exc.code == 1
    else:
        raise AssertionError("Expected run_server to exit")

    assert called["uvicorn"] is False
    stderr = capsys.readouterr().err
    assert str(tokens_path) in stderr
    assert "IGNORE__FILE_PERMISSION_CHECK=true" in stderr


def test_run_server_allows_override_for_file_permission_check(tmp_path, monkeypatch) -> None:
    auth_dir = tmp_path / "homebase" / "vault-one" / "auth"
    auth_dir.mkdir(parents=True)
    tokens_path = auth_dir / "tokens.json"
    tokens_path.write_text("{}", encoding="utf-8")
    tokens_path.chmod(0o644)
    monkeypatch.setattr(api, "SERVER_ADMIN_PASSWORD", "configured")
    monkeypatch.setenv("IGNORE__FILE_PERMISSION_CHECK", "true")
    called = {"uvicorn": False}

    def _stop_run(*args, **kwargs) -> None:
        called["uvicorn"] = True

    monkeypatch.setattr(api.uvicorn, "run", _stop_run)

    api.run_server(vaults_root=str(tmp_path))

    assert called["uvicorn"] is True