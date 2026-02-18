from __future__ import annotations

from typing import Any

import httpx

from sp.logging_flags import log_enabled


_LOG_HOMEBASE = log_enabled("homebase_sync")
_ANSI_BLUE = "\033[94m"
_ANSI_RESET = "\033[0m"


def _log_client(message: str) -> None:
    if _LOG_HOMEBASE:
        print(f"{_ANSI_BLUE}[HomebaseClient] {message}{_ANSI_RESET}")


class HomebaseClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        vault_id: str,
        timeout: float = 30.0,
        local_ui_token: str = "",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.vault_id = vault_id
        headers: dict[str, str] = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if local_ui_token:
            headers["x-local-ui-token"] = local_ui_token
        self.client = httpx.Client(headers=headers, timeout=timeout)
        _log_client(
            f"init base_url={self.base_url} vault_id={self.vault_id} "
            f"auth={'yes' if bool(token) else 'no'} local_ui_token={'yes' if bool(local_ui_token) else 'no'}"
        )

    def close(self) -> None:
        _log_client("close")
        self.client.close()

    def get_latest(self) -> dict[str, Any]:
        url = f"{self.base_url}/v1/homebase/{self.vault_id}/latest"
        _log_client(f"GET {url}")
        resp = self.client.get(url)
        _log_client(f"GET {url} -> {resp.status_code}")
        if resp.status_code == 404:
            _log_client("latest missing (404)")
            return {}
        resp.raise_for_status()
        payload = resp.json()
        checkpoint_id = payload.get("checkpoint_id") if isinstance(payload, dict) else None
        _log_client(f"latest checkpoint_id={checkpoint_id}")
        return payload if isinstance(payload, dict) else {}

    def put_latest(self, checkpoint_id: str) -> None:
        url = f"{self.base_url}/v1/homebase/{self.vault_id}/latest"
        _log_client(f"PUT {url} checkpoint_id={checkpoint_id}")
        resp = self.client.put(
            url,
            json={"checkpoint_id": checkpoint_id},
        )
        _log_client(f"PUT {url} -> {resp.status_code}")
        resp.raise_for_status()

    def get_manifest(self, manifest_id: str) -> bytes:
        url = f"{self.base_url}/v1/homebase/{self.vault_id}/manifests/{manifest_id}"
        _log_client(f"GET {url}")
        resp = self.client.get(url)
        _log_client(f"GET {url} -> {resp.status_code} bytes={len(resp.content)}")
        resp.raise_for_status()
        return resp.content

    def put_manifest(self, manifest_id: str, data: bytes) -> None:
        url = f"{self.base_url}/v1/homebase/{self.vault_id}/manifests/{manifest_id}"
        _log_client(f"PUT {url} bytes={len(data)}")
        resp = self.client.put(
            url,
            content=data,
            headers={"Content-Type": "application/octet-stream"},
        )
        _log_client(f"PUT {url} -> {resp.status_code}")
        if resp.status_code >= 400:
            _log_client(f"PUT {url} error_body={resp.text[:300]}")
        resp.raise_for_status()

    def has_object(self, object_id: str) -> bool:
        url = f"{self.base_url}/v1/homebase/{self.vault_id}/objects/{object_id}"
        resp = self.client.head(url)
        exists = resp.status_code == 200
        _log_client(f"HEAD {url} -> {resp.status_code} exists={exists}")
        return exists

    def get_object(self, object_id: str) -> bytes:
        url = f"{self.base_url}/v1/homebase/{self.vault_id}/objects/{object_id}"
        _log_client(f"GET {url}")
        resp = self.client.get(url)
        _log_client(f"GET {url} -> {resp.status_code} bytes={len(resp.content)}")
        resp.raise_for_status()
        return resp.content

    def put_object(self, object_id: str, data: bytes) -> None:
        url = f"{self.base_url}/v1/homebase/{self.vault_id}/objects/{object_id}"
        _log_client(f"PUT {url} bytes={len(data)}")
        resp = self.client.put(
            url,
            content=data,
            headers={"Content-Type": "application/octet-stream"},
        )
        _log_client(f"PUT {url} -> {resp.status_code}")
        if resp.status_code >= 400:
            _log_client(f"PUT {url} error_body={resp.text[:300]}")
        resp.raise_for_status()
