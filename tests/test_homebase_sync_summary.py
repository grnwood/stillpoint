from __future__ import annotations

from PySide6.QtWidgets import QDialog, QFrame, QPushButton, QScrollArea

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


def test_homebase_recovery_buttons_remain_outside_scrolling_body(main_window, monkeypatch) -> None:
    status = HomebaseSyncStatus(state="idle", summary="Up to date")

    class Engine:
        def get_status(self):
            return status

        def list_sync_errors(self, *, limit: int):
            return []

        def list_conflicts(self, *, limit: int):
            return []

    captured: list[QDialog] = []
    main_window._homebase_sync_engine = Engine()
    monkeypatch.setattr(main_window, "_is_homebase_mode_enabled", lambda: True)
    monkeypatch.setattr(QDialog, "exec", lambda dialog: captured.append(dialog) or QDialog.Rejected)

    main_window._show_homebase_sync_summary()

    assert len(captured) == 1
    dialog = captured[0]
    recovery = dialog.findChild(QFrame, "homebaseSyncRecoveryBar")
    body_scroll = dialog.findChild(QScrollArea, "homebaseSyncBodyScroll")
    reset_auth = dialog.findChild(QPushButton, "homebaseResetAuthButton")
    reset_encryption = dialog.findChild(QPushButton, "homebaseResetEncryptionButton")
    assert recovery is not None
    assert body_scroll is not None
    assert reset_auth is not None and reset_auth.parentWidget() is recovery
    assert reset_encryption is not None and reset_encryption.parentWidget() is recovery
    assert not body_scroll.isAncestorOf(reset_auth)
    assert not body_scroll.isAncestorOf(reset_encryption)
