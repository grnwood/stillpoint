from __future__ import annotations

from types import SimpleNamespace

import sp.app.ui.ai_chat_panel as ai_chat_panel_module
from sp.app.ui.ai_chat_panel import AIChatPanel, CHAT_SUMMARY_TITLE_PROMPT


def test_build_chat_summary_request_messages_weights_latest() -> None:
    panel = AIChatPanel.__new__(AIChatPanel)

    payload = panel._build_chat_summary_request_messages(
        [
            ("user", "earlier question"),
            ("assistant", "earlier answer"),
            ("user", "latest question"),
            ("assistant", "latest answer"),
        ]
    )

    assert payload[0] == {"role": "system", "content": CHAT_SUMMARY_TITLE_PROMPT}
    assert payload[1]["role"] == "user"
    assert "Latest chat messages to weight most heavily" in payload[1]["content"]
    assert "User: latest question" in payload[1]["content"]
    assert "Assistant: latest answer" in payload[1]["content"]


def test_request_chat_summary_title_uses_operations_model(monkeypatch) -> None:
    panel = AIChatPanel.__new__(AIChatPanel)
    statuses: list[str] = []
    stop_updates: list[str] = []
    captured: dict[str, object] = {}

    class FakeSignal:
        def __init__(self) -> None:
            self.callbacks = []

        def connect(self, callback) -> None:
            self.callbacks.append(callback)

    class FakeWorker:
        def __init__(self, server_config, messages, model, stream=False):
            captured["server_config"] = server_config
            captured["messages"] = messages
            captured["model"] = model
            captured["stream"] = stream
            self.finished = FakeSignal()
            self.failed = FakeSignal()

        def start(self) -> None:
            captured["started"] = True

        def request_cancel(self) -> None:
            captured["cancelled"] = True

    monkeypatch.setattr(ai_chat_panel_module, "ApiWorker", FakeWorker)
    monkeypatch.setattr(
        ai_chat_panel_module,
        "resolve_operations_server_and_model",
        lambda manager: ({"name": "operations-server", "default_model": "server-default"}, "operations-model"),
    )

    panel._api_worker = None
    panel._condense_worker = None
    panel._title_worker = None
    panel._agent_tool_worker = None
    panel._title_target_session_id = None
    panel.current_session_id = 22
    panel.current_server = {"name": "current-server", "default_model": "current-default"}
    panel.server_combo = SimpleNamespace(currentText=lambda: "current-server")
    panel.server_manager = SimpleNamespace(get_server=lambda name: {"name": name, "default_model": "server-default"})
    panel.store = SimpleNamespace(
        get_session_by_id=lambda session_id: {"id": session_id, "type": "chat", "last_server": "chat-server"},
        get_messages=lambda session_id: [("user", "Older topic"), ("assistant", "Newer topic")],
    )
    panel._config_default_server = lambda: "configured-server"
    panel._config_default_model = lambda: "configured-model"
    panel._set_status = lambda text, color=None: statuses.append(text)
    panel._update_stop_button = lambda: stop_updates.append("updated")

    panel._request_chat_summary_title({"id": 22, "type": "chat"})

    assert captured["started"] is True
    assert captured["stream"] is False
    assert captured["server_config"] == {"name": "operations-server", "default_model": "server-default"}
    assert captured["model"] == "operations-model"
    assert panel._title_target_session_id == 22
    assert statuses[-1] == "Generating chat summary..."
    assert stop_updates[-1] == "updated"


def test_handle_chat_summary_title_finished_renames_chat(monkeypatch) -> None:
    panel = AIChatPanel.__new__(AIChatPanel)
    renamed: list[tuple[int, str, bool]] = []
    loaded: list[int] = []
    statuses: list[str] = []
    stop_updates: list[str] = []

    panel._title_worker = object()
    panel._title_target_session_id = 31
    panel.current_session_id = 31
    panel.store = SimpleNamespace(
        rename_session=lambda session_id, title, manual=False: renamed.append((session_id, title, manual))
    )
    panel._load_chat_tree = lambda select_id=None: loaded.append(select_id)
    panel._set_status = lambda text, color=None: statuses.append(text)
    panel._update_stop_button = lambda: stop_updates.append("updated")

    panel._handle_chat_summary_title_finished("  Latest Action Plan Review.  ")

    assert renamed == [(31, "Latest Action Plan Review", False)]
    assert loaded == [31]
    assert statuses[-1] == "Renamed chat to 'Latest Action Plan Review'."
    assert stop_updates[-1] == "updated"
    assert panel._title_worker is None
    assert panel._title_target_session_id is None
