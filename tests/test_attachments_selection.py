from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from sp.app.ui.attachments_panel import AttachmentsPanel


def click(panel, row, modifiers=Qt.NoModifier):
    listing = panel.attachments_list
    QTest.mouseClick(listing.viewport(), Qt.LeftButton, modifiers,
                     listing.visualItemRect(listing.item(row)).center())


@pytest.mark.parametrize("selection", ["range", "individual"])
@pytest.mark.parametrize("delete_with", ["keyboard", "button"])
def test_delete_multiple_local_attachments_keeps_unselected_files(
    qtbot, tmp_path, selection, delete_with,
):
    page = tmp_path / "Page.md"
    page.write_text("# Page")
    for name in ("a.pdf", "b.pdf", "c.pdf"):
        (tmp_path / name).write_bytes(b"attachment")
    panel = AttachmentsPanel()
    panel.resize(400, 300)
    qtbot.addWidget(panel)
    panel.set_page(page)
    click(panel, 0)
    if selection == "range":
        click(panel, 1, Qt.ShiftModifier)
        expected = {"c.pdf"}
    else:
        click(panel, 2, Qt.ControlModifier)
        expected = {"b.pdf"}
    assert len(panel.attachments_list.selectedItems()) == 2
    assert panel.remove_button.isEnabled()
    changes = []
    panel.attachmentsModified.connect(changes.append)
    if delete_with == "keyboard":
        QTest.keyClick(panel.attachments_list, Qt.Key_Delete)
    else:
        QTest.mouseClick(panel.remove_button, Qt.LeftButton)
    assert {p.name for p in tmp_path.iterdir()} == expected | {"Page.md"}
    assert page.read_text() == "# Page"
    assert panel.attachments_list.count() == 1
    assert not panel.remove_button.isEnabled()
    assert changes == ["attachments removed"]


def test_multiselect_remote_delete_sends_all_selected_paths(qtbot, tmp_path, monkeypatch):
    entries = [{"attachment_path": f"/Page/{name}.pdf"} for name in ("a", "b", "c")]
    deleted = []

    class Client:
        def get(self, path, params):
            return SimpleNamespace(status_code=200, raise_for_status=lambda: None,
                                   json=lambda: {"attachments": list(entries)})

        def post(self, path, json):
            assert path == "/files/delete"
            deleted.extend(json["paths"])
            entries[:] = [e for e in entries if e["attachment_path"] not in deleted]
            return SimpleNamespace(status_code=200, raise_for_status=lambda: None)

    panel = AttachmentsPanel(api_client=Client())
    panel.set_remote_mode(True, "https://example.test")
    panel.set_remote_vault_root("/vault")
    monkeypatch.setattr(panel, "_get_remote_cache_root", lambda: tmp_path)
    panel.resize(400, 300)
    qtbot.addWidget(panel)
    panel.set_page(Path("/vault/Page/Page.md"))
    click(panel, 0)
    click(panel, 2, Qt.ControlModifier)
    assert len(panel.attachments_list.selectedItems()) == 2
    QTest.keyClick(panel.attachments_list, Qt.Key_Delete)
    assert deleted == ["/Page/a.pdf", "/Page/c.pdf"]
    assert panel.attachments_list.count() == 1


def test_macos_backspace_deletes_selected_attachment(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr("sp.app.ui.attachments_panel.sys.platform", "darwin")
    page = tmp_path / "Page.md"
    page.write_text("# Page")
    attachment = tmp_path / "a.pdf"
    attachment.write_bytes(b"attachment")
    panel = AttachmentsPanel()
    qtbot.addWidget(panel)
    panel.set_page(page)
    click(panel, 0)
    QTest.keyClick(panel.attachments_list, Qt.Key_Backspace)
    assert not attachment.exists()
    assert page.exists()
