from pathlib import Path
import subprocess

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from sp.app import git_support
from sp.app.ui.git_widgets import GitCommitViewer, GitLogPanel


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout.strip()


def repository(root):
    root.mkdir(exist_ok=True)
    git_support.initialize_repository(root)
    git(root, "config", "user.name", "History Test")
    git(root, "config", "user.email", "test@example.invalid")
    return root


def test_recent_commits_scopes_paths_and_handles_empty_repository(tmp_path):
    root = repository(tmp_path)
    assert git_support.recent_commits(root) == []
    (root / "First.md").write_text("first\n")
    git_support.save_changes(root, "First commit")
    (root / "Second.md").write_text("second\n")
    git_support.save_changes(root, "Second commit")
    assert [entry.subject for entry in git_support.recent_commits(root)] == ["Second commit", "First commit"]
    assert [entry.subject for entry in git_support.recent_commits(root, "First.md")] == ["First commit"]


def test_log_selection_and_refresh_do_not_reopen_diff(qtbot, tmp_path, monkeypatch):
    entry = git_support.GitHistoryEntry("a" * 40, "2026-11-12T12:00:00Z", "Test", "Save page")
    scopes = []
    monkeypatch.setattr(git_support, "recent_commits", lambda root, path: scopes.append(path) or [entry])
    panel = GitLogPanel(tmp_path)
    qtbot.addWidget(panel)
    opened = []
    panel.commitActivated.connect(lambda *args: opened.append(args))
    panel.show_history("Notes/Page.md")
    assert scopes == ["Notes/Page.md"]
    panel.entries.setCurrentRow(0)
    assert opened == [(entry, "Notes/Page.md")]
    panel.refresh()
    assert len(opened) == 1
    assert panel.entries.currentRow() == 0
    QTest.qWait(1)
    panel.entries.itemClicked.emit(panel.entries.currentItem())
    assert len(opened) == 2


def test_all_history_toggles_back_to_latest_file_or_folder_scope(qtbot, tmp_path, monkeypatch):
    scopes = []
    monkeypatch.setattr(git_support, "recent_commits", lambda root, path: scopes.append(path) or [])
    panel = GitLogPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.show_history("Notes/Page.md")
    assert not panel.all_button.isChecked()
    panel.all_button.click()
    assert panel.relative_path == ""
    assert panel.all_button.isChecked()
    panel.refresh()
    panel.all_button.click()
    assert panel.relative_path == "Notes/Page.md"
    panel.show_history("Projects")
    panel.all_button.click()
    panel.all_button.click()
    assert panel.relative_path == "Projects"
    assert scopes == ["Notes/Page.md", "", "", "Notes/Page.md", "Projects", "", "Projects"]
    panel.show_history("")
    assert not panel.all_button.isEnabled()


def test_commit_viewer_selects_files_without_editing_working_copy(qtbot, tmp_path):
    root = repository(tmp_path)
    (root / "One.md").write_text("one\n")
    (root / "Two.md").write_text("two\n")
    git_support.save_changes(root, "Save two pages")
    entry = git_support.recent_commits(root)[0]
    viewer = GitCommitViewer(root, entry, "Two.md")
    qtbot.addWidget(viewer)
    assert "Two.md" in viewer.files.currentText()
    assert "+two" in viewer.viewer.diff_text
    index = next(index for index in range(viewer.files.count()) if "One.md" in viewer.files.itemText(index))
    viewer.files.setCurrentIndex(index)
    assert "+one" in viewer.viewer.diff_text
    assert git_support.status(root) == []


def test_folder_history_opens_in_editor_and_preserves_dirty_tab(qtbot, tmp_path, monkeypatch):
    from sp.app.folder_navigator.window import Window
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    root = repository(tmp_path / "vault")
    page = root / "Page.md"
    page.write_text("saved\n")
    git_support.save_changes(root, "Save page")
    window = Window(root)
    qtbot.addWidget(window)
    window.open_file(page, pinned=True)
    original = window.active_tab()
    original.editor.insertPlainText("local edit")
    window._show_git_log(page)
    assert window.rail.currentWidget() is window.git_page
    assert window.git_log_panel.entries.count() == 1
    window.git_log_panel.entries.setCurrentRow(0)
    assert window.active_tab().property("gitCommitHistory")
    assert "Save page" in window.active_tab().viewer.entry.subject
    assert "local edit" in original.editor.toPlainText()
    assert "Git Log History" in [action.text() for action in window._create_git_context_menu().actions()]
    original.editor.document().setModified(False)
    original.clean_text = original.editor.to_markdown()


def test_main_history_survives_status_refresh(main_window, monkeypatch):
    from sp.app.ui.git_widgets import VaultGitPanel
    monkeypatch.setattr(git_support, "repository_state", lambda root: git_support.GitRepositoryState(True, True))
    monkeypatch.setattr(git_support, "status", lambda root: [])
    entry = git_support.GitHistoryEntry("c" * 40, "", "Test", "Saved commit")
    monkeypatch.setattr(git_support, "recent_commits", lambda *args: [entry])
    monkeypatch.setattr(git_support, "commit_files", lambda *args: [git_support.GitCommitFile("Page.md", "M", 1, 1)])
    monkeypatch.setattr(git_support, "commit_diff", lambda *args: "@@ -1 +1 @@\n-old\n+new")
    monkeypatch.setattr(main_window, "_is_git_mode_enabled", lambda: True)
    panel = VaultGitPanel(main_window.vault_root, main_window)
    main_window.git_panel = panel
    panel.log_panel.commitActivated.connect(main_window._show_git_commit)
    panel.log_panel.show_history()
    panel.log_panel.entries.setCurrentRow(0)
    viewer = main_window._git_diff_editor
    assert main_window._editor_view_stack.currentWidget() is viewer
    main_window._update_git_status_badge(git_support.GitRepositoryState(True, True), [])
    assert main_window._git_diff_editor is viewer


def test_push_dialog_lists_files_from_confirmed_remote_range(qtbot, tmp_path):
    from sp.app.folder_navigator.window import GitCommandDialog
    root = repository(tmp_path / "vault")
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    page = root / "Published page.md"
    page.write_text("before\n")
    git_support.save_changes(root, "Initial")
    git(root, "remote", "add", "origin", str(remote))
    git(root, "push", "--set-upstream", "origin", "HEAD")
    page.write_text("after\n")
    git_support.save_changes(root, "Publish page update")
    dialog = GitCommandDialog(root, ["push"], "Git Push")
    qtbot.addWidget(dialog)
    for _ in range(200):
        if dialog.ok_button.isEnabled():
            break
        QTest.qWait(20)
    assert dialog.ok_button.isEnabled()
    output = dialog.output.toPlainText()
    assert "\x1b" not in output
    assert "\x1b" not in dialog._git_output
    assert "Published commits and files" in output
    assert "Published page.md" in output
    assert "Publish page update" in output
    assert "Initial" not in output
