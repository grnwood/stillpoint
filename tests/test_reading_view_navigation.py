from pathlib import Path

from PySide6.QtTest import QTest


def test_stillpoint_tree_click_updates_visible_reading_page(main_window, qapp):
    main_window._open_file("/PageA/PageA.md")
    main_window._set_reading_view_enabled(True)
    QTest.qWait(main_window.editor._post_load_paint_guard_ms + 100)
    assert main_window.editor.isHidden()
    assert main_window.editor.is_ready_for_page_switch()
    target = main_window._find_item(main_window.tree_model.invisibleRootItem(), "/PageB/PageB.md")
    assert target is not None
    main_window._on_tree_row_clicked(target.index())
    QTest.qWait(250)
    assert main_window.current_path == "/PageB/PageB.md"
    assert main_window._reading_view_enabled
    assert main_window._editor_view_stack.currentWidget() is main_window.reading_view
    assert main_window.reading_view.last_markdown == main_window.editor.to_markdown()
    assert main_window._reading_saved_path == "/PageB/PageB.md"
    assert main_window._pending_tree_open_path is None
    main_window._set_reading_view_enabled(False)
    assert main_window.current_path == "/PageB/PageB.md"


def test_hidden_editor_still_blocks_page_switch_during_real_mutations(qapp):
    from sp.app.ui.markdown_editor import MarkdownEditor
    editor = MarkdownEditor()
    try:
        editor.hide()
        editor._suppress_paint = True
        assert editor.is_ready_for_page_switch()
        editor._push_paint_block()
        assert not editor.is_ready_for_page_switch()
        editor._pop_paint_block()
        editor._load_in_flight_token = editor.current_load_token() + 1
        assert not editor.is_ready_for_page_switch()
    finally:
        editor.close()


def test_folder_navigator_follows_files_in_reading_view_and_remembers_choice(tmp_path, monkeypatch, qapp):
    from sp.app.folder_navigator.window import Window
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    first = tmp_path / "first.md"
    second = tmp_path / "second.md"
    plain = tmp_path / "plain.txt"
    first.write_text("# First\n\nFirst page content.\n")
    second.write_text("# Second\n\nSecond page content.\n")
    plain.write_text("Plain text")
    window = Window(tmp_path)
    monkeypatch.setattr(window, "_review_dirty", lambda tabs: True)
    window.show()
    window.activateWindow()
    qapp.processEvents()
    try:
        window.open_file(first)
        window.active_tab().set_reading_view(True)
        window.tree.setFocus()
        qapp.processEvents()
        assert window.tree.hasFocus()
        window.open_file(second)
        tab = window.active_tab()
        assert tab.path == second
        assert tab.reading_view_enabled
        assert "Second page content" in tab.reading_view.last_markdown
        assert "First page content" not in tab.reading_view.last_markdown
        assert tab.editor_view_stack.currentWidget() is tab.reading_view
        assert window.tree.hasFocus()
        window.open_file(plain)
        window.open_file(first)
        assert window.active_tab().reading_view_enabled
        window.active_tab().set_reading_view(False)
        window.open_file(second)
        assert not window.active_tab().reading_view_enabled
    finally:
        window.close()


def test_hidden_editor_does_not_spin_scroll_margin_retries(qapp, monkeypatch):
    from sp.app.ui.markdown_editor import MarkdownEditor
    editor = MarkdownEditor()
    editor.resize(500, 400)
    editor.show()
    QTest.qWait(30)
    editor.hide()
    retries = []
    original = editor._retry_scroll_margin

    def retry(token=None):
        retries.append(token)
        original(token)

    monkeypatch.setattr(editor, "_retry_scroll_margin", retry)
    try:
        editor._apply_scroll_past_end_margin()
        editor._refresh_hr_selections()
        QTest.qWait(160)
        assert len(retries) <= 1
        assert not editor._scroll_margin_retry_pending
        assert not editor._hr_retry_timer.isActive()
        assert editor.is_ready_for_page_switch()
        editor.show()
        QTest.qWait(100)
        assert editor.document().rootFrame().frameFormat().bottomMargin() == int(editor.viewport().height() * 0.4)
    finally:
        editor.close()


def test_reading_tree_clicks_keep_switching_without_retry_spin(main_window, qapp, monkeypatch):
    from PySide6.QtCore import Qt
    retries = []
    original = main_window.editor._retry_scroll_margin

    def retry(token=None):
        retries.append(token)
        original(token)

    monkeypatch.setattr(main_window.editor, "_retry_scroll_margin", retry)
    main_window._open_file("/PageA/PageA.md")
    main_window._set_reading_view_enabled(True)
    QTest.qWait(200)
    for path in ("/PageB/PageB.md", "/PageA/PageA.md", "/PageB/PageB.md"):
        item = main_window._find_item(main_window.tree_model.invisibleRootItem(), path)
        assert item is not None
        main_window.tree_view.scrollTo(item.index())
        qapp.processEvents()
        QTest.mouseClick(main_window.tree_view.viewport(), Qt.LeftButton,
                        pos=main_window.tree_view.visualRect(item.index()).center())
        QTest.qWait(220)
        assert main_window.current_path == path
        assert main_window._reading_view_enabled
        assert main_window.reading_view.last_markdown == main_window.editor.to_markdown()
    assert len(retries) < 15


def test_theme_toggle_during_navigation_has_bounded_reader_refreshes(main_window, qapp, monkeypatch):
    monkeypatch.setattr("sp.app.ui.markdown_preview.config.save_reading_view_use_theme_colors", lambda enabled: None)
    main_window._open_file("/PageA/PageA.md")
    main_window._set_reading_view_enabled(True)
    QTest.qWait(200)
    refreshes = []
    original = main_window.reading_view.set_markdown

    def refresh(*args, **kwargs):
        refreshes.append(main_window.current_path)
        original(*args, **kwargs)

    monkeypatch.setattr(main_window.reading_view, "set_markdown", refresh)
    for index in range(8):
        main_window.reading_view.theme_toggle.click()
        path = "/PageB/PageB.md" if index % 2 == 0 else "/PageA/PageA.md"
        item = main_window._find_item(main_window.tree_model.invisibleRootItem(), path)
        main_window._on_tree_row_clicked(item.index())
        QTest.qWait(200)
        assert main_window.current_path == path
        assert main_window.reading_view.last_markdown == main_window.editor.to_markdown()
    count = len(refreshes)
    QTest.qWait(200)
    assert len(refreshes) == count
    assert len(refreshes) <= 16


def test_reader_refresh_burst_renders_only_latest_page_once(main_window, qapp, monkeypatch):
    main_window._open_file("/PageA/PageA.md")
    main_window._set_reading_view_enabled(True)
    QTest.qWait(200)
    renders = []
    original = main_window.reading_view.set_markdown

    def render(source, **kwargs):
        renders.append(source)
        original(source, **kwargs)

    monkeypatch.setattr(main_window.reading_view, "set_markdown", render)
    main_window.editor.set_markdown("# Latest\n\nUpdated page.\n")
    for _ in range(50):
        main_window._schedule_reading_view_refresh()
    main_window._refresh_reading_view()
    QTest.qWait(220)
    assert len(renders) == 1
    assert "Updated page" in renders[0]
    assert not main_window._reading_view_refresh_timer.isActive()
    main_window.editor.document().setModified(False)


def test_reader_tool_layout_cannot_reenter_itself(qapp, monkeypatch):
    from sp.app.ui.markdown_preview import MarkdownReadingView
    view = MarkdownReadingView()
    calls = []

    def layout():
        calls.append(True)
        view._position_reader_tools()

    monkeypatch.setattr(view, "_layout_reader_tools", layout)
    try:
        view._position_reader_tools()
        assert calls == [True]
    finally:
        view.close()


def test_reader_appearance_reentry_defers_and_new_page_supersedes_it(qapp, monkeypatch):
    from sp.app.ui.markdown_preview import MarkdownReadingView
    from sp.app import config
    view = MarkdownReadingView()
    original = view._load_reader_document
    calls = []

    def load():
        calls.append(view.last_markdown)
        if len(calls) == 1:
            view._apply_reading_options(config.normalize_reading_options({
                "width": "narrow", "size": 150, "spacing": "relaxed", "stripe_tables": False,
            }))
            view.set_markdown("# Queued old page")
        original()

    monkeypatch.setattr(view, "_load_reader_document", load)
    try:
        view.set_markdown("# First page")
        assert calls == ["# First page"]
        assert view._render_request_timer.isActive()
        view.set_markdown("# Latest page")
        QTest.qWait(80)
        assert calls == ["# First page", "# Latest page"]
        assert view.last_markdown == "# Latest page"
        assert not view._render_request_timer.isActive()
        assert not view._rendering_markdown
    finally:
        view.close()


def test_reading_options_focus_and_zoom_keep_navigation_responsive(main_window, qapp, monkeypatch):
    from sp.app import config
    monkeypatch.setattr(config, "save_reading_options", lambda options: None)
    main_window._open_file("/PageA/PageA.md")
    main_window._set_reading_view_enabled(True)
    QTest.qWait(180)
    for index, width in enumerate(("narrow", "comfortable", "wide", "full")):
        reader = main_window.reading_view
        reader.set_reading_options({
            "width": width, "size": (80, 140, 110, 100)[index],
            "spacing": ("compact", "relaxed", "normal", "normal")[index],
            "stripe_tables": bool(index % 2),
        })
        reader.set_focus_mode(True)
        reader.set_focus_mode(False)
        main_window._adjust_reading_view_zoom(1 if index % 2 == 0 else -1)
        path = "/PageB/PageB.md" if index % 2 == 0 else "/PageA/PageA.md"
        item = main_window._find_item(main_window.tree_model.invisibleRootItem(), path)
        main_window._on_tree_row_clicked(item.index())
        QTest.qWait(220)
        assert main_window.current_path == path
        assert reader.last_markdown == main_window.editor.to_markdown()
        assert not reader._rendering_markdown
        assert not reader._render_request_timer.isActive()
    main_window._set_reading_view_enabled(False)
    QTest.qWait(160)
    assert main_window.editor.is_ready_for_page_switch()
