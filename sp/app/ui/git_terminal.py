"""Render Git's streamed ANSI colors in a selectable Qt text document."""
from __future__ import annotations

import re

from PySide6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor


class GitTerminalWriter:
    COLORS = (
        "#282c34", "#e06c75", "#98c379", "#e5c07b",
        "#61afef", "#c678dd", "#56b6c2", "#dbe4ed",
        "#7f848e", "#ff8088", "#b3e58b", "#ffe099",
        "#82c4ff", "#e2a0ff", "#80e5ed", "#ffffff",
    )
    CSI = re.compile(r"\x1b\[([0-?]*[ -/]*)([@-~])")

    def __init__(self, output):
        self.output = output
        self.pending = ""
        self.format = self._default_format()

    @staticmethod
    def _default_format():
        fmt = QTextCharFormat()
        fmt.setForeground(QColor("#dbe4ed"))
        return fmt

    @classmethod
    def _color(cls, index):
        if index < 16:
            return QColor(cls.COLORS[index])
        if index < 232:
            index -= 16
            levels = (0, 95, 135, 175, 215, 255)
            return QColor(levels[index // 36], levels[(index // 6) % 6], levels[index % 6])
        level = 8 + (index - 232) * 10
        return QColor(level, level, level)

    def _style(self, parameters):
        try:
            codes = [int(value or 0) for value in parameters.split(";")]
        except ValueError:
            return
        index = 0
        while index < len(codes):
            code = codes[index]
            if code == 0:
                self.format = self._default_format()
            elif code in (1, 22):
                self.format.setFontWeight(QFont.Bold if code == 1 else QFont.Normal)
            elif code in (3, 23):
                self.format.setFontItalic(code == 3)
            elif code in (4, 24):
                self.format.setFontUnderline(code == 4)
            elif 30 <= code <= 37 or 90 <= code <= 97:
                self.format.setForeground(self._color(code - 30 if code < 90 else code - 90 + 8))
            elif 40 <= code <= 47 or 100 <= code <= 107:
                self.format.setBackground(self._color(code - 40 if code < 100 else code - 100 + 8))
            elif code == 39:
                self.format.setForeground(QColor("#dbe4ed"))
            elif code == 49:
                self.format.clearBackground()
            elif code in (38, 48) and index + 2 < len(codes):
                color = None
                if codes[index + 1] == 5 and 0 <= codes[index + 2] <= 255:
                    color = self._color(codes[index + 2])
                    index += 2
                elif codes[index + 1] == 2 and index + 4 < len(codes):
                    color = QColor(*(max(0, min(255, value)) for value in codes[index + 2:index + 5]))
                    index += 4
                if color is not None:
                    if code == 38:
                        self.format.setForeground(color)
                    else:
                        self.format.setBackground(color)
            index += 1

    def append(self, text):
        text = self.pending + text
        self.pending = ""
        cursor = self.output.textCursor()
        cursor.movePosition(QTextCursor.End)
        plain = []
        position = 0
        while position < len(text):
            escape = text.find("\x1b", position)
            end = len(text) if escape < 0 else escape
            if end > position:
                chunk = text[position:end]
                cursor.insertText(chunk, self.format)
                plain.append(chunk)
            if escape < 0:
                break
            sequence = self.CSI.match(text, escape)
            if sequence:
                if sequence.group(2) == "m":
                    self._style(sequence.group(1))
                position = sequence.end()
            elif text[escape:] == "\x1b" or text.startswith("\x1b[", escape):
                self.pending = text[escape:]
                break
            else:
                position = escape + 2
        self.output.setTextCursor(cursor)
        self.output.ensureCursorVisible()
        return "".join(plain)
