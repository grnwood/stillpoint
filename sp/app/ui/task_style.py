from __future__ import annotations

from datetime import date, timedelta
import math
from typing import Optional

from PySide6.QtGui import QColor


def relative_day_label(target: date, prefix: str = "") -> str:
    today = date.today()
    delta_days = (target - today).days
    if delta_days <= 13:
        label = f"{max(delta_days, 0)}d"
    elif delta_days < 56:
        label = f"{max(1, math.ceil(delta_days / 7))}w"
    elif delta_days < 365:
        label = f"{max(1, math.ceil(delta_days / 30))}m"
    else:
        label = f"{max(1, math.ceil(delta_days / 365))}y"
    return f"{prefix}{label}" if label else ""


def priority_time_label(task: dict) -> tuple[str, bool]:
    priority_level = min(task.get("priority", 0) or 0, 3)
    priority = "!" * priority_level
    due_str = (task.get("due") or "").strip()
    start_str = (task.get("starts") or task.get("start") or "").strip()
    label = ""
    overdue = False
    if due_str:
        try:
            due_dt = date.fromisoformat(due_str)
            if due_dt < date.today():
                label = "OD"
                overdue = True
            else:
                label = relative_day_label(due_dt)
        except Exception:
            label = ""
    elif start_str:
        try:
            start_dt = date.fromisoformat(start_str)
            if start_dt > date.today():
                label = relative_day_label(start_dt, prefix=">")
        except Exception:
            label = ""
    if label and priority:
        return f"{label} {priority}", overdue
    if label:
        return label, overdue
    return priority, overdue


def contrast_text_color(bg: QColor) -> QColor:
    return QColor("#FFFFFF") if bg.lightness() < 128 else QColor("#000000")


def priority_brush(level: int) -> Optional[dict]:
    if level <= 0:
        return None
    colors = [
        {"bg": QColor("#FFF9C4")},
        {"bg": QColor("#F57900")},
        {"bg": QColor("#CC0000")},
    ]
    idx = min(level - 1, len(colors) - 1)
    bg = colors[idx]["bg"]
    return {"bg": bg, "fg": contrast_text_color(bg)}


def due_colors_from_due_str(
    due_str: str,
    *,
    include_tomorrow: bool = True,
) -> Optional[tuple[QColor | None, QColor | None]]:
    due_str = (due_str or "").strip()
    if not due_str:
        return None
    try:
        due_dt = date.fromisoformat(due_str)
    except ValueError:
        return None
    today_dt = date.today()
    if due_dt < today_dt:
        return QColor("#FFFFFF"), QColor("#CC0000")
    if due_dt == today_dt:
        return QColor("#3A1D00"), QColor("#F57900")
    if include_tomorrow and due_dt == today_dt + timedelta(days=1):
        return QColor("#444444"), QColor("#FDD835")
    return None


def due_colors_from_task(
    task: dict,
    *,
    include_tomorrow: bool = True,
) -> Optional[tuple[QColor | None, QColor | None]]:
    return due_colors_from_due_str(task.get("due") or "", include_tomorrow=include_tomorrow)
