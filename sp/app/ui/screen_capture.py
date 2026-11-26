"""Frozen desktop region selection and non-destructive screenshot annotation."""
from __future__ import annotations

import re
import math
from dataclasses import dataclass
from pathlib import Path
from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRect, QRectF, QSettings, QSize, QThread, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QGuiApplication, QIcon, QKeySequence, QPainter, QPainterPath, QPainterPathStroker, QPen, QPixmap, QPolygonF, QShortcut
from PySide6.QtWidgets import (QApplication, QButtonGroup, QColorDialog, QFormLayout,
    QGridLayout, QPlainTextEdit, QFrame, QKeySequenceEdit, QLabel, QMessageBox, QPushButton, QWidget)


ASSETS = Path(__file__).resolve().parents[2] / "assets"
TOOLBAR_ICONS = {
    "Line": "line.svg", "Arrow": "arrow.svg", "Circle": "circle.svg",
    "Box": "rectangle.svg", "Text overlay": "text.svg", "Color": "colors.svg",
    "Undo": "undo.svg", "Copy (Ctrl+C)": "clipboard.svg",
    "Attach (Ctrl+S)": "attachment-paperclip.svg",
    "Numbering": "numbering.svg", "Cancel": "cancel.svg", "Select": "select.svg", "Draw": "draw.svg",
}

TOOL_KEYS = {"Draw": "D", "Numbering": "1", "Select": "S", "Line": "L", "Arrow": "A", "Box": "B", "Circle": "C", "Text overlay": "T"}
ACTION_KEYS = {"Color": "P", "Undo": "U", "Cancel": "Esc"}


@dataclass(frozen=True)
class TextAnnotation:
    text: str
    size: int = 18


def _text_data(value):
    return value if isinstance(value, TextAnnotation) else TextAnnotation(value)


def _text_font(value):
    return QFont("Sans Serif", _text_data(value).size, QFont.Bold)


class ImageSaveKeyFilter(QObject):
    """Claim image Save before the main page's application-wide shortcut."""
    def __init__(self, owner, callback, canvas=None):
        super().__init__(owner)
        self.owner = owner
        self.callback = callback
        self.canvas = canvas
        QApplication.instance().installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() not in (QEvent.ShortcutOverride, QEvent.KeyPress):
            return False
        if not self.owner.isVisible() or not isinstance(watched, QWidget):
            return False
        # Separate native dialogs keep their own shortcuts. Graphics-proxy
        # canvas widgets have a separate QWidget window and need explicit scope.
        belongs = watched.window() is self.owner
        if not belongs and self.canvas is not None:
            belongs = (watched.window() is self.canvas.window()
                       and (watched is self.canvas or self.canvas.isAncestorOf(watched)))
        if not belongs:
            return False
        modifiers = event.modifiers()
        if (event.key() != Qt.Key_S or not modifiers & (Qt.ControlModifier | Qt.MetaModifier)
                or modifiers & (Qt.ShiftModifier | Qt.AltModifier)):
            return False
        event.accept()
        if event.type() == QEvent.KeyPress and not event.isAutoRepeat():
            self.callback()
        return True


class CanvasTextEdit(QPlainTextEdit):
    def __init__(self, overlay, parent):
        super().__init__(parent)
        self.overlay = overlay
        self.setFrameShape(QFrame.NoFrame)
        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.document().setDocumentMargin(0)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

    def wheelEvent(self, event):
        if event.modifiers() & (Qt.ControlModifier | Qt.MetaModifier):
            self.overlay._resize_text_from_wheel(event)
        else:
            super().wheelEvent(event)

    def keyPressEvent(self, event):
        modified = event.modifiers() & (Qt.ControlModifier | Qt.MetaModifier)
        if event.key() == Qt.Key_Escape:
            self.overlay.close()
        elif modified and event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.overlay._finish_text()
        elif modified and event.key() == Qt.Key_S:
            self.overlay.save_attachment()
        elif modified and event.key() == Qt.Key_C:
            self.overlay.copy_image()
        else:
            super().keyPressEvent(event)


class ScreenCapturePreferences(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        form = QFormLayout(self)
        self.hotkey = QKeySequenceEdit(QSettings("StillPoint", "StillPoint").value("screenCapture/hotkey", "Alt+Shift+S"))
        self.hotkey.setMaximumSequenceLength(1)
        form.addRow("Capture region hotkey:", self.hotkey)
        hint = QLabel("Capture a region, annotate it, then Ctrl+C to copy or Ctrl+S to attach to the current page.")
        hint.setWordWrap(True)
        form.addRow(hint)

    def save(self):
        sequence = self.hotkey.keySequence().toString()
        if sequence and (not re.fullmatch(r"(?:(?:Ctrl|Alt|Shift|Meta)\+)+[A-Za-z0-9]", sequence)
                         or not any(mod in sequence for mod in ("Ctrl+", "Alt+", "Meta+"))):
            QMessageBox.warning(self, "Capture hotkey", "Choose one letter or number with Ctrl, Alt, or Meta (and optionally Shift), or clear the shortcut to disable it.")
            return False
        QSettings("StillPoint", "StillPoint").setValue("screenCapture/hotkey", sequence)
        return True


class ScreenCaptureOverlay(QWidget):
    attachmentSaved = Signal(str)

    def __init__(self, desktop, geometry, save_image=None, parent=None):
        super().__init__(parent, Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setGeometry(geometry)
        self.desktop = desktop
        self.save_image = save_image
        self.selection = QRect()
        self._anchor = None
        self._current = None
        self._stroke_points = []
        self._selected = False
        self.annotations = []
        self._history = []
        self._selected_annotation = None
        self._drag_original = None
        self._drag_snapshot = None
        self._text_size = 18
        self._text_index = None
        self._text_before = None
        self._text_start = None
        self._text_color = None
        self.tool = "Line"
        self.settings = QSettings("StillPoint", "StillPoint")
        self.color = QColor(self.settings.value("screenCapture/color", "#ff0000"))
        if not self.color.isValid():
            self.color = QColor("#ff0000")
        self.setCursor(Qt.CrossCursor)
        self.toolbar = QWidget(self)
        self.toolbar.setStyleSheet("QWidget {background: #252525; color: white;} QPushButton {padding: 6px; border: 1px solid #777;} QPushButton:checked {background: #555;}")
        row = QGridLayout(self.toolbar)
        column = 0
        self.tools = QButtonGroup(self)
        self.tool_buttons = {}
        for tool in ("Select", "Draw", "Line", "Arrow", "Circle", "Box", "Numbering", "Text overlay"):
            button = self._toolbar_button(tool)
            button.setCheckable(True)
            button.setChecked(tool == self.tool)
            button.clicked.connect(lambda checked=False, name=tool: self._set_tool(name))
            self.tools.addButton(button)
            self.tool_buttons[tool] = button
            row.addWidget(button, column // 8, column % 8)
            column += 1
        for label, callback in (("Color", self.choose_color), ("Undo", self.undo),
                                ("Copy (Ctrl+C)", self.copy_image), ("Attach (Ctrl+S)", self.save_attachment),
                                ("Cancel", self.close)):
            button = self._toolbar_button(label)
            button.clicked.connect(callback)
            row.addWidget(button, column // 8, column % 8)
            column += 1
        self.toolbar.hide()
        self.text_frame = QWidget(self)
        self.text_frame.setStyleSheet("background: transparent; border: 1px dashed #00bfff;")
        self.text_editor = CanvasTextEdit(self, self.text_frame)
        self.text_editor.textChanged.connect(self._layout_text_editor)
        self.text_smaller = QPushButton("−", self.text_frame)
        self.text_larger = QPushButton("+", self.text_frame)
        for button, delta in ((self.text_smaller, -2), (self.text_larger, 2)):
            button.setFixedSize(22, 22)
            button.setFocusPolicy(Qt.NoFocus)
            button.setStyleSheet("background: #252525; color: white; border: 1px solid #777;")
            button.clicked.connect(lambda checked=False, step=delta: self._change_text_size(step))
        self.text_smaller.setToolTip("Decrease text size (Ctrl/Cmd+scroll)")
        self.text_larger.setToolTip("Increase text size (Ctrl/Cmd+scroll)")
        self.text_smaller.setAccessibleName("Decrease text size")
        self.text_larger.setAccessibleName("Increase text size")
        self.text_frame.hide()
        self.setFocusPolicy(Qt.StrongFocus)
        self.hint = QLabel("Drag to select a region · Esc to cancel", self)
        self.hint.setStyleSheet("background: #252525; color: white; padding: 8px;")
        self.hint.adjustSize()
        self.hint.move(20, 20)
        for key, callback in (("Ctrl+C", self.copy_image), ("Ctrl+S", self.save_attachment),
                              ("Ctrl+Z", self.undo), ("Escape", self.close),
                              ("P", self.choose_color), ("U", self.undo)):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.activated.connect(callback)

        for tool, key in TOOL_KEYS.items():
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.activated.connect(lambda name=tool: self._set_tool(name))
        self._save_keys = ImageSaveKeyFilter(self, lambda: self.save_attachment())

    def _toolbar_button(self, label):
        button = QPushButton()
        key = TOOL_KEYS.get(label) or ACTION_KEYS.get(label)
        button.setToolTip(f"{label} ({key})" if key else label)
        button.setAccessibleName(label)
        asset = TOOLBAR_ICONS.get(label)
        if asset:
            icon = QIcon(str(ASSETS / asset))
            if label != "Color":
                # Monochrome assets need light strokes on the dark capture bar.
                pixmap = icon.pixmap(QSize(24, 24))
                painter = QPainter(pixmap)
                painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
                painter.fillRect(pixmap.rect(), QColor("#ffffff"))
                painter.end()
                icon = QIcon(pixmap)
            button.setIcon(icon)
            button.setIconSize(QSize(24, 24))
            button.setFixedSize(40, 40)
        else:
            button.setText(label)
            button.setMinimumHeight(40)
        return button

    def _set_tool(self, tool):
        self._finish_text()
        self._finish_move()
        self._anchor = None
        self._selected_annotation = None
        self.tool = tool
        self.setCursor(Qt.ArrowCursor if tool == "Select" else Qt.CrossCursor)
        self.tool_buttons[tool].setChecked(True)
        self.update()

    def _begin_text(self, point, index=None):
        self._finish_text()
        self._finish_move()
        self._anchor = None
        self._selected_annotation = None
        self._text_before = list(self.annotations)
        self._text_index = index
        self._text_start = QPointF(point)
        self._text_color = QColor(self.color)
        payload = TextAnnotation("", self._text_size)
        if index is not None:
            _, self._text_start, _, self._text_color, value = self.annotations[index]
            payload = _text_data(value)
            self._text_size = payload.size
        for shortcut in self.findChildren(QShortcut):
            shortcut.setEnabled(False)
        self.text_editor.setPlainText(payload.text)
        self._layout_text_editor()
        self.text_frame.show()
        self.text_frame.raise_()
        self.text_editor.setFocus()
        cursor = self.text_editor.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.text_editor.setTextCursor(cursor)
        self.update()

    def _layout_text_editor(self):
        if self._text_start is None:
            return
        payload = TextAnnotation(self.text_editor.toPlainText(), self._text_size)
        font = _text_font(payload)
        self.text_editor.setFont(font)
        self.text_editor.setStyleSheet(f"background: transparent; color: {self._text_color.name()}; border: none;")
        metrics = QFontMetricsF(font)
        lines = payload.text.split("\n")
        width = max(90, math.ceil(max((metrics.horizontalAdvance(line) for line in lines), default=0)) + 12)
        height = math.ceil(metrics.lineSpacing() * len(lines)) + 8
        x = max(self.selection.left(), round(self._text_start.x()) - 4)
        y = max(self.selection.top(), round(self._text_start.y() - metrics.ascent()) - 26)
        width = max(50, min(width, self.selection.right() - x + 1))
        height = max(24, min(height, self.selection.bottom() - y - 26))
        self.text_frame.setGeometry(x, y, width, height + 28)
        self.text_editor.setGeometry(4, 26, max(1, width-8), height)
        self.text_smaller.move(max(0, width-48), 2)
        self.text_larger.move(max(24, width-24), 2)
        # The stored baseline matches the editor's text position after clamping.
        self._text_start = QPointF(x+4, y+26+metrics.ascent())

    def _finish_text(self):
        if self._text_start is None:
            return
        text = self.text_editor.toPlainText()
        if text:
            annotation = ("Text overlay", QPointF(self._text_start), QPointF(self._text_start),
                          QColor(self._text_color), TextAnnotation(text, self._text_size))
            if self._text_index is None:
                self.annotations.append(annotation)
            else:
                self.annotations[self._text_index] = annotation
        elif self._text_index is not None:
            self.annotations.pop(self._text_index)
        if self.annotations != self._text_before:
            self._history.append(self._text_before)
        self._text_start = None
        self._text_index = None
        self._text_before = None
        self.text_frame.hide()
        for shortcut in self.findChildren(QShortcut):
            shortcut.setEnabled(True)
        self.setFocus()
        self.update()

    def _change_text_size(self, delta):
        self._text_size = max(8, min(144, self._text_size + delta))
        self._layout_text_editor()
        if self._text_start is not None:
            self.text_editor.setFocus()

    def _resize_text_from_wheel(self, event):
        delta = event.angleDelta().y() or event.pixelDelta().y()
        if delta:
            self._change_text_size(2 if delta > 0 else -2)
        event.accept()

    def wheelEvent(self, event):
        if self._text_start is not None and event.modifiers() & (Qt.ControlModifier | Qt.MetaModifier):
            self._resize_text_from_wheel(event)
        else:
            super().wheelEvent(event)

    def mouseDoubleClickEvent(self, event):
        if self._selected and event.button() == Qt.LeftButton and self.selection.contains(event.position().toPoint()):
            index = self._hit_annotation(event.position())
            if index is not None and self.annotations[index][0] == "Text overlay":
                self._begin_text(event.position(), index)
                return
        super().mouseDoubleClickEvent(event)

    def choose_color(self):
        color = QColorDialog.getColor(self.color, self, "Annotation color")
        if color.isValid():
            self.color = color
            self.settings.setValue("screenCapture/color", color.name(QColor.HexArgb))

    def undo(self):
        self._finish_text()
        self._finish_move()
        self._anchor = None
        self._selected_annotation = None
        if self._history:
            self.annotations = self._history.pop()
        elif self.annotations:
            self.annotations.pop()
        self.update()

    @staticmethod
    def _annotation_path(annotation):
        tool, start, end, color, text = annotation
        path = QPainterPath()
        rect = QRectF(start, end).normalized()
        if tool == "Draw":
            if len(text) > 1:
                path.moveTo(text[0])
                for point in text[1:]:
                    path.lineTo(point)
            else:
                path.addEllipse(start, 1.5, 1.5)
        elif tool in ("Line", "Arrow"):
            path.moveTo(start)
            path.lineTo(end)
            if tool == "Arrow":
                angle = math.atan2(end.y() - start.y(), end.x() - start.x())
                path.addPolygon(QPolygonF([end, end - QPointF(math.cos(angle - .5) * 16, math.sin(angle - .5) * 16),
                                                end - QPointF(math.cos(angle + .5) * 16, math.sin(angle + .5) * 16)]))
                path.closeSubpath()
        elif tool == "Circle":
            path.addEllipse(rect)
        elif tool == "Box":
            path.addRect(rect)
        elif tool == "Numbering":
            path.addEllipse(start, 16, 16)
        elif tool == "Text overlay":
            data = _text_data(text)
            metrics = QFontMetricsF(_text_font(data))
            for number, line in enumerate(data.text.split("\n")):
                path.addRect(metrics.boundingRect(line).translated(start + QPointF(0, number * metrics.lineSpacing())))
        return path

    def _hit_annotation(self, point):
        stroker = QPainterPathStroker()
        stroker.setWidth(12)
        # The last drawn element is visually on top and gets picked first.
        for index in range(len(self.annotations) - 1, -1, -1):
            path = self._annotation_path(self.annotations[index])
            annotation = self.annotations[index]
            filled = annotation[0] != "Draw" or len(annotation[4]) == 1
            if ((filled and path.contains(point))
                    or stroker.createStroke(path).contains(point)):
                return index
        return None

    def _finish_move(self):
        if self._drag_snapshot is not None and self.annotations != self._drag_snapshot:
            self._history.append(self._drag_snapshot)
        self._drag_original = None
        self._drag_snapshot = None

    def _move_annotation(self, point):
        if self._drag_original is None:
            return
        delta = point - self._anchor
        bounds = self._annotation_path(self._drag_original).boundingRect().adjusted(-1.5, -1.5, 1.5, 1.5)
        region = QRectF(self.selection)
        # Keep the entire element in the screenshot where it fits. Oversized
        # text can still move, with its visible extent bounded by the region.
        def clamp(value, low, high):
            return max(min(low, high), min(max(low, high), value))
        delta = QPointF(clamp(delta.x(), region.left() - bounds.left(), region.right() - bounds.right()),
                        clamp(delta.y(), region.top() - bounds.top(), region.bottom() - bounds.bottom()))
        tool, start, end, color, text = self._drag_original
        if tool == "Draw":
            text = tuple(point + delta for point in text)
        self.annotations[self._selected_annotation] = (tool, start + delta, end + delta, color, text)

    @staticmethod
    def draw_annotation(painter, annotation):
        tool, start, end, color, text = annotation
        painter.setPen(QPen(color, 3, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.setBrush(Qt.NoBrush)
        rect = QRectF(start, end).normalized()
        if tool == "Draw":
            if len(text) > 1:
                painter.drawPath(ScreenCaptureOverlay._annotation_path(annotation))
            else:
                painter.drawPoint(start)
        elif tool in ("Line", "Arrow"):
            painter.drawLine(start, end)
            if tool == "Arrow":
                angle = math.atan2(end.y() - start.y(), end.x() - start.x())
                painter.setBrush(color)
                painter.drawPolygon(QPolygonF([end, end - QPointF(math.cos(angle - .5) * 16, math.sin(angle - .5) * 16),
                                                       end - QPointF(math.cos(angle + .5) * 16, math.sin(angle + .5) * 16)]))
        elif tool == "Circle":
            painter.drawEllipse(rect)
        elif tool == "Box":
            painter.drawRect(rect)
        else:
            painter.setFont(QFont("Sans Serif", 18, QFont.Bold))
            if tool == "Numbering":
                painter.setBrush(color)
                painter.drawEllipse(start, 16, 16)
                painter.setPen(Qt.white)
                painter.drawText(QRectF(start.x()-16, start.y()-16, 32, 32), Qt.AlignCenter, text)
            else:
                data = _text_data(text)
                painter.setFont(_text_font(data))
                metrics = QFontMetricsF(painter.font())
                for number, line in enumerate(data.text.split("\n")):
                    painter.drawText(start + QPointF(0, number * metrics.lineSpacing()), line)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.drawPixmap(self.rect(), self.desktop)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 145))
        if not self.selection.isEmpty():
            painter.save()
            painter.setClipRect(self.selection)
            painter.drawPixmap(self.rect(), self.desktop)
            painter.setRenderHint(QPainter.Antialiasing)
            for index, annotation in enumerate(self.annotations):
                if self._text_start is None or index != self._text_index:
                    self.draw_annotation(painter, annotation)
            if self.tool == "Select" and self._selected_annotation is not None:
                bounds = self._annotation_path(self.annotations[self._selected_annotation]).boundingRect().adjusted(-5, -5, 5, 5)
                painter.setBrush(Qt.NoBrush)
                painter.setPen(QPen(QColor("#00bfff"), 1, Qt.DashLine))
                painter.drawRect(bounds)
            if self._selected and self._anchor is not None and self.tool != "Select":
                payload = tuple(self._stroke_points) if self.tool == "Draw" else ""
                self.draw_annotation(painter, (self.tool, self._anchor, self._current, self.color, payload))
            painter.restore()
            painter.setPen(QPen(Qt.white, 1, Qt.DashLine))
            painter.drawRect(self.selection.adjusted(0, 0, -1, -1))

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        if self._text_start is not None:
            self._set_tool("Select")
            return
        point = event.position().toPoint()
        if self._selected and not self.selection.contains(point):
            return
        self._anchor = QPointF(point)
        self._current = self._anchor
        self._stroke_points = [self._anchor]
        if self._selected and self.tool == "Select":
            self._selected_annotation = self._hit_annotation(QPointF(point))
            if self._selected_annotation is not None:
                self._drag_original = self.annotations[self._selected_annotation]
                self._drag_snapshot = list(self.annotations)
            else:
                self._anchor = None
            self.update()
            return
        if self._selected and self.tool == "Text overlay":
            index = self._hit_annotation(QPointF(point))
            if index is not None and self.annotations[index][0] != "Text overlay":
                index = None
            self._begin_text(point, index)
            return
        if self._selected and self.tool == "Numbering":
            text = str(1 + sum(a[0] == "Numbering" for a in self.annotations))
            self._history.append(list(self.annotations))
            self.annotations.append((self.tool, self._anchor, self._anchor, QColor(self.color), text))
            self._anchor = None
            self.update()

    def mouseMoveEvent(self, event):
        if self._anchor is None:
            return
        if self._selected and self.tool == "Select":
            self._move_annotation(event.position())
            self.update()
            return
        point = event.position().toPoint()
        bounds = self.selection if self._selected else self.rect()
        point = QPoint(max(bounds.left(), min(bounds.right(), point.x())),
                       max(bounds.top(), min(bounds.bottom(), point.y())))
        self._current = QPointF(point)
        if self._selected and self.tool == "Draw" and self._stroke_points[-1] != self._current:
            self._stroke_points.append(self._current)
        if not self._selected:
            self.selection = QRect(self._anchor.toPoint(), point).normalized().intersected(self.rect())
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton or self._anchor is None:
            return
        self.mouseMoveEvent(event)
        if self._selected and self.tool == "Select":
            self._finish_move()
        elif self._selected:
            self._history.append(list(self.annotations))
            payload = tuple(self._stroke_points) if self.tool == "Draw" else ""
            self.annotations.append((self.tool, self._anchor, self._current, QColor(self.color), payload))
        elif self.selection.width() >= 3 and self.selection.height() >= 3:
            self._selected = True
            self.hint.hide()
            self.toolbar.adjustSize()
            self.toolbar.move(max(0, min(self.selection.left(), self.width()-self.toolbar.width())),
                              max(0, min(self.selection.bottom()+8, self.height()-self.toolbar.height())))
            self.toolbar.show()
        self._anchor = None
        self.update()

    def rendered_image(self):
        self._finish_text()
        if not self._selected:
            return None
        scale = self.desktop.devicePixelRatio()
        image = self.desktop.toImage().copy(QRect(int(self.selection.x()*scale), int(self.selection.y()*scale),
                                                int(self.selection.width()*scale), int(self.selection.height()*scale)))
        image.setDevicePixelRatio(scale)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.translate(-self.selection.x(), -self.selection.y())
        painter.setClipRect(self.selection)
        for annotation in self.annotations:
            self.draw_annotation(painter, annotation)
        painter.end()
        return image

    def copy_image(self):
        image = self.rendered_image()
        if image is not None:
            QApplication.clipboard().setImage(image)
            self.close()

    def save_attachment(self):
        image = self.rendered_image()
        if image is None:
            return
        if self.save_image is None:
            QMessageBox.information(self, "Capture screen", "Open a writable page to save this capture as an attachment. You can still copy it with Ctrl+C.")
            return
        try:
            name = self.save_image(image)
        except Exception as exc:
            QMessageBox.warning(self, "Could not save capture", str(exc))
            return
        self.attachmentSaved.emit(name)
        self.close()


def capture_desktop():
    screens = QGuiApplication.screens()
    geometry = QRect()
    for screen in screens:
        geometry = geometry.united(screen.geometry())
    # Use the highest scale to retain detail on mixed-DPI desktops.
    scale = max((s.devicePixelRatio() for s in screens), default=1)
    desktop = QPixmap(round(geometry.width()*scale), round(geometry.height()*scale))
    desktop.setDevicePixelRatio(scale)
    desktop.fill(Qt.black)
    painter = QPainter(desktop)
    for screen in screens:
        pixmap = screen.grabWindow(0)
        if pixmap.isNull():
            painter.end()
            raise RuntimeError("The desktop did not allow screen capture. Check screen recording permissions.")
        painter.drawPixmap(screen.geometry().translated(-geometry.topLeft()), pixmap)
    painter.end()
    return desktop, geometry


class _PortalScreenshotWorker(QThread):
    ready = Signal(str)
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loop = None
        self._task = None

    def cancel(self):
        self.requestInterruption()
        if self._loop is not None and self._task is not None:
            try:
                self._loop.call_soon_threadsafe(self._task.cancel)
            except RuntimeError:
                pass
        self.wait(3000)

    def run(self):
        import asyncio
        asyncio.run(self._capture())

    async def _capture(self):
        import asyncio
        import uuid
        from dbus_next import Message, MessageType, Variant
        from dbus_next.aio import MessageBus
        bus = None
        try:
            self._loop = asyncio.get_running_loop()
            self._task = asyncio.current_task()
            if self.isInterruptionRequested():
                return
            bus = await MessageBus().connect()
            token = "capture_" + uuid.uuid4().hex
            request_path = "/org/freedesktop/portal/desktop/request/" + bus.unique_name[1:].replace('.', '_') + "/" + token
            response = asyncio.get_running_loop().create_future()
            def received(message):
                if (message.path == request_path and message.interface == "org.freedesktop.portal.Request"
                        and message.member == "Response" and not response.done()):
                    response.set_result(message.body)
            bus.add_message_handler(received)
            reply = await bus.call(Message(destination="org.freedesktop.DBus", path="/org/freedesktop/DBus",
                interface="org.freedesktop.DBus", member="AddMatch", signature="s",
                body=[f"type='signal',interface='org.freedesktop.portal.Request',path='{request_path}'"]))
            if reply.message_type == MessageType.ERROR:
                raise RuntimeError(reply.body[0])
            reply = await bus.call(Message(destination="org.freedesktop.portal.Desktop",
                path="/org/freedesktop/portal/desktop", interface="org.freedesktop.portal.Screenshot",
                member="Screenshot", signature="sa{sv}", body=["", {
                    "handle_token": Variant("s", token), "interactive": Variant("b", False)}]))
            if reply.message_type == MessageType.ERROR:
                raise RuntimeError(reply.body[0])
            code, result = await asyncio.wait_for(response, 120)
            if code != 0:
                raise RuntimeError("Screen capture was cancelled or denied by the desktop.")
            self.ready.emit(result["uri"].value)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self.failed.emit(str(exc) or "The desktop screenshot request timed out.")
        finally:
            if bus is not None:
                bus.disconnect()


def start_capture(parent, save_image, opened):
    from PySide6.QtCore import QTimer, QUrl
    # Give command palette/popups time to disappear before freezing the desktop.
    def show(desktop, geometry):
        overlay = ScreenCaptureOverlay(desktop, geometry, save_image)
        opened(overlay)
        overlay.show()
        overlay.raise_()
        overlay.activateWindow()
    def failed(message):
        QMessageBox.warning(parent, "Screen capture unavailable", message)
    def begin():
        if QGuiApplication.platformName().lower().startswith("wayland"):
            worker = _PortalScreenshotWorker(QApplication.instance())
            QApplication.instance().aboutToQuit.connect(worker.cancel)
            parent._screenshot_worker = worker
            def ready(uri):
                desktop = QPixmap(QUrl(uri).toLocalFile())
                if desktop.isNull():
                    failed("The desktop returned an unreadable screenshot.")
                    return
                geometry = QRect()
                for screen in QGuiApplication.screens():
                    geometry = geometry.united(screen.geometry())
                desktop.setDevicePixelRatio(desktop.width() / max(1, geometry.width()))
                show(desktop, geometry)
            worker.ready.connect(ready)
            worker.failed.connect(failed)
            worker.finished.connect(worker.deleteLater)
            worker.finished.connect(lambda: setattr(parent, "_screenshot_worker", None))
            worker.start()
        else:
            try:
                show(*capture_desktop())
            except Exception as exc:
                failed(str(exc))
    if getattr(parent, "_screenshot_pending", False):
        return
    parent._screenshot_pending = True
    def launch():
        parent._screenshot_pending = False
        if getattr(parent, "_screenshot_worker", None) is None:
            begin()
    QTimer.singleShot(200, launch)
