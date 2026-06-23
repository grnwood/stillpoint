from __future__ import annotations

from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QDialog

from sp.app.ui.main_window import MainWindow


class _FakeInsertLinkDialogCreateAnchorWithCustomLabel:
    def __init__(self, *args, **kwargs):
        self.search = type("_Search", (), {"setFocus": lambda self: None})()

    def exec(self):
        return QDialog.Accepted

    def selected_colon_path(self):
        return ":Journal:2026:06:23#dk-questions"

    def selected_link_name(self):
        return "My label"

    def should_create_new_page(self):
        return True


class _FakeInsertLinkDialogCreateAnchorWithAutoLabel:
    def __init__(self, *args, **kwargs):
        self.search = type("_Search", (), {"setFocus": lambda self: None})()

    def exec(self):
        return QDialog.Accepted

    def selected_colon_path(self):
        return ":Journal:2026:06:23#dk-questions"

    def selected_link_name(self):
        return ":Journal:2026:06:23#dk-questions"

    def should_create_new_page(self):
        return True


def _build_main_window(qapp, monkeypatch):
    monkeypatch.setattr("sp.app.ui.main_window.config.has_active_vault", lambda: True)
    monkeypatch.setattr("sp.app.ui.main_window.config.load_vi_mode_enabled", lambda: False)
    monkeypatch.setattr("sp.app.ui.main_window.config.load_vi_cursor_style", lambda: "line")
    monkeypatch.setattr("sp.app.ui.markdown_editor.config.load_prefer_short_links", lambda: True)

    win = MainWindow("http://127.0.0.1:8765")
    win.editor.setPlainText("Dealing with")
    cursor = win.editor.textCursor()
    cursor.movePosition(QTextCursor.End)
    win.editor.setTextCursor(cursor)
    win.current_path = "/Projects/Projects.md"
    return win


def test_insert_link_create_new_with_anchor_and_custom_label_keeps_label(qapp, monkeypatch):
    monkeypatch.setattr(
        "sp.app.ui.main_window.InsertLinkDialog",
        _FakeInsertLinkDialogCreateAnchorWithCustomLabel,
    )

    win = _build_main_window(qapp, monkeypatch)

    monkeypatch.setattr(
        win,
        "_ensure_inline_link_target_page",
        lambda target, template_name="": (":Journal:2026:06:23", True),
    )

    win._insert_link()

    assert "Dealing with [:Journal:2026:06:23#dk-questions|My label]" in win.editor.to_markdown()
    win.close()


def test_insert_link_create_new_with_anchor_and_auto_label_omits_label(qapp, monkeypatch):
    monkeypatch.setattr(
        "sp.app.ui.main_window.InsertLinkDialog",
        _FakeInsertLinkDialogCreateAnchorWithAutoLabel,
    )

    win = _build_main_window(qapp, monkeypatch)

    monkeypatch.setattr(
        win,
        "_ensure_inline_link_target_page",
        lambda target, template_name="": (":Journal:2026:06:23", True),
    )

    win._insert_link()

    assert "Dealing with [:Journal:2026:06:23#dk-questions|:Journal:2026:06:23#dk-questions]" in win.editor.to_markdown()
    win.close()
