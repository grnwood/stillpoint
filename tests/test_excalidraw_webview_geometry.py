from __future__ import annotations

from PySide6.QtTest import QTest


def test_excalidraw_window_saves_resize_and_restores_geometry(
        qtbot, monkeypatch, qapp) -> None:
    import sp.app.excalidraw_webview_process as webview

    class MemorySettings:
        values: dict[str, str] = {}

        def __init__(self, *_args):
            self.sync_count = 0

        def value(self, key, default=""):
            return self.values.get(key, default)

        def setValue(self, key, value):
            self.values[key] = value

        def sync(self):
            self.sync_count += 1

    monkeypatch.setattr(webview, "QSettings", MemorySettings)
    monkeypatch.setattr(webview, "get_diagram_editor_icon", lambda _editor: qapp.windowIcon())

    first = webview.ExcalidrawWindow("Drawing")
    qtbot.addWidget(first)
    first.resize(611, 477)
    QTest.qWait(first._geometry_timer.interval() + 50)
    qapp.processEvents()

    assert MemorySettings.values[webview._GEOMETRY_KEY]
    saved_size = first.size()
    first.close()

    second = webview.ExcalidrawWindow("Drawing")
    qtbot.addWidget(second)
    second.show()
    qapp.processEvents()
    assert second.size() == saved_size
    second.close()
