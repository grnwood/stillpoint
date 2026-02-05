from __future__ import annotations

import json
import re
from html import unescape
from urllib.parse import quote_plus, urlparse, urlunparse, parse_qs, unquote
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

import httpx
from PySide6 import QtCore

from .ai_api import build_api_request
from .path_utils import colon_to_path
from sp.app import config

try:
    from bs4 import BeautifulSoup
except Exception:
    BeautifulSoup = None


AGENT_MESSAGE_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "AgentMessage",
    "type": "object",
    "oneOf": [
        {"$ref": "#/$defs/FinalAnswer"},
        {"$ref": "#/$defs/ToolRequest"},
    ],
    "$defs": {
        "FinalAnswer": {
            "type": "object",
            "required": ["type", "content"],
            "properties": {
                "type": {"const": "final"},
                "content": {"type": "string", "minLength": 1},
            },
            "additionalProperties": False,
        },
        "ToolRequest": {
            "type": "object",
            "required": ["type", "calls"],
            "properties": {
                "type": {"const": "tool_request"},
                "calls": {
                    "type": "array",
                    "minItems": 1,
                    "items": {"$ref": "#/$defs/ToolCall"},
                },
            },
            "additionalProperties": False,
        },
        "ToolCall": {
            "type": "object",
            "required": ["id", "name", "args"],
            "properties": {
                "id": {"type": "string", "minLength": 1},
                "name": {"type": "string", "minLength": 1},
                "args": {"type": "object"},
            },
            "additionalProperties": False,
        },
        "ToolResult": {
            "type": "object",
            "required": ["type", "id", "name", "status"],
            "properties": {
                "type": {"const": "tool_result"},
                "id": {"type": "string", "minLength": 1},
                "name": {"type": "string", "minLength": 1},
                "status": {"enum": ["ok", "error"]},
                "output": {},
                "error": {"$ref": "#/$defs/ToolError"},
            },
            "additionalProperties": False,
        },
        "ToolError": {
            "type": "object",
            "required": ["code", "message"],
            "properties": {
                "code": {
                    "type": "string",
                    "enum": [
                        "invalid_args",
                        "not_found",
                        "permission_denied",
                        "conflict",
                        "timeout",
                        "rate_limited",
                        "unavailable",
                        "internal",
                    ],
                },
                "message": {"type": "string", "minLength": 1},
                "details": {},
            },
            "additionalProperties": False,
        },
    },
}

DEFAULT_AGENT_SYSTEM_PROMPT = f"""You are a StillPoint agent.

You must respond with a single JSON object that matches this schema:
{json.dumps(AGENT_MESSAGE_SCHEMA, separators=(",", ":"))}

If you need a tool, respond with type="tool_request" and include one or more calls.
If you are done, respond with type="final" and include the final content.

Critical formatting rules:
- Output ONLY the JSON object. No markdown, no prose, no analysis, no <think> blocks.
- Do not include any extra text before or after the JSON.
- Do not include example JSON inside explanations (no explanations at all).
- Never include stray braces or JSON snippets outside the single JSON object.

Available tools:
- vault.read: args={{"path": "string | null"}}
  Reads a vault page and returns content. If path is null/empty, use the current page.
- vault.search: args={{"query":"string","limit":20,"path_prefix":"string | null"}}
  Full-text search across the vault. Returns matches with snippets.
- vault.write: args={{"path":"string","content":"string","mode":"replace|append"}}
  Writes content to a vault page. If mode=append, it appends to the existing file.
- tasks.list: args={{"query":"string | null","tags":["tag"],"status":"todo|done|all"}}
  Lists tasks with optional filters.
- daily.open: args={{}}
  Opens (creates if needed) today's daily journal page and returns its path.
- web.fetch: args={{"url":"string"}}
  Fetches a URL and returns its text content (limited size).
- web.scrape: args={{"url":"string"}}
  Fetches a URL and returns cleaned text (HTML stripped).
- web.search: args={{"query":"string","limit":10}}
  Searches the web and returns a list of links and titles.

If the user says "add", "append", or "edit", prefer vault.write with mode="append". If the page name is ambiguous, search for the page and ask the user to pick one.
When appending without an explicit page name, default to the current editor page or the chat page; if both exist and differ, ask the user to choose.
Only call tools the user explicitly asked for. Do not call daily.open unless the user asked about today's journal or daily page.

Tool results will be sent back as JSON objects with type="tool_result".
Continue the loop until you can return a final answer.
"""


_TOOL_LOG_GREEN = "\033[32m"
_TOOL_LOG_RESET = "\033[0m"


def _log_tool(message: str) -> None:
    if not message:
        return
    print(f"{_TOOL_LOG_GREEN}[AgentTools] {message}{_TOOL_LOG_RESET}")


def _summarize_output_for_log(output: Any) -> str:
    if output is None:
        return ""
    if isinstance(output, dict):
        if "content" in output and isinstance(output["content"], str):
            content = output["content"]
            trimmed = content[:50]
            suffix = f'... <trimmed {max(len(content) - len(trimmed), 0)} chars>' if len(content) > len(trimmed) else ""
            return f' content="{trimmed}{suffix}"'
        if "results" in output and isinstance(output["results"], list):
            return f" results={len(output['results'])}"
        if "matches" in output and isinstance(output["matches"], list):
            return f" matches={len(output['matches'])}"
        if "items" in output and isinstance(output["items"], list):
            return f" items={len(output['items'])}"
        if "path" in output:
            return f" path={output.get('path')}"
    if isinstance(output, list):
        return f" items={len(output)}"
    if isinstance(output, str):
        trimmed = output[:50]
        suffix = f'... <trimmed {max(len(output) - len(trimmed), 0)} chars>' if len(output) > len(trimmed) else ""
        return f' content="{trimmed}{suffix}"'
    return ""


def build_vault_key(api_base: str, vault_root: str) -> str:
    return f"{api_base.rstrip('/') or ''}::{vault_root or ''}"


def _clone_http_client(client: httpx.Client) -> httpx.Client:
    verify = getattr(client, "verify", None)
    if verify is None:
        verify = getattr(client, "_verify", True)
    follow_redirects = getattr(client, "follow_redirects", None)
    if follow_redirects is None:
        follow_redirects = getattr(client, "_follow_redirects", False)
    return httpx.Client(
        base_url=client.base_url,
        headers=client.headers,
        timeout=client.timeout,
        verify=verify,
        follow_redirects=follow_redirects,
        auth=client.auth,
    )


def _extract_json_block(text: str) -> Optional[str]:
    if not text:
        return None
    trimmed = text.strip()
    if trimmed.startswith("{") and trimmed.endswith("}"):
        return trimmed
    if "{" in trimmed and "}" in trimmed:
        start = trimmed.find("{")
        end = trimmed.rfind("}")
        if start >= 0 and end > start:
            return trimmed[start : end + 1]
    fence = "```"
    if fence in trimmed:
        parts = trimmed.split(fence)
        for part in parts:
            candidate = part.strip()
            if candidate.startswith("json"):
                candidate = candidate[4:].strip()
            if candidate.startswith("{") and candidate.endswith("}"):
                return candidate
    return None


def parse_agent_message(text: str) -> Optional[dict]:
    raw = text or ""
    payload = _extract_json_block(raw)
    candidates: list[str] = []
    if payload:
        candidates.append(payload)
    # If the message contains multiple brace blocks, try each JSON object.
    if raw:
        stack: list[int] = []
        for idx, ch in enumerate(raw):
            if ch == "{":
                stack.append(idx)
            elif ch == "}" and stack:
                start = stack.pop()
                candidates.append(raw[start : idx + 1])
    seen: set[str] = set()
    for candidate in reversed(candidates):
        if candidate in seen:
            continue
        seen.add(candidate)
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and parsed.get("type") in {"tool_request", "final"}:
            return parsed
    return None


def _extract_think_blocks(text: str) -> str:
    if not text:
        return ""
    matches = re.findall(r"<think\b[^>]*>(.*?)</think>", text, flags=re.DOTALL | re.IGNORECASE)
    return "\n\n".join(m.strip() for m in matches if m and m.strip())


def _tool_error(code: str, message: str, details: Optional[dict] = None) -> dict:
    err = {"code": code, "message": message}
    if details is not None:
        err["details"] = details
    return err


def _tool_result(call_id: str, name: str, *, output: Any = None, error: Optional[dict] = None) -> dict:
    if error:
        return {"type": "tool_result", "id": call_id, "name": name, "status": "error", "error": error}
    return {"type": "tool_result", "id": call_id, "name": name, "status": "ok", "output": output}


def _normalize_tool_name(name: str) -> str:
    raw = (name or "").strip()
    if not raw:
        return raw
    compact = re.sub(r"[^a-z0-9]+", "", raw.lower())
    if "web" in compact and "search" in compact:
        return "web.search"
    if "scrape" in compact:
        return "web.scrape"
    if "fetch" in compact:
        return "web.fetch"
    if "search" in compact:
        return "vault.search"
    if "read" in compact and "vault" in compact or compact in {"read", "vaultread", "readpage"}:
        return "vault.read"
    if "write" in compact:
        return "vault.write"
    if "task" in compact:
        return "tasks.list"
    if "daily" in compact or "journal" in compact:
        return "daily.open"
    key = raw.lower().replace(" ", "").replace("-", "").replace("_", "")
    aliases = {
        "read": "vault.read",
        "vaultread": "vault.read",
        "readvaultpage": "vault.read",
        "readpage": "vault.read",
        "readvault": "vault.read",
        "vault.read": "vault.read",
        "search": "vault.search",
        "vaultsearch": "vault.search",
        "searchvault": "vault.search",
        "vault.search": "vault.search",
        "write": "vault.write",
        "vaultwrite": "vault.write",
        "writevaultpage": "vault.write",
        "vault.write": "vault.write",
        "taskslist": "tasks.list",
        "listtasks": "tasks.list",
        "tasks.list": "tasks.list",
        "dailyopen": "daily.open",
        "opendaily": "daily.open",
        "daily.open": "daily.open",
        "websearch": "web.search",
        "web.search": "web.search",
        "webfetch": "web.fetch",
        "web.fetch": "web.fetch",
        "webscrape": "web.scrape",
        "web.scrape": "web.scrape",
    }
    return aliases.get(key, raw)


def _infer_tool_name_from_args(args: dict) -> str:
    if not isinstance(args, dict):
        return ""
    if "query" in args and "limit" in args and "path_prefix" in args:
        return "vault.search"
    if "query" in args and "limit" in args:
        return "vault.search"
    if "query" in args and "path_prefix" in args:
        return "vault.search"
    if "query" in args:
        return "tasks.list"
    if "content" in args and "path" in args:
        return "vault.write"
    if "url" in args:
        return "web.fetch"
    if "path" in args and len(args.keys()) <= 3:
        return "vault.read"
    return ""


def _normalize_read_path(path: str, vault_root_name: str = "") -> str:
    cleaned = (path or "").strip()
    if not cleaned:
        return ""
    lowered = cleaned.lower()
    if lowered in {"null", "none"}:
        return ""
    if "#" in cleaned:
        cleaned = cleaned.split("#", 1)[0]
    if ":" in cleaned:
        return colon_to_path(cleaned, vault_root_name=vault_root_name)
    if cleaned.startswith(":"):
        cleaned = cleaned[1:]
    if cleaned and not cleaned.startswith("/"):
        cleaned = f"/{cleaned}"
    return cleaned


def _extract_colon_link(text: str) -> str:
    if not text:
        return ""
    match = re.search(r"(:[A-Za-z0-9][A-Za-z0-9_\- ]*(?::[A-Za-z0-9][A-Za-z0-9_\- ]*)*(?:#[\w\-]+)?)", text)
    if not match:
        return ""
    return match.group(1).strip()


def _normalize_write_path(path: str, vault_root_name: str = "") -> str:
    cleaned = (path or "").strip()
    if not cleaned:
        return ""
    if "#" in cleaned:
        cleaned = cleaned.split("#", 1)[0]
    cleaned = cleaned.rstrip("/").strip()
    if ":" in cleaned or cleaned.startswith(":"):
        return colon_to_path(cleaned, vault_root_name=vault_root_name)
    if cleaned.startswith("/"):
        return cleaned
    # Treat bare titles as page names
    return colon_to_path(cleaned, vault_root_name=vault_root_name)


def _clean_page_query(text: str) -> str:
    cleaned = (text or "").strip().strip("'\"")
    if not cleaned:
        return ""
    cleaned = re.sub(r"\b(page|note|document)\b", "", cleaned, flags=re.IGNORECASE).strip()
    return cleaned or text


def _looks_explicit_path(text: str) -> bool:
    cleaned = (text or "").strip()
    if not cleaned:
        return False
    return cleaned.startswith("/") or cleaned.startswith(":") or ":" in cleaned


def _extract_tag_from_prompt(text: str) -> str:
    if not text:
        return ""
    match = re.search(r"@([A-Za-z0-9_\-]+)", text)
    return match.group(1) if match else ""


def _extract_search_query_from_prompt(text: str) -> str:
    if not text:
        return ""
    lowered = text.lower()
    for prefix in ("search the web for ", "web search for ", "search for ", "search "):
        if lowered.startswith(prefix):
            return text[len(prefix) :].strip(" .")
    return text.strip()


def _detect_guard_state(prompt: str) -> dict:
    lowered = (prompt or "").lower()
    settings = _load_agent_settings()
    triggers = settings.get("triggers", {})
    def _any_trigger(keys: list[str], default: tuple[str, ...]) -> bool:
        terms = keys if keys else list(default)
        return any(term in lowered for term in terms)
    return {
        "wants_tasks": _any_trigger(triggers.get("tasks.list", []), (" task", " tasks", "todo", "to-do", "overdue")),
        "wants_write": _any_trigger(triggers.get("vault.write", []), ("write", "create a page", "new page", "make a page", "add a page")),
        "wants_append": _any_trigger(triggers.get("vault.write.append", []), ("add to", "append", "insert into", "update", "edit")),
        "wants_daily": _any_trigger(triggers.get("daily.open", []), ("daily", "journal", "today")),
        "wants_search": _any_trigger(triggers.get("vault.search", []), ("search", "find references", "look for")),
        "wants_web": _any_trigger(triggers.get("web.search", []), ("search the web", "web search", "internet search")),
        "wants_summary": _any_trigger(triggers.get("summary", []), ("summarize", "summary", "tl;dr", "overview")),
        "tag": _extract_tag_from_prompt(prompt),
        "prompt": prompt or "",
    }


def _apply_guard_to_tool_call(
    name: str,
    args: dict,
    guard: dict,
    *,
    tasks_done: bool,
) -> tuple[str, dict, Optional[dict]]:
    if name == "daily.open" and not guard.get("wants_daily"):
        return name, args, _tool_error("permission_denied", "daily.open blocked unless user asked about daily/journal.")
    if guard.get("wants_web") and name != "web.search":
        prompt = guard.get("prompt") or ""
        query = (args or {}).get("query") or _extract_search_query_from_prompt(prompt) or guard.get("tag") or ""
        new_args = {"query": query}
        if (args or {}).get("limit"):
            new_args["limit"] = (args or {}).get("limit")
        return "web.search", new_args, None
    if guard.get("wants_tasks") and not tasks_done and name not in ("tasks.list", "vault.search"):
        tag = guard.get("tag") or ""
        new_args: dict = {"status": "todo" if "todo" in (guard or {}) else "all"}
        if tag:
            new_args["tags"] = [tag]
            new_args["status"] = "todo"
        else:
            new_args["query"] = "tasks"
            new_args["status"] = "todo"
        return "tasks.list", new_args, None
    return name, args, None


def _parse_trigger_settings(settings: dict) -> dict:
    tools = settings.get("tools") if isinstance(settings, dict) else None
    if not isinstance(tools, list):
        return {}
    trigger_map: dict[str, list[str]] = {}
    for tool in tools:
        name = (tool or {}).get("name")
        tweaks = (tool or {}).get("settings") or ""
        if not name:
            continue
        if "triggers=" in tweaks:
            raw = tweaks.split("triggers=", 1)[1]
            raw = raw.split(";", 1)[0]
            terms = [t.strip().lower() for t in raw.split(",") if t.strip()]
            if terms:
                trigger_map[name] = terms
    return trigger_map


def _parse_engine_setting(settings: dict) -> str:
    tools = settings.get("tools") if isinstance(settings, dict) else None
    if not isinstance(tools, list):
        return ""
    for tool in tools:
        if (tool or {}).get("name") != "web.search":
            continue
        tweaks = (tool or {}).get("settings") or ""
        if "engine=" in tweaks:
            return tweaks.split("engine=", 1)[1].split(";", 1)[0].strip().lower()
    return ""


def _load_agent_settings() -> dict:
    settings = config.load_agent_tool_settings()
    return {
        "triggers": _parse_trigger_settings(settings),
        "web_engine": _parse_engine_setting(settings),
    }


def _tool_vault_read(client: httpx.Client, args: dict, context: dict) -> dict:
    arg_path = (args or {}).get("path") or ""
    requested_path = context.get("requested_path") or ""
    last_read_path = context.get("last_read_path") or ""
    chat_page_path = context.get("chat_page_path") or ""
    current_editor_path = context.get("current_editor_path") or ""
    chat_scope = context.get("chat_scope") or ""
    if not arg_path and not requested_path and chat_page_path and current_editor_path and chat_page_path != current_editor_path:
        return {
            "ok": False,
            "error": _tool_error(
                "conflict",
                "Ambiguous target page. Ask user to choose current editor page or chat page.",
                {"current_page": current_editor_path, "chat_page": chat_page_path},
            ),
        }
    if not arg_path and not requested_path:
        if chat_scope == "page" and chat_page_path:
            path = chat_page_path
        elif current_editor_path:
            path = current_editor_path
        else:
            path = context.get("current_path") or last_read_path or ""
    else:
        path = arg_path or requested_path
    vault_root_name = context.get("vault_root_name", "")
    normalized_requested = _normalize_read_path(requested_path, vault_root_name=vault_root_name) if requested_path else ""
    normalized_arg = _normalize_read_path(arg_path, vault_root_name=vault_root_name) if arg_path else ""
    if normalized_requested and normalized_arg and normalized_requested != normalized_arg:
        _log_tool(f"Overriding tool path {normalized_arg} -> {normalized_requested}")
        path = requested_path
    path = _normalize_read_path(path, vault_root_name=vault_root_name)
    if not path:
        return {"ok": False, "error": _tool_error("invalid_args", "path is required or no current page is available")}
    try:
        resp = client.post("/api/file/read", json={"path": path})
        resp.raise_for_status()
        payload = resp.json()
        return {
            "ok": True,
            "output": {
                "path": path,
                "content": payload.get("content", ""),
                "rev": payload.get("rev"),
                "mtime_ns": payload.get("mtime_ns"),
            },
        }
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response else "n/a"
        return {"ok": False, "error": _tool_error("not_found", f"HTTP {status}", {"path": path})}
    except httpx.HTTPError as exc:
        return {"ok": False, "error": _tool_error("unavailable", str(exc), {"path": path})}


def _tool_vault_search(client: httpx.Client, args: dict, context: dict) -> dict:
    query = (args or {}).get("query") or ""
    if not query:
        return {"ok": False, "error": _tool_error("invalid_args", "query is required")}
    limit = (args or {}).get("limit") or 20
    try:
        limit = int(limit)
    except Exception:
        limit = 20
    path_prefix = (args or {}).get("path_prefix") or None
    params = {"q": query, "limit": max(1, min(200, limit))}
    if path_prefix:
        params["subtree"] = _normalize_read_path(path_prefix, vault_root_name=context.get("vault_root_name", ""))
    try:
        resp = client.get("/api/search", params=params)
        resp.raise_for_status()
        payload = resp.json()
        results = payload.get("results") or []
        matches = []
        for item in results:
            if not isinstance(item, dict):
                continue
            matches.append(
                {
                    "path": item.get("path"),
                    "snippet": item.get("snippet"),
                    "score": item.get("rank"),
                }
            )
        for idx, match in enumerate(matches[:5], start=1):
            path = match.get("path") or ""
            snippet = (match.get("snippet") or "").replace("\n", " ")
            snippet = snippet[:50]
            if len(match.get("snippet") or "") > 50:
                snippet += f"... <trimmed {len(match.get('snippet') or '') - 50} chars>"
            _log_tool(f"vault.search result {idx}: {path} :: {snippet}")
        return {"ok": True, "output": {"query": query, "matches": matches}}
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response else "n/a"
        return {"ok": False, "error": _tool_error("not_found", f"HTTP {status}", {"query": query})}
    except httpx.HTTPError as exc:
        return {"ok": False, "error": _tool_error("unavailable", str(exc), {"query": query})}


def _tool_vault_write(client: httpx.Client, args: dict, context: dict) -> dict:
    path = (args or {}).get("path") or ""
    content = (args or {}).get("content")
    mode = (args or {}).get("mode") or "replace"
    if not path or content is None:
        return {"ok": False, "error": _tool_error("invalid_args", "path and content are required")}
    if mode == "append" and not _looks_explicit_path(path):
        current_path = context.get("current_editor_path") or context.get("current_path") or ""
        chat_page_path = context.get("chat_page_path") or ""
        chat_scope = context.get("chat_scope") or ""
        if current_path and chat_page_path and current_path != chat_page_path:
            return {
                "ok": False,
                "error": _tool_error(
                    "conflict",
                    "Ambiguous target page. Ask user to choose current editor page or chat page.",
                    {"current_page": current_path, "chat_page": chat_page_path},
                ),
            }
        if chat_scope == "page" and chat_page_path:
            path = chat_page_path
        elif current_path:
            path = current_path
    if mode == "append" and not _looks_explicit_path(path):
        query = _clean_page_query(path)
        try:
            resp = client.get("/api/pages/search", params={"q": query, "limit": 5})
            resp.raise_for_status()
            pages = (resp.json() or {}).get("pages") or []
            if len(pages) == 1 and pages[0].get("path"):
                path = pages[0]["path"]
                _log_tool(f"vault.write resolved '{query}' -> {path}")
            elif len(pages) > 1:
                return {
                    "ok": False,
                    "error": _tool_error(
                        "conflict",
                        "Multiple pages match; ask user to choose one.",
                        {"candidates": pages},
                    ),
                }
        except httpx.HTTPError as exc:
            return {"ok": False, "error": _tool_error("unavailable", str(exc), {"query": query})}
    path = _normalize_write_path(path, vault_root_name=context.get("vault_root_name", ""))
    if not path:
        return {"ok": False, "error": _tool_error("invalid_args", "path is required")}
    _log_tool(f"vault.write preparing path={path} mode={mode} bytes={len(str(content).encode('utf-8'))}")
    final_content = content
    if mode == "append":
        try:
            read_resp = client.post("/api/file/read", json={"path": path})
            read_resp.raise_for_status()
            existing = (read_resp.json() or {}).get("content") or ""
            final_content = f"{existing}{content}"
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code if exc.response else None
            if status == 404:
                _log_tool(f"vault.write append: {path} not found, creating new file")
                final_content = str(content)
            else:
                return {"ok": False, "error": _tool_error("unavailable", str(exc), {"path": path})}
        except httpx.HTTPError as exc:
            return {"ok": False, "error": _tool_error("unavailable", str(exc), {"path": path})}
    try:
        resp = client.post("/api/file/write", json={"path": path, "content": final_content})
        resp.raise_for_status()
        return {
            "ok": True,
            "output": {
                "path": path,
                "bytes": len(final_content.encode("utf-8")),
                "mode": mode,
            },
        }
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response else "n/a"
        return {"ok": False, "error": _tool_error("conflict", f"HTTP {status}", {"path": path})}
    except httpx.HTTPError as exc:
        return {"ok": False, "error": _tool_error("unavailable", str(exc), {"path": path})}


def _tool_tasks_list(client: httpx.Client, args: dict, context: dict) -> dict:
    params = {}
    query = (args or {}).get("query")
    tags = (args or {}).get("tags")
    if isinstance(query, str) and query.strip().startswith("@") and not tags:
        tags = [query.strip().lstrip("@")]
        query = None
    if query:
        params["query"] = query
    if tags:
        params["tags"] = tags
    status = (args or {}).get("status")
    if status and status != "all":
        params["status"] = status
    try:
        resp = client.get("/api/tasks", params=params)
        resp.raise_for_status()
        payload = resp.json() or {}
        items = payload.get("items") or []
        if not items and query and not tags:
            # Fallback: treat query as a tag if no text matches.
            tag = query.strip().lstrip("@")
            if tag:
                fallback_params = dict(params)
                fallback_params.pop("query", None)
                fallback_params["tags"] = [tag]
                _log_tool(f"tasks.list fallback: query -> tag '{tag}'")
                fallback_resp = client.get("/api/tasks", params=fallback_params)
                fallback_resp.raise_for_status()
                payload = fallback_resp.json() or {}
                items = payload.get("items") or []
        return {"ok": True, "output": {"items": items}}
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response else "n/a"
        return {"ok": False, "error": _tool_error("not_found", f"HTTP {status}")} 
    except httpx.HTTPError as exc:
        return {"ok": False, "error": _tool_error("unavailable", str(exc))}


def _tool_daily_open(client: httpx.Client, args: dict, context: dict) -> dict:
    try:
        resp = client.post("/api/journal/today", json={})
        resp.raise_for_status()
        payload = resp.json() or {}
        return {"ok": True, "output": {"path": payload.get("path"), "created": payload.get("created")}}
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response else "n/a"
        return {"ok": False, "error": _tool_error("not_found", f"HTTP {status}")} 
    except httpx.HTTPError as exc:
        return {"ok": False, "error": _tool_error("unavailable", str(exc))}


def _web_safe_url(url: str) -> str:
    cleaned = (url or "").strip()
    if not cleaned:
        return ""
    if not cleaned.startswith(("http://", "https://")):
        return ""
    return cleaned


def _clean_url(url: str) -> str:
    try:
        if url.startswith("//"):
            url = f"https:{url}"
        parsed = urlparse(url)
        if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
            qs = parse_qs(parsed.query or "")
            uddg = qs.get("uddg", [None])[0]
            if uddg:
                url = unquote(uddg)
                parsed = urlparse(url)
        cleaned = parsed._replace(query="", fragment="")
        return urlunparse(cleaned)
    except Exception:
        return url


def _tool_web_fetch(client: httpx.Client, args: dict, context: dict) -> dict:
    url = _web_safe_url((args or {}).get("url") or "")
    if not url:
        return {"ok": False, "error": _tool_error("invalid_args", "url must be http(s)")}
    try:
        resp = httpx.get(
            url,
            timeout=15.0,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "")
        text = resp.text or ""
        if len(text) > 200_000:
            text = text[:200_000]
        return {
            "ok": True,
            "output": {
                "url": str(resp.url),
                "content_type": content_type,
                "bytes": len(text.encode("utf-8")),
                "content": text,
            },
        }
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response else "n/a"
        return {"ok": False, "error": _tool_error("unavailable", f"HTTP {status}", {"url": url})}
    except httpx.HTTPError as exc:
        return {"ok": False, "error": _tool_error("unavailable", str(exc), {"url": url})}


def _strip_html(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"(?is)<(script|style).*?>.*?(</\\1>)", " ", text)
    text = re.sub(r"(?is)<br\\s*/?>", "\n", text)
    text = re.sub(r"(?is)</p>", "\n\n", text)
    text = re.sub(r"(?is)<.*?>", " ", text)
    text = unescape(text)
    text = re.sub(r"[\\t\\r ]+", " ", text)
    text = re.sub(r"\\n\\s+\\n", "\n\n", text)
    return text.strip()


def _parse_search_results_with_bs4(html: str, engine: str) -> list[dict]:
    if not BeautifulSoup or not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    results: list[dict] = []
    if engine == "brave":
        for a in soup.select("a.result-header, a.result__title"):
            href = a.get("href")
            title = a.get_text(" ", strip=True)
            if href and title:
                results.append({"title": title, "url": href})
    elif engine == "google":
        for a in soup.select("a"):
            href = a.get("href", "")
            if href.startswith("/url?q="):
                url = href.split("/url?q=", 1)[1].split("&", 1)[0]
                title = a.get_text(" ", strip=True)
                if url and title:
                    results.append({"title": title, "url": url})
    else:
        for a in soup.select("a.result__a"):
            href = a.get("href")
            title = a.get_text(" ", strip=True)
            if href and title:
                results.append({"title": title, "url": href})
        if not results:
            for a in soup.select("a.result-link"):
                href = a.get("href")
                title = a.get_text(" ", strip=True)
                if href and title:
                    results.append({"title": title, "url": href})
    return results


def _scrape_url(url: str, *, max_chars: int = 8000) -> dict:
    safe = _web_safe_url(url)
    if not safe:
        return {"ok": False, "error": _tool_error("invalid_args", "url must be http(s)")}
    try:
        resp = httpx.get(
            safe,
            timeout=15.0,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "")
        text = resp.text or ""
        if len(text) > 200_000:
            text = text[:200_000]
        cleaned = _strip_html(text)
        if max_chars and len(cleaned) > max_chars:
            cleaned = cleaned[:max_chars]
        return {
            "ok": True,
            "output": {
                "url": str(resp.url),
                "content_type": content_type,
                "bytes": len(cleaned.encode("utf-8")),
                "content": cleaned,
            },
        }
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response else "n/a"
        return {"ok": False, "error": _tool_error("unavailable", f"HTTP {status}", {"url": safe})}
    except httpx.HTTPError as exc:
        return {"ok": False, "error": _tool_error("unavailable", str(exc), {"url": safe})}


def _tool_web_scrape(client: httpx.Client, args: dict, context: dict) -> dict:
    url = _web_safe_url((args or {}).get("url") or "")
    if not url:
        return {"ok": False, "error": _tool_error("invalid_args", "url must be http(s)")}
    try:
        resp = httpx.get(
            url,
            timeout=15.0,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "")
        text = resp.text or ""
        if len(text) > 200_000:
            text = text[:200_000]
        cleaned = _strip_html(text)
        return {
            "ok": True,
            "output": {
                "url": str(resp.url),
                "content_type": content_type,
                "bytes": len(cleaned.encode("utf-8")),
                "content": cleaned,
            },
        }
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response else "n/a"
        return {"ok": False, "error": _tool_error("unavailable", f"HTTP {status}", {"url": url})}
    except httpx.HTTPError as exc:
        return {"ok": False, "error": _tool_error("unavailable", str(exc), {"url": url})}


def _tool_web_search(client: httpx.Client, args: dict, context: dict) -> dict:
    query = (args or {}).get("query") or ""
    if not query:
        return {"ok": False, "error": _tool_error("invalid_args", "query is required")}
    try:
        limit = int((args or {}).get("limit") or 10)
    except Exception:
        limit = 10
    engine = (args or {}).get("engine") or (context.get("web_engine") if isinstance(context, dict) else "") or "duckduckgo"
    engine = str(engine).strip().lower()
    if engine == "google":
        url = f"https://www.google.com/search?q={quote_plus(query)}"
    elif engine == "brave":
        url = f"https://search.brave.com/search?q={quote_plus(query)}"
    else:
        url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    try:
        resp = httpx.get(
            url,
            timeout=15.0,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        resp.raise_for_status()
        html = resp.text or ""
        results = _parse_search_results_with_bs4(html, engine)
        links = [r.get("url") for r in results]
        titles = [r.get("title") for r in results]
        if not links and engine == "brave":
            links = re.findall(r'class="result-header"[^>]*href="([^"]+)"', html)
            titles = re.findall(r'class="result-header"[^>]*>(.*?)</a>', html)
            if not links:
                links = re.findall(r'class="result__title"[^>]*href="([^"]+)"', html)
                titles = re.findall(r'class="result__title"[^>]*>(.*?)</a>', html)
        if not links and engine == "google":
            _log_tool("web.search google returned 0 results; falling back to duckduckgo")
            engine = "duckduckgo"
            url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
            resp = httpx.get(
                url,
                timeout=15.0,
                follow_redirects=True,
                headers={
                    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
                    "Accept-Language": "en-US,en;q=0.9",
                },
            )
            resp.raise_for_status()
            html = resp.text or ""
            results = _parse_search_results_with_bs4(html, engine)
            links = [r.get("url") for r in results]
            titles = [r.get("title") for r in results]
        if not links:
            lite_url = f"https://duckduckgo.com/lite/?q={quote_plus(query)}"
            lite_resp = httpx.get(
                lite_url,
                timeout=15.0,
                follow_redirects=True,
                headers={
                    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
                    "Accept-Language": "en-US,en;q=0.9",
                },
            )
            lite_resp.raise_for_status()
            lite_html = lite_resp.text or ""
            results = _parse_search_results_with_bs4(lite_html, "duckduckgo")
            links = [r.get("url") for r in results]
            titles = [r.get("title") for r in results]
        results = []
        for idx, link in enumerate(links):
            title = titles[idx] if idx < len(titles) else ""
            if isinstance(title, str):
                title = re.sub(r"\s+", " ", title).strip()
            if not title:
                title = link
            results.append({"title": title, "url": link})
            if len(results) >= limit:
                break
        for idx, result in enumerate(results, start=1):
            clean_url = _clean_url(result.get("url") or "")
            _log_tool(f"web.search result {idx}: {result.get('title')} -> {clean_url}")
        return {"ok": True, "output": {"query": query, "results": results}}
    except httpx.HTTPError as exc:
        return {"ok": False, "error": _tool_error("unavailable", str(exc), {"query": query})}


@dataclass
class AgentLoopConfig:
    server_config: dict
    model: str
    system_prompt: str
    max_steps: int = 6


class AgentToolLoopWorker(QtCore.QThread):
    toolLog = QtCore.Signal(str)
    finished = QtCore.Signal(str)
    failed = QtCore.Signal(str)

    def __init__(
        self,
        *,
        config: AgentLoopConfig,
        client: httpx.Client,
        user_prompt: str,
        context: dict,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._config = config
        self._client = client
        self._user_prompt = user_prompt
        self._context = context or {}
        self._cancel_requested = False

    def request_cancel(self) -> None:
        self._cancel_requested = True

    def _send_llm(self, messages: list[dict]) -> str:
        url, headers, verify, timeout, payload = build_api_request(
            self._config.server_config, messages, self._config.model, stream=False
        )
        resp = httpx.post(url, json=payload, headers=headers, timeout=timeout, verify=verify)
        resp.raise_for_status()
        data = resp.json()
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") if isinstance(choice, dict) else {}
        return (message or {}).get("content", "") if isinstance(message, dict) else ""

    def run(self) -> None:
        tools: Dict[str, Callable[[httpx.Client, dict, dict], dict]] = {
            "vault.read": _tool_vault_read,
            "vault.search": _tool_vault_search,
            "vault.write": _tool_vault_write,
            "tasks.list": _tool_tasks_list,
            "daily.open": _tool_daily_open,
            "web.fetch": _tool_web_fetch,
            "web.scrape": _tool_web_scrape,
            "web.search": _tool_web_search,
        }
        client = _clone_http_client(self._client)
        try:
            agent_settings = _load_agent_settings()
            guard = _detect_guard_state(self._user_prompt)
            requested = _extract_colon_link(self._user_prompt)
            if requested:
                self._context["requested_path"] = requested
            self._context["guard_state"] = guard
            self._context["web_engine"] = agent_settings.get("web_engine")
            messages: list[dict] = [
                {"role": "system", "content": self._config.system_prompt},
                {
                    "role": "user",
                    "content": (
                        f"User request:\n{self._user_prompt}\n\n"
                        f"Context:\ncurrent_page={self._context.get('current_path') or ''}"
                    ),
                },
            ]
            for step in range(self._config.max_steps):
                if self._cancel_requested:
                    self.failed.emit("Cancelled")
                    return
                reply = self._send_llm(messages)
                if self._context.get("debug"):
                    think_text = _extract_think_blocks(reply)
                    if think_text:
                        msg = f"Thinking: {think_text}"
                        self.toolLog.emit(msg)
                        _log_tool(msg)
                parsed = parse_agent_message(reply)
                if not parsed or parsed.get("type") == "final":
                    final_text = parsed.get("content") if isinstance(parsed, dict) else reply
                    self.finished.emit(final_text or reply)
                    return
                if parsed.get("type") != "tool_request":
                    self.finished.emit(reply)
                    return
                calls = parsed.get("calls") or []
                if not isinstance(calls, list) or not calls:
                    self.failed.emit("Tool request missing calls.")
                    return
                tasks_done = bool(self._context.get("tasks_list_done"))
                guard_state = self._context.get("guard_state") or {}
                for call in calls:
                    if self._cancel_requested:
                        self.failed.emit("Cancelled")
                        return
                    call_id = str(call.get("id") or uuid.uuid4())
                    name = str(call.get("name") or "")
                    args = call.get("args") if isinstance(call.get("args"), dict) else {}
                    if not name:
                        inferred = _infer_tool_name_from_args(args)
                        if inferred:
                            name = inferred
                        elif "path" in args:
                            name = "vault.read"
                    normalized_name = _normalize_tool_name(name)
                    normalized_name, args, guard_error = _apply_guard_to_tool_call(
                        normalized_name, args, guard_state, tasks_done=tasks_done
                    )
                    if not name:
                        msg = f"Tool call: <missing name> {args}"
                    elif call.get("name") in (None, "") and normalized_name == name:
                        msg = f"Tool call: <inferred {normalized_name}> {args}"
                    elif normalized_name != name:
                        msg = f"Tool call: {name} -> {normalized_name} {args}"
                    else:
                        msg = f"Tool call: {name} {args}"
                    self.toolLog.emit(msg)
                    _log_tool(msg)
                    handler = tools.get(normalized_name)
                    if guard_error:
                        result = _tool_result(
                            call_id,
                            normalized_name or name,
                            error=guard_error,
                        )
                    elif not handler:
                        result = _tool_result(
                            call_id,
                            normalized_name or name,
                            error=_tool_error("not_found", f"Unknown tool: {name}"),
                        )
                    else:
                        outcome = handler(client, args, self._context)
                        if outcome.get("ok"):
                            result = _tool_result(call_id, normalized_name, output=outcome.get("output"))
                        else:
                            result = _tool_result(call_id, normalized_name, error=outcome.get("error"))
                    if normalized_name == "tasks.list" and result.get("status") == "ok":
                        self._context["tasks_list_done"] = True
                    messages.append({"role": "assistant", "content": json.dumps(result)})
                    msg = f"Tool result: {normalized_name or name} status={result.get('status')}"
                    if result.get("status") == "ok":
                        output = result.get("output") if isinstance(result, dict) else None
                        if isinstance(output, dict):
                            path = output.get("path")
                            content = output.get("content")
                            content_len = len(content.encode("utf-8")) if isinstance(content, str) else None
                            if path:
                                msg += f" path={path}"
                            if content_len is not None:
                                msg += f" bytes={content_len}"
                        msg += _summarize_output_for_log(output)
                    self.toolLog.emit(msg)
                    _log_tool(msg)
                # After tools, prompt the model to continue with the tool results.
                messages.append(
                    {
                        "role": "user",
                        "content": "Continue. Use the tool results above. If done, return a final answer.",
                    }
                )
            self.failed.emit("Agent tool loop exceeded max steps.")
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            try:
                client.close()
            except Exception:
                pass


class AgentToolChatWorker(QtCore.QThread):
    toolMessage = QtCore.Signal(str)
    finalMessage = QtCore.Signal(str)
    failed = QtCore.Signal(str)

    def __init__(
        self,
        *,
        config: AgentLoopConfig,
        client: httpx.Client,
        user_prompt: str,
        context: dict,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._config = config
        self._client = client
        self._user_prompt = user_prompt
        self._context = context or {}
        self._cancel_requested = False

    def request_cancel(self) -> None:
        self._cancel_requested = True

    def _send_llm(self, messages: list[dict]) -> str:
        url, headers, verify, timeout, payload = build_api_request(
            self._config.server_config, messages, self._config.model, stream=False
        )
        resp = httpx.post(url, json=payload, headers=headers, timeout=timeout, verify=verify)
        resp.raise_for_status()
        data = resp.json()
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") if isinstance(choice, dict) else {}
        return (message or {}).get("content", "") if isinstance(message, dict) else ""

    def run(self) -> None:
        tools: Dict[str, Callable[[httpx.Client, dict, dict], dict]] = {
            "vault.read": _tool_vault_read,
            "vault.search": _tool_vault_search,
            "vault.write": _tool_vault_write,
            "tasks.list": _tool_tasks_list,
            "daily.open": _tool_daily_open,
            "web.fetch": _tool_web_fetch,
            "web.scrape": _tool_web_scrape,
            "web.search": _tool_web_search,
        }
        client = _clone_http_client(self._client)
        try:
            agent_settings = _load_agent_settings()
            guard = _detect_guard_state(self._user_prompt)
            requested = _extract_colon_link(self._user_prompt)
            if requested:
                self._context["requested_path"] = requested
            self._context["guard_state"] = guard
            self._context["web_engine"] = agent_settings.get("web_engine")
            messages: list[dict] = [
                {"role": "system", "content": self._config.system_prompt},
                {
                    "role": "user",
                    "content": (
                        f"User request:\n{self._user_prompt}\n\n"
                        f"Context:\ncurrent_page={self._context.get('current_path') or ''}"
                    ),
                },
            ]
            for _ in range(self._config.max_steps):
                if self._cancel_requested:
                    self.failed.emit("Cancelled")
                    return
                reply = self._send_llm(messages)
                if self._context.get("debug"):
                    think_text = _extract_think_blocks(reply)
                    if think_text:
                        self.toolMessage.emit(f"Thinking: {think_text}")
                parsed = parse_agent_message(reply)
                if not parsed or parsed.get("type") == "final":
                    final_text = parsed.get("content") if isinstance(parsed, dict) else reply
                    self.finalMessage.emit(final_text or reply)
                    return
                if parsed.get("type") != "tool_request":
                    self.finalMessage.emit(reply)
                    return
                calls = parsed.get("calls") or []
                if not isinstance(calls, list) or not calls:
                    self.failed.emit("Tool request missing calls.")
                    return
                tasks_done = bool(self._context.get("tasks_list_done"))
                guard_state = self._context.get("guard_state") or {}
                for call in calls:
                    if self._cancel_requested:
                        self.failed.emit("Cancelled")
                        return
                    call_id = str(call.get("id") or uuid.uuid4())
                    name = str(call.get("name") or "")
                    args = call.get("args") if isinstance(call.get("args"), dict) else {}
                    if not name:
                        inferred = _infer_tool_name_from_args(args)
                        if inferred:
                            name = inferred
                        elif "path" in args:
                            name = "vault.read"
                    normalized_name = _normalize_tool_name(name)
                    normalized_name, args, guard_error = _apply_guard_to_tool_call(
                        normalized_name, args, guard_state, tasks_done=tasks_done
                    )
                    if not name:
                        msg = f"Tool call: <missing name> {args}"
                    elif call.get("name") in (None, "") and normalized_name == name:
                        msg = f"Tool call: <inferred {normalized_name}> {args}"
                    elif normalized_name != name:
                        msg = f"Tool call: {name} -> {normalized_name} {args}"
                    else:
                        msg = f"Tool call: {name} {args}"
                    self.toolMessage.emit(msg)
                    _log_tool(msg)
                    handler = tools.get(normalized_name)
                    if guard_error:
                        result = _tool_result(
                            call_id,
                            normalized_name or name,
                            error=guard_error,
                        )
                    elif not handler:
                        result = _tool_result(
                            call_id,
                            normalized_name or name,
                            error=_tool_error("not_found", f"Unknown tool: {name}"),
                        )
                    else:
                        outcome = handler(client, args, self._context)
                        if outcome.get("ok"):
                            result = _tool_result(call_id, normalized_name, output=outcome.get("output"))
                        else:
                            result = _tool_result(call_id, normalized_name, error=outcome.get("error"))
                    if normalized_name == "tasks.list" and result.get("status") == "ok":
                        self._context["tasks_list_done"] = True
                    messages.append({"role": "assistant", "content": json.dumps(result)})
                    if result.get("status") == "error":
                        err = result.get("error") or {}
                        code = err.get("code", "unknown")
                        msg = err.get("message", "")
                        details = err.get("details")
                        detail_str = f" details={details}" if details is not None else ""
                        out = f"Tool result: {normalized_name or name} status=error code={code} message={msg}{detail_str}"
                        self.toolMessage.emit(out)
                        _log_tool(out)
                    else:
                        output = result.get("output") if isinstance(result, dict) else None
                        details = ""
                        if isinstance(output, dict):
                            path = output.get("path")
                            content = output.get("content")
                            content_len = len(content.encode("utf-8")) if isinstance(content, str) else None
                            if path:
                                details += f" path={path}"
                            if content_len is not None:
                                details += f" bytes={content_len}"
                            if "matches" in output:
                                try:
                                    details += f" matches={len(output.get('matches') or [])}"
                                except Exception:
                                    pass
                            if "items" in output:
                                try:
                                    details += f" items={len(output.get('items') or [])}"
                                except Exception:
                                    pass
                            if "created" in output and output.get("path"):
                                details += f" created={bool(output.get('created'))}"
                        out = f"Tool result: {normalized_name or name} status=ok{details}{_summarize_output_for_log(output)}"
                        self.toolMessage.emit(out)
                        _log_tool(out)
                messages.append(
                    {
                        "role": "user",
                        "content": "Continue. Use the tool results above. If done, return a final answer.",
                    }
                )
            self.failed.emit("Agent tool loop exceeded max steps.")
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            try:
                client.close()
            except Exception:
                pass
