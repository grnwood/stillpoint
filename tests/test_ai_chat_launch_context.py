from __future__ import annotations

from types import SimpleNamespace

from sp.app.ui.ai_chat_panel import AIChatPanel


class _DebugToggle:
    def __init__(self, checked: bool) -> None:
        self._checked = checked

    def isChecked(self) -> bool:
        return self._checked


def test_agent_activity_hides_raw_tool_result_when_debug_is_off(monkeypatch) -> None:
    panel = AIChatPanel.__new__(AIChatPanel)
    panel.debug_checkbox = _DebugToggle(False)
    panel._agent_placeholder_index = None
    panel._agent_progress_lines = []
    panel._last_agent_read_path = None
    progress: list[str] = []
    assistant_messages: list[str] = []
    monkeypatch.setattr(panel, "_update_agent_progress", progress.append)
    monkeypatch.setattr(panel, "_append_assistant_message", assistant_messages.append)

    panel._handle_agent_tool_message("Agent activity: [Agent: searching the vault for ClubGlove…]")
    panel._handle_agent_tool_message(
        "Tool result: vault.search status=ok matches=4 details={'large': 'payload'}"
    )

    assert progress == ["[Agent: searching the vault for ClubGlove…]"]
    assert assistant_messages == []


def test_agent_activity_keeps_raw_call_and_result_in_debug_entry(monkeypatch) -> None:
    panel = AIChatPanel.__new__(AIChatPanel)
    panel.debug_checkbox = _DebugToggle(True)
    panel._agent_placeholder_index = None
    panel._agent_progress_lines = []
    panel._last_agent_read_path = None
    panel._active_tool_debug = None
    panel._debug_entries = []
    progress: list[str] = []
    updates: list[tuple[int, str, bool]] = []
    monkeypatch.setattr(panel, "_update_agent_progress", progress.append)

    def _append_debug(title, content, *, open_state, anchor_index):
        panel._debug_entries.append({"title": title, "content": content})
        return len(panel._debug_entries) - 1

    monkeypatch.setattr(panel, "_append_debug_entry", _append_debug)
    monkeypatch.setattr(
        panel,
        "_update_debug_entry",
        lambda entry_id, content, *, open_state: updates.append((entry_id, content, open_state)),
    )

    panel._handle_agent_tool_message("Agent activity: [Agent: reading /Projects/Alpha/Alpha.md…]")
    panel._handle_agent_tool_message(
        "Tool call: vault.read {'path': '/Projects/Alpha/Alpha.md'}"
    )
    panel._handle_agent_tool_message(
        "Tool result: vault.read status=ok path=/Projects/Alpha/Alpha.md bytes=400"
    )

    assert progress == ["[Agent: reading /Projects/Alpha/Alpha.md…]"]
    assert panel._debug_entries[0]["content"].startswith("Tool call: vault.read")
    assert updates and "Tool result: vault.read status=ok" in updates[0][1]


def test_unparsed_tool_payload_is_never_rendered_as_non_debug_final(monkeypatch) -> None:
    panel = AIChatPanel.__new__(AIChatPanel)
    panel.debug_checkbox = _DebugToggle(False)
    panel.messages = [("assistant", "Running agent tools...")]
    panel._agent_placeholder_index = 0
    panel.current_session_id = None
    panel._agent_progress_lines = []
    panel._agent_tool_worker = object()
    panel.send_btn = SimpleNamespace(setEnabled=lambda _enabled: None)
    monkeypatch.setattr(panel, "_set_status", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(panel, "_render_messages", lambda: None)
    monkeypatch.setattr(panel, "_update_stop_button", lambda: None)

    panel._handle_agent_final(
        "Tool call: vault.create_child {'content': 'an extremely long generated page'}"
    )

    assert panel.messages[0] == (
        "assistant",
        "[Agent: unable to parse the tool operation response.]",
    )


def test_open_chat_for_page_attaches_page_context(monkeypatch) -> None:
    panel = AIChatPanel.__new__(AIChatPanel)
    calls: list[tuple[str, object]] = []

    monkeypatch.setattr(panel, "_new_chat", lambda: calls.append(("new_chat", None)))
    monkeypatch.setattr(panel, "set_current_page", lambda path: calls.append(("set_current_page", path)))
    monkeypatch.setattr(
        panel,
        "ensure_context_page_ref",
        lambda path, index=True: calls.append(("ensure_context_page_ref", (path, index))),
    )

    panel.open_chat_for_page("/PageA/PageA.md")

    assert calls == [
        ("new_chat", None),
        ("set_current_page", "/PageA/PageA.md"),
        ("ensure_context_page_ref", ("/PageA/PageA.md", True)),
    ]


def test_start_new_ai_chat_uses_current_page_context(main_window, monkeypatch) -> None:
    from sp.app import config

    main_window.current_path = "/PageB/PageB.md"
    monkeypatch.setattr(config, "load_enable_ai_chats", lambda: True)
    monkeypatch.setattr(main_window, "_active_ai_chat_panel", lambda: None)
    monkeypatch.setattr(main_window, "_ensure_right_panel_visible", lambda: None)
    monkeypatch.setattr(main_window.right_panel, "ai_chat_panel", object())

    calls: list[tuple[str, object, object]] = []
    monkeypatch.setattr(
        main_window.right_panel,
        "focus_ai_chat",
        lambda page_path=None, create=False: calls.append(("focus_ai_chat", page_path, create)),
    )
    monkeypatch.setattr(
        main_window.right_panel,
        "focus_ai_chat_input",
        lambda: calls.append(("focus_ai_chat_input", None, None)),
    )

    main_window._start_new_ai_chat()

    assert calls == [
        ("focus_ai_chat", "/PageB/PageB.md", True),
        ("focus_ai_chat_input", None, None),
    ]


def test_start_new_ai_chat_detached_uses_current_page_context(main_window, monkeypatch) -> None:
    from sp.app import config

    main_window.current_path = "/PageC/PageC.md"
    monkeypatch.setattr(config, "load_enable_ai_chats", lambda: True)

    detached_calls: list[tuple[str, object]] = []
    detached = SimpleNamespace(
        open_chat_for_page=lambda path: detached_calls.append(("open_chat_for_page", path)),
        focus_input=lambda: detached_calls.append(("focus_input", None)),
    )
    monkeypatch.setattr(main_window, "_active_ai_chat_panel", lambda: detached)
    main_window._detached_ai_chat_window = None

    main_window._start_new_ai_chat()

    assert detached_calls == [
        ("open_chat_for_page", "/PageC/PageC.md"),
        ("focus_input", None),
    ]
