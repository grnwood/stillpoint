from pathlib import Path
import sqlite3

import pytest

from sp.app import config, indexer
from sp.app.task_mutations import TaskConflictError, TaskMutationTarget, mutate_task, undo_file_mutation
from sp.server import api
from sp.server.adapters.files import FileAccessError


@pytest.fixture
def vault(tmp_path, monkeypatch):
    config.set_active_vault(str(tmp_path))
    monkeypatch.setattr(api, "_get_vault_root", lambda: tmp_path)
    monkeypatch.setattr(config, "_TASK_SOURCE_CHECK", None)
    api._clear_task_cache()
    yield tmp_path
    api._clear_task_cache()
    config.set_active_vault(None)


def page(root, path, text):
    target = root / path.lstrip("/")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    indexer.index_page(path, text)
    return target


def test_prune_only_removes_derived_tasks_for_unavailable_sources(vault):
    live = page(vault, "/Keep_%/Keep_%.md", "☐ Keep @live\n")
    missing = page(vault, "/Gone/Gone.md", "☐ Ghost @orphan\n")
    unsupported = page(vault, "/Import/Other.md", "☐ Real file but not a StillPoint page @invalid\n")
    missing.unlink()
    assert config.prune_task_sources(vault, force=True) == 2
    assert [task["text"] for task in config.fetch_tasks()] == ["Keep"]
    assert live.read_text() == "☐ Keep @live\n"
    assert unsupported.read_text() == "☐ Real file but not a StillPoint page @invalid\n"
    assert not missing.exists()
    conn = config._get_conn()
    assert conn.execute("SELECT COUNT(*) FROM task_tags WHERE tag IN ('orphan', 'invalid')").fetchone()[0] == 0
    if config._tasks_fts_exists(conn):
        assert conn.execute("SELECT COUNT(*) FROM tasks_fts WHERE task_id IN (?, ?)",
                            ("/Gone/Gone.md:1", "/Import/Other.md:1")).fetchone()[0] == 0
    assert config.get_page_hash("/Gone/Gone.md") is None
    # Reappearing with the same content must not be skipped by the hash cache.
    missing.write_text("☐ Ghost @orphan\n")
    assert indexer.index_page("/Gone/Gone.md", missing.read_text())
    assert len(config.fetch_tasks()) == 2


@pytest.mark.parametrize("changes", [{"delete": True}, {"status": "done"}])
def test_stale_task_action_cleans_index_without_recreating_page(vault, changes):
    missing = page(vault, "/Gone/Gone.md", "☐ Ghost\n")
    missing.unlink()
    result = mutate_task(vault, TaskMutationTarget("/Gone/Gone.md", 1, "Ghost", "todo"), **changes)
    assert result["removed_stale"] == 1
    assert result["paths"] == []
    assert config.fetch_tasks() == []
    assert not missing.exists()


def test_stale_task_action_leaves_noncanonical_source_untouched(vault):
    source = page(vault, "/Import/Other.md", "☐ Task\n")
    result = mutate_task(vault, TaskMutationTarget("/Import/Other.md", 1, "Task", "todo"), delete=True)
    assert result["removed_stale"] == 1
    assert source.read_text() == "☐ Task\n"
    assert config.fetch_tasks() == []


def test_prune_does_not_clear_tasks_for_an_unmounted_vault(vault):
    source = page(vault, "/Page/Page.md", "☐ Task\n")
    moved = vault.with_name(vault.name + "-unmounted")
    vault.rename(moved)
    try:
        assert config.prune_task_sources(vault, force=True) == 0
        assert len(config.fetch_tasks()) == 1
    finally:
        moved.rename(vault)
    assert source.exists()


def test_api_cache_cannot_keep_deleted_source_tasks(vault, monkeypatch):
    source = page(vault, "/Page/Page.md", "☐ Task\n")
    params = dict(include_done=True, include_ancestors=False, actionable_only=False, status=None)
    assert len(api._fetch_tasks("", (), **params)) == 1
    source.unlink()
    monkeypatch.setattr(config, "_TASK_SOURCE_CHECK", None)
    assert api._fetch_tasks("", (), **params) == []


def test_reindex_reconciles_sources_and_reparses_unchanged_pages(vault):
    valid = page(vault, "/Valid/Valid.md", "☐ Valid\n")
    missing = page(vault, "/Gone/Gone.md", "☐ Ghost\n")
    unsupported = page(vault, "/Import/Other.md", "☐ Attachment\n")
    missing.unlink()
    # Simulate a damaged task index whose content hash still claims it is current.
    conn = config._get_conn()
    with conn:
        conn.execute("DELETE FROM tasks WHERE path = '/Valid/Valid.md'")
    config._invalidate_task_cache()
    job = "task-recovery-test"
    api._REINDEX_JOBS[job] = {}
    try:
        api._do_reindex_vault(job, vault, False)
        assert api._REINDEX_JOBS[job]["status"] == "completed"
        assert api._REINDEX_JOBS[job]["total"] == 1
        assert [task["text"] for task in config.fetch_tasks()] == ["Valid"]
        assert not missing.exists()
        assert unsupported.read_text() == "☐ Attachment\n"
        assert valid.read_text() == "☐ Valid\n"
    finally:
        api._REINDEX_JOBS.pop(job, None)


def test_missing_source_api_action_has_no_fake_undo(vault):
    source = page(vault, "/Gone/Gone.md", "☐ Ghost\n")
    source.unlink()
    result = api.api_mutate_tasks(api.TaskMutationPayload(targets=[api.TaskMutationTargetPayload(
        path="/Gone/Gone.md", line=1, expected_text="Ghost", expected_status="todo",
    )], delete=True), user=None)
    assert result["updated"] == 0
    assert result["removed_stale"] == 1
    assert result["undo_id"] is None
    assert result["paths"] == []
    assert config.fetch_tasks() == []


def test_missing_destination_does_not_create_or_change_files(vault):
    source = page(vault, "/Source/Source.md", "☐ Move me\n")
    (vault / "Destination").mkdir()
    with pytest.raises(FileNotFoundError):
        mutate_task(vault, TaskMutationTarget("/Source/Source.md", 1, "Move me", "todo"),
                    destination="/Destination/Destination.md")
    assert source.read_text() == "☐ Move me\n"
    assert not (vault / "Destination/Destination.md").exists()


def test_outside_vault_source_is_not_treated_as_a_stale_task(vault):
    with pytest.raises(FileAccessError):
        mutate_task(vault, TaskMutationTarget("/../outside.md", 1, "Task", "todo"), delete=True)


def test_cleanup_keeps_legacy_tasks_and_migrates_them_to_preferred_markdown(vault):
    legacy = page(vault, "/Legacy/Legacy.txt", "☐ Task\n")
    assert config.prune_task_sources(vault, force=True) == 0
    mutate_task(vault, TaskMutationTarget("/Legacy/Legacy.txt", 1, "Task", "todo"), status="done")
    assert config.prune_task_sources(vault, force=True) == 1
    tasks = config.fetch_tasks(include_done=True)
    assert len(tasks) == 1
    assert tasks[0]["path"] == "/Legacy/Legacy.md"
    assert tasks[0]["status"] == "done"
    assert legacy.read_text() == "☐ Task\n"


def test_main_window_reindex_does_not_reintroduce_invalid_sources(vault):
    from types import SimpleNamespace
    from sp.app.ui.main_window import MainWindow

    page(vault, "/Valid/Valid.md", "☐ Valid\n")
    missing = page(vault, "/Gone/Gone.md", "☐ Ghost\n")
    invalid = page(vault, "/Import/Other.md", "☐ Invalid\n")
    missing.unlink()
    host = SimpleNamespace(
        vault_root=str(vault), _ensure_writable=lambda operation: True, current_path=None,
        right_panel=SimpleNamespace(refresh_tasks=lambda: None, refresh_links=lambda path: None),
    )
    MainWindow._reindex_vault(host)
    assert [task["text"] for task in config.fetch_tasks()] == ["Valid"]
    assert invalid.read_text() == "☐ Invalid\n"


def test_task_that_no_longer_exists_in_a_real_page_is_reconciled(vault):
    source = page(vault, "/Page/Page.md", "☐ Old task\n")
    source.write_text("# Page\n\n☐ New task\n")
    result = mutate_task(vault, TaskMutationTarget("/Page/Page.md", 1, "Old task", "todo"), delete=True)
    assert result["removed_stale"] == 1
    assert [task["text"] for task in config.fetch_tasks()] == ["New task"]
    assert source.read_text() == "# Page\n\n☐ New task\n"


def test_task_caches_observe_another_process_index_revision(vault):
    source = page(vault, "/Page/Page.md", "☐ Task\n")
    params = dict(include_done=True, include_ancestors=False, actionable_only=False, status=None)
    assert api._fetch_tasks("", (), **params)[0]["status"] == "todo"
    local_version = config._TASK_INDEX_VERSION
    source.write_text("☑ Task\n")
    with sqlite3.connect(vault / ".stillpoint/settings.db") as external:
        external.execute("UPDATE tasks SET status='done', actionable=0")
        external.execute("UPDATE kv SET value=CAST(value AS INTEGER) + 1 WHERE key='task_index_revision'")
    assert config._TASK_INDEX_VERSION == local_version
    assert config.fetch_tasks(include_done=True)[0]["status"] == "done"
    assert api._fetch_tasks("", (), **params)[0]["status"] == "done"


def test_full_rebuild_invalidates_cached_tasks_even_with_no_pages(vault):
    source = page(vault, "/Gone/Gone.md", "☐ Ghost\n")
    assert len(config.fetch_tasks()) == 1
    version = config.get_task_index_version()
    source.unlink()
    config.rebuild_index_from_disk(vault)
    assert config.get_task_index_version() > version
    assert config.fetch_tasks() == []


def test_undo_does_not_recreate_a_deleted_source_page(vault):
    source = page(vault, "/Page/Page.md", "☐ Task\n")
    receipt = mutate_task(vault, TaskMutationTarget("/Page/Page.md", 1, "Task", "todo"), status="done")
    source.unlink()
    with pytest.raises(TaskConflictError, match="missing"):
        undo_file_mutation(vault, receipt)
    assert not source.exists()
