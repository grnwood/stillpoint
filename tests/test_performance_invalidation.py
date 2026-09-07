from __future__ import annotations

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent, QTextCursor
from PySide6.QtWidgets import QLineEdit, QVBoxLayout, QWidget

from sp.app import config, indexer
from sp.app.ui.main_window import MainWindow
from sp.app.ui.markdown_editor import MarkdownEditor


def test_changed_page_metadata_ignores_ordinary_prose() -> None:
    before = "# Project\n\nSome ordinary prose.\n"
    after = "# Project\n\nSome edited ordinary prose.\n"

    assert indexer.changed_page_metadata("/Project/Project.md", before, after) == set()


def test_changed_page_metadata_classifies_panel_inputs() -> None:
    before = "# Old title\n\n#old\n\n- [ ] Ship it @work\n\n[Page|]\n"
    after = "# New title\n\n#new\n\n- [x] Ship it @work\n\n[Other|]\n"

    assert indexer.changed_page_metadata("/Project/Project.md", before, after) == {
        "tags",
        "links",
        "tasks",
        "title",
    }


def test_line_count_change_does_not_force_full_rehighlight(qtbot, monkeypatch) -> None:
    editor = MarkdownEditor()
    qtbot.addWidget(editor)
    calls: list[bool] = []
    monkeypatch.setattr(editor.highlighter, "rehighlight", lambda: calls.append(True))

    editor.setPlainText("one\ntwo")
    editor._check_block_count_change()

    assert calls == []


def test_paint_event_does_not_rebuild_vi_cursor(qtbot, monkeypatch) -> None:
    editor = MarkdownEditor()
    qtbot.addWidget(editor)
    editor._vi_mode_active = True
    calls: list[bool] = []
    monkeypatch.setattr(editor, "_update_vi_cursor", lambda: calls.append(True))

    editor.eventFilter(editor.viewport(), QEvent(QEvent.Paint))
    editor.eventFilter(editor.viewport(), QEvent(QEvent.UpdateRequest))

    assert calls == []


def test_keystroke_to_paint_span_is_emitted_after_dispatch(qtbot, monkeypatch, qapp) -> None:
    editor = MarkdownEditor()
    qtbot.addWidget(editor)
    records: list[tuple[str, dict]] = []
    monkeypatch.setattr("sp.app.ui.markdown_editor.PERFORMANCE_LOGGING_ENABLED", True)
    monkeypatch.setattr(
        "sp.app.ui.markdown_editor.emit_performance_span",
        lambda name, _started_at, **kwargs: records.append((name, kwargs)),
    )

    editor.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_A, Qt.NoModifier, "a"))
    editor.eventFilter(editor.viewport(), QEvent(QEvent.Paint))
    qapp.processEvents()

    assert records
    assert records[-1][0] == "editor.keystroke_to_paint_start"
    assert records[-1][1]["fields"]["revision_at_paint"] >= records[-1][1]["fields"]["revision_before"]


def test_focus_stylesheet_cache_skips_identical_repolish(qtbot) -> None:
    widget = QWidget()
    qtbot.addWidget(widget)
    host = type("Host", (), {"_focus_stylesheet_cache": {}})()
    calls: list[str] = []
    original = widget.setStyleSheet

    def record(stylesheet: str) -> None:
        calls.append(stylesheet)
        original(stylesheet)

    widget.setStyleSheet = record  # type: ignore[method-assign]

    assert MainWindow._set_cached_focus_stylesheet(host, "pane", widget, "color: red") is True
    assert MainWindow._set_cached_focus_stylesheet(host, "pane", widget, "color: red") is False
    assert MainWindow._set_cached_focus_stylesheet(host, "pane", widget, "color: blue") is True
    assert calls == ["color: red", "color: blue"]


def test_ordinary_prose_save_does_not_refresh_metadata_panels(main_window, monkeypatch) -> None:
    path = "/PageA/PageA.md"
    main_window.current_path = path
    main_window._last_saved_content = "# PageA\n\nOld prose.\n"
    refreshes: list[str] = []

    monkeypatch.setattr(config, "has_active_vault", lambda: True)
    monkeypatch.setattr(indexer, "index_page", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(main_window.right_panel, "refresh_tasks", lambda: refreshes.append("tasks"))
    monkeypatch.setattr(main_window.right_panel, "refresh_links", lambda _path: refreshes.append("links"))
    monkeypatch.setattr(main_window, "_refresh_detached_task_panels", lambda: refreshes.append("detached tasks"))
    monkeypatch.setattr(main_window, "_refresh_detached_calendar_panels", lambda: refreshes.append("calendars"))
    monkeypatch.setattr(main_window, "_refresh_detached_link_panels", lambda _path: refreshes.append("detached links"))
    monkeypatch.setattr(main_window, "_mark_homebase_unsynced_local_change", lambda: None)
    monkeypatch.setattr(main_window, "_mark_recent_self_saved_path", lambda _path: None)
    monkeypatch.setattr(main_window, "_persist_recent_history", lambda: None)
    monkeypatch.setattr(main_window, "_schedule_homebase_sync", lambda _reason: None)

    main_window._finalize_save(
        path,
        "# PageA\n\nNew prose.\n",
        {},
        "Saved",
    )

    assert refreshes == []


def test_body_edit_after_last_heading_skips_outline_scan(qtbot) -> None:
    editor = MarkdownEditor()
    qtbot.addWidget(editor)
    editor.setPlainText("# Heading\nBody")
    editor._emit_heading_outline()
    editor._heading_timer.stop()
    editor._document_scan_timer.stop()

    cursor = QTextCursor(editor.document().findBlockByNumber(1))
    cursor.movePosition(QTextCursor.EndOfBlock)
    cursor.insertText(" text")

    assert editor._heading_timer.isActive() is False
    assert editor._document_scan_timer.isActive() is True
    assert editor._pending_edit_heading_refresh is False


def test_body_edit_before_later_heading_invalidates_outline_positions(qtbot) -> None:
    editor = MarkdownEditor()
    qtbot.addWidget(editor)
    editor.setPlainText("# Heading\nBody\n## Later")
    editor._emit_heading_outline()
    editor._heading_timer.stop()
    editor._document_scan_timer.stop()

    cursor = QTextCursor(editor.document().findBlockByNumber(1))
    cursor.movePosition(QTextCursor.EndOfBlock)
    cursor.insertText(" text")

    assert editor._document_scan_timer.isActive() is True
    assert editor._pending_edit_heading_refresh is True


def test_horizontal_rule_edit_scans_nearby_blocks_and_preserves_other_rules(
    qtbot,
    monkeypatch,
) -> None:
    editor = MarkdownEditor()
    qtbot.addWidget(editor)
    editor.setPlainText("---\none\ntwo\nthree\n---")
    editor._refresh_hr_selections()
    editor._hr_timer.stop()
    records: list[dict] = []
    monkeypatch.setattr("sp.app.ui.markdown_editor.performance_start", lambda: 1.0)
    monkeypatch.setattr(
        "sp.app.ui.markdown_editor.emit_performance_span",
        lambda _name, _started_at, **kwargs: records.append(kwargs["fields"]),
    )

    cursor = QTextCursor(editor.document().findBlockByNumber(2))
    cursor.movePosition(QTextCursor.EndOfBlock)
    cursor.insertText(" changed")
    editor._hr_timer.stop()
    editor._refresh_pending_hr_selections()

    rule_selections = [
        selection
        for selection in editor.extraSelections()
        if selection.format.property(editor._HR_EXTRA_KEY) is True
    ]
    assert len(rule_selections) == 2
    assert records[-1]["incremental"] is True
    assert records[-1]["scanned_blocks"] <= 3


def test_pending_top_nav_refresh_is_applied_only_to_current_page() -> None:
    calls: list[str] = []
    host = type(
        "Host",
        (),
        {
            "current_path": "/Current/Current.md",
            "_pending_top_nav_refresh": ("/Old/Old.md", True),
            "_refresh_history_buttons": lambda self: calls.append("history"),
            "_update_active_page_chicklets": lambda self: calls.append("active"),
        },
    )()

    MainWindow._flush_pending_top_nav_refresh(host, "/Old/Old.md")
    assert calls == []

    host._pending_top_nav_refresh = (host.current_path, False)
    MainWindow._flush_pending_top_nav_refresh(host, host.current_path)
    assert calls == ["active"]
    assert host._pending_top_nav_refresh is None


def test_set_markdown_preserves_focus_owned_by_another_pane(qtbot, qapp) -> None:
    container = QWidget()
    layout = QVBoxLayout(container)
    editor = MarkdownEditor()
    other_pane = QLineEdit()
    layout.addWidget(editor)
    layout.addWidget(other_pane)
    qtbot.addWidget(container)
    container.show()
    other_pane.setFocus(Qt.OtherFocusReason)
    qapp.processEvents()
    assert other_pane.hasFocus()

    editor.set_markdown("# Loaded without stealing focus\n")
    qapp.processEvents()

    assert other_pane.hasFocus()
