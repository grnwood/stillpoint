import inspect
import sqlite3
import time
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from sp.app.ui.main_window import MainWindow
from sp.app import config
from sp.sync.engine import HomebaseSyncStatus


class _DummyLabel:
    def __init__(self) -> None:
        self.text = ""
        self.tooltip = ""
        self.stylesheet = ""
        self.visible = False

    def hide(self) -> None:
        self.visible = False

    def show(self) -> None:
        self.visible = True

    def setText(self, value: str) -> None:
        self.text = value

    def setToolTip(self, value: str) -> None:
        self.tooltip = value

    def setStyleSheet(self, value: str) -> None:
        self.stylesheet = value


class _DummySyncCfg:
    auto_sync = True


class _DummySyncEngine:
    cfg = _DummySyncCfg()


class _DummyWindow:
    def __init__(self) -> None:
        self._homebase_status_label = _DummyLabel()
        self._homebase_sync_engine = _DummySyncEngine()
        self._badge_base_style = ""
        self._dirty_flag = False
        self._homebase_has_unsynced_local_changes = False
        self._homebase_sync_blue_threshold_seconds = 0.5
        self._homebase_sync_activity_started_at = None
        self._homebase_sync_cycle_had_true_activity = False
        self._homebase_last_real_sync_at = None

    def _is_homebase_mode_enabled(self) -> bool:
        return True


class _DummyDoc:
    def __init__(self, modified: bool) -> None:
        self._modified = modified

    def isModified(self) -> bool:
        return self._modified


class _DummyEditor:
    def __init__(self, content: str, *, modified: bool = False) -> None:
        self._content = content
        self._doc = _DummyDoc(modified)

    def document(self):
        return self._doc

    def to_markdown(self) -> str:
        return self._content


def test_open_file_does_not_schedule_sync_on_page_load() -> None:
    source = inspect.getsource(MainWindow._open_file)
    assert '_schedule_homebase_sync("page load")' not in source


def test_syncing_badge_stays_non_blue_without_true_activity() -> None:
    window = _DummyWindow()
    status = HomebaseSyncStatus(state="syncing", summary="Syncing", pending=False)

    MainWindow._update_homebase_status_badge(window, status)

    assert window._homebase_status_label.visible is True
    assert window._homebase_status_label.text == "HOMEBASE"
    assert "#1565c0" not in window._homebase_status_label.stylesheet


def test_syncing_badge_stays_non_blue_before_threshold() -> None:
    window = _DummyWindow()
    status = HomebaseSyncStatus(
        state="syncing",
        summary="Syncing",
        pending=False,
        pending_uploads=1,
    )

    MainWindow._update_homebase_status_badge(window, status)

    assert "#1565c0" not in window._homebase_status_label.stylesheet


def test_syncing_badge_turns_blue_with_true_activity() -> None:
    window = _DummyWindow()
    window._homebase_sync_activity_started_at = time.monotonic() - 0.6
    status = HomebaseSyncStatus(
        state="syncing",
        summary="Syncing",
        pending=False,
        pending_uploads=1,
    )

    MainWindow._update_homebase_status_badge(window, status)

    assert "#1565c0" in window._homebase_status_label.stylesheet


def test_hibernated_badge_stays_gray_without_manifest_delta() -> None:
    window = _DummyWindow()
    status = HomebaseSyncStatus(state="hibernated", summary="Hibernated", pending=False)

    MainWindow._update_homebase_status_badge(window, status)

    assert "#757575" in window._homebase_status_label.stylesheet


def test_tooltip_includes_last_real_sync_timestamp() -> None:
    window = _DummyWindow()
    window._homebase_sync_cycle_had_true_activity = True
    status = HomebaseSyncStatus(
        state="idle",
        summary="Up to date",
        pending=False,
        last_sync_at="2026-03-01T10:00:00Z",
    )

    MainWindow._update_homebase_status_badge(window, status)

    assert "Last real sync: 2026-03-01T10:00:00Z" in window._homebase_status_label.tooltip


def test_editor_not_idle_for_remote_reload_when_content_differs_from_last_saved() -> None:
    class _Dummy:
        current_path = "/Journal/2026/03/04/Test.md"
        _merge_dialog_open = False
        _dirty_flag = False
        _last_saved_content = "saved content"
        editor = _DummyEditor("changed content", modified=False)

        class autosave_timer:
            @staticmethod
            def isActive() -> bool:
                return False

    assert MainWindow._is_editor_idle_for_remote_reload(_Dummy()) is False


def test_editor_idle_for_remote_reload_when_clean_and_content_matches_last_saved() -> None:
    class _Dummy:
        current_path = "/Journal/2026/03/04/Test.md"
        _merge_dialog_open = False
        _dirty_flag = False
        _last_saved_content = "saved content"
        editor = _DummyEditor("saved content", modified=False)

        class autosave_timer:
            @staticmethod
            def isActive() -> bool:
                return False

    assert MainWindow._is_editor_idle_for_remote_reload(_Dummy()) is True


def test_autosave_does_not_skip_when_dirty_flag_true_even_if_doc_unmodified() -> None:
    class _Dummy:
        _heading_picker_active = False
        _merge_dialog_open = False
        _suspend_autosave = False
        _read_only = False
        _dirty_flag = True
        current_path = "/Journal/2026/03/04/Test.md"
        _last_saved_content = "old content"
        editor = _DummyEditor("new content", modified=False)
        virtual_pages = set()
        autosave_timer = None

        def __init__(self):
            self.ensure_writable_called = False

        def _debug(self, *_args, **_kwargs) -> None:
            pass

        def _ensure_writable(self, *_args, **_kwargs) -> bool:
            self.ensure_writable_called = True
            return False

    dummy = _Dummy()
    MainWindow._save_current_file(dummy, auto=True, reason="application deactivated")
    assert dummy.ensure_writable_called is True


def test_force_autosave_can_bypass_suspend_flag() -> None:
    class _Dummy:
        _heading_picker_active = False
        _merge_dialog_open = False
        _suspend_autosave = True
        _read_only = False
        _dirty_flag = True
        current_path = "/Journal/2026/03/04/Test.md"
        _last_saved_content = "old content"
        editor = _DummyEditor("new content", modified=False)
        virtual_pages = set()
        autosave_timer = None

        def __init__(self):
            self.ensure_writable_called = False

        def _debug(self, *_args, **_kwargs) -> None:
            pass

        def _ensure_writable(self, *_args, **_kwargs) -> bool:
            self.ensure_writable_called = True
            return False

    dummy = _Dummy()
    MainWindow._save_current_file(
        dummy,
        auto=True,
        reason="application deactivated",
        force=True,
        allow_when_suspended=True,
    )
    assert dummy.ensure_writable_called is True


def test_application_deactivated_forces_save_when_dirty() -> None:
    class _Dummy:
        def __init__(self) -> None:
            self.saved_calls = []
            self.remember_calls = 0

        def _remember_history_cursor(self) -> None:
            self.remember_calls += 1

        def _is_editor_dirty(self) -> bool:
            return True

        def _save_current_file(self, *args, **kwargs) -> None:
            self.saved_calls.append((args, kwargs))

    dummy = _Dummy()
    MainWindow._on_application_state_changed(dummy, Qt.ApplicationState.ApplicationInactive)
    assert dummy.remember_calls == 1
    assert len(dummy.saved_calls) == 1
    _args, kwargs = dummy.saved_calls[0]
    assert kwargs.get("auto") is True
    assert kwargs.get("reason") == "application deactivated"
    assert kwargs.get("force") is True
    assert kwargs.get("allow_when_suspended") is True


def test_is_editor_dirty_clears_false_positive_for_local_mode() -> None:
    class _Dummy:
        current_path = "/Journal/2026/03/04/Test.md"
        _dirty_flag = True
        _last_saved_content = "saved content"
        editor = _DummyEditor("saved content", modified=True)
        _homebase_sync_engine = None

    dummy = _Dummy()
    assert MainWindow._is_editor_dirty(dummy) is False
    assert dummy._dirty_flag is False


def test_on_document_modified_ignores_noop_content_change_in_local_mode() -> None:
    """
    Test that document modification events are ignored when content hasn't actually changed in local mode.
    This test verifies that when a document is marked as modified but the content remains the same
    as the last saved content, and the application is in local mode (not homebase sync mode), the
    dirty flag should not be set and the dirty indicator should not be updated.
    The test creates a dummy MainWindow instance with:
    - Homebase sync disabled (local mode)
    - Editor content matching the last saved content
    - Modified flag set to True on the editor
    It then triggers the _on_document_modified handler and asserts that:
    - The dirty flag remains False (no-op change detected)
    - The update counter remains 0 (dirty indicator not updated)
    """
    class _Dummy:
        _suspend_dirty_tracking = False
        _dirty_flag = False
        _last_saved_content = "saved content"
        _homebase_sync_engine = None
        editor = _DummyEditor("saved content", modified=True)

        def __init__(self) -> None:
            self.updated = 0

        def _update_dirty_indicator(self) -> None:
            self.updated += 1

        def _is_homebase_mode_enabled(self) -> bool:
            return False

        def _dirty_state_from_editor(self, *, default: bool) -> bool:
            return MainWindow._dirty_state_from_editor(self, default=default)

    dummy = _Dummy()
    MainWindow._on_document_modified(dummy, True)
    assert dummy._dirty_flag is False
    assert dummy.updated == 0


def test_on_editor_text_changed_marks_dirty_when_content_differs() -> None:
    class _Timer:
        def __init__(self) -> None:
            self.starts = 0

        def start(self) -> None:
            self.starts += 1

    class _Dummy:
        _suspend_autosave = False
        _suspend_dirty_tracking = False
        _dirty_flag = False
        current_path = "/Journal/2026/03/04/Test.md"
        _last_saved_content = "saved content"
        _homebase_sync_engine = None
        editor = _DummyEditor("changed content", modified=False)

        def __init__(self) -> None:
            self.updated = 0
            self.autosave_timer = _Timer()

        def _update_dirty_indicator(self) -> None:
            self.updated += 1

        def _is_homebase_mode_enabled(self) -> bool:
            return False

        def _dirty_state_from_editor(self, *, default: bool) -> bool:
            return MainWindow._dirty_state_from_editor(self, default=default)

    dummy = _Dummy()
    MainWindow._on_editor_text_changed(dummy)
    assert dummy.autosave_timer.starts == 1
    assert dummy._dirty_flag is True
    assert dummy.updated == 1


def test_poll_homebase_status_does_not_reload_pending_page_when_auto_reload_blocked() -> None:
    class _Dummy:
        _homebase_pending_reload_path = "/Journal/2026/03/04/Test.md"
        current_path = "/Journal/2026/03/04/Test.md"
        _homebase_sync_engine = None
        _homebase_sync_cycle_had_true_activity = False

        def __init__(self) -> None:
            self.open_calls = 0

        def _homebase_status_clears_unsynced_marker(self, _status) -> bool:
            return False

        def _maybe_show_homebase_conflict_popup(self, _status) -> None:
            return

        def _can_auto_reload_homebase_current_page(self) -> bool:
            return False

        def _is_editor_idle_for_remote_reload(self) -> bool:
            return True

        def _open_file(self, *_args, **_kwargs) -> None:
            self.open_calls += 1

        def _update_homebase_status_badge(self, _status) -> None:
            return

        def _update_homebase_sync_action_state(self) -> None:
            return

    dummy = _Dummy()
    MainWindow._poll_homebase_status(dummy)
    assert dummy.open_calls == 0
    assert dummy._homebase_pending_reload_path == "/Journal/2026/03/04/Test.md"


def test_rebuild_index_reapplies_homebase_profile_after_db_reset(tmp_path, monkeypatch) -> None:
    class _DummyStatusBar:
        def __init__(self) -> None:
            self.messages = []

        def showMessage(self, text: str, timeout: int = 0) -> None:
            self.messages.append((text, timeout))

    class _DummySearchSync:
        def __init__(self) -> None:
            self.calls = []

        def suspend(self, reason: str) -> None:
            self.calls.append(("suspend", reason))

        def resume(self, reason: str) -> None:
            self.calls.append(("resume", reason))

    class _Dummy:
        _remote_mode = False

        def __init__(self, vault_root: str) -> None:
            self.vault_root = vault_root
            self._search_sync = _DummySearchSync()
            self._status = _DummyStatusBar()
            self.profile_applied = None
            self.reindex_calls = []
            self.alerts = []
            self.refresh_tree_calls = 0
            self.load_bookmarks_calls = 0

        def _ensure_writable(self, _reason: str) -> bool:
            return True

        def statusBar(self) -> _DummyStatusBar:
            return self._status

        def _alert(self, message: str) -> None:
            self.alerts.append(message)

        def _homebase_profile_for_path(self, local_path: str):
            assert local_path == self.vault_root
            return {
                "path": self.vault_root,
                "server_url": "https://homebase.example",
                "vault_id": "vault-123",
            }

        def _apply_homebase_profile(self, profile) -> None:
            self.profile_applied = profile

        def _reindex_vault(self, *, show_progress: bool = False) -> None:
            self.reindex_calls.append(show_progress)

        def _refresh_tree(self) -> None:
            self.refresh_tree_calls += 1

        def _load_bookmarks(self) -> None:
            self.load_bookmarks_calls += 1

    vault_root = tmp_path / "vault"
    settings_db = vault_root / ".stillpoint" / "settings.db"
    settings_db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(settings_db) as conn:
        conn.execute("CREATE TABLE kv(key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO kv(key, value) VALUES(?, ?)", ("preserved", "yes"))
        conn.commit()

    dummy = _Dummy(str(vault_root))
    active_vault_calls: list[str | None] = []
    rebuild_calls: list[Path] = []
    cleared_hashes: list[str] = []
    bumped_tree_versions: list[str] = []

    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes)
    monkeypatch.setattr(config, "has_active_vault", lambda: True)
    monkeypatch.setattr(config, "set_active_vault", lambda path: active_vault_calls.append(path))
    monkeypatch.setattr(config, "close_cached_vault_connections", lambda: None)
    monkeypatch.setattr(config, "rebuild_index_from_disk", lambda root: rebuild_calls.append(root))
    monkeypatch.setattr(config, "clear_page_hashes", lambda: cleared_hashes.append("cleared"))
    monkeypatch.setattr(config, "bump_tree_version", lambda: bumped_tree_versions.append("bumped"))

    MainWindow._rebuild_vault_index_from_disk(dummy)

    assert active_vault_calls == [str(vault_root)]
    assert rebuild_calls == [vault_root]
    assert cleared_hashes == ["cleared"]
    assert bumped_tree_versions == ["bumped"]
    assert dummy.profile_applied is not None
    assert dummy.profile_applied["server_url"] == "https://homebase.example"
    assert dummy.reindex_calls == [True]
    assert dummy.refresh_tree_calls == 1
    assert dummy.load_bookmarks_calls == 1
    assert settings_db.exists()
    assert dummy._search_sync.calls == [
        ("suspend", "manual rebuild index"),
        ("resume", "manual rebuild index"),
    ]
    assert dummy.alerts == []


def test_rebuild_index_retries_after_database_locked_error(tmp_path, monkeypatch) -> None:
    class _DummyStatusBar:
        def __init__(self) -> None:
            self.messages = []

        def showMessage(self, message: str, timeout: int = 0) -> None:
            self.messages.append((message, timeout))

    class _DummySearchSync:
        def __init__(self) -> None:
            self.calls = []

        def suspend(self, reason: str) -> None:
            self.calls.append(("suspend", reason))

        def resume(self, reason: str) -> None:
            self.calls.append(("resume", reason))

    class _Dummy:
        _remote_mode = False

        def __init__(self, vault_root: str) -> None:
            self.vault_root = vault_root
            self._status = _DummyStatusBar()
            self._search_sync = _DummySearchSync()
            self.reindex_calls = []
            self.alerts = []
            self.refresh_tree_calls = 0
            self.load_bookmarks_calls = 0

        def _ensure_writable(self, _reason: str) -> bool:
            return True

        def statusBar(self) -> _DummyStatusBar:
            return self._status

        def _alert(self, message: str) -> None:
            self.alerts.append(message)

        def _homebase_profile_for_path(self, _local_path: str):
            return None

        def _apply_homebase_profile(self, _profile) -> None:
            raise AssertionError("No Homebase profile expected")

        def _reindex_vault(self, *, show_progress: bool = False) -> None:
            self.reindex_calls.append(show_progress)

        def _refresh_tree(self) -> None:
            self.refresh_tree_calls += 1

        def _load_bookmarks(self) -> None:
            self.load_bookmarks_calls += 1

    vault_root = tmp_path / "vault"
    dummy = _Dummy(str(vault_root))
    active_vault_calls: list[str | None] = []
    close_calls: list[str] = []
    rebuild_attempts = {"count": 0}

    def flaky_rebuild(_root: Path) -> None:
        if rebuild_attempts["count"] == 0:
            rebuild_attempts["count"] += 1
            raise sqlite3.OperationalError("database is locked")
        rebuild_attempts["count"] += 1

    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes)
    monkeypatch.setattr(config, "has_active_vault", lambda: True)
    monkeypatch.setattr(config, "set_active_vault", lambda path: active_vault_calls.append(path))
    monkeypatch.setattr(config, "close_cached_vault_connections", lambda: close_calls.append("closed"))
    monkeypatch.setattr(config, "rebuild_index_from_disk", flaky_rebuild)
    monkeypatch.setattr(config, "clear_page_hashes", lambda: None)
    monkeypatch.setattr(config, "bump_tree_version", lambda: None)
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)

    MainWindow._rebuild_vault_index_from_disk(dummy)

    assert active_vault_calls == [str(vault_root)]
    assert close_calls == ["closed", "closed"]
    assert rebuild_attempts["count"] == 2
    assert dummy.reindex_calls == [True]
    assert dummy.refresh_tree_calls == 1
    assert dummy.load_bookmarks_calls == 1
    assert dummy.alerts == []
