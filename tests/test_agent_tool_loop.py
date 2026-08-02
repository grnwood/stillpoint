import httpx

from sp.app.ui.agent_tool_loop import (
    _apply_guard_to_tool_call,
    _child_page_path,
    _daily_page_repeated_as_child,
    _extract_key_date_lines,
    _fallback_search_query,
    _format_agent_activity,
    _infer_tool_name_from_args,
    _normalize_read_path,
    _normalize_agent_page_links,
    _normalize_tool_name,
    _normalize_write_path,
    _paths_refer_same_page,
    _parse_trigger_settings,
    parse_agent_message,
    _tool_vault_write,
    _tool_vault_create_child,
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


def test_normalize_tool_name_maps_create_child_page() -> None:
    assert _normalize_tool_name("Create Child Page") == "vault.create_child"


def test_agent_activity_is_a_concise_one_liner_without_content_payload() -> None:
    activity = _format_agent_activity(
        "vault.write",
        {
            "path": "/Journal/2026/08/02/Summary/Summary.md",
            "content": "very detailed body\nwith another line",
            "mode": "replace",
        },
    )
    assert activity == "[Agent: writing /Journal/2026/08/02/Summary/Summary.md…]"
    assert "detailed body" not in activity


def test_agent_activity_describes_child_creation_without_body_details() -> None:
    activity = _format_agent_activity(
        "vault.create_child",
        {
            "parent_path": "/Journal/2026/08/02/02.md",
            "title": "ClubGlove Results",
            "content": "# large generated page",
        },
    )
    assert activity == (
        "[Agent: creating ClubGlove Results under /Journal/2026/08/02/02.md…]"
    )


def test_parse_agent_message_accepts_python_style_tool_call_without_leaking_content() -> None:
    raw = (
        "Tool call: vault.create_child "
        "{'parent_path': '02', 'title': 'MSC Summary', "
        "'content': '# MSC Summary\\nA long generated body'}"
    )
    parsed = parse_agent_message(raw)
    assert parsed is not None
    assert parsed["type"] == "tool_request"
    call = parsed["calls"][0]
    assert call["name"] == "vault.create_child"
    assert call["args"]["parent_path"] == "02"
    assert call["args"]["content"].startswith("# MSC Summary\n")


def test_agent_links_strip_journal_prefix_from_mixed_root_path() -> None:
    malformed = (
        "[:Journal:2026:08:02:Clubglove_Results:"
        "[/2-Projects/Acushnet/1-Projects/ClubGlove/Design/LineItemXml/LineItemXml.md|"
        "LineItemXml]"
    )
    assert _normalize_agent_page_links(malformed) == (
        "[:2-Projects:Acushnet:1-Projects:ClubGlove:Design:LineItemXml|LineItemXml]"
    )


def test_agent_links_convert_local_markdown_page_link_to_root_colon_link() -> None:
    content = "[Requirements](/2-Projects/ClubGlove/Requirements/Requirements.md)"
    assert _normalize_agent_page_links(content) == (
        "[:2-Projects:ClubGlove:Requirements|Requirements]"
    )


def test_agent_links_leave_external_links_and_fenced_code_unchanged() -> None:
    content = (
        "[OpenAI](https://openai.com)\n"
        "```markdown\n"
        "[Local](/Projects/Local/Local.md)\n"
        "```\n"
    )
    assert _normalize_agent_page_links(content) == content


def test_agent_links_add_label_to_unlabeled_colon_link() -> None:
    assert _normalize_agent_page_links("[:Projects:Alpha]") == (
        "[:Projects:Alpha|Alpha]"
    )


def test_child_page_path_uses_daily_page_folder_without_repeating_day() -> None:
    assert _child_page_path(
        "/Journal/2026/08/02/02.md",
        "msc-vault-search-summary",
    ) == (
        "/Journal/2026/08/02/msc-vault-search-summary/"
        "msc-vault-search-summary.md"
    )


def test_vault_create_child_writes_directly_beneath_daily_page() -> None:
    client = _DummyClient()
    result = _tool_vault_create_child(
        client,
        {
            "parent_path": "/Journal/2026/08/02/02.md",
            "title": "msc-vault-search-summary",
            "content": "# MSC vault search summary\n",
        },
        {},
    )
    assert result.get("ok") is True
    assert client.last_write_payload == {
        "path": (
            "/Journal/2026/08/02/msc-vault-search-summary/"
            "msc-vault-search-summary.md"
        ),
        "content": "# MSC vault search summary\n",
    }


def test_vault_create_child_resolves_today_label_from_daily_open_context() -> None:
    client = _DummyClient()
    result = _tool_vault_create_child(
        client,
        {
            "parent_path": "today's journal",
            "title": "Call notes",
            "content": "# Call notes\n",
        },
        {"last_daily_path": "/Journal/2026/08/02/02.md"},
    )
    assert result.get("ok") is True
    assert client.last_write_payload["path"] == (
        "/Journal/2026/08/02/Call notes/Call notes.md"
    )


def test_daily_duplicate_child_path_is_detected_from_daily_open_result() -> None:
    details = _daily_page_repeated_as_child(
        "/Journal/2026/08/02/02/msc-vault-search-summary/msc-vault-search-summary.md",
        "/Journal/2026/08/02/02.md",
    )
    assert details is not None
    assert details["repeated_folder"] == "/Journal/2026/08/02/02"


def test_vault_write_rejects_repeated_daily_page_folder() -> None:
    client = _DummyClient()
    result = _tool_vault_write(
        client,
        {
            "path": "/Journal/2026/08/02/02/msc-vault-search-summary/msc-vault-search-summary.md",
            "content": "# Summary\n",
            "mode": "replace",
        },
        {"last_daily_path": "/Journal/2026/08/02/02.md"},
    )
    assert result.get("ok") is False
    assert (result.get("error") or {}).get("code") == "invalid_args"
    assert client.last_write_payload is None


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
    assert client.last_write_payload.get("path") == "/MyDates/MyDates.md"


def test_vault_write_append_current_page_resolves_when_chat_and_editor_refer_same_page() -> None:
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
    assert result.get("ok") is True
    output = result.get("output") or {}
    assert output.get("path") == "/Journal/2026/02/02/HibbetDevEnvMeeting"


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
