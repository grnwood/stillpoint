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

        assert config.load_vault_remote_mode() == "git"
        assert config.load_git_publish_versions() is True
    finally:
        config.set_active_vault(None)


def test_initialize_requires_repository_at_vault_root_and_ignores_metadata(tmp_path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    git_support.initialize_repository(vault)

    assert git_support.is_exact_repository(vault)
    assert ".stillpoint/" in (vault / ".gitignore").read_text(encoding="utf-8")

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
    _git(tmp_path, "add", "-f", ".stillpoint/settings.db")
    _git(tmp_path, "commit", "-m", "tracked local state")

    git_support.initialize_repository(tmp_path)

    assert settings.read_bytes() == b"local settings"
    assert _git(tmp_path, "ls-files", ".stillpoint").stdout == ""


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
