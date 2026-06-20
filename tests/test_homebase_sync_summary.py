from __future__ import annotations

from sp.app.ui.main_window import MainWindow
from sp.sync.engine import HomebaseSyncStatus


def test_homebase_activity_snapshot_reports_pull_phase(main_window) -> None:
    status = HomebaseSyncStatus(
        state="syncing",
        summary="Pulling 24 object(s)...",
        pending_downloads=24,
        transfer_workers=["GET Notes/Page.md", "Idle"],
    )

    phase, details = MainWindow._homebase_activity_snapshot(main_window, status)

    assert phase == "Pulling from Homebase"
    assert "Pulling 24 object(s)..." in details
    assert "24 download(s) remaining" in details
    assert "GET Notes/Page.md" in details


def test_homebase_activity_snapshot_reports_upload_phase(main_window) -> None:
    status = HomebaseSyncStatus(
        state="syncing",
        summary="Uploading 8 object(s)...",
        pending_uploads=8,
        transfer_workers=["PUT Journal/2026/20/20.md"],
    )

    phase, details = MainWindow._homebase_activity_snapshot(main_window, status)

    assert phase == "Uploading to Homebase"
    assert "Uploading 8 object(s)..." in details
    assert "8 upload(s) remaining" in details
    assert "PUT Journal/2026/20/20.md" in details


def test_homebase_activity_snapshot_reports_backoff_phase(main_window) -> None:
    status = HomebaseSyncStatus(
        state="offline",
        summary="Offline (retry backoff)",
        last_error="timeout",
    )

    phase, details = MainWindow._homebase_activity_snapshot(main_window, status)

    assert phase == "Waiting to retry"
    assert details == ["Offline (retry backoff)"]
