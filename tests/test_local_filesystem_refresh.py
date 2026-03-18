from __future__ import annotations

import queue
from pathlib import Path
import threading

from sp.app.ui.main_window import MainWindow
from sp.app import config


class _DummyStatusBar:
    def __init__(self) -> None:
        self.messages: list[tuple[str, int]] = []

    def showMessage(self, text: str, timeout: int = 0) -> None:
        self.messages.append((text, timeout))


class _DummyTimer:
    def __init__(self) -> None:
        self.started = 0
        self.stopped = 0

    def start(self) -> None:
        self.started += 1

    def stop(self) -> None:
        self.stopped += 1


class _ImmediateThread:
    def __init__(self, *, target, daemon: bool = False) -> None:
        self._target = target
        self.daemon = daemon

    def start(self) -> None:
        self._target()


class _ReconcileDummy:
    def __init__(self, vault_root: Path, snapshot: dict[str, tuple[int, int]]) -> None:
        self._remote_mode = False
        self.vault_root = str(vault_root)
        self._local_fs_page_snapshot = dict(snapshot)
        self.current_path = None
        self.calls: list[tuple[list[str], list[str]]] = []

    def _snapshot_local_page_state(self, root: Path) -> dict[str, tuple[int, int]]:
        return MainWindow._snapshot_local_page_state(self, root)

    def _apply_incremental_page_index_changes(
        self,
        changed_paths: list[str],
        removed_paths: list[str],
    ) -> dict[str, object]:
        self.calls.append((list(changed_paths), list(removed_paths)))
        return {
            "indexed_paths": list(changed_paths),
            "removed_paths": list(removed_paths),
            "current_page_changed": False,
            "current_page_removed": False,
        }


def test_reconcile_local_filesystem_index_detects_added_page(tmp_path) -> None:
    vault_root = tmp_path / "vault"
    page_a = vault_root / "PageA" / "PageA.md"
    page_a.parent.mkdir(parents=True, exist_ok=True)
    page_a.write_text("# A\n", encoding="utf-8")

    initial_snapshot = MainWindow._snapshot_local_page_state(object(), vault_root)
    dummy = _ReconcileDummy(vault_root, initial_snapshot)

    page_b = vault_root / "PageB" / "PageB.md"
    page_b.parent.mkdir(parents=True, exist_ok=True)
    page_b.write_text("# B\n", encoding="utf-8")

    result = MainWindow._reconcile_local_filesystem_index(dummy)

    assert dummy.calls == [(["/PageB/PageB.md"], [])]
    assert result["structure_changed"] is True
    assert "/PageB/PageB.md" in dummy._local_fs_page_snapshot


def test_reconcile_local_filesystem_index_detects_removed_page(tmp_path) -> None:
    vault_root = tmp_path / "vault"
    page_a = vault_root / "PageA" / "PageA.md"
    page_b = vault_root / "PageB" / "PageB.md"
    for path in (page_a, page_b):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {path.stem}\n", encoding="utf-8")

    initial_snapshot = MainWindow._snapshot_local_page_state(object(), vault_root)
    dummy = _ReconcileDummy(vault_root, initial_snapshot)

    page_b.unlink()

    result = MainWindow._reconcile_local_filesystem_index(dummy)

    assert dummy.calls == [([], ["/PageB/PageB.md"])]
    assert result["structure_changed"] is True
    assert "/PageB/PageB.md" not in dummy._local_fs_page_snapshot


def test_local_fs_quiet_timeout_refreshes_tree_only_for_structure_changes(monkeypatch) -> None:
    class _Dummy:
        _remote_mode = False
        vault_root = "/vault"
        _homebase_tree_refresh_reason = "filesystem change"
        current_path = None
        _local_fs_refresh_generation = 0
        _recent_self_saved_paths = {}

        def __init__(self) -> None:
            self.status = _DummyStatusBar()
            self.refreshed = 0
            self.context_calls = 0
            self._local_fs_refresh_result_queue = queue.Queue()
            self._local_fs_refresh_result_timer = _DummyTimer()
            self._local_fs_page_snapshot = {}
            self._homebase_tree_refresh_pending = False
            self.right_panel = type(
                "_RightPanel",
                (),
                {"refresh_tasks": lambda self: None, "refresh_links": lambda self, _path: None},
            )()

        def _compute_local_fs_refresh_payload(self, *, current_path, recent_self_saved_paths):
            return {
                "indexed_paths": ["/PageB/PageB.md"],
                "removed_paths": [],
                "structure_changed": True,
                "current_page_changed": False,
                "current_page_removed": False,
                "snapshot": {},
            }

        def _prune_recent_self_saved_paths(self) -> None:
            return None

        def _ensure_config_active_vault_context(self) -> None:
            self.context_calls += 1

        def _schedule_homebase_tree_refresh_on_ui_activity(self, reason: str) -> None:
            self._homebase_tree_refresh_pending = True

        def _is_editor_idle_for_remote_reload(self) -> bool:
            return False

        def _refresh_detached_task_panels(self) -> None:
            return None

        def _refresh_detached_calendar_panels(self) -> None:
            return None

        def _refresh_detached_link_panels(self, path) -> None:
            return None

        def statusBar(self) -> _DummyStatusBar:
            return self.status

    bumped: list[str] = []
    monkeypatch.setattr(config, "bump_tree_version", lambda: bumped.append("tree"))
    monkeypatch.setattr(threading, "Thread", _ImmediateThread)

    dummy = _Dummy()
    MainWindow._on_local_fs_ui_quiet_timeout(dummy)
    MainWindow._drain_local_fs_refresh_results(dummy)

    assert bumped == ["tree"]
    assert dummy._homebase_tree_refresh_pending is True
    assert dummy.context_calls == 1


def test_local_fs_quiet_timeout_reloads_current_page_after_incremental_index(monkeypatch) -> None:
    class _Dummy:
        _remote_mode = False
        vault_root = "/vault"
        _homebase_tree_refresh_reason = "filesystem change"
        current_path = "/PageA/PageA.md"
        _local_fs_refresh_generation = 0
        _recent_self_saved_paths = {}

        def __init__(self) -> None:
            self.status = _DummyStatusBar()
            self.refreshed = 0
            self.open_calls = []
            self._local_fs_refresh_result_queue = queue.Queue()
            self._local_fs_refresh_result_timer = _DummyTimer()
            self._local_fs_page_snapshot = {}
            self.right_panel = type(
                "_RightPanel",
                (),
                {"refresh_tasks": lambda self: None, "refresh_links": lambda self, _path: None},
            )()

        def _compute_local_fs_refresh_payload(self, *, current_path, recent_self_saved_paths):
            return {
                "indexed_paths": ["/PageA/PageA.md"],
                "removed_paths": [],
                "structure_changed": False,
                "current_page_changed": True,
                "current_page_removed": False,
                "snapshot": {},
            }

        def _prune_recent_self_saved_paths(self) -> None:
            return None

        def _ensure_config_active_vault_context(self) -> None:
            return None

        def _refresh_detached_task_panels(self) -> None:
            return None

        def _refresh_detached_calendar_panels(self) -> None:
            return None

        def _refresh_detached_link_panels(self, path) -> None:
            return None

        def _is_editor_idle_for_remote_reload(self) -> bool:
            return True

        def _open_file(self, *args, **kwargs) -> None:
            self.open_calls.append((args, kwargs))

        def statusBar(self) -> _DummyStatusBar:
            return self.status

    monkeypatch.setattr(config, "bump_tree_version", lambda: None)
    monkeypatch.setattr(threading, "Thread", _ImmediateThread)

    dummy = _Dummy()
    MainWindow._on_local_fs_ui_quiet_timeout(dummy)
    MainWindow._drain_local_fs_refresh_results(dummy)

    assert dummy.refreshed == 0
    assert len(dummy.open_calls) == 1
    args, kwargs = dummy.open_calls[0]
    assert args[0] == "/PageA/PageA.md"
    assert kwargs["add_to_history"] is False
    assert kwargs["force"] is True
