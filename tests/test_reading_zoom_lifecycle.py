from PySide6.QtTest import QTest

from sp.app.ui.markdown_editor import MarkdownEditor


def test_font_zoom_blocks_expose_paint_until_layout_settles(qapp, monkeypatch):
    editor = MarkdownEditor()
    editor.set_markdown("# Heading\n\n---\n\nBody\n")
    token = editor.current_load_token()
    observed = []
    original = editor.highlighter.set_base_font_size

    def rehighlight(size):
        observed.append((editor._suppress_paint, editor._cursor_events_blocked))
        original(size)

    monkeypatch.setattr(editor.highlighter, "set_base_font_size", rehighlight)
    try:
        editor.set_font_point_size(18)
        assert observed == [(True, True)]
        assert editor.current_load_token() == token
        assert editor._post_load_paint_guard_active()
        QTest.qWait(editor._post_load_paint_guard_ms + 80)
        assert not editor._post_load_paint_guard_active()
        assert editor.font().pointSize() == 18
    finally:
        editor.close()


def test_zoom_keeps_pending_image_hydration_and_nested_paint_block(qapp):
    editor = MarkdownEditor()
    editor.set_markdown("# Heading\n\n![Image](missing.png)\n")
    pending_images = editor._pending_image_hydration
    token = editor.current_load_token()
    editor._push_paint_block()
    try:
        editor.set_font_point_size(17)
        assert editor._suppress_paint
        assert editor._suppress_paint_depth == 1
        assert editor._pending_image_hydration == pending_images
        assert editor.current_load_token() == token
    finally:
        editor._pop_paint_block()
        editor.close()


def test_reading_zoom_and_immediate_edit_handoff_keeps_content(main_window, qapp, monkeypatch):
    monkeypatch.setattr("sp.app.ui.main_window.config.save_global_editor_font_size", lambda size: None)
    main_window._open_file("/PageA/PageA.md")
    source = "# Heading\n\n---\n\nParagraph **bold**.\n\n## Section\n\n- [ ] Task\n\n"
    main_window.editor.set_markdown(source * 12)
    main_window.editor.document().setModified(False)
    original = main_window.editor.to_markdown()
    initial_size = main_window.font_size
    for iteration in range(12):
        main_window._set_reading_view_enabled(True)
        main_window._adjust_reading_view_zoom(1 if iteration % 2 == 0 else -1)
        # Switch before the 60ms zoom debounce timer fires: this flushes zoom
        # against a hidden editor, then immediately exposes its native layout.
        main_window._set_reading_view_enabled(False)
        assert main_window.editor._post_load_paint_guard_active()
        main_window.editor.viewport().repaint()
        qapp.processEvents()
        QTest.qWait(main_window.editor._post_load_paint_guard_ms + 30)
        main_window.editor.viewport().repaint()
    assert main_window.font_size == initial_size
    assert main_window.editor.to_markdown() == original
    assert main_window.reading_view._editor_zoom_delta == 0
    assert main_window.editor.hasFocus()
    main_window.editor.document().setModified(False)
