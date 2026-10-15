"""Shared mouse and trackpad navigation policy for canvas-style views."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import sys

from PySide6.QtCore import QEvent, QObject, QPointF, Qt
from PySide6.QtGui import QInputDevice
from PySide6.QtWidgets import QWidget


@dataclass(frozen=True)
class CanvasWheelAction:
    """A normalized canvas action produced from a Qt wheel event."""

    pan: QPointF = field(default_factory=QPointF)
    zoom_steps: float = 0.0

    @property
    def is_zoom(self) -> bool:
        return not math.isclose(self.zoom_steps, 0.0)

    @property
    def is_pan(self) -> bool:
        return not self.pan.isNull()


def wheel_action(event) -> CanvasWheelAction:
    """Map precision trackpad and discrete mouse-wheel input consistently.

    Precision pixel deltas pan in both axes. A primary-modifier precision
    gesture zooms instead. Traditional mouse-wheel ticks zoom, while Shift
    converts a vertical wheel to horizontal panning.
    """

    modifiers = event.modifiers()
    primary_modifier = modifiers & (Qt.ControlModifier | Qt.MetaModifier)
    pixel_delta = event.pixelDelta()
    if not pixel_delta.isNull():
        if primary_modifier:
            delta = pixel_delta.y() or pixel_delta.x()
            return CanvasWheelAction(zoom_steps=float(delta) / 40.0)
        x = float(pixel_delta.x())
        y = float(pixel_delta.y())
        if modifiers & Qt.ShiftModifier and math.isclose(x, 0.0):
            x, y = y, 0.0
        return CanvasWheelAction(pan=QPointF(x, y))

    angle_delta = event.angleDelta()
    if sys.platform == "win32" and not primary_modifier:
        # Windows precision touchpads frequently arrive as angle-only mouse
        # wheel events with no scroll phase or TouchPad device metadata.  They
        # cannot be distinguished reliably from a notched wheel, so use the
        # suite-wide Windows rule: scrolling pans and Ctrl/Cmd+scroll zooms.
        x = float(angle_delta.x()) / 3.0
        y = float(angle_delta.y()) / 3.0
        if modifiers & Qt.ShiftModifier and math.isclose(x, 0.0):
            x, y = y, 0.0
        return CanvasWheelAction(pan=QPointF(x, y))
    if _is_precision_scroll(event):
        if primary_modifier:
            delta = angle_delta.y() or angle_delta.x()
            return CanvasWheelAction(zoom_steps=float(delta) / 120.0)
        x = float(angle_delta.x()) / 3.0
        y = float(angle_delta.y()) / 3.0
        if modifiers & Qt.ShiftModifier and math.isclose(x, 0.0):
            x, y = y, 0.0
        return CanvasWheelAction(pan=QPointF(x, y))
    if modifiers & Qt.ShiftModifier:
        delta = angle_delta.x() or angle_delta.y()
        return CanvasWheelAction(pan=QPointF(float(delta) / 3.0, 0.0))
    if angle_delta.y():
        return CanvasWheelAction(zoom_steps=float(angle_delta.y()) / 120.0)
    if angle_delta.x():
        return CanvasWheelAction(pan=QPointF(float(angle_delta.x()) / 3.0, 0.0))
    return CanvasWheelAction()


def _is_precision_scroll(event) -> bool:
    """Recognize trackpads even when Windows omits pixelDelta()."""

    try:
        device = event.device()
        if device is not None and device.type() == QInputDevice.DeviceType.TouchPad:
            return True
        if (device is not None
                and device.capabilities() & QInputDevice.Capability.PixelScroll):
            return True
    except (AttributeError, RuntimeError, TypeError):
        pass
    try:
        # Windows precision touchpads commonly emit angle-only events, but
        # unlike a notched mouse wheel they participate in a scroll phase.
        return event.phase() != Qt.NoScrollPhase
    except (AttributeError, RuntimeError, TypeError):
        return False


def zoom_factor(steps: float) -> float:
    """Return a smooth multiplicative zoom factor for normalized wheel steps."""

    return math.pow(1.1, steps)


def native_zoom_steps(value: float) -> float:
    """Convert Qt's incremental native pinch scale into normalized steps."""

    factor = max(0.01, 1.0 + value)
    return math.log(factor, 1.1)


class PrimaryWheelZoomFilter(QObject):
    """Route Ctrl/Cmd wheel gestures anywhere inside a widget to one zoom control."""

    def __init__(self, root, callback) -> None:
        super().__init__(root)
        self._root = root
        self._callback = callback
        self._accumulator = 0.0

    def eventFilter(self, watched, event):  # type: ignore[override]
        if (event.type() != QEvent.Wheel
                or not event.modifiers() & (Qt.ControlModifier | Qt.MetaModifier)):
            return False
        action = wheel_action(event)
        if not action.is_zoom:
            return False
        self._accumulator += action.zoom_steps
        while abs(self._accumulator) >= 1.0:
            direction = 1 if self._accumulator > 0 else -1
            self._callback(direction)
            self._accumulator -= direction
        event.accept()
        return True


def install_primary_wheel_zoom(root, callback) -> PrimaryWheelZoomFilter:
    """Install suite-standard Ctrl/Cmd wheel zoom for a widget subtree."""

    handlers: list[PrimaryWheelZoomFilter] = []
    descendants = (
        child for child in root.findChildren(QObject) if isinstance(child, QWidget)
    )
    for target in (root, *descendants):
        handler = PrimaryWheelZoomFilter(target, callback)
        target.installEventFilter(handler)
        handlers.append(handler)
    # Each handler is parented to the exact object it watches. This avoids
    # cross-widget native event-filter lifetimes during Qt teardown.
    root._primary_wheel_zoom_filters = handlers
    return handlers[0]
