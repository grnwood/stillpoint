from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt, QTimer

from sp.attachment_ops import rename_attachment
from sp.app.ui.attachments_panel import AttachmentsPanel, QInputDialog, QMessageBox, QMenu


@pytest.fixture
def files(tmp_path):
    folder = tmp_path / "Page"
    folder.mkdir()
    page = folder / "Page.md"
    page.write_text("# Page")
    old = folder / "old.pdf"
    old.write_bytes(b"original attachment")
    return tmp_path, page, old


def test_local_rename_refreshes_and_selects_file(qtbot, files, monkeypatch):
    root, page, old = files
    panel = AttachmentsPanel()
    panel.set_vault_root(str(root))
    qtbot.addWidget(panel)
    panel.set_page(page)
    panel.attachments_list.setCurrentRow(0)
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **kw: ("new.pdf", True))
    changes = []
    panel.attachmentsModified.connect(changes.append)
    panel._rename_selected_attachment()
    assert not old.exists()
    assert old.with_name("new.pdf").read_bytes() == b"original attachment"
    assert page.read_text() == "# Page"
    assert panel.attachments_list.currentItem().text() == "new.pdf"
    assert changes == ["attachment renamed"]


@pytest.mark.parametrize("name", ["exists.pdf", "../outside.pdf", "", "  ", "Page.md"])
def test_rename_error_preserves_files(qtbot, files, monkeypatch, name):
    root, page, old = files
    existing = old.with_name("exists.pdf")
    existing.write_bytes(b"keep this")
    panel = AttachmentsPanel()
    panel.set_vault_root(str(root))
    qtbot.addWidget(panel)
    panel.set_page(page)
    item = panel.attachments_list.findItems("old.pdf", Qt.MatchExactly)[0]
    panel.attachments_list.setCurrentItem(item)
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **kw: (name, True))
    errors = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: errors.append(args[-1]))
    panel._rename_selected_attachment()
    assert len(errors) == 1
    assert old.read_bytes() == b"original attachment"
    assert existing.read_bytes() == b"keep this"
    assert page.read_text() == "# Page"


@pytest.mark.parametrize("selected_count", [0, 1, 2])
def test_context_menu_rename_requires_one_selection(qtbot, files, monkeypatch, selected_count):
    root, page, old = files
    old.with_name("second.pdf").write_bytes(b"second")
    panel = AttachmentsPanel()
    qtbot.addWidget(panel)
    panel.set_page(page)
    for row in range(selected_count):
        panel.attachments_list.item(row).setSelected(True)
    captured = []
    def close_menu():
        menu = panel.findChildren(QMenu)[-1]
        captured.append(menu)
        menu.close()

    QTimer.singleShot(0, close_menu)
    panel._on_attachments_context_menu(panel.attachments_list.rect().center())
    action = next(a for a in captured[0].actions() if a.text() == "Rename…")
    assert action.isEnabled() == (selected_count == 1)


def test_remote_rename_uses_api_and_refreshes(qtbot, tmp_path, monkeypatch):
    entry = {"attachment_path": "/Page/old.pdf"}
    calls = []

    class Client:
        def get(self, path, params):
            return SimpleNamespace(status_code=200, raise_for_status=lambda: None,
                                   json=lambda: {"attachments": [entry]})

        def post(self, path, json):
            calls.append((path, json))
            entry["attachment_path"] = "/Page/new.pdf"
            return SimpleNamespace(status_code=200, raise_for_status=lambda: None)

    panel = AttachmentsPanel(api_client=Client())
    panel.set_remote_mode(True, "https://example.test")
    panel.set_remote_vault_root("/vault")
    monkeypatch.setattr(panel, "_get_remote_cache_root", lambda: tmp_path)
    qtbot.addWidget(panel)
    panel.set_page(Path("/vault/Page/Page.md"))
    panel.attachments_list.setCurrentRow(0)
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **kw: ("new.pdf", True))
    panel._rename_selected_attachment()
    assert calls == [("/files/rename", {"page_path": "/Page/Page.md",
                                       "attachment_path": "/Page/old.pdf", "name": "new.pdf"})]
    assert panel.attachments_list.currentItem().data(Qt.UserRole)["path"] == "/Page/new.pdf"


def test_api_rename_updates_attachment_index(files, monkeypatch):
    from sp.server import api
    root, page, old = files
    monkeypatch.setattr(api, "_get_vault_root", lambda: root)
    removed, added = [], []
    monkeypatch.setattr(api.config, "delete_attachment_entry", lambda path: removed.append(path))
    monkeypatch.setattr(api.config, "upsert_attachment_entry", lambda *args: added.append(args))
    payload = api.AttachmentRenamePayload(page_path="/Page/Page.md",
                                         attachment_path="/Page/old.pdf", name="new.pdf")
    response = api.rename_file_attachment(payload)
    assert response == {"ok": True, "attachment_path": "/Page/new.pdf"}
    assert removed == ["/Page/old.pdf"]
    assert added == [("/Page/Page.md", "/Page/new.pdf", str(old.with_name("new.pdf")))]
    assert not old.exists()


def test_rename_cannot_target_another_pages_attachment(files):
    root, page, old = files
    with pytest.raises(ValueError):
        rename_attachment(root, "/Other/Other.md", "/Page/old.pdf", "new.pdf")
    assert old.exists()
