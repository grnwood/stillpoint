from __future__ import annotations

import hashlib
import math
import random
import sqlite3
from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt, Signal, QVariantAnimation, QParallelAnimationGroup, QEasingCurve
from PySide6.QtGui import QColor, QFont, QBrush, QKeyEvent, QPalette, QPen, QPainter, QPolygonF
from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsLineItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QCheckBox,
    QApplication,
    QHBoxLayout,
    QLabel,
    QSlider,
    QTextBrowser,
    QMenu,
    QVBoxLayout,
    QWidget,
)

from sp.app import config
from sp.server.adapters.files import strip_page_suffix
from .path_utils import path_to_colon
from .theme import apply_menu_theme, theme_value


@dataclass
class _NodeData:
    path: str
    label: str
    degree: int
    kind: str = "page"


class _GalaxyNodeItem(QGraphicsEllipseItem):
    def __init__(self, data: _NodeData, radius: float, color: QColor) -> None:
        super().__init__(-radius, -radius, radius * 2, radius * 2)
        self.data = data
        self.radius = radius
        self._base_brush = QBrush(color)
        self._dim_brush = QBrush(QColor(40, 40, 48))
        self._active_brush = QBrush(QColor(47, 111, 237))
        self._attach_brush = QBrush(QColor(90, 160, 120))
        self._base_pen = QPen(QColor(20, 20, 24), 1.2)
        self._active_pen = QPen(QColor(139, 184, 255), 2.0)
        self._base_label_color = QColor(235, 240, 248)
        self._active_label_color = QColor(255, 255, 255)
        self._emphasis_label_color = QColor(255, 255, 255)
        self._base_z = 2
        self._active_z = 6
        self._label_base_z = 3
        self._label_active_z = 20
        self.setBrush(self._base_brush)
        self.setPen(self._base_pen)
        self.setZValue(self._base_z)
        self.setAcceptHoverEvents(True)

        label = QGraphicsSimpleTextItem(data.label, self)
        font = QFont(label.font())
        font.setPointSize(10)
        font.setWeight(QFont.Weight.Bold)
        label.setFont(font)
        label.setBrush(QBrush(self._base_label_color))
        label.setZValue(self._label_base_z)
        label.setFlag(label.GraphicsItemFlag.ItemIgnoresTransformations, True)
        rect = label.boundingRect()
        label.setPos(-rect.width() / 2, -rect.height() / 2)
        label.setOpacity(0.82)
        self.label_item = label

    def set_theme_colors(
        self,
        base_bg: QColor,
        attach_bg: QColor,
        dim_bg: QColor,
        base_border: QColor,
        base_label: QColor,
        emphasis_label: QColor,
    ) -> None:
        self._base_brush = QBrush(base_bg)
        self._attach_brush = QBrush(attach_bg)
        self._dim_brush = QBrush(dim_bg)
        self._base_pen = QPen(base_border, 1.2)
        self._base_label_color = base_label
        self._emphasis_label_color = emphasis_label

    def set_accent_colors(self, active_bg: QColor, active_border: QColor, active_text: QColor) -> None:
        self._active_brush = QBrush(active_bg)
        self._active_pen = QPen(active_border, 2.0)
        self._active_label_color = active_text

    def set_active(self, active: bool) -> None:
        if active:
            self.setBrush(self._active_brush)
            self.setPen(self._active_pen)
            self.setZValue(self._active_z)
            self.label_item.setOpacity(1.0)
            self.label_item.setBrush(QBrush(self._active_label_color))
            self.label_item.setZValue(self._label_active_z)
        else:
            self.setBrush(self._attach_brush if self.data.kind == "attachment" else self._base_brush)
            self.setPen(self._base_pen)
            self.setZValue(self._base_z)
            self.label_item.setOpacity(0.82)
            self.label_item.setBrush(QBrush(self._base_label_color))
            self.label_item.setZValue(self._label_base_z)

    def set_dimmed(self, dimmed: bool) -> None:
        if dimmed:
            self.setBrush(self._dim_brush)
            self.label_item.setOpacity(0.42)
        else:
            self.setBrush(self._attach_brush if self.data.kind == "attachment" else self._base_brush)
            if self.label_item.opacity() < 0.82:
                self.label_item.setOpacity(0.82)

    def set_label_emphasis(self, enabled: bool) -> None:
        if enabled:
            self.label_item.setOpacity(1.0)
            self.label_item.setBrush(QBrush(self._emphasis_label_color))
            self.label_item.setZValue(self._label_active_z)
        else:
            if self.label_item.opacity() > 0.82:
                self.label_item.setOpacity(0.82)
            if self.label_item.zValue() > self._label_base_z:
                self.label_item.setZValue(self._label_base_z)


class _GalaxyEdge(QGraphicsLineItem):
    def __init__(self, source: _GalaxyNodeItem, target: _GalaxyNodeItem, arrows: bool, width_scale: float) -> None:
        super().__init__()
        self.source = source
        self.target = target
        self._arrows = arrows
        self._width_scale = max(0.5, min(3.0, width_scale))
        self._base_pen = QPen(QColor(140, 140, 170, 160), 1.2 * self._width_scale)
        self._active_pen = QPen(QColor(47, 111, 237, 255), 2.4 * self._width_scale)
        self.setPen(self._base_pen)
        self.setZValue(1)

    def set_active(self, active: bool) -> None:
        self.setPen(self._active_pen if active else self._base_pen)

    def set_theme_color(self, color: QColor) -> None:
        base = QColor(color)
        base.setAlpha(170)
        self._base_pen = QPen(base, 1.2 * self._width_scale)
        if self.pen().color() != self._active_pen.color():
            self.setPen(self._base_pen)

    def set_accent_color(self, color: QColor) -> None:
        active = QColor(color)
        active.setAlpha(255)
        self._active_pen = QPen(active, 2.4 * self._width_scale)

    def set_arrows(self, enabled: bool) -> None:
        self._arrows = bool(enabled)

    def paint(self, painter: QPainter, option, widget=None) -> None:  # type: ignore[override]
        super().paint(painter, option, widget)
        if not self._arrows:
            return
        line = self.line()
        length = line.length()
        if length <= 1.0:
            return
        angle = math.atan2(line.dy(), line.dx())
        arrow_size = 10.0 * self._width_scale
        # Pull arrowhead back so it doesn't hide under the node
        backoff = max(4.0, getattr(self.target, "radius", 0.0) + 4.0)
        ux = line.dx() / length
        uy = line.dy() / length
        dest = QPointF(line.p2().x() - ux * backoff, line.p2().y() - uy * backoff)
        p1 = dest - QPointF(math.cos(angle - math.pi / 6) * arrow_size, math.sin(angle - math.pi / 6) * arrow_size)
        p2 = dest - QPointF(math.cos(angle + math.pi / 6) * arrow_size, math.sin(angle + math.pi / 6) * arrow_size)
        painter.setBrush(self.pen().color())
        painter.setPen(Qt.NoPen)
        painter.drawPolygon(QPolygonF([dest, p1, p2]))


class GalaxyGraphView(QGraphicsView):
    nodeActivated = Signal(str, bool)
    attachmentActivated = Signal(str)
    blankCanvasClicked = Signal(QPoint)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setRenderHint(self.renderHints().Antialiasing)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setMouseTracking(True)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._nodes: dict[str, _GalaxyNodeItem] = {}
        self._edges: list[_GalaxyEdge] = []
        self._center_path: Optional[str] = None
        self._zoom = 1.0
        self._hover_path: Optional[str] = None
        self._base_positions: dict[str, QPointF] = {}
        self._spread_anim: Optional[QParallelAnimationGroup] = None
        self._arrows_enabled = True
        self._node_size_scale = 1.0
        self._edge_width_scale = 1.0
        self._link_distance_scale = 0.6
        self._selected_path: Optional[str] = None
        self._is_panning = False
        self._pan_start_pos = None
        self._vault_accent_color: Optional[str] = None
        self._active_bg = QColor(47, 111, 237)
        self._active_border = QColor(139, 184, 255)
        self._active_text = QColor(255, 255, 255)
        self._theme_colors = self._resolve_theme_colors()
        self._applying_theme = False
        self.apply_theme()

    def clear(self) -> None:
        self._scene.clear()
        self._scene.setBackgroundBrush(QBrush(self._theme_colors["canvas"]))
        self._nodes.clear()
        self._edges.clear()
        self._center_path = None
        self._hover_path = None
        self._selected_path = None
        self._is_panning = False
        self._pan_start_pos = None
        self._base_positions.clear()
        if self._spread_anim:
            self._spread_anim.stop()
            self._spread_anim = None

    def changeEvent(self, event) -> None:  # type: ignore[override]
        super().changeEvent(event)
        if not self._applying_theme and event.type() in (QEvent.PaletteChange, QEvent.ApplicationPaletteChange):
            self.apply_theme()

    def wheelEvent(self, event) -> None:  # type: ignore[override]
        if event.modifiers() & Qt.ShiftModifier:
            delta = event.angleDelta().y()
            if delta:
                self.horizontalScrollBar().setValue(
                    self.horizontalScrollBar().value() - (int(delta / 120) * 40)
                )
                event.accept()
                return
        delta = event.angleDelta().y()
        if delta == 0:
            return
        factor = 1.15 if delta > 0 else 0.87
        self._zoom = max(0.2, min(4.0, self._zoom * factor))
        self.scale(factor, factor)
        self._update_label_visibility()

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.RightButton:
            self._is_panning = True
            self._pan_start_pos = self._event_global_pos(event)
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
            return
        if event.button() == Qt.LeftButton:
            item = self.itemAt(event.position().toPoint())
            if isinstance(item, _GalaxyNodeItem) and item.data.kind == "page":
                self._set_selected_path(item.data.path, center=True)
                self.nodeActivated.emit(item.data.path, bool(event.modifiers() & Qt.ShiftModifier))
                event.accept()
                return
            if isinstance(item, _GalaxyNodeItem) and item.data.kind == "attachment":
                self._set_selected_path(item.data.path, center=True)
                self.attachmentActivated.emit(item.data.path)
                event.accept()
                return
            if item is None:
                self.blankCanvasClicked.emit(event.position().toPoint())
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if self._is_panning and self._pan_start_pos is not None:
            current = self._event_global_pos(event)
            delta = current - self._pan_start_pos
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            self._pan_start_pos = current
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.RightButton and self._is_panning:
            self._is_panning = False
            self._pan_start_pos = None
            self.setCursor(Qt.ArrowCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # type: ignore[override]
        item = self.itemAt(event.position().toPoint())
        if isinstance(item, _GalaxyNodeItem):
            self._set_selected_path(item.data.path, center=True)
            self.nodeActivated.emit(item.data.path, bool(event.modifiers() & Qt.ShiftModifier))
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        key = event.key()
        mods = event.modifiers()
        if key in (Qt.Key_Return, Qt.Key_Enter) and mods in (Qt.NoModifier, Qt.ShiftModifier):
            if self._activate_selected_node(keep_focus=mods == Qt.ShiftModifier):
                event.accept()
                return
        if key == Qt.Key_F and not mods:
            self.fit_to_graph()
            event.accept()
            return
        direction = None
        if not mods:
            if key in (Qt.Key_Up, Qt.Key_K):
                direction = "up"
            elif key in (Qt.Key_Down, Qt.Key_J):
                direction = "down"
            elif key in (Qt.Key_Left, Qt.Key_H):
                direction = "left"
            elif key in (Qt.Key_Right, Qt.Key_L):
                direction = "right"
        if direction and self._move_selection(direction):
            event.accept()
            return
        super().keyPressEvent(event)

    def set_graph(
        self,
        center_path: str,
        nodes: list[_NodeData],
        edges: list[tuple[str, str]],
        focus_paths: Optional[set[str]] = None,
        preserve_zoom: bool = False,
    ) -> None:
        previous_selected = self._selected_path
        self.clear()
        self._center_path = center_path
        positions = self._layout_positions(center_path, nodes)
        for data in nodes:
            radius = self._radius_for_degree(data.degree, data.path == center_path) * self._node_size_scale
            if data.kind == "attachment":
                color = self._theme_colors["attachment_fill"]
            else:
                color = self._theme_colors["center_fill"] if data.path == center_path else self._theme_colors["node_fill"]
            item = _GalaxyNodeItem(data, radius, color)
            item.set_theme_colors(
                color,
                self._theme_colors["attachment_fill"],
                self._theme_colors["dim_fill"],
                self._theme_colors["node_border"],
                self._theme_colors["label"],
                self._theme_colors["label_emphasis"],
            )
            item.set_accent_colors(self._active_bg, self._active_border, self._active_text)
            pos = positions.get(data.path, QPointF(0, 0))
            item.setPos(pos)
            item.hoverEnterEvent = lambda _e, p=data.path: self._on_hover(p)  # type: ignore[assignment]
            item.hoverLeaveEvent = lambda _e: self._on_hover(None)  # type: ignore[assignment]
            self._scene.addItem(item)
            self._nodes[data.path] = item
            self._base_positions[data.path] = QPointF(pos)

        edge_limit = 2500
        if len(edges) > edge_limit:
            edges = [e for e in edges if center_path in e][:edge_limit]
        for source_path, target_path in edges:
            source = self._nodes.get(source_path)
            target = self._nodes.get(target_path)
            if not source or not target:
                continue
            edge = _GalaxyEdge(source, target, self._arrows_enabled, self._edge_width_scale)
            edge.set_theme_color(self._theme_colors["edge"])
            edge.set_accent_color(self._active_border)
            self._scene.addItem(edge)
            self._edges.append(edge)

        self._update_edges()
        self._update_label_visibility()
        if center_path in self._nodes:
            self.centerOn(self._nodes[center_path])
        self._set_selected_path(previous_selected if previous_selected in self._nodes else center_path)
        focus_rect = None
        if focus_paths:
            rects = [
                self._nodes[p].sceneBoundingRect()
                for p in focus_paths
                if p in self._nodes
            ]
            if rects:
                focus_rect = rects[0]
                for rect in rects[1:]:
                    focus_rect = focus_rect.united(rect)
        if not preserve_zoom:
            target = focus_rect or self._scene.itemsBoundingRect()
            self.fitInView(target.adjusted(-40, -40, 40, 40), Qt.KeepAspectRatio)
            self._zoom = max(0.2, min(4.0, self.transform().m11()))
            self._update_label_visibility()

    def fit_to_graph(self) -> None:
        target = self._scene.itemsBoundingRect()
        if target.isNull():
            return
        self.fitInView(target.adjusted(-40, -40, 40, 40), Qt.KeepAspectRatio)
        self._zoom = max(0.2, min(4.0, self.transform().m11()))
        self._update_label_visibility()

    def _layout_positions(self, center_path: str, nodes: list[_NodeData]) -> dict[str, QPointF]:
        positions: dict[str, QPointF] = {}
        max_radius = max(420.0, math.sqrt(len(nodes) + 1) * 55.0) * self._link_distance_scale
        for node in nodes:
            if node.path == center_path:
                positions[node.path] = QPointF(0, 0)
                continue
            seed = int(hashlib.sha1(node.path.encode("utf-8")).hexdigest()[:8], 16)
            rng = random.Random(seed)
            angle = rng.random() * math.tau
            radius = (rng.random() ** 0.5) * max_radius
            positions[node.path] = QPointF(math.cos(angle) * radius, math.sin(angle) * radius)

        neighbors = [n for n in nodes if n.path != center_path]
        ring = 150.0 * self._link_distance_scale
        for idx, node in enumerate(neighbors[:48]):
            angle = (idx / max(1, len(neighbors[:48]))) * math.tau
            jitter = 24.0
            positions[node.path] = QPointF(
                math.cos(angle) * ring + (random.random() - 0.5) * jitter,
                math.sin(angle) * ring + (random.random() - 0.5) * jitter,
            )
        return positions

    def _radius_for_degree(self, degree: int, is_center: bool) -> float:
        base = 10.0
        scale = 3.5
        radius = base + scale * math.sqrt(max(0, degree))
        if is_center:
            radius += 8.0
        return min(48.0, max(8.0, radius))

    def _update_edges(self) -> None:
        for edge in self._edges:
            edge.setLine(
                edge.source.pos().x(),
                edge.source.pos().y(),
                edge.target.pos().x(),
                edge.target.pos().y(),
            )

    def _update_label_visibility(self) -> None:
        zoomed_out = self._zoom < 0.35
        for node in self._nodes.values():
            node.label_item.setVisible(not zoomed_out)

    def _on_hover(self, path: Optional[str]) -> None:
        self._hover_path = path
        self._apply_focus_effect(path or self._selected_path)

    def _apply_focus_effect(self, path: Optional[str]) -> None:
        if not path:
            for node in self._nodes.values():
                node.set_active(node.data.path in {self._center_path, self._selected_path})
                node.set_dimmed(False)
                node.set_label_emphasis(False)
            for edge in self._edges:
                edge.set_active(False)
            self._animate_node_positions(self._base_positions)
            return
        connected_paths: set[str] = set()
        for edge in self._edges:
            if edge.source.data.path == path or edge.target.data.path == path:
                connected_paths.add(edge.source.data.path)
                connected_paths.add(edge.target.data.path)
        active_paths = {path, self._center_path, self._selected_path}
        for node in self._nodes.values():
            is_active = node.data.path in active_paths
            node.set_dimmed(not is_active)
            node.set_active(is_active)
            if not is_active:
                node.set_label_emphasis(node.data.path in connected_paths)
        for edge in self._edges:
            active = edge.source.data.path == path or edge.target.data.path == path
            edge.set_active(active)
        self._animate_node_positions(self._spread_positions(path, connected_paths))

    def _spread_positions(self, path: str, connected_paths: set[str]) -> dict[str, QPointF]:
        hover_base = self._base_positions.get(path)
        if hover_base is None:
            hover_base = self._nodes.get(path).pos() if path in self._nodes else QPointF(0, 0)
        spread = 1.45
        min_offset = 28.0
        targets: dict[str, QPointF] = {}
        for node_path, base in self._base_positions.items():
            if node_path == path or node_path not in connected_paths:
                targets[node_path] = base
                continue
            delta = base - hover_base
            dist = math.hypot(delta.x(), delta.y())
            if dist < 1.0:
                seed = int(hashlib.sha1(node_path.encode("utf-8")).hexdigest()[:8], 16)
                rng = random.Random(seed)
                angle = rng.random() * math.tau
                delta = QPointF(math.cos(angle) * min_offset, math.sin(angle) * min_offset)
            elif dist < min_offset:
                scale = min_offset / max(dist, 1.0)
                delta = QPointF(delta.x() * scale, delta.y() * scale)
            targets[node_path] = hover_base + QPointF(delta.x() * spread, delta.y() * spread)
        return targets

    def _animate_node_positions(self, targets: dict[str, QPointF]) -> None:
        if self._spread_anim:
            self._spread_anim.stop()
            self._spread_anim = None
        group = QParallelAnimationGroup(self)
        duration = 220
        for path, node in self._nodes.items():
            target = targets.get(path)
            if target is None:
                continue
            start = node.pos()
            if start == target:
                continue
            anim = QVariantAnimation(self)
            anim.setStartValue(start)
            anim.setEndValue(target)
            anim.setDuration(duration)
            anim.setEasingCurve(QEasingCurve.OutCubic)
            anim.valueChanged.connect(lambda value, n=node: n.setPos(value))
            anim.valueChanged.connect(lambda _value: self._update_edges())
            group.addAnimation(anim)
        if group.animationCount() == 0:
            return
        self._spread_anim = group
        group.start()

    def set_arrow_mode(self, enabled: bool) -> None:
        self._arrows_enabled = bool(enabled)
        for edge in self._edges:
            edge.set_arrows(self._arrows_enabled)
        self.viewport().update()

    def set_link_distance_scale(self, scale: float) -> None:
        self._link_distance_scale = max(0.6, min(2.5, float(scale)))

    def set_node_size_scale(self, scale: float) -> None:
        self._node_size_scale = max(0.6, min(2.5, float(scale)))

    def set_edge_width_scale(self, scale: float) -> None:
        self._edge_width_scale = max(0.6, min(2.5, float(scale)))

    def set_vault_accent_color(self, color_hex: Optional[str]) -> None:
        self._vault_accent_color = (color_hex or "").strip() or None
        active_bg, active_border, active_text = self._accent_graph_colors(self._vault_accent_color)
        self._active_bg = active_bg
        self._active_border = active_border
        self._active_text = active_text
        for node in self._nodes.values():
            node.set_accent_colors(active_bg, active_border, active_text)
        for edge in self._edges:
            edge.set_accent_color(active_border)
        self._apply_focus_effect(self._hover_path or self._selected_path)
        self.viewport().update()

    def apply_theme(self) -> None:
        if self._applying_theme:
            return
        self._applying_theme = True
        try:
            self._apply_theme()
        finally:
            self._applying_theme = False

    def _apply_theme(self) -> None:
        self._theme_colors = self._resolve_theme_colors()
        canvas = self._theme_colors["canvas"]
        text = self._theme_colors["label"]
        self.setPalette(self._editor_theme_palette())
        self._scene.setBackgroundBrush(QBrush(canvas))
        self.setStyleSheet(
            f"QGraphicsView {{ background: {canvas.name()}; color: {text.name()}; border: none; }}"
            f"QGraphicsView::viewport {{ background: {canvas.name()}; }}"
        )
        for node in self._nodes.values():
            base = self._theme_colors["attachment_fill"] if node.data.kind == "attachment" else self._theme_colors["node_fill"]
            if node.data.path == self._center_path:
                base = self._theme_colors["center_fill"]
            node.set_theme_colors(
                base,
                self._theme_colors["attachment_fill"],
                self._theme_colors["dim_fill"],
                self._theme_colors["node_border"],
                self._theme_colors["label"],
                self._theme_colors["label_emphasis"],
            )
            node.set_accent_colors(self._active_bg, self._active_border, self._active_text)
        for edge in self._edges:
            edge.set_theme_color(self._theme_colors["edge"])
            edge.set_accent_color(self._active_border)
        self._apply_focus_effect(self._hover_path or self._selected_path)
        self.viewport().update()

    @staticmethod
    def _editor_theme_palette() -> QPalette:
        palette = QPalette(QApplication.palette())
        bg = theme_value("markdown_editor.base.bg", None)
        text = theme_value("markdown_editor.base.text", None)
        selection_bg = theme_value("markdown_editor.base.selection_bg", None)
        selection_text = theme_value("markdown_editor.base.selection_text", None)
        base_color = palette.color(QPalette.Base)
        if bg is not None:
            base_color = QColor(str(bg))
            palette.setColor(QPalette.Window, base_color)
            palette.setColor(QPalette.Base, base_color)
            palette.setColor(
                QPalette.AlternateBase,
                base_color.lighter(112) if base_color.lightness() < 128 else base_color.darker(104),
            )
            palette.setColor(QPalette.Button, base_color)
        if text is not None:
            text_color = QColor(str(text))
            palette.setColor(QPalette.WindowText, text_color)
            palette.setColor(QPalette.Text, text_color)
            palette.setColor(QPalette.ButtonText, text_color)
        if selection_bg is not None:
            palette.setColor(QPalette.Highlight, QColor(str(selection_bg)))
        if selection_text is not None:
            palette.setColor(QPalette.HighlightedText, QColor(str(selection_text)))
        border_color = QColor(base_color)
        border_color = border_color.lighter(170) if border_color.lightness() < 128 else border_color.darker(135)
        palette.setColor(QPalette.Mid, border_color)
        return palette

    @classmethod
    def _resolve_theme_colors(cls) -> dict[str, QColor]:
        palette = cls._editor_theme_palette()
        base = palette.color(QPalette.Base)
        label = cls._contrast_text_color(base)
        border = palette.color(QPalette.Mid)
        is_light = base.lightness() > 128
        return {
            "canvas": QColor(base),
            "label": QColor(label),
            "label_emphasis": QColor(label),
            "node_fill": base.darker(106) if is_light else base.lighter(150),
            "center_fill": base.darker(114) if is_light else base.lighter(175),
            "attachment_fill": QColor("#d1fae5") if is_light else QColor("#164e3b"),
            "dim_fill": base.darker(112) if is_light else base.lighter(118),
            "node_border": QColor(border),
            "edge": border.darker(115) if is_light else border.lighter(135),
        }

    @classmethod
    def _accent_graph_colors(cls, color_hex: Optional[str]) -> tuple[QColor, QColor, QColor]:
        accent = QColor((color_hex or "").strip())
        if not accent.isValid():
            accent = QColor(47, 111, 237)
        bg = QColor(accent)
        if bg.lightness() < 72:
            bg = bg.lighter(150)
        elif bg.lightness() > 205:
            bg = bg.darker(125)
        border = QColor(bg)
        border = border.lighter(135) if border.lightness() < 160 else border.darker(115)
        return bg, border, cls._contrast_text_color(bg)

    @staticmethod
    def _contrast_text_color(bg: QColor) -> QColor:
        return QColor("#111111") if bg.lightness() >= 150 else QColor("#ffffff")

    @staticmethod
    def _event_global_pos(event):
        try:
            return event.globalPosition().toPoint()
        except Exception:
            return event.globalPos()

    def _set_selected_path(self, path: Optional[str], *, center: bool = False) -> bool:
        if not path or path not in self._nodes:
            return False
        self._selected_path = path
        self._apply_focus_effect(self._hover_path or self._selected_path)
        if center:
            self.centerOn(self._nodes[path])
        return True

    def _activate_selected_node(self, *, keep_focus: bool = False) -> bool:
        path = self._selected_path or self._center_path
        if not path:
            return False
        node = self._nodes.get(path)
        if not node:
            return False
        if node.data.kind == "attachment":
            self.attachmentActivated.emit(path)
        else:
            self.nodeActivated.emit(path, keep_focus)
        return True

    def _move_selection(self, direction: str) -> bool:
        if not self._nodes:
            return False
        current_path = self._selected_path if self._selected_path in self._nodes else self._center_path
        if current_path not in self._nodes:
            current_path = next(iter(self._nodes))
        current = self._nodes.get(current_path)
        if current is None:
            return False
        origin = current.pos()
        candidates: list[tuple[float, str]] = []
        for path, node in self._nodes.items():
            if path == current_path:
                continue
            delta = node.pos() - origin
            dx = delta.x()
            dy = delta.y()
            if direction == "left" and dx >= -1:
                continue
            if direction == "right" and dx <= 1:
                continue
            if direction == "up" and dy >= -1:
                continue
            if direction == "down" and dy <= 1:
                continue
            primary = abs(dx) if direction in {"left", "right"} else abs(dy)
            secondary = abs(dy) if direction in {"left", "right"} else abs(dx)
            candidates.append((secondary * 4.0 + primary, path))
        if not candidates:
            return False
        _, next_path = min(candidates, key=lambda item: item[0])
        return self._set_selected_path(next_path, center=True)


class LinkNavigatorPanel(QWidget):
    """Galaxy-style link navigator."""

    pageActivated = Signal(str, bool)
    openInWindowRequested = Signal(str)
    backRequested = Signal()
    forwardRequested = Signal()
    homeRequested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:  # type: ignore[override]
        super().__init__(parent)
        self.current_page: Optional[str] = None
        self.setFocusPolicy(Qt.StrongFocus)
        self._applying_theme = False
        self._vault_accent_color: Optional[str] = self._load_vault_accent_color()
        self._show_arrows = False
        self._show_orphans = True
        self._show_attachments = False
        self._show_raw = False

        self.title_label = QLabel("Link Navigator")

        self.graph_view = GalaxyGraphView()
        self.graph_view.set_vault_accent_color(self._vault_accent_color)
        self.graph_view.nodeActivated.connect(self.pageActivated.emit)
        self.graph_view.attachmentActivated.connect(self._open_attachment_node)
        self.graph_view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.graph_view.blankCanvasClicked.connect(self._open_graph_menu)

        self.raw_view = QTextBrowser()
        self.raw_view.setOpenLinks(False)
        self.raw_view.setOpenExternalLinks(False)
        self.raw_view.anchorClicked.connect(lambda url: self.pageActivated.emit(url.toString(), False))
        self.raw_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.raw_view.customContextMenuRequested.connect(self._open_raw_menu)

        controls = QHBoxLayout()
        controls.setContentsMargins(8, 2, 8, 2)
        self.arrows_checkbox = QCheckBox("Arrows")
        self.arrows_checkbox.setChecked(False)
        self.arrows_checkbox.toggled.connect(self._toggle_arrows)
        self.orphans_checkbox = QCheckBox("Orphans")
        self.orphans_checkbox.setChecked(True)
        self.orphans_checkbox.toggled.connect(self._toggle_orphans)
        self.attachments_checkbox = QCheckBox("Attachments")
        self.attachments_checkbox.setChecked(False)
        self.attachments_checkbox.toggled.connect(self._toggle_attachments)
        size_label = QLabel("Node size")
        self.node_size_slider = QSlider(Qt.Horizontal)
        self.node_size_slider.setRange(6, 20)
        self.node_size_slider.setValue(12)
        self.node_size_slider.valueChanged.connect(self._update_node_size)
        distance_label = QLabel("Link distance")
        self.link_distance_slider = QSlider(Qt.Horizontal)
        self.link_distance_slider.setRange(6, 20)
        self.link_distance_slider.setValue(6)
        self.link_distance_slider.valueChanged.connect(self._update_link_distance)

        for widget in (
            self.arrows_checkbox,
            self.orphans_checkbox,
            self.attachments_checkbox,
            size_label,
            self.node_size_slider,
            distance_label,
            self.link_distance_slider,
        ):
            controls.addWidget(widget)
        controls.addStretch(1)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.title_label)
        layout.addLayout(controls)
        layout.addWidget(self.graph_view, 1)
        layout.addWidget(self.raw_view, 1)
        self.setLayout(layout)
        self.apply_theme()

    @staticmethod
    def _load_vault_accent_color() -> Optional[str]:
        try:
            color = (config.load_vault_accent_color() or "").strip()
        except Exception:
            return None
        return color if color.startswith("#") else None

    def set_vault_accent_color(self, color_hex: Optional[str]) -> None:
        candidate = (color_hex or "").strip()
        self._vault_accent_color = candidate if candidate.startswith("#") else None
        self.graph_view.set_vault_accent_color(self._vault_accent_color)

    def apply_theme(self) -> None:
        if self._applying_theme:
            return
        self._applying_theme = True
        try:
            self._apply_theme()
        finally:
            self._applying_theme = False

    def _apply_theme(self) -> None:
        palette = GalaxyGraphView._editor_theme_palette()
        base = palette.color(QPalette.Base)
        text = palette.color(QPalette.Text)
        border = palette.color(QPalette.Mid)
        button = palette.color(QPalette.Button)
        self.setPalette(palette)
        self.graph_view.apply_theme()
        self.title_label.setStyleSheet(
            "font-weight: bold; padding: 6px 8px; "
            f"color: {text.name()}; background: {base.name()};"
        )
        self.raw_view.setPalette(palette)
        self.raw_view.setStyleSheet(
            "QTextBrowser {"
            " font-family: monospace;"
            f" color: {text.name()};"
            f" background: {base.name()};"
            f" border: 1px solid {border.name()};"
            "}"
        )
        self.setStyleSheet(
            "LinkNavigatorPanel, QWidget {"
            f" background: {base.name()};"
            f" color: {text.name()};"
            "}"
            "QCheckBox, QLabel {"
            f" color: {text.name()};"
            f" background: {base.name()};"
            "}"
            "QSlider::groove:horizontal {"
            f" background: {border.name()};"
            " height: 4px;"
            " border-radius: 2px;"
            "}"
            "QSlider::handle:horizontal {"
            f" background: {button.name()};"
            f" border: 1px solid {border.name()};"
            " width: 12px;"
            " margin: -5px 0px;"
            " border-radius: 6px;"
            "}"
        )

    def set_page(self, page_path: Optional[str]) -> None:
        self.current_page = page_path
        self.refresh()

    def changeEvent(self, event) -> None:  # type: ignore[override]
        super().changeEvent(event)
        if not self._applying_theme and event.type() in (QEvent.PaletteChange, QEvent.ApplicationPaletteChange):
            self.apply_theme()

    def refresh(self, page_path: Optional[str] = None, *, preserve_zoom: bool = False) -> None:
        if page_path is not None:
            self.current_page = page_path
        if not self.current_page or not config.has_active_vault():
            self.graph_view.clear()
            self.title_label.setText("Link Navigator")
            return
        if self._show_raw:
            self._update_raw_view()
            self.graph_view.hide()
            self.raw_view.show()
            return
        self.raw_view.hide()
        self.graph_view.show()
        nodes, edges = self._load_vault_graph()
        center = self.current_page
        node_by_path = {n.path: n for n in nodes}
        if center not in node_by_path:
            node_by_path[center] = _NodeData(center, self._label_for_path(center, {}), 0)
        linked_paths = {center}
        for src, dst in edges:
            if src == center:
                linked_paths.add(dst)
            elif dst == center:
                linked_paths.add(src)
        folder_paths = {center}
        if self._show_orphans:
            folder_prefix = self._folder_prefix(center)
            if folder_prefix:
                for path in node_by_path:
                    if path.startswith(folder_prefix):
                        folder_paths.add(path)
        visible_paths = linked_paths | folder_paths
        filtered_nodes = [node_by_path[p] for p in node_by_path if p in visible_paths]
        filtered_edges = [
            (src, dst)
            for src, dst in edges
            if src in visible_paths and dst in visible_paths and (src == center or dst == center)
        ]
        attachment_nodes, attachment_edges = self._collect_attachment_nodes(visible_paths)
        if self._show_attachments:
            filtered_nodes.extend(attachment_nodes)
            filtered_edges.extend(attachment_edges)
        self.graph_view.set_arrow_mode(self._show_arrows)
        self.graph_view.set_graph(
            center,
            filtered_nodes,
            filtered_edges,
            focus_paths=visible_paths,
            preserve_zoom=preserve_zoom,
        )
        self.title_label.setText(f"Link Navigator: {self._label_for_path(center, {})}")

    def reload_mode_from_config(self) -> None:
        return

    def reload_layout_from_config(self) -> None:
        return

    def set_navigation_filter(self, path: Optional[str], refresh: bool = True) -> None:
        if refresh:
            self.refresh(self.current_page)

    def _toggle_arrows(self, checked: bool) -> None:
        self._show_arrows = bool(checked)
        self.graph_view.set_arrow_mode(self._show_arrows)

    def _toggle_orphans(self, checked: bool) -> None:
        self._show_orphans = bool(checked)
        self.refresh(self.current_page)

    def _toggle_attachments(self, checked: bool) -> None:
        self._show_attachments = bool(checked)
        self.refresh(self.current_page)

    def _update_link_distance(self, value: int) -> None:
        self.graph_view.set_link_distance_scale(value / 10.0)
        self.refresh(self.current_page, preserve_zoom=True)

    def _update_node_size(self, value: int) -> None:
        self.graph_view.set_node_size_scale(value / 10.0)
        self.refresh(self.current_page)

    def _load_vault_graph(self) -> tuple[list[_NodeData], list[tuple[str, str]]]:
        conn = None
        try:
            conn = config._connect_to_vault_db()
        except Exception:
            return [], []
        nodes: list[_NodeData] = []
        edges: list[tuple[str, str]] = []
        titles: dict[str, str] = {}
        try:
            try:
                rows = conn.execute("SELECT path, title FROM pages WHERE deleted = 0").fetchall()
            except sqlite3.OperationalError:
                rows = conn.execute("SELECT path, title FROM pages").fetchall()
            titles = {row[0]: (row[1] or "") for row in rows}
            link_rows = conn.execute("SELECT from_path, to_path FROM links").fetchall()
            edges = [(row[0], row[1]) for row in link_rows if row[0] and row[1]]
        except Exception:
            return [], []
        finally:
            try:
                conn.close()
            except Exception:
                pass

        degree_map: dict[str, int] = {}
        for src, dst in edges:
            degree_map[src] = degree_map.get(src, 0) + 1
            degree_map[dst] = degree_map.get(dst, 0) + 1

        for path, title in titles.items():
            if not path:
                continue
            nodes.append(_NodeData(path=path, label=self._label_for_path(path, titles), degree=degree_map.get(path, 0)))
        return nodes, edges

    def _label_for_path(self, path: str, titles: dict[str, str]) -> str:
        if titles and path in titles and titles[path]:
            return titles[path]
        colon = path_to_colon(path)
        if colon:
            return colon.split(":")[-1] or colon
        leaf = path.rsplit("/", 1)[-1] or path
        leaf = strip_page_suffix(leaf)
        return leaf

    def _collect_attachment_nodes(self, visible_paths: set[str]) -> tuple[list[_NodeData], list[tuple[str, str]]]:
        nodes: list[_NodeData] = []
        edges: list[tuple[str, str]] = []
        for page_path in visible_paths:
            attachments = config.list_page_attachments(page_path) or []
            for entry in attachments:
                if not isinstance(entry, dict):
                    continue
                attachment_path = entry.get("attachment_path") or entry.get("stored_path")
                if not attachment_path:
                    continue
                name = attachment_path.rsplit("/", 1)[-1]
                node_id = f"{page_path}::attach::{name}"
                nodes.append(_NodeData(path=node_id, label=name, degree=0, kind="attachment"))
                edges.append((page_path, node_id))
        return nodes, edges

    def _open_attachment_node(self, node_id: str) -> None:
        if "::attach::" not in node_id:
            return
        page_path, name = node_id.split("::attach::", 1)
        if not page_path or not name:
            return
        attachments = config.list_page_attachments(page_path) or []
        attachment_path = None
        for entry in attachments:
            if not isinstance(entry, dict):
                continue
            candidate = entry.get("attachment_path") or entry.get("stored_path")
            if candidate and candidate.rsplit("/", 1)[-1] == name:
                attachment_path = candidate
                break
        if not attachment_path:
            return
        if attachment_path.startswith("http://") or attachment_path.startswith("https://"):
            QDesktopServices.openUrl(QUrl(attachment_path))
            return
        vault_root = config.get_active_vault()
        if not vault_root:
            return
        if attachment_path.startswith("/"):
            local_path = f"{vault_root}{attachment_path}"
        else:
            local_path = f"{vault_root}/{attachment_path}"
        QDesktopServices.openUrl(QUrl.fromLocalFile(local_path))

    def _open_graph_menu(self, pos) -> None:
        menu = QMenu(self)
        self._apply_link_menu_theme(menu)
        menu.addAction("Back", self.backRequested.emit)
        menu.addAction("Forward", self.forwardRequested.emit)
        menu.addAction("Home", self.homeRequested.emit)
        menu.addSeparator()
        menu.addAction("Show Raw Links", self._show_raw_links)
        menu.exec(self.graph_view.mapToGlobal(pos))

    def _open_raw_menu(self, pos) -> None:
        menu = QMenu(self)
        self._apply_link_menu_theme(menu)
        menu.addAction("Back", self.backRequested.emit)
        menu.addAction("Forward", self.forwardRequested.emit)
        menu.addAction("Home", self.homeRequested.emit)
        menu.addSeparator()
        menu.addAction("Show Graph", self._show_graph)
        menu.exec(self.raw_view.mapToGlobal(pos))

    def _apply_link_menu_theme(self, menu: QMenu) -> None:
        apply_menu_theme(menu, self)

    def _show_raw_links(self) -> None:
        self._show_raw = True
        self.refresh(self.current_page)

    def _show_graph(self) -> None:
        self._show_raw = False
        self.refresh(self.current_page)

    def _update_raw_view(self) -> None:
        if not self.current_page:
            self.raw_view.setPlainText("No page selected.")
            return
        relations = config.fetch_link_relations(self.current_page)
        titles = config.fetch_page_titles({self.current_page, *relations["incoming"], *relations["outgoing"]})
        center_label = self._label_for_path(self.current_page, titles)

        def _link_html(path: str, arrow: str) -> str:
            colon = path_to_colon(path) or path
            label = self._label_for_path(path, titles)
            return f"{arrow} <a href='{path}'>:{colon}</a> ({label})"

        parts = [f"<b>Page:</b> {center_label}", "<br><b>Links from here:</b>"]
        if relations["outgoing"]:
            parts.extend(_link_html(p, "→") for p in relations["outgoing"])
        else:
            parts.append("(none)")
        parts.append("<br><b>Links to here:</b>")
        if relations["incoming"]:
            parts.extend(_link_html(p, "←") for p in relations["incoming"])
        else:
            parts.append("(none)")
        self.raw_view.setHtml("<br>".join(parts))

    @staticmethod
    def _folder_prefix(path: str) -> str:
        if not path or not path.startswith("/"):
            return ""
        if "/" not in path.lstrip("/"):
            return "/"
        folder = path.rsplit("/", 1)[0]
        return folder.rstrip("/") + "/"
