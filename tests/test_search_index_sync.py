from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject

from sp.app.ui.search_index_sync import PeriodicSearchIndexSync


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, rows, row_query: str = "SELECT path, mtime FROM pages_search_index"):
        self._rows = rows
        self._row_query = row_query
        self.closed = False
        self.pragmas: list[str] = []

    def execute(self, sql: str):
        if "PRAGMA busy_timeout" in sql:
            self.pragmas.append(sql)
            return _FakeCursor([])
        assert self._row_query in sql
        return _FakeCursor(self._rows)

    def close(self):
        self.closed = True


class _ImmediateThread:
    def __init__(self, target, daemon=False):
        self._target = target
        self.daemon = daemon

    def start(self):
        self._target()


def _build_sync(*, enabled: bool, remote: bool, vault_root: str | None, db_path: str | None):
    state = {
        "enabled": enabled,
        "remote": remote,
        "vault_root": vault_root,
        "db_path": db_path,
    }
    logs: list[str] = []
    parent = QObject()
    sync = PeriodicSearchIndexSync(
        parent,
        is_enabled=lambda: bool(state["enabled"]),
        is_remote_mode=lambda: bool(state["remote"]),
        get_vault_root=lambda: state["vault_root"],
        get_db_path=lambda: state["db_path"],
        log_fn=logs.append,
        interval_ms=1000,
    )
    return parent, sync, state, logs


def test_update_timer_gates_on_feature_mode_and_vault(qapp) -> None:
    _parent, sync, state, _logs = _build_sync(enabled=False, remote=False, vault_root=None, db_path=None)

    sync.update_timer()
    assert not sync._timer.isActive()

    state["enabled"] = True
    state["vault_root"] = "/tmp/vault"
    sync.update_timer()
    assert sync._timer.isActive()

    state["remote"] = True
    sync.update_timer()
    assert not sync._timer.isActive()

    sync.stop()
    assert not sync._timer.isActive()


def test_update_timer_schedules_deferred_first_run_when_enabled(monkeypatch, qapp) -> None:
    _parent, sync, state, _logs = _build_sync(enabled=False, remote=False, vault_root=None, db_path="db.sqlite")
    calls: list[str] = []
    monkeypatch.setattr(sync, "maybe_run", lambda: calls.append("run"))

    sync.update_timer()
    assert calls == []

    state["enabled"] = True
    state["vault_root"] = "/tmp/vault"
    # Use a very short startup delay so the deferred timer fires during processEvents
    sync._startup_delay_ms = 0
    sync.update_timer()
    qapp.processEvents()
    assert calls == ["run"]

    # Re-applying while already active should not trigger a second immediate run.
    sync.update_timer()
    qapp.processEvents()
    assert calls == ["run"]


def test_suspend_prevents_timer_and_runs_until_resumed(monkeypatch, qapp) -> None:
    _parent, sync, state, logs = _build_sync(enabled=True, remote=False, vault_root="/tmp/vault", db_path="db.sqlite")
    calls: list[str] = []
    monkeypatch.setattr(sync, "maybe_run", lambda: calls.append("run"))

    sync.suspend("test")
    sync.update_timer()
    assert not sync._timer.isActive()
    assert calls == []
    assert "[SearchIndex] periodic sync suspended (test)" in logs

    # Use a very short startup delay so deferred timer fires during processEvents
    sync._startup_delay_ms = 0
    sync.resume("test")
    qapp.processEvents()
    assert sync._timer.isActive()
    assert calls == ["run"]
    assert any("periodic sync resumed (test)" in log for log in logs)

    state["enabled"] = False
    sync.update_timer()
    assert not sync._timer.isActive()


def test_maybe_run_skips_when_already_running(qapp) -> None:
    _parent, sync, _state, logs = _build_sync(enabled=True, remote=False, vault_root="/tmp/vault", db_path="db.sqlite")
    sync._in_progress = True

    sync.maybe_run()

    assert "[SearchIndex] periodic sync skipped: previous run still in progress" in logs


def test_maybe_run_emits_summary_and_updates_index(monkeypatch, qapp, tmp_path: Path) -> None:
    vault_root = tmp_path / "vault"
    vault_root.mkdir(parents=True)
    page = vault_root / "Page.md"
    page.write_text("# Hello\n", encoding="utf-8")

    _parent, sync, _state, logs = _build_sync(
        enabled=True,
        remote=False,
        vault_root=str(vault_root),
        db_path=str(tmp_path / "settings.db"),
    )

    fake_conn = _FakeConn(rows=[("/stale.md", 0)])
    monkeypatch.setattr("sp.app.ui.search_index_sync.sqlite3.connect", lambda *_args, **_kwargs: fake_conn)
    monkeypatch.setattr("sp.app.ui.search_index_sync.threading.Thread", _ImmediateThread)

    deleted: list[str] = []
    upserted: list[tuple[str, int, str]] = []

    monkeypatch.setattr(
        "sp.app.ui.search_index_sync.search_index.delete_page",
        lambda _conn, path: (deleted.append(path), True)[1],
    )
    monkeypatch.setattr(
        "sp.app.ui.search_index_sync.search_index.upsert_page",
        lambda _conn, path, mtime, content: (upserted.append((path, mtime, content)), True)[1],
    )

    status_messages: list[tuple[str, int]] = []
    sync.statusReady.connect(lambda message, timeout_ms: status_messages.append((message, timeout_ms)))

    sync.maybe_run()

    assert "[SearchIndex] periodic sync started" in logs
    assert any(line.startswith("[SearchIndex] periodic sync summary ") for line in logs)
    assert deleted == ["/stale.md"]
    assert len(upserted) == 1
    assert upserted[0][0] == "/Page.md"
    assert upserted[0][2].startswith("# Hello")
    assert status_messages
    assert status_messages[0][0].startswith("Search index sync: scanned 1, indexed 1, removed 1")
    assert status_messages[0][1] == 4000
    assert fake_conn.closed is True
    assert fake_conn.pragmas == ["PRAGMA busy_timeout = 750"]
    assert sync._in_progress is False


def test_maybe_run_skips_upsert_when_mtime_unchanged(monkeypatch, qapp, tmp_path: Path) -> None:
    vault_root = tmp_path / "vault"
    vault_root.mkdir(parents=True)
    page = vault_root / "Page.md"
    page.write_text("# Hello\n", encoding="utf-8")
    page_mtime = int(page.stat().st_mtime)

    _parent, sync, _state, logs = _build_sync(
        enabled=True,
        remote=False,
        vault_root=str(vault_root),
        db_path=str(tmp_path / "settings.db"),
    )

    fake_conn = _FakeConn(rows=[("/Page.md", page_mtime)])
    monkeypatch.setattr("sp.app.ui.search_index_sync.sqlite3.connect", lambda *_args, **_kwargs: fake_conn)
    monkeypatch.setattr("sp.app.ui.search_index_sync.threading.Thread", _ImmediateThread)

    deleted: list[str] = []
    upserted: list[tuple[str, int, str]] = []

    monkeypatch.setattr(
        "sp.app.ui.search_index_sync.search_index.delete_page",
        lambda _conn, path: (deleted.append(path), True)[1],
    )
    monkeypatch.setattr(
        "sp.app.ui.search_index_sync.search_index.upsert_page",
        lambda _conn, path, mtime, content: (upserted.append((path, mtime, content)), True)[1],
    )

    sync.maybe_run()

    assert deleted == []
    assert upserted == []
    assert any("upsert_candidates=0 upserted=0" in line for line in logs)
