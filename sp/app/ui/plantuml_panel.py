"""PlantUML preview panel with async rendering and actions."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Dict, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QLabel,
    QPushButton,
    QHBoxLayout,
    QFileDialog,
    QScrollArea,
    QFrame,
)

from sp.app.plantuml_renderer import extract_plantuml_blocks
from sp.app.plantuml_display import PlantUMLDisplayManager
from sp.app import config

logger = logging.getLogger(__name__)


class _DiagramWidget(QFrame):
    """Container for a single diagram preview + actions."""

    def __init__(self, title: str):
        super().__init__()
        self.setFrameShape(QFrame.StyledPanel)
        self.setFrameShadow(QFrame.Plain)
        self.setObjectName("plantumlDiagramFrame")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        self.title_label = QLabel(title)
        self.title_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(self.title_label)

        self.image_label = QLabel("Rendering…")
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setStyleSheet("color: #888;")
        layout.addWidget(self.image_label)

        self.error_label = QLabel()
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        layout.addWidget(self.error_label)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.copy_btn = QPushButton("Copy SVG")
        self.save_btn = QPushButton("Save SVG…")
        self.copy_btn.setEnabled(False)
        self.save_btn.setEnabled(False)
        btn_row.addWidget(self.copy_btn)
        btn_row.addWidget(self.save_btn)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        self.svg_content: Optional[str] = None

    def set_svg(self, svg: str) -> None:
        self.svg_content = svg
        pix = self._svg_to_pixmap(svg)
        if pix:
            self.image_label.setPixmap(pix)
            self.image_label.setScaledContents(True)
            self.image_label.setMinimumHeight(pix.height())
            self.image_label.setStyleSheet("")
            self.image_label.setText("")
        else:
            self.image_label.setText("SVG render failed")
            self.image_label.setStyleSheet("color: #c00;")
        self.copy_btn.setEnabled(True)
        self.save_btn.setEnabled(True)
        self.error_label.hide()

    def set_error(self, message: str, details: str = "") -> None:
        self.svg_content = None
        self.image_label.setText("Render failed")
        self.image_label.setStyleSheet("color: #c00;")
        if details:
            self.error_label.setText(f"{message}\n\n{details}")
        else:
            self.error_label.setText(message)
        self.error_label.show()
        self.copy_btn.setEnabled(False)
        self.save_btn.setEnabled(False)

    def _svg_to_pixmap(self, svg: str, max_width: int = 720) -> Optional[QPixmap]:
        try:
            renderer = QSvgRenderer()
            if not renderer.load(svg.encode("utf-8")):
                return None
            size = renderer.defaultSize()
            if not size.isValid():
                size.setWidth(max_width)
                size.setHeight(max_width)
            if size.width() > max_width:
                scale = max_width / size.width()
                size.setWidth(int(size.width() * scale))
                size.setHeight(int(size.height() * scale))
            image = QImage(size, QImage.Format_ARGB32_Premultiplied)
            image.fill(0x00000000)
            painter = QPainter(image)
            renderer.render(painter)
            painter.end()
            return QPixmap.fromImage(image)
        except Exception as exc:
            logger.warning("Failed to convert SVG to pixmap: %s", exc)
            return None


class PlantUMLPanel(QWidget):
    """Right-panel tab that renders PlantUML blocks to SVG previews."""

    openSettingsRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.manager = PlantUMLDisplayManager(self)
        self.manager.diagramRendered.connect(self._on_diagram_rendered)
        self.manager.renderError.connect(self._on_diagram_error)
        self._generation = 0
        self._widgets: Dict[str, _DiagramWidget] = {}
        self._pending: Dict[str, str] = {}

        self._status = QLabel("No PlantUML blocks")
        self._status.setAlignment(Qt.AlignCenter)
        self._status.setStyleSheet("color: #777;")

        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(8, 8, 8, 8)
        self._content_layout.setSpacing(10)
        self._content_layout.addWidget(self._status)
        self._content_layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._content)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(scroll)

        footer = QHBoxLayout()
        self.open_settings_btn = QPushButton("Open Settings")
        self.open_settings_btn.clicked.connect(self.openSettingsRequested.emit)
        footer.addStretch(1)
        footer.addWidget(self.open_settings_btn)
        root.addLayout(footer)

    def set_content(self, page_path: str, markdown_text: str) -> None:
        """Render PlantUML blocks from markdown text."""
        # Refresh debounce from settings each call so preference changes apply immediately
        try:
            self.manager._render_debounce_ms = config.load_plantuml_render_debounce_ms()
        except Exception:
            pass
        enabled = config.load_plantuml_enabled()
        if not enabled:
            self._set_status("PlantUML rendering is disabled in settings.")
            return

        blocks = extract_plantuml_blocks(markdown_text)
        if not blocks:
            self._set_status("No PlantUML blocks found on this page.")
            return

        self._generation += 1
        gen = self._generation
        self._clear_widgets()
        self._pending.clear()
        self._set_status(f"Rendering {len(blocks)} diagram(s)…")

        for idx, (_start, _end, body) in enumerate(blocks, start=1):
            block_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]
            block_id = f"{gen}:{block_hash}"
            widget = _DiagramWidget(f"Diagram {idx}")
            widget.copy_btn.clicked.connect(lambda _, bid=block_id: self._copy_svg(bid))
            widget.save_btn.clicked.connect(lambda _, bid=block_id: self._save_svg(bid))
            self._widgets[block_id] = widget
            self._pending[block_id] = body
            self._content_layout.insertWidget(self._content_layout.count() - 1, widget)
            self.manager.queue_render(block_id, body, _start, _end)

    def _set_status(self, text: str) -> None:
        self._status.setText(text)
        self._status.show()

    def _clear_widgets(self) -> None:
        for wid in list(self._widgets.values()):
            wid.setParent(None)
            wid.deleteLater()
        self._widgets.clear()

    def _copy_svg(self, block_id: str) -> None:
        widget = self._widgets.get(block_id)
        if not widget or not widget.svg_content:
            return
        QGuiApplication.clipboard().setText(widget.svg_content)

    def _save_svg(self, block_id: str) -> None:
        widget = self._widgets.get(block_id)
        if not widget or not widget.svg_content:
            return
        filename, _ = QFileDialog.getSaveFileName(self, "Save SVG", "diagram.svg", "SVG Files (*.svg)")
        if not filename:
            return
        try:
            Path(filename).write_text(widget.svg_content, encoding="utf-8")
        except Exception as exc:
            logger.error("Failed to save SVG: %s", exc)

    def _on_diagram_rendered(self, block_id: str, svg: str) -> None:
        if not block_id.startswith(f"{self._generation}:"):
            return  # stale
        widget = self._widgets.get(block_id)
        if not widget:
            return
        widget.set_svg(svg)
        self._status.hide()

    def _on_diagram_error(self, block_id: str, message: str, stderr: str) -> None:
        if not block_id.startswith(f"{self._generation}:"):
            return  # stale
        widget = self._widgets.get(block_id)
        if not widget:
            return
        widget.set_error(message, stderr)
        self._status.hide()
