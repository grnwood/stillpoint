from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QPoint, QPointF, QSettings, Qt, QUrl
from PySide6.QtGui import QColor, QImage, QTextCursor, QTextDocument, QTextImageFormat, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QListWidgetItem, QMessageBox

from sp.app.ui import image_editor_window as module
from sp.app.ui import screen_capture


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch, tmp_path):
    monkeypatch.setattr(screen_capture, "QSettings", lambda *args: QSettings(str(tmp_path / "capture.ini"), QSettings.IniFormat))


@pytest.fixture
def image_path(tmp_path):
    path = tmp_path / "test.png"
    image = QImage(300, 200, QImage.Format_ARGB32)
    image.fill(Qt.white)
    assert image.save(str(path))
    return path


def add_line(window):
    window.canvas.annotations.append(("Line", QPointF(30, 30), QPointF(90, 30), QColor("red"), ""))


def test_window_canvas_saves_original_and_copy_keeps_editor_open(qtbot, image_path):
    window = module.ImageEditorWindow(image_path)
    qtbot.addWidget(window)
    assert window.canvas.tool == "Select"
    assert window.canvas.selection == window.canvas.rect()
    assert window.canvas.toolbar.parent() is not window.canvas
    add_line(window)
    window.canvas.copy_image()
    assert window.isVisible()
    assert window.save_changes()
    image = QImage(str(image_path))
    assert image.pixelColor(60, 30).green() < 100
    assert image.pixelColor(0, 0) == QColor("white")
    assert window._saved_annotations == window.canvas.annotations
    assert window.close()


def test_close_cancel_preserves_edits_and_save_failure_retains_window(qtbot, monkeypatch, image_path):
    window = module.ImageEditorWindow(image_path)
    qtbot.addWidget(window)
    add_line(window)
    monkeypatch.setattr(module.QMessageBox, "question", lambda *args: QMessageBox.Cancel)
    assert not window.close()
    assert window.isVisible()
    monkeypatch.setattr(module, "_write_image", lambda *args: (_ for _ in ()).throw(OSError("disk full")))
    warnings = []
    monkeypatch.setattr(module.QMessageBox, "warning", lambda *args: warnings.append(args[-1]))
    assert not window.save_changes()
    assert warnings == ["disk full"]
    assert window._saved_annotations == []
    assert QImage(str(image_path)).pixelColor(60, 30) == QColor("white")
    monkeypatch.setattr(module.QMessageBox, "question", lambda *args: QMessageBox.Discard)
    window.close()


def test_save_as_png_preserves_original_and_read_only(qtbot, monkeypatch, image_path, tmp_path):
    window = module.ImageEditorWindow(image_path, read_only=True)
    qtbot.addWidget(window)
    add_line(window)
    copy = tmp_path / "edited.png"
    monkeypatch.setattr(module.QFileDialog, "getSaveFileName", lambda *args: (str(copy), "PNG"))
    assert window.save_changes()
    assert QImage(str(image_path)).pixelColor(60, 30) == QColor("white")
    assert QImage(str(copy)).pixelColor(60, 30).green() < 100
    assert window.path == copy
    assert not window.read_only
    window.close()


def test_attachment_double_click_uses_editor_and_native_viewer_is_explicit(qtbot, monkeypatch, image_path):
    from sp.app.ui.attachments_panel import AttachmentsPanel
    panel = AttachmentsPanel()
    qtbot.addWidget(panel)
    item = QListWidgetItem()
    item.setData(Qt.UserRole, str(image_path))
    edited, native = [], []
    monkeypatch.setattr(panel, "_open_image_attachment", lambda item: edited.append(item))
    monkeypatch.setattr("sp.app.ui.attachments_panel.open_path_in_system", native.append)
    panel._open_attachment(item)
    assert edited == [item]
    assert not native
    panel._open_in_default_viewer(item)
    assert native == [image_path]


def test_editor_double_click_opens_image_editor_and_refresh_keeps_text(qtbot, monkeypatch, image_path):
    from sp.app.ui.markdown_editor import MarkdownEditor
    editor = MarkdownEditor()
    qtbot.addWidget(editor)
    editor.set_context(image_path.parent, "page.md")
    fmt = QTextImageFormat()
    fmt.setName(str(image_path))
    cursor = QTextCursor(editor.document())
    cursor.insertImage(fmt)
    cursor.insertText("keep this")
    calls = []
    monkeypatch.setattr(editor, "_image_at_position", lambda point: (None, fmt))
    monkeypatch.setattr(module, "open_image_editor", lambda *args, **kwargs: calls.append((args, kwargs)))
    event = QMouseEvent(QMouseEvent.MouseButtonDblClick, QPointF(10, 10), QPointF(10, 10), Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    editor.mouseDoubleClickEvent(event)
    assert calls[0][0][0] == image_path
    assert calls[0][1]["read_only"] is False
    before = editor.toPlainText()
    image = QImage(str(image_path))
    image.fill(Qt.red)
    image.save(str(image_path))
    module.refresh_open_image_resources(image_path)
    assert editor.toPlainText() == before
    resource = editor.document().resource(QTextDocument.ImageResource, QUrl(str(image_path)))
    assert resource.pixelColor(0, 0) == QColor("red")


def test_remote_saver_replaces_bound_image_then_updates_cache(image_path):
    uploaded = []
    class Client:
        def post(self, endpoint, **kwargs):
            uploaded.append((endpoint, kwargs))
            return SimpleNamespace(status_code=200, raise_for_status=lambda: None)
    save = module.remote_image_saver(Client(), "/Projects/picture.png", "/Projects/page.md")
    image = QImage(str(image_path))
    image.fill(Qt.blue)
    save(image, image_path, "PNG")
    assert uploaded[0][0] == "/files/attach"
    assert uploaded[0][1]["data"] == {"page_path": "/Projects/page.md"}
    assert uploaded[0][1]["files"]["files"][0] == "picture.png"
    assert QImage(str(image_path)).pixelColor(0, 0) == QColor("blue")


def test_attachment_image_context_menu_exposes_native_viewer(qtbot, monkeypatch, image_path):
    from PySide6.QtWidgets import QMenu
    from sp.app.ui.attachments_panel import AttachmentsPanel, AttachmentTableItem
    panel = AttachmentsPanel()
    qtbot.addWidget(panel)
    item = AttachmentTableItem("test.png")
    item.setData(Qt.UserRole, str(image_path))
    panel.attachments_list.addItem(item)
    item.setSelected(True)
    opened = []
    monkeypatch.setattr(panel, "_open_in_default_viewer", opened.append)
    class Menu(QMenu):
        def exec(self, pos):
            actions = {action.text(): action for action in self.actions()}
            assert "Edit image" in actions
            actions["Open in default viewer"].trigger()
    monkeypatch.setattr("sp.app.ui.attachments_panel.QMenu", Menu)
    panel._on_attachments_context_menu(QPoint(-1, -1))
    assert opened == [item]


def test_canvas_save_shortcut_and_tool_buttons_work_in_window(qtbot, image_path):
    window = module.ImageEditorWindow(image_path)
    qtbot.addWidget(window)
    window.canvas.setFocus()
    QTest.keyClick(window.canvas, Qt.Key_L)
    assert window.canvas.tool == "Line"
    QTest.mousePress(window.canvas, Qt.LeftButton, pos=QPoint(30, 50))
    QTest.mouseRelease(window.canvas, Qt.LeftButton, pos=QPoint(90, 50))
    QTest.keyClick(window.canvas, Qt.Key_S, Qt.ControlModifier)
    assert window.isVisible()
    assert QImage(str(image_path)).pixelColor(60, 50).green() < 100
    assert window._saved_annotations == window.canvas.annotations
    window.close()


def test_zoomed_view_maps_drawing_to_original_pixels_and_ctrl_s_saves(qtbot, image_path):
    window = module.ImageEditorWindow(image_path)
    qtbot.addWidget(window)
    window.zoom_in_button.click()
    assert window.zoom == pytest.approx(1.2)
    assert window.zoom_label.text() == "120%"
    window.set_zoom(2)
    window.canvas._set_tool("Line")
    view = window.scroll
    first = view.mapFromScene(QPointF(30, 60))
    last = view.mapFromScene(QPointF(90, 60))
    QTest.mousePress(view.viewport(), Qt.LeftButton, pos=first)
    QTest.mouseRelease(view.viewport(), Qt.LeftButton, pos=last)
    assert len(window.canvas.annotations) == 1
    annotation = window.canvas.annotations[0]
    assert abs(annotation[1].x()-30) < 1
    assert abs(annotation[2].x()-90) < 1
    QTest.keyClick(view.viewport(), Qt.Key_S, Qt.ControlModifier)
    image = QImage(str(image_path))
    assert image.size().width() == 300
    assert image.size().height() == 200
    assert image.pixelColor(60, 60).green() < 100
    assert window._saved_annotations == window.canvas.annotations
    window.close()


@pytest.mark.parametrize("modifier", [Qt.ControlModifier, Qt.MetaModifier])
def test_modified_wheel_zooms_view_and_edits_text_size_when_typing(qtbot, image_path, modifier):
    from PySide6.QtGui import QWheelEvent
    window = module.ImageEditorWindow(image_path)
    qtbot.addWidget(window)
    view = window.scroll
    def scroll(amount):
        pos = view.viewport().rect().center()
        event = QWheelEvent(QPointF(pos), QPointF(view.viewport().mapToGlobal(pos)), QPoint(), QPoint(0, amount),
                            Qt.NoButton, modifier, Qt.NoScrollPhase, False)
        module.QApplication.sendEvent(view.viewport(), event)
    scroll(120)
    assert window.zoom == pytest.approx(1.2)
    scroll(-120)
    assert window.zoom == pytest.approx(1)
    window.canvas._set_tool("Text overlay")
    QTest.mouseClick(view.viewport(), Qt.LeftButton, pos=view.mapFromScene(QPointF(50, 80)))
    QTest.keyClicks(window.canvas.text_editor, "Inline save")
    scroll(120)
    assert window.zoom == pytest.approx(1)
    assert window.canvas._text_size == 20
    QTest.keyClick(window.canvas.text_editor, Qt.Key_S, Qt.ControlModifier)
    assert window.canvas.annotations[0][4].text == "Inline save"
    assert window._saved_annotations == window.canvas.annotations
    assert window.isVisible()
    window.close()


def test_ctrl_s_works_with_popup_toolbar_focus(qtbot, image_path):
    window = module.ImageEditorWindow(image_path)
    qtbot.addWidget(window)
    add_line(window)
    window.save_as_button.setFocus()
    QTest.keyClick(window.save_as_button, Qt.Key_S, Qt.ControlModifier)
    assert QImage(str(image_path)).pixelColor(60, 30).green() < 100
    assert window._saved_annotations == window.canvas.annotations
    window.close()


@pytest.mark.parametrize("mode", ["capture", "popup", "popup_toolbar", "popup_text"])
@pytest.mark.parametrize("modifier", [Qt.ControlModifier, Qt.MetaModifier])
def test_image_save_chord_takes_priority_over_main_application_shortcut(qtbot, image_path, mode, modifier):
    from PySide6.QtGui import QAction, QKeySequence, QPixmap
    from PySide6.QtWidgets import QApplication, QMainWindow
    host = QMainWindow()
    qtbot.addWidget(host)
    main_saves = []
    action = QAction("Save page", host)
    action.setShortcut(QKeySequence.Save)
    action.setShortcutContext(Qt.ApplicationShortcut)
    action.triggered.connect(lambda: main_saves.append(True))
    host.addAction(action)
    saved = []
    if mode == "capture":
        window = screen_capture.ScreenCaptureOverlay(QPixmap.fromImage(QImage(str(image_path))),
                                                     module.QRect(0, 0, 300, 200),
                                                     lambda image: saved.append(True) or "capture.png")
        qtbot.addWidget(window)
        window.selection = window.rect()
        window._selected = True
        target = window
    else:
        window = module.ImageEditorWindow(image_path, host)
        qtbot.addWidget(window)
        add_line(window)
        window.saved.connect(lambda path: saved.append(True))
        target = window.scroll.viewport()
        if mode == "popup_toolbar":
            target = window.save_as_button
        elif mode == "popup_text":
            window.canvas._set_tool("Text overlay")
            window.canvas._begin_text(QPointF(50, 90))
            QTest.keyClicks(window.canvas.text_editor, "Save this text")
            target = window.canvas.text_editor
    window.activateWindow()
    target.setFocus()
    QApplication.processEvents()
    QTest.keyClick(target, Qt.Key_S, modifier)
    assert saved == [True]
    assert main_saves == []
    if mode != "capture":
        host.activateWindow()
        host.setFocus()
        QApplication.processEvents()
        QTest.keyClick(host, Qt.Key_S, Qt.ControlModifier)
        assert main_saves == [True]
        assert saved == [True]
        window.close()
