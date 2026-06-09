from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPoint, QRect, QSize
from PySide6.QtGui import QCursor, QGuiApplication, QScreen
from PySide6.QtWidgets import QWidget


def _screen_from_parent(parent: Optional[QWidget]) -> Optional[QScreen]:
    if parent is None:
        return None
    try:
        window = parent.window()
    except Exception:
        window = parent
    try:
        handle = window.windowHandle() if window is not None else None
        if handle is not None and handle.screen() is not None:
            return handle.screen()
    except Exception:
        pass
    try:
        screen = window.screen() if window is not None else None
        if screen is not None:
            return screen
    except Exception:
        pass
    return None


def popup_available_geometry(anchor: Optional[QPoint] = None, parent: Optional[QWidget] = None) -> QRect:
    """Resolve the best available screen geometry for placing a popup."""
    screen: Optional[QScreen] = None
    if anchor is not None:
        try:
            screen = QGuiApplication.screenAt(anchor)
        except Exception:
            screen = None
    if screen is None:
        screen = _screen_from_parent(parent)
    if screen is None:
        try:
            screen = QGuiApplication.screenAt(QCursor.pos())
        except Exception:
            screen = None
    if screen is None:
        try:
            screen = QGuiApplication.primaryScreen()
        except Exception:
            screen = None
    if screen is not None:
        return screen.availableGeometry()
    # Last-resort fallback when Qt cannot resolve any screen.
    return QRect(0, 0, 1920, 1080)


def clamp_popup_top_left(desired: QPoint, size: QSize, bounds: QRect, margin: int = 0) -> QPoint:
    """Clamp a popup top-left point so the whole popup stays within bounds."""
    left = int(bounds.left()) + int(margin)
    top = int(bounds.top()) + int(margin)
    max_x = int(bounds.right()) - int(size.width()) - int(margin) + 1
    max_y = int(bounds.bottom()) - int(size.height()) - int(margin) + 1
    if max_x < left:
        max_x = left
    if max_y < top:
        max_y = top
    x = max(left, min(int(desired.x()), max_x))
    y = max(top, min(int(desired.y()), max_y))
    return QPoint(x, y)
