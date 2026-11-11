import hashlib
from types import MethodType

import httpx
import pytest
from PySide6.QtCore import QEvent, QTimer, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QMainWindow

from sp.app.task_mutations import rewrite_task_line
from sp.app.ui.main_window import MainWindow
from sp.app.ui.markdown_editor import MarkdownEditor
from sp.app.ui.page_editor_window import PageEditorWindow


def digest(content):
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class Vault:
    def __init__(self):
        self.content = "# Page\n\nIntroduction\n- [ ] Task\n"
        self.writes = []
        self.undo_content = None

    def post(self, path, json, headers=None):
        status = 200
        if path == "/api/file/read":
            payload = {"content": self.content, "content_sha256": digest(self.content)}
        elif path == "/api/file/write":
            if headers and headers["If-Match"] != "sha256:" + digest(self.content):
                status = 409
                payload = {"detail": {"current_content": self.content, "current_content_sha256": digest(self.content)}}
            else:
                self.content = json["content"]
                self.writes.append(self.content)
                payload = {"ok": True, "content_sha256": digest(self.content)}
        elif path == "/api/tasks/mutate":
            self.undo_content = self.content
            lines = self.content.splitlines(keepends=True)
            line = json["targets"][0]["line"] - 1
            lines[line] = rewrite_task_line(lines[line], status=json["status"])
            self.content = "".join(lines)
            payload = {"undo_id": "task-undo", "paths": ["/Page/Page.md"]}
        elif path == "/api/tasks/undo/task-undo":
            self.content = self.undo_content
            payload = {"paths": ["/Page/Page.md"]}
        else:
            raise AssertionError(path)
        return httpx.Response(status, request=httpx.Request("POST", "http://vault" + path), json=payload)

    def close(self):
        pass


@pytest.fixture
def editors(qtbot, monkeypatch):
    vault = Vault()
    main = QMainWindow()
    main.editor = MarkdownEditor()
    main.setCentralWidget(main.editor)
    main.editor.set_markdown(vault.content)
    main._last_saved_content = main.editor.to_markdown()
    main.editor.document().setModified(False)
    main.current_path = "/Page/Page.md"
    main._mode_window = None
    main._page_revisions = {main.current_path: {"content_sha256": digest(vault.content)}}
    main._page_windows = []
    main._dirty_flag = False
    main._suspend_autosave = False
    main._remote_mode = False
    main.autosave_timer = QTimer(main)
    main.http = vault
    main._update_dirty_indicator = lambda: None
    main._normalize_editor_path = lambda path: path
    for name in ("_refresh_current_saved_state", "_update_page_revision", "_if_match_headers", "_on_page_editor_request"):
        setattr(main, name, MethodType(getattr(MainWindow, name), main))
    qtbot.addWidget(main)

    load = PageEditorWindow._load_content
    monkeypatch.setattr(PageEditorWindow, "_load_content", lambda self: None)
    popup = PageEditorWindow(
        api_base="http://vault", vault_root="/tmp/test-vault", page_path=main.current_path,
        read_only=False, open_in_main_callback=main._on_page_editor_request,
    )
    popup.http.close()
    popup.http = vault
    popup._load_content = MethodType(load, popup)
    qtbot.addWidget(popup)
    main._page_windows.append(popup)
    popup._load_content()
    yield main, popup, vault
    popup._read_only = True


def refocus(popup):
    QApplication.sendEvent(popup, QEvent(QEvent.WindowActivate))
    QApplication.processEvents()


@pytest.mark.parametrize("content", ["# Page\n\n☐ Task", "Heading\n- [ ] Task\n", "Just text"])
def test_loaded_render_normalization_is_clean_and_save_indicator_is_green(editors, content):
    main, popup, vault = editors
    vault.content = content
    popup._load_content()
    assert not popup._is_dirty()
    assert popup._dirty_status_label.toolTip() == "All changes saved"
    popup._save_current_file(auto=True)
    assert not vault.writes


def test_main_task_change_refreshes_clean_detached_buffer_on_refocus(editors):
    main, popup, vault = editors
    cursor = popup.editor.textCursor()
    cursor.setPosition(8)
    popup.editor.setTextCursor(cursor)
    vault.content = vault.content.replace("- [ ]", "- [x]")
    refocus(popup)
    assert "- [x] Task" in popup.editor.to_markdown()
    assert popup.editor.textCursor().position() == 8
    assert not popup._is_dirty()
    assert popup._dirty_status_label.toolTip() == "All changes saved"
    popup.editor.moveCursor(QTextCursor.End)
    popup.editor.insertPlainText("New detached note\n")
    popup._save_current_file(auto=True)
    assert "- [x] Task" in vault.content
    assert "New detached note" in vault.content
    assert not popup._is_dirty()


def test_detached_task_change_refreshes_clean_main_buffer_before_next_edit(editors):
    main, popup, vault = editors
    popup._task_context_menu(3, popup.pos()).actions()[0].trigger()
    assert "- [x] Task" in vault.content
    main._refresh_current_saved_state()
    assert "- [x] Task" in main.editor.to_markdown()
    assert not main._dirty_flag
    main.editor.moveCursor(QTextCursor.End)
    main.editor.insertPlainText("Main note\n")
    response = vault.post("/api/file/write", {"content": main.editor.to_markdown()}, headers=main._if_match_headers(main.current_path))
    assert response.status_code == 200
    assert "- [x] Task" in vault.content
    assert "Main note" in vault.content


def test_dirty_detached_buffer_and_new_task_state_cannot_overwrite_each_other(editors):
    main, popup, vault = editors
    popup.editor.moveCursor(QTextCursor.End)
    popup.editor.insertPlainText("Unsaved detached note\n")
    local = popup.editor.to_markdown()
    baseline = popup._saved_content_sha256
    vault.content = vault.content.replace("- [ ]", "- [x]")
    refocus(popup)
    assert popup.editor.to_markdown() == local
    assert popup._saved_content_sha256 == baseline
    popup._save_current_file(auto=True)
    assert not vault.writes
    assert "- [x] Task" in vault.content
    assert popup._is_dirty()
    assert "Save paused" in popup.statusBar().currentMessage()


def test_detached_save_notification_preserves_dirty_main_buffer(editors):
    main, popup, vault = editors
    main.editor.moveCursor(QTextCursor.End)
    main.editor.insertPlainText("Unsaved main note\n")
    local = main.editor.to_markdown()
    old_hash = main._page_revisions[main.current_path]["content_sha256"]
    popup.editor.moveCursor(QTextCursor.End)
    popup.editor.insertPlainText("Saved detached note\n")
    popup._save_current_file(auto=True)
    main._refresh_current_saved_state()
    assert main.editor.to_markdown() == local
    assert main._page_revisions[main.current_path]["content_sha256"] == old_hash
    response = vault.post("/api/file/write", {"content": local}, headers=main._if_match_headers(main.current_path))
    assert response.status_code == 409
    assert "Saved detached note" in vault.content


def test_explicit_save_can_merge_local_note_with_saved_task_change(editors, monkeypatch):
    main, popup, vault = editors
    popup.editor.moveCursor(QTextCursor.End)
    popup.editor.insertPlainText("Unsaved note\n")
    vault.content = vault.content.replace("- [ ]", "- [x]")
    merged = vault.content + "Unsaved note\n"

    class Merge:
        def __init__(self, local, remote, path, parent):
            assert "Unsaved note" in local
            assert "- [x] Task" in remote

        def setWindowModality(self, modality):
            pass

        def exec(self):
            return QDialog.Accepted

        def merged_text(self):
            return merged

    monkeypatch.setattr("sp.app.ui.merge_conflict_dialog.MergeConflictDialog", Merge)
    popup._save_current_file(auto=False)
    assert vault.content == merged
    assert not popup._is_dirty()
    assert popup._dirty_status_label.toolTip() == "All changes saved"


def test_cancelled_conflict_does_not_close_dirty_editor(editors, monkeypatch):
    main, popup, vault = editors
    popup.editor.moveCursor(QTextCursor.End)
    popup.editor.insertPlainText("Keep my unsaved note\n")
    vault.content = vault.content.replace("- [ ]", "- [x]")

    class Merge:
        def __init__(self, *args, **kwargs):
            pass

        def setWindowModality(self, modality):
            pass

        def exec(self):
            return QDialog.Rejected

    monkeypatch.setattr("sp.app.ui.merge_conflict_dialog.MergeConflictDialog", Merge)
    popup.close()
    assert popup.isVisible()
    assert popup._is_dirty()
    assert not popup.property("_sp_closing")
    assert "Keep my unsaved note" in popup.editor.to_markdown()
    assert not vault.writes


def test_task_undo_remembers_exact_saved_version_before_next_save(editors):
    main, popup, vault = editors
    original = "# Page\n- [ ] @phone Task !!"
    vault.content = original
    popup._load_content()
    popup._task_context_menu(1, popup.pos()).actions()[0].trigger()
    popup._undo_task_action()
    assert vault.content == original
    assert popup._saved_content_sha256 == digest(original)
    assert not popup._is_dirty()
    popup.editor.moveCursor(QTextCursor.End)
    popup.editor.insertPlainText("\nNew note\n")
    popup._save_current_file(auto=True)
    assert "New note" in vault.content
    assert not popup._is_dirty()


def test_save_hotkey_triggers_toolbar_action_and_persists_dirty_page(editors):
    main, popup, vault = editors
    popup.activateWindow()
    popup.editor.setFocus()
    QApplication.processEvents()
    popup.editor.moveCursor(QTextCursor.End)
    popup.editor.insertPlainText("Saved with the keyboard\n")
    assert popup._is_dirty()
    actions = []
    popup._save_action.triggered.connect(lambda checked=False: actions.append("save"))
    QTest.keyClick(popup.editor, Qt.Key_S, Qt.ControlModifier)
    assert actions == ["save"]
    assert len(vault.writes) == 1
    assert "Saved with the keyboard" in vault.content
    assert not popup._is_dirty()
    assert popup._dirty_status_label.toolTip() == "All changes saved"
