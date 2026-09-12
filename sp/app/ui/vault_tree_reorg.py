from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any, Optional

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
)


_JOURNAL_CONTAINER_RE = re.compile(
    r"^/Journal(?:/\d{4}(?:/\d{1,2}(?:/\d{1,2})?)?)?$", re.IGNORECASE
)


def is_protected_tree_path(path: str) -> bool:
    normalized = (path or "/").rstrip("/") or "/"
    return normalized == "/" or bool(_JOURNAL_CONTAINER_RE.fullmatch(normalized))


class _TreeNodeItem(QGraphicsRectItem):
    WIDTH = 190.0
    HEIGHT = 48.0

    def __init__(
        self,
        *,
        canvas: "VaultTreeCanvas",
        path: str,
        label: str,
        protected: bool,
        staged: bool,
        movable: bool,
    ) -> None:
        super().__init__(0.0, 0.0, self.WIDTH, self.HEIGHT)
        self.canvas = canvas
        self.path = path
        self.protected = protected
        self.movable = movable
        self._press_scene_pos: Optional[QPointF] = None
        self._home_pos = QPointF()
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsFocusable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, movable)
        self.setAcceptHoverEvents(True)
        self.setCursor(
            Qt.CursorShape.OpenHandCursor
            if movable
            else (Qt.CursorShape.ForbiddenCursor if protected else Qt.CursorShape.ArrowCursor)
        )

        palette = canvas.palette()
        background = QColor(palette.base().color())
        foreground = QColor(palette.text().color())
        border = QColor(palette.mid().color())
        if staged:
            background.setAlpha(105)
            foreground.setAlpha(145)
            pen = QPen(border, 1.5, Qt.PenStyle.DashLine)
        else:
            pen = QPen(border, 1.5)
        if protected:
            pen = QPen(QColor("#a46a21"), 1.8)
        self.setBrush(QBrush(background))
        self.setPen(pen)

        display = f"🔒 {label}" if protected else label
        self.text_item = QGraphicsSimpleTextItem(display, self)
        self.text_item.setBrush(QBrush(foreground))
        self.text_item.setPos(10, 7)
        path_text = QGraphicsSimpleTextItem(path, self)
        path_color = QColor(foreground)
        path_color.setAlpha(165)
        path_text.setBrush(QBrush(path_color))
        font = path_text.font()
        font.setPointSize(max(7, font.pointSize() - 2))
        path_text.setFont(font)
        path_text.setPos(10, 27)

    def remember_home(self) -> None:
        self._home_pos = QPointF(self.pos())

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        self._press_scene_pos = QPointF(event.scenePos())
        self.canvas._select_path(self.path)
        if self.movable:
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        super().mouseMoveEvent(event)
        if self.movable:
            self.canvas._preview_drop(self, event.scenePos())

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        moved = bool(
            self._press_scene_pos
            and (event.scenePos() - self._press_scene_pos).manhattanLength() >= 8
        )
        if moved and self.movable:
            self.canvas._complete_drop(self, event.scenePos())
        self.setPos(self._home_pos)
        self.setCursor(
            Qt.CursorShape.OpenHandCursor
            if self.movable
            else (Qt.CursorShape.ForbiddenCursor if self.protected else Qt.CursorShape.ArrowCursor)
        )
        self._press_scene_pos = None
        super().mouseReleaseEvent(event)


class VaultTreeCanvas(QGraphicsView):
    """Deterministic map-style view of a scoped physical vault tree."""

    nodeSelected = Signal(str)
    moveRequested = Signal(str, str, str)  # source, target, child|before|after
    statusChanged = Signal(str)

    MAX_RENDERED_NODES = 700
    MAX_INITIAL_DEPTH = 8
    X_GAP = 245.0
    Y_GAP = 72.0

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setBackgroundBrush(self.palette().window())
        self._payload: list[dict[str, Any]] = []
        self._scope_root = "/"
        self._plan: list[dict[str, Any]] = []
        self._nodes: dict[str, _TreeNodeItem] = {}
        self._selected_path: Optional[str] = None
        self._drop_target: Optional[_TreeNodeItem] = None
        self._read_only = False

    @property
    def scope_root(self) -> str:
        return self._scope_root

    def set_model(
        self,
        payload: list[dict[str, Any]],
        scope_root: str,
        plan: list[dict],
        *,
        read_only: bool = False,
    ) -> None:
        self._payload = list(payload or [])
        self._scope_root = (scope_root or "/").rstrip("/") or "/"
        self._plan = list(plan or [])
        self._read_only = bool(read_only)
        self.rebuild()

    def fit_tree(self) -> None:
        bounds = self.scene().itemsBoundingRect()
        if bounds.isValid() and not bounds.isEmpty():
            self.fitInView(bounds.adjusted(-30, -30, 30, 30), Qt.AspectRatioMode.KeepAspectRatio)

    def wheelEvent(self, event) -> None:  # type: ignore[override]
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            factor = math.pow(1.18, event.angleDelta().y() / 120.0)
            self.scale(factor, factor)
            event.accept()
            return
        super().wheelEvent(event)

    def _find_payload_node(self, path: str) -> Optional[dict[str, Any]]:
        stack = list(reversed(self._payload))
        while stack:
            node = stack.pop()
            node_path = str(node.get("path") or "")
            if node_path == path:
                return node
            stack.extend(reversed(list(node.get("children") or [])))
        return None

    @staticmethod
    def _node_label(node: dict[str, Any]) -> str:
        path = str(node.get("path") or "/")
        return str(node.get("name") or Path(path.rstrip("/")).name or "Vault root")

    def rebuild(self) -> None:
        previous_selection = self._selected_path
        scene = self.scene()
        scene.clear()
        self._nodes.clear()
        self._drop_target = None
        scope_node = self._find_payload_node(self._scope_root)
        if scope_node is None and self._scope_root == "/":
            if len(self._payload) == 1 and str(self._payload[0].get("path") or "") == "/":
                scope_node = self._payload[0]
            else:
                scope_node = {"name": "Vault root", "path": "/", "children": self._payload}
        if scope_node is None:
            message = scene.addSimpleText(f"Scope is no longer available: {self._scope_root}")
            message.setBrush(self.palette().text())
            return

        staged_sources = {
            str(op.get("source_path") or "")
            for op in self._plan
            if op.get("operation_type", "move") == "move"
        }
        positions: dict[str, QPointF] = {}
        visible_nodes: list[tuple[dict[str, Any], int]] = []
        stack: list[tuple[dict[str, Any], int]] = [(scope_node, 0)]
        while stack and len(visible_nodes) < self.MAX_RENDERED_NODES:
            node, depth = stack.pop()
            visible_nodes.append((node, depth))
            if depth >= self.MAX_INITIAL_DEPTH:
                continue
            children = list(node.get("children") or [])
            stack.extend((child, depth + 1) for child in reversed(children))

        for row, (node, depth) in enumerate(visible_nodes):
            path = str(node.get("path") or "/")
            positions[path] = QPointF(depth * self.X_GAP, row * self.Y_GAP)

        accent = QColor(self.palette().highlight().color())
        for node, _depth in visible_nodes:
            parent_path = str(node.get("path") or "/")
            parent_pos = positions[parent_path]
            for child in list(node.get("children") or []):
                child_path = str(child.get("path") or "")
                child_pos = positions.get(child_path)
                if child_pos is None:
                    continue
                path_item = QGraphicsPathItem()
                curve = QPainterPath(parent_pos + QPointF(_TreeNodeItem.WIDTH, _TreeNodeItem.HEIGHT / 2))
                midpoint = (parent_pos.x() + _TreeNodeItem.WIDTH + child_pos.x()) / 2
                curve.cubicTo(
                    midpoint,
                    parent_pos.y() + _TreeNodeItem.HEIGHT / 2,
                    midpoint,
                    child_pos.y() + _TreeNodeItem.HEIGHT / 2,
                    child_pos.x(),
                    child_pos.y() + _TreeNodeItem.HEIGHT / 2,
                )
                path_item.setPath(curve)
                path_item.setPen(QPen(self.palette().mid().color(), 1.4))
                path_item.setZValue(-10)
                scene.addItem(path_item)

        for node, _depth in visible_nodes:
            path = str(node.get("path") or "/")
            protected = is_protected_tree_path(path)
            item = _TreeNodeItem(
                canvas=self,
                path=path,
                label=self._node_label(node),
                protected=protected,
                staged=path in staged_sources,
                movable=not protected and not self._read_only,
            )
            item.setPos(positions[path])
            item.remember_home()
            scene.addItem(item)
            self._nodes[path] = item

        ghost_offsets: dict[str, int] = {}
        for op in self._plan:
            if op.get("operation_type", "move") != "move":
                continue
            source = str(op.get("source_path") or "")
            destination = str(op.get("destination_parent") or "/")
            name = str(op.get("new_name") or Path(source).name)
            source_item = self._nodes.get(source)
            target_item = self._nodes.get(destination)
            if target_item is not None:
                offset = ghost_offsets.get(destination, 0)
                ghost_offsets[destination] = offset + 1
                ghost = QGraphicsRectItem(0, 0, 178, 38)
                ghost.setBrush(QBrush(QColor(accent.red(), accent.green(), accent.blue(), 36)))
                ghost.setPen(QPen(accent, 1.5, Qt.PenStyle.DashLine))
                ghost.setPos(
                    target_item.pos().x() + self.X_GAP,
                    target_item.pos().y() + offset * 44,
                )
                text = QGraphicsSimpleTextItem(f"{name}  (staged)", ghost)
                text.setBrush(QBrush(accent))
                text.setPos(8, 9)
                ghost.setZValue(5)
                scene.addItem(ghost)
            elif source_item is not None:
                portal = QGraphicsSimpleTextItem(f"↗ moves outside scope → {destination}")
                portal.setBrush(QBrush(QColor("#b97819")))
                portal.setPos(
                    source_item.pos().x() + self.X_GAP,
                    source_item.pos().y() + 12,
                )
                scene.addItem(portal)

        if len(visible_nodes) >= self.MAX_RENDERED_NODES:
            warning = scene.addSimpleText(
                f"Showing the first {self.MAX_RENDERED_NODES} nodes. Choose a narrower scope to continue."
            )
            warning.setBrush(QBrush(QColor("#b97819")))
            warning.setPos(0, len(visible_nodes) * self.Y_GAP + 10)

        scene.setSceneRect(scene.itemsBoundingRect().adjusted(-40, -40, 80, 80))
        if previous_selection in self._nodes:
            self._select_path(previous_selection)

    def _select_path(self, path: str) -> None:
        self._selected_path = path
        for node_path, item in self._nodes.items():
            item.setSelected(node_path == path)
        self.nodeSelected.emit(path)

    def _node_at(self, scene_pos: QPointF, *, exclude: Optional[_TreeNodeItem] = None) -> Optional[_TreeNodeItem]:
        for item in self.scene().items(scene_pos):
            candidate = item
            while candidate is not None and not isinstance(candidate, _TreeNodeItem):
                candidate = candidate.parentItem()
            if isinstance(candidate, _TreeNodeItem) and candidate is not exclude:
                return candidate
        return None

    def _clear_drop_target(self) -> None:
        if self._drop_target is not None:
            self._drop_target.setPen(
                QPen(
                    QColor("#a46a21") if self._drop_target.protected else self.palette().mid().color(),
                    1.8 if self._drop_target.protected else 1.5,
                )
            )
        self._drop_target = None

    def _preview_drop(self, source: _TreeNodeItem, scene_pos: QPointF) -> None:
        target = self._node_at(scene_pos, exclude=source)
        if target is self._drop_target:
            return
        self._clear_drop_target()
        self._drop_target = target
        if target is None:
            self.statusChanged.emit("Drop onto a page, or use Move to… for another scope.")
            return
        invalid = target.path == source.path or target.path.startswith(source.path.rstrip("/") + "/")
        target.setPen(QPen(QColor("#b3261e") if invalid else self.palette().highlight().color(), 3.0))
        self.statusChanged.emit(
            "Cannot move a page into its own subtree."
            if invalid
            else f"Move {source.path} relative to {target.path}"
        )

    def _complete_drop(self, source: _TreeNodeItem, scene_pos: QPointF) -> None:
        target = self._node_at(scene_pos, exclude=source)
        self._clear_drop_target()
        if target is None:
            self.statusChanged.emit("Move canceled. Use Move to… to choose another destination.")
            return
        if target.path == source.path or target.path.startswith(source.path.rstrip("/") + "/"):
            self.statusChanged.emit("Cannot move a page into itself or its own subtree.")
            return
        local_y = scene_pos.y() - target.sceneBoundingRect().top()
        if local_y < _TreeNodeItem.HEIGHT * 0.25:
            placement = "before"
        elif local_y > _TreeNodeItem.HEIGHT * 0.75:
            placement = "after"
        else:
            placement = "child"
        self.moveRequested.emit(source.path, target.path, placement)
