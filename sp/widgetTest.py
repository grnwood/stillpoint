import sys
from PySide6.QtCore import Qt, QEvent
from PySide6.QtGui import QTextCursor, QColor, QTextFormat
from PySide6.QtWidgets import QApplication, QMainWindow, QTextEdit

class ViTextEdit(QTextEdit):
    def __init__(self):
        super().__init__()
        self._vi_mode = False
        self._vi_last_cursor_pos = -1
        self.cursorPositionChanged.connect(self._update_vi_cursor)
        self.setPlainText("Type here...\nThis is a native QTextEdit widget with vi-mode.")

    def set_vi_mode(self, active: bool):
        if self._vi_mode == active:
            return
        self._vi_mode = active
        self._update_vi_cursor()

    def _update_vi_cursor(self):
        if not self._vi_mode:
            self.setExtraSelections([])
            return
        pos = self.textCursor().position()
        if pos == self._vi_last_cursor_pos:
            return
        self._vi_last_cursor_pos = pos
        cursor = self.textCursor()
        block_cursor = QTextCursor(cursor)
        if not block_cursor.atEnd():
            block_cursor.movePosition(QTextCursor.Right, QTextCursor.KeepAnchor)
        extra = QTextEdit.ExtraSelection()
        extra.cursor = block_cursor
        fmt = extra.format
        fmt.setBackground(QColor("#b36aff"))  # purple block
        fmt.setForeground(QColor("#fff"))     # white text
        fmt.setProperty(QTextFormat.FullWidthSelection, True)
        self.setExtraSelections([extra])

class WidgetTest(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("WidgetTest - Native QTextEdit Demo with vi-mode")
        self.resize(600, 400)
        self.editor = ViTextEdit()
        self.setCentralWidget(self.editor)
        self._vi_mode = False
        self._install_vi_event_filter()

    def _install_vi_event_filter(self):
        app = QApplication.instance()
        if app:
            app.installEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.KeyPress:
            if event.key() == Qt.Key_Semicolon and event.modifiers() == Qt.AltModifier:
                self._vi_mode = not self._vi_mode
                self.editor.set_vi_mode(self._vi_mode)
                return True
            if self._vi_mode:
                mapping = self._translate_vi_key_event(event)
                if mapping:
                    self._dispatch_vi_navigation(mapping)
                    return True
                # Block unmapped letter keys unless Control
                if Qt.Key_A <= event.key() <= Qt.Key_Z:
                    if not (event.modifiers() & Qt.ControlModifier):
                        return True
        return super().eventFilter(obj, event)

    def _translate_vi_key_event(self, event):
        key = event.key()
        shift = bool(event.modifiers() & Qt.ShiftModifier)
        keep = QTextCursor.KeepAnchor if shift else QTextCursor.MoveAnchor
        if key == Qt.Key_J:
            return (QTextCursor.Down, keep)
        elif key == Qt.Key_K:
            return (QTextCursor.Up, keep)
        elif key == Qt.Key_H:
            return (QTextCursor.Left, keep)
        elif key == Qt.Key_L:
            return (QTextCursor.Right, keep)
        elif key == Qt.Key_A:
            return (QTextCursor.StartOfLine, keep)
        elif key == Qt.Key_Semicolon:
            return (QTextCursor.EndOfLine, keep)
        return None

    def _dispatch_vi_navigation(self, mapping):
        move, keep = mapping
        cur = self.editor.textCursor()
        cur.movePosition(move, keep)
        self.editor.setTextCursor(cur)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = WidgetTest()
    win.show()
    sys.exit(app.exec())
