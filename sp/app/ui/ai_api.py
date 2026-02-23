from __future__ import annotations

from typing import List
import httpx

from sp.app import config

def normalize_base_url(url: str) -> str:
    if not url:
        return ""
    return url.rstrip("/")


def compose_url(base_url: str, path: str) -> str:
    base = normalize_base_url(base_url)
    if not path:
        return base
    return f"{base}/{path.lstrip('/')}"


def build_auth_headers(server_config: dict) -> dict:
    headers = {}
    auth_mode = (server_config or {}).get("auth_mode", "proxy")
    if auth_mode == "proxy":
        token = (server_config or {}).get("api_secret")
        if token:
            headers["x-api-secret"] = token
    elif auth_mode == "openai":
        api_key = (server_config or {}).get("api_key")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
    else:
        header_name = (server_config or {}).get("custom_header_name")
        header_value = (server_config or {}).get("custom_header_value")
        if header_name and header_value:
            headers[header_name] = header_value
    return headers


def build_httpx_timeout(server_config: dict | None = None) -> httpx.Timeout:
    """Build a timeout with distinct connect/read limits."""
    _ = server_config  # Reserved for possible per-server overrides.
    connect_timeout = config.load_ai_chat_connect_timeout(5.0)
    read_timeout = config.load_ai_chat_read_timeout(15.0)
    return httpx.Timeout(connect=connect_timeout, read=read_timeout, write=read_timeout, pool=connect_timeout)


def build_api_request(server_config: dict, messages: List[dict], model: str, stream: bool = True):
    server = server_config or {}
    base_url = server.get("base_url", "")
    chat_path = server.get("chat_path") or "/v1/chat/completions"
    if not base_url:
        raise ValueError("Selected server does not have a base URL configured.")
    url = compose_url(base_url, chat_path)
    headers = {"Content-Type": "application/json"}
    headers.update(build_auth_headers(server))
    verify = bool(server.get("verify_ssl", True))

    timeout = build_httpx_timeout(server)

    payload = {"model": model, "messages": messages, "stream": bool(stream)}
    return url, headers, verify, timeout, payload
