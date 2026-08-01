from __future__ import annotations

from sp.app.ui.main_window import (
    _auto_rename_source_body,
    _bounded_auto_rename_body,
    _normalize_auto_rename_title,
)


def test_bounded_auto_rename_body_keeps_short_content() -> None:
    body, truncated = _bounded_auto_rename_body("  First line\nSecond line  ")

    assert body == "First line\nSecond line"
    assert truncated is False


def test_bounded_auto_rename_body_limits_lines_and_characters() -> None:
    body, truncated = _bounded_auto_rename_body("\n".join(f"line {i}" for i in range(250)))
    assert len(body.splitlines()) == 200
    assert truncated is True

    body, truncated = _bounded_auto_rename_body("short line\n" + ("é" * 100), max_chars=20)
    assert body == "short line"
    assert truncated is True


def test_auto_rename_source_omits_matching_conventional_heading() -> None:
    body = (
        "# Pickles Are Pickles\n"
        "Created Saturday 01 August 2026\n"
        "---\n\n"
        "## Summary\n"
        "lets have a hamburger\n"
    )

    prepared = _auto_rename_source_body(body, "Pickles are Pickles")

    assert "Pickles Are Pickles" not in prepared
    assert "lets have a hamburger" in prepared


def test_auto_rename_source_keeps_nonmatching_or_nonleading_heading() -> None:
    assert _auto_rename_source_body("# Custom Topic\nBody\n", "Page Name") == "# Custom Topic\nBody\n"
    assert _auto_rename_source_body("Body first\n# Page Name\n", "Page Name") == "Body first\n# Page Name\n"


def test_hidden_journal_page_is_active_rename_target(main_window) -> None:
    main_window._show_journal_in_nav = False
    main_window._select_tree_path("/PageA/PageA.md")
    main_window.current_path = "/Journal/2026/08/01/01.md"

    assert main_window._active_rename_target() == "/Journal/2026/08/01"


def test_manual_rename_prompts_for_hidden_active_journal_page(main_window, monkeypatch) -> None:
    from sp.app.ui import main_window as main_window_module

    calls: list[tuple[str, str, str]] = []
    main_window._show_journal_in_nav = False
    main_window.current_path = "/Journal/2026/08/01/Pickles/Pickles.md"
    monkeypatch.setattr(
        main_window_module.QInputDialog,
        "getText",
        lambda *args, **kwargs: ("Hamburger Notes", True),
    )
    monkeypatch.setattr(
        main_window,
        "_handle_inline_rename",
        lambda parent, source, name: calls.append((parent, source, name)),
    )

    main_window._trigger_manual_rename()

    assert calls == [
        ("/Journal/2026/08/01", "/Journal/2026/08/01/Pickles", "Hamburger Notes")
    ]


def test_normalize_auto_rename_title_handles_common_model_wrappers() -> None:
    assert _normalize_auto_rename_title('<think>reasoning</think>\n```text\n  "Project Launch Plan.md."  \n```') == "Project Launch Plan"
    assert _normalize_auto_rename_title("First Useful Line\nExplanation follows") == "First Useful Line"


def test_normalize_auto_rename_title_rejects_unsafe_names() -> None:
    assert _normalize_auto_rename_title("../Unsafe") is None
    assert _normalize_auto_rename_title("CON") is None
    assert _normalize_auto_rename_title("Unsafe\u007fTitle") is None
    assert _normalize_auto_rename_title("   ") is None


def test_auto_rename_uses_operations_model_and_original_selection(main_window, monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeSignal:
        def __init__(self) -> None:
            self.callbacks = []

        def connect(self, callback) -> None:
            self.callbacks.append(callback)

        def emit(self, value: str) -> None:
            for callback in list(self.callbacks):
                callback(value)

    class FakeWorker:
        def __init__(self, server_config, messages, model, stream=False, parent=None):
            captured.update(server=server_config, messages=messages, model=model, stream=stream)
            self.finished = FakeSignal()
            self.failed = FakeSignal()
            self.started = False

        def start(self) -> None:
            self.started = True

        def deleteLater(self) -> None:
            return None

        def request_cancel(self) -> None:
            return None

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self):
            return self.payload

    class FakeHttp:
        def __init__(self) -> None:
            self.renames = []

        def post(self, path, json=None, **kwargs):
            if path == "/api/file/read":
                return FakeResponse({"content": "# Existing Heading\n\nLaunch planning details."})
            if path == "/api/file/rename":
                self.renames.append(json)
                return FakeResponse(
                    {"page_map": {"/PageA/PageA.md": "/Launch Plan/Launch Plan.md"}}
                )
            raise AssertionError(path)

    from sp.app import config
    from sp.app.ui import ai_chat_panel

    monkeypatch.setattr(config, "load_enable_ai_chats", lambda: True)
    monkeypatch.setattr(ai_chat_panel, "ApiWorker", FakeWorker)
    monkeypatch.setattr(
        ai_chat_panel,
        "resolve_operations_server_and_model",
        lambda: ({"name": "Primary"}, "utility-small"),
    )
    main_window.http = FakeHttp()
    assert main_window._action_rename_manual.text() == "Rename (Manual)"
    assert main_window._action_rename_manual.shortcut().toString() == "F2"
    assert main_window._action_rename_auto.text() == "Rename Auto (AI)"
    main_window._select_tree_path("/PageA/PageA.md")
    monkeypatch.setattr(main_window, "_populate_vault_tree", lambda: None)
    monkeypatch.setattr(main_window, "_open_file", lambda *args, **kwargs: None)
    monkeypatch.setattr(main_window, "_register_link_path_map", lambda path_map: None)

    main_window._trigger_auto_rename()
    worker = main_window._auto_rename_worker
    assert worker is not None and worker.started is True
    assert captured["model"] == "utility-small"
    assert captured["stream"] is False

    # Navigation changes while AI runs must not change the retained source target.
    main_window._select_tree_path("/PageB/PageB.md")
    worker.finished.emit("Launch Plan")

    assert main_window.http.renames == [{"from": "/PageA", "to": "/Launch Plan"}]
    assert main_window._auto_rename_worker is None
