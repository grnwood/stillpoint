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
