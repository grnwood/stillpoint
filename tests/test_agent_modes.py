import json
import sqlite3

import httpx
import pytest
from PySide6 import QtCore

from sp.ai.agent_modes import conversation_history, tool_allowed
from sp.app import config
from sp.app.ui import agent_tool_loop as loop
from sp.app.ui.ai_chat_panel import AIChatPanel, AIChatStore
from sp.app.folder_navigator.chat_tools import FolderAgentChatWorker


@pytest.mark.parametrize("worker_class", [loop.AgentToolChatWorker, loop.AgentToolLoopWorker])
def test_research_blocks_model_requested_mutations(worker_class, monkeypatch, qapp):
    requests = []
    client = httpx.Client(base_url="http://vault", transport=httpx.MockTransport(
        lambda request: requests.append(request) or httpx.Response(200, json={"content": "text"})
    ))
    monkeypatch.setattr(loop, "_clone_http_client", lambda source: source)
    worker = worker_class(config=loop.AgentLoopConfig({}, "test", "Test", mode="research"),
                          client=client, user_prompt="Create a new page today", context={})
    replies = iter([
        {"type": "tool_request", "calls": [
            {"id": name, "name": name, "args": {"path": "/Page/Page.md", "content": "text"}}
            for name in ("vault.write", "vault.create_child", "daily.open")
        ]},
        {"type": "final", "content": "Choose Create / Edit to make changes."},
    ])
    seen = []
    monkeypatch.setattr(worker, "_send_llm", lambda messages: seen.append(list(messages)) or json.dumps(next(replies)))
    worker.run()
    assert requests == []
    results = [json.loads(item["content"]) for item in seen[-1] if item["role"] == "assistant"]
    assert len(results) == 3
    assert all(result["error"]["code"] == "permission_denied" for result in results)


def test_main_agent_keeps_history_and_allows_clarification_without_forced_write(monkeypatch, qapp):
    monkeypatch.setattr(loop, "_clone_http_client", lambda source: source)
    client = httpx.Client(base_url="http://vault", transport=httpx.MockTransport(lambda request: pytest.fail("Unexpected tool request")))
    history = [("user", "Our project is Atlas."), ("assistant", "Atlas uses a REST API."), ("app", "Status")]
    worker = loop.AgentToolChatWorker(
        config=loop.AgentLoopConfig({}, "test", "Test", mode="create"), client=client,
        user_prompt="How would I write a summary? Don't change anything yet.", context={}, history=history,
    )
    seen, final, failed = [], [], []
    monkeypatch.setattr(worker, "_send_llm", lambda messages: seen.append(list(messages)) or json.dumps(
        {"type": "final", "content": "Which section of Atlas should the draft cover?"}))
    worker.finalMessage.connect(final.append)
    worker.failed.connect(failed.append)
    worker.run()
    assert len(seen) == 1
    assert seen[0][1:3] == [{"role": role, "content": text} for role, text in history[:2]]
    assert "Don't change anything yet" in seen[0][-1]["content"]
    assert final == ["Which section of Atlas should the draft cover?"]
    assert failed == []


def test_research_does_not_redirect_read_to_tasks_from_keywords(monkeypatch, qapp):
    requests = []
    client = httpx.Client(base_url="http://vault", transport=httpx.MockTransport(
        lambda request: requests.append(request) or httpx.Response(200, json={"content": "Tasks API design"})
    ))
    monkeypatch.setattr(loop, "_clone_http_client", lambda source: source)
    worker = loop.AgentToolChatWorker(config=loop.AgentLoopConfig({}, "test", "Test", mode="research"),
                                     client=client, user_prompt="Compare the tasks API and billing API", context={})
    replies = iter([
        {"type": "tool_request", "calls": [{"id": "read", "name": "vault.read", "args": {"path": "/API/API.md"}}]},
        {"type": "final", "content": "The APIs differ."},
    ])
    monkeypatch.setattr(worker, "_send_llm", lambda messages: json.dumps(next(replies)))
    worker.run()
    assert [request.url.path for request in requests] == ["/api/file/read"]


def test_folder_research_cannot_create_files(tmp_path, monkeypatch, qapp):
    worker = FolderAgentChatWorker(server_config={}, model="test", system_prompt="Test",
                                   user_prompt="Create a page", root=tmp_path, current_path="", dirty_paths=set(), mode="research")
    replies = iter([
        {"type": "tool_request", "calls": [{"id": "write", "name": "folder.create", "args": {"path": "new.md", "content": "text"}}]},
        {"type": "final", "content": "Research cannot create pages."},
    ])
    monkeypatch.setattr(worker, "_send_llm", lambda messages: json.dumps(next(replies)))
    worker.run()
    assert not (tmp_path / "new.md").exists()


def test_history_has_a_bounded_budget_and_ignores_app_events():
    history = [("user", "x" * 20_000), ("app", "status"), ("assistant", "y" * 20_000)] * 10
    result = conversation_history(history)
    assert len(result) <= 12
    assert sum(len(message["content"]) for message in result) <= 45_000
    assert all(message["role"] in {"user", "assistant"} for message in result)
    assert not tool_allowed("chat", "vault.search")
    assert not tool_allowed("research", "folder.write")
    assert tool_allowed("create", "vault.write")


def test_mode_migrates_and_persists_per_conversation(tmp_path):
    database = tmp_path / "chat.db"
    store = AIChatStore(db_path=database)
    first = store._create_session("First", None, "/First", "chat")
    second = store._create_session("Second", None, "/Second", "chat")
    # Simulate a database created by the previous application version.
    with sqlite3.connect(database) as conn:
        conn.execute("ALTER TABLE sessions DROP COLUMN agent_mode")
    store = AIChatStore(db_path=database)
    assert store.get_session_by_id(first)["agent_mode"] == "chat"
    store.update_session_agent_mode(first, "research")
    store = AIChatStore(db_path=database)
    assert store.get_session_by_id(first)["agent_mode"] == "research"
    assert store.get_session_by_id(second)["agent_mode"] == "chat"


class FakeWorker(QtCore.QObject):
    chunk = QtCore.Signal(str)
    finished = QtCore.Signal(str)
    failed = QtCore.Signal(str)
    visionFallback = QtCore.Signal()
    toolMessage = QtCore.Signal(str)
    finalMessage = QtCore.Signal(str)

    def __init__(self, *args, **kwargs):
        super().__init__()
        self.kwargs = kwargs

    def start(self):
        pass


def test_ui_defaults_to_chat_and_explicitly_routes_and_restores_modes(qtbot, tmp_path, monkeypatch):
    from sp.app.ui import ai_chat_panel as ui
    monkeypatch.setattr(config, "GLOBAL_CONFIG", tmp_path / "preferences.json")
    monkeypatch.setattr(config, "load_global_enable_ai_agents", lambda: True)
    monkeypatch.setattr(config, "is_agent_tool_approved", lambda key: True)
    monkeypatch.setattr(ui, "ApiWorker", FakeWorker)
    monkeypatch.setattr(ui, "AgentToolChatWorker", FakeWorker)
    store = AIChatStore(db_path=tmp_path / "chat.db")
    client = httpx.Client(base_url="http://vault", transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})))
    panel = AIChatPanel(store=store, api_client=client)
    qtbot.addWidget(panel)
    panel.vault_root = str(tmp_path)
    panel.current_server = {"name": "test"}
    assert panel._selected_agent_mode() == "chat"
    panel._start_send("Find API decisions")
    assert panel._api_worker is not None
    assert panel._agent_tool_worker is None
    assert not panel.agent_mode_combo.isEnabled()
    panel._api_worker = None
    panel._update_stop_button()
    panel.agent_mode_combo.setCurrentIndex(1)
    first = panel.current_session_id
    assert store.get_session_by_id(first)["agent_mode"] == "research"
    assert not panel.agent_mode_combo.itemIcon(1).isNull()
    assert not panel.agent_mode_combo.itemIcon(2).isNull()
    panel.messages = [("user", "Compare Atlas with Orion"), ("assistant", "Atlas has a simpler API.")]
    panel._start_send("Read the decision pages")
    assert panel._agent_tool_worker.kwargs["config"].mode == "research"
    assert panel._agent_tool_worker.kwargs["history"] == panel.messages[:2]
    panel._agent_tool_worker = None
    second = store._create_session("Other", None, "/Other", "chat")
    panel._load_chat_messages(second)
    assert panel._selected_agent_mode() == "chat"
    panel._load_chat_messages(first)
    assert panel._selected_agent_mode() == "research"
    client.close()
