from pathlib import Path

from PySide6.QtTest import QTest

from sp.app.ui.main_window import MainWindow
from sp.app.ui.markdown_editor import MarkdownEditor


def _force_initial_paint(editor: MarkdownEditor, wait_ms: int = 20) -> None:
    editor.resize(400, 300)
    editor.show()
    for _ in range(5):
        QTest.qWait(wait_ms)
        editor.repaint()


def _drain_events(wait_ms: int = 25, rounds: int = 6) -> None:
    for _ in range(rounds):
        QTest.qWait(wait_ms)


def test_paint_guard_blocks_during_load_in_flight(monkeypatch, qapp) -> None:
    """Paint guard must be active on all platforms while _load_in_flight_token is set.

    Previously the in-flight check was Linux-only, which allowed paintEvent to
    call super().paintEvent() against a partially-cleared document on Windows,
    causing an intermittent access violation (faulthandler-detected crash).
    """
    editor = MarkdownEditor()
    _force_initial_paint(editor)
    try:
        # Simulate a load in progress by manually setting the in-flight token.
        editor._load_generation = 5  # type: ignore[attr-defined]
        editor._load_in_flight_token = 5  # type: ignore[attr-defined]

        assert editor._post_load_paint_guard_active() is True, (
            "_post_load_paint_guard_active() must return True on all platforms "
            "while a document load is in flight"
        )

        # After the load finishes the guard should clear.  Set _post_load_repaint_token
        # to match the load token so that the repaint-queuing branch on all platforms
        # considers the guard satisfied (it waits until a repaint has been scheduled
        # for the current token before releasing the guard).
        editor._load_in_flight_token = 0  # type: ignore[attr-defined]
        editor._post_load_paint_guard_until = 0.0  # type: ignore[attr-defined]
        editor._post_load_repaint_token = editor.current_load_token()  # type: ignore[attr-defined]
        assert editor._post_load_paint_guard_active() is False
    finally:
        editor.close()


def test_paint_guard_blocks_until_repaint_token_set_on_all_platforms(monkeypatch, qapp) -> None:
    """Paint guard must block until _deferred_post_load_repaint fires on all platforms.

    Previously the _post_load_repaint_token check was Linux-only.  On Windows,
    a spurious OS-level paint event (e.g. from a DPI change, window expose, or
    theme switch) could arrive right as the time-based guard expired, calling
    super().paintEvent() before the document layout had fully settled after a
    load.  The guard now blocks on all platforms until the explicitly-scheduled
    _deferred_post_load_repaint has fired and set _post_load_repaint_token.
    """
    editor = MarkdownEditor()
    _force_initial_paint(editor)
    try:
        editor._load_generation = 7  # type: ignore[attr-defined]
        editor._load_in_flight_token = 0  # type: ignore[attr-defined]
        # Simulate the time-based guard having just expired.
        editor._post_load_paint_guard_until = 0.0  # type: ignore[attr-defined]
        # _post_load_repaint_token NOT yet updated – deferred repaint hasn't fired.
        editor._post_load_repaint_token = 0  # type: ignore[attr-defined]

        assert editor._post_load_paint_guard_active() is True, (
            "_post_load_paint_guard_active() must return True on all platforms "
            "when the time guard has expired but _deferred_post_load_repaint "
            "has not yet fired (i.e. _post_load_repaint_token != load_token)"
        )

        # Once the deferred repaint fires and sets the token, the guard clears.
        editor._post_load_repaint_token = editor.current_load_token()  # type: ignore[attr-defined]
        assert editor._post_load_paint_guard_active() is False
    finally:
        editor.close()


def test_close_event_blocks_paint_before_destruction(qapp) -> None:
    """closeEvent must mark the editor as not alive before calling super().

    On Windows, QTextEdit::paintEvent can be dispatched by the OS during the
    Qt close/destroy sequence while the document and viewport are in a
    partially-freed state, causing an access violation.  After closeEvent has
    been called, paintEvent should immediately bail out without forwarding to
    QTextEdit::paintEvent.
    """
    editor = MarkdownEditor()
    _force_initial_paint(editor)
    editor.set_markdown("# Title\n\nSome content\n")
    _drain_events(wait_ms=30, rounds=8)

    # Close the editor – this should flip _editor_alive and _suppress_paint.
    editor.close()

    assert editor._editor_alive is False, (  # type: ignore[attr-defined]
        "closeEvent must set _editor_alive=False before calling super().closeEvent() "
        "to prevent paintEvent from forwarding to QTextEdit::paintEvent during "
        "widget destruction"
    )
    assert editor._suppress_paint is True, (  # type: ignore[attr-defined]
        "closeEvent must set _suppress_paint=True as an additional guard against "
        "paint events arriving during widget destruction"
    )


def test_editor_paint_suppressed_when_hidden_without_explicit_close(qapp) -> None:
    """hideEvent must set _suppress_paint=True to guard against late WM_PAINT.

    When a parent window closes, Qt destroys child widgets WITHOUT calling
    closeEvent on them.  On Windows, the OS can still dispatch WM_PAINT
    messages to the child during the parent's close/destroy sequence while
    Qt internal objects are in a partially-freed state – bypassing the
    closeEvent guards added in v1.1.9h and causing an access violation
    (faulthandler-detected fatal crash, v1.1.9i).

    Overriding hideEvent gives an earlier suppression point: Qt dispatches
    QHideEvent to every child widget when the parent is hidden, BEFORE the
    C++ destructor chain begins.  This test verifies that the flag is set
    correctly so that any late WM_PAINT arriving after hide is blocked.
    """
    editor = MarkdownEditor()
    _force_initial_paint(editor)
    editor.set_markdown("# Title\n\nSome content\n")
    _drain_events(wait_ms=30, rounds=8)

    # Sanity: editor is fully loaded and guard is not active.
    assert editor._suppress_paint is False, (  # type: ignore[attr-defined]
        "Precondition failed: _suppress_paint should be False after a settled load"
    )

    # Hide the editor WITHOUT calling close() – simulates the parent window
    # hiding/closing without explicitly closing child editors.
    editor.hide()

    assert editor._suppress_paint is True, (  # type: ignore[attr-defined]
        "hideEvent must set _suppress_paint=True to block late WM_PAINT messages "
        "that arrive during/after the parent-window close sequence on Windows "
        "(v1.1.9i crash: access violation inside super().paintEvent())"
    )

    # Re-showing the editor must lift the suppression so painting resumes.
    editor.show()
    _drain_events(wait_ms=20, rounds=4)

    assert editor._suppress_paint is False, (  # type: ignore[attr-defined]
        "showEvent must clear _suppress_paint so the editor can paint normally "
        "after being re-shown (e.g. window restored from tray or unminimised)"
    )

    editor.close()


def test_set_markdown_repeated_loads_clear_guard_and_remain_paintable(qapp) -> None:
    editor = MarkdownEditor()
    _force_initial_paint(editor)
    try:
        for idx in range(3):
            editor.set_markdown(f"# Page {idx}\n\nBody line {idx}\n")
            editor.repaint()
            _drain_events()

        _drain_events(wait_ms=30, rounds=8)

        assert editor.current_load_token() >= 3
        assert editor._load_in_flight_token == 0
        assert editor.toPlainText()
        assert editor._post_load_paint_guard_active() is False
    finally:
        editor.close()


def test_stale_image_retry_is_ignored_after_next_load(monkeypatch, qapp, tmp_path) -> None:
    vault_root = tmp_path / "vault"
    page_dir = vault_root / "Playpage"
    page_dir.mkdir(parents=True, exist_ok=True)
    (page_dir / "a.png").write_bytes(
        bytes.fromhex(
            "89504E470D0A1A0A0000000D49484452000000010000000108060000001F15C489"
            "0000000D49444154789C63F8CFC0F01F00050001FF89993D1D0000000049454E44AE426082"
        )
    )
    (page_dir / "b.png").write_bytes(
        bytes.fromhex(
            "89504E470D0A1A0A0000000D49484452000000010000000108060000001F15C489"
            "0000000D49444154789C636060F8CF000002020100C8B5579B0000000049454E44AE426082"
        )
    )

    editor = MarkdownEditor()
    editor.set_context(str(vault_root), "/Playpage/Playpage.md")
    _force_initial_paint(editor)
    try:
        retry_calls: list[tuple[str, int | None]] = []

        def tracking_render_images(display_text: str, scheduled_at=None, load_token=None):
            retry_calls.append((display_text, load_token))

        monkeypatch.setattr(editor, "_render_images", tracking_render_images)

        editor.set_markdown("![Old](a.png)\n")
        stale_token = editor.current_load_token()
        editor.set_markdown("![New](b.png)\n")
        current_token = editor.current_load_token()
        retry_calls.clear()

        editor._retry_render_images("![Old](a.png)\n", None, stale_token)
        editor._retry_render_images("![New](b.png)\n", None, current_token)

        assert retry_calls == [("![New](b.png)\n", current_token)]
    finally:
        editor.close()


def test_scroll_to_position_with_flash_ignores_stale_path_or_token(qtbot, monkeypatch) -> None:
    window = MainWindow(api_base="http://localhost:5050")
    qtbot.addWidget(window)
    window.current_path = "/Current/Page.md"
    window.editor.setPlainText("hello\nworld\n")

    flashes: list[int] = []
    monkeypatch.setattr(window.editor, "current_load_token", lambda: 7)
    monkeypatch.setattr(window, "_animate_or_flash_to_cursor", lambda cursor: flashes.append(cursor.position()))

    window._scroll_to_position_with_flash(3, expected_path="/Other/Page.md", expected_load_token=7)
    window._scroll_to_position_with_flash(3, expected_path="/Current/Page.md", expected_load_token=6)
    window._scroll_to_position_with_flash(3, expected_path="/Current/Page.md", expected_load_token=7)

    assert flashes == [3]


def test_search_navigation_drops_stale_delayed_scrolls(qtbot, monkeypatch) -> None:
    window = MainWindow(api_base="http://localhost:5050")
    qtbot.addWidget(window)

    flashes: list[str] = []

    def fake_open_file(path: str, *args, **kwargs) -> None:
        window.current_path = path
        window.editor.set_markdown(f"# {Path(path).stem}\n\nBody\n")

    monkeypatch.setattr(window, "_open_file", fake_open_file)
    monkeypatch.setattr(window, "_animate_or_flash_to_cursor", lambda cursor: flashes.append(window.current_path or ""))

    window._on_search_result_selected_with_editor_focus("/PageA/PageA.md", 1, 2)
    window._on_search_result_selected_with_editor_focus("/PageB/PageB.md", 1, 2)
    _drain_events(wait_ms=40, rounds=6)

    assert window.current_path == "/PageB/PageB.md"
    assert flashes == ["/PageB/PageB.md"]
