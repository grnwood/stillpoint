"""Folder chat tools stay inside their root and preserve safe editor writes."""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from sp.app.folder_navigator.chat_tools import FolderAgentChatWorker, FolderChatTools
from sp.app.folder_navigator import chat as folder_chat


def test_folder_tools_search_read_create_and_write(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    source = root / "src" / "main.py"
    source.parent.mkdir()
    source.write_text("def greet():\n    return 'hello'\n", encoding="utf-8")
    tools = FolderChatTools(root)

    matches = tools.call("folder.search", {"query": "greet"})["matches"]
    assert matches == [{"path": "src/main.py", "line": 1, "snippet": "def greet():"}]
    read = tools.call("folder.read", {"path": "src/main.py"})
    assert "hello" in read["content"]

    created = tools.call("folder.create", {"path": "src/helper.py", "content": "VALUE = 1\n"})
    assert created == {"path": "src/helper.py", "created": True}
    assert tools.call("folder.create", {"path": "src/helper.py", "content": "other"})["error"]["code"] == "conflict"
    assert tools.call("folder.write", {
        "path": "src/main.py", "content": "\n# updated\n", "mode": "append",
        "expected_mtime_ns": read["mtime_ns"],
    })["written"]
    assert source.read_text(encoding="utf-8").endswith("# updated\n")
    assert tools.call("folder.write", {
        "path": "src/main.py", "content": "stale", "expected_mtime_ns": read["mtime_ns"],
    })["error"]["code"] == "conflict"


def test_folder_tools_reject_escape_and_dirty_files(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "private.txt"
    outside.write_text("secret", encoding="utf-8")
    (root / "link.txt").symlink_to(outside)
    local = root / "open.txt"
    local.write_text("original", encoding="utf-8")
    tools = FolderChatTools(root, dirty_paths={local})

    for path in ("../private.txt", "link.txt", str(outside)):
        assert "error" in tools.call("folder.read", {"path": path})
    assert "error" in tools.call("folder.create", {"path": "../new.txt", "content": "x"})
    assert "error" in tools.call("folder.create", {"path": ".git/config", "content": "x"})
    assert not (tmp_path / "new.txt").exists()
    read = tools.call("folder.read", {"path": "open.txt"})
    assert tools.call("folder.write", {
        "path": "open.txt", "content": "changed", "expected_mtime_ns": read["mtime_ns"],
    })["error"]["code"] == "conflict"
    assert local.read_text(encoding="utf-8") == "original"


def test_folder_write_preserves_encoding_and_line_endings(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    source = root / "unicode.txt"
    source.write_bytes("Café\r\n".encode("utf-16"))
    tools = FolderChatTools(root)
    read = tools.call("folder.read", {"path": "unicode.txt"})
    assert read["content"] == "Café\n"
    assert tools.call("folder.write", {
        "path": "unicode.txt", "content": "more\n", "mode": "append",
        "expected_mtime_ns": read["mtime_ns"],
    })["written"]
    assert source.read_bytes().decode("utf-16") == "Café\r\nmore\r\n"


def test_folder_tools_extract_pdf_and_calamine_spreadsheet_text(tmp_path):
    pytest.importorskip("python_calamine")
    xlsxwriter = pytest.importorskip("xlsxwriter")
    root = tmp_path / "documents"
    root.mkdir()
    pdf = Path(__file__).resolve().parent.parent / "dev-assets" / "richesrestaurant.pdf"
    (root / "menu.pdf").write_bytes(pdf.read_bytes())
    workbook = xlsxwriter.Workbook(str(root / "budget.xlsx"))
    sheet = workbook.add_worksheet("Revenue")
    sheet.write_row(0, 0, ["Region", "Amount"])
    sheet.write_row(1, 0, ["West", 42])
    workbook.close()
    tools = FolderChatTools(root)

    spreadsheet = tools.call("folder.read", {"path": "budget.xlsx"})
    assert "Sheet: Revenue" in spreadsheet["content"]
    assert "West | 42" in spreadsheet["content"]
    assert tools.call("folder.search", {"query": "West"})["matches"][0]["path"] == "budget.xlsx"
    focused = tools.call("folder.read", {"path": "budget.xlsx", "query": "West"})
    assert "West | 42" in focused["content"] and focused["match_count"] == 1
    pdf_text = tools.call("folder.read", {"path": "menu.pdf"})["content"]
    assert "Riches" in pdf_text or "Restaurant" in pdf_text
    assert tools.call("folder.write", {
        "path": "menu.pdf", "content": "overwrite", "expected_mtime_ns": 0,
    })["error"]["code"] == "invalid_args"


def test_folder_worker_uses_tool_results_before_final(tmp_path, monkeypatch, qapp):
    root = tmp_path / "project"
    root.mkdir()
    replies = iter([
        json.dumps({"type": "tool_request", "calls": [{
            "id": "read-1", "name": "folder.create",
            "args": {"path": "answer.txt", "content": "done\n"},
        }]}),
        json.dumps({"type": "final", "content": "Created answer.txt"}),
    ])
    worker = FolderAgentChatWorker(
        server_config={}, model="test", system_prompt="test", user_prompt="Create a file",
        root=root, current_path="", dirty_paths=set(),
        history=[("user", "Earlier request"), ("assistant", "Earlier result")],
    )
    seen = []
    monkeypatch.setattr(worker, "_send_llm", lambda messages: (seen.append(messages.copy()), next(replies))[1])
    final = []
    worker.finalMessage.connect(final.append)
    worker.run()
    assert (root / "answer.txt").read_text(encoding="utf-8") == "done\n"
    assert final == ["Created answer.txt"]
    assert seen[0][1:3] == [
        {"role": "user", "content": "Earlier request"},
        {"role": "assistant", "content": "Earlier result"},
    ]
    assert any("tool_result" in message["content"] for message in seen[1])


def test_folder_chat_prompt_runs_local_create_tool(tmp_path, monkeypatch, qapp):
    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.setattr(folder_chat, "folder_chat_database", lambda _root: tmp_path / "chat.db")
    monkeypatch.setattr(folder_chat.config, "load_global_enable_ai_agents", lambda: True)
    monkeypatch.setattr(folder_chat.config, "is_agent_tool_approved", lambda _key: True)
    replies = iter([
        json.dumps({"type": "tool_request", "calls": [{
            "id": "create-1", "name": "folder.create",
            "args": {"path": "notes/answer.txt", "content": "done\n"},
        }]}),
        json.dumps({"type": "final", "content": "Created notes/answer.txt"}),
    ])
    monkeypatch.setattr(FolderAgentChatWorker, "_send_llm", lambda _self, _messages: next(replies))
    monkeypatch.setattr(FolderAgentChatWorker, "start", FolderAgentChatWorker.run)
    panel = folder_chat.FolderChatPanel(
        root, lambda _q, _s: [], lambda _q, _s: [], lambda _p: None,
    )
    panel.current_server = {"name": "test"}
    panel._start_send("Create notes/answer.txt")

    assert (root / "notes" / "answer.txt").read_text(encoding="utf-8") == "done\n"
    assert panel.messages[-1] == ("assistant", "Created notes/answer.txt")
    panel.close()


def test_folder_chat_created_file_enters_quick_open(tmp_path, monkeypatch, qapp):
    from sp.app.folder_navigator.window import Window

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    window = Window(tmp_path)
    created = tmp_path / "answer.txt"
    created.write_text("done\n", encoding="utf-8")
    window._chat_file_written(str(created))

    assert created in window.catalog_candidates("answer", tmp_path)
    window.close()
