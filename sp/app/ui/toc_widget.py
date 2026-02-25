from __future__ import annotations

from typing import Iterable

from PySide6.QtCore import QPoint, Qt, Signal, QPropertyAnimation
from PySide6.QtGui import QAction, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QMenu,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from sp.app import config
from .heading_utils import heading_slug
from .theme import theme_value


class TableOfContentsWidget(QFrame):
    """Floating outline widget that lists headings inside the editor."""

    headingActivated = Signal(int)  # Absolute cursor position for the heading
    collapsedChanged = Signal(bool)
    linkCopied = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("tocWidget")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._apply_theme_styles()
        self._collapsed = False
        self._expanded_width = 220
        self._base_path = ""
        self._headings = []
        self._idle_opacity = 0.25  # Mostly transparent when not hovered
        self._hover_opacity = 0.85  # More visible on hover
        self._build_ui()
        from PySide6.QtWidgets import QGraphicsOpacityEffect
        self._opacity_effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._opacity_effect)
        self._opacity_effect.setOpacity(self._idle_opacity)
        self._opacity_anim = QPropertyAnimation(self._opacity_effect, b"opacity", self)
        self._opacity_anim.setDuration(140)

    def _apply_theme_styles(self) -> None:
        app = QApplication.instance()
        try:
            base_lightness = app.palette().color(QPalette.ColorRole.Base).lightness() if app else 0
        except Exception:
            base_lightness = 0
        is_light_palette = base_lightness >= 128
        pref = (config.load_theme_preference() or "").strip().lower()
        using_default_dark_theme = pref in {"", "default", "dark-theme", "dark-theme.json", "theme-config", "theme-config.json"}
        bg_default = "#f3f4f6" if is_light_palette else "#2d2d2d"
        border_default = "#d1d5db" if is_light_palette else "#aaa"
        text_default = "#111827" if is_light_palette else "#f5f5f5"
        hover_default = "rgba(37, 99, 235, 0.12)" if is_light_palette else "rgba(108, 180, 255, 0.18)"
        selected_default = hover_default
        if is_light_palette and using_default_dark_theme:
            bg = bg_default
            border = border_default
            text = text_default
            hover_bg = hover_default
            selected_bg = selected_default
        else:
            bg = theme_value("main_window.toc_widget.bg", bg_default)
            border = theme_value("main_window.toc_widget.border", border_default)
            text = theme_value("main_window.toc_widget.text", text_default)
            hover_bg = theme_value("main_window.toc_widget.hover_bg", hover_default)
            selected_bg = theme_value("main_window.toc_widget.selected_bg", selected_default)
        self.setStyleSheet(
            f"""
            QFrame#tocWidget {{
                background-color: {bg};
                border: 1px solid {border};
                border-radius: 6px;
            }}
            QTreeWidget {{
                background: transparent;
                color: {text};
            }}
            QTreeWidget#tocTree::item {{
                padding: 2px 6px;
                text-align: right;
                border: none;
            }}
            QTreeWidget#tocTree::item:hover {{
                background: {hover_bg};
                border-radius: 4px;
            }}
            QTreeWidget#tocTree::item:selected {{
                background: {selected_bg};
                border: none;
                outline: none;
            }}
            QTreeWidget#tocTree::branch:selected,
            QTreeWidget#tocTree::branch:selected:has-children {{
                background: transparent;
            }}
            """
        )

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        # Hide the header toggle to keep the widget unobtrusive
        self.toggle_button = None

        self.tree = QTreeWidget()
        self.tree.setObjectName("tocTree")
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(16)
        self.tree.setUniformRowHeights(True)
        self.tree.setExpandsOnDoubleClick(False)
        self.tree.setMouseTracking(True)
        self.tree.itemActivated.connect(self._on_item_activated)
        self.tree.itemClicked.connect(self._on_item_activated)
        self.tree.setVerticalScrollMode(QTreeWidget.ScrollMode.ScrollPerPixel)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_context_menu)
        layout.addWidget(self.tree, 1)

        # Remove fixed width to allow auto-sizing
        self.setMinimumWidth(120)  # Set a reasonable minimum width

    # --- Public API -----------------------------------------------------
    def set_headings(self, headings: Iterable[dict]) -> None:
        """Populate outline tree with heading entries."""
        self._headings = list(headings or [])
        self.tree.clear()
        root = self.tree.invisibleRootItem()
        stack: list[tuple[int, QTreeWidgetItem]] = []
        for entry in self._headings:
            if entry.get("type") == "hr":
                continue
            level = int(entry.get("level", 1))
            level = max(1, min(5, level))
            text = entry.get("title") or "(untitled heading)"
            item = QTreeWidgetItem([text])
            data = {
                "position": entry.get("position", 0),
                "line": entry.get("line", 1),
                "title": text,
                "level": level,
                "anchor": heading_slug(text),
            }
            item.setData(0, Qt.ItemDataRole.UserRole, data)
            while stack and stack[-1][0] >= level:
                stack.pop()
            parent = stack[-1][1] if stack else root
            parent.addChild(item)
            stack.append((level, item))
        self.tree.expandAll()
        self._update_placeholder()
        self._update_geometry()
        self.adjustSize()  # Ensure widget resizes to fit content
        # Apply idle opacity on population to keep it translucent when not hovered
        try:
            self._opacity_effect.setOpacity(self._idle_opacity)
        except Exception:
            pass

    def set_base_path(self, colon_path: str) -> None:
        """Set the base colon path for copy-link actions."""
        self._base_path = colon_path or ""

    def set_collapsed(self, collapsed: bool) -> None:
        # Collapse toggle removed; always stay expanded but keep API compatibility.
        if self._collapsed != collapsed:
            self._collapsed = False
            self.tree.setVisible(True)
            # self.setFixedWidth(self._expanded_width)  # No longer force fixed width
            self.collapsedChanged.emit(False)

    def collapsed(self) -> bool:
        return self._collapsed

    # --- Internal helpers -----------------------------------------------
    def _handle_toggle_clicked(self, checked: bool) -> None:
        # Checked means expanded
        self.set_collapsed(not checked)

    def _on_item_activated(self, item: QTreeWidgetItem) -> None:
        if not item:
            return
        if item.flags() == Qt.ItemFlag.NoItemFlags:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return
        position = int(data.get("position", 0))
        self.headingActivated.emit(position)

    def _show_context_menu(self, pos: QPoint) -> None:
        item = self.tree.itemAt(pos)
        if not item:
            return
        if item.flags() == Qt.ItemFlag.NoItemFlags:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return
        menu = QMenu(self)
        copy_action = QAction("Copy Link Location", self)
        copy_action.triggered.connect(lambda: self._copy_link(data))
        menu.addAction(copy_action)
        global_pos = self.tree.viewport().mapToGlobal(pos)
        menu.exec(global_pos)

    def _copy_link(self, data: dict) -> None:
        anchor = data.get("anchor") or ""
        if not anchor:
            return
        base = self._base_path or ""
        link = f"{base}#{anchor}" if base else f"#{anchor}"
        QApplication.clipboard().setText(link)
        self.linkCopied.emit(link)

    def _update_placeholder(self) -> None:
        if self._headings:
            return
        item = QTreeWidgetItem(["(No headings)"])
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        self.tree.addTopLevelItem(item)

    def _update_geometry(self) -> None:
        self.adjustSize()
        self.updateGeometry()
        self.raise_()

    # --- Hover opacity --------------------------------------------------
    def enterEvent(self, event):  # type: ignore[override]
        self._fade_to(self._hover_opacity)
        super().enterEvent(event)

    def leaveEvent(self, event):  # type: ignore[override]
        self._fade_to(self._idle_opacity)
        super().leaveEvent(event)

    def wheelEvent(self, event):  # type: ignore[override]
        """Scroll TOC without affecting the editor."""
        self._fade_to(self._hover_opacity)
        event.accept()
        self.tree.wheelEvent(event)

    def _fade_to(self, target: float) -> None:
        try:
            self._opacity_anim.stop()
            self._opacity_anim.setStartValue(self._opacity_effect.opacity())
            self._opacity_anim.setEndValue(target)
            self._opacity_anim.start()
        except Exception:
            self._opacity_effect.setOpacity(target)
