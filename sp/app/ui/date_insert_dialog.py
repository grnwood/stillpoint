from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QDate
from PySide6.QtWidgets import (
    QCalendarWidget,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QLabel,
    QVBoxLayout,
    QToolButton,
    QMessageBox,
)

WEEKDAY_MAP = {
    "mon": 0, "monday": 0,
    "tue": 1, "tues": 1, "tuesday": 1,
    "wed": 2, "weds": 2, "wednesday": 2,
    "thu": 3, "thur": 3, "thurs": 3, "thursday": 3,
    "fri": 4, "friday": 4,
    "sat": 5, "saturday": 5,
    "sun": 6, "sunday": 6,
}


def _next_weekday(from_date: date, target_weekday: int) -> date:
    days_ahead = (target_weekday - from_date.weekday() + 7) % 7
    if days_ahead == 0:
        days_ahead = 7
    return from_date + timedelta(days=days_ahead)


def _this_weekday(from_date: date, target_weekday: int) -> date:
    start_of_week = from_date - timedelta(days=from_date.weekday())
    return start_of_week + timedelta(days=target_weekday)


def _apply_weekday_only(dt: date, weekdays_only: bool, prefer_forward: bool = True) -> date:
    """
    If weekdays_only is enabled and dt lands on Sat/Sun, shift it.
    prefer_forward=True shifts forward to Monday, prefer_forward=False shifts backward to Friday.
    """
    if not weekdays_only:
        return dt
    if dt.weekday() < 5:
        return dt
    delta = 1 if prefer_forward else -1
    while dt.weekday() >= 5:
        dt += timedelta(days=delta)
    return dt


def _add_business_days(base: date, days: int) -> date:
    """
    Add N business days (Mon-Fri), skipping weekends.
    If days == 0 and base is weekend, move forward to Monday.
    """
    if days == 0:
        return _apply_weekday_only(base, True, prefer_forward=True)

    step = 1 if days > 0 else -1
    remaining = abs(days)
    current = base
    while remaining > 0:
        current += timedelta(days=step)
        if current.weekday() < 5:
            remaining -= 1
    return current


def _end_of_week(from_date: date, weekdays_only: bool) -> date:
    """
    End of week means:
      - Saturday normally
      - Friday if weekdays_only is True

    If the computed end-of-week for the current ISO week is already in the past,
    roll forward to next week's end-of-week.
    """
    target = 4 if weekdays_only else 5  # Fri or Sat
    candidate = _this_weekday(from_date, target)
    if candidate < from_date:
        candidate += timedelta(days=7)
    return candidate


def parse_human_date(text: str, today: date, weekdays_only: bool) -> Optional[date]:
    clean = text.strip()
    if not clean:
        return None

    # Direct ISO date
    try:
        iso = date.fromisoformat(clean)
        return _apply_weekday_only(iso, weekdays_only, prefer_forward=True)
    except ValueError:
        pass

    lowered = clean.lower()

    # Simple keywords
    if lowered == "today":
        return _apply_weekday_only(today, weekdays_only, prefer_forward=True)

    if lowered == "tomorrow":
        base = today + timedelta(days=1)
        return _apply_weekday_only(base, weekdays_only, prefer_forward=True)

    if lowered == "yesterday":
        base = today - timedelta(days=1)
        return _apply_weekday_only(base, weekdays_only, prefer_forward=False)

    # Week anchors
    if lowered == "next week":
        # next week -> next Monday
        return _apply_weekday_only(_next_weekday(today, 0), weekdays_only, prefer_forward=True)

    if lowered == "this week":
        # this week -> this week's Monday
        return _apply_weekday_only(_this_weekday(today, 0), weekdays_only, prefer_forward=True)

    if lowered in ("end of week", "eow"):
        # end of week -> Saturday (or Friday if weekdays_only)
        return _end_of_week(today, weekdays_only)

    # next <weekday>
    next_match = re.match(r"^next\s+([a-z]+)$", lowered)
    if next_match:
        token = next_match.group(1)
        if token in WEEKDAY_MAP:
            return _apply_weekday_only(
                _next_weekday(today, WEEKDAY_MAP[token]),
                weekdays_only,
                prefer_forward=True,
            )

    # this <weekday> (same week), only if not already passed
    this_match = re.match(r"^this\s+([a-z]+)$", lowered)
    if this_match:
        token = this_match.group(1)
        if token in WEEKDAY_MAP:
            candidate = _this_weekday(today, WEEKDAY_MAP[token])
            if candidate < today:
                return None
            return _apply_weekday_only(candidate, weekdays_only, prefer_forward=True)

    # bare weekday like "mon", "fri", "tuesday" => treat as "next <weekday>" if today is that day
    if lowered in WEEKDAY_MAP:
        return _apply_weekday_only(
            _next_weekday(today, WEEKDAY_MAP[lowered]),
            weekdays_only,
            prefer_forward=True,
        )

    # in X days
    in_days = re.match(r"^in\s+(-?\d+)\s+days?$", lowered)
    if in_days:
        delta = int(in_days.group(1))
        if weekdays_only:
            # _add_business_days already returns a weekday; apply_weekday_only is harmless here.
            return _apply_weekday_only(_add_business_days(today, delta), True, prefer_forward=delta >= 0)
        return _apply_weekday_only(today + timedelta(days=delta), weekdays_only, prefer_forward=delta >= 0)

    # in X weeks (calendar weeks, but you said weekday-only should skip weekends for calculations,
    # so we treat weeks as 7 business days when weekday-only is enabled)
    in_weeks = re.match(r"^in\s+(-?\d+)\s+weeks?$", lowered)
    if in_weeks:
        weeks = int(in_weeks.group(1))
        if weekdays_only:
            # Interpret "in N weeks" as N*5 business days (not 7) OR N*7 business days?
            # Your prior code used *7 business days (skips weekends while counting), keeping "week" ~= 7 days.
            # Keep your original semantics: N*7 days applied as business-day counting.
            delta_days = weeks * 7
            return _apply_weekday_only(_add_business_days(today, delta_days), True, prefer_forward=delta_days >= 0)
        delta_days = weeks * 7
        return _apply_weekday_only(today + timedelta(days=delta_days), weekdays_only, prefer_forward=delta_days >= 0)

    # +N or -N (days)
    plus_minus = re.match(r"^([+-])\s*(\d+)\s*d?$", lowered)
    if plus_minus:
        sign = 1 if plus_minus.group(1) == "+" else -1
        delta = int(plus_minus.group(2)) * sign
        if weekdays_only:
            return _apply_weekday_only(_add_business_days(today, delta), True, prefer_forward=delta >= 0)
        return _apply_weekday_only(today + timedelta(days=delta), weekdays_only, prefer_forward=delta >= 0)

    return None


class DateInsertDialog(QDialog):
    """Dialog for inserting dates via calendar or text expressions."""

    def __init__(self, parent=None, anchor_pos=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Insert Date")
        self.setModal(True)
        self.resize(320, 360)

        self._valid_date: Optional[date] = None
        self._default_style = ""

        layout = QVBoxLayout(self)

        self.calendar = QCalendarWidget()
        self.calendar.setGridVisible(True)
        layout.addWidget(self.calendar, 1)

        form = QFormLayout()
        self.date_edit = QLineEdit()
        self.date_edit.setPlaceholderText("YYYY-mm-dd or 'next monday', 'in 2 days', 'eow'…")
        self._default_style = self.date_edit.styleSheet()
        form.addRow("Date:", self.date_edit)

        self.weekdays_only = QCheckBox("Weekdays only")
        self.weekdays_only.setChecked(True)
        form.addRow("", self.weekdays_only)
        layout.addLayout(form)

        hint_row = QHBoxLayout()
        hint = QLabel("Examples: today, tomorrow, next week, eow, next monday, in 3 days, +2")
        hint.setWordWrap(True)
        hint_row.addWidget(hint, 1)
        help_btn = QToolButton()
        help_btn.setText("?")
        help_btn.setToolTip(self._examples_text().replace("\n", "<br>"))
        help_btn.clicked.connect(self._show_examples)
        hint_row.addWidget(help_btn, 0, Qt.AlignTop)
        layout.addLayout(hint_row)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self._try_accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        # Wire signals
        self.calendar.selectionChanged.connect(self._on_calendar_changed)
        self.date_edit.textChanged.connect(self._on_text_changed)
        self.date_edit.returnPressed.connect(self._try_accept)
        self.weekdays_only.toggled.connect(self._on_constraints_changed)

        # Initialize with today
        today = date.today()
        self._apply_date(today)
        self.date_edit.setFocus()

        # Move near anchor if provided
        if anchor_pos is not None:
            self.move(anchor_pos)

    def selected_date_text(self) -> Optional[str]:
        if self._valid_date:
            return self._valid_date.isoformat()
        return None

    def _on_calendar_changed(self) -> None:
        qd = self.calendar.selectedDate()
        picked = date(qd.year(), qd.month(), qd.day())
        picked = _apply_weekday_only(picked, self.weekdays_only.isChecked(), prefer_forward=True)
        self._apply_date(picked, update_calendar=False)

    def _on_text_changed(self, text: str) -> None:
        parsed = parse_human_date(text, date.today(), self.weekdays_only.isChecked())
        if parsed:
            self._apply_date(parsed)
        else:
            self._set_invalid()

    def _on_constraints_changed(self) -> None:
        current_text = self.date_edit.text()
        if current_text:
            self._on_text_changed(current_text)
        else:
            self._set_invalid()

    def _examples_text(self) -> str:
        return (
            "Supported inputs:\n"
            "  • YYYY-mm-dd\n"
            "  • today / tomorrow / yesterday\n"
            "  • next week (→ next Monday)\n"
            "  • this week (→ this week's Monday)\n"
            "  • end of week / eow (→ Saturday; Friday if 'Weekdays only')\n"
            "  • next <weekday> (next monday, next fri)\n"
            "  • this <weekday> (only if not already passed)\n"
            "  • in X days / in X weeks\n"
            "  • +N / -N / +Nd (days delta)\n"
            "\n"
            "When 'Weekdays only' is checked: all calculations skip weekends."
        )

    def _show_examples(self) -> None:
        QMessageBox.information(self, "Date Input Help", self._examples_text())

    def _apply_date(self, dt: date, update_calendar: bool = True) -> None:
        self._valid_date = dt
        self.date_edit.blockSignals(True)
        self.date_edit.setText(dt.isoformat())
        self.date_edit.blockSignals(False)
        self.date_edit.setStyleSheet(self._default_style)

        ok_btn = self.buttons.button(QDialogButtonBox.Ok)
        if ok_btn:
            ok_btn.setEnabled(True)

        if update_calendar:
            self.calendar.blockSignals(True)
            self.calendar.setSelectedDate(QDate(dt.year, dt.month, dt.day))
            self.calendar.blockSignals(False)

    def _set_invalid(self) -> None:
        self._valid_date = None
        self.date_edit.setStyleSheet(self._default_style)
        ok_btn = self.buttons.button(QDialogButtonBox.Ok)
        if ok_btn:
            ok_btn.setEnabled(False)

    def _try_accept(self) -> None:
        if self._valid_date is None:
            return
        self.accept()
