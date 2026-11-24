from io import BytesIO
from pathlib import Path
import threading
import time

import httpx
import pytest
from PIL import Image
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QImage, QImageReader
from PySide6.QtTest import QTest

from sp.app.ui import attachment_thumbnails as thumbnails
from sp.app.ui import attachments_panel as panels


@pytest.fixture(autouse=True)
def isolated_settings_and_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(panels, "QSettings", lambda *args:
                        QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat))
    monkeypatch.setattr(thumbnails, "thumbnail_cache_root", lambda: tmp_path / "cache")


def wait_for(predicate, timeout=4000):
    deadline = time.monotonic() + timeout / 1000
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(20)
    assert predicate()


def make_image(path, color="red"):
    Image.new("RGB", (1200, 600), color).save(path)


def source_for(path):
    stat = path.stat()
    return thumbnails.ThumbnailSource(str(path.resolve()), f"{stat.st_mtime_ns}:{stat.st_size}", path=path)


def make_panel(qtbot, tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    page = vault / "Page.md"
    page.write_text("# Page")
    make_image(vault / "photo.png")
    widget = panels.AttachmentsPanel()
    widget.resize(450, 280)
    qtbot.addWidget(widget)
    widget.set_page(page)
    return widget, vault


def test_thumbnail_toggle_loads_in_background_and_restores_file_icons(qtbot, tmp_path, monkeypatch):
    widget, vault = make_panel(qtbot, tmp_path)
    threads = []
    original = thumbnails.load_thumbnail

    def load(*args):
        threads.append(threading.get_ident())
        return original(*args)

    monkeypatch.setattr(thumbnails, "load_thumbnail", load)
    assert not widget.thumbnails_button.icon().isNull()
    assert not widget.thumbnails_button.isChecked()
    item = widget.attachments_list.item(0)
    file_icon = item.icon().cacheKey()
    QTest.qWait(300)
    assert threads == []
    widget.thumbnails_button.click()
    wait_for(lambda: item.data(panels._THUMB_KEY_ROLE))
    assert threads and all(thread != threading.get_ident() for thread in threads)
    assert item.icon().cacheKey() != file_icon
    assert widget.attachments_list.iconSize().width() == 48
    assert {file.name for file in vault.iterdir()} == {"Page.md", "photo.png"}
    cached = list((tmp_path / "cache").glob("*.png"))
    assert len(cached) == 1
    assert QImageReader(str(cached[0])).size().width() == 192
    widget.thumbnails_button.click()
    assert item.icon().cacheKey() == file_icon
    assert widget.attachments_list.iconSize().width() == 16


def test_thumbnail_choice_is_remembered(qtbot, tmp_path):
    widget, _ = make_panel(qtbot, tmp_path)
    widget.thumbnails_button.click()
    widget.hide()
    second = panels.AttachmentsPanel()
    qtbot.addWidget(second)
    assert second.thumbnails_button.isChecked()


def test_refresh_and_toggle_reuse_ready_thumbnails_immediately(qtbot, tmp_path, monkeypatch):
    widget, _ = make_panel(qtbot, tmp_path)
    widget.thumbnails_button.click()
    wait_for(lambda: widget.attachments_list.item(0).data(panels._THUMB_KEY_ROLE))
    key = widget.attachments_list.item(0).data(panels._THUMB_KEY_ROLE)
    monkeypatch.setattr(widget._thumbnail_loader, "request", lambda source: pytest.fail("cached preview was queued again"))
    widget.refresh()
    assert widget.attachments_list.item(0).data(panels._THUMB_KEY_ROLE) == key
    widget.thumbnails_button.click()
    widget.thumbnails_button.click()
    assert widget.attachments_list.item(0).data(panels._THUMB_KEY_ROLE) == key


def test_new_panel_reads_existing_disk_thumbnail_before_first_paint(qtbot, tmp_path, monkeypatch):
    widget, vault = make_panel(qtbot, tmp_path)
    widget.thumbnails_button.click()
    wait_for(lambda: widget.attachments_list.item(0).data(panels._THUMB_KEY_ROLE))
    key = widget.attachments_list.item(0).data(panels._THUMB_KEY_ROLE)
    widget.hide()
    second = panels.AttachmentsPanel()
    second.resize(450, 280)
    qtbot.addWidget(second)
    assert not second._thumbnail_loader._images
    monkeypatch.setattr(second._thumbnail_loader, "request", lambda source: pytest.fail("disk preview was queued again"))
    second.set_page(vault / "Page.md")
    assert second.attachments_list.item(0).data(panels._THUMB_KEY_ROLE) == key
    assert not second._thumbnail_loader._active
    QTest.qWait(300)


def test_changed_source_does_not_show_a_stale_cached_preview(qtbot, tmp_path):
    widget, vault = make_panel(qtbot, tmp_path)
    widget.thumbnails_button.click()
    wait_for(lambda: widget.attachments_list.item(0).data(panels._THUMB_KEY_ROLE))
    old_key = widget.attachments_list.item(0).data(panels._THUMB_KEY_ROLE)
    make_image(vault / "photo.png", "blue")
    widget.refresh()
    item = widget.attachments_list.item(0)
    assert not item.data(panels._THUMB_KEY_ROLE)
    wait_for(lambda: item.data(panels._THUMB_KEY_ROLE))
    assert item.data(panels._THUMB_KEY_ROLE) != old_key
    preview = item.icon().pixmap(48, 48).toImage()
    assert preview.pixelColor(preview.rect().center()).blue() == 255


def test_cache_reused_and_changed_images_get_new_thumbnail(tmp_path, monkeypatch):
    path = tmp_path / "image.png"
    make_image(path)
    first_source = source_for(path)
    cache = tmp_path / "cache"
    first = thumbnails.load_thumbnail(first_source, cache)
    assert not first.isNull()
    assert first.width() == 192 and first.height() == 96
    reads = []
    original = thumbnails._read_image

    def read(reader):
        reads.append(reader.fileName())
        return original(reader)

    monkeypatch.setattr(thumbnails, "_read_image", read)
    second = thumbnails.load_thumbnail(first_source, cache)
    assert second == first
    assert reads == [str(cache / f"{first_source.key}.png")]
    make_image(path, "blue")
    changed_source = source_for(path)
    assert changed_source.key != first_source.key
    changed = thumbnails.load_thumbnail(changed_source, cache)
    assert changed.pixelColor(0, 0).blue() == 255
    assert thumbnails.load_thumbnail(first_source, cache).isNull()


def test_only_visible_matching_images_are_requested(qtbot, tmp_path, monkeypatch):
    widget, vault = make_panel(qtbot, tmp_path)
    for index in range(30):
        make_image(vault / f"image-{index:02}.png")
    widget.refresh()
    requests = []

    def request(source):
        requests.append(Path(source.identity).name)
        return QImage()

    monkeypatch.setattr(widget._thumbnail_loader, "request", request)
    widget.filename_filter.setText("photo")
    widget.thumbnails_button.click()
    wait_for(lambda: bool(requests))
    assert requests == ["photo.png"]
    requests.clear()
    widget.filename_filter.clear()
    wait_for(lambda: bool(requests))
    assert 0 < len(requests) < 10
    assert "image-29.png" not in requests
    requests.clear()
    widget.attachments_list.verticalScrollBar().setValue(widget.attachments_list.verticalScrollBar().maximum())
    wait_for(lambda: bool(requests))
    assert "image-29.png" in requests


def test_hidden_panel_does_not_start_loading(qtbot, tmp_path, monkeypatch):
    widget, _ = make_panel(qtbot, tmp_path)
    requested = []
    monkeypatch.setattr(widget._thumbnail_loader, "request", lambda source: requested.append(source) or QImage())
    widget.thumbnails_button.click()
    widget.hide()
    QTest.qWait(350)
    assert requested == []
    widget.show()
    wait_for(lambda: bool(requested))


def test_page_change_during_loading_does_not_apply_old_thumbnail(qtbot, tmp_path, monkeypatch):
    widget, vault = make_panel(qtbot, tmp_path)
    started, release = threading.Event(), threading.Event()
    original = thumbnails.load_thumbnail

    def load(*args):
        started.set()
        release.wait(3)
        return original(*args)

    monkeypatch.setattr(thumbnails, "load_thumbnail", load)
    widget.thumbnails_button.click()
    try:
        wait_for(started.is_set)
        other = tmp_path / "other"
        other.mkdir()
        page = other / "Other.md"
        page.touch()
        (other / "document.pdf").touch()
        widget.set_page(page)
        item = widget.attachments_list.item(0)
        original_icon = item.icon().cacheKey()
        release.set()
        wait_for(lambda: not widget._thumbnail_loader._active)
        QTest.qWait(300)
        assert item.text() == "document.pdf"
        assert item.icon().cacheKey() == original_icon
        assert not item.data(panels._THUMB_KEY_ROLE)
    finally:
        release.set()


def test_remote_thumbnails_use_cache_without_downloading_to_attachment_folder(qtbot, tmp_path, monkeypatch):
    image = BytesIO()
    Image.new("RGB", (500, 250), "green").save(image, "PNG")
    requests = []
    revision = {"mtime_ns": 100}

    def response(request):
        requests.append(request.url.path)
        if request.url.path == "/files/":
            return httpx.Response(200, json={"attachments": [
                {"attachment_path": "/Page/photo.png", "updated": 123, "mtime_ns": revision["mtime_ns"],
                 "size_bytes": len(image.getvalue())},
            ]})
        assert request.url.path == "/api/file/raw"
        return httpx.Response(200, content=image.getvalue())

    with httpx.Client(base_url="https://example.test", transport=httpx.MockTransport(response)) as client:
        widget = panels.AttachmentsPanel(api_client=client)
        qtbot.addWidget(widget)
        widget.set_remote_mode(True, "https://example.test")
        widget.set_remote_vault_root("/vault")
        monkeypatch.setattr(widget, "_get_remote_cache_root", lambda: tmp_path / "remote")
        widget.set_page(Path("/vault/Page/Page.md"))
        assert requests == ["/files/"]
        widget.thumbnails_button.click()
        wait_for(lambda: widget.attachments_list.item(0).data(panels._THUMB_KEY_ROLE))
        assert requests.count("/api/file/raw") == 1
        widget.refresh()
        wait_for(lambda: widget.attachments_list.item(0).data(panels._THUMB_KEY_ROLE))
        assert requests.count("/api/file/raw") == 1
        assert not (tmp_path / "remote").exists()
        revision["mtime_ns"] = 200
        widget.refresh()
        wait_for(lambda: widget.attachments_list.item(0).data(panels._THUMB_KEY_ROLE))
        assert requests.count("/api/file/raw") == 2


def test_corrupt_image_keeps_file_icon(qtbot, tmp_path):
    widget, vault = make_panel(qtbot, tmp_path)
    (vault / "photo.png").write_bytes(b"not an image")
    widget.refresh()
    item = widget.attachments_list.item(0)
    icon = item.icon().cacheKey()
    widget.thumbnails_button.click()
    wait_for(lambda: item.data(panels._THUMB_KEY_ROLE))
    assert item.icon().cacheKey() == icon
    assert not list((tmp_path / "cache").glob("*.png"))
