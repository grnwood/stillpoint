from __future__ import annotations

from PySide6.QtWidgets import QDialog, QFrame, QListWidget, QPushButton, QScrollArea, QTextEdit

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


def test_homebase_activity_snapshot_reports_incremental_check_phase(main_window) -> None:
    status = HomebaseSyncStatus(
        state="syncing",
        summary="Checking local vault (7500/7615; 1 hashed)...",
    )

    phase, details = MainWindow._homebase_activity_snapshot(main_window, status)

    assert phase == "Checking local changes"
    assert details == ["Checking local vault (7500/7615; 1 hashed)..."]


def test_homebase_activity_snapshot_reports_backoff_phase(main_window) -> None:
    status = HomebaseSyncStatus(
        state="offline",
        summary="Offline (retry backoff)",
        last_error="timeout",
    )

    phase, details = MainWindow._homebase_activity_snapshot(main_window, status)

    assert phase == "Waiting to retry"
    assert details == ["Offline (retry backoff)"]


def test_homebase_activity_snapshot_reports_timed_retry_phase(main_window) -> None:
    status = HomebaseSyncStatus(
        state="offline",
        summary="Offline (retry in 42s)",
        last_error="File name too long",
    )

    phase, details = MainWindow._homebase_activity_snapshot(main_window, status)

    assert phase == "Waiting to retry"
    assert details == ["Offline (retry in 42s)"]


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
    open_backup_folder = dialog.findChild(QPushButton, "homebaseOpenRecoveryFolderButton")
    assert recovery is not None
    assert body_scroll is not None
    assert reset_auth is not None and reset_auth.parentWidget() is recovery
    assert reset_encryption is not None and reset_encryption.parentWidget() is recovery
    assert open_backup_folder is not None
    assert not body_scroll.isAncestorOf(reset_auth)
    assert not body_scroll.isAncestorOf(reset_encryption)


def test_sync_problem_dialog_treats_old_errors_as_history_and_hides_destructive_action(
    main_window, monkeypatch
) -> None:
    captured: list[QDialog] = []
    monkeypatch.setattr(QDialog, "exec", lambda dialog: captured.append(dialog) or QDialog.Rejected)

    main_window._show_homebase_sync_errors_popup(
        [
            {
                "path": "Journal/old.md",
                "phase": "apply",
                "reason": "temporary staging failure",
                "object_id": "a" * 64,
                "ts": "2026-08-27T14:35:00Z",
                "attempts": 1,
                "active": False,
            }
        ]
    )

    assert len(captured) == 1
    dialog = captured[0]
    assert dialog.windowTitle() == "Homebase Sync Problems"
    retry = dialog.findChild(QPushButton, "homebaseSyncRetryButton")
    dismiss = dialog.findChild(QPushButton, "homebaseSyncDismissHistoryButton")
    delete_remote = dialog.findChild(QPushButton, "homebaseSyncDeleteRemoteButton")
    assert retry is not None and retry.isEnabled() is False
    assert dismiss is not None and dismiss.isEnabled() is True
    assert delete_remote is not None and delete_remote.isHidden() is True


def test_local_authoritative_review_lists_files_and_displays_diff(main_window, monkeypatch) -> None:
    captured: list[QDialog] = []
    monkeypatch.setattr(QDialog, "exec", lambda dialog: captured.append(dialog) or QDialog.Rejected)
    preview = {
        "local_only": 1,
        "remote_only": 1,
        "changed": 1,
        "unchanged": 4,
        "changes": [
            {
                "path": "Notes/Page.md",
                "action": "replace",
                "local_size": 12,
                "remote_size": 13,
                "local_object_id": "a" * 64,
                "remote_object_id": "b" * 64,
                "preview": "--- Homebase version\n+++ This device\n-old\n+new\n",
            },
            {
                "path": "Notes/New.md",
                "action": "add",
                "local_size": 8,
                "remote_size": None,
                "local_object_id": "c" * 64,
                "remote_object_id": "",
                "preview": "new file",
            },
            {
                "path": "Notes/RemoteOnly.md",
                "action": "remove",
                "local_size": None,
                "remote_size": 9,
                "local_object_id": "",
                "remote_object_id": "d" * 64,
                "preview": "old remote file",
            },
        ],
    }

    accepted = main_window._review_local_authoritative_preview(preview)

    assert accepted is False
    assert len(captured) == 1
    dialog = captured[0]
    changes = dialog.findChild(QListWidget, "homebaseAuthoritativeChangeList")
    diff = dialog.findChild(QTextEdit, "homebaseAuthoritativeDiff")
    publish = dialog.findChild(QPushButton, "homebaseAuthoritativePublishButton")
    assert changes is not None and changes.count() == 3
    assert diff is not None and "-old" in diff.toPlainText() and "+new" in diff.toPlainText()
    assert publish is not None and publish.isEnabled() is True
