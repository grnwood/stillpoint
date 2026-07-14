from __future__ import annotations

from types import SimpleNamespace

from sp.app.ui.main_window import MainWindow
from sp.app.ui.markdown_editor import MarkdownEditor


def test_link_context_opens_link_target_in_new_editor(qtbot) -> None:
    editor = MarkdownEditor()
    qtbot.addWidget(editor)
    editor._current_path = "/Parent/Parent.md"
    opened: list[str] = []
    editor.set_open_in_window_callback(opened.append)

    editor._open_page_in_new_editor("Child")

    assert opened == ["Child"]


def test_non_link_context_still_opens_current_page(qtbot) -> None:
    editor = MarkdownEditor()
    qtbot.addWidget(editor)
    editor._current_path = "/Parent/Parent.md"
    opened: list[str] = []
    editor.set_open_in_window_callback(opened.append)

    editor._open_page_in_new_editor()

    assert opened == ["/Parent/Parent.md"]


def test_page_editor_target_resolution_matches_normal_link_navigation() -> None:
    window = SimpleNamespace(
        current_path="/Parent/Parent.md",
        vault_root_name="Vault",
        _home_page_path=lambda: "/Home/Home.md",
        _vault_root_page_path=lambda: "/Vault/Vault.md",
        _is_attachment_link=lambda value: value.endswith(".pdf"),
        _is_local_file_link=lambda value: value.startswith("./"),
        _split_link_anchor=lambda value: (value.split("#", 1) + [None])[:2] if "#" in value else (value, None),
        _normalize_editor_path=lambda value: {
            ":Top:Child": "/Top/Child/Child.md",
            "/Top/Child/Child.md": "/Top/Child/Child.md",
        }[value],
        _resolve_case_insensitive_rel_path=lambda value: value,
    )

    resolve = lambda target: MainWindow._resolve_page_editor_target(window, target)

    assert resolve("Child#Details") == "/Parent/Child/Child.md"
    assert resolve(":Top:Child#Details") == "/Top/Child/Child.md"
    assert resolve("/Top/Child/Child.md#Details") == "/Top/Child/Child.md"
    assert resolve("https://example.com") is None
    assert resolve("manual.pdf") is None
