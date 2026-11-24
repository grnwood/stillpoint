from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from sp.app.ui.attachments_panel import AttachmentsPanel


def visible_names(panel):
    listing = panel.attachments_list
    return [listing.item(row).text().split(" (", 1)[0] for row in range(listing.count())
            if not listing.item(row).isHidden()]


def test_dot_files_hidden_by_default_and_toggle_does_not_delete_them(qtbot, tmp_path):
    page = tmp_path / "Page.md"
    page.write_text("# Page")
    hidden = tmp_path / ".DS_Store"
    hidden.write_bytes(b"finder metadata")
    (tmp_path / "diagram.puml").write_text("@startuml\n@enduml")
    panel = AttachmentsPanel()
    qtbot.addWidget(panel)
    counts, modifications = [], []
    panel.countReady.connect(lambda page, count: counts.append(count))
    panel.attachmentsModified.connect(modifications.append)
    panel.set_page(page)
    assert visible_names(panel) == ["diagram.puml"]
    assert counts[-1] == 1
    assert not panel.hidden_files_button.isChecked()
    assert not panel.hidden_files_button.icon().isNull()
    closed_icon = panel.hidden_files_button.icon().cacheKey()
    QTest.mouseClick(panel.hidden_files_button, Qt.LeftButton)
    assert visible_names(panel) == [".DS_Store", "diagram.puml"]
    assert counts[-1] == 2
    assert panel.hidden_files_button.icon().cacheKey() != closed_icon
    item = panel.attachments_list.findItems(".DS_Store", Qt.MatchExactly)[0]
    panel.attachments_list.setCurrentItem(item)
    QTest.mouseClick(panel.hidden_files_button, Qt.LeftButton)
    assert visible_names(panel) == ["diagram.puml"]
    assert panel.attachments_list.selectedItems() == []
    assert not panel.remove_button.isEnabled()
    panel._remove_selected_attachments()
    assert hidden.read_bytes() == b"finder metadata"
    assert modifications == []
    assert counts[-1] == 1


def test_remote_dot_files_follow_same_filter_without_mutation_requests(qtbot, tmp_path, monkeypatch):
    entries = [{"attachment_path": "/Page/.secret"}, {"attachment_path": "/Page/notes.pdf"}]
    requests = []

    class Client:
        def get(self, path, params):
            requests.append(path)
            return SimpleNamespace(status_code=200, raise_for_status=lambda: None,
                                   json=lambda: {"attachments": entries})

        def post(self, *args, **kwargs):
            raise AssertionError("Filtering must not mutate remote attachments")

    panel = AttachmentsPanel(api_client=Client())
    panel.set_remote_mode(True, "https://example.test")
    panel.set_remote_vault_root("/vault")
    monkeypatch.setattr(panel, "_get_remote_cache_root", lambda: tmp_path)
    qtbot.addWidget(panel)
    panel.set_page(Path("/vault/Page/Page.md"))
    assert visible_names(panel) == ["notes.pdf"]
    panel.hidden_files_button.click()
    assert visible_names(panel) == [".secret", "notes.pdf"]
    panel.hidden_files_button.click()
    assert visible_names(panel) == ["notes.pdf"]
    assert requests == ["/files/"]


def test_hidden_files_remain_tracked_during_local_server_sync(qtbot, tmp_path, monkeypatch):
    page = tmp_path / "Page.md"
    page.write_text("# Page")
    (tmp_path / ".DS_Store").write_bytes(b"metadata")
    panel = AttachmentsPanel()
    qtbot.addWidget(panel)
    synced = []
    monkeypatch.setattr(panel, "_sync_with_server", lambda files: synced.append([p.name for p in files]))
    panel.set_page(page)
    assert synced == [[".DS_Store"]]
    assert visible_names(panel) == []
    panel.hidden_files_button.click()
    panel.refresh()
    assert visible_names(panel) == [".DS_Store"]
    assert synced == [[".DS_Store"], [".DS_Store"]]


def test_hidden_metadata_does_not_advertise_an_attachment(main_window):
    import time
    window = main_window
    folder = Path(window.vault_root) / "PageB"
    (folder / ".DS_Store").write_bytes(b"metadata")
    window._set_right_panel_collapsed(True)
    window._open_file("/PageB/PageB.md")
    deadline = time.monotonic() + 3
    while window.right_panel._current_page_path != folder / "PageB.md" and time.monotonic() < deadline:
        QTest.qWait(10)
    assert window.right_panel._current_page_path == folder / "PageB.md"
    QTest.qWait(50)
    assert not window._attachment_indicator.isVisible()
    window._focus_attachments_tab()
    panel = window.right_panel.attachments_panel
    assert visible_names(panel) == []
    panel.hidden_files_button.click()
    assert visible_names(panel) == [".DS_Store"]
    assert window._attachment_indicator.isVisible()
    panel.hidden_files_button.click()
    assert not window._attachment_indicator.isVisible()
