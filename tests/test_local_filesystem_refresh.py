from __future__ import annotations

import queue
from pathlib import Path
import threading

from PySide6.QtCore import QEvent

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


def test_pending_tree_refresh_is_triggered_only_by_navigation_activity() -> None:
    viewport = object()

    class _Tree:
        def viewport(self):
            return viewport

    class _Dummy:
        tree_view = _Tree()

    dummy = _Dummy()
    editor = object()

    # MouseButtonRelease (not Press) is the trigger to avoid mid-click tree rebuilds
    assert MainWindow._is_tree_navigation_activity(dummy, viewport, QEvent.MouseButtonRelease)
    assert MainWindow._is_tree_navigation_activity(dummy, dummy.tree_view, QEvent.KeyPress)
    assert MainWindow._is_tree_navigation_activity(dummy, dummy.tree_view, QEvent.FocusIn)
    # MouseButtonPress no longer triggers a flush (prevents indexAt() race between press and release)
    assert not MainWindow._is_tree_navigation_activity(dummy, viewport, QEvent.MouseButtonPress)
    assert not MainWindow._is_tree_navigation_activity(dummy, editor, QEvent.KeyPress)
    assert not MainWindow._is_tree_navigation_activity(dummy, editor, QEvent.FocusIn)
    assert not MainWindow._is_tree_navigation_activity(dummy, viewport, QEvent.Paint)


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


def test_local_fs_quiet_timeout_stops_result_timer_after_worker_error(monkeypatch) -> None:
    class _Dummy:
        _remote_mode = False
        vault_root = "/vault"
        _homebase_tree_refresh_reason = "filesystem change"
        current_path = None
        _local_fs_refresh_generation = 0
        _recent_self_saved_paths = {}
        _local_fs_page_snapshot = {"/PageA/PageA.md": (1, 1)}

        def __init__(self) -> None:
            self._local_fs_refresh_result_queue = queue.Queue()
            self._local_fs_refresh_result_timer = _DummyTimer()

        def _compute_local_fs_refresh_payload(self, *, current_path, recent_self_saved_paths):
            raise RuntimeError("scan failed")

        def _backoff_local_fs_refresh_poll(self) -> None:
            raise AssertionError("no backoff needed once worker error is queued")

    monkeypatch.setattr(threading, "Thread", _ImmediateThread)

    dummy = _Dummy()
    MainWindow._on_local_fs_ui_quiet_timeout(dummy)
    MainWindow._drain_local_fs_refresh_results(dummy)

    assert dummy._local_fs_refresh_result_timer.started == 1
    assert dummy._local_fs_refresh_result_timer.stopped == 1
    assert dummy._local_fs_refresh_started_at is None


def test_local_fs_scan_schedules_homebase_sync_for_external_changes(monkeypatch) -> None:
    class _DummyEngine:
        pass

    class _Dummy:
        _remote_mode = False
        vault_root = "/vault"
        _homebase_tree_refresh_reason = "periodic local filesystem scan"
        current_path = None
        _local_fs_refresh_generation = 0
        _recent_self_saved_paths = {}

        def __init__(self) -> None:
            self.status = _DummyStatusBar()
            self._local_fs_refresh_result_queue = queue.Queue()
            self._local_fs_refresh_result_timer = _DummyTimer()
            self._local_fs_page_snapshot = {}
            self._homebase_sync_engine = _DummyEngine()
            self.unsynced_marks = 0
            self.sync_reasons: list[str] = []
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
            return None

        def _schedule_homebase_tree_refresh_on_ui_activity(self, reason: str) -> None:
            return None

        def _is_editor_idle_for_remote_reload(self) -> bool:
            return False

        def _refresh_detached_task_panels(self) -> None:
            return None

        def _refresh_detached_calendar_panels(self) -> None:
            return None

        def _refresh_detached_link_panels(self, path) -> None:
            return None

        def _is_homebase_mode_enabled(self) -> bool:
            return True

        def _mark_homebase_unsynced_local_change(self) -> None:
            self.unsynced_marks += 1

        def _schedule_homebase_sync(self, reason: str) -> None:
            self.sync_reasons.append(reason)

        def statusBar(self) -> _DummyStatusBar:
            return self.status

    monkeypatch.setattr(config, "bump_tree_version", lambda: None)
    monkeypatch.setattr(threading, "Thread", _ImmediateThread)

    dummy = _Dummy()
    MainWindow._on_local_fs_ui_quiet_timeout(dummy)
    MainWindow._drain_local_fs_refresh_results(dummy)

    assert dummy.unsynced_marks == 1
    assert dummy.sync_reasons == ["local filesystem scan"]


def test_self_saved_path_updates_local_snapshot(tmp_path) -> None:
    vault_root = tmp_path / "vault"
    page = vault_root / "PageA" / "PageA.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text("# A\n", encoding="utf-8")

    class _Dummy:
        _remote_mode = False

        def __init__(self) -> None:
            self.vault_root = str(vault_root)
            self.vault_root_name = vault_root.name
            self._recent_self_saved_paths = {}
            self._local_fs_page_snapshot = {}

        def _normalize_editor_path(self, path: str) -> str:
            return MainWindow._normalize_editor_path(self, path)

        def _folder_to_file_path(self, path: str) -> str:
            return MainWindow._folder_to_file_path(self, path)

        def _normalize_root_page_path(self, path: str) -> str:
            return MainWindow._normalize_root_page_path(self, path)

    dummy = _Dummy()
    MainWindow._mark_recent_self_saved_path(dummy, "/PageA/PageA.md")

    assert "/PageA/PageA.md" in dummy._recent_self_saved_paths
    assert dummy._local_fs_page_snapshot["/PageA/PageA.md"][1] == page.stat().st_size


def test_homebase_fs_change_suppresses_status_for_recent_self_save(tmp_path) -> None:
    vault_root = tmp_path / "vault"
    page_dir = vault_root / "PageA"
    page_dir.mkdir(parents=True, exist_ok=True)

    class _Dummy:
        _remote_mode = False
        _homebase_sync_engine = None

        def __init__(self) -> None:
            self.vault_root = str(vault_root)
            self.status = _DummyStatusBar()
            self._homebase_watch_refresh_timer = _DummyTimer()
            self._recent_self_saved_paths = {"/PageA/PageA.md": 10_000.0}
            self.calls: list[str] = []

        def _prune_recent_self_saved_paths(self) -> None:
            return None

        def _normalize_local_watch_path(self, changed_path: str | None) -> str | None:
            return MainWindow._normalize_local_watch_path(self, changed_path)

        def _should_suppress_local_fs_change(self, changed_path: str | None) -> bool:
            return MainWindow._should_suppress_local_fs_change(self, changed_path)

        def _schedule_local_filesystem_ui_refresh(self, reason: str, changed_path: str | None = None) -> None:
            self.calls.append(f"schedule:{reason}:{changed_path}")

        def _is_homebase_mode_enabled(self) -> bool:
            return False

        def _update_homebase_status_badge(self, status) -> None:
            self.calls.append("badge")

        def statusBar(self) -> _DummyStatusBar:
            return self.status

    dummy = _Dummy()
    MainWindow._on_homebase_fs_changed(dummy, str(page_dir))

    assert dummy.calls == []
    assert dummy.status.messages == []
    assert dummy._homebase_watch_refresh_timer.started == 1
