from __future__ import annotations

import subprocess

import pytest

from sp.app import config, git_support


def _git(root, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _repository(tmp_path):
    git_support.initialize_repository(tmp_path)
    _git(tmp_path, "config", "user.name", "StillPoint Test")
    _git(tmp_path, "config", "user.email", "stillpoint@example.invalid")
    return tmp_path


def test_git_mode_and_publish_choice_are_vault_local(tmp_path) -> None:
    config.set_active_vault(str(tmp_path))
    try:
        config.save_vault_remote_mode("git")
        config.save_git_publish_versions(True)
        config.save_git_auto_backup_enabled(True)
        config.save_git_auto_backup_interval_minutes(30)
        config.save_git_auto_publish_enabled(False)
        config.save_git_backup_on_close(False)

        assert config.load_vault_remote_mode() == "git"
        assert config.load_git_publish_versions() is True
        assert config.load_git_auto_backup_enabled() is True
        assert config.load_git_auto_backup_interval_minutes() == 30
        assert config.load_git_auto_publish_enabled() is False
        assert config.load_git_backup_on_close() is False
    finally:
        config.set_active_vault(None)


def test_git_mode_can_be_read_before_vault_is_active(tmp_path) -> None:
    config.set_active_vault(str(tmp_path))
    try:
        config.save_vault_remote_mode("git")
    finally:
        config.set_active_vault(None)

    assert config.load_vault_remote_mode_for_path(tmp_path) == "git"


def test_default_vault_save_records_explicit_confirmation(monkeypatch) -> None:
    updates: list[dict[str, object]] = []
    monkeypatch.setattr(config, "_update_global_config", updates.append)

    config.save_default_vault("/vaults/git")

    assert updates == [
        {
            "default_vault": "/vaults/git",
            "default_vault_confirmation": "/vaults/git",
        }
    ]


def test_initialize_requires_repository_at_vault_root_and_ignores_metadata(tmp_path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    git_support.initialize_repository(vault)

    assert git_support.is_exact_repository(vault)
    ignore_text = (vault / ".gitignore").read_text(encoding="utf-8")
    assert ".stillpoint/" in ignore_text
    assert ".codex/" in ignore_text
    assert ".mcp.json" in ignore_text
    git_support.initialize_repository(vault)
    ignore_lines = (vault / ".gitignore").read_text(encoding="utf-8").splitlines()
    for entry in git_support.DEFAULT_GITIGNORE_ENTRIES:
        assert ignore_lines.count(entry) == 1

    child = vault / "child"
    child.mkdir()
    with pytest.raises(git_support.GitError, match="inside"):
        git_support.initialize_repository(child)


def test_setup_stops_tracking_stillpoint_metadata_without_deleting_it(tmp_path) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.name", "StillPoint Test")
    _git(tmp_path, "config", "user.email", "stillpoint@example.invalid")
    metadata = tmp_path / ".stillpoint"
    metadata.mkdir()
    settings = metadata / "settings.db"
    settings.write_bytes(b"local settings")
    codex = tmp_path / ".codex"
    codex.mkdir()
    codex_settings = codex / "settings.json"
    codex_settings.write_text("{}", encoding="utf-8")
    mcp = tmp_path / ".mcp.json"
    mcp.write_text("{}", encoding="utf-8")
    _git(
        tmp_path,
        "add",
        "-f",
        ".stillpoint/settings.db",
        ".codex/settings.json",
        ".mcp.json",
    )
    _git(tmp_path, "commit", "-m", "tracked local state")

    git_support.initialize_repository(tmp_path)

    assert settings.read_bytes() == b"local settings"
    assert codex_settings.read_text(encoding="utf-8") == "{}"
    assert mcp.read_text(encoding="utf-8") == "{}"
    assert _git(tmp_path, "ls-files", ".stillpoint", ".codex", ".mcp.json").stdout == ""


def test_named_version_uses_reserved_tag_and_appears_in_file_history(tmp_path) -> None:
    root = _repository(tmp_path)
    page = root / "Page.md"
    page.write_text("first\n", encoding="utf-8")

    tag, committed = git_support.save_version(root, "First draft", "A useful note")

    (root / "Other.md").write_text("another page\n", encoding="utf-8")
    second_tag, _ = git_support.save_version(root, "Whole vault checkpoint")

    assert committed is True
    assert tag.startswith(git_support.VERSION_TAG_PREFIX)
    entries = git_support.history(root, "Page.md", versions_only=True)
    assert [entry.version_name for entry in entries] == [
        "Whole vault checkpoint",
        "First draft",
    ]
    assert entries[0].version_tag == second_tag
    assert entries[1].version_tag == tag


def test_save_changes_reports_clean_repository_without_empty_commit(tmp_path) -> None:
    root = _repository(tmp_path)
    (root / "Page.md").write_text("content\n", encoding="utf-8")
    assert git_support.save_changes(root, "Initial") is True
    assert git_support.save_changes(root, "Nothing") is False


def test_commit_details_report_files_counts_renames_and_binary(tmp_path) -> None:
    root = _repository(tmp_path)
    page = root / "Page.md"
    page.write_text("first\nsecond\nthird\n", encoding="utf-8")
    (root / "Remove.md").write_text("remove me\n", encoding="utf-8")
    (root / "Modified.md").write_text("before\n", encoding="utf-8")
    git_support.save_changes(root, "Initial")
    initial = _git(root, "rev-parse", "HEAD").stdout.strip()
    first_files = {file.path: file for file in git_support.commit_files(root, initial)}
    assert first_files["Page.md"].status == "A"
    assert first_files["Page.md"].additions == 3
    assert "+first" in git_support.commit_diff(root, initial, ["Page.md"])

    renamed = root / "Renamed ü\tpage.md"
    page.rename(renamed)
    (root / "Remove.md").unlink()
    (root / "Modified.md").write_text("after\n", encoding="utf-8")
    (root / "image.bin").write_bytes(b"\0\x01\x02")
    (root / "New.md").write_text("new\n", encoding="utf-8")
    git_support.save_changes(root, "Rename and add files")
    files = {file.path: file for file in git_support.commit_files(root, "HEAD")}
    assert files[renamed.name].original_path == "Page.md"
    assert files[renamed.name].status == "R"
    assert files[renamed.name].additions == 0
    assert files["image.bin"].additions is None
    assert files["New.md"].additions == 1
    assert files["Remove.md"].status == "D"
    assert files["Remove.md"].deletions == 1
    assert files["Modified.md"].status == "M"
    assert (files["Modified.md"].additions, files["Modified.md"].deletions) == (1, 1)
    patch = git_support.commit_diff(root, "HEAD", ["Page.md", renamed.name])
    assert "rename from" in patch
    # The commit patch is stable even after more working-copy edits.
    (root / "New.md").write_text("working edit\n", encoding="utf-8")
    assert "+new" in git_support.commit_diff(root, "HEAD", ["New.md"])
    assert "working edit" not in git_support.commit_diff(root, "HEAD", ["New.md"])
    assert "working edit" in git_support.diff_from_revision(root, "HEAD", "")


def test_commit_details_compare_merge_with_first_parent(tmp_path) -> None:
    root = _repository(tmp_path)
    (root / "Base.md").write_text("base\n", encoding="utf-8")
    git_support.save_changes(root, "Initial")
    branch = _git(root, "branch", "--show-current").stdout.strip()
    _git(root, "checkout", "-b", "topic")
    (root / "Topic.md").write_text("topic\n", encoding="utf-8")
    git_support.save_changes(root, "Topic")
    _git(root, "checkout", branch)
    (root / "Main.md").write_text("main\n", encoding="utf-8")
    git_support.save_changes(root, "Main")
    _git(root, "merge", "--no-ff", "topic", "-m", "Merge topic")
    files = git_support.commit_files(root, "HEAD")
    assert [file.path for file in files] == ["Topic.md"]
    assert "+topic" in git_support.commit_diff(root, "HEAD", ["Topic.md"])


def test_commit_paths_commits_only_selected_files(tmp_path) -> None:
    root = _repository(tmp_path)
    first = root / "First.md"
    second = root / "Second.md"
    first.write_text("initial first\n", encoding="utf-8")
    second.write_text("initial second\n", encoding="utf-8")
    assert git_support.save_changes(root, "Initial") is True
    first.write_text("changed first\n", encoding="utf-8")
    second.write_text("changed second\n", encoding="utf-8")

    assert git_support.commit_paths(root, "Update first", ["First.md"]) is True

    committed_paths = _git(root, "show", "--format=", "--name-only", "HEAD").stdout.splitlines()
    assert committed_paths == ["First.md"]
    remaining = git_support.status(root)
    assert [entry.path for entry in remaining] == ["Second.md"]


def test_automatic_backup_is_marked_and_hidden_from_history_by_default(tmp_path) -> None:
    root = _repository(tmp_path)
    page = root / "Page.md"
    page.write_text("draft\n", encoding="utf-8")

    assert git_support.save_automatic_backup(root) is True
    assert git_support.save_automatic_backup(root) is False
    message = _git(root, "log", "-1", "--format=%s%n%B").stdout
    assert message.startswith(git_support.AUTOMATIC_BACKUP_PREFIX)
    assert "StillPoint-Backup: true" in message
    assert git_support.history(root, "Page.md", versions_only=False) == []
    history = git_support.history(
        root,
        "Page.md",
        versions_only=False,
        include_automatic_backups=True,
    )
    assert len(history) == 1
    assert history[0].subject.startswith(git_support.AUTOMATIC_BACKUP_PREFIX)


def test_automatic_backup_publish_sets_upstream_and_pushes(tmp_path) -> None:
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    root = tmp_path / "vault"
    root.mkdir()
    _repository(root)
    git_support.set_remote(root, str(remote))
    (root / "Page.md").write_text("backed up\n", encoding="utf-8")
    git_support.save_automatic_backup(root)

    assert git_support.publish_automatic_backup(root) is True

    state = git_support.repository_state(root)
    assert state.has_upstream is True
    assert state.ahead == 0
    remote_subject = subprocess.run(
        ["git", "--git-dir", str(remote), "log", "-1", "--format=%s"],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    assert remote_subject.startswith(git_support.AUTOMATIC_BACKUP_PREFIX)


def test_repository_state_suggests_existing_non_origin_remote(tmp_path) -> None:
    root = _repository(tmp_path)
    _git(root, "remote", "add", "work", "ssh://git.example.test/team/vault.git")

    state = git_support.repository_state(root)

    assert state.remote_name == "work"
    assert state.remote_url == "ssh://git.example.test/team/vault.git"


def test_folder_restore_never_deletes_files_created_later(tmp_path) -> None:
    root = _repository(tmp_path)
    folder = root / "Notes"
    folder.mkdir()
    existing = folder / "Existing.md"
    existing.write_text("old\n", encoding="utf-8")
    tag, _ = git_support.save_version(root, "Before edits")

    folder_history = git_support.history(root, "Notes", versions_only=True)
    assert [entry.version_name for entry in folder_history] == ["Before edits"]

    existing.write_text("new\n", encoding="utf-8")
    newer = folder / "Newer.md"
    newer.write_text("keep me\n", encoding="utf-8")

    restored = git_support.restore_path(root, tag, "Notes")

    assert restored == [existing]
    assert existing.read_text(encoding="utf-8") == "old\n"
    assert newer.read_text(encoding="utf-8") == "keep me\n"


def test_status_parser_preserves_renames_and_unusual_names() -> None:
    output = "R  renamed.md\0old name.md\0?? new name.md\0"

    entries = git_support.parse_status(output)

    assert entries[0].path == "renamed.md"
    assert entries[0].original_path == "old name.md"
    assert entries[1].path == "new name.md"
    assert entries[1].index == "?"


def test_working_diff_renders_untracked_text_as_new_file(tmp_path) -> None:
    root = _repository(tmp_path)
    page = root / "New Page.md"
    page.write_text("first line\nsecond line\n", encoding="utf-8")

    diff, binary = git_support.working_diff(root, "New Page.md")

    assert binary is False
    assert "--- /dev/null" in diff
    assert "+++ b/New Page.md" in diff
    assert "+first line" in diff


def test_working_diff_labels_untracked_binary_file(tmp_path) -> None:
    root = _repository(tmp_path)
    attachment = root / "image.bin"
    attachment.write_bytes(b"\x00\x01\x02")

    diff, binary = git_support.working_diff(root, "image.bin")

    assert binary is True
    assert diff == "New binary file: image.bin (3 bytes)\n"


def test_working_diff_labels_deleted_tracked_binary_file(tmp_path) -> None:
    root = _repository(tmp_path)
    attachment = root / "drawing.png"
    attachment.write_bytes(b"\x89PNG\r\n\x1a\n\x00")
    git_support.save_changes(root, "Add drawing")
    attachment.unlink()

    diff, binary = git_support.working_diff(root, "drawing.png")

    assert binary is True
    assert diff == "Deleted binary file: drawing.png\n"


def test_restore_working_paths_reverts_tracked_file_without_touching_untracked(
    tmp_path,
) -> None:
    root = _repository(tmp_path)
    page = root / "Page.md"
    page.write_text("saved\n", encoding="utf-8")
    git_support.save_changes(root, "Initial")
    page.write_text("changed\n", encoding="utf-8")
    extra = root / "Extra.md"
    extra.write_text("keep\n", encoding="utf-8")

    restored = git_support.restore_working_paths(root, ["Page.md", "Extra.md"])

    assert restored == ["Page.md"]
    assert page.read_text(encoding="utf-8") == "saved\n"
    assert extra.read_text(encoding="utf-8") == "keep\n"


def test_get_updates_explains_that_local_changes_must_be_saved_first(tmp_path) -> None:
    root = _repository(tmp_path)
    (root / "Page.md").write_text("unsaved change\n", encoding="utf-8")

    with pytest.raises(git_support.GitError) as error:
        git_support.pull(root)

    message = str(error.value)
    assert "local changes" in message
    assert "Save Changes" in message
    assert "remote updates" in message
