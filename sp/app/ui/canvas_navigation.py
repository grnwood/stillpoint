"""Shared mouse and trackpad navigation policy for canvas-style views."""

from __future__ import annotations

from dataclasses import dataclass, field
import math

from PySide6.QtCore import QPointF, Qt


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

    pixel_delta = event.pixelDelta()
    if not pixel_delta.isNull():
        if event.modifiers() & Qt.ControlModifier:
            delta = pixel_delta.y() or pixel_delta.x()
            return CanvasWheelAction(zoom_steps=float(delta) / 40.0)
        x = float(pixel_delta.x())
        y = float(pixel_delta.y())
        if event.modifiers() & Qt.ShiftModifier and math.isclose(x, 0.0):
            x, y = y, 0.0
        return CanvasWheelAction(pan=QPointF(x, y))

    angle_delta = event.angleDelta()
    if event.modifiers() & Qt.ShiftModifier:
        delta = angle_delta.x() or angle_delta.y()
        return CanvasWheelAction(pan=QPointF(float(delta) / 3.0, 0.0))
    if angle_delta.y():
        return CanvasWheelAction(zoom_steps=float(angle_delta.y()) / 120.0)
    if angle_delta.x():
        return CanvasWheelAction(pan=QPointF(float(angle_delta.x()) / 3.0, 0.0))
    return CanvasWheelAction()


def zoom_factor(steps: float) -> float:
    """Return a smooth multiplicative zoom factor for normalized wheel steps."""

    return math.pow(1.1, steps)


def native_zoom_steps(value: float) -> float:
    """Convert Qt's incremental native pinch scale into normalized steps."""

    factor = max(0.01, 1.0 + value)
    return math.log(factor, 1.1)
