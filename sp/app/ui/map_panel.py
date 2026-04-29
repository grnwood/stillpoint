from __future__ import annotations

from dataclasses import dataclass, field
import html
import math
import re
import textwrap
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QBuffer, QEvent, QIODevice, QPointF, QSize, Qt, Signal, QMimeData
from PySide6.QtGui import QColor, QFontMetrics, QIcon, QImage, QKeyEvent, QKeySequence, QMouseEvent, QNativeGestureEvent, QPainter, QPalette, QPixmap, QShortcut
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from .markdown_editor import HEADING_MARK_PATTERN, HEADING_MAX_LEVEL, heading_level_from_char
from .theme import theme_color, theme_value


class ZoomablePreviewLabel(QLabel):
    """Preview label with wheel or gesture zoom and drag-to-pan."""

    zoomRequested = Signal(int, object)

    def __init__(self):
        super().__init__()
        self.pan_start_pos = None
        self.is_panning = False
        self.grabGesture(Qt.PinchGesture)

    def wheelEvent(self, event) -> None:  # type: ignore[override]
        if event.modifiers() & Qt.ControlModifier:
            delta = event.angleDelta().y()
            self.zoomRequested.emit(1 if delta > 0 else -1, QPointF(event.position()))
            event.accept()
            return
        if event.pixelDelta().y() and event.modifiers() == Qt.NoModifier and abs(event.pixelDelta().x()) <= abs(event.pixelDelta().y()) * 0.5:
            self.zoomRequested.emit(1 if event.pixelDelta().y() > 0 else -1, QPointF(event.position()))
            event.accept()
            return
        super().wheelEvent(event)

    def nativeEvent(self, eventType, message):  # type: ignore[override]
        return super().nativeEvent(eventType, message)

    def event(self, event):  # type: ignore[override]
        if isinstance(event, QNativeGestureEvent) and event.gestureType() == Qt.ZoomNativeGesture:
            value = event.value()
            if value:
                self.zoomRequested.emit(1 if value > 0 else -1, QPointF(event.position()))
                event.accept()
                return True
        return super().event(event)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        pixmap = self.pixmap()
        if event.button() == Qt.LeftButton and pixmap:
            self.is_panning = True
            self.pan_start_pos = event.globalPos()
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if self.is_panning and self.pan_start_pos:
            delta = event.globalPos() - self.pan_start_pos
            parent = self.parent()
            while parent:
                if isinstance(parent, QScrollArea):
                    parent.horizontalScrollBar().setValue(parent.horizontalScrollBar().value() - delta.x())
                    parent.verticalScrollBar().setValue(parent.verticalScrollBar().value() - delta.y())
                    self.pan_start_pos = event.globalPos()
                    event.accept()
                    return
                parent = parent.parent()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.LeftButton and self.is_panning:
            self.is_panning = False
            self.pan_start_pos = None
            self.setCursor(Qt.ArrowCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)


@dataclass
class _MindNode:
    node_id: str
    label: str
    depth: int
    level: int
    line_number: int = 0
    children: list["_MindNode"] = field(default_factory=list)
    side: int = 1
    lines: list[str] = field(default_factory=list)
    width: float = 0.0
    height: float = 0.0
    subtree_height: float = 0.0
    x: float = 0.0
    y: float = 0.0


@dataclass
class _DraftHeading:
    node_id: str
    anchor_node_id: str
    parent_node_id: str
    level: int
    text: str = ""
    as_child: bool = False
    restored_scope_depth: Optional[int] = None


class MapPanel(QWidget):
    """Native SVG-based mind map panel for markdown headings only."""

    headingActivated = Signal(str, int)
    headingCreateRequested = Signal(str, int, int, str)

    _HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
    _HR_RE = re.compile(r"^\s{0,3}([-*_])(?:\s*\1){2,}\s*$")

    _BOX_HPAD = 14
    _BOX_VPAD = 8
    _LINE_HEIGHT = 20
    _MAX_TEXT_WIDTH = 28
    _H_GAP = 72
    _V_GAP = 20
    _MARGIN = 40
    _INDICATOR_GAP = 8
    _INDICATOR_SIZE = 16
    _INDICATOR_FILL = "#4b5563"
    _INDICATOR_BG = "#ffffff"
    _INDICATOR_STROKE = "#8c959f"
    _RIGHT_ARROW_BOUNDS = (24.0, 24.0)
    _RIGHT_ARROW_PATH = "M5.536 21.886a1.004 1.004 0 0 0 1.033-.064l13-9a1 1 0 0 0 0-1.644l-13-9A1 1 0 0 0 5 3v18a1 1 0 0 0 .536.886z"
    _DOWN_ARROW_BOUNDS = (21590.0, 27940.0)
    _DOWN_ARROW_PATH = "M 2711,8517 C 2713,8661 2753,8801 2827,8925 2838,8943 2850,8961 2862,8979 L 10346,19473 C 10416,19571 10507,19653 10612,19711 10737,19781 10878,19817 11021,19815 11164,19813 11304,19773 11427,19700 11531,19638 11619,19554 11686,19455 L 18874,8756 C 18887,8737 18898,8718 18909,8699 18979,8574 19015,8433 19013,8290 19011,8147 18971,8007 18898,7884 18825,7761 18720,7659 18595,7590 18470,7520 18330,7484 18187,7486 L 3515,7691 C 3515,7691 3514,7691 3514,7691 3371,7693 3231,7733 3108,7806 2985,7879 2884,7983 2814,8108 2809,8118 2804,8128 2798,8138 2739,8255 2710,8385 2711,8517 Z"

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._current_page_path: Optional[str] = None
        self._current_markdown: str = ""
        self._svg_content: str = ""
        self._preview_pixmap: Optional[QPixmap] = None
        self._base_size: Optional[QSize] = None
        self._zoom_factor: float = 1.0
        self._collapsed_node_ids: set[str] = set()
        self._node_hitboxes: dict[str, tuple[float, float, float, float]] = {}
        self._indicator_hitboxes: dict[str, tuple[float, float, float, float]] = {}
        self._latest_root: Optional[_MindNode] = None
        self._canvas_bounds: Optional[tuple[float, float, float, float]] = None
        self._max_heading_level: int = 1
        self._scope_expansion_depths: dict[str, int] = {}
        self._selected_node_id: Optional[str] = None
        self._pending_selected_line: Optional[int] = None
        self._last_activation_source: Optional[str] = None
        self._root_is_page_h1: bool = False
        self._draft_heading: Optional[_DraftHeading] = None
        self._draft_runtime_node: Optional[_MindNode] = None
        self._theme_colors = self._map_theme_colors()

        self.setFocusPolicy(Qt.StrongFocus)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(8, 8, 8, 8)
        toolbar.setSpacing(6)

        self.level_label = QLabel("H1")
        self.level_label.setAlignment(Qt.AlignCenter)
        toolbar.addWidget(self.level_label)

        self.expand_all_btn = QToolButton()
        self.expand_all_btn.setAutoRaise(True)
        self.expand_all_btn.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.expand_all_btn.setIconSize(QSize(18, 18))
        self.expand_all_btn.setToolTip("Expand all")
        self.expand_all_btn.clicked.connect(self.expand_all)
        toolbar.addWidget(self.expand_all_btn)

        self.collapse_all_btn = QToolButton()
        self.collapse_all_btn.setAutoRaise(True)
        self.collapse_all_btn.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.collapse_all_btn.setIconSize(QSize(18, 18))
        self.collapse_all_btn.setToolTip("Collapse all")
        self.collapse_all_btn.clicked.connect(self.collapse_all)
        toolbar.addWidget(self.collapse_all_btn)

        self.copy_btn = QToolButton()
        self.copy_btn.setAutoRaise(True)
        self.copy_btn.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.copy_btn.setIconSize(QSize(18, 18))
        self.copy_btn.setToolTip("Copy image")
        self.copy_btn.clicked.connect(self.copy_image)
        toolbar.addWidget(self.copy_btn)

        self.fit_btn = QToolButton()
        self.fit_btn.setAutoRaise(True)
        self.fit_btn.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.fit_btn.setIconSize(QSize(18, 18))
        self.fit_btn.setToolTip("Fit map")
        self.fit_btn.clicked.connect(self.fit_map)
        toolbar.addWidget(self.fit_btn)

        toolbar.addStretch(1)

        self.zoom_out_btn = QToolButton()
        self.zoom_out_btn.setAutoRaise(True)
        self.zoom_out_btn.setFixedSize(26, 26)
        self.zoom_out_btn.setText("−")
        self.zoom_out_btn.setToolTip("Zoom out")
        self.zoom_out_btn.clicked.connect(lambda: self._adjust_zoom(-1, None))
        toolbar.addWidget(self.zoom_out_btn)

        self.zoom_in_btn = QToolButton()
        self.zoom_in_btn.setAutoRaise(True)
        self.zoom_in_btn.setFixedSize(26, 26)
        self.zoom_in_btn.setText("+")
        self.zoom_in_btn.setToolTip("Zoom in")
        self.zoom_in_btn.clicked.connect(lambda: self._adjust_zoom(1, None))
        toolbar.addWidget(self.zoom_in_btn)
        root.addLayout(toolbar)

        self.preview_label = ZoomablePreviewLabel()
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setFocusPolicy(Qt.StrongFocus)
        self.preview_label.setText("Open a page to view its map.")
        self.preview_label.zoomRequested.connect(self._adjust_zoom)
        self.preview_label.installEventFilter(self)
        root.addWidget(self._wrap_scroll_area(), 1)

        self._apply_palette_styles()
        self._update_level_controls()

    def changeEvent(self, event) -> None:  # type: ignore[override]
        super().changeEvent(event)
        if event.type() in (QEvent.PaletteChange, QEvent.ApplicationPaletteChange):
            self._apply_palette_styles()
            if self._current_page_path:
                self.refresh()

    def _asset_path(self, name: str) -> Optional[Path]:
        path = Path(__file__).resolve().parents[2] / "assets" / name
        return path if path.exists() else None

    def _editor_theme_palette(self) -> QPalette:
        palette = QPalette(QApplication.palette())
        bg = theme_value("markdown_editor.base.bg", None)
        text = theme_value("markdown_editor.base.text", None)
        selection_bg = theme_value("markdown_editor.base.selection_bg", None)
        selection_text = theme_value("markdown_editor.base.selection_text", None)
        base_color = palette.color(QPalette.Base)
        if bg is not None:
            base_color = theme_color("markdown_editor.base.bg", bg)
            palette.setColor(QPalette.Window, base_color)
            palette.setColor(QPalette.Base, base_color)
            palette.setColor(QPalette.AlternateBase, base_color.lighter(112) if base_color.lightness() < 128 else base_color.darker(104))
            palette.setColor(QPalette.Button, base_color)
        if text is not None:
            text_color = theme_color("markdown_editor.base.text", text)
            palette.setColor(QPalette.WindowText, text_color)
            palette.setColor(QPalette.Text, text_color)
            palette.setColor(QPalette.ButtonText, text_color)
        if selection_bg is not None:
            palette.setColor(QPalette.Highlight, theme_color("markdown_editor.base.selection_bg", selection_bg))
        if selection_text is not None:
            palette.setColor(QPalette.HighlightedText, theme_color("markdown_editor.base.selection_text", selection_text))
        border_color = QColor(base_color)
        border_color = border_color.lighter(170) if border_color.lightness() < 128 else border_color.darker(135)
        palette.setColor(QPalette.Mid, border_color)
        return palette

    def _map_theme_colors(self) -> dict[str, str]:
        palette = self._editor_theme_palette()
        base = palette.color(QPalette.Base)
        text = palette.color(QPalette.Text)
        border = palette.color(QPalette.Mid)
        is_light_palette = base.lightness() > 128
        return {
            "canvas": base.name(),
            "text": text.name(),
            "root_fill": base.lighter(118).name() if not is_light_palette else base.darker(108).name(),
            "root_stroke": border.lighter(120).name() if not is_light_palette else border.name(),
            "branch_pos_fill": "#0f2f4a" if not is_light_palette else "#dbeafe",
            "branch_pos_stroke": "#7dd3fc" if not is_light_palette else "#1f6feb",
            "branch_neg_fill": "#4a3410" if not is_light_palette else "#fef3c7",
            "branch_neg_stroke": "#fbbf24" if not is_light_palette else "#b26a00",
            "child_fill": base.lighter(108).name() if not is_light_palette else base.darker(104).name(),
            "child_stroke": border.name(),
            "collapsed_fill": "#3f3114" if not is_light_palette else "#fef3c7",
            "selected_stroke": "#f87171" if not is_light_palette else "#d1242f",
            "selected_shadow": "rgba(248, 113, 113, 0.32)" if not is_light_palette else "rgba(209, 36, 47, 0.18)",
            "edge_pos": "#7dd3fc" if not is_light_palette else "#1f6feb",
            "edge_neg": "#fbbf24" if not is_light_palette else "#b26a00",
            "indicator_fill": text.name(),
            "indicator_bg": base.name(),
            "indicator_stroke": border.name(),
        }

    def _apply_palette_styles(self) -> None:
        self._theme_colors = self._map_theme_colors()
        colors = self._theme_colors
        self.expand_all_btn.setIcon(self._load_svg_icon("expand-all.svg", QSize(18, 18)))
        self.collapse_all_btn.setIcon(self._load_svg_icon("collapse-all.svg", QSize(18, 18)))
        self.copy_btn.setIcon(self._load_svg_icon("copy-image.svg", QSize(18, 18)))
        self.fit_btn.setIcon(self._load_svg_icon("fit-image.svg", QSize(18, 18)))
        button_color = self._toolbar_icon_color().name()
        self.zoom_out_btn.setStyleSheet(f"color: {button_color};")
        self.zoom_in_btn.setStyleSheet(f"color: {button_color};")
        self.preview_label.setStyleSheet(f"background: {colors['canvas']};")
        self.scroll_area.setStyleSheet(f"QScrollArea, QScrollArea > QWidget > QWidget {{ background: {colors['canvas']}; border: none; }}")

    def _load_svg_icon(self, name: str, size: QSize) -> QIcon:
        path = self._asset_path(name)
        if path is None:
            return QIcon()
        try:
            svg_text = path.read_text(encoding="utf-8", errors="replace")
            if name in {"expand-all.svg", "collapse-all.svg"}:
                svg_text = re.sub(r"<text\b.*?</text>", "", svg_text, flags=re.DOTALL)
            renderer = QSvgRenderer()
            if not renderer.load(svg_text.encode("utf-8")):
                return QIcon()
            pixmap = QPixmap(size)
            pixmap.fill(Qt.transparent)
            painter = QPainter(pixmap)
            renderer.render(painter)
            painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
            painter.fillRect(pixmap.rect(), self._toolbar_icon_color())
            painter.end()
            return QIcon(pixmap)
        except Exception:
            return QIcon()

    def _toolbar_icon_color(self) -> QColor:
        palette = self._editor_theme_palette()
        return QColor(0, 0, 0) if palette.color(QPalette.Window).lightness() > 128 else QColor(255, 255, 255)

    def _wrap_scroll_area(self) -> QScrollArea:
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(False)
        self.scroll_area.setAlignment(Qt.AlignCenter)
        self.scroll_area.setWidget(self.preview_label)
        return self.scroll_area

    def set_content(self, page_path: Optional[str], markdown_text: str) -> None:
        is_new_page = page_path != self._current_page_path
        self._current_page_path = page_path
        self._current_markdown = markdown_text or ""
        if not page_path:
            self.clear_content()
            return
        if is_new_page:
            self._collapsed_node_ids.clear()
            self._canvas_bounds = None
            self._scope_expansion_depths.clear()
        self._render_current(reset_zoom=is_new_page, reset_canvas=is_new_page)

    def clear_content(self) -> None:
        self._current_page_path = None
        self._current_markdown = ""
        self._svg_content = ""
        self._preview_pixmap = None
        self._base_size = None
        self._node_hitboxes.clear()
        self._indicator_hitboxes.clear()
        self._latest_root = None
        self._canvas_bounds = None
        self._max_heading_level = 1
        self._scope_expansion_depths.clear()
        self._selected_node_id = None
        self._pending_selected_line = None
        self._last_activation_source = None
        self._root_is_page_h1 = False
        self._draft_heading = None
        self._draft_runtime_node = None
        self.preview_label.setPixmap(QPixmap())
        self.preview_label.setText("Open a page to view its map.")
        self._update_level_controls()

    def refresh(self) -> None:
        if not self._current_page_path:
            self.clear_content()
            return
        self._render_current(reset_zoom=False)

    def fit_map(self) -> None:
        if not self._current_page_path:
            return
        self._render_current(reset_zoom=False, reset_canvas=True)

    def _selected_scope_node(self) -> Optional[_MindNode]:
        if not self._latest_root:
            return None
        return self._selected_node() or self._latest_root

    def _scope_depth(self, node: _MindNode) -> int:
        return max(0, int(self._scope_expansion_depths.get(node.node_id, 0)))

    def _max_scope_depth(self, node: _MindNode) -> int:
        if not node.children:
            return 0
        def _subtree_depth(current: _MindNode) -> int:
            children = current.children
            if not children:
                return 0
            return 1 + max(_subtree_depth(child) for child in children)
        return _subtree_depth(node)

    def _scope_base_level(self, node: Optional[_MindNode]) -> int:
        if not node or not node.children:
            return 1
        return min(max(1, child.level) for child in node.children)

    def increase_heading_level(self) -> None:
        scope = self._selected_scope_node()
        if scope is None:
            return
        current_depth = self._scope_depth(scope)
        max_depth = self._max_scope_depth(scope)
        if current_depth >= max_depth:
            return
        self._scope_expansion_depths[scope.node_id] = current_depth + 1
        self._update_level_controls()
        self.refresh()

    def decrease_heading_level(self) -> None:
        scope = self._selected_scope_node()
        if scope is None:
            return
        current_depth = self._scope_depth(scope)
        if current_depth <= 0:
            return
        next_depth = current_depth - 1
        if next_depth <= 0:
            self._scope_expansion_depths.pop(scope.node_id, None)
        else:
            self._scope_expansion_depths[scope.node_id] = next_depth
        self._update_level_controls()
        self.refresh()

    def _fit_current_canvas(self) -> None:
        if not self._svg_content or not self._base_size:
            return
        viewport = self.scroll_area.viewport().size()
        if viewport.width() < 20 or viewport.height() < 20:
            return
        x_ratio = max(0.1, (viewport.width() - 16) / max(1, self._base_size.width()))
        y_ratio = max(0.1, (viewport.height() - 16) / max(1, self._base_size.height()))
        self._zoom_factor = max(0.2, min(3.0, min(x_ratio, y_ratio)))
        self._update_preview(fit=False)

    def _reset_view_to_root(self) -> bool:
        if not self._latest_root:
            return False
        self._set_selected_node(self._latest_root)
        self.fit_map()
        return True

    def expand_all(self) -> None:
        if self._latest_root:
            self._scope_expansion_depths = {
                node.node_id: 1
                for node in self._collect_nodes(self._latest_root)
                if node.children
            }
        self._collapsed_node_ids.clear()
        self.refresh()

    def collapse_all(self) -> None:
        if not self._latest_root:
            return
        self._scope_expansion_depths.clear()
        self._collapsed_node_ids = set()
        self._set_selected_node(self._latest_root)
        self.refresh()

    def copy_image(self) -> None:
        if not self._preview_pixmap:
            return
        clipboard = QApplication.clipboard()
        buffer = QBuffer()
        buffer.open(QIODevice.WriteOnly)
        self._preview_pixmap.save(buffer, "PNG")
        png_bytes = bytes(buffer.data())
        mime = QMimeData()
        mime.setData("image/png", png_bytes)
        mime.setImageData(self._preview_pixmap.toImage())
        clipboard.setMimeData(mime)

    def _render_current(self, *, reset_zoom: bool, reset_canvas: bool = False) -> None:
        if not self._current_page_path:
            self.clear_content()
            return
        root = self._parse_markdown(self._current_page_path, self._current_markdown)
        self._draft_runtime_node = None
        self._apply_draft_node(root)
        self._latest_root = root
        self._max_heading_level = max((node.level for node in self._collect_nodes(root)), default=1)
        valid_scope_ids = {node.node_id for node in self._collect_nodes(root)}
        self._scope_expansion_depths = {
            node_id: max(0, depth)
            for node_id, depth in self._scope_expansion_depths.items()
            if node_id in valid_scope_ids
        }
        self._update_level_controls()
        self._sync_selected_node(root)
        self._svg_content = self._build_map_svg(root, reset_canvas=reset_canvas)
        if reset_zoom:
            self._zoom_factor = 1.0
        self._update_preview(fit=reset_zoom or reset_canvas)

    def _adjust_zoom(self, delta: int, anchor: object = None) -> None:
        if not self._svg_content:
            return
        old_zoom = self._zoom_factor
        new_zoom = max(0.2, min(4.0, self._zoom_factor + (0.1 * delta)))
        if abs(new_zoom - old_zoom) < 1e-9:
            return
        viewport_anchor = None
        svg_anchor = None
        if isinstance(anchor, QPointF):
            mapped_anchor = self.preview_label.mapTo(self.scroll_area.viewport(), anchor.toPoint())
            viewport_anchor = QPointF(mapped_anchor)
            svg_anchor = QPointF(anchor.x() / old_zoom, anchor.y() / old_zoom)
        self._zoom_factor = new_zoom
        self._update_preview(fit=False)
        if svg_anchor is not None and viewport_anchor is not None:
            self.scroll_area.horizontalScrollBar().setValue(
                max(0, int(round(svg_anchor.x() * self._zoom_factor - viewport_anchor.x())))
            )
            self.scroll_area.verticalScrollBar().setValue(
                max(0, int(round(svg_anchor.y() * self._zoom_factor - viewport_anchor.y())))
            )

    def _selected_node_zoom_anchor(self) -> Optional[QPointF]:
        if not self._selected_node_id:
            return None
        hitbox = self._node_hitboxes.get(self._selected_node_id)
        if not hitbox:
            return None
        x, y, w, h = hitbox
        return QPointF((x + (w / 2.0)) * self._zoom_factor, (y + (h / 2.0)) * self._zoom_factor)

    def zoom_selected_node(self, delta: int) -> bool:
        if not self._svg_content:
            return False
        self._adjust_zoom(delta, self._selected_node_zoom_anchor())
        return True

    def contains_focus(self) -> bool:
        focus_widget = QApplication.focusWidget()
        if focus_widget is None:
            return False
        return focus_widget is self or focus_widget is self.preview_label or self.isAncestorOf(focus_widget)

    def _update_preview(self, *, fit: bool) -> None:
        pixmap = self._svg_to_pixmap(self._svg_content)
        if not pixmap:
            self.preview_label.setText("Failed to render map.")
            self._preview_pixmap = None
            self._base_size = None
            return
        self._base_size = pixmap.size()
        if fit:
            self._fit_current_canvas()
            return
        scaled = pixmap.scaled(
            max(1, int(self._base_size.width() * self._zoom_factor)),
            max(1, int(self._base_size.height() * self._zoom_factor)),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        self._preview_pixmap = scaled
        self.preview_label.setPixmap(scaled)
        self.preview_label.resize(scaled.size())
        self.preview_label.setText("")

    def _svg_to_pixmap(self, svg_text: str) -> Optional[QPixmap]:
        renderer = QSvgRenderer()
        if not renderer.load(svg_text.encode("utf-8")):
            return None
        size = renderer.defaultSize()
        if not size.isValid():
            size = QSize(1400, 900)
        image = QImage(size, QImage.Format_ARGB32_Premultiplied)
        image.fill(QColor(self._theme_colors["canvas"]))
        painter = QPainter(image)
        renderer.render(painter)
        painter.end()
        return QPixmap.fromImage(image)

    def _node_is_expanded(self, node: _MindNode) -> bool:
        return self._scope_depth(node) > 0

    def _update_level_controls(self) -> None:
        if self._draft_heading is not None:
            self.level_label.setText(f"H{self._draft_heading.level}")
            return
        node = self._selected_node()
        if node is None:
            self.level_label.setText("H1")
            return
        node_level = node.level if node.level > 0 else 1
        self.level_label.setText(f"H{node_level}")

    def _mark_activation_source(self, source: str) -> None:
        self._last_activation_source = source

    def consume_activation_source(self) -> Optional[str]:
        src = self._last_activation_source
        self._last_activation_source = None
        return src

    def _sync_selected_node(self, root: _MindNode) -> None:
        visible_nodes = self._visible_nodes(root)
        if not visible_nodes:
            self._selected_node_id = None
            self._pending_selected_line = None
            self._update_level_controls()
            return
        if self._pending_selected_line is not None:
            target = next((node for node in visible_nodes if node.line_number == self._pending_selected_line), None)
            self._pending_selected_line = None
            if target is not None:
                self._selected_node_id = target.node_id
                self._update_level_controls()
                return
        visible_ids = {node.node_id for node in visible_nodes}
        if self._selected_node_id in visible_ids:
            self._update_level_controls()
            return
        self._selected_node_id = visible_nodes[0].node_id
        self._update_level_controls()

    def _selected_node(self) -> Optional[_MindNode]:
        if self._draft_runtime_node and self._selected_node_id == self._draft_runtime_node.node_id:
            return self._draft_runtime_node
        if not self._latest_root or not self._selected_node_id:
            return None
        return next((node for node in self._collect_nodes(self._latest_root) if node.node_id == self._selected_node_id), None)

    def _subtree_end_line(self, node: _MindNode) -> int:
        lines = [current.line_number for current in self._collect_nodes(node) if current.line_number > 0]
        return max(lines, default=0)

    def _new_heading_level(self, node: _MindNode, *, as_child: bool) -> int:
        if as_child:
            if node.depth == 0:
                return 2 if self._root_is_page_h1 else 1
            return min(HEADING_MAX_LEVEL, max(1, node.level + 1))
        if node.depth == 0:
            return 1
        return max(1, node.level)

    def _start_draft_heading(self, *, as_child: bool) -> bool:
        if self._draft_heading is not None or not self._latest_root:
            return False
        anchor = self._selected_node() or self._latest_root
        if anchor is None:
            return False
        parent = anchor if as_child else (self._find_parent(self._latest_root, anchor) or self._latest_root)
        restored_scope_depth: Optional[int] = None
        if as_child:
            current_depth = self._scope_depth(anchor)
            if current_depth <= 0:
                restored_scope_depth = current_depth
                self._scope_expansion_depths[anchor.node_id] = 1
        self._draft_heading = _DraftHeading(
            node_id="draft:new-heading",
            anchor_node_id=anchor.node_id,
            parent_node_id=parent.node_id,
            level=self._new_heading_level(anchor, as_child=as_child),
            as_child=as_child,
            restored_scope_depth=restored_scope_depth,
        )
        self._selected_node_id = self._draft_heading.node_id
        self.refresh()
        return True

    def _cancel_draft_heading(self) -> bool:
        draft = self._draft_heading
        if draft is None:
            return False
        anchor_id = draft.anchor_node_id
        if draft.as_child and draft.restored_scope_depth is not None:
            if draft.restored_scope_depth <= 0:
                self._scope_expansion_depths.pop(anchor_id, None)
            else:
                self._scope_expansion_depths[anchor_id] = draft.restored_scope_depth
        self._draft_heading = None
        self._draft_runtime_node = None
        self._selected_node_id = anchor_id
        self.refresh()
        return True

    def _commit_draft_heading(self) -> bool:
        draft = self._draft_heading
        if draft is None or not self._current_page_path or not self._latest_root:
            return False
        heading_text = draft.text.strip()
        if not heading_text:
            return self._cancel_draft_heading()
        anchor = self._node_by_id(draft.anchor_node_id)
        after_line = self._subtree_end_line(anchor) if anchor is not None else 0
        anchor_id = draft.anchor_node_id
        self._current_markdown, inserted_line = self._insert_heading_into_markdown(
            self._current_markdown,
            after_line=after_line,
            level=draft.level,
            text=heading_text,
        )
        if draft.as_child and draft.restored_scope_depth is not None:
            self._scope_expansion_depths[anchor_id] = max(self._scope_depth(anchor) if anchor else 0, 1)
        self._draft_heading = None
        self._draft_runtime_node = None
        self._pending_selected_line = inserted_line
        self.headingCreateRequested.emit(self._current_page_path, after_line, draft.level, heading_text)
        self.refresh()
        self.preview_label.setFocus(Qt.ShortcutFocusReason)
        return True

    def _insert_heading_into_markdown(self, markdown_text: str, *, after_line: int, level: int, text: str) -> tuple[str, int]:
        heading_level = max(1, min(int(level or 1), HEADING_MAX_LEVEL))
        heading_line = f"{'#' * heading_level} {text.strip()}".rstrip()
        source = markdown_text or ""
        lines = source.splitlines()
        if not lines:
            return f"{heading_line}\n", 1
        if after_line <= 0:
            insert_at = 0
            while insert_at < len(lines) and not lines[insert_at].strip():
                insert_at += 1
        else:
            insert_at = max(0, min(after_line, len(lines)))
        lines.insert(insert_at, heading_line)
        result = "\n".join(lines)
        if source.endswith("\n") or not result.endswith("\n"):
            result += "\n"
        return result, insert_at + 1

    def _handle_draft_keypress(self, event: QKeyEvent) -> bool:
        draft = self._draft_heading
        if draft is None:
            return False
        key = event.key()
        mods = event.modifiers()
        if key == Qt.Key_Escape and not mods:
            return self._cancel_draft_heading()
        if key in (Qt.Key_Return, Qt.Key_Enter) and not mods:
            return self._commit_draft_heading()
        if key == Qt.Key_Backspace and not mods:
            if draft.text:
                draft.text = draft.text[:-1]
                self.refresh()
            return True
        if key == Qt.Key_Space and not mods:
            draft.text += " "
            self.refresh()
            return True
        if key == Qt.Key_Tab:
            return True
        if mods in (Qt.NoModifier, Qt.ShiftModifier):
            text = event.text()
            if text and text >= " " and text != "\x7f":
                draft.text += text
                self.refresh()
                return True
        return True

    def _set_selected_node(self, node: Optional[_MindNode]) -> bool:
        node_id = node.node_id if node else None
        changed = node_id != self._selected_node_id
        self._selected_node_id = node_id
        self._update_level_controls()
        if changed and self._latest_root is not None:
            self._svg_content = self._build_map_svg(self._latest_root, reset_canvas=False)
            self._update_preview(fit=False)
            self._ensure_selected_visible()
        return changed

    def _ensure_selected_visible(self) -> None:
        if not self._preview_pixmap or not self._selected_node_id:
            return
        hitbox = self._node_hitboxes.get(self._selected_node_id)
        if not hitbox:
            return
        x, y, w, h = hitbox
        target_x = int((x + (w / 2)) * self._zoom_factor)
        target_y = int((y + (h / 2)) * self._zoom_factor)
        viewport = self.scroll_area.viewport().size()
        self.scroll_area.horizontalScrollBar().setValue(max(0, target_x - (viewport.width() // 2)))
        self.scroll_area.verticalScrollBar().setValue(max(0, target_y - (viewport.height() // 2)))

    def _node_sort_key(self, node: _MindNode) -> tuple[float, float, str]:
        return (node.y, node.x, node.node_id)

    def _visible_siblings(self, node: _MindNode) -> list[_MindNode]:
        if not self._latest_root:
            return []
        parent = self._find_parent(self._latest_root, node)
        if parent is None:
            return [node]
        return self._visible_children(parent)

    def _hierarchy_neighbor(self, direction: str) -> Optional[_MindNode]:
        if not self._latest_root:
            return None
        current = self._selected_node() or self._latest_root
        if direction == "left":
            parent = self._find_parent(self._latest_root, current)
            return parent
        if direction == "right":
            children = self._visible_children(current)
            if not children:
                return None
            return min(children, key=self._node_sort_key)
        siblings = self._visible_siblings(current)
        if len(siblings) <= 1:
            return None
        try:
            index = next(idx for idx, sibling in enumerate(siblings) if sibling.node_id == current.node_id)
        except StopIteration:
            return None
        step = -1 if direction == "up" else 1
        return siblings[(index + step) % len(siblings)]

    def _move_selection(self, direction: str) -> bool:
        node = self._hierarchy_neighbor(direction)
        if not node:
            return False
        self._set_selected_node(node)
        return True

    def _activate_selected_node(self, *, keep_focus: bool) -> bool:
        node = self._selected_node()
        if not node or not self._current_page_path or node.line_number <= 0:
            return False
        self._mark_activation_source("keyboard_keep_panel" if keep_focus else "keyboard")
        self.headingActivated.emit(self._current_page_path, node.line_number)
        return True

    def _build_map_svg(self, root: _MindNode, *, reset_canvas: bool) -> str:
        self._theme_colors = self._map_theme_colors()
        self._node_hitboxes.clear()
        self._indicator_hitboxes.clear()
        self._measure_tree(root)
        self._assign_sides(root)
        root.x = 0.0
        root.y = 0.0
        self._layout_children(root, root.children, 1)

        visible_nodes = self._visible_nodes(root)
        if not visible_nodes:
            visible_nodes = [root]
        min_x, max_x, min_y, max_y = self._bounds_for_nodes(visible_nodes)
        if reset_canvas or self._canvas_bounds is None:
            self._canvas_bounds = (min_x, max_x, min_y, max_y)
        else:
            canvas_min_x, canvas_max_x, canvas_min_y, canvas_max_y = self._canvas_bounds
            self._canvas_bounds = (
                min(canvas_min_x, min_x),
                max(canvas_max_x, max_x),
                min(canvas_min_y, min_y),
                max(canvas_max_y, max_y),
            )
        canvas_min_x, canvas_max_x, canvas_min_y, canvas_max_y = self._canvas_bounds
        width = max(600, int(math.ceil(canvas_max_x - canvas_min_x)))
        height = max(400, int(math.ceil(canvas_max_y - canvas_min_y)))
        offset_x = -canvas_min_x
        offset_y = -canvas_min_y

        line_parts: list[str] = []
        node_parts: list[str] = []
        for node in visible_nodes:
            if node is not root:
                parent = self._find_parent(root, node)
                if parent:
                    line_parts.append(self._render_edge(parent, node, offset_x, offset_y))
            node_parts.append(self._render_node(node, offset_x, offset_y))

        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
            "<style>"
            f"text {{ font-family: 'Noto Sans', 'Segoe UI', sans-serif; fill: {self._theme_colors['text']}; user-select: none; }}"
            f".root {{ fill: {self._theme_colors['root_fill']}; stroke: {self._theme_colors['root_stroke']}; stroke-width: 2; }}"
            f".branch-1 {{ fill: {self._theme_colors['branch_pos_fill']}; stroke: {self._theme_colors['branch_pos_stroke']}; stroke-width: 2; }}"
            f".branch--1 {{ fill: {self._theme_colors['branch_neg_fill']}; stroke: {self._theme_colors['branch_neg_stroke']}; stroke-width: 2; }}"
            f".child {{ fill: {self._theme_colors['child_fill']}; stroke: {self._theme_colors['child_stroke']}; stroke-width: 1.5; }}"
            f".collapsed {{ fill: {self._theme_colors['collapsed_fill']}; }}"
            f".selected {{ stroke: {self._theme_colors['selected_stroke']}; stroke-width: 3.5; filter: drop-shadow(0 0 10px {self._theme_colors['selected_shadow']}); }}"
            f".edge-1 {{ stroke: {self._theme_colors['edge_pos']}; stroke-width: 2.2; fill: none; }}"
            f".edge--1 {{ stroke: {self._theme_colors['edge_neg']}; stroke-width: 2.2; fill: none; }}"
            "</style>"
            f'<rect width="100%" height="100%" fill="{self._theme_colors["canvas"]}"/>'
            + "".join(line_parts)
            + "".join(node_parts)
            + "</svg>"
        )

    def _apply_draft_node(self, root: _MindNode) -> None:
        draft = self._draft_heading
        if draft is None:
            return
        parent = next((node for node in self._collect_nodes(root) if node.node_id == draft.parent_node_id), None)
        if parent is None:
            self._draft_heading = None
            self._selected_node_id = root.node_id
            return
        label = draft.text if draft.text else " "
        node = _MindNode(
            node_id=draft.node_id,
            label=label,
            depth=parent.depth + 1,
            level=draft.level,
            line_number=0,
            side=parent.side if parent.depth > 0 else 1,
        )
        self._draft_runtime_node = node
        if draft.as_child:
            parent.children.append(node)
            return
        anchor = next((child for child in parent.children if child.node_id == draft.anchor_node_id), None)
        if anchor is None:
            parent.children.append(node)
            return
        try:
            insert_idx = parent.children.index(anchor) + 1
        except ValueError:
            insert_idx = len(parent.children)
        parent.children.insert(insert_idx, node)

    def _parse_markdown(self, page_path: str, markdown_text: str) -> _MindNode:
        title = Path(page_path).stem or "Map"
        root = _MindNode(node_id=f"root:{page_path}", label=title, depth=0, level=0)
        heading_stack: list[_MindNode] = [root]
        seq = 0
        self._root_is_page_h1 = False

        for line_number, raw_line in enumerate(markdown_text.splitlines(), start=1):
            text = raw_line.rstrip("\n")
            stripped = text.lstrip()
            if not stripped.strip() or self._HR_RE.match(text):
                continue
            level = heading_level_from_char(stripped[0]) if stripped else 0
            title_text: Optional[str] = None
            if level:
                title_text = stripped[1:].strip()
            else:
                match = HEADING_MARK_PATTERN.match(text)
                if match:
                    hashes = match.group(2)
                    title_text = (match.group(4) or "").strip()
                    level = min(len(hashes), HEADING_MAX_LEVEL)
            if not level or not title_text:
                continue
            label = self._normalize_label(title_text)
            if level == 1 and line_number == 1:
                root.label = label
                root.line_number = line_number
                self._root_is_page_h1 = True
                heading_stack = [root, root]
                continue
            while len(heading_stack) > max(1, level):
                heading_stack.pop()
            if not heading_stack:
                heading_stack = [root]
            parent = heading_stack[-1]
            seq += 1
            node = _MindNode(
                node_id=f"node:{seq}",
                label=label,
                depth=parent.depth + 1,
                level=level,
                line_number=line_number,
            )
            parent.children.append(node)
            heading_stack.append(node)

        if not root.children:
            root.children.append(_MindNode(node_id="empty", label="No headings", depth=1, level=1))
        return root

    def _normalize_label(self, text: str) -> str:
        label = text.strip()
        label = re.sub(r"\[(.*?)\]\((.*?)\)", r"\1", label)
        label = re.sub(r"\[(.*?)\|(.*?)\]", r"\2", label)
        label = re.sub(r"`([^`]*)`", r"\1", label)
        label = re.sub(r"[_*~#]+", "", label)
        return label.strip() or "Heading"

    def _visible_children(self, node: _MindNode) -> list[_MindNode]:
        if node.node_id in self._collapsed_node_ids:
            return []
        return list(node.children)

    def _visible_nodes(self, node: _MindNode, inherited_depth: int = 0) -> list[_MindNode]:
        nodes = [node]
        if node.node_id in self._collapsed_node_ids:
            return nodes
        child_depth = max(inherited_depth, self._scope_depth(node))
        for child in self._visible_children(node):
            nodes.append(child)
            next_depth = max(0, child_depth - 1)
            if next_depth > 0 or self._scope_depth(child) > 0:
                nodes.extend(self._visible_nodes(child, next_depth)[1:])
        return nodes

    def _bounds_for_nodes(self, nodes: list[_MindNode]) -> tuple[float, float, float, float]:
        min_x = min(node.x - node.width / 2 for node in nodes) - self._MARGIN
        max_x = max(
            node.x + node.width / 2 + (self._INDICATOR_SIZE / 2 if node.children else 0)
            for node in nodes
        ) + self._MARGIN
        min_y = min(node.y - node.height / 2 for node in nodes) - self._MARGIN
        max_y = max(node.y + node.height / 2 for node in nodes) + self._MARGIN
        return min_x, max_x, min_y, max_y

    def _measure_tree(self, node: _MindNode) -> None:
        metrics = QFontMetrics(QApplication.font())
        lines = textwrap.wrap(node.label, width=self._MAX_TEXT_WIDTH) or [node.label]
        node.lines = lines[:4]
        text_width = max(metrics.horizontalAdvance(line) for line in node.lines) if node.lines else 40
        node.width = float(text_width + (self._BOX_HPAD * 2))
        node.height = float((len(node.lines) * self._LINE_HEIGHT) + (self._BOX_VPAD * 2))
        visible_children = self._visible_children(node)
        for child in visible_children:
            self._measure_tree(child)
        if not visible_children:
            node.subtree_height = node.height
            return
        total = sum(child.subtree_height for child in visible_children)
        total += self._V_GAP * (len(visible_children) - 1)
        node.subtree_height = max(node.height, total)

    def _assign_sides(self, root: _MindNode) -> None:
        for child in root.children:
            self._propagate_side(child, 1)

    def _propagate_side(self, node: _MindNode, side: int) -> None:
        node.side = side
        for child in node.children:
            self._propagate_side(child, side)

    def _layout_children(self, parent: _MindNode, children: list[_MindNode], side: int) -> None:
        if not children:
            return
        total_height = sum(child.subtree_height for child in children)
        total_height += self._V_GAP * (len(children) - 1)
        current_top = parent.y - (total_height / 2)
        for child in children:
            child.y = current_top + (child.subtree_height / 2)
            child.x = parent.x + side * ((parent.width / 2) + self._H_GAP + (child.width / 2))
            current_top += child.subtree_height + self._V_GAP
            visible_grandchildren = self._visible_children(child)
            if visible_grandchildren:
                self._layout_children(child, visible_grandchildren, side)

    def _collect_nodes(self, node: _MindNode) -> list[_MindNode]:
        nodes = [node]
        for child in node.children:
            nodes.extend(self._collect_nodes(child))
        return nodes

    def _find_parent(self, root: _MindNode, target: _MindNode) -> Optional[_MindNode]:
        for child in root.children:
            if child is target:
                return root
            found = self._find_parent(child, target)
            if found:
                return found
        return None

    def _render_edge(self, parent: _MindNode, child: _MindNode, ox: float, oy: float) -> str:
        sx = parent.x + ox + (parent.width / 2 if child.side > 0 else -parent.width / 2)
        sy = parent.y + oy
        ex = child.x + ox + (-child.width / 2 if child.side > 0 else child.width / 2)
        ey = child.y + oy
        ctrl = (ex - sx) * 0.45
        c1x = sx + ctrl
        c2x = ex - ctrl
        css = f"edge-{child.side}"
        return f'<path class="{css}" d="M {sx:.1f} {sy:.1f} C {c1x:.1f} {sy:.1f}, {c2x:.1f} {ey:.1f}, {ex:.1f} {ey:.1f}"/>'

    def _render_node(self, node: _MindNode, ox: float, oy: float) -> str:
        x = node.x + ox - (node.width / 2)
        y = node.y + oy - (node.height / 2)
        css = "root" if node.depth == 0 else ("branch-1" if node.depth == 1 and node.side > 0 else "branch--1" if node.depth == 1 else "child")
        if node.node_id in self._collapsed_node_ids:
            css += " collapsed"
        if node.node_id == self._selected_node_id:
            css += " selected"
        self._node_hitboxes[node.node_id] = (x, y, node.width, node.height)
        parts = [f'<rect class="{css}" x="{x:.1f}" y="{y:.1f}" width="{node.width:.1f}" height="{node.height:.1f}" rx="14" ry="14"/>']
        text_y = y + self._BOX_VPAD + 15
        visible_lines = node.lines
        if self._draft_runtime_node is not None and node.node_id == self._draft_runtime_node.node_id and visible_lines:
            visible_lines = visible_lines[:-1] + [visible_lines[-1] + "|"]
        for line in visible_lines:
            tx = node.x + ox
            parts.append(f'<text x="{tx:.1f}" y="{text_y:.1f}" text-anchor="middle" font-size="14">{html.escape(line)}</text>')
            text_y += self._LINE_HEIGHT
        if node.children:
            indicator_size = float(self._INDICATOR_SIZE)
            indicator_x = x + node.width - (indicator_size / 2)
            indicator_y = y + (node.height - indicator_size) / 2
            self._indicator_hitboxes[node.node_id] = (indicator_x, indicator_y, indicator_size, indicator_size)
            parts.append(self._render_indicator_svg(node, indicator_x, indicator_y, indicator_size))
        return "".join(parts)

    def _render_indicator_svg(self, node: _MindNode, x: float, y: float, size: float) -> str:
        expanded = self._node_is_expanded(node)
        bounds = self._DOWN_ARROW_BOUNDS if expanded else self._RIGHT_ARROW_BOUNDS
        path_data = self._DOWN_ARROW_PATH if expanded else self._RIGHT_ARROW_PATH
        base_width, base_height = bounds
        scale = size / max(base_width, base_height)
        dx = (size - (base_width * scale)) / 2
        dy = (size - (base_height * scale)) / 2
        return (
            f'<circle cx="{x + (size / 2):.3f}" cy="{y + (size / 2):.3f}" r="{size / 2:.3f}" fill="{self._theme_colors["indicator_bg"]}" stroke="{self._theme_colors["indicator_stroke"]}" stroke-width="1.2"/>'
            f'<g transform="translate({x + dx:.3f} {y + dy:.3f}) scale({scale:.6f})">'
            f'<path d="{path_data}" fill="{self._theme_colors["indicator_fill"]}" fill-rule="evenodd"/>'
            "</g>"
        )

    def _event_svg_position(self, event: QMouseEvent) -> Optional[tuple[float, float]]:
        if not self._preview_pixmap or not self._base_size or not self._node_hitboxes:
            return None
        pixmap = self.preview_label.pixmap()
        if not pixmap:
            return None
        label_size = self.preview_label.size()
        pm_size = pixmap.size()
        offset_x = max(0.0, (label_size.width() - pm_size.width()) / 2)
        offset_y = max(0.0, (label_size.height() - pm_size.height()) / 2)
        px = event.position().x() - offset_x
        py = event.position().y() - offset_y
        if not (0 <= px <= pm_size.width() and 0 <= py <= pm_size.height()):
            return None
        return px / self._zoom_factor, py / self._zoom_factor

    def _node_at_position(self, svg_x: float, svg_y: float) -> Optional[_MindNode]:
        target_id: Optional[str] = None
        for node_id, (x, y, w, h) in self._node_hitboxes.items():
            if x <= svg_x <= x + w and y <= svg_y <= y + h:
                target_id = node_id
                break
        if not target_id or not self._latest_root:
            return None
        return next((item for item in self._collect_nodes(self._latest_root) if item.node_id == target_id), None)

    def _toggle_node_at(self, event: QMouseEvent) -> bool:
        svg_pos = self._event_svg_position(event)
        if not svg_pos:
            return False
        svg_x, svg_y = svg_pos
        for node_id, indicator_hitbox in self._indicator_hitboxes.items():
            x, y, w, h = indicator_hitbox
            if x <= svg_x <= x + w and y <= svg_y <= y + h:
                node = self._node_by_id(node_id)
                if not node or not node.children:
                    return False
                self._set_selected_node(node)
                return self._toggle_node(node)
        return False

    def _node_by_id(self, node_id: str) -> Optional[_MindNode]:
        if self._draft_runtime_node and self._draft_runtime_node.node_id == node_id:
            return self._draft_runtime_node
        if not self._latest_root:
            return None
        return next((item for item in self._collect_nodes(self._latest_root) if item.node_id == node_id), None)

    def _toggle_node(self, node: _MindNode) -> bool:
        if not node.children:
            return False
        self._set_selected_node(node)
        current_depth = self._scope_depth(node)
        if current_depth > 0:
            self._scope_expansion_depths.pop(node.node_id, None)
        else:
            self._scope_expansion_depths[node.node_id] = 1
        self._update_level_controls()
        self.refresh()
        return True

    def _toggle_node_on_double_click(self, event: QMouseEvent) -> bool:
        svg_pos = self._event_svg_position(event)
        if not svg_pos:
            return False
        node = self._node_at_position(*svg_pos)
        if not node:
            return False
        return self._toggle_node(node)

    def _activate_node_at(self, event: QMouseEvent) -> bool:
        if self._draft_heading is not None:
            return False
        svg_pos = self._event_svg_position(event)
        if not svg_pos:
            return False
        node = self._node_at_position(*svg_pos)
        if not node or not self._current_page_path or node.line_number <= 0:
            return False
        self._set_selected_node(node)
        self._mark_activation_source("mouse")
        self.headingActivated.emit(self._current_page_path, node.line_number)
        return True

    def keyPressEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        if event.type() != QEvent.KeyPress:
            event.ignore()
            return
        key = event.key()
        mods = event.modifiers()
        if key == Qt.Key_J and mods == Qt.AltModifier:
            if self.zoom_selected_node(1):
                event.accept()
                return
        if key == Qt.Key_K and mods == Qt.AltModifier:
            if self.zoom_selected_node(-1):
                event.accept()
                return
        if self._draft_heading is not None:
            if self._handle_draft_keypress(event):
                event.accept()
                return
        if key == Qt.Key_Escape and not mods:
            if self._reset_view_to_root():
                event.accept()
                return
        if key == Qt.Key_PageUp and not mods:
            self.decrease_heading_level()
            event.accept()
            return
        if key == Qt.Key_PageDown and not mods:
            self.increase_heading_level()
            event.accept()
            return
        if key in (Qt.Key_Return, Qt.Key_Enter) and mods == Qt.ShiftModifier:
            if self._activate_selected_node(keep_focus=False):
                event.accept()
                return
        if key in (Qt.Key_Return, Qt.Key_Enter) and mods == Qt.ControlModifier:
            if self._activate_selected_node(keep_focus=True):
                event.accept()
                return
        if key in (Qt.Key_Return, Qt.Key_Enter) and not mods:
            if self._start_draft_heading(as_child=False):
                event.accept()
                return
        if key == Qt.Key_Insert and not mods:
            if self._start_draft_heading(as_child=True):
                event.accept()
                return
        if key == Qt.Key_Space and not mods:
            node = self._selected_node()
            if node and self._toggle_node(node):
                event.accept()
                return
        if key in (Qt.Key_Right, Qt.Key_L) and not mods:
            node = self._selected_node()
            if node and node.children and self._scope_depth(node) <= 0:
                if self._toggle_node(node):
                    event.accept()
                    return
        direction = None
        if key == Qt.Key_Left or (key == Qt.Key_H and not mods):
            direction = "left"
        elif key == Qt.Key_Right or (key == Qt.Key_L and not mods):
            direction = "right"
        elif key == Qt.Key_Up or (key == Qt.Key_K and not mods):
            direction = "up"
        elif key == Qt.Key_Down or (key == Qt.Key_J and not mods):
            direction = "down"
        if direction and self._move_selection(direction):
            event.accept()
            return
        super().keyPressEvent(event)

    def eventFilter(self, watched, event):  # type: ignore[override]
        if watched is self.preview_label and isinstance(event, QMouseEvent):
            if self._draft_heading is not None and event.type() == event.Type.MouseButtonPress:
                event.accept()
                return True
            if event.type() == event.Type.MouseButtonDblClick and event.button() == Qt.LeftButton:
                self.preview_label.setFocus(Qt.MouseFocusReason)
                if self._toggle_node_on_double_click(event):
                    event.accept()
                    return True
            if event.type() == event.Type.MouseButtonPress and event.button() == Qt.LeftButton:
                self.preview_label.setFocus(Qt.MouseFocusReason)
                if self._toggle_node_at(event):
                    event.accept()
                    return True
                if self._activate_node_at(event):
                    event.accept()
                    return True
        if watched is self.preview_label and isinstance(event, QKeyEvent) and event.type() == QEvent.KeyPress:
            self.keyPressEvent(event)
            if event.isAccepted():
                return True
        if watched is self.preview_label and event.type() == QEvent.NativeGesture:
            self.preview_label.setFocus(Qt.OtherFocusReason)
        return super().eventFilter(watched, event)
