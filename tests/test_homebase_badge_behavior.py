import time
import inspect

from sp.app.ui.main_window import MainWindow
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
