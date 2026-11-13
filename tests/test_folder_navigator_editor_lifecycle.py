from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, QObject

from sp.app.folder_navigator.core import read_text
from sp.app.folder_navigator.window import Tab, Window


def test_replaced_markdown_preview_stops_editor_before_deferred_delete(tmp_path, monkeypatch, qapp):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    paths = [tmp_path / "first.md", tmp_path / "second.md"]
    for path in paths:
        path.write_text("# Heading\n\n---\n\nBody\n")
    window = Window(tmp_path)
    try:
        window.open_file(paths[0])
        old = window.active_tab()
        old_editor = old.editor
        zoom_filters = list(old_editor._primary_wheel_zoom_filters)
        old_editor._heading_timer.start()
        old_editor._document_scan_timer.start()
        window.open_file(paths[1])
        # This must hold before Qt delivers DeferredDelete, not just afterwards.
        assert old._retired
        assert not old_editor._editor_alive
        assert not old_editor.updatesEnabled()
        assert not old_editor._heading_timer.isActive()
        assert not old_editor._document_scan_timer.isActive()
        assert old_editor.highlighter.document() is None
        assert old_editor._primary_wheel_zoom_filters == []
        assert all(handler._root is None and handler._callback is None for handler in zoom_filters)
        assert window.active_tab().editor._editor_alive
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    finally:
        window.close()


def test_accepted_window_close_quiesces_all_markdown_editors(tmp_path, monkeypatch, qapp):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    window = Window(tmp_path)
    for name in ("first.md", "second.md"):
        path = tmp_path / name
        path.write_text("# Heading\n\nBody\n")
        window.open_file(path, pinned=True)
    tabs = window.all_tabs()
    window.close()
    assert window.property("_sp_closing")
    assert all(tab._retired and not tab.editor._editor_alive for tab in tabs)
    assert all(tab.editor.highlighter.document() is None for tab in tabs)
    assert window.pending_markdown_preview is None


def test_native_deferred_delete_quiesces_markdown_tab(tmp_path, qapp):
    path = tmp_path / "notes.md"
    path.write_text("# Heading\n\nBody\n")
    tab = Tab(path, read_text(path), markdown=True, root=tmp_path)
    editor = tab.editor
    observed = []
    tab.destroyed.connect(lambda: observed.append(editor._teardown_done))
    # Bypass the Python deleteLater override, as native Qt callers can do.
    QObject.deleteLater(tab)
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert observed == [True]
