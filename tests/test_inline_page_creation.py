from __future__ import annotations

import httpx

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest


def test_inline_page_enter_opens_new_page_and_focuses_editor(
    main_window, monkeypatch, qapp
) -> None:
    original_post = main_window.http.post

    def create_page(path, json=None):
        if path != "/api/path/create":
            return original_post(path, json=json)
        target = str((json or {}).get("path") or "")
        name = target.rsplit("/", 1)[-1]
        main_window._test_tree_state.append(
            {
                "name": name,
                "path": target,
                "open_path": f"{target}/{name}.md",
                "children": [],
            }
        )
        request = httpx.Request("POST", f"http://localhost{path}")
        return httpx.Response(200, request=request)

    monkeypatch.setattr(main_window.http, "post", create_page)
    monkeypatch.setattr(main_window, "_apply_new_page_template", lambda *_args: None)
    monkeypatch.setattr(main_window, "_mark_homebase_unsynced_local_change", lambda: None)
    monkeypatch.setattr(main_window, "_schedule_homebase_sync", lambda _reason: None)

    main_window._start_inline_creation("/", QPoint(10, 10))
    inline_editor = main_window._inline_editor
    assert inline_editor is not None

    QTest.keyClicks(inline_editor, "Fresh Page")
    QTest.keyClick(inline_editor, Qt.Key_Return)
    qapp.processEvents()

    assert main_window.current_path == "/Fresh Page/Fresh Page.md"
    assert main_window.editor.hasFocus()
    assert main_window.editor.textCursor().position() == len(
        main_window.editor.toPlainText()
    )
