"""Current-page attachment shortcut for the main StillPoint toolbar."""
from __future__ import annotations

from pathlib import Path

import httpx
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
from PySide6.QtGui import QAction


class _CountResult(QObject):
    ready = Signal(int, object)


class _AttachmentCount(QRunnable):
    def __init__(self, generation, page, page_key, client, show_hidden):
        super().__init__()
        self.generation, self.page = generation, Path(page)
        self.page_key, self.client = page_key, client
        self.show_hidden = show_hidden
        self.result = _CountResult()

    def run(self):
        count = None
        try:
            if self.client is not None:
                response = self.client.get("/files/", params={"page_path": self.page_key})
                response.raise_for_status()
                entries = response.json().get("attachments", [])
                paths = [entry.get("attachment_path") or entry.get("stored_path")
                         for entry in entries if isinstance(entry, dict)]
                count = sum(1 for path in paths if path
                            and (self.show_hidden or not Path(path).name.startswith(".")))
            else:
                count = sum(1 for path in self.page.parent.iterdir()
                            if path != self.page and path.is_file()
                            and (self.show_hidden or not path.name.startswith(".")))
        except (OSError, httpx.HTTPError, ValueError, TypeError, RuntimeError):
            pass
        self.result.ready.emit(self.generation, count)


class AttachmentIndicator(QAction):
    """Check attachment counts without loading the sidebar or its thumbnails."""

    def __init__(self, parent):
        super().__init__("Show page attachments", parent)
        self._page = None
        self._generation = 0
        self._show_hidden = False
        self.setObjectName("mainEditorAttachmentIndicator")
        self.setVisible(False)

    def set_page(self, page, *, page_key=None, client=None):
        if page != self._page:
            self._show_count(0)
        self._page = page
        self._generation += 1
        if page is None:
            self._show_count(0)
            return
        worker = _AttachmentCount(self._generation, page, page_key, client, self._show_hidden)
        worker.result.ready.connect(self._count_ready)
        QThreadPool.globalInstance().start(worker)

    def set_show_hidden(self, show_hidden):
        self._show_hidden = show_hidden
        self._generation += 1

    def set_count(self, page, count):
        if page == self._page:
            self._generation += 1
            self._show_count(count)

    def _count_ready(self, generation, count):
        if generation == self._generation and count is not None:
            self._show_count(count)

    def _show_count(self, count):
        self.setToolTip(f"Show attachments ({count})")
        self.setVisible(count > 0)
