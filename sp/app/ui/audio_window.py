"""Movable, modeless tool windows shared by the recorder and audio player."""
from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QDialog


class AudioWindow(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent, Qt.Tool | Qt.WindowTitleHint | Qt.WindowCloseButtonHint)
        self.setWindowModality(Qt.NonModal)
        self._drag_handles = []
        self._drag_offset = None

    def add_drag_handle(self, widget):
        self._drag_handles.append(widget)
        widget.setCursor(Qt.OpenHandCursor)
        widget.installEventFilter(self)

    def eventFilter(self, watched, event):
        if watched in getattr(self, "_drag_handles", ()):
            if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                window = self.windowHandle()
                # Native movement also works on Wayland, where direct window
                # positioning is restricted. Other backends can use the fallback.
                if window is None or not window.startSystemMove():
                    self._drag_offset = event.globalPosition().toPoint() - self.pos()
                return True
            if event.type() == QEvent.MouseMove and self._drag_offset is not None:
                self.move(event.globalPosition().toPoint() - self._drag_offset)
                return True
            if event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
                self._drag_offset = None
                return True
        return super().eventFilter(watched, event)
