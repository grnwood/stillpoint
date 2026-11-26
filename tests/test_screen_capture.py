from PySide6.QtCore import QPoint, QPointF, QRect, QSettings, Qt
from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
import pytest

from sp.app.ui import screen_capture


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch, tmp_path):
    settings = QSettings(str(tmp_path / "capture.ini"), QSettings.IniFormat)
    monkeypatch.setattr(screen_capture, "QSettings", lambda *a: settings)
    return settings


def overlay(qtbot, save=None, scale=1):
    desktop = QPixmap(400 * scale, 300 * scale)
    desktop.setDevicePixelRatio(scale)
    desktop.fill(Qt.white)
    widget = screen_capture.ScreenCaptureOverlay(desktop, QRect(0, 0, 400, 300), save)
    qtbot.addWidget(widget)
    QTest.mousePress(widget, Qt.LeftButton, pos=QPoint(20, 30))
    QTest.mouseMove(widget, QPoint(200, 180))
    QTest.mouseRelease(widget, Qt.LeftButton, pos=QPoint(200, 180))
    return widget


def test_region_selection_and_all_annotations_render_without_controls(qtbot):
    widget = overlay(qtbot)
    assert widget._selected
    assert widget.toolbar.isVisible()
    for tool in ("Line", "Arrow", "Circle", "Box", "Numbering", "Text overlay"):
        widget.annotations = [(tool, QPointF(60, 65), QPointF(120, 115), QColor("red"), "1" if tool == "Numbering" else "Hi")]
        image = widget.rendered_image()
        assert image.size() == widget.selection.size()
        red = sum(image.pixelColor(x, y).red() > 180 and image.pixelColor(x, y).green() < 100
                  for x in range(image.width()) for y in range(image.height()))
        assert red > 10, tool
        assert image.pixelColor(0, 0) == QColor("white")
    widget.undo()
    assert widget.annotations == []
    widget.close()


def test_color_persists_and_copy_exports_only_selected_pixels(qtbot, monkeypatch):
    widget = overlay(qtbot, scale=2)
    assert widget.color == QColor("red")
    monkeypatch.setattr(screen_capture.QColorDialog, "getColor", lambda *a: QColor("#00aa55"))
    widget.choose_color()
    QTest.keyClick(widget, Qt.Key_C, Qt.ControlModifier)
    image = QApplication.clipboard().image()
    assert image.width() == widget.selection.width() * 2
    assert image.height() == widget.selection.height() * 2
    assert image.pixelColor(0, 0) == QColor("white")
    second = overlay(qtbot)
    assert second.color == QColor("#00aa55")
    second.close()


def test_save_attachment_and_failure_retains_editable_capture(qtbot, monkeypatch):
    saved = []
    def save(image):
        saved.append(image)
        return "capture.png"
    widget = overlay(qtbot, save)
    signals = []
    widget.attachmentSaved.connect(signals.append)
    QTest.keyClick(widget, Qt.Key_S, Qt.ControlModifier)
    assert len(saved) == 1
    assert signals == ["capture.png"]
    def fail(image):
        raise OSError("disk full")
    widget = overlay(qtbot, fail)
    warnings = []
    monkeypatch.setattr(screen_capture.QMessageBox, "warning", lambda *a: warnings.append(a[-1]))
    widget.save_attachment()
    assert warnings == ["disk full"]
    assert widget.isVisible()
    widget.close()


def test_preferences_save_shortcut(isolated_settings, qtbot):
    prefs = screen_capture.ScreenCapturePreferences()
    qtbot.addWidget(prefs)
    assert prefs.hotkey.keySequence().toString() == "Alt+Shift+S"
    prefs.hotkey.setKeySequence("Ctrl+Alt+G")
    prefs.save()
    second = screen_capture.ScreenCapturePreferences()
    qtbot.addWidget(second)
    assert second.hotkey.keySequence().toString() == "Ctrl+Alt+G"


def test_attachment_destination_is_bound_before_page_switch(qtbot, monkeypatch, tmp_path):
    from types import SimpleNamespace
    from sp.app.ui.attachments_panel import AttachmentsPanel
    first = tmp_path / "first" / "page.md"
    first.parent.mkdir()
    first.write_text("page")
    second = tmp_path / "second.md"
    second.write_text("second")
    callbacks = []
    changed = []
    monkeypatch.setattr(screen_capture, "start_capture", lambda parent, save, opened: callbacks.append(save))
    panel = SimpleNamespace(current_page_path=first, _remote_mode=False, _http_client=None,
                            _current_page_key=lambda: "first:page", window=lambda: SimpleNamespace(),
                            _refresh_attachments=lambda: None,
                            attachmentsModified=SimpleNamespace(emit=changed.append))
    AttachmentsPanel._capture_screen(panel)
    panel.current_page_path = second
    image = QImage(10, 20, QImage.Format_ARGB32)
    image.fill(Qt.red)
    filename = callbacks[0](image)
    assert (first.parent / filename).is_file()
    assert not (second.parent / filename).exists()
    assert QImage(str(first.parent / filename)).size() == image.size()
    assert changed == ["screen capture saved"]


def test_global_capture_shortcut_refresh_routes_to_last_window(qapp, monkeypatch, isolated_settings):
    from types import SimpleNamespace
    from PySide6.QtGui import QAction
    from sp.app.ui import screen_capture_hotkeys
    from sp.app.ui.recording_hotkeys import RecordingHotkeys
    monkeypatch.setattr(screen_capture_hotkeys, "QSettings", lambda *args: isolated_settings)
    monkeypatch.setattr(RecordingHotkeys, "start", lambda self: None)
    controller = screen_capture_hotkeys.CaptureShortcut(qapp)
    called = []
    window = SimpleNamespace(_capture_region_action=QAction(), _capture_screen=lambda: called.append(True))
    monkeypatch.setattr(controller, "_live_windows", lambda: [window])
    controller._last_window = lambda: window
    controller.refresh()
    assert controller.hotkeys._bindings == (("capture", "Alt+Shift+S", "Capture screen region"),)
    controller._activate("capture")
    assert called == [True]
    isolated_settings.setValue("screenCapture/hotkey", "Ctrl+Alt+G")
    controller.refresh()
    assert controller.hotkeys._bindings[0][1] == "Ctrl+Alt+G"
    isolated_settings.setValue("screenCapture/hotkey", "")
    controller.refresh()
    assert controller.hotkeys._bindings == ()
    controller.hotkeys.close()


@pytest.mark.parametrize("allowed", [True, False])
def test_wayland_screenshot_portal_early_response(qapp, monkeypatch, allowed):
    import asyncio
    from dbus_next import Message, Variant
    from types import SimpleNamespace
    worker = screen_capture._PortalScreenshotWorker()
    ready, failed = [], []
    worker.ready.connect(ready.append)
    worker.failed.connect(failed.append)
    class Bus:
        unique_name = ":1.42"
        disconnected = False
        async def connect(self):
            return self
        def add_message_handler(self, callback):
            self.handler = callback
        async def call(self, message):
            message.serial = 1
            message._marshall()
            if message.member == "AddMatch":
                return Message.new_method_return(message)
            assert message.member == "Screenshot"
            assert message.body[1]["interactive"].value is False
            token = message.body[1]["handle_token"].value
            path = "/org/freedesktop/portal/desktop/request/1_42/" + token
            self.handler(Message.new_signal(path, "org.freedesktop.portal.Request", "Response", "ua{sv}",
                [0 if allowed else 1, {"uri": Variant("s", "file:///tmp/capture.png")}]))
            return Message.new_method_return(message, "o", [path])
        def disconnect(self):
            self.disconnected = True
    bus = Bus()
    monkeypatch.setattr("dbus_next.aio.MessageBus", lambda: bus)
    asyncio.run(worker._capture())
    assert bus.disconnected
    assert ready == (["file:///tmp/capture.png"] if allowed else [])
    assert bool(failed) is not allowed


def test_tool_keys_update_checked_buttons_and_actions(qtbot, monkeypatch):
    widget = overlay(qtbot)
    for key, tool in ((Qt.Key_A, "Arrow"), (Qt.Key_B, "Box"), (Qt.Key_C, "Circle"),
                      (Qt.Key_T, "Text overlay"), (Qt.Key_L, "Line")):
        QTest.keyClick(widget, key)
        assert widget.tool == tool
        assert widget.tool_buttons[tool].isChecked()
    widget.annotations.append(("Line", QPointF(40, 40), QPointF(80, 80), QColor("red"), ""))
    QTest.keyClick(widget, Qt.Key_U)
    assert not widget.annotations
    monkeypatch.setattr(screen_capture.QColorDialog, "getColor", lambda *args: QColor("blue"))
    QTest.keyClick(widget, Qt.Key_P)
    assert widget.color == QColor("blue")
    QTest.keyClick(widget, Qt.Key_Escape)
    assert not widget.isVisible()


@pytest.mark.parametrize("tool,point,text", [
    ("Line", QPoint(80, 80), ""), ("Arrow", QPoint(80, 80), ""),
    ("Circle", QPoint(80, 80), ""), ("Box", QPoint(80, 80), ""),
    ("Numbering", QPoint(60, 60), "1"), ("Text overlay", QPoint(65, 55), "Hi"),
])
def test_select_moves_each_annotation_and_undo_restores(qtbot, tool, point, text):
    widget = overlay(qtbot)
    original = (tool, QPointF(60, 60), QPointF(100, 100), QColor("red"), text)
    widget.annotations = [original]
    QTest.keyClick(widget, Qt.Key_S)
    assert widget.tool == "Select"
    assert widget.tool_buttons["Select"].isChecked()
    QTest.mousePress(widget, Qt.LeftButton, pos=point)
    assert widget._selected_annotation == 0
    QTest.mouseMove(widget, point + QPoint(20, 25))
    QTest.mouseRelease(widget, Qt.LeftButton, pos=point + QPoint(20, 25))
    moved = widget.annotations[0]
    assert moved[1] == original[1] + QPointF(20, 25)
    assert moved[2] == original[2] + QPointF(20, 25)
    assert len(widget.annotations) == 1
    widget.undo()
    assert widget.annotations == [original]
    widget.close()


def test_select_picks_topmost_clamps_drag_and_deselects_empty_space(qtbot):
    widget = overlay(qtbot)
    first = ("Box", QPointF(60, 60), QPointF(100, 100), QColor("red"), "")
    top = ("Line", QPointF(60, 80), QPointF(100, 80), QColor("blue"), "")
    widget.annotations = [first, top]
    widget._set_tool("Select")
    QTest.mousePress(widget, Qt.LeftButton, pos=QPoint(80, 80))
    assert widget._selected_annotation == 1
    QTest.mouseMove(widget, QPoint(390, 290))
    QTest.mouseRelease(widget, Qt.LeftButton, pos=QPoint(390, 290))
    bounds = widget._annotation_path(widget.annotations[1]).boundingRect().adjusted(-1.5, -1.5, 1.5, 1.5)
    assert QRect(widget.selection).contains(bounds.toAlignedRect())
    assert widget.annotations[0] == first
    QTest.mouseClick(widget, Qt.LeftButton, pos=QPoint(35, 40))
    assert widget._selected_annotation is None
    assert len(widget.annotations) == 2
    widget.close()


def test_freehand_stroke_renders_moves_and_undoes(qtbot):
    widget = overlay(qtbot)
    QTest.keyClick(widget, Qt.Key_D)
    assert widget.tool == "Draw"
    assert widget.tool_buttons["Draw"].isChecked()
    points = [QPoint(50, 60), QPoint(80, 100), QPoint(110, 60), QPoint(140, 100)]
    QTest.mousePress(widget, Qt.LeftButton, pos=points[0])
    for point in points[1:]:
        QTest.mouseMove(widget, point)
    QTest.mouseRelease(widget, Qt.LeftButton, pos=points[-1])
    original = widget.annotations[0]
    assert original[4] == tuple(QPointF(point) for point in points)
    image = widget.rendered_image()
    assert image.pixelColor(80-widget.selection.x(), 100-widget.selection.y()).green() < 100
    QTest.keyClick(widget, Qt.Key_S)
    QTest.mousePress(widget, Qt.LeftButton, pos=points[1])
    assert widget._selected_annotation == 0
    QTest.mouseRelease(widget, Qt.LeftButton, pos=points[1] + QPoint(15, 20))
    assert widget.annotations[0][4] == tuple(QPointF(point) + QPointF(15, 20) for point in points)
    widget.undo()
    assert widget.annotations == [original]
    widget.undo()
    assert not widget.annotations
    widget.close()


def test_numbering_key_and_freehand_single_click(qtbot):
    widget = overlay(qtbot)
    QTest.keyClick(widget, Qt.Key_1)
    assert widget.tool == "Numbering"
    assert widget.tool_buttons["Numbering"].isChecked()
    QTest.mouseClick(widget, Qt.LeftButton, pos=QPoint(60, 60))
    assert widget.annotations[0][4] == "1"
    QTest.keyClick(widget, Qt.Key_D)
    QTest.mouseClick(widget, Qt.LeftButton, pos=QPoint(110, 110))
    assert widget.annotations[1][0] == "Draw"
    assert len(widget.annotations[1][4]) == 1
    assert widget.rendered_image().pixelColor(110-widget.selection.x(), 110-widget.selection.y()).green() < 100
    widget._set_tool("Select")
    assert widget._hit_annotation(QPointF(110, 110)) == 1
    widget.close()


def test_inline_text_accepts_tool_letters_and_exports_multiline(qtbot):
    widget = overlay(qtbot)
    QTest.keyClick(widget, Qt.Key_T)
    QTest.mouseClick(widget, Qt.LeftButton, pos=QPoint(60, 80))
    assert widget.text_editor.isVisible()
    assert widget.text_editor.hasFocus()
    QTest.keyClicks(widget.text_editor, "labcputds1")
    QTest.keyClick(widget.text_editor, Qt.Key_Return)
    QTest.keyClicks(widget.text_editor, "second line")
    assert widget.tool == "Text overlay"
    assert widget.text_editor.toPlainText() == "labcputds1\nsecond line"
    image = widget.rendered_image()
    assert not widget.text_frame.isVisible()
    assert len(widget.annotations) == 1
    payload = widget.annotations[0][4]
    assert payload.text == "labcputds1\nsecond line"
    assert payload.size == 18
    assert any(image.pixelColor(x, y).green() < 100 for x in range(image.width()) for y in range(image.height()))
    widget.undo()
    assert not widget.annotations
    widget.close()


@pytest.mark.parametrize("modifier", [Qt.ControlModifier, Qt.MetaModifier])
def test_inline_text_size_buttons_and_modified_scroll(qtbot, modifier):
    from PySide6.QtGui import QWheelEvent
    widget = overlay(qtbot)
    widget._set_tool("Text overlay")
    QTest.mouseClick(widget, Qt.LeftButton, pos=QPoint(50, 90))
    QTest.keyClicks(widget.text_editor, "Text")
    QTest.mouseClick(widget.text_larger, Qt.LeftButton)
    assert widget._text_size == 20
    QTest.mouseClick(widget.text_smaller, Qt.LeftButton)
    assert widget._text_size == 18
    assert widget.text_editor.hasFocus()
    def scroll(amount, modifiers):
        event = QWheelEvent(QPointF(10, 10), QPointF(widget.text_editor.mapToGlobal(QPoint(10, 10))),
                            QPoint(), QPoint(0, amount), Qt.NoButton, modifiers, Qt.NoScrollPhase, False)
        QApplication.sendEvent(widget.text_editor.viewport(), event)
    scroll(120, modifier)
    assert widget._text_size == 20
    scroll(-120, modifier)
    assert widget._text_size == 18
    scroll(120, Qt.NoModifier)
    assert widget._text_size == 18
    QTest.mouseClick(widget.text_larger, Qt.LeftButton)
    QTest.keyClick(widget.text_editor, Qt.Key_Return, Qt.ControlModifier)
    assert widget.annotations[0][4].size == 20
    assert not widget.text_frame.isVisible()
    assert widget._annotation_path(widget.annotations[0]).boundingRect().height() > 18
    widget.close()


def test_existing_inline_text_reedit_preserves_size_and_undo(qtbot):
    widget = overlay(qtbot)
    widget._set_tool("Text overlay")
    QTest.mouseClick(widget, Qt.LeftButton, pos=QPoint(60, 90))
    QTest.keyClicks(widget.text_editor, "Before")
    widget._change_text_size(8)
    widget._set_tool("Select")
    original = widget.annotations[0]
    point = widget._annotation_path(original).boundingRect().center().toPoint()
    QTest.mouseDClick(widget, Qt.LeftButton, pos=point)
    assert widget.text_editor.isVisible()
    assert widget._text_size == 26
    widget.text_editor.selectAll()
    QTest.keyClicks(widget.text_editor, "After")
    widget._finish_text()
    assert len(widget.annotations) == 1
    assert widget.annotations[0][4].text == "After"
    assert widget.annotations[0][4].size == 26
    widget.undo()
    assert widget.annotations == [original]
    widget.close()


def test_click_off_inline_text_commits_and_returns_to_select(qtbot):
    widget = overlay(qtbot)
    widget._set_tool("Text overlay")
    QTest.mouseClick(widget, Qt.LeftButton, pos=QPoint(50, 80))
    QTest.keyClicks(widget.text_editor, "Done")
    QTest.mouseClick(widget, Qt.LeftButton, pos=QPoint(180, 160))
    assert widget.tool == "Select"
    assert widget.tool_buttons["Select"].isChecked()
    assert not widget.text_frame.isVisible()
    assert widget._text_start is None
    assert len(widget.annotations) == 1
    assert widget.annotations[0][4].text == "Done"
    QTest.mouseClick(widget, Qt.LeftButton, pos=QPoint(180, 160))
    assert len(widget.annotations) == 1
    assert not widget.text_frame.isVisible()
    widget.close()


def test_ctrl_s_commits_inline_text_and_saves_capture(qtbot):
    saved = []
    widget = overlay(qtbot, lambda image: saved.append(image) or "capture.png")
    widget._set_tool("Text overlay")
    QTest.mouseClick(widget, Qt.LeftButton, pos=QPoint(50, 90))
    QTest.keyClicks(widget.text_editor, "Save inline")
    QTest.keyClick(widget.text_editor, Qt.Key_S, Qt.ControlModifier)
    assert len(saved) == 1
    assert widget.annotations[0][4].text == "Save inline"
    assert not widget.isVisible()


@pytest.mark.parametrize("stale_page", [False, True])
@pytest.mark.parametrize("save_method", ["shortcut", "paperclip"])
def test_main_capture_binds_active_page_before_lazy_attachment_sync(qtbot, monkeypatch, tmp_path, stale_page, save_method):
    from types import SimpleNamespace
    from sp.app.ui.main_window import MainWindow
    from sp.app.ui.attachments_panel import AttachmentsPanel
    old = tmp_path / "old" / "page.md"
    active = tmp_path / "active" / "page.md"
    for page in (old, active):
        page.parent.mkdir()
        page.write_text("page")
    panel = AttachmentsPanel()
    qtbot.addWidget(panel)
    panel.set_vault_root(str(tmp_path))
    if stale_page:
        panel.set_page(old)
    captures = []
    def start(parent, save, opened):
        desktop = QPixmap(400, 300)
        desktop.fill(Qt.white)
        widget = screen_capture.ScreenCaptureOverlay(desktop, QRect(0, 0, 400, 300), save)
        qtbot.addWidget(widget)
        opened(widget)
        widget.selection = QRect(20, 30, 180, 150)
        widget._selected = True
        captures.append(widget)
    monkeypatch.setattr(screen_capture, "start_capture", start)
    owner = SimpleNamespace(vault_root=str(tmp_path), current_path="/active/page.md",
                            right_panel=SimpleNamespace(attachments_panel=panel))
    MainWindow._capture_screen(owner)
    assert panel.current_page_path == active
    widget = captures[0]
    assert widget.save_image is not None
    # Switching pages during annotation must not redirect the saved PNG.
    panel.set_page(old)
    owner.current_path = "/old/page.md"
    if save_method == "shortcut":
        QTest.keyClick(widget, Qt.Key_S, Qt.ControlModifier)
    else:
        widget.save_attachment()
    files = list(active.parent.glob("capture-*.png"))
    assert len(files) == 1
    assert QImage(str(files[0])).size() == widget.selection.size()
    assert not list(old.parent.glob("capture-*.png"))
    assert not widget.isVisible()
