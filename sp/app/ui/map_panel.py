from __future__ import annotations

from dataclasses import dataclass, field
import html
import math
import re
import textwrap
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QBuffer, QEasingCurve, QEvent, QIODevice, QPoint, QPointF, QPropertyAnimation, QSize, Qt, Signal, QMimeData, QTimer
from PySide6.QtGui import QAction, QColor, QCursor, QFontMetrics, QGuiApplication, QIcon, QImage, QKeyEvent, QKeySequence, QMouseEvent, QNativeGestureEvent, QPainter, QPalette, QPixmap, QShortcut
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from sp.app import config
from sp.logging_flags import log_enabled
from .markdown_editor import HEADING_MARK_PATTERN, HEADING_MAX_LEVEL, MarkdownEditor, heading_level_from_char
from .screen_positioning import popup_available_geometry, clamp_popup_top_left
from .theme import apply_menu_theme, theme_color, theme_value


class ZoomablePreviewLabel(QLabel):
    """Preview label with wheel or gesture zoom and drag-to-pan."""

    zoomRequested = Signal(int, object)

    def __init__(self):
        super().__init__()
        self.pan_start_pos = None
        self.is_panning = False
        self.grabGesture(Qt.PinchGesture)

    def wheelEvent(self, event) -> None:  # type: ignore[override]
        # Shift + mouse wheel for horizontal scrolling
        if event.modifiers() & Qt.ShiftModifier:
            delta = event.angleDelta().y()
            if delta:
                parent = self.parent()
                while parent:
                    if isinstance(parent, QScrollArea):
                        parent.horizontalScrollBar().setValue(
                            parent.horizontalScrollBar().value() - (int(delta / 120) * 40)
                        )
                        event.accept()
                        return
                    parent = parent.parent()
        # Mouse wheel controls zoom (Lucidchart style)
        delta = event.angleDelta().y()
        if delta:
            self.zoomRequested.emit(1 if delta > 0 else -1, QPointF(event.position()))
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
        # Right-click drag for panning (Lucidchart style)
        if event.button() == Qt.RightButton and pixmap:
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
        # Right-click drag for panning (Lucidchart style)
        if event.button() == Qt.RightButton and self.is_panning:
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
    heading_text: str = ""
    line_number: int = 0
    section_end_line: int = 0
    content_end_line: int = 0
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


@dataclass
class _DetachedSession:
    root: _MindNode
    base_text: str
    selected_node_ids: set[str]
    anchor_node_id: Optional[str]
    focus_node_id: Optional[str]


class _MapContentTooltip(QFrame):
    pinRequested = Signal()
    dismissed = Signal()
    closeRequested = Signal()
    hoverChanged = Signal(bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint)
        self.setObjectName("mapContentTooltip")
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.NoFocus)
        self._is_pinned = False
        self._editor = MarkdownEditor(self)
        self._editor.set_context(None, None)
        self._editor.set_read_only_mode(True)
        self._editor.setFocusPolicy(Qt.NoFocus)
        self._editor.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._editor.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._editor.installEventFilter(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.addStretch(1)
        self._close_btn = QToolButton(self)
        self._close_btn.setText("Close")
        self._close_btn.hide()
        self._close_btn.clicked.connect(self._request_close)
        header_row.addWidget(self._close_btn)
        layout.addLayout(header_row)
        layout.addWidget(self._editor)
        self.closeRequested.connect(self._handle_close_requested)
        self.resize(520, 320)
        self.setStyleSheet(
            "#mapContentTooltip {"
            "border: 2px solid palette(mid);"
            "border-radius: 6px;"
            "}"
            "#mapContentTooltip QTextEdit {"
            "border: none;"
            "}"
        )

    def set_pinned(self, pinned: bool) -> None:
        if pinned == self._is_pinned:
            if log_enabled("ui_state"):
                print(f"[MAP_TOOLTIP] set_pinned noop pinned={pinned} visible={self.isVisible()}")
            return
        if log_enabled("ui_state"):
            print(f"[MAP_TOOLTIP] set_pinned pinned={pinned} visible_before={self.isVisible()}")
        pos = self.pos()
        self.hide()
        self.setWindowFlag(Qt.Tool, True)
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, not pinned)
        self.setFocusPolicy(Qt.StrongFocus if pinned else Qt.NoFocus)
        self._editor.setFocusPolicy(Qt.StrongFocus if pinned else Qt.NoFocus)
        self._close_btn.setVisible(pinned)
        self.move(pos)
        self._is_pinned = pinned

    def set_font_zoom(self, zoom_factor: float) -> None:
        """Apply zoom factor to the editor font size."""
        base_font = QApplication.font()
        zoomed_size = max(8, int(base_font.pointSize() * zoom_factor))
        font = self._editor.font()
        font.setPointSize(zoomed_size)
        self._editor.setFont(font)

    def focus_reader(self) -> None:
        self.setFocus(Qt.OtherFocusReason)
        self._editor.setFocus(Qt.OtherFocusReason)

    def _dismiss_and_forward_key(self, event) -> bool:
        self.hide()
        self.dismissed.emit()
        parent = self.parentWidget()
        if parent is not None:
            handled = False
            handler = getattr(parent, "_handle_navigation_key", None)
            if callable(handler):
                handled = bool(handler(event.key(), event.modifiers()))
            if not handled:
                forwarded = QKeyEvent(
                    event.type(),
                    event.key(),
                    event.modifiers(),
                    event.text(),
                    event.isAutoRepeat(),
                    event.count(),
                )
                parent.keyPressEvent(forwarded)
        event.accept()
        return True

    def _dismiss_key(self, event) -> bool:
        self.hide()
        self.dismissed.emit()
        event.accept()
        return True

    def _request_close(self) -> None:
        self.closeRequested.emit()

    def _handle_close_requested(self) -> None:
        self.hide()
        self.dismissed.emit()

    def page_forward(self) -> bool:
        scrollbar = self._editor.verticalScrollBar()
        if scrollbar is None:
            return False
        old_value = scrollbar.value()
        step = max(1, scrollbar.pageStep())
        scrollbar.setValue(min(scrollbar.maximum(), old_value + step))
        return scrollbar.value() != old_value

    def page_backward(self) -> bool:
        scrollbar = self._editor.verticalScrollBar()
        if scrollbar is None:
            return False
        old_value = scrollbar.value()
        step = max(1, scrollbar.pageStep())
        scrollbar.setValue(max(scrollbar.minimum(), old_value - step))
        return scrollbar.value() != old_value

    def line_forward(self) -> bool:
        scrollbar = self._editor.verticalScrollBar()
        if scrollbar is None:
            return False
        old_value = scrollbar.value()
        step = max(1, scrollbar.singleStep())
        scrollbar.setValue(min(scrollbar.maximum(), old_value + step))
        return scrollbar.value() != old_value

    def line_backward(self) -> bool:
        scrollbar = self._editor.verticalScrollBar()
        if scrollbar is None:
            return False
        old_value = scrollbar.value()
        step = max(1, scrollbar.singleStep())
        scrollbar.setValue(max(scrollbar.minimum(), old_value - step))
        return scrollbar.value() != old_value

    def show_markdown(self, markdown_text: str, page_path: Optional[str], cursor_pos: QPoint) -> None:
        if log_enabled("ui_state"):
            print(
                f"[MAP_TOOLTIP] show_markdown len={len(markdown_text or '')} page={page_path!r} "
                f"cursor=({cursor_pos.x()},{cursor_pos.y()}) pinned={self._is_pinned}"
            )
        self._editor.set_context(None, page_path)
        self._editor.set_markdown(markdown_text)
        try:
            self._editor.document().setModified(False)
        except Exception:
            pass
        self.move(cursor_pos)  # temporary placement so sizeHint() is valid
        self.show()
        self.raise_()
        # Smart placement: keep the popup fully within the screen.
        tip_w = self.width()
        tip_h = self.height()
        gap = 16
        avail = popup_available_geometry(anchor=cursor_pos, parent=self)
        # Prefer right of cursor, flip left if it would be clipped.
        if cursor_pos.x() + gap + tip_w <= avail.right():
            x = cursor_pos.x() + gap
        else:
            x = cursor_pos.x() - gap - tip_w
        # Prefer below cursor, flip above if it would be clipped.
        if cursor_pos.y() + gap + tip_h <= avail.bottom():
            y = cursor_pos.y() + gap
        else:
            y = cursor_pos.y() - gap - tip_h
        # Final clamp so we never go off-screen on any edge.
        self.move(clamp_popup_top_left(QPoint(x, y), QSize(tip_w, tip_h), avail))

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.LeftButton:
            self.pinRequested.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def enterEvent(self, event) -> None:  # type: ignore[override]
        self.hoverChanged.emit(True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # type: ignore[override]
        self.hoverChanged.emit(False)
        super().leaveEvent(event)

    def eventFilter(self, obj, event) -> bool:  # type: ignore[override]
        if obj is self._editor and event.type() == QEvent.KeyPress:
            try:
                if event.key() == Qt.Key_Escape and event.modifiers() == Qt.NoModifier:
                    self.hide()
                    self.dismissed.emit()
                    event.accept()
                    return True
                if event.key() in (Qt.Key_Left, Qt.Key_Right) and event.modifiers() == Qt.NoModifier:
                    return self._dismiss_key(event)
                if (
                    config.load_vi_mode_enabled()
                    and event.key() in (Qt.Key_H, Qt.Key_L)
                    and event.modifiers() == Qt.NoModifier
                ):
                    return self._dismiss_key(event)
                if event.key() in (Qt.Key_Up, Qt.Key_Down) and event.modifiers() == Qt.NoModifier:
                    if event.key() == Qt.Key_Down:
                        self.line_forward()
                    else:
                        self.line_backward()
                    event.accept()
                    return True
                if (
                    config.load_vi_mode_enabled()
                    and event.key() in (Qt.Key_J, Qt.Key_K)
                    and event.modifiers() == Qt.NoModifier
                ):
                    if event.key() == Qt.Key_J:
                        self.line_forward()
                    else:
                        self.line_backward()
                    event.accept()
                    return True
                if (
                    config.load_vi_mode_enabled()
                    and event.key() in (Qt.Key_J, Qt.Key_K)
                    and event.modifiers() == (Qt.ControlModifier | Qt.ShiftModifier)
                ):
                    if event.key() == Qt.Key_J:
                        self.page_forward()
                    else:
                        self.page_backward()
                    event.accept()
                    return True
                if event.key() == Qt.Key_Space and event.modifiers() == Qt.ControlModifier:
                    self.page_forward()
                    event.accept()
                    return True
            except Exception:
                pass
        if obj is self._editor and event.type() == QEvent.ContextMenu:
            event.accept()
            return True
        return super().eventFilter(obj, event)

    def contextMenuEvent(self, event) -> None:  # type: ignore[override]
        event.accept()

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if event.key() == Qt.Key_Escape and event.modifiers() == Qt.NoModifier:
            self.hide()
            self.dismissed.emit()
            event.accept()
            return
        if event.key() in (Qt.Key_Left, Qt.Key_Right) and event.modifiers() == Qt.NoModifier:
            if self._dismiss_key(event):
                return
        if (
            config.load_vi_mode_enabled()
            and event.key() in (Qt.Key_H, Qt.Key_L)
            and event.modifiers() == Qt.NoModifier
        ):
            if self._dismiss_key(event):
                return
        if event.key() in (Qt.Key_Up, Qt.Key_Down) and event.modifiers() == Qt.NoModifier:
            if event.key() == Qt.Key_Down:
                self.line_forward()
            else:
                self.line_backward()
            event.accept()
            return
        if (
            config.load_vi_mode_enabled()
            and event.key() in (Qt.Key_J, Qt.Key_K)
            and event.modifiers() == Qt.NoModifier
        ):
            if event.key() == Qt.Key_J:
                self.line_forward()
            else:
                self.line_backward()
            event.accept()
            return
        if (
            config.load_vi_mode_enabled()
            and event.key() in (Qt.Key_J, Qt.Key_K)
            and event.modifiers() == (Qt.ControlModifier | Qt.ShiftModifier)
        ):
            if event.key() == Qt.Key_J:
                self.page_forward()
            else:
                self.page_backward()
            event.accept()
            return
        if event.key() == Qt.Key_Space and event.modifiers() == Qt.ControlModifier:
            self.page_forward()
            event.accept()
            return
        super().keyPressEvent(event)


class _InlineNodeRenameEdit(QLineEdit):
    acceptRequested = Signal()
    cancelRequested = Signal()

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and event.modifiers() == Qt.NoModifier:
            self.acceptRequested.emit()
            event.accept()
            return
        if event.key() == Qt.Key_Escape and event.modifiers() == Qt.NoModifier:
            self.cancelRequested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class MapPanel(QWidget):
    """Native SVG-based mind map panel for markdown headings only."""

    headingActivated = Signal(str, int)
    headingCreateRequested = Signal(str, int, int, str)
    headingRenameRequested = Signal(str, int, int, str)
    headingReorderRequested = Signal(str, str, str, int)
    statusMessageRequested = Signal(str, int)
    focusSyncRequested = Signal()

    _HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
    _HR_RE = re.compile(r"^\s{0,3}([-*_])(?:\s*\1){2,}\s*$")

    _BOX_HPAD = 14
    _BOX_VPAD = 8
    _LINE_HEIGHT = 20
    _MAX_TEXT_WIDTH = 28
    _H_GAP = 72
    _V_GAP = 20
    _MARGIN = 40
    _PREVIEW_PAD_X = 520
    _PREVIEW_PAD_Y = 220
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
        self._selected_node_ids: set[str] = set()
        self._selection_anchor_node_id: Optional[str] = None
        self._pending_selected_line: Optional[int] = None
        self._last_activation_source: Optional[str] = None
        self._filter_node_id: Optional[str] = None
        self._root_is_page_h1: bool = False
        self._draft_heading: Optional[_DraftHeading] = None
        self._draft_runtime_node: Optional[_MindNode] = None
        self._inline_rename_node_id: Optional[str] = None
        self._detached_session: Optional[_DetachedSession] = None
        self._drag_press_node_id: Optional[str] = None
        self._drag_start_pos: Optional[QPointF] = None
        self._drag_active: bool = False
        self._drop_target_node_id: Optional[str] = None
        self._drop_target_valid: bool = False
        self._content_preview_enabled: bool = False
        self._hovered_node_id: Optional[str] = None
        self._hover_global_pos: Optional[QPoint] = None
        self._tooltip_pinned: bool = False
        self._tooltip_hovered: bool = False
        self._selected_note_popup_active: bool = False
        self._note_font_size_offset: int = 0
        self._theme_colors = self._map_theme_colors()
        self._scroll_animations: list[QPropertyAnimation] = []

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

        self.filter_btn = QToolButton()
        self.filter_btn.setAutoRaise(True)
        self.filter_btn.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.filter_btn.setIconSize(QSize(18, 18))
        self.filter_btn.clicked.connect(self.toggle_selected_filter)
        toolbar.addWidget(self.filter_btn)

        self.center_btn = QToolButton()
        self.center_btn.setAutoRaise(True)
        self.center_btn.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.center_btn.setIconSize(QSize(18, 18))
        self.center_btn.setToolTip("Center selected node (Alt+C)")
        self.center_btn.clicked.connect(self.center_selected_node)
        toolbar.addWidget(self.center_btn)

        self.left_indent_btn = QToolButton()
        self.left_indent_btn.setAutoRaise(True)
        self.left_indent_btn.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.left_indent_btn.setIconSize(QSize(18, 18))
        self.left_indent_btn.setToolTip("Move selected node to left side (Alt+S)")
        self.left_indent_btn.clicked.connect(self.left_align_selected_node)
        toolbar.addWidget(self.left_indent_btn)

        toolbar.addStretch(1)

        self.detached_modal = QFrame()
        self.detached_modal.setObjectName("mapDetachedModal")
        detached_layout = QHBoxLayout(self.detached_modal)
        detached_layout.setContentsMargins(8, 4, 8, 4)
        detached_layout.setSpacing(6)
        self.detached_modal_label = QLabel("Structural edits pending: Enter to accept, Esc to cancel")
        detached_layout.addWidget(self.detached_modal_label)

        self.accept_btn = QToolButton()
        self.accept_btn.setAutoRaise(True)
        self.accept_btn.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.accept_btn.setIconSize(QSize(18, 18))
        self.accept_btn.setToolTip("Accept structural edits")
        self.accept_btn.clicked.connect(self.commit_detached_changes)
        detached_layout.addWidget(self.accept_btn)

        self.cancel_btn = QToolButton()
        self.cancel_btn.setAutoRaise(True)
        self.cancel_btn.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.cancel_btn.setIconSize(QSize(18, 18))
        self.cancel_btn.setToolTip("Cancel structural edits")
        self.cancel_btn.clicked.connect(self.cancel_detached_changes)
        detached_layout.addWidget(self.cancel_btn)
        toolbar.addWidget(self.detached_modal)

        self.note_toggle_btn = QToolButton()
        self.note_toggle_btn.setAutoRaise(True)
        self.note_toggle_btn.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.note_toggle_btn.setIconSize(QSize(18, 18))
        self.note_toggle_btn.setToolTip("Toggle hover content previews")
        self.note_toggle_btn.setCheckable(True)
        self.note_toggle_btn.toggled.connect(self._toggle_content_previews)
        toolbar.addWidget(self.note_toggle_btn)

        self.note_font_smaller_btn = QToolButton()
        self.note_font_smaller_btn.setAutoRaise(True)
        self.note_font_smaller_btn.setFixedSize(26, 26)
        self.note_font_smaller_btn.setText("a-")
        self.note_font_smaller_btn.setToolTip("Decrease note preview font size")
        self.note_font_smaller_btn.clicked.connect(self._decrease_note_font_size)
        toolbar.addWidget(self.note_font_smaller_btn)

        self.note_font_larger_btn = QToolButton()
        self.note_font_larger_btn.setAutoRaise(True)
        self.note_font_larger_btn.setFixedSize(26, 26)
        self.note_font_larger_btn.setText("a+")
        self.note_font_larger_btn.setToolTip("Increase note preview font size")
        self.note_font_larger_btn.clicked.connect(self._increase_note_font_size)
        toolbar.addWidget(self.note_font_larger_btn)

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
        self.preview_label.setMouseTracking(True)
        self.preview_label.setText("Open a page to view its map.")
        self.preview_label.zoomRequested.connect(self._adjust_zoom)
        self.preview_label.installEventFilter(self)
        self._tooltip_timer = QTimer(self)
        self._tooltip_timer.setSingleShot(True)
        self._tooltip_timer.setInterval(600)
        self._tooltip_timer.timeout.connect(self._show_hover_tooltip)
        self._tooltip_hide_timer = QTimer(self)
        self._tooltip_hide_timer.setSingleShot(True)
        self._tooltip_hide_timer.setInterval(250)
        self._tooltip_hide_timer.timeout.connect(self._hide_hover_tooltip_if_idle)
        self._content_tooltip = _MapContentTooltip()
        self._content_tooltip.pinRequested.connect(self._pin_hover_tooltip)
        self._content_tooltip.dismissed.connect(self._on_tooltip_dismissed)
        self._content_tooltip.hoverChanged.connect(self._on_tooltip_hover_changed)
        self._inline_rename_edit = _InlineNodeRenameEdit(self.preview_label)
        self._inline_rename_edit.hide()
        self._inline_rename_edit.acceptRequested.connect(self._commit_inline_rename)
        self._inline_rename_edit.cancelRequested.connect(self._cancel_inline_rename)
        self._filter_on_shortcut = QShortcut(QKeySequence("Alt+["), self)
        self._filter_on_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self._filter_on_shortcut.activated.connect(self.apply_selected_filter)
        self._filter_off_shortcut = QShortcut(QKeySequence("Alt+]"), self)
        self._filter_off_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self._filter_off_shortcut.activated.connect(self.clear_filter)

        root.addWidget(self._wrap_scroll_area(), 1)

        self._apply_palette_styles()
        self._update_level_controls()
        self._note_font_size_offset = config.load_map_note_font_size_offset()
        self._toggle_content_previews(config.load_map_note_panel_visible())

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
            "filter_active_stroke": "#dc2626",
            "filter_active_shadow": "rgba(220, 38, 38, 0.22)",
            "edge_pos": "#7dd3fc" if not is_light_palette else "#1f6feb",
            "edge_neg": "#fbbf24" if not is_light_palette else "#b26a00",
            "indicator_fill": text.name(),
            "indicator_bg": base.name(),
            "indicator_stroke": border.name(),
        }

    def _apply_palette_styles(self) -> None:
        self._theme_colors = self._map_theme_colors()
        colors = self._theme_colors
        mono = self._toolbar_icon_color()
        accept = QColor("#16a34a")
        cancel = QColor("#dc2626")
        self.expand_all_btn.setIcon(self._load_svg_icon("expand-all.svg", QSize(18, 18), tint=mono))
        self.collapse_all_btn.setIcon(self._load_svg_icon("collapse-all.svg", QSize(18, 18), tint=mono))
        self.copy_btn.setIcon(self._load_svg_icon("copy-image.svg", QSize(18, 18), tint=mono))
        self.fit_btn.setIcon(self._load_svg_icon("fit-image.svg", QSize(18, 18), tint=mono))
        self._update_filter_controls()
        self.center_btn.setIcon(self._load_svg_icon("center.svg", QSize(18, 18), tint=mono))
        self.left_indent_btn.setIcon(self._load_svg_icon("left-indent.svg", QSize(18, 18), tint=mono))
        self.accept_btn.setIcon(self._load_svg_icon("accept.svg", QSize(18, 18), tint=accept))
        self.cancel_btn.setIcon(self._load_svg_icon("cancel.svg", QSize(18, 18), tint=cancel))
        self.note_toggle_btn.setIcon(self._load_svg_icon("show-note.svg", QSize(18, 18), tint=mono))
        button_color = self._toolbar_icon_color().name()
        self.zoom_out_btn.setStyleSheet(f"color: {button_color};")
        self.zoom_in_btn.setStyleSheet(f"color: {button_color};")
        self.preview_label.setStyleSheet(f"background: {colors['canvas']};")
        self.scroll_area.setStyleSheet(f"QScrollArea, QScrollArea > QWidget > QWidget {{ background: {colors['canvas']}; border: none; }}")
        modal_border = colors["selected_stroke"]
        modal_fill = QColor(colors["selected_stroke"])
        modal_fill.setAlpha(28)
        self.detached_modal.setStyleSheet(
            "#mapDetachedModal {"
            f"border: 1px solid {modal_border};"
            "border-radius: 6px;"
            f"background: {modal_fill.name(QColor.HexArgb)};"
            "}"
        )
        self._update_detached_mode_controls()

    def _load_svg_icon(self, name: str, size: QSize, *, tint: Optional[QColor] = None) -> QIcon:
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
            if tint is not None:
                painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
                painter.fillRect(pixmap.rect(), tint)
            painter.end()
            return QIcon(pixmap)
        except Exception:
            return QIcon()

    def _toolbar_icon_color(self) -> QColor:
        palette = self._editor_theme_palette()
        return QColor(0, 0, 0) if palette.color(QPalette.Window).lightness() > 128 else QColor(255, 255, 255)

    def _filter_active(self) -> bool:
        return bool(self._filter_node_id)

    def _update_filter_controls(self) -> None:
        mono = self._toolbar_icon_color()
        active = self._filter_active()
        icon_name = "filter-off.svg" if active else "filter.svg"
        tooltip = "Clear subtree filter (Alt+])" if active else "Filter to selected subtree (Alt+[)"
        tint = QColor("#dc2626") if active else mono
        self.filter_btn.setIcon(self._load_svg_icon(icon_name, QSize(18, 18), tint=tint))
        self.filter_btn.setToolTip(tooltip)

    def _wrap_scroll_area(self) -> QScrollArea:
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(False)
        self.scroll_area.setAlignment(Qt.AlignCenter)
        self.scroll_area.setWidget(self.preview_label)
        return self.scroll_area

    def set_content(self, page_path: Optional[str], markdown_text: str) -> None:
        self._cancel_hover_tooltip()
        is_new_page = page_path != self._current_page_path
        incoming_text = markdown_text or ""
        if (
            not is_new_page
            and self._detached_session is not None
            and incoming_text != self._current_markdown
        ):
            self._detached_session = None
            self._clear_drag_state()
        self._current_page_path = page_path
        self._current_markdown = incoming_text
        if not page_path:
            self.clear_content()
            return
        if is_new_page:
            self._inline_rename_node_id = None
            self._inline_rename_edit.hide()
            self._detached_session = None
            self._collapsed_node_ids.clear()
            self._canvas_bounds = None
            self._scope_expansion_depths.clear()
            self._filter_node_id = None
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
        self._selected_node_ids.clear()
        self._selection_anchor_node_id = None
        self._pending_selected_line = None
        self._last_activation_source = None
        self._filter_node_id = None
        self._root_is_page_h1 = False
        self._draft_heading = None
        self._draft_runtime_node = None
        self._inline_rename_node_id = None
        self._inline_rename_edit.hide()
        self._detached_session = None
        self._clear_drag_state()
        self._cancel_hover_tooltip()
        self.preview_label.setPixmap(QPixmap())
        self.preview_label.setText("Open a page to view its map.")
        self._update_level_controls()
        self._update_detached_mode_controls()
        self._update_filter_controls()

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
        view_root = self._view_root()
        if not view_root:
            return None
        selected = self._selected_node()
        if selected and self._node_is_visible_in_view(selected.node_id):
            return selected
        return view_root

    def _view_root(self, root: Optional[_MindNode] = None) -> Optional[_MindNode]:
        base_root = root or self._latest_root
        if base_root is None:
            return None
        if not self._filter_node_id:
            return base_root
        filtered = next((node for node in self._collect_nodes(base_root) if node.node_id == self._filter_node_id), None)
        if filtered is None and root is None:
            self._filter_node_id = None
            self._update_filter_controls()
            return self._latest_root
        return filtered or base_root

    def _node_is_visible_in_view(self, node_id: Optional[str]) -> bool:
        if not node_id:
            return False
        view_root = self._view_root()
        if view_root is None:
            return False
        return any(node.node_id == node_id for node in self._collect_nodes(view_root))

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
        QTimer.singleShot(0, self._center_fitted_map)

    def _center_fitted_map(self) -> None:
        viewport = self.scroll_area.viewport().size()
        if viewport.width() <= 0 or viewport.height() <= 0:
            return
        hbar = self.scroll_area.horizontalScrollBar()
        vbar = self.scroll_area.verticalScrollBar()
        desired_x = max(hbar.minimum(), min(int(round((self.preview_label.width() - viewport.width()) / 2)), hbar.maximum()))
        desired_y = max(vbar.minimum(), min(int(round((self.preview_label.height() - viewport.height()) / 2)), vbar.maximum()))
        hbar.setValue(desired_x)
        vbar.setValue(desired_y)

    def _apply_initial_view(self) -> None:
        view_root = self._view_root()
        if view_root is None:
            return
        self._set_selected_node(view_root)
        self._zoom_factor = max(0.2, self._zoom_factor * 0.9)
        self._update_preview(fit=False)
        QTimer.singleShot(0, self.left_align_selected_node)

    def _reset_view_to_root(self) -> bool:
        view_root = self._view_root()
        if not view_root:
            return False
        self._set_selected_node(view_root)
        self.fit_map()
        self.left_align_selected_node()
        return True

    def _collapse_entire_map_to_root(self) -> bool:
        if not self._latest_root:
            return False
        root = self._latest_root
        self._filter_node_id = None
        self._scope_expansion_depths.clear()
        self._collapsed_node_ids = {root.node_id} if root.children else set()
        self._set_selected_node(root)
        self.refresh()
        self.center_selected_node()
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
        self._collapsed_node_ids = {self._latest_root.node_id} if self._latest_root.children else set()
        self._set_selected_node(self._latest_root)
        self.refresh()

    def copy_image(self) -> None:
        pixmap = self._cropped_preview_pixmap()
        if not pixmap:
            return
        clipboard = QApplication.clipboard()
        buffer = QBuffer()
        buffer.open(QIODevice.WriteOnly)
        pixmap.save(buffer, "PNG")
        png_bytes = bytes(buffer.data())
        mime = QMimeData()
        mime.setData("image/png", png_bytes)
        mime.setImageData(pixmap.toImage())
        clipboard.setMimeData(mime)

    def _cropped_preview_pixmap(self) -> Optional[QPixmap]:
        if not self._preview_pixmap:
            return None
        bounds: list[tuple[float, float, float, float]] = list(self._node_hitboxes.values())
        if not bounds:
            return self._preview_pixmap
        bounds.extend(self._indicator_hitboxes.values())
        min_x = min(x for x, _, _, _ in bounds)
        min_y = min(y for _, y, _, _ in bounds)
        max_x = max(x + w for x, _, w, _ in bounds)
        max_y = max(y + h for _, y, _, h in bounds)
        pad = max(18, int(round(24 * self._zoom_factor)))
        left = max(0, int(math.floor(min_x * self._zoom_factor)) - pad)
        top = max(0, int(math.floor(min_y * self._zoom_factor)) - pad)
        right = min(self._preview_pixmap.width(), int(math.ceil(max_x * self._zoom_factor)) + pad)
        bottom = min(self._preview_pixmap.height(), int(math.ceil(max_y * self._zoom_factor)) + pad)
        width = max(1, right - left)
        height = max(1, bottom - top)
        return self._preview_pixmap.copy(left, top, width, height)

    def clear_filter(self) -> bool:
        if not self._filter_node_id:
            return False
        self._filter_node_id = None
        self._update_filter_controls()
        self.refresh()
        self.center_selected_node()
        return True

    def apply_selected_filter(self) -> bool:
        if self._filter_node_id:
            return True
        node = self._selected_node() or self._latest_root
        if node is None:
            return False
        self._filter_node_id = node.node_id
        self._update_filter_controls()
        self.refresh()
        self.center_selected_node()
        return True

    def toggle_selected_filter(self) -> bool:
        if self._filter_node_id:
            return self.clear_filter()
        return self.apply_selected_filter()

    def _render_current(self, *, reset_zoom: bool, reset_canvas: bool = False) -> None:
        if not self._current_page_path:
            self.clear_content()
            return
        if self._detached_session is not None:
            root = self._detached_session.root
        else:
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
        if self._filter_node_id and self._filter_node_id not in valid_scope_ids:
            self._filter_node_id = None
        self._update_level_controls()
        self._sync_selected_node(root)
        self._svg_content = self._build_map_svg(root, reset_canvas=reset_canvas)
        if reset_zoom:
            self._zoom_factor = 1.0
        self._update_preview(fit=reset_zoom or reset_canvas)
        if reset_zoom:
            self._apply_initial_view()
        self._update_detached_mode_controls()
        self._update_filter_controls()

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
            offset_x, offset_y = self._preview_pixmap_offset()
            svg_anchor = QPointF((anchor.x() - offset_x) / old_zoom, (anchor.y() - offset_y) / old_zoom)
        self._zoom_factor = new_zoom
        self._update_preview(fit=False)
        if svg_anchor is not None and viewport_anchor is not None:
            new_offset_x, new_offset_y = self._preview_pixmap_offset()
            self.scroll_area.horizontalScrollBar().setValue(
                max(0, int(round(new_offset_x + (svg_anchor.x() * self._zoom_factor) - viewport_anchor.x())))
            )
            self.scroll_area.verticalScrollBar().setValue(
                max(0, int(round(new_offset_y + (svg_anchor.y() * self._zoom_factor) - viewport_anchor.y())))
            )
        # Update tooltip font size if visible
        if self._content_tooltip.isVisible():
            self._update_tooltip_font_size()

    def _selected_node_zoom_anchor(self) -> Optional[QPointF]:
        if not self._selected_node_id:
            return None
        hitbox = self._node_hitboxes.get(self._selected_node_id)
        if not hitbox:
            return None
        x, y, w, h = hitbox
        offset_x, offset_y = self._preview_pixmap_offset()
        return QPointF(offset_x + ((x + (w / 2.0)) * self._zoom_factor), offset_y + ((y + (h / 2.0)) * self._zoom_factor))

    def zoom_selected_node(self, delta: int) -> bool:
        if not self._svg_content:
            return False
        self._adjust_zoom(delta, self._selected_node_zoom_anchor())
        return True

    def focus_restore_target(self) -> str:
        return "rename" if self._inline_rename_edit.isVisible() else "map"

    def restore_selection_focus(self, line_number: int = 0, *, target: str = "map") -> None:
        if line_number > 0:
            self._pending_selected_line = int(line_number)
        self.refresh()
        if target == "rename" and self._position_inline_rename_editor():
            self._inline_rename_edit.setFocus(Qt.OtherFocusReason)
            self._inline_rename_edit.selectAll()
            return
        self.preview_label.setFocus(Qt.OtherFocusReason)

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
            self._cancel_hover_tooltip()
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
        
        # Ensure the label is always larger than viewport to enable panning
        viewport = self.scroll_area.viewport().size()
        pad_x = max(self._PREVIEW_PAD_X, viewport.width() // 2)
        pad_y = max(self._PREVIEW_PAD_Y, viewport.height() // 2)
        min_width = max(scaled.width() + (pad_x * 2), viewport.width() + 200)
        min_height = max(scaled.height() + (pad_y * 2), viewport.height() + 200)
        self.preview_label.resize(min_width, min_height)
        
        self.preview_label.setText("")
        self._position_inline_rename_editor()

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
        visible_nodes = self._visible_nodes(self._view_root(root) or root)
        if not visible_nodes:
            self._selected_node_id = None
            self._selected_node_ids.clear()
            self._selection_anchor_node_id = None
            self._pending_selected_line = None
            self._update_level_controls()
            return
        if self._pending_selected_line is not None:
            target = next((node for node in visible_nodes if node.line_number == self._pending_selected_line), None)
            self._pending_selected_line = None
            if target is not None:
                self._selected_node_id = target.node_id
                self._selected_node_ids = {target.node_id}
                self._selection_anchor_node_id = target.node_id
                if self._inline_rename_node_id and self._inline_rename_node_id != target.node_id:
                    self._cancel_inline_rename(restore_focus=False)
                self._update_level_controls()
                return
        visible_ids = {node.node_id for node in visible_nodes}
        if self._selected_node_id in visible_ids:
            self._selected_node_ids = {
                node_id for node_id in self._selected_node_ids
                if node_id in visible_ids
            } or ({self._selected_node_id} if self._selected_node_id else set())
            if self._selection_anchor_node_id not in visible_ids:
                self._selection_anchor_node_id = self._selected_node_id
            if self._inline_rename_node_id and self._inline_rename_node_id not in visible_ids:
                self._cancel_inline_rename(restore_focus=False)
            self._update_level_controls()
            return
        self._selected_node_id = visible_nodes[0].node_id
        self._selected_node_ids = {self._selected_node_id}
        self._selection_anchor_node_id = self._selected_node_id
        if self._inline_rename_node_id and self._inline_rename_node_id != self._selected_node_id:
            self._cancel_inline_rename(restore_focus=False)
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

    def _replace_heading_in_markdown(self, markdown_text: str, *, line_number: int, level: int, text: str) -> tuple[str, bool]:
        source = markdown_text or ""
        lines = source.splitlines()
        if line_number <= 0 or line_number > len(lines):
            return source, False
        heading_level = max(1, min(int(level or 1), HEADING_MAX_LEVEL))
        lines[line_number - 1] = f"{'#' * heading_level} {text.strip()}".rstrip()
        result = "\n".join(lines)
        if source.endswith("\n") or (source and not result.endswith("\n")):
            result += "\n"
        return result, True

    def _inline_rename_geometry(self, node: _MindNode) -> Optional[tuple[int, int, int, int]]:
        if not self._preview_pixmap:
            return None
        hitbox = self._node_hitboxes.get(node.node_id)
        if not hitbox:
            return None
        x, y, w, h = hitbox
        offset_x, offset_y = self._preview_pixmap_offset()
        left = int(round(offset_x + (x * self._zoom_factor) - 2))
        top = int(round(offset_y + (y * self._zoom_factor) - 2))
        width = max(140, int(round(w * self._zoom_factor)) + 4)
        height = max(26, int(round(h * self._zoom_factor)) + 4)
        return left, top, width, height

    def _position_inline_rename_editor(self) -> bool:
        if self._inline_rename_node_id is None:
            return False
        node = self._node_by_id(self._inline_rename_node_id)
        if node is None:
            return False
        geometry = self._inline_rename_geometry(node)
        if geometry is None:
            return False
        self._inline_rename_edit.setGeometry(*geometry)
        return True

    def _start_inline_rename(self) -> bool:
        if self._draft_heading is not None or self._detached_session is not None:
            return False
        node = self._selected_node()
        if node is None or node.line_number <= 0:
            return False
        self._inline_rename_node_id = node.node_id
        self._inline_rename_edit.setText(node.heading_text or node.label or "")
        if not self._position_inline_rename_editor():
            self._inline_rename_node_id = None
            return False
        self._inline_rename_edit.show()
        self._inline_rename_edit.raise_()
        self._inline_rename_edit.setFocus(Qt.ShortcutFocusReason)
        self._inline_rename_edit.selectAll()
        return True

    def _cancel_inline_rename(self, *, restore_focus: bool = True) -> bool:
        if self._inline_rename_node_id is None and not self._inline_rename_edit.isVisible():
            return False
        self._inline_rename_node_id = None
        self._inline_rename_edit.hide()
        if restore_focus:
            self.preview_label.setFocus(Qt.OtherFocusReason)
        return True

    def _commit_inline_rename(self) -> bool:
        node = self._selected_node()
        if (
            node is None
            or self._inline_rename_node_id != node.node_id
            or node.line_number <= 0
            or not self._current_page_path
        ):
            return self._cancel_inline_rename()
        heading_text = self._inline_rename_edit.text().strip()
        if not heading_text:
            self._show_status_message("Heading text cannot be empty.")
            self._inline_rename_edit.setFocus(Qt.OtherFocusReason)
            return False
        updated_markdown, changed = self._replace_heading_in_markdown(
            self._current_markdown,
            line_number=node.line_number,
            level=node.level,
            text=heading_text,
        )
        if not changed:
            return self._cancel_inline_rename()
        self._current_markdown = updated_markdown
        self._inline_rename_node_id = None
        self._inline_rename_edit.hide()
        self._pending_selected_line = node.line_number
        self.headingRenameRequested.emit(self._current_page_path, node.line_number, node.level, heading_text)
        self.refresh()
        self.preview_label.setFocus(Qt.OtherFocusReason)
        return True

    def _set_selected_node(self, node: Optional[_MindNode]) -> bool:
        node_id = node.node_id if node else None
        changed = node_id != self._selected_node_id
        if changed and self._inline_rename_node_id and self._inline_rename_node_id != node_id:
            self._cancel_inline_rename(restore_focus=False)
        self._selected_node_id = node_id
        self._selected_node_ids = {node_id} if node_id else set()
        self._selection_anchor_node_id = node_id
        if self._detached_session is not None:
            self._detached_session.selected_node_ids = set(self._selected_node_ids)
            self._detached_session.anchor_node_id = self._selection_anchor_node_id
            self._detached_session.focus_node_id = self._selected_node_id
        self._update_level_controls()
        if changed and self._latest_root is not None:
            self._svg_content = self._build_map_svg(self._latest_root, reset_canvas=False)
            self._update_preview(fit=False)
        return changed

    def _selected_node_center(self) -> Optional[tuple[float, float]]:
        if not self._preview_pixmap or not self._selected_node_id:
            return None
        hitbox = self._node_hitboxes.get(self._selected_node_id)
        if not hitbox:
            return None
        x, y, w, h = hitbox
        offset_x, offset_y = self._preview_pixmap_offset()
        return (offset_x + ((x + (w / 2)) * self._zoom_factor), offset_y + ((y + (h / 2)) * self._zoom_factor))

    def _preview_pixmap_offset(self) -> tuple[float, float]:
        pixmap = self.preview_label.pixmap()
        if not pixmap:
            return (0.0, 0.0)
        label_size = self.preview_label.size()
        pm_size = pixmap.size()
        return (
            max(0.0, (label_size.width() - pm_size.width()) / 2),
            max(0.0, (label_size.height() - pm_size.height()) / 2),
        )

    def _animate_scrollbars_to(self, desired_x: int, desired_y: int) -> bool:
        hbar = self.scroll_area.horizontalScrollBar()
        vbar = self.scroll_area.verticalScrollBar()
        target_h = max(hbar.minimum(), min(desired_x, hbar.maximum()))
        target_v = max(vbar.minimum(), min(desired_y, vbar.maximum()))
        if target_h == hbar.value() and target_v == vbar.value():
            return False
        self._scroll_animations.clear()
        for bar, end_value in (
            (hbar, target_h),
            (vbar, target_v),
        ):
            animation = QPropertyAnimation(bar, b"value", self)
            animation.setDuration(220)
            animation.setEasingCurve(QEasingCurve.InOutCubic)
            animation.setStartValue(bar.value())
            animation.setEndValue(end_value)
            animation.start()
            self._scroll_animations.append(animation)
        return True

    def _animate_scroll_to_selected(self, *, viewport_fraction_x: float, viewport_fraction_y: float) -> bool:
        center = self._selected_node_center()
        if center is None:
            return False
        target_x, target_y = center
        viewport = self.scroll_area.viewport().size()
        desired_x = int(round(target_x - (viewport.width() * viewport_fraction_x)))
        desired_y = int(round(target_y - (viewport.height() * viewport_fraction_y)))
        return self._animate_scrollbars_to(desired_x, desired_y)

    def center_selected_node(self) -> bool:
        return self._animate_scroll_to_selected(viewport_fraction_x=0.5, viewport_fraction_y=0.5)

    def left_align_selected_node(self) -> bool:
        return self._animate_scroll_to_selected(viewport_fraction_x=0.14, viewport_fraction_y=0.5)

    def _scroll_selected_into_view(self, *, padding: int = 36) -> bool:
        if not self._preview_pixmap or not self._selected_node_id:
            return False
        hitbox = self._node_hitboxes.get(self._selected_node_id)
        if not hitbox:
            return False
        offset_x, offset_y = self._preview_pixmap_offset()
        x, y, w, h = hitbox
        left = offset_x + (x * self._zoom_factor)
        top = offset_y + (y * self._zoom_factor)
        right = offset_x + ((x + w) * self._zoom_factor)
        bottom = offset_y + ((y + h) * self._zoom_factor)
        hbar = self.scroll_area.horizontalScrollBar()
        vbar = self.scroll_area.verticalScrollBar()
        viewport = self.scroll_area.viewport().size()
        desired_x = hbar.value()
        desired_y = vbar.value()
        viewport_left = hbar.value()
        viewport_top = vbar.value()
        viewport_right = viewport_left + viewport.width()
        viewport_bottom = viewport_top + viewport.height()
        if left < viewport_left + padding:
            desired_x = int(round(left - padding))
        elif right > viewport_right - padding:
            desired_x = int(round(right + padding - viewport.width()))
        if top < viewport_top + padding:
            desired_y = int(round(top - padding))
        elif bottom > viewport_bottom - padding:
            desired_y = int(round(bottom + padding - viewport.height()))
        return self._animate_scrollbars_to(desired_x, desired_y)

    def _node_sort_key(self, node: _MindNode) -> tuple[float, float, str]:
        return (node.y, node.x, node.node_id)

    def _visible_siblings(self, node: _MindNode) -> list[_MindNode]:
        view_root = self._view_root()
        if not view_root:
            return []
        parent = self._find_parent(view_root, node)
        if parent is None:
            return [node]
        return self._visible_children(parent)

    def _vertical_navigation_candidates(self, node: _MindNode) -> list[_MindNode]:
        view_root = self._view_root()
        if not view_root:
            return []
        return [
            candidate
            for candidate in self._visible_nodes(view_root)
            if candidate.node_id != node.node_id
            and candidate.depth == node.depth
            and candidate.side == node.side
            and candidate.line_number > 0
        ]

    def _hierarchy_neighbor(self, direction: str) -> Optional[_MindNode]:
        view_root = self._view_root()
        if not view_root:
            return None
        current = self._selected_node() or view_root
        if direction == "left":
            parent = self._find_parent(view_root, current)
            return parent
        if direction == "right":
            children = self._visible_children(current)
            if not children:
                return None
            return min(children, key=self._node_sort_key)
        if direction in {"up", "down"}:
            candidates = self._vertical_navigation_candidates(current)
            if not candidates:
                return None
            if direction == "up":
                directional = [node for node in candidates if node.y < current.y]
            else:
                directional = [node for node in candidates if node.y > current.y]
            if not directional:
                return None
            return min(
                directional,
                key=lambda node: (
                    abs(node.y - current.y),
                    abs(node.x - current.x),
                    node.y,
                    node.x,
                    node.node_id,
                ),
            )
        siblings = self._visible_siblings(current)
        if len(siblings) <= 1:
            return None
        try:
            index = next(idx for idx, sibling in enumerate(siblings) if sibling.node_id == current.node_id)
        except StopIteration:
            return None
        step = -1 if direction == "up" else 1
        return siblings[(index + step) % len(siblings)]

    def _visual_horizontal_neighbor(self, direction: str) -> Optional[_MindNode]:
        view_root = self._view_root()
        if not view_root:
            return None
        current = self._selected_node() or view_root
        if direction == "right":
            children = [child for child in self._visible_children(current) if child.line_number > 0]
            if not children:
                return None
            return min(
                children,
                key=lambda node: (
                    abs(node.y - current.y),
                    node.x - current.x,
                    abs(node.x - current.x) + abs(node.y - current.y),
                    node.y,
                    node.node_id,
                ),
            )
        candidates = [
            node
            for node in self._visible_nodes(view_root)
            if node.node_id != current.node_id and node.line_number > 0
        ]
        if not candidates:
            return None
        if direction == "left":
            directional = [node for node in candidates if node.x < current.x]
            if not directional:
                return None
            return min(
                directional,
                key=lambda node: (
                    abs(node.y - current.y),
                    current.x - node.x,
                    abs(node.x - current.x) + abs(node.y - current.y),
                    node.y,
                    node.node_id,
                ),
            )
        return None

    def _visual_neighbor(self, direction: str) -> Optional[_MindNode]:
        if direction in {"left", "right"}:
            node = self._visual_horizontal_neighbor(direction)
            if node is not None:
                return node
        return self._hierarchy_neighbor(direction)

    def _handle_navigation_key(self, key: int, mods: Qt.KeyboardModifiers) -> bool:
        if key in (Qt.Key_Right, Qt.Key_L) and not mods:
            node = self._selected_node()
            if node and node.children and not self._visible_children(node):
                return self._toggle_node(node)
        direction = None
        if key == Qt.Key_Left or (key == Qt.Key_H and not mods):
            direction = "left"
        elif key == Qt.Key_Right or (key == Qt.Key_L and not mods):
            direction = "right"
        elif key == Qt.Key_Up or (key == Qt.Key_K and not mods):
            direction = "up"
        elif key == Qt.Key_Down or (key == Qt.Key_J and not mods):
            direction = "down"
        return bool(direction and self._move_selection(direction))

    def _move_selection(self, direction: str) -> bool:
        node = self._visual_neighbor(direction)
        if not node:
            return False
        self._set_selected_node(node)
        self._scroll_selected_into_view()
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
        view_root = self._view_root(root) or root
        self._measure_tree(view_root, 1)
        self._assign_sides(view_root)
        view_root.x = 0.0
        view_root.y = 0.0
        self._layout_children(view_root, 1, 1)

        visible_nodes = self._visible_nodes(view_root)
        if not visible_nodes:
            visible_nodes = [view_root]
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
            if node is not view_root:
                parent = self._find_parent(view_root, node)
                if parent:
                    line_parts.append(self._render_edge(parent, node, offset_x, offset_y))
            node_parts.append(self._render_node(node, offset_x, offset_y, is_view_root=node is view_root))
        selected_stroke = self._theme_colors["filter_active_stroke"] if self._filter_active() else self._theme_colors["selected_stroke"]
        selected_shadow = self._theme_colors["filter_active_shadow"] if self._filter_active() else self._theme_colors["selected_shadow"]

        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
            "<style>"
            f"text {{ font-family: 'Noto Sans', 'Segoe UI', sans-serif; fill: {self._theme_colors['text']}; user-select: none; }}"
            f".root {{ fill: {self._theme_colors['root_fill']}; stroke: {self._theme_colors['root_stroke']}; stroke-width: 2; }}"
            f".branch-1 {{ fill: {self._theme_colors['branch_pos_fill']}; stroke: {self._theme_colors['branch_pos_stroke']}; stroke-width: 2; }}"
            f".branch--1 {{ fill: {self._theme_colors['branch_neg_fill']}; stroke: {self._theme_colors['branch_neg_stroke']}; stroke-width: 2; }}"
            f".child {{ fill: {self._theme_colors['child_fill']}; stroke: {self._theme_colors['child_stroke']}; stroke-width: 1.5; }}"
            f".collapsed {{ fill: {self._theme_colors['collapsed_fill']}; }}"
            f".selected {{ stroke: {selected_stroke}; stroke-width: 3.5; filter: drop-shadow(0 0 10px {selected_shadow}); }}"
            f".multi-selected {{ stroke: {selected_stroke}; stroke-width: 2.25; }}"
            f".drop-target {{ stroke: #16a34a; stroke-width: 3; stroke-dasharray: 8 5; }}"
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
            heading_text=draft.text,
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
        root = _MindNode(node_id=f"root:{page_path}", label=title, depth=0, level=0, heading_text=title)
        heading_stack: list[_MindNode] = [root]
        ordered_nodes: list[_MindNode] = []
        seq = 0
        self._root_is_page_h1 = False
        total_lines = len(markdown_text.splitlines())

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
                root.heading_text = title_text
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
                heading_text=title_text,
                line_number=line_number,
            )
            parent.children.append(node)
            heading_stack.append(node)
            ordered_nodes.append(node)

        root.line_number = 1 if total_lines > 0 else 0
        root.section_end_line = total_lines
        for index, node in enumerate(ordered_nodes):
            end_line = total_lines
            for candidate in ordered_nodes[index + 1:]:
                if candidate.level <= node.level:
                    end_line = max(node.line_number, candidate.line_number - 1)
                    break
            node.section_end_line = end_line
        for node in self._collect_nodes(root):
            if node.line_number <= 0:
                node.content_end_line = node.section_end_line
                continue
            direct_children = [child for child in node.children if child.line_number > 0]
            first_child_line = min((child.line_number for child in direct_children), default=node.section_end_line + 1)
            node.content_end_line = min(node.section_end_line, first_child_line - 1)
        root.content_end_line = root.section_end_line
        return root

    def _normalize_label(self, text: str) -> str:
        label = text.strip()
        label = re.sub(r"\[(.*?)\]\((.*?)\)", r"\1", label)
        label = re.sub(r"\[(.*?)\|(.*?)\]", r"\2", label)
        label = re.sub(r"`([^`]*)`", r"\1", label)
        label = re.sub(r"[_*~#]+", "", label)
        return label.strip() or "Heading"

    def _visible_children(self, node: _MindNode, inherited_depth: Optional[int] = None) -> list[_MindNode]:
        if node.node_id in self._collapsed_node_ids:
            return []
        child_depth = self._visible_child_depth(node, inherited_depth)
        if child_depth <= 0:
            return []
        return list(node.children)

    def _visible_child_depth(self, node: _MindNode, inherited_depth: Optional[int] = None) -> int:
        if node.node_id in self._collapsed_node_ids:
            return 0
        if inherited_depth is not None:
            return max(inherited_depth, self._scope_depth(node))
        visible_depth = self._visible_depth_in_view(node)
        if visible_depth is None:
            return 0
        return max(visible_depth, self._scope_depth(node))

    def _visible_depth_in_view(self, target: _MindNode) -> Optional[int]:
        view_root = self._view_root()
        if view_root is None:
            return None
        return self._visible_depth_for_target(view_root, target.node_id, 1)

    def _visible_depth_for_target(self, node: _MindNode, target_id: str, inherited_depth: int) -> Optional[int]:
        if node.node_id == target_id:
            return inherited_depth
        if node.node_id in self._collapsed_node_ids:
            return None
        child_depth = max(inherited_depth, self._scope_depth(node))
        if child_depth <= 0:
            return None
        next_depth = max(0, child_depth - 1)
        for child in node.children:
            result = self._visible_depth_for_target(child, target_id, next_depth)
            if result is not None:
                return result
        return None

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

    def _measure_tree(self, node: _MindNode, inherited_depth: int = 1) -> None:
        metrics = QFontMetrics(QApplication.font())
        lines = textwrap.wrap(node.label, width=self._MAX_TEXT_WIDTH) or [node.label]
        node.lines = lines[:4]
        text_width = max(metrics.horizontalAdvance(line) for line in node.lines) if node.lines else 40
        node.width = float(text_width + (self._BOX_HPAD * 2))
        node.height = float((len(node.lines) * self._LINE_HEIGHT) + (self._BOX_VPAD * 2))
        visible_children = self._visible_children(node, inherited_depth)
        next_depth = max(0, self._visible_child_depth(node, inherited_depth) - 1)
        for child in visible_children:
            self._measure_tree(child, next_depth)
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

    def _layout_children(self, parent: _MindNode, side: int, inherited_depth: int = 1) -> None:
        children = self._visible_children(parent, inherited_depth)
        if not children:
            return
        next_depth = max(0, self._visible_child_depth(parent, inherited_depth) - 1)
        total_height = sum(child.subtree_height for child in children)
        total_height += self._V_GAP * (len(children) - 1)
        current_top = parent.y - (total_height / 2)
        for child in children:
            child.y = current_top + (child.subtree_height / 2)
            child.x = parent.x + side * ((parent.width / 2) + self._H_GAP + (child.width / 2))
            current_top += child.subtree_height + self._V_GAP
            self._layout_children(child, side, next_depth)

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

    def _render_node(self, node: _MindNode, ox: float, oy: float, *, is_view_root: bool = False) -> str:
        x = node.x + ox - (node.width / 2)
        y = node.y + oy - (node.height / 2)
        css = "root" if is_view_root or node.depth == 0 else ("branch-1" if node.depth == 1 and node.side > 0 else "branch--1" if node.depth == 1 else "child")
        if node.node_id in self._collapsed_node_ids:
            css += " collapsed"
        if node.node_id == self._selected_node_id:
            css += " selected"
        elif node.node_id in self._selected_node_ids:
            css += " multi-selected"
        if node.node_id == self._drop_target_node_id and self._drop_target_valid:
            css += " drop-target"
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
        pm_size = pixmap.size()
        offset_x, offset_y = self._preview_pixmap_offset()
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
        if node.depth == 0:
            if node.node_id in self._collapsed_node_ids:
                self._collapsed_node_ids.discard(node.node_id)
            else:
                self._collapsed_node_ids.add(node.node_id)
                self._scope_expansion_depths.clear()
            self._update_level_controls()
            self.refresh()
            return True
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

    def _drop_target_for_node(self, node: Optional[_MindNode]) -> tuple[Optional[_MindNode], bool]:
        if node is None or node.line_number <= 0:
            return None, False
        moved_nodes = [self._node_by_id(node_id) for node_id in self._selected_node_ids]
        moved_nodes = [candidate for candidate in moved_nodes if candidate is not None]
        if not moved_nodes:
            return None, False
        if any(self._subtree_contains(moved, node) for moved in moved_nodes):
            return node, False
        new_level = self._direct_child_level(node)
        focus = self._selected_node()
        if focus is None:
            return node, False
        delta = new_level - focus.level
        if any(self._max_subtree_level(moved) + delta > HEADING_MAX_LEVEL for moved in moved_nodes):
            return node, False
        return node, True

    def _update_detached_mode_controls(self) -> None:
        active = self._detached_session is not None
        self.detached_modal.setVisible(active)
        self.accept_btn.setVisible(active)
        self.cancel_btn.setVisible(active)
        self.note_toggle_btn.setEnabled(not active)
        if active:
            self._cancel_hover_tooltip()

    def _show_status_message(self, message: str, timeout_ms: int = 3500) -> None:
        if message:
            self.statusMessageRequested.emit(message, timeout_ms)

    def _toggle_content_previews(self, checked: bool) -> None:
        self._content_preview_enabled = bool(checked)
        was_blocked = self.note_toggle_btn.blockSignals(True)
        self.note_toggle_btn.setChecked(self._content_preview_enabled)
        self.note_toggle_btn.blockSignals(was_blocked)
        config.save_map_note_panel_visible(self._content_preview_enabled)
        if not self._content_preview_enabled:
            self._cancel_hover_tooltip()

    def _increase_note_font_size(self) -> None:
        """Increase the note preview font size offset."""
        self._note_font_size_offset = min(10, self._note_font_size_offset + 1)
        config.save_map_note_font_size_offset(self._note_font_size_offset)
        if self._content_tooltip.isVisible():
            self._update_tooltip_font_size()

    def _decrease_note_font_size(self) -> None:
        """Decrease the note preview font size offset."""
        self._note_font_size_offset = max(-5, self._note_font_size_offset - 1)
        config.save_map_note_font_size_offset(self._note_font_size_offset)
        if self._content_tooltip.isVisible():
            self._update_tooltip_font_size()

    def _update_tooltip_font_size(self) -> None:
        """Update the tooltip font size based on note font preference only."""
        # Keep note text size independent from map canvas zoom.
        zoom = 1.0 + (self._note_font_size_offset * 0.1)
        self._content_tooltip.set_font_zoom(zoom)

    def _tooltip_debug_log(self, event: str, **fields) -> None:
        if not log_enabled("ui_state"):
            return
        try:
            focus = QApplication.focusWidget()
            focus_name = focus.__class__.__name__ if focus else "None"
            parts = [
                f"event={event}",
                f"visible={self._content_tooltip.isVisible()}",
                f"pinned={self._tooltip_pinned}",
                f"hovered={self._tooltip_hovered}",
                f"selected_popup={self._selected_note_popup_active}",
                f"focus={focus_name}",
            ]
            for key, value in fields.items():
                parts.append(f"{key}={value!r}")
            print("[MAP_TOOLTIP] " + " ".join(parts))
        except Exception:
            pass

    def flush_pending_changes(self) -> bool:
        return False

    def _clear_drag_state(self) -> None:
        self._drag_press_node_id = None
        self._drag_start_pos = None
        self._drag_active = False
        self._drop_target_node_id = None
        self._drop_target_valid = False
        if getattr(self, "preview_label", None) is not None:
            self.preview_label.setCursor(Qt.ArrowCursor)

    def _node_tooltip_markdown(self, node: Optional[_MindNode]) -> str:
        if node is None:
            return ""
        if node.depth == 0:
            text = self._current_markdown or ""
            return text if text.endswith("\n") or not text else f"{text}\n"
        if node.line_number <= 0:
            return ""
        lines = (self._current_markdown or "").splitlines()
        if not lines or node.line_number > len(lines):
            return ""
        end_line = min(max(node.line_number, node.section_end_line), len(lines))
        text = "\n".join(lines[node.line_number - 1:end_line])
        return text if text.endswith("\n") or not text else f"{text}\n"

    def _cancel_hover_tooltip(self) -> None:
        self._tooltip_debug_log("cancel_hover_tooltip:start")
        self._tooltip_timer.stop()
        self._tooltip_hide_timer.stop()
        self._hovered_node_id = None
        self._hover_global_pos = None
        self._tooltip_pinned = False
        self._tooltip_hovered = False
        self._selected_note_popup_active = False
        try:
            self._content_tooltip.set_pinned(False)
            self._content_tooltip.hide()
        except Exception:
            pass
        self._tooltip_debug_log("cancel_hover_tooltip:end")

    def _schedule_hover_tooltip(self, node: Optional[_MindNode], global_pos: QPoint) -> None:
        self._tooltip_debug_log(
            "schedule_hover_tooltip",
            node_id=(node.node_id if node is not None else None),
            x=global_pos.x(),
            y=global_pos.y(),
        )
        if self._tooltip_pinned:
            return
        if not self._content_preview_enabled or self._detached_session is not None:
            self._cancel_hover_tooltip()
            return
        self._tooltip_hide_timer.stop()
        node_id = node.node_id if node is not None else None
        if node_id is None:
            if self._content_tooltip.isVisible():
                self._tooltip_hide_timer.start()
            else:
                self._cancel_hover_tooltip()
            return
        if node_id == self._hovered_node_id and self._content_tooltip.isVisible():
            return
        self._hovered_node_id = node_id
        self._hover_global_pos = QPoint(global_pos)
        self._tooltip_timer.start()
        try:
            self._content_tooltip.hide()
        except Exception:
            pass

    def _on_tooltip_hover_changed(self, hovered: bool) -> None:
        self._tooltip_hovered = bool(hovered)
        if hovered:
            self._tooltip_hide_timer.stop()
            return
        if not self._tooltip_pinned and self._content_tooltip.isVisible():
            self._tooltip_hide_timer.start()

    def _hide_hover_tooltip_if_idle(self) -> None:
        if self._tooltip_pinned or self._tooltip_hovered:
            return
        try:
            self._content_tooltip.hide()
        except Exception:
            pass

    def _on_tooltip_dismissed(self) -> None:
        self._tooltip_debug_log("tooltip_dismissed:start")
        self._tooltip_timer.stop()
        self._tooltip_hide_timer.stop()
        self._tooltip_pinned = False
        self._tooltip_hovered = False
        self._selected_note_popup_active = False
        self.preview_label.setFocus(Qt.OtherFocusReason)
        self._tooltip_debug_log("tooltip_dismissed:end")

    def _pin_hover_tooltip(self) -> None:
        if not self._content_tooltip.isVisible():
            return
        self._tooltip_debug_log("pin_hover_tooltip:start")
        self._tooltip_timer.stop()
        self._tooltip_hide_timer.stop()
        self._tooltip_pinned = True
        self._selected_note_popup_active = False
        self._content_tooltip.set_pinned(True)
        try:
            self._content_tooltip.show()
            self._content_tooltip.raise_()
            self._content_tooltip.activateWindow()
            self._content_tooltip._editor.setFocus(Qt.OtherFocusReason)
        except Exception:
            pass
        self._tooltip_debug_log("pin_hover_tooltip:end")

    def _show_hover_tooltip(self) -> None:
        self._tooltip_debug_log("show_hover_tooltip:start", hovered_node=self._hovered_node_id)
        if not self._content_preview_enabled or self._hovered_node_id is None:
            return
        node = self._node_by_id(self._hovered_node_id)
        text = self._node_tooltip_markdown(node)
        if not text.strip():
            return
        pos = self._hover_global_pos or QCursor.pos()
        self._tooltip_pinned = False
        self._tooltip_hovered = False
        self._selected_note_popup_active = False
        self._content_tooltip.set_pinned(False)
        self._update_tooltip_font_size()
        self._content_tooltip.show_markdown(text, self._current_page_path, pos)
        self._tooltip_debug_log("show_hover_tooltip:end", text_len=len(text))

    def _selected_node_popup_anchor(self) -> QPoint:
        node = self._selected_node()
        if node is None:
            return QCursor.pos()
        hitbox = self._node_hitboxes.get(node.node_id)
        if not hitbox:
            return QCursor.pos()
        x, y, w, h = hitbox
        offset_x, offset_y = self._preview_pixmap_offset()
        local_point = QPoint(
            int(round(offset_x + ((x + (w / 2.0)) * self._zoom_factor))),
            int(round(offset_y + ((y + h) * self._zoom_factor))),
        )
        return self.preview_label.mapToGlobal(local_point)

    def _show_selected_node_note_popup(self) -> bool:
        self._tooltip_debug_log("show_selected_popup:start")
        node = self._selected_node()
        if node is None:
            return False
        text = self._node_tooltip_markdown(node)
        if not text.strip():
            return False
        self._tooltip_timer.stop()
        self._tooltip_hide_timer.stop()
        self._tooltip_pinned = True
        self._tooltip_hovered = False
        self._selected_note_popup_active = True
        self._content_tooltip.set_pinned(True)
        self._update_tooltip_font_size()
        self._content_tooltip.show_markdown(text, self._current_page_path, self._selected_node_popup_anchor())
        try:
            self._content_tooltip.raise_()
            self._content_tooltip.activateWindow()
        except Exception:
            pass
        QTimer.singleShot(0, self._content_tooltip.focus_reader)
        self._tooltip_debug_log("show_selected_popup:end", node_id=node.node_id, text_len=len(text))
        return True

    def _page_selected_node_note_popup(self) -> bool:
        self._tooltip_debug_log("page_selected_popup:start")
        if not (self._selected_note_popup_active and self._content_tooltip.isVisible()):
            self._tooltip_debug_log("page_selected_popup:skip")
            return False
        try:
            self._content_tooltip.raise_()
            self._content_tooltip.activateWindow()
        except Exception:
            pass
        self._content_tooltip.focus_reader()
        self._content_tooltip.page_forward()
        self._tooltip_debug_log("page_selected_popup:end")
        return True

    def _handle_selected_note_popup_keypress(self, key: int, mods: Qt.KeyboardModifiers) -> bool:
        if not (self._selected_note_popup_active and self._content_tooltip.isVisible()):
            return False
        self._tooltip_debug_log("selected_popup_keypress", key=int(key), mods=getattr(mods, "value", 0))
        if key == Qt.Key_Escape and mods == Qt.NoModifier:
            self._content_tooltip.hide()
            self._on_tooltip_dismissed()
            return True
        if key in (Qt.Key_Return, Qt.Key_Enter) and mods == Qt.AltModifier:
            self._content_tooltip.hide()
            self._on_tooltip_dismissed()
            return True
        if key in (Qt.Key_Left, Qt.Key_Right) and mods == Qt.NoModifier:
            self._content_tooltip.hide()
            self._on_tooltip_dismissed()
            return True
        if (
            config.load_vi_mode_enabled()
            and key in (Qt.Key_H, Qt.Key_L)
            and mods == Qt.NoModifier
        ):
            self._content_tooltip.hide()
            self._on_tooltip_dismissed()
            return True
        if key in (Qt.Key_Up, Qt.Key_Down) and mods == Qt.NoModifier:
            if key == Qt.Key_Down:
                self._content_tooltip.line_forward()
            else:
                self._content_tooltip.line_backward()
            return True
        if (
            config.load_vi_mode_enabled()
            and key in (Qt.Key_J, Qt.Key_K)
            and mods == Qt.NoModifier
        ):
            if key == Qt.Key_J:
                self._content_tooltip.line_forward()
            else:
                self._content_tooltip.line_backward()
            return True
        if (
            config.load_vi_mode_enabled()
            and key in (Qt.Key_J, Qt.Key_K)
            and mods == (Qt.ControlModifier | Qt.ShiftModifier)
        ):
            if key == Qt.Key_J:
                self._content_tooltip.page_forward()
            else:
                self._content_tooltip.page_backward()
            return True
        if key == Qt.Key_Space and mods == Qt.ControlModifier:
            self._tooltip_debug_log("selected_popup_keypress:ctrl_space")
            self._content_tooltip.page_forward()
            return True
        if key in (Qt.Key_PageDown, Qt.Key_Space) and mods == Qt.NoModifier:
            self._content_tooltip.page_forward()
            return True
        if key == Qt.Key_PageUp and mods == Qt.NoModifier:
            self._content_tooltip.page_backward()
            return True
        return False

    def _clone_node_tree(self, node: _MindNode) -> _MindNode:
        cloned = _MindNode(
            node_id=node.node_id,
            label=node.label,
            depth=node.depth,
            level=node.level,
            heading_text=node.heading_text,
            line_number=node.line_number,
            section_end_line=node.section_end_line,
            content_end_line=node.content_end_line,
            side=node.side,
        )
        cloned.children = [self._clone_node_tree(child) for child in node.children]
        return cloned

    def _sync_detached_selection_state(self) -> None:
        session = self._detached_session
        if session is None:
            return
        self._latest_root = session.root
        self._selected_node_ids = set(session.selected_node_ids)
        self._selection_anchor_node_id = session.anchor_node_id
        self._selected_node_id = session.focus_node_id

    def _ensure_detached_session(self) -> Optional[_DetachedSession]:
        if self._detached_session is not None:
            return self._detached_session
        if not self._latest_root or not self._current_page_path:
            return None
        session = _DetachedSession(
            root=self._clone_node_tree(self._latest_root),
            base_text=self._current_markdown,
            selected_node_ids=set(self._selected_node_ids) or ({self._selected_node_id} if self._selected_node_id else set()),
            anchor_node_id=self._selection_anchor_node_id or self._selected_node_id,
            focus_node_id=self._selected_node_id,
        )
        self._detached_session = session
        self._sync_detached_selection_state()
        self.refresh()
        return session

    def _selection_siblings_for(self, node: _MindNode) -> tuple[Optional[_MindNode], list[_MindNode]]:
        if not self._latest_root:
            return None, []
        parent = self._find_parent(self._latest_root, node)
        if parent is None:
            return None, []
        siblings = [child for child in parent.children if child.line_number > 0]
        return parent, siblings

    def _select_range(self, anchor_node: _MindNode, focus_node: _MindNode) -> bool:
        parent, siblings = self._selection_siblings_for(anchor_node)
        focus_parent, _ = self._selection_siblings_for(focus_node)
        if parent is None or focus_parent is None or parent.node_id != focus_parent.node_id:
            self._show_status_message("Multi-select only supports same-parent siblings.")
            return False
        if anchor_node.level != focus_node.level:
            self._show_status_message("Multi-select only supports headings of the same level.")
            return False
        positions = {node.node_id: idx for idx, node in enumerate(siblings)}
        if anchor_node.node_id not in positions or focus_node.node_id not in positions:
            return False
        start = min(positions[anchor_node.node_id], positions[focus_node.node_id])
        end = max(positions[anchor_node.node_id], positions[focus_node.node_id])
        self._selected_node_ids = {siblings[idx].node_id for idx in range(start, end + 1)}
        self._selected_node_id = focus_node.node_id
        self._selection_anchor_node_id = anchor_node.node_id
        if self._detached_session is not None:
            self._detached_session.selected_node_ids = set(self._selected_node_ids)
            self._detached_session.focus_node_id = self._selected_node_id
            self._detached_session.anchor_node_id = self._selection_anchor_node_id
        self.refresh()
        return True

    def _move_selection_extended(self, direction: str) -> bool:
        current = self._selected_node()
        if current is None:
            return False
        candidate = self._hierarchy_neighbor(direction)
        if candidate is None:
            return False
        anchor = self._node_by_id(self._selection_anchor_node_id) if self._selection_anchor_node_id else current
        return self._select_range(anchor or current, candidate)

    def _selected_sibling_block(self) -> tuple[Optional[_MindNode], list[_MindNode], int, int]:
        focus = self._selected_node()
        if focus is None:
            return None, [], -1, -1
        parent, siblings = self._selection_siblings_for(focus)
        if parent is None or not siblings:
            return None, [], -1, -1
        indexes = [idx for idx, child in enumerate(siblings) if child.node_id in self._selected_node_ids]
        if not indexes:
            return parent, siblings, -1, -1
        start = min(indexes)
        end = max(indexes)
        contiguous = all(siblings[idx].node_id in self._selected_node_ids for idx in range(start, end + 1))
        same_level = len({siblings[idx].level for idx in indexes}) == 1
        if not contiguous or not same_level:
            return parent, siblings, -1, -1
        return parent, siblings, start, end

    def _selected_raw_block(self) -> tuple[Optional[_MindNode], list[_MindNode], int, int]:
        focus = self._selected_node()
        if focus is None or not self._latest_root:
            return None, [], -1, -1
        parent = self._find_parent(self._latest_root, focus)
        if parent is None:
            return None, [], -1, -1
        children = list(parent.children)
        indexes = [idx for idx, child in enumerate(children) if child.node_id in self._selected_node_ids]
        if not indexes:
            return parent, children, -1, -1
        start = min(indexes)
        end = max(indexes)
        if not all(children[idx].node_id in self._selected_node_ids for idx in range(start, end + 1)):
            return parent, children, -1, -1
        return parent, children, start, end

    def _recompute_depths(self, node: _MindNode, depth: int = 0) -> None:
        node.depth = depth
        for child in node.children:
            self._recompute_depths(child, depth + 1)

    def _adjust_subtree_levels(self, node: _MindNode, delta: int) -> None:
        if node.line_number > 0:
            node.level = max(1, min(HEADING_MAX_LEVEL, node.level + delta))
        for child in node.children:
            self._adjust_subtree_levels(child, delta)

    def _max_subtree_level(self, node: _MindNode) -> int:
        maximum = node.level if node.line_number > 0 else 0
        for child in node.children:
            maximum = max(maximum, self._max_subtree_level(child))
        return maximum

    def _subtree_contains(self, node: _MindNode, target: _MindNode) -> bool:
        if node.node_id == target.node_id:
            return True
        return any(self._subtree_contains(child, target) for child in node.children)

    def _direct_child_level(self, parent: _MindNode) -> int:
        return self._new_heading_level(parent, as_child=True)

    def _apply_block_move(
        self,
        old_parent: _MindNode,
        raw_children: list[_MindNode],
        start: int,
        end: int,
        new_parent: _MindNode,
        insert_at: int,
        new_level: int,
    ) -> bool:
        block = raw_children[start:end + 1]
        if any(self._subtree_contains(node, new_parent) for node in block):
            self._show_status_message("Cannot move a heading into itself or its own subtree.")
            return True
        delta = new_level - block[0].level
        if any(self._max_subtree_level(node) + delta > HEADING_MAX_LEVEL for node in block):
            self._show_status_message("Move would exceed the maximum heading level.")
            return True
        old_parent.children = list(raw_children)
        del old_parent.children[start:end + 1]
        target_children = old_parent.children if new_parent.node_id == old_parent.node_id else list(new_parent.children)
        if new_parent.node_id != old_parent.node_id:
            new_parent.children = target_children
        insert_at = max(0, min(insert_at, len(target_children)))
        for node in block:
            self._adjust_subtree_levels(node, delta)
        target_children[insert_at:insert_at] = block
        self._recompute_depths(self._latest_root or new_parent)
        self._scope_expansion_depths[new_parent.node_id] = max(self._scope_depth(new_parent), 1)
        if self._detached_session is not None:
            self._detached_session.selected_node_ids = set(self._selected_node_ids)
            self._detached_session.anchor_node_id = self._selection_anchor_node_id
            self._detached_session.focus_node_id = self._selected_node_id
        self.refresh()
        return True

    def _move_selected_block(self, step: int) -> bool:
        session = self._ensure_detached_session()
        if session is None:
            return False
        self._sync_detached_selection_state()
        parent, raw_siblings, start, end = self._selected_raw_block()
        if parent is None or start < 0 or end < start:
            self._show_status_message("Move requires a contiguous selection of same-level sibling headings.")
            return True
        if step < 0 and start == 0:
            return True
        if step > 0 and end >= len(raw_siblings) - 1:
            return True
        insert_at = start - 1 if step < 0 else start + 1
        return self._apply_block_move(parent, raw_siblings, start, end, parent, insert_at, raw_siblings[start].level)

    def _indent_selected_block(self) -> bool:
        session = self._ensure_detached_session()
        if session is None:
            return False
        self._sync_detached_selection_state()
        parent, raw_siblings, start, end = self._selected_raw_block()
        if parent is None or start <= 0 or end < start:
            self._show_status_message("Indent requires a previous sibling target.")
            return True
        new_parent = raw_siblings[start - 1]
        new_level = self._direct_child_level(new_parent)
        insert_at = len(new_parent.children)
        return self._apply_block_move(parent, raw_siblings, start, end, new_parent, insert_at, new_level)

    def _outdent_selected_block(self) -> bool:
        session = self._ensure_detached_session()
        if session is None:
            return False
        self._sync_detached_selection_state()
        parent, raw_siblings, start, end = self._selected_raw_block()
        if parent is None or start < 0 or end < start or not self._latest_root:
            self._show_status_message("Outdent requires a nested heading selection.")
            return True
        grandparent = self._find_parent(self._latest_root, parent)
        if grandparent is None or parent.node_id == self._latest_root.node_id:
            self._show_status_message("Selection is already at the outermost level.")
            return True
        gp_children = list(grandparent.children)
        try:
            parent_index = next(idx for idx, child in enumerate(gp_children) if child.node_id == parent.node_id)
        except StopIteration:
            return False
        new_level = self._new_heading_level(parent, as_child=False)
        return self._apply_block_move(parent, raw_siblings, start, end, grandparent, parent_index + 1, new_level)

    def _drop_selected_block_onto(self, target: _MindNode) -> bool:
        session = self._ensure_detached_session()
        if session is None:
            return False
        self._sync_detached_selection_state()
        target = self._node_by_id(target.node_id) or target
        parent, raw_siblings, start, end = self._selected_raw_block()
        if parent is None or start < 0 or end < start:
            self._show_status_message("Drag move requires a contiguous heading selection.")
            return True
        if target.line_number <= 0:
            self._show_status_message("Cannot drop onto this node.")
            return True
        new_level = self._direct_child_level(target)
        return self._apply_block_move(parent, raw_siblings, start, end, target, len(target.children), new_level)

    def _handle_multi_select_keypress(self, key: int, mods: Qt.KeyboardModifiers) -> bool:
        if mods != Qt.ShiftModifier:
            return False
        direction = None
        if key in (Qt.Key_Up, Qt.Key_U):
            direction = "up"
        elif key in (Qt.Key_Down, Qt.Key_N):
            direction = "down"
        elif key in (Qt.Key_Left, Qt.Key_H):
            direction = "left"
        elif key in (Qt.Key_Right, Qt.Key_L):
            direction = "right"
        if not direction:
            return False
        return self._move_selection_extended(direction)

    def _handle_detached_reorder_keypress(self, key: int, mods: Qt.KeyboardModifiers) -> bool:
        if mods != Qt.ControlModifier:
            return False
        if key in (Qt.Key_Left, Qt.Key_H):
            return self._outdent_selected_block()
        if key in (Qt.Key_Right, Qt.Key_L):
            return self._indent_selected_block()
        if key in (Qt.Key_Up, Qt.Key_K):
            return self._move_selected_block(-1)
        if key in (Qt.Key_Down, Qt.Key_J):
            return self._move_selected_block(1)
        return False

    def _render_subtree_lines(
        self,
        node: _MindNode,
        source_lines: list[str],
        out_lines: list[str],
        line_map: dict[str, int],
    ) -> None:
        if node.line_number <= 0 or node.section_end_line < node.line_number:
            return
        line_map[node.node_id] = len(out_lines) + 1
        out_lines.append(self._render_heading_line(node))
        out_lines.extend(source_lines[node.line_number:node.content_end_line])
        if not node.children:
            return
        for index, child in enumerate(node.children):
            if index > 0 and out_lines and out_lines[-1].strip():
                out_lines.append("")
            self._render_subtree_lines(child, source_lines, out_lines, line_map)

    def _render_heading_line(self, node: _MindNode) -> str:
        heading_text = (node.heading_text or node.label or "Heading").strip() or "Heading"
        return f"{'#' * max(1, min(HEADING_MAX_LEVEL, node.level))} {heading_text}".rstrip()

    def _rebuild_markdown_from_tree(self, root: _MindNode, markdown_text: str) -> tuple[str, dict[str, int]]:
        source_lines = markdown_text.splitlines()
        if not source_lines:
            return "", {}
        root_children = [child for child in root.children if child.line_number > 0]
        if not root_children:
            return markdown_text, {}
        line_map: dict[str, int] = {}
        out_lines: list[str] = []
        first_child = min(root_children, key=lambda child: child.line_number)
        out_lines.extend(source_lines[:first_child.line_number - 1])
        for index, child in enumerate(root.children):
            if index > 0 and out_lines and out_lines[-1].strip():
                out_lines.append("")
            self._render_subtree_lines(child, source_lines, out_lines, line_map)
        result = "\n".join(out_lines)
        if markdown_text.endswith("\n") or not result.endswith("\n"):
            result += "\n"
        return result, line_map

    def cancel_detached_changes(self) -> bool:
        if self._detached_session is None:
            return False
        self._detached_session = None
        self._selected_node_ids = {self._selected_node_id} if self._selected_node_id else set()
        self._selection_anchor_node_id = self._selected_node_id
        self._clear_drag_state()
        self.refresh()
        self.preview_label.setFocus(Qt.ShortcutFocusReason)
        return True

    def commit_detached_changes(self) -> bool:
        session = self._detached_session
        if session is None or not self._current_page_path:
            return False
        rebuilt_text, line_map = self._rebuild_markdown_from_tree(session.root, session.base_text)
        focus_line = int(line_map.get(self._selected_node_id or "", 0))
        self._current_markdown = rebuilt_text
        self._pending_selected_line = focus_line or self._pending_selected_line
        self._detached_session = None
        self._clear_drag_state()
        self.headingReorderRequested.emit(self._current_page_path, session.base_text, rebuilt_text, focus_line)
        self.refresh()
        self.preview_label.setFocus(Qt.ShortcutFocusReason)
        return True

    def keyPressEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        if event.type() != QEvent.KeyPress:
            event.ignore()
            return
        if self._content_tooltip.isVisible() and not self._tooltip_pinned:
            self._cancel_hover_tooltip()
        key = event.key()
        mods = event.modifiers()
        if self._handle_selected_note_popup_keypress(key, mods):
            event.accept()
            return
        if key == Qt.Key_Escape and not mods and self._detached_session is not None:
            if self.cancel_detached_changes():
                event.accept()
                return
        if key in (Qt.Key_Return, Qt.Key_Enter) and not mods and self._detached_session is not None:
            if self.commit_detached_changes():
                event.accept()
                return
        if key == Qt.Key_J and mods == Qt.AltModifier:
            if self.zoom_selected_node(1):
                event.accept()
                return
        if key == Qt.Key_K and mods == Qt.AltModifier:
            if self.zoom_selected_node(-1):
                event.accept()
                return
        if key == Qt.Key_Down and mods == Qt.AltModifier:
            if self.zoom_selected_node(-1):
                event.accept()
                return
        if key == Qt.Key_Up and mods == Qt.AltModifier:
            if self.zoom_selected_node(1):
                event.accept()
                return
        if key == Qt.Key_C and mods == Qt.AltModifier:
            if self.center_selected_node():
                event.accept()
                return
        if key == Qt.Key_S and mods == Qt.AltModifier:
            if self.left_align_selected_node():
                event.accept()
                return
        if key == Qt.Key_F and not mods:
            self.fit_map()
            event.accept()
            return
        if self._draft_heading is not None:
            if self._handle_draft_keypress(event):
                event.accept()
                return
        if key == Qt.Key_Escape and not mods:
            if self._cancel_inline_rename():
                event.accept()
                return
            if self._content_tooltip.isVisible():
                self._content_tooltip.hide()
                self._on_tooltip_dismissed()
                event.accept()
                return
            if self._collapse_entire_map_to_root():
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
        if key in (Qt.Key_Return, Qt.Key_Enter) and mods == Qt.AltModifier:
            if self._page_selected_node_note_popup() or self._show_selected_node_note_popup():
                event.accept()
                return
        if key in (Qt.Key_Return, Qt.Key_Enter) and mods == Qt.ShiftModifier:
            if self._activate_selected_node(keep_focus=True):
                event.accept()
                return
        if key in (Qt.Key_Return, Qt.Key_Enter) and mods == Qt.ControlModifier:
            if self._start_inline_rename():
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
        if key == Qt.Key_I and mods == Qt.ControlModifier:
            if self._start_draft_heading(as_child=True):
                event.accept()
                return
        if key == Qt.Key_Space and not mods:
            node = self._selected_node()
            if node and node.children:
                if self._toggle_node(node):
                    event.accept()
                    return
        if self._handle_detached_reorder_keypress(key, mods):
            event.accept()
            return
        if self._handle_multi_select_keypress(key, mods):
            event.accept()
            return
        if self._handle_navigation_key(key, mods):
            event.accept()
            return
        super().keyPressEvent(event)

    def eventFilter(self, watched, event):  # type: ignore[override]
        if watched is self.preview_label and event.type() == QEvent.FocusIn:
            self.focusSyncRequested.emit()
        if watched is self.preview_label and event.type() == QEvent.Leave:
            # Keep keyboard-open selected-note popup stable when focus leaves preview.
            if self._tooltip_pinned or self._selected_note_popup_active:
                self._tooltip_debug_log("preview_leave:keep_pinned_popup")
                return False
            if self._content_tooltip.isVisible():
                self._tooltip_hide_timer.start()
            else:
                self._cancel_hover_tooltip()
        if watched is self.preview_label and isinstance(event, QMouseEvent):
            if self._draft_heading is not None and event.type() == event.Type.MouseButtonPress:
                event.accept()
                return True
            if self._inline_rename_edit.isVisible() and event.type() == event.Type.MouseButtonPress:
                self._cancel_inline_rename()
            if event.type() == event.Type.MouseMove and self._drag_press_node_id is None:
                svg_pos = self._event_svg_position(event)
                node = self._node_at_position(*svg_pos) if svg_pos else None
                self._schedule_hover_tooltip(node, event.globalPos())
            if event.type() == event.Type.MouseButtonPress and self._content_tooltip.isVisible() and not self._tooltip_pinned:
                self._pin_hover_tooltip()
                event.accept()
                return True
            if event.type() == event.Type.MouseButtonDblClick and event.button() == Qt.LeftButton:
                self._cancel_hover_tooltip()
                self.preview_label.setFocus(Qt.MouseFocusReason)
                if self._toggle_node_on_double_click(event):
                    event.accept()
                    return True
            if event.type() == event.Type.MouseButtonPress and event.button() == Qt.LeftButton:
                self._cancel_hover_tooltip()
                self.preview_label.setFocus(Qt.MouseFocusReason)
                if self._toggle_node_at(event):
                    event.accept()
                    return True
                svg_pos = self._event_svg_position(event)
                node = self._node_at_position(*svg_pos) if svg_pos else None
                if node is not None:
                    self._set_selected_node(node)
                    self._drag_press_node_id = node.node_id
                    self._drag_start_pos = QPointF(event.position())
                    self._drag_active = False
                    event.accept()
                    return True
            if event.type() == event.Type.MouseMove and self._drag_press_node_id is not None:
                self._cancel_hover_tooltip()
                if self._drag_start_pos is None:
                    return True
                if not self._drag_active:
                    distance = event.position() - self._drag_start_pos
                    if abs(distance.x()) + abs(distance.y()) < QApplication.startDragDistance():
                        event.accept()
                        return True
                    if self._ensure_detached_session() is None:
                        self._clear_drag_state()
                        return True
                    self._drag_active = True
                svg_pos = self._event_svg_position(event)
                node = self._node_at_position(*svg_pos) if svg_pos else None
                target, valid = self._drop_target_for_node(node)
                target_id = target.node_id if target is not None else None
                changed = target_id != self._drop_target_node_id or valid != self._drop_target_valid
                self._drop_target_node_id = target_id
                self._drop_target_valid = valid
                self.preview_label.setCursor(Qt.DragMoveCursor if valid else Qt.ForbiddenCursor)
                if changed:
                    self.refresh()
                event.accept()
                return True
            if event.type() == event.Type.MouseButtonRelease and event.button() == Qt.LeftButton and self._drag_press_node_id is not None:
                self._cancel_hover_tooltip()
                if self._drag_active:
                    target = self._node_by_id(self._drop_target_node_id) if self._drop_target_node_id else None
                    if target is not None and self._drop_target_valid:
                        self._drop_selected_block_onto(target)
                    self._clear_drag_state()
                    self.refresh()
                    event.accept()
                    return True
                svg_pos = self._event_svg_position(event)
                released = self._node_at_position(*svg_pos) if svg_pos else None
                pressed = self._node_by_id(self._drag_press_node_id)
                self._clear_drag_state()
                if released is not None and pressed is not None and released.node_id == pressed.node_id:
                    if released.line_number > 0 and self._current_page_path:
                        self._mark_activation_source("mouse")
                        self.headingActivated.emit(self._current_page_path, released.line_number)
                    event.accept()
                    return True
        if watched is self.preview_label and isinstance(event, QKeyEvent) and event.type() == QEvent.KeyPress:
            self._cancel_hover_tooltip()
            self.keyPressEvent(event)
            if event.isAccepted():
                return True
        if watched is self.preview_label and event.type() == QEvent.NativeGesture:
            self.preview_label.setFocus(Qt.OtherFocusReason)
        return super().eventFilter(watched, event)

    def focusInEvent(self, event) -> None:  # type: ignore[override]
        super().focusInEvent(event)
        self.focusSyncRequested.emit()
