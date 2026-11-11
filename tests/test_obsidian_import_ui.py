"""Exercise the desktop import flow with real Qt widgets and temporary vaults."""
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from PySide6.QtWidgets import QMainWindow, QMessageBox

from sp.app.ui.main_window import MainWindow
from sp.app.ui.markdown_editor import IMAGE_PATTERN, MarkdownEditor
from sp.server.adapters.files import write_file


class ImportWindow(QMainWindow):
    _import_obsidian_vault = MainWindow._import_obsidian_vault

    def __init__(self, root):
        super().__init__()
        self.vault_root = str(root)
        self.refreshed = False
        self.reindexed = False
        self.alerts = []

    def _require_local_mode(self, label):
        return True

    def _ensure_writable(self, label):
        return True

    def _prompt_import_target_folder(self):
        return "/Imported"

    def _log_write(self, *args, **kwargs):
        pass

    def _populate_vault_tree(self):
        self.refreshed = True

    def _reindex_vault(self, show_progress=False):
        self.reindexed = show_progress

    def _alert(self, message):
        self.alerts.append(message)


@pytest.fixture
def flow(qapp, tmp_path, monkeypatch):
    root, source = tmp_path / "stillpoint", tmp_path / "obsidian"
    root.mkdir()
    source.mkdir()
    (source / "Note.md").write_text("![[image.png]]", encoding="utf-8")
    (source / "image.png").write_bytes(b"image bytes")
    window = ImportWindow(root)
    monkeypatch.setattr("sp.app.ui.main_window.config.has_active_vault", lambda: True)
    monkeypatch.setattr("sp.app.ui.main_window.QFileDialog.getExistingDirectory", lambda *args: str(source))
    messages = []
    replies = [QMessageBox.Ok, QMessageBox.No]

    def dialog_exec(dialog):
        messages.append((dialog.windowTitle(), dialog.text(), dialog.detailedText()))
        return replies.pop(0)

    monkeypatch.setattr(QMessageBox, "exec", dialog_exec)
    calls = []

    def post(url, json=None, data=None, files=None):
        calls.append(url)
        if url == "/api/file/write":
            write_file(root, json["path"], json["content"])
        elif url == "/files/attach":
            _, (name, handle, _) = files[0]
            page = root / data["page_path"].lstrip("/")
            (page.parent / name).write_bytes(handle.read())
        else:
            raise AssertionError(url)
        return httpx.Response(200, request=httpx.Request("POST", "http://test" + url))

    window.http = SimpleNamespace(post=post)
    yield SimpleNamespace(window=window, root=root, source=source, messages=messages,
                          replies=replies, calls=calls, post=post)
    window.close()


def test_desktop_sample_vault_flow(flow, monkeypatch):
    source = Path(__file__).parents[1] / "dev-assets/specs/obsidian-sample/Sample Vault"
    monkeypatch.setattr("sp.app.ui.main_window.QFileDialog.getExistingDirectory", lambda *args: str(source))
    flow.replies[-1] = QMessageBox.Yes
    flow.window._import_obsidian_vault()
    imported = flow.root / "Imported"
    assert (imported / "MainNote/MainNote.md").is_file()
    assert (imported / "New Folder/New Note/New Note.md").is_file()
    for name, owner in [("Pasted image 20251216231932.png", "MainNote"),
                        ("Pasted image 20251216232514.png", "New Folder/New Note")]:
        assert (imported / owner / name.replace(" ", "_")).read_bytes() == (source / name).read_bytes()
    assert flow.window.refreshed
    assert flow.window.reindexed
    assert "Imported 2/2 page(s) and 2/2 attachment copies" in flow.messages[-1][1]
    assert not flow.window.alerts
    # Verify assigned paths can actually load in StillPoint's inline editor,
    # rather than merely checking that the copied files exist.
    editor = MarkdownEditor()
    try:
        for owner in ("MainNote", "New Folder/New Note"):
            page = imported / owner / (Path(owner).name + ".md")
            editor.set_context(str(flow.root), "/" + page.relative_to(flow.root).as_posix())
            local_images = [m for m in IMAGE_PATTERN.finditer(page.read_text())
                            if m["path"].startswith("./")]
            assert len(local_images) == 1
            match = local_images[0]
            assert editor._create_image_format(match["path"], match["alt"], None) is not None
    finally:
        editor.close()


def test_preview_cancel_makes_no_writes(flow):
    flow.replies[:] = [QMessageBox.Cancel]
    flow.window._import_obsidian_vault()
    assert not flow.calls
    assert not list(flow.root.iterdir())


def test_source_cancel_makes_no_writes(flow, monkeypatch):
    monkeypatch.setattr("sp.app.ui.main_window.QFileDialog.getExistingDirectory", lambda *args: "")
    flow.window._import_obsidian_vault()
    assert not flow.messages
    assert not flow.calls


def test_empty_source_reports_no_importable_notes(flow):
    (flow.source / "Note.md").unlink()
    flow.window._import_obsidian_vault()
    assert "0 page(s)" in flow.messages[0][1]
    assert not flow.calls


def test_upload_failure_reports_retained_page_and_failed_copy(flow):
    def post(url, **kwargs):
        if url == "/files/attach":
            raise OSError("upload failed")
        return flow.post(url, **kwargs)

    flow.window.http.post = post
    flow.window._import_obsidian_vault()
    assert (flow.root / "Imported/Note/Note.md").exists()
    assert flow.window.refreshed
    assert "Failed operations: 1" in flow.messages[-1][1]
    assert "Imported 1/1 page(s) and 0/1 attachment copies" in flow.messages[-1][1]
    assert "upload failed" in flow.messages[-1][2]


def test_existing_page_is_skipped(flow):
    page = flow.root / "Imported/Note/Note.txt"
    page.parent.mkdir(parents=True)
    page.write_text("existing", encoding="utf-8")
    flow.window._import_obsidian_vault()
    assert not flow.calls
    assert "Skipped pages: 1" in flow.messages[0][1]
    assert page.read_text() == "existing"


def test_file_created_during_preview_is_not_overwritten(flow, monkeypatch):
    def dialog_exec(dialog):
        flow.messages.append((dialog.windowTitle(), dialog.text(), dialog.detailedText()))
        if len(flow.messages) == 1:
            page = flow.root / "Imported/Note/Note.md"
            page.parent.mkdir(parents=True)
            page.write_text("new content", encoding="utf-8")
            return QMessageBox.Ok
        return QMessageBox.No

    monkeypatch.setattr(QMessageBox, "exec", dialog_exec)
    flow.window._import_obsidian_vault()
    assert not flow.calls
    assert (flow.root / "Imported/Note/Note.md").read_text() == "new content"
    assert "Destination appeared after planning" in flow.messages[-1][2]


def test_remote_and_readonly_guards(flow, monkeypatch):
    monkeypatch.setattr(flow.window, "_require_local_mode", lambda label: False)
    flow.window._import_obsidian_vault()
    monkeypatch.setattr(flow.window, "_require_local_mode", lambda label: True)
    monkeypatch.setattr(flow.window, "_ensure_writable", lambda label: False)
    flow.window._import_obsidian_vault()
    assert not flow.messages
    assert not flow.calls


def test_menu_action_tracks_remote_mode(main_window):
    action = main_window._action_obsidian_import
    assert action.text() == "Obsidian Vault…"
    main_window._remote_mode = True
    main_window._apply_remote_mode_ui()
    assert not action.isEnabled()
    main_window._remote_mode = False
    main_window._apply_remote_mode_ui()
    assert action.isEnabled()


def test_write_failure_reports_no_imported_pages(flow):
    def post(url, **kwargs):
        raise OSError("write failed")

    flow.window.http.post = post
    flow.window._import_obsidian_vault()
    assert not flow.window.refreshed
    assert "Imported 0/1 page(s) and 0/1 attachment copies" in flow.messages[-1][1]
    assert "write failed" in flow.messages[-1][2]


def test_planning_failure_never_starts_writes(flow):
    (flow.source / "Note.md").write_bytes(b"\xff")
    flow.window._import_obsidian_vault()
    assert flow.window.alerts
    assert not flow.calls
    assert not flow.messages
