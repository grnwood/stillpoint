"""In-app reference for standard shortcuts and vi navigation bindings."""
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QDialog, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from .keyboard_shortcuts import history_cycle_sequences, vi_navigation_sequences
from .recording_hotkeys import BINDINGS, START_BINDINGS
from .screen_capture import ACTION_KEYS, TOOL_KEYS


# Each row is an action, its standard shortcut, and its vi navigation binding.
# Separate alternatives with " / " so Qt can format each chord for the platform.
_GROUPS = {
    "Pages and vaults": (
        ("Save page", "Ctrl+S", ""),
        ("Save all pages", "Ctrl+Shift+S", ""),
        ("New page", "Ctrl+N", ""),
        ("Jump to page", "Ctrl+J", ""),
        ("Open vault", "Ctrl+O", ""),
        ("Open vault in new window", "Ctrl+Shift+O", ""),
        ("Quick vault picker", "Ctrl+Alt+V", "v"),
        ("Bookmark picker", "Ctrl+Alt+J", "f"),
        ("Add bookmark", "Ctrl+Alt+B", ""),
        ("Rename page", "F2", ""),
        ("Reload page", "Ctrl+R", ""),
        ("Home page", "Alt+Home", ""),
        ("Today's journal", "Alt+D", ""),
        ("Jump to journal date", "Ctrl+Alt+D", ""),
        ("Move selection or page", "Ctrl+Shift+M", "m"),
    ),
    "Navigation and search": (
        ("Back / forward", "Alt+Left / Alt+Right" + (" / Meta+[ / Meta+]" if sys.platform == "darwin" else ""), "Alt+h / Alt+l"),
        ("Parent / child", "Alt+Up / Alt+Down", "Alt+k / Alt+j"),
        ("Next / previous recent page", " / ".join(history_cycle_sequences()), ""),
        ("File tree selection up / down", "Alt+PgUp / Alt+PgDown", "k / j"),
        ("File tree collapse / expand", "Left / Right", "h / l"),
        ("Collapse entire file tree", "\\", "\\"),
        ("Open item / keep panel focus", "Enter / Shift+Enter", ""),
        ("Heading picker", "Ctrl+Alt+T", "t"),
        ("Find in page", "Ctrl+F", "/ / ?"),
        ("Repeat find", "", "n"),
        ("Find word forward / backward", "", "* / #"),
        ("Replace in page", "Ctrl+H", ""),
        ("Search vault", "Ctrl+Shift+F", ""),
    ),
    "Editing": (
        ("Undo / redo", "Ctrl+Z / Ctrl+Y", "u / y"),
        ("Cut / copy / paste", "Ctrl+X / Ctrl+C / Ctrl+V", "x / c / p"),
        ("Select all", "Ctrl+A", ""),
        ("Delete selection or line", "", "d"),
        ("Repeat last edit", "", "."),
        ("Insert link", "Ctrl+L", ""),
        ("Copy page link or heading", "Ctrl+Shift+L", "Ctrl+Shift+L"),
        ("Insert date", "Ctrl+D", ""),
        ("Insert horizontal rule", "", "-"),
        ("List or quote selected text", "", "- / * / >"),
    ),
    "Vi editor movement": (
        ("Return to navigation mode", "", "Esc"),
        ("Insert before / after cursor", "", "i / a"),
        ("Open line below / above", "", "o / O"),
        ("Move left / down / up / right", "Left / Down / Up / Right", "h / j / k / l"),
        ("Select left / down / up / right", "Shift+Left / Shift+Down / Shift+Up / Shift+Right", "H / N / U / L"),
        ("Start / first nonblank / end of line", "Home / End", "0 or q / ^ / $ or ;"),
        ("Select to end of line", "Shift+End", ":"),
        ("Next / previous word", "Ctrl+Right / Ctrl+Left", "w / b"),
        ("Page down / up", "PgDown / PgUp", " / ".join(QKeySequence(chord).toString(QKeySequence.NativeText) for chord in vi_navigation_sequences())),
        ("Top / bottom of document", "Ctrl+Home / Ctrl+End", "g / G"),
    ),
    "Tasks and calendar": (
        ("Toggle task completion", "F12", "Space"),
        ("Edit task", "Ctrl+Alt+E", "e"),
        ("Remove task markers", "Ctrl+Alt+R", "r"),
        ("Focus task search", "Ctrl+\\", ""),
        ("Move between dates", "Left / Down / Up / Right", "h / j / k / l"),
        ("Extend date range", "Shift+Arrow", "Shift+h/j/k/l"),
        ("Select today", "", "t"),
        ("Focus headings or subpages", "", "/"),
        ("Return focus to calendar grid", "", "Esc"),
    ),
    "Panels and display": (
        ("Command bar", "Ctrl+Shift+P / Alt+G" + (" / Meta+Shift+P" if sys.platform == "darwin" else ""), ""),
        ("Preferences", "Ctrl+.", ""),
        ("Cycle panel focus", "Ctrl+Shift+Space", ""),
        ("Toggle left / right panel", "Ctrl+Shift+B / Ctrl+Shift+N", ""),
        ("Vault / tags / search tabs", "Ctrl+1 / Ctrl+2 / Ctrl+3", ""),
        ("Tag chip navigation", "Shift+Tab / Tab", "h or k / j or l"),
        ("Toggle tag filter", "Enter / Space", ""),
        ("Fullscreen", "F11", ""),
        ("Focus mode", "Ctrl+Alt+F", ""),
        ("Audience mode", "Ctrl+Alt+A", ""),
        ("Zoom in / out", "Ctrl++ / Ctrl+-", ""),
    ),
    "Task editor and review": (
        ("Task editor: save and open next task", "Ctrl+Enter", ""),
        ("Task editor: focus destination", "Ctrl+L", ""),
        ("Git diff: next / previous change", "Ctrl+J / Ctrl+K / Shift+J / Shift+K", ""),
        ("Quick Capture review: move", "M", ""),
        ("Quick Capture review: undo", "Ctrl+Z", ""),
    ),
    "Screenshot annotation": (
        *((action, key, "") for action, key in TOOL_KEYS.items()),
        *((action, key, "") for action, key in ACTION_KEYS.items()),
        ("Copy / paste", "Ctrl+C / Ctrl+V", ""),
        ("Save attachment", "Ctrl+S", ""),
    ),
    "Map": (
        ("Open node / keep map focus", "Ctrl+Enter / Shift+Enter", ""),
        ("Zoom in / out", "Alt+Up / Alt+Down / Alt+J / Alt+K", ""),
        ("Fit visible map", "F", ""),
        ("Filter subtree / clear filter", "Alt+[ / Alt+]", ""),
        ("Rename node", "Alt+Enter", ""),
        ("Toggle node note", "Ctrl+Space", ""),
        ("Fold or unfold node", "Space", ""),
        ("Collapse map to root", "Esc", ""),
        ("Scroll node note by line", "Up / Down", "k / j"),
        ("Scroll node note by page", "", " / ".join(QKeySequence(chord).toString(QKeySequence.NativeText) for chord in reversed(vi_navigation_sequences()))),
        ("Close node note", "Left / Right", "h / l"),
    ),
}


def _native_keys(keys):
    return " / ".join(
        QKeySequence(key).toString(QKeySequence.NativeText) or key
        if key not in ("Shift+Arrow", "\\") else key
        for key in keys.split(" / ")
    )


def _menu_shortcuts(window):
    """Include the current bindings of menu actions, including user settings."""
    rows = []
    seen = set()
    for action in window.menuBar().findChildren(QAction):
        label = action.text().replace("&", "").strip()
        keys = " / ".join(key.toString(QKeySequence.NativeText) for key in action.shortcuts() if not key.isEmpty())
        if label and keys and (label, keys) not in seen:
            seen.add((label, keys))
            rows.append((label, keys, ""))
    return rows


class KeyboardShortcutsDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Keyboard Shortcuts")
        self.setWindowModality(Qt.WindowModal)
        self.setAttribute(Qt.WA_DeleteOnClose)

        screen = parent.screen().availableGeometry()
        width = round(screen.width() * 0.8)
        height = round(screen.height() * 0.8)
        self.resize(width, height)
        self.move(screen.center() - self.rect().center())

        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        title = QLabel("Keyboard Shortcuts")
        font = title.font()
        font.setPointSizeF(font.pointSizeF() + 5)
        font.setBold(True)
        title.setFont(font)
        header.addWidget(title, 1)
        close = QPushButton("Close (Esc)")
        close.setAutoDefault(False)
        close.clicked.connect(self.reject)
        header.addWidget(close)
        layout.addLayout(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content = QWidget()
        columns = QHBoxLayout(content)
        columns.setSpacing(18)
        banks = [QVBoxLayout() for _ in range(max(1, min(3, width // 510)))]
        for bank in banks:
            columns.addLayout(bank, 1)

        groups = {
            name: [(action, _native_keys(standard), vi) for action, standard, vi in rows]
            for name, rows in _GROUPS.items()
        }
        modifier = "Control" if sys.platform == "darwin" else "Ctrl"
        groups["Screen recording · global"] = [
            (label if name != "start" else "Start screen recording", f"{modifier}+Alt+Shift+{key}", "")
            for name, key, label in (*START_BINDINGS, *BINDINGS)
        ]
        menu_rows = _menu_shortcuts(parent)
        if menu_rows:
            groups["Menu actions · current bindings"] = menu_rows

        heights = [0] * len(banks)
        for name, rows in groups.items():
            index = heights.index(min(heights))
            banks[index].addWidget(self._section(name, rows))
            heights[index] += len(rows) + 3
        for bank in banks:
            bank.addStretch()

        scroll.setWidget(content)
        layout.addWidget(scroll)

    @staticmethod
    def _section(name, rows):
        section = QWidget()
        grid = QGridLayout(section)
        grid.setContentsMargins(2, 4, 2, 4)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(2)
        grid.setColumnStretch(0, 4)
        grid.setColumnStretch(1, 3)
        grid.setColumnStretch(2, 2)

        heading = QLabel(name)
        font = heading.font()
        font.setBold(True)
        heading.setFont(font)
        grid.addWidget(heading, 0, 0, 1, 3)
        for row, values in enumerate((("Action", "Standard", "Vi"), *rows), 1):
            for column, value in enumerate(values):
                label = QLabel(value or "—")
                label.setTextFormat(Qt.PlainText)
                label.setWordWrap(True)
                label.setTextInteractionFlags(Qt.TextSelectableByMouse)
                if row == 1:
                    label.setFont(font)
                grid.addWidget(label, row, column, Qt.AlignTop)
        return section
