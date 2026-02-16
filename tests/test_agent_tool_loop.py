import httpx

from sp.app.ui.agent_tool_loop import (
    _apply_guard_to_tool_call,
    _extract_key_date_lines,
    _fallback_search_query,
    _infer_tool_name_from_args,
    _normalize_read_path,
    _normalize_tool_name,
    _normalize_write_path,
    _paths_refer_same_page,
    _parse_trigger_settings,
    _tool_vault_write,
)


class _DummyResponse:
    def __init__(self, status_code=200, payload=None, url="http://localhost/test"):
        self.status_code = status_code
        self._payload = payload or {}
        self.request = httpx.Request("GET", url)
        self.url = url

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=self.request,
                response=httpx.Response(self.status_code, request=self.request),
            )

    def json(self):
        return self._payload


class _DummyClient:
    def __init__(self):
        self.last_write_payload = None

    def get(self, path, params=None):
        if path == "/api/pages/search":
            return _DummyResponse(payload={"pages": []}, url=f"http://localhost{path}")
        raise AssertionError(f"Unexpected GET path: {path}")

    def post(self, path, json=None):
        if path == "/api/file/read":
            return _DummyResponse(status_code=404, payload={}, url=f"http://localhost{path}")
        if path == "/api/file/write":
            self.last_write_payload = json or {}
            return _DummyResponse(payload={"ok": True}, url=f"http://localhost{path}")
        raise AssertionError(f"Unexpected POST path: {path}")


def test_normalize_tool_name_maps_read_page_content() -> None:
    assert _normalize_tool_name("Read Page Content") == "vault.read"


def test_vault_write_append_bare_title_is_not_treated_as_implicit_page_reference() -> None:
    client = _DummyClient()
    result = _tool_vault_write(
        client,
        {"path": "MyDates", "content": "Key Dates", "mode": "append"},
        {
            "current_editor_path": "/Journal/2026/02/02/HibbetDevEnvMeeting/HibbetDevEnvMeeting.md",
            "chat_page_path": "/Journal/2026/02/02/HibbetDevEnvMeeting",
            "chat_scope": "page",
            "vault_root_name": "",
        },
    )
    assert result.get("ok") is True
    assert client.last_write_payload is not None
    assert client.last_write_payload.get("path") == "/MyDates"


def test_vault_write_append_current_page_still_conflicts_when_context_is_ambiguous() -> None:
    client = _DummyClient()
    result = _tool_vault_write(
        client,
        {"path": "current page", "content": "Key Dates", "mode": "append"},
        {
            "current_editor_path": "/Journal/2026/02/02/HibbetDevEnvMeeting/HibbetDevEnvMeeting.md",
            "chat_page_path": "/Journal/2026/02/02/HibbetDevEnvMeeting",
            "chat_scope": "page",
            "vault_root_name": "",
        },
    )
    assert result.get("ok") is False
    error = result.get("error") or {}
    assert error.get("code") == "conflict"


def test_normalize_write_path_canonicalizes_non_matching_md_filename() -> None:
    assert _normalize_write_path("/Playpage/Key Dates.md") == "/Playpage/Key Dates/Key Dates.md"


def test_normalize_read_path_canonicalizes_non_matching_md_filename() -> None:
    assert _normalize_read_path("/Playpage/Key Dates.md") == "/Playpage/Key Dates/Key Dates.md"


def test_guard_blocks_tasks_list_without_task_intent() -> None:
    name, args, err = _apply_guard_to_tool_call(
        "tasks.list",
        {"query": "key date"},
        {"wants_tasks": False, "wants_search": False, "prompt": ""},
        tasks_done=False,
    )
    assert name == "tasks.list"
    assert args == {"query": "key date"}
    assert err is not None
    assert err.get("code") == "permission_denied"


def test_infer_tool_name_prefers_vault_search_for_plain_query() -> None:
    assert _infer_tool_name_from_args({"query": "key dates"}) == "vault.search"


def test_infer_tool_name_uses_tasks_when_tags_or_status_present() -> None:
    assert _infer_tool_name_from_args({"query": "x", "tags": ["key dates"]}) == "tasks.list"
    assert _infer_tool_name_from_args({"query": "x", "status": "todo"}) == "tasks.list"


def test_fallback_search_query_prefers_key_dates_token() -> None:
    prompt = "search this page for any key dates, summarize, and write them"
    assert _fallback_search_query(prompt) == "key dates"


def test_vault_write_rejects_empty_content() -> None:
    client = _DummyClient()
    result = _tool_vault_write(
        client,
        {"path": "Key dates", "content": "", "mode": "append"},
        {
            "current_editor_path": "/Playpage/Playpage.md",
            "chat_page_path": "/Playpage",
            "chat_scope": "page",
            "vault_root_name": "",
        },
    )
    assert result.get("ok") is False
    assert (result.get("error") or {}).get("code") == "invalid_args"


def test_vault_write_empty_content_synthesizes_when_last_read_exists() -> None:
    client = _DummyClient()
    source = "Created Monday 16 February 2026\nMeeting on 2026-02-02\n"
    result = _tool_vault_write(
        client,
        {"path": "Key dates", "content": "", "mode": "replace"},
        {
            "last_read_content": source,
            "current_editor_path": "/Playpage/Playpage.md",
            "chat_page_path": "/Playpage",
            "chat_scope": "page",
            "vault_root_name": "",
        },
    )
    assert result.get("ok") is True
    assert client.last_write_payload is not None
    written = client.last_write_payload.get("content") or ""
    assert "Monday 16 February 2026" in written
    assert "2026-02-02" in written


def test_paths_refer_same_page_for_folder_and_page_file() -> None:
    assert _paths_refer_same_page("/Playpage", "/Playpage/Playpage.md") is True


def test_parse_trigger_settings_ignores_non_string_tool_names() -> None:
    settings = {
        "tools": [
            {"name": {"bad": "value"}, "settings": "triggers=foo,bar"},
            {"name": "vault.search", "settings": "triggers=search,find"},
        ]
    }
    out = _parse_trigger_settings(settings)
    assert "vault.search" in out
    assert out["vault.search"] == ["search", "find"]


def test_extract_key_date_lines_finds_multiple_date_formats() -> None:
    text = """
    Created Monday 16 February 2026
    A non-date line
    Next milestone: 2026-03-01
    """
    lines = _extract_key_date_lines(text)
    assert any("Monday 16 February 2026" in line for line in lines)
    assert any("2026-03-01" in line for line in lines)
