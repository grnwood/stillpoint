"""Bounded background image decoding and a thumbnail cache outside the vault."""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import hashlib
from pathlib import Path
import time

import httpx
from PySide6.QtCore import (
    QBuffer, QByteArray, QIODevice, QObject, QRunnable, QSaveFile,
    QSize, QStandardPaths, QThreadPool, Signal, Slot, Qt,
)
from PySide6.QtGui import QImage, QImageReader

THUMBNAIL_SIZE = QSize(192, 192)
MAX_REMOTE_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True)
class ThumbnailSource:
    identity: str
    revision: str
    path: Path | None = None
    client: object = None
    remote_path: str | None = None

    @property
    def key(self) -> str:
        return hashlib.sha256(f"v1\0{self.identity}\0{self.revision}".encode()).hexdigest()


def thumbnail_cache_root() -> Path:
    return Path(QStandardPaths.writableLocation(QStandardPaths.GenericCacheLocation)) / "StillPoint" / "attachment-thumbnails"


def _read_image(reader: QImageReader) -> QImage:
    reader.setAutoTransform(True)
    size = reader.size()
    if size.isValid() and (size.width() > THUMBNAIL_SIZE.width() or size.height() > THUMBNAIL_SIZE.height()):
        size.scale(THUMBNAIL_SIZE, Qt.KeepAspectRatio)
        reader.setScaledSize(size)
    image = reader.read()
    if image.width() > THUMBNAIL_SIZE.width() or image.height() > THUMBNAIL_SIZE.height():
        image = image.scaled(THUMBNAIL_SIZE, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    return image


def load_thumbnail(source: ThumbnailSource, cache_root: Path) -> QImage:
    """Run on a worker thread; never create a QPixmap or touch a widget here."""
    try:
        if source.path is not None:
            stat = source.path.stat()
            if f"{stat.st_mtime_ns}:{stat.st_size}" != source.revision:
                return QImage()
        cached = cache_root / f"{source.key}.png"
        if cached.is_file():
            image = _read_image(QImageReader(str(cached)))
            if not image.isNull():
                return image
        if source.path is not None:
            image = _read_image(QImageReader(str(source.path)))
            stat = source.path.stat()
            if f"{stat.st_mtime_ns}:{stat.st_size}" != source.revision:
                return QImage()
        else:
            content = bytearray()
            deadline = time.monotonic() + 15
            with source.client.stream("GET", "/api/file/raw", params={"path": source.remote_path}, timeout=5) as response:
                response.raise_for_status()
                for chunk in response.iter_bytes(64 * 1024):
                    content.extend(chunk)
                    if len(content) > MAX_REMOTE_BYTES or time.monotonic() > deadline:
                        return QImage()
            buffer = QBuffer()
            buffer.setData(QByteArray(bytes(content)))
            buffer.open(QIODevice.ReadOnly)
            image = _read_image(QImageReader(buffer))
        if image.isNull():
            return image
        try:
            cache_root.mkdir(parents=True, exist_ok=True)
            output = QSaveFile(str(cached))
            if output.open(QIODevice.WriteOnly):
                if image.save(output, "PNG"):
                    output.commit()
                else:
                    output.cancelWriting()
        except OSError:
            pass  # A read-only cache must not prevent an in-memory preview.
        return image
    except (OSError, httpx.HTTPError, RuntimeError):
        return QImage()


class _Result(QObject):
    ready = Signal(str, QImage)


class _ThumbnailWorker(QRunnable):
    def __init__(self, source, cache_root):
        super().__init__()
        self.source, self.cache_root = source, cache_root
        self.result = _Result()

    def run(self):
        self.result.ready.emit(self.source.key, load_thumbnail(self.source, self.cache_root))


class ThumbnailLoader(QObject):
    ready = Signal(str, QImage)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._images = OrderedDict()
        self._active = {}

    def cached_image(self, source: ThumbnailSource, *, include_disk=False) -> QImage | None:
        """Reuse ready previews before painting, without decoding source files."""
        key = source.key
        if key in self._images:
            self._images.move_to_end(key)
            return self._images[key]
        if include_disk:
            cached = thumbnail_cache_root() / f"{key}.png"
            try:
                # Only our small, already-generated previews are read inline.
                # Missing/corrupt/oversized entries take the background path.
                if cached.stat().st_size <= 256 * 1024:
                    reader = QImageReader(str(cached))
                    size = reader.size()
                    if size.isValid() and size.width() <= 192 and size.height() <= 192:
                        image = reader.read()
                        if not image.isNull():
                            self._remember(key, image)
                            return image
            except OSError:
                pass
        return None

    def request(self, source: ThumbnailSource) -> QImage | None:
        key = source.key
        image = self.cached_image(source)
        if image is not None:
            return image
        if key not in self._active and len(self._active) < 2:
            worker = _ThumbnailWorker(source, thumbnail_cache_root())
            self._active[key] = worker
            worker.result.ready.connect(self._finished)
            QThreadPool.globalInstance().start(worker)
        return None

    @Slot(str, QImage)
    def _finished(self, key, image):
        self._active.pop(key, None)
        self._remember(key, image)
        self.ready.emit(key, image)

    def _remember(self, key, image):
        self._images[key] = image
        self._images.move_to_end(key)
        while len(self._images) > 128:
            self._images.popitem(last=False)
