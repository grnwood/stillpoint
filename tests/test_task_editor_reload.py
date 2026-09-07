from __future__ import annotations

from types import SimpleNamespace

from sp.app.ui.main_window import MainWindow


class _Timer:
    def __init__(self) -> None:
        self.starts = 0

    def start(self) -> None:
        self.starts += 1


def test_task_page_reloads_are_coalesced_until_timer_fires() -> None:
    timer = _Timer()
    dummy = SimpleNamespace(
        current_path="/Page/Page.md",
        _pending_task_editor_reload_paths=set(),
        _task_editor_reload_timer=timer,
        _page_windows=[],
        _normalize_task_date_paths=lambda paths: set(paths),
    )

    MainWindow._on_task_dates_applied(dummy, ["/Page/Page.md"])
    MainWindow._on_task_dates_applied(dummy, ["/Page/Page.md", "/Other/Other.md"])

    assert timer.starts == 2
    assert dummy._pending_task_editor_reload_paths == {
        "/Page/Page.md",
        "/Other/Other.md",
    }

