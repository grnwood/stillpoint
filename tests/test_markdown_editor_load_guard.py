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
