from __future__ import annotations

import hashlib
import math
import random
import sqlite3
from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import QPointF, Qt, Signal, QVariantAnimation, QParallelAnimationGroup, QEasingCurve
from PySide6.QtGui import QColor, QFont, QBrush, QPen, QPainter, QPolygonF
from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsLineItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QCheckBox,
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
        self._active_brush = QBrush(QColor(255, 220, 140))
        self._attach_brush = QBrush(QColor(90, 160, 120))
        self._base_pen = QPen(QColor(20, 20, 24), 1.2)
        self._active_pen = QPen(QColor(255, 245, 220), 2.0)
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
        label.setBrush(QBrush(QColor(200, 200, 210)))
        label.setZValue(self._label_base_z)
        label.setFlag(label.GraphicsItemFlag.ItemIgnoresTransformations, True)
        rect = label.boundingRect()
        label.setPos(-rect.width() / 2, -rect.height() / 2)
        label.setOpacity(0.25)
        self.label_item = label

    def set_active(self, active: bool) -> None:
        if active:
            self.setBrush(self._active_brush)
            self.setPen(self._active_pen)
            self.setZValue(self._active_z)
            self.label_item.setOpacity(1.0)
            self.label_item.setBrush(QBrush(QColor(255, 255, 255)))
            self.label_item.setZValue(self._label_active_z)
        else:
            self.setBrush(self._attach_brush if self.data.kind == "attachment" else self._base_brush)
            self.setPen(self._base_pen)
            self.setZValue(self._base_z)
        self.label_item.setOpacity(0.25)
        self.label_item.setBrush(QBrush(QColor(200, 200, 210)))
        self.label_item.setZValue(self._label_base_z)

    def set_dimmed(self, dimmed: bool) -> None:
        if dimmed:
            self.setBrush(self._dim_brush)
            self.label_item.setOpacity(0.08)
        else:
            self.setBrush(self._attach_brush if self.data.kind == "attachment" else self._base_brush)
            if self.label_item.opacity() < 0.25:
                self.label_item.setOpacity(0.25)

    def set_label_emphasis(self, enabled: bool) -> None:
        if enabled:
            self.label_item.setOpacity(1.0)
            self.label_item.setBrush(QBrush(QColor(245, 245, 245)))
            self.label_item.setZValue(self._label_active_z)
        else:
            if self.label_item.opacity() > 0.25:
                self.label_item.setOpacity(0.25)
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
        self._active_pen = QPen(QColor(255, 220, 140, 255), 2.4 * self._width_scale)
        self.setPen(self._base_pen)
        self.setZValue(1)

    def set_active(self, active: bool) -> None:
        self.setPen(self._active_pen if active else self._base_pen)

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
    nodeActivated = Signal(str)
    attachmentActivated = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setRenderHint(self.renderHints().Antialiasing)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setMouseTracking(True)
        self.setStyleSheet(
            "background: qradialgradient(cx:0.5, cy:0.5, radius:0.9, "
            "fx:0.5, fy:0.45, stop:0 rgba(10,10,14,255), stop:1 rgba(2,2,4,255));"
        )
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

    def clear(self) -> None:
        self._scene.clear()
        self._nodes.clear()
        self._edges.clear()
        self._center_path = None
        self._hover_path = None
        self._base_positions.clear()
        if self._spread_anim:
            self._spread_anim.stop()
            self._spread_anim = None

    def wheelEvent(self, event) -> None:  # type: ignore[override]
        delta = event.angleDelta().y()
        if delta == 0:
            return
        factor = 1.15 if delta > 0 else 0.87
        self._zoom = max(0.2, min(4.0, self._zoom * factor))
        self.scale(factor, factor)
        self._update_label_visibility()

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.LeftButton:
            item = self.itemAt(event.position().toPoint())
            if isinstance(item, _GalaxyNodeItem) and item.data.kind == "page":
                self.nodeActivated.emit(item.data.path)
                event.accept()
                return
            if isinstance(item, _GalaxyNodeItem) and item.data.kind == "attachment":
                self.attachmentActivated.emit(item.data.path)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # type: ignore[override]
        item = self.itemAt(event.position().toPoint())
        if isinstance(item, _GalaxyNodeItem):
            self.nodeActivated.emit(item.data.path)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def set_graph(
        self,
        center_path: str,
        nodes: list[_NodeData],
        edges: list[tuple[str, str]],
        focus_paths: Optional[set[str]] = None,
        preserve_zoom: bool = False,
    ) -> None:
        self.clear()
        self._center_path = center_path
        positions = self._layout_positions(center_path, nodes)
        for data in nodes:
            radius = self._radius_for_degree(data.degree, data.path == center_path) * self._node_size_scale
            if data.kind == "attachment":
                color = QColor(80, 140, 110)
            else:
                color = QColor(90, 120, 180) if data.path == center_path else QColor(70, 80, 110)
            item = _GalaxyNodeItem(data, radius, color)
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
            self._scene.addItem(edge)
            self._edges.append(edge)

        self._update_edges()
        self._update_label_visibility()
        if center_path in self._nodes:
            self.centerOn(self._nodes[center_path])
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
        if not path:
            for node in self._nodes.values():
                node.set_active(node.data.path == self._center_path)
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
        for node in self._nodes.values():
            node.set_dimmed(node.data.path != path and node.data.path != self._center_path)
            node.set_active(node.data.path == path or node.data.path == self._center_path)
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


class LinkNavigatorPanel(QWidget):
    """Galaxy-style link navigator."""

    pageActivated = Signal(str)
    openInWindowRequested = Signal(str)
    backRequested = Signal()
    forwardRequested = Signal()
    homeRequested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:  # type: ignore[override]
        super().__init__(parent)
        self.current_page: Optional[str] = None
        self.setFocusPolicy(Qt.StrongFocus)
        self._show_arrows = False
        self._show_orphans = True
        self._show_attachments = False
        self._show_raw = False

        self.title_label = QLabel("Link Navigator")
        self.title_label.setStyleSheet("font-weight: bold; padding: 6px 8px; color: #e6e6e6;")

        self.graph_view = GalaxyGraphView()
        self.graph_view.nodeActivated.connect(self.pageActivated.emit)
        self.graph_view.attachmentActivated.connect(self._open_attachment_node)
        self.graph_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.graph_view.customContextMenuRequested.connect(self._open_graph_menu)

        self.raw_view = QTextBrowser()
        self.raw_view.setOpenLinks(False)
        self.raw_view.setOpenExternalLinks(False)
        self.raw_view.anchorClicked.connect(lambda url: self.pageActivated.emit(url.toString()))
        self.raw_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.raw_view.customContextMenuRequested.connect(self._open_raw_menu)
        self.raw_view.setStyleSheet("font-family: monospace; color: #e6e6e6; background: #0b0b0b;")

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

    def set_page(self, page_path: Optional[str]) -> None:
        self.current_page = page_path
        self.refresh()

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
        menu.addAction("Back", self.backRequested.emit)
        menu.addAction("Forward", self.forwardRequested.emit)
        menu.addAction("Home", self.homeRequested.emit)
        menu.addSeparator()
        menu.addAction("Show Raw Links", self._show_raw_links)
        menu.exec(self.graph_view.mapToGlobal(pos))

    def _open_raw_menu(self, pos) -> None:
        menu = QMenu(self)
        menu.addAction("Back", self.backRequested.emit)
        menu.addAction("Forward", self.forwardRequested.emit)
        menu.addAction("Home", self.homeRequested.emit)
        menu.addSeparator()
        menu.addAction("Show Graph", self._show_graph)
        menu.exec(self.raw_view.mapToGlobal(pos))

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
