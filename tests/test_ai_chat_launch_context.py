from __future__ import annotations

from types import SimpleNamespace

from sp.app.ui.ai_chat_panel import AIChatPanel


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
