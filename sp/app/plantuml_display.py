"""PlantUML display integration for Markdown Editor.

Handles rendering PlantUML diagrams as embedded SVG in the editor's display layer.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable, Optional, Dict, Tuple
from dataclasses import dataclass
from PySide6.QtCore import QObject, Signal, QTimer, Qt
from PySide6.QtGui import QTextDocument, QTextCursor, QTextFormat, QTextImageFormat
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtGui import QImage, QPixmap

from sp.app.plantuml_renderer import PlantUMLRenderer, extract_plantuml_blocks, RenderResult
from sp.app import config

logger = logging.getLogger(__name__)


@dataclass
class DiagramRenderJob:
    """A pending diagram render job."""
    block_id: str
    puml_text: str
    start_line: int
    end_line: int


class PlantUMLDisplayManager(QObject):
    """Manages PlantUML diagram rendering and signals results asynchronously."""

    diagramRendered = Signal(str, str)  # block_id, svg_content
    renderError = Signal(str, str, str)  # block_id, error_message, stderr

    def __init__(self, parent=None):
        super().__init__(parent)
        self.renderer = PlantUMLRenderer()
        self._render_queue: Dict[str, DiagramRenderJob] = {}
        self._queue_lock = threading.Lock()
        self._render_debounce_ms = config.load_plantuml_render_debounce_ms()
        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.timeout.connect(self._flush_render_queue)

    def start(self) -> None:
        """No-op kept for API symmetry."""
        return

    def stop(self) -> None:
        """Clear pending render queue."""
        with self._queue_lock:
            self._render_queue.clear()

    def queue_render(self, block_id: str, puml_text: str, start_line: int, end_line: int) -> None:
        """Queue a diagram for rendering with debouncing.
        
        Args:
            block_id: Unique identifier for the block
            puml_text: PlantUML source code
            start_line: 1-indexed line number of opening fence
            end_line: 1-indexed line number of closing fence
        """
        with self._queue_lock:
            self._render_queue[block_id] = DiagramRenderJob(
                block_id=block_id,
                puml_text=puml_text,
                start_line=start_line,
                end_line=end_line,
            )

        # Restart debounce timer
        self._debounce_timer.stop()
        self._debounce_timer.start(self._render_debounce_ms)

    def _flush_render_queue(self) -> None:
        """Dispatch queued jobs to background threads after debounce."""
        with self._queue_lock:
            jobs = list(self._render_queue.values())
            self._render_queue.clear()

        for job in jobs:
            thread = threading.Thread(target=self._render_job_threadsafe, args=(job,), daemon=True)
            thread.start()

    def _render_job_threadsafe(self, job: DiagramRenderJob) -> None:
        """Render a diagram in a worker thread and emit results."""
        try:
            result = self.renderer.render_svg(job.puml_text)
            if result.success and result.svg_content:
                self.diagramRendered.emit(job.block_id, result.svg_content)
            else:
                stderr = result.stderr or ""
                self.renderError.emit(job.block_id, result.error_message or "Unknown error", stderr)
        except Exception as exc:
            logger.exception("PlantUML render job failed: %s", exc)

    def set_enabled(self, enabled: bool) -> None:
        """Enable or disable PlantUML rendering."""
        config.save_plantuml_enabled(enabled)

    def set_jar_path(self, jar_path: str) -> bool:
        """Set PlantUML JAR path and save to config."""
        success = self.renderer.set_jar_path(jar_path)
        if success:
            config.save_plantuml_jar_path(jar_path)
            self.renderer.clear_cache()  # Invalidate cache on JAR change
        return success

    def set_java_path(self, java_path: str) -> bool:
        """Set Java executable path and save to config."""
        success = self.renderer.set_java_path(java_path)
        if success:
            config.save_plantuml_java_path(java_path)
            self.renderer.clear_cache()  # Invalidate cache on Java change
        return success

    def test_setup(self) -> RenderResult:
        """Test PlantUML setup with a simple diagram."""
        test_puml = """@startuml
Alice -> Bob: Test Diagram
@enduml"""
        return self.renderer.render_svg(test_puml)

    def svg_to_image(self, svg_content: str, max_width: int = 800) -> Optional[QImage]:
        """Convert SVG content to QImage for display.
        
        Args:
            svg_content: SVG XML string
            max_width: Maximum width in pixels
            
        Returns:
            QImage or None if rendering failed
        """
        try:
            # Use QSvgRenderer to convert SVG to image
            renderer = QSvgRenderer()
            if not renderer.load(svg_content.encode()):
                logger.warning("Failed to load SVG in QSvgRenderer")
                return None

            # Get natural size and calculate scaled size
            size = renderer.defaultSize()
            if not size.isValid():
                size.setWidth(max_width)
                size.setHeight(max_width)

            # Scale if too wide
            if size.width() > max_width:
                scale = max_width / size.width()
                size.setWidth(int(size.width() * scale))
                size.setHeight(int(size.height() * scale))

            # Render to QImage
            image = QImage(size, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(0xFFFFFFFF)  # White background
            
            from PySide6.QtGui import QPainter
            painter = QPainter(image)
            renderer.render(painter)
            painter.end()

            return image

        except Exception as e:
            logger.warning(f"Failed to convert SVG to image: {e}")
            return None
