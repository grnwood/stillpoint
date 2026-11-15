"""Small controls and source ranges shared by both Markdown readers."""
from __future__ import annotations

import re
from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QFrame, QHBoxLayout, QLabel,
    QPushButton, QToolButton, QVBoxLayout,
)


@dataclass(frozen=True)
class ReadingSourceBlock:
    kind: str
    line: int
    end_line: int
    markdown: str


def reading_source_blocks(markdown: str) -> list[ReadingSourceBlock]:
    """Find copyable top-level blocks without reconstructing their Markdown."""
    lines = markdown.splitlines(keepends=True)
    blocks = []
    index = 0
    while index < len(lines):
        start = index
        opening = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", lines[index].rstrip("\r\n"))
        if opening:
            fence = opening[1]
            index += 1
            while index < len(lines):
                closing = re.match(r"^ {0,3}" + re.escape(fence[0]) + r"{" + str(len(fence)) + r",}\s*$", lines[index])
                index += 1
                if closing:
                    break
            blocks.append(ReadingSourceBlock("code", start + 1, index, "".join(lines[start:index])))
        elif (
            index + 1 < len(lines) and "|" in lines[index]
            and all(re.fullmatch(r":?-{3,}:?", cell.strip()) for cell in lines[index + 1].strip().strip("|").split("|"))
        ):
            index += 2
            while index < len(lines) and lines[index].strip() and "|" in lines[index]:
                index += 1
            blocks.append(ReadingSourceBlock("table", start + 1, index, "".join(lines[start:index])))
        elif lines[index].startswith(("    ", "\t")) and (index == 0 or not lines[index - 1].strip()):
            index += 1
            while index < len(lines) and (not lines[index].strip() or lines[index].startswith(("    ", "\t"))):
                index += 1
            while index > start and not lines[index - 1].strip():
                index -= 1
            blocks.append(ReadingSourceBlock("code", start + 1, index, "".join(lines[start:index])))
        else:
            index += 1
    return blocks


class ReadingOptionsPopup(QFrame):
    optionsChanged = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Popup)
        self.setObjectName("readingOptionsPopup")
        self.setAccessibleName("Reading options")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        title = QLabel("Reading options")
        title.setStyleSheet("font-weight: 600;")
        layout.addWidget(title)
        form = QFormLayout()
        self.width_choice = QComboBox()
        for label, value in (("Narrow", "narrow"), ("Comfortable", "comfortable"), ("Wide", "wide"), ("Full width", "full")):
            self.width_choice.addItem(label, value)
        self.width_choice.setAccessibleName("Reading width")
        self.width_choice.currentIndexChanged.connect(self._changed)
        form.addRow("Width", self.width_choice)
        size_row = QHBoxLayout()
        self.smaller = QToolButton()
        self.smaller.setText("A−")
        self.smaller.setAccessibleName("Decrease reading text size")
        self.smaller.clicked.connect(lambda: self._size_changed(-10))
        self.size_label = QLabel("100%")
        self.size_label.setAlignment(Qt.AlignCenter)
        self.larger = QToolButton()
        self.larger.setText("A+")
        self.larger.setAccessibleName("Increase reading text size")
        self.larger.clicked.connect(lambda: self._size_changed(10))
        for widget in (self.smaller, self.size_label, self.larger):
            size_row.addWidget(widget)
        form.addRow("Text size", size_row)
        self.spacing_choice = QComboBox()
        for label, value in (("Compact", "compact"), ("Normal", "normal"), ("Relaxed", "relaxed")):
            self.spacing_choice.addItem(label, value)
        self.spacing_choice.setAccessibleName("Reading line spacing")
        self.spacing_choice.currentIndexChanged.connect(self._changed)
        form.addRow("Line spacing", self.spacing_choice)
        layout.addLayout(form)
        self.stripes = QCheckBox("Alternate table row shading")
        self.stripes.toggled.connect(self._changed)
        layout.addWidget(self.stripes)
        reset = QPushButton("Reset reading appearance")
        reset.clicked.connect(lambda: self.optionsChanged.emit({}))
        layout.addWidget(reset)
        self._size = 100

    def set_options(self, options):
        controls = (self.width_choice, self.spacing_choice, self.stripes)
        blocked = [control.blockSignals(True) for control in controls]
        self.width_choice.setCurrentIndex(self.width_choice.findData(options["width"]))
        self.spacing_choice.setCurrentIndex(self.spacing_choice.findData(options["spacing"]))
        self.stripes.setChecked(options["stripe_tables"])
        self._size = options["size"]
        self.size_label.setText(f"{self._size}%")
        self.smaller.setEnabled(self._size > 60)
        self.larger.setEnabled(self._size < 200)
        for control, previous in zip(controls, blocked):
            control.blockSignals(previous)

    def _size_changed(self, delta):
        self._size = max(60, min(200, self._size + delta))
        self._changed()

    def _changed(self, *_args):
        self.optionsChanged.emit({
            "width": self.width_choice.currentData(),
            "size": self._size,
            "spacing": self.spacing_choice.currentData(),
            "stripe_tables": self.stripes.isChecked(),
        })
