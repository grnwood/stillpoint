from __future__ import annotations

from typing import Any, Optional

import httpx


class HomebaseClient:
    def __init__(self, base_url: str, token: str, vault_id: str, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.vault_id = vault_id
        headers: dict[str, str] = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.client = httpx.Client(headers=headers, timeout=timeout)

    def close(self) -> None:
        self.client.close()

    def get_latest(self) -> dict[str, Any]:
        resp = self.client.get(f"{self.base_url}/v1/homebase/{self.vault_id}/latest")
        if resp.status_code == 404:
            return {}
        resp.raise_for_status()
        payload = resp.json()
        return payload if isinstance(payload, dict) else {}

    def put_latest(self, checkpoint_id: str) -> None:
        resp = self.client.put(
            f"{self.base_url}/v1/homebase/{self.vault_id}/latest",
            json={"checkpoint_id": checkpoint_id},
        )
        resp.raise_for_status()

    def get_manifest(self, manifest_id: str) -> bytes:
        resp = self.client.get(f"{self.base_url}/v1/homebase/{self.vault_id}/manifests/{manifest_id}")
        resp.raise_for_status()
        return resp.content

    def put_manifest(self, manifest_id: str, data: bytes) -> None:
        resp = self.client.put(
            f"{self.base_url}/v1/homebase/{self.vault_id}/manifests/{manifest_id}",
            content=data,
        )
        resp.raise_for_status()

    def has_object(self, object_id: str) -> bool:
        resp = self.client.head(f"{self.base_url}/v1/homebase/{self.vault_id}/objects/{object_id}")
        return resp.status_code == 200

    def get_object(self, object_id: str) -> bytes:
        resp = self.client.get(f"{self.base_url}/v1/homebase/{self.vault_id}/objects/{object_id}")
        resp.raise_for_status()
        return resp.content

    def put_object(self, object_id: str, data: bytes) -> None:
        resp = self.client.put(
            f"{self.base_url}/v1/homebase/{self.vault_id}/objects/{object_id}",
            content=data,
        )
        resp.raise_for_status()

