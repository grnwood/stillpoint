from __future__ import annotations

import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QLabel, QMessageBox, QTableWidget

from sp.app import config, git_support
from sp.app.ui.git_widgets import GitDiffEditor, VaultGitPanel


def test_git_mode_adds_vault_rail_and_command_palette_actions(main_window, monkeypatch) -> None:
    monkeypatch.setattr(config, "load_vault_remote_mode", lambda default="none": "git")

    main_window._refresh_git_mode_ui()

    assert main_window.git_panel is not None
    assert main_window.left_tab_widget.tabText(
        main_window.left_tab_widget.indexOf(main_window.git_panel)
    ) == "Git"
    labels = [label for label, _action in main_window._collect_menu_actions()]
    assert "Vault / Save Changes…" in labels
    assert "Vault / Save Version…" in labels
    assert "Vault / Go to Git" in labels

    main_window.left_tab_widget.setCurrentWidget(main_window.search_tab)
    main_window._action_go_to_git.trigger()
    assert main_window.left_tab_widget.currentWidget() is main_window.git_panel


def test_non_git_mode_removes_vault_rail_and_git_commands(main_window, monkeypatch) -> None:
    mode = {"value": "git"}
    monkeypatch.setattr(
        config,
        "load_vault_remote_mode",
        lambda default="none": mode["value"],
    )
    main_window._refresh_git_mode_ui()
    assert main_window.git_panel is not None

    mode["value"] = "none"
    main_window._refresh_git_mode_ui()

    assert main_window.git_panel is None
    labels = [label for label, _action in main_window._collect_menu_actions()]
    assert "Vault / Save Version…" not in labels


def test_git_tab_temporarily_collapses_and_restores_right_rail(
    main_window, monkeypatch
) -> None:
    monkeypatch.setattr(config, "load_vault_remote_mode", lambda default="none": "git")
    main_window._set_right_panel_collapsed(False)
    main_window._refresh_git_mode_ui()

    main_window._go_to_git()

    assert not main_window._is_right_panel_expanded()
    assert main_window._git_right_rail_was_expanded is True

    # This is also the handler behind Go > Vault.
    main_window._focus_vault_tab()

    assert main_window._is_right_panel_expanded()
    assert main_window._git_right_rail_was_expanded is None


def test_git_tab_restores_an_already_collapsed_right_rail(
    main_window, monkeypatch
) -> None:
    monkeypatch.setattr(config, "load_vault_remote_mode", lambda default="none": "git")
    main_window._set_right_panel_collapsed(True)
    main_window._refresh_git_mode_ui()

    main_window._go_to_git()
    main_window.left_tab_widget.setCurrentWidget(main_window.search_tab)

    assert not main_window._is_right_panel_expanded()
    assert main_window._git_right_rail_was_expanded is None


def test_git_mode_does_not_start_homebase_watcher(main_window, monkeypatch, tmp_path) -> None:
    watched_paths: list[object] = []
    main_window.vault_root = str(tmp_path)
    monkeypatch.setattr(main_window, "_is_homebase_mode_enabled", lambda: False)
    monkeypatch.setattr(
        main_window,
        "_ensure_homebase_watcher",
        lambda path: watched_paths.append(path),
    )

    main_window._configure_homebase_sync_for_vault()

    assert watched_paths == []


def test_automatic_backup_saves_then_publishes_and_records_timestamps(
    main_window, monkeypatch, tmp_path
) -> None:
    main_window.vault_root = str(tmp_path)
    main_window._last_editor_activity = 0.0
    calls: list[str] = []
    timestamps: list[tuple[str, str]] = []
    monkeypatch.setattr(main_window, "_is_git_mode_enabled", lambda: True)
    monkeypatch.setattr(config, "load_git_auto_backup_enabled", lambda default=False: True)
    monkeypatch.setattr(config, "load_git_auto_publish_enabled", lambda default=True: True)
    monkeypatch.setattr(main_window, "_save_current_file", lambda **_kwargs: calls.append("flush"))
    monkeypatch.setattr(git_support, "save_automatic_backup", lambda _root: calls.append("save") or True)
    monkeypatch.setattr(git_support, "has_head", lambda _root: True)
    monkeypatch.setattr(
        git_support,
        "publish_automatic_backup",
        lambda _root, timeout=15: calls.append(f"publish:{timeout}") or True,
    )
    monkeypatch.setattr(
        config,
        "save_git_backup_timestamp",
        lambda key, value: timestamps.append((key, value)),
    )
    monkeypatch.setattr(main_window, "_configure_git_automatic_backup", lambda *_args: None)
    main_window.git_panel = None

    main_window._run_git_automatic_backup()

    assert calls == ["flush", "save", "publish:15"]
    assert [key for key, _value in timestamps] == ["last_backup", "last_publish"]
    assert main_window._git_auto_backup_attention == ""


def test_automatic_backup_waits_until_editor_is_idle(main_window, monkeypatch, tmp_path) -> None:
    main_window.vault_root = str(tmp_path)
    main_window._last_editor_activity = time.monotonic()
    monkeypatch.setattr(main_window, "_is_git_mode_enabled", lambda: True)
    monkeypatch.setattr(config, "load_git_auto_backup_enabled", lambda default=False: True)
    calls: list[str] = []
    monkeypatch.setattr(git_support, "save_automatic_backup", lambda _root: calls.append("save"))

    main_window._run_git_automatic_backup()

    assert calls == []
    assert main_window.git_auto_backup_timer.isActive()


def test_git_panel_reset_hover_uses_all_selected_new_files(qtbot, tmp_path) -> None:
    git_support.initialize_repository(tmp_path)
    (tmp_path / "one.bin").write_bytes(b"one")
    (tmp_path / "two.bin").write_bytes(b"two")
    panel = VaultGitPanel(tmp_path)
    qtbot.addWidget(panel)
    requested: list[list[str]] = []
    panel.resetRequested.connect(requested.append)

    file_items = {
        str(panel.changes.item(row).data(Qt.UserRole) or ""): panel.changes.item(row)
        for row in range(panel.changes.count())
        if panel.changes.item(row).data(Qt.UserRole)
    }
    file_items["one.bin"].setSelected(True)
    file_items["two.bin"].setSelected(True)
    panel.changes._show_action_for_item(file_items["one.bin"])

    first_row = panel.changes.itemWidget(file_items["one.bin"])
    status_code = first_row.findChild(QLabel, "vaultGitStatusCode")
    file_path = first_row.findChild(QLabel, "vaultGitFilePath")
    assert status_code.text() == "NEW"
    assert "Not yet included" in status_code.toolTip()
    assert file_path.text() == "one.bin"
    assert "border-bottom" in panel.changes.styleSheet()
    assert panel.changes.reset_button.isVisibleTo(panel.changes.viewport())
    assert "Trash" in panel.changes.reset_button.toolTip()
    panel.changes.reset_button.click()
    assert requested == [["one.bin", "two.bin"]]


def test_git_panel_status_codes_are_fixed_three_letter_labels() -> None:
    assert VaultGitPanel._status_info("M")[:2] == ("MOD", "Modified")
    assert VaultGitPanel._status_info("?")[:2] == ("NEW", "New")
    assert VaultGitPanel._status_info("U")[:2] == ("CNF", "Conflict")
    assert all(len(VaultGitPanel._status_info(code)[0]) == 3 for code in "MADRCTU?")


def test_git_panel_reset_hover_is_available_for_modified_files(qtbot, tmp_path) -> None:
    git_support.initialize_repository(tmp_path)
    page = tmp_path / "Page.md"
    page.write_text("saved\n", encoding="utf-8")
    monkeypatch_config = [
        ("user.name", "StillPoint Test"),
        ("user.email", "stillpoint@example.invalid"),
    ]
    import subprocess

    for key, value in monkeypatch_config:
        subprocess.run(
            ["git", "-C", str(tmp_path), "config", key, value],
            check=True,
        )
    git_support.save_changes(tmp_path, "Initial")
    page.write_text("changed\n", encoding="utf-8")
    panel = VaultGitPanel(tmp_path)
    qtbot.addWidget(panel)
    requested: list[list[str]] = []
    panel.resetRequested.connect(requested.append)
    item = next(
        panel.changes.item(row)
        for row in range(panel.changes.count())
        if panel.changes.item(row).data(Qt.UserRole) == "Page.md"
    )

    panel.changes._show_action_for_item(item)

    assert panel.changes.reset_button.isVisibleTo(panel.changes.viewport())
    assert "latest saved version" in panel.changes.reset_button.toolTip()
    panel.changes.reset_button.click()
    assert requested == [["Page.md"]]


def test_git_file_picker_keyboard_navigation_and_focus_handoff(qtbot, tmp_path) -> None:
    git_support.initialize_repository(tmp_path)
    (tmp_path / "one.md").write_text("one", encoding="utf-8")
    (tmp_path / "two.md").write_text("two", encoding="utf-8")
    panel = VaultGitPanel(tmp_path)
    qtbot.addWidget(panel)
    panel.resize(360, 500)
    panel.show()
    requests: list[tuple[str, bool]] = []
    panel.changePreviewRequested.connect(
        lambda path, focus: requests.append((path, focus))
    )
    file_items = [
        panel.changes.item(row)
        for row in range(panel.changes.count())
        if panel.changes.item(row).data(Qt.UserRole)
    ]
    panel.changes.setCurrentItem(file_items[0])
    requests.clear()

    QTest.keyClick(panel.changes, Qt.Key_Down)
    assert panel.changes.currentItem() is file_items[1]
    assert requests[-1] == (str(file_items[1].data(Qt.UserRole)), False)

    QTest.keyClick(panel.changes, Qt.Key_K)
    assert panel.changes.currentItem() is file_items[0]
    QTest.keyClick(panel.changes, Qt.Key_Return, Qt.ShiftModifier)
    assert requests[-1] == (str(file_items[0].data(Qt.UserRole)), True)


def test_git_refresh_does_not_reopen_selected_diff(qtbot, tmp_path) -> None:
    git_support.initialize_repository(tmp_path)
    (tmp_path / "Page.md").write_text("changed", encoding="utf-8")
    panel = VaultGitPanel(tmp_path)
    qtbot.addWidget(panel)
    requests: list[tuple[str, bool]] = []
    panel.changePreviewRequested.connect(
        lambda path, focus: requests.append((path, focus))
    )
    file_item = next(
        panel.changes.item(row)
        for row in range(panel.changes.count())
        if panel.changes.item(row).data(Qt.UserRole) == "Page.md"
    )
    panel.changes.setCurrentItem(file_item)
    requests.clear()

    panel.refresh()

    assert requests == []


def test_git_status_badge_uses_green_yellow_and_red_attention_model(
    main_window, monkeypatch
) -> None:
    monkeypatch.setattr(main_window, "_is_git_mode_enabled", lambda: True)
    clean = git_support.GitRepositoryState(True, True, branch="main", remote_url="remote")
    changed = git_support.GitStatusEntry("Page.md", " ", "M")
    conflict = git_support.GitStatusEntry("Page.md", "U", "U")

    main_window._update_git_status_badge(clean, [])
    assert "#2e7d32" in main_window._git_status_label.styleSheet()
    assert "Local vault is clean" in main_window._git_status_label.toolTip()

    main_window._update_git_status_badge(clean, [changed])
    assert "#edb400" in main_window._git_status_label.styleSheet()

    main_window._update_git_status_badge(clean, [conflict])
    assert "#d32f2f" in main_window._git_status_label.styleSheet()
    assert main_window._git_status_label.text() == "GIT !"


def test_reset_new_files_moves_only_untracked_files_to_trash(
    main_window, monkeypatch, tmp_path
) -> None:
    git_support.initialize_repository(tmp_path)
    attachment = tmp_path / "wrong-attachment.bin"
    attachment.write_bytes(b"wrong")
    main_window.vault_root = str(tmp_path)
    main_window.current_path = None
    moved: list[str] = []
    monkeypatch.setattr(main_window, "_is_git_mode_enabled", lambda: True)
    monkeypatch.setattr(
        "sp.app.ui.main_window.QMessageBox.question",
        lambda *_args, **_kwargs: QMessageBox.Yes,
    )
    monkeypatch.setattr(
        "sp.app.ui.main_window.QFile.moveToTrash",
        lambda path: moved.append(path) or True,
    )
    monkeypatch.setattr(main_window, "_refresh_tree", lambda: None)
    monkeypatch.setattr(main_window, "_refresh_git_mode_ui", lambda: None)

    main_window._git_reset_new_files(["wrong-attachment.bin"])

    assert moved == [str(attachment)]


def test_visual_git_diff_aligns_before_and_after_lines(qtbot) -> None:
    patch = """diff --git a/Page.md b/Page.md
--- a/Page.md
+++ b/Page.md
@@ -1,3 +1,4 @@
 same
-old wording
+new wording
+extra line
 end
"""
    dialog = GitDiffEditor("Changes · Page.md", patch)
    qtbot.addWidget(dialog)

    assert isinstance(dialog.side_by_side, QTableWidget)
    table = dialog.side_by_side
    replace_rows = [
        row
        for row in range(table.rowCount())
        if table.item(row, 1) is not None and table.item(row, 1).text() == "old wording"
    ]
    assert len(replace_rows) == 1
    replace_row = replace_rows[0]
    assert table.item(replace_row, 3).text() == "new wording"
    assert table.item(replace_row + 1, 1).text() == ""
    assert table.item(replace_row + 1, 3).text() == "extra line"
    assert dialog.views.currentWidget() is table
    line_heights = {
        table.rowHeight(row)
        for row in range(table.rowCount())
        if row not in dialog._hunk_rows
    }
    assert len(line_heights) == 1

    initial_size = table.font().pointSize()
    dialog.adjust_font_size(1)
    assert table.font().pointSize() == initial_size + 1
    assert dialog.unified.font().pointSize() == initial_size + 1
    assert getattr(dialog, "_primary_wheel_zoom_filters", None)


def test_visual_git_diff_keeps_markdown_horizontal_rules_aligned() -> None:
    patch = """diff --git a/Page.md b/Page.md
--- a/Page.md
+++ b/Page.md
@@ -1,4 +1,4 @@
-# Page
----
-Old wording
-Last line
+# Page
+---
+New wording
+Last line
"""

    rows = GitDiffEditor.side_by_side_rows(patch)
    content_rows = [row for row in rows if row.get("kind") != "hunk"]

    assert [(row["old_text"], row["new_text"]) for row in content_rows] == [
        ("# Page", "# Page"),
        ("---", "---"),
        ("Old wording", "New wording"),
        ("Last line", "Last line"),
    ]


def test_visual_git_diff_gives_binary_change_a_clear_summary(qtbot) -> None:
    dialog = GitDiffEditor(
        "New · image.png",
        "New binary file: image.png (2048 bytes)",
        binary=True,
    )
    qtbot.addWidget(dialog)

    assert isinstance(dialog.side_by_side, QLabel)
    assert "New binary file" in dialog.side_by_side.text()


def test_embedded_git_diff_navigates_changes_and_uses_readable_header_text(qtbot) -> None:
    patch = """diff --git a/Page.md b/Page.md
--- a/Page.md
+++ b/Page.md
@@ -1,2 +1,2 @@
-first old
+first new
 same
@@ -20,2 +20,2 @@
-second old
+second new
 end
"""
    viewer = GitDiffEditor("Changes · Page.md", patch)
    qtbot.addWidget(viewer)
    viewer.show()

    assert not isinstance(viewer, QDialog)
    assert viewer.change_count_label.text() == "Change 1 of 2"
    first_header = viewer.side_by_side.item(viewer._hunk_rows[0], 0)
    assert first_header.foreground().color() == viewer.palette().color(QPalette.Text)

    QTest.keyClick(viewer.side_by_side, Qt.Key_Down)
    assert viewer.change_count_label.text() == "Change 2 of 2"
    QTest.keyClick(viewer.side_by_side, Qt.Key_K, Qt.ControlModifier)
    assert viewer.change_count_label.text() == "Change 1 of 2"


def test_git_selection_replaces_main_editor_with_embedded_diff(
    main_window, monkeypatch, tmp_path
) -> None:
    main_window.vault_root = str(tmp_path)
    entry = git_support.GitStatusEntry("Page.md", " ", "M")
    patch = """--- a/Page.md
+++ b/Page.md
@@ -1 +1 @@
-old
+new
"""
    monkeypatch.setattr(git_support, "status", lambda _root: [entry])
    monkeypatch.setattr(
        git_support,
        "working_diff",
        lambda _root, _path: (patch, False),
    )

    main_window._show_git_working_diff("Page.md", focus_diff=False)

    viewer = main_window._git_diff_editor
    assert viewer is not None
    assert isinstance(viewer, GitDiffEditor)
    assert not isinstance(viewer, QDialog)
    assert main_window._editor_view_stack.currentWidget() is viewer
    assert viewer.property("gitDiffPath") == "Page.md"

    main_window._close_git_diff_editor()
    assert main_window._editor_view_stack.currentWidget() is main_window.editor


def test_git_diff_hides_toc_and_markdown_editor_restores_it(
    main_window, monkeypatch, tmp_path
) -> None:
    main_window.vault_root = str(tmp_path)
    main_window._toc_headings = [{"text": "One"}, {"text": "Two"}]
    main_window.editor.verticalScrollBar().setRange(0, 10)
    monkeypatch.setattr(config, "load_toc_widget_enabled", lambda: True)
    monkeypatch.setattr(git_support, "status", lambda _root: [])
    monkeypatch.setattr(
        git_support,
        "working_diff",
        lambda _root, _path: ("@@ -1 +1 @@\n-old\n+new\n", False),
    )
    main_window._update_toc_visibility(force=True)
    assert not main_window.toc_widget.isHidden()

    main_window._show_git_working_diff("Page.md")

    assert main_window.toc_widget.isHidden()
    main_window._close_git_diff_editor()
    assert not main_window.toc_widget.isHidden()
