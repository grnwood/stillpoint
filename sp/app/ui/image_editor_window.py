"""Windowed image annotation using the screen capture canvas and tools."""
from pathlib import Path
import mimetypes

from PySide6.QtCore import QBuffer, QIODevice, QRect, QSaveFile, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QImageReader, QImageWriter, QKeySequence, QPainter, QPixmap, QShortcut, QTransform
from PySide6.QtWidgets import (QApplication, QFileDialog, QHBoxLayout, QLabel, QMainWindow, QGraphicsScene, QGraphicsView,
    QMessageBox, QPushButton, QVBoxLayout, QWidget)

from .screen_capture import ASSETS, ImageSaveKeyFilter, ScreenCaptureOverlay


def _light_icon(asset, size=24):
    pixmap = QIcon(str(ASSETS / asset)).pixmap(QSize(size, size))
    painter = QPainter(pixmap)
    painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
    painter.fillRect(pixmap.rect(), Qt.white)
    painter.end()
    return QIcon(pixmap)


def get_image_editor_icon():
    """Stillpoint with an image badge for the OS window switcher."""
    base = QIcon(str(ASSETS / "icons" / "linux-png" / "stillpoint-512x512.png"))
    icon = QIcon()
    for size in (32, 48, 64, 128, 256):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.drawPixmap(0, 0, base.pixmap(QSize(size, size)))
        badge = round(size * .46)
        offset = size - badge
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#18312d"))
        painter.drawRoundedRect(offset, offset, badge, badge, size*.06, size*.06)
        inset = max(2, round(size*.04))
        mark = _light_icon("show-thumbnails.svg", badge-2*inset)
        painter.drawPixmap(offset+inset, offset+inset, mark.pixmap(QSize(badge-2*inset, badge-2*inset)))
        painter.end()
        icon.addPixmap(pixmap)
    return icon


def _write_image(image, path, image_format):
    output = QSaveFile(str(path))
    if not output.open(QIODevice.WriteOnly):
        raise OSError(output.errorString())
    if not image.save(output, image_format):
        output.cancelWriting()
        raise OSError(f"Could not encode {image_format} image.")
    if not output.commit():
        raise OSError(output.errorString())


def remote_image_saver(client, remote_path, page_path, auth_prompt=None):
    """Keep the remote destination fixed even after the active page changes."""
    def save(image, cache_path, image_format):
        buffer = QBuffer()
        buffer.open(QIODevice.WriteOnly)
        if not image.save(buffer, image_format):
            raise OSError(f"Could not encode {image_format} image.")
        filename = Path(remote_path).name
        payload = bytes(buffer.data())
        def upload():
            return client.post("/files/attach", data={"page_path": page_path},
                               files={"files": (filename, payload, mimetypes.guess_type(filename)[0] or "application/octet-stream")})
        response = upload()
        if response.status_code == 401 and auth_prompt and auth_prompt():
            response = upload()
        response.raise_for_status()
        _write_image(image, cache_path, image_format)
    return save


class ImageCanvas(ScreenCaptureOverlay):
    def __init__(self, pixmap, editor):
        super().__init__(pixmap, QRect(0, 0, pixmap.width(), pixmap.height()))
        self.editor_window = editor
        self.setWindowFlags(Qt.Widget)
        self.setFixedSize(pixmap.size())
        self.selection = self.rect()
        self._selected = True
        self.hint.hide()
        self._set_tool("Select")
        # Saving/copying belong to the popup window, including when its toolbar
        # has focus. Avoid duplicate shortcuts on the embedded capture widget.
        for shortcut in self.findChildren(QShortcut):
            if shortcut.key() in (QKeySequence("Ctrl+S"), QKeySequence("Ctrl+C")):
                shortcut.setKey(QKeySequence())

    def copy_image(self):
        QApplication.clipboard().setImage(self.rendered_image())
        self.editor_window.statusBar().showMessage("Image copied", 3000)

    def save_attachment(self):
        self.editor_window.save_changes()

    def close(self):
        return self.editor_window.close()


class ImageView(QGraphicsView):
    def __init__(self, editor):
        super().__init__()
        self.editor = editor
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setAlignment(Qt.AlignCenter)
        self.setTransformationAnchor(QGraphicsView.AnchorViewCenter)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setScene(QGraphicsScene(self))
        self.proxy = self.scene().addWidget(editor.canvas)
        self.setSceneRect(self.proxy.boundingRect())

    def wheelEvent(self, event):
        if event.modifiers() & (Qt.ControlModifier | Qt.MetaModifier):
            if self.editor.canvas._text_start is not None:
                self.editor.canvas._resize_text_from_wheel(event)
            elif self.editor.canvas.tool == "Highlight":
                self.editor.canvas._resize_highlight_from_wheel(event)
            elif self.editor.canvas.tool == "Pixelate":
                self.editor.canvas._resize_pixelate_from_wheel(event)
            else:
                delta = event.angleDelta().y() or event.pixelDelta().y()
                if delta:
                    self.editor.set_zoom(self.editor.zoom * (1.2 if delta > 0 else 1/1.2), event.position())
                event.accept()
            return
        super().wheelEvent(event)


class ImageEditorWindow(QMainWindow):
    saved = Signal(str)

    def __init__(self, path, parent=None, *, read_only=False, save_callback=None):
        super().__init__(parent, Qt.Window)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowIcon(get_image_editor_icon())
        self.path = Path(path).resolve()
        self.read_only = read_only
        self._save_callback = save_callback
        reader = QImageReader(str(self.path))
        reader.setAutoTransform(True)
        image = reader.read()
        if image.isNull():
            raise OSError(reader.errorString() or "Could not load this image.")
        suffix = self.path.suffix.lower().lstrip(".")
        writable_formats = {bytes(fmt).decode().lower() for fmt in QImageWriter.supportedImageFormats()}
        self._can_overwrite = (suffix in writable_formats and suffix not in {"svg", "gif", "ico"}
                               and not reader.supportsAnimation())
        self._saved_annotations = []
        self.zoom = 1.0
        self.canvas = ImageCanvas(QPixmap.fromImage(image), self)
        body = QWidget()
        layout = QVBoxLayout(body)
        header = QHBoxLayout()
        self.path_label = QLabel(str(self.path))
        self.path_label.setTextFormat(Qt.PlainText)
        self.path_label.setWordWrap(True)
        header.addWidget(self.path_label, 1)
        layout.addLayout(header)
        # Keep every image-editing action on one row outside the canvas.
        self.canvas.toolbar.setParent(body)
        tools_layout = self.canvas.toolbar.layout()
        buttons = []
        while tools_layout.count():
            item = tools_layout.takeAt(0)
            if item.widget() is not None:
                buttons.append(item.widget())
        self.save_as_button = QPushButton(self.canvas.toolbar)
        self.save_as_button.setIcon(_light_icon("save-as.svg"))
        self.save_as_button.setIconSize(QSize(24, 24))
        self.save_as_button.setFixedSize(40, 40)
        self.save_as_button.setToolTip("Save As (Ctrl/Cmd+Shift+S)")
        self.save_as_button.setAccessibleName("Save As")
        self.save_as_button.clicked.connect(self.save_as)
        for button in buttons:
            if button.accessibleName() == "Attach (Ctrl+S)":
                self.save_button = button
                button.setIcon(_light_icon("save.svg"))
                button.setToolTip("Save image (Ctrl/Cmd+S)")
                button.setAccessibleName("Save image")
        buttons.insert(len(buttons)-1, self.save_as_button)
        self.zoom_out_button = QPushButton("−", self.canvas.toolbar)
        self.zoom_in_button = QPushButton("+", self.canvas.toolbar)
        for button, title, factor in ((self.zoom_out_button, "Zoom out", 1/1.2),
                                      (self.zoom_in_button, "Zoom in", 1.2)):
            button.setFixedSize(40, 40)
            button.setAccessibleName(title)
            button.setToolTip(title + " (Ctrl/Cmd+scroll)")
            button.clicked.connect(lambda checked=False, amount=factor: self.set_zoom(self.zoom * amount))
        self.zoom_label = QLabel("100%", self.canvas.toolbar)
        self.zoom_label.setAlignment(Qt.AlignCenter)
        self.zoom_label.setMinimumWidth(46)
        buttons.extend((self.zoom_out_button, self.zoom_label, self.zoom_in_button))
        for column, button in enumerate(buttons):
            tools_layout.addWidget(button, 0, column)
        layout.addWidget(self.canvas.toolbar, 0, Qt.AlignLeft)
        self.canvas.toolbar.show()
        self.scroll = ImageView(self)
        layout.addWidget(self.scroll, 1)
        self.setCentralWidget(body)
        for title, sequence, callback in (("Save", QKeySequence.Save, self.save_changes),
                                           ("Save As", QKeySequence.SaveAs, self.save_as),
                                           ("Copy", QKeySequence.Copy, self.canvas.copy_selection_or_image),
                                           ("Paste item", QKeySequence.Paste, self.canvas.paste_annotation)):
            action = QAction(title, self)
            action.setShortcut(sequence)
            self.addAction(action)
            action.triggered.connect(callback)
        for sequence, callback in ((QKeySequence.ZoomIn, lambda: self.set_zoom(self.zoom * 1.2)),
                                   (QKeySequence.ZoomOut, lambda: self.set_zoom(self.zoom / 1.2)),
                                   ("Ctrl+=", lambda: self.set_zoom(self.zoom * 1.2)),
                                   ("Ctrl+0", lambda: self.set_zoom(1.0))):
            action = QAction(self)
            action.setShortcut(QKeySequence(sequence))
            action.triggered.connect(callback)
            self.addAction(action)
        self._save_keys = ImageSaveKeyFilter(self, lambda: self.save_changes(), self.canvas)
        self.resize(1100, 760)
        self._update_title()
        self.statusBar().showMessage("Annotate with the toolbar · Ctrl/Cmd+S to save · Ctrl/Cmd+C to copy")

    def set_zoom(self, zoom, viewport_position=None):
        zoom = max(.1, min(8.0, zoom))
        before = self.scroll.mapToScene(viewport_position.toPoint()) if viewport_position is not None else None
        self.zoom = zoom
        self.scroll.setTransform(QTransform.fromScale(zoom, zoom))
        if before is not None:
            after = self.scroll.mapToScene(viewport_position.toPoint())
            center = self.scroll.mapToScene(self.scroll.viewport().rect().center())
            self.scroll.centerOn(center + before - after)
        self.zoom_label.setText(f"{round(zoom * 100)}%")
        self.zoom_out_button.setEnabled(zoom > .1)
        self.zoom_in_button.setEnabled(zoom < 8.0)

    def _update_title(self):
        self.setWindowTitle(f"Edit image — {self.path.name}" + (" (read only)" if self.read_only else ""))
        self.path_label.setText(str(self.path))

    def _save_to(self, target, *, original=False):
        try:
            image = self.canvas.rendered_image()
            suffix = target.suffix.lower().lstrip(".")
            image_format = {"jpg": "JPEG", "tif": "TIFF"}.get(suffix, suffix.upper())
            if image_format.lower().encode() not in {bytes(fmt).lower() for fmt in QImageWriter.supportedImageFormats()}:
                raise OSError("Choose a supported raster format such as PNG or JPEG.")
            if original and self.read_only:
                raise OSError("This image is read only. Use Save As to save a copy.")
            if original and self._save_callback is not None:
                self._save_callback(image, target, image_format)
            else:
                _write_image(image, target, image_format)
            self._saved_annotations = list(self.canvas.annotations)
            self.statusBar().showMessage(f"Saved {target.name}", 4000)
            self.saved.emit(str(target))
            refresh_open_image_resources(target)
            return True
        except Exception as exc:
            QMessageBox.warning(self, "Could not save image", str(exc))
            return False

    def save_changes(self):
        if self.read_only or not self._can_overwrite:
            return self.save_as()
        return self._save_to(self.path, original=True)

    def save_as(self):
        filename, _ = QFileDialog.getSaveFileName(self, "Save edited image", str(self.path.with_name(self.path.stem + "-edited.png")),
                                                 "PNG (*.png);;JPEG (*.jpg *.jpeg);;WebP (*.webp);;Bitmap (*.bmp)")
        if not filename:
            return False
        target = Path(filename).resolve()
        if not target.suffix:
            target = target.with_suffix(".png")
        if target == self.path:
            if self.read_only or not self._can_overwrite:
                QMessageBox.warning(self, "Save a copy", "Choose a different filename to preserve the original image.")
                return False
            return self._save_to(target, original=True)
        if self._save_to(target):
            self.path = target
            self._save_callback = None
            self.read_only = False
            self._can_overwrite = True
            self._update_title()
            return True
        return False

    def closeEvent(self, event):
        self.canvas._finish_text()
        if self.canvas.annotations != self._saved_annotations:
            choice = QMessageBox.question(self, "Save image changes?", "Save changes to this image?",
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Save)
            if choice == QMessageBox.Cancel or (choice == QMessageBox.Save and not self.save_changes()):
                event.ignore()
                return
        event.accept()


def refresh_open_image_resources(path):
    """Refresh Qt's document image cache without reloading page text or undo."""
    from PySide6.QtGui import QImage, QTextDocument
    from .markdown_editor import MarkdownEditor
    image = QImage(str(path))
    if image.isNull():
        return
    for widget in QApplication.allWidgets():
        if isinstance(widget, MarkdownEditor):
            document = widget.document()
            block = document.begin()
            names = set()
            while block.isValid():
                iterator = block.begin()
                while not iterator.atEnd():
                    fragment = iterator.fragment()
                    if fragment.isValid() and fragment.charFormat().isImageFormat():
                        name = fragment.charFormat().toImageFormat().name()
                        if Path(name).resolve() == Path(path).resolve():
                            names.add(name)
                    iterator += 1
                block = block.next()
            for name in names:
                document.addResource(QTextDocument.ImageResource, QUrl(name), image)
            if names:
                document.markContentsDirty(0, document.characterCount())
                widget.viewport().update()


def open_image_editor(path, parent=None, *, read_only=False, save_callback=None, on_saved=None):
    app = QApplication.instance()
    windows = getattr(app, "_stillpoint_image_editors", None)
    if windows is None:
        windows = app._stillpoint_image_editors = {}
    key = str(Path(path).resolve())
    existing = next((window for window in windows.values() if str(window.path) == key), None)
    if existing is not None:
        existing.showNormal()
        existing.raise_()
        existing.activateWindow()
        return existing
    try:
        window = ImageEditorWindow(path, parent.window() if parent else None,
                                   read_only=read_only, save_callback=save_callback)
    except (OSError, RuntimeError) as exc:
        QMessageBox.warning(parent, "Image editor unavailable", str(exc))
        return None
    identifier = id(window)
    windows[identifier] = window
    window.destroyed.connect(lambda: windows.pop(identifier, None))
    if on_saved is not None:
        window.saved.connect(on_saved)
    window.show()
    window.canvas.setFocus()
    return window
