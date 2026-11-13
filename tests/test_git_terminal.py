from PySide6.QtGui import QFont
from PySide6.QtWidgets import QPlainTextEdit

from sp.app.ui.git_terminal import GitTerminalWriter


def test_terminal_renders_streamed_colors_and_returns_plain_output(qtbot):
    output = QPlainTextEdit()
    qtbot.addWidget(output)
    writer = GitTerminalWriter(output)
    plain = writer.append("\x1b[33mcommit abc123\x1b[0m\n\x1b[3")
    plain += writer.append("1mremoved\x1b[0m \x1b[32;1madded\x1b[m normal")

    assert plain == output.toPlainText() == "commit abc123\nremoved added normal"
    document = output.document()
    assert document.find("commit abc123").charFormat().foreground().color().name() == "#e5c07b"
    assert document.find("removed").charFormat().foreground().color().name() == "#e06c75"
    added = document.find("added").charFormat()
    assert added.foreground().color().name() == "#98c379"
    assert added.fontWeight() == QFont.Bold
    normal = document.find("normal").charFormat()
    assert normal.foreground().color().name() == "#dbe4ed"
    assert normal.fontWeight() != QFont.Bold


def test_terminal_supports_git_custom_extended_colors_and_ignores_cursor_codes(qtbot):
    output = QPlainTextEdit()
    qtbot.addWidget(output)
    writer = GitTerminalWriter(output)
    writer.append("\x1b[38;5;196mindexed\x1b[0m \x1b[38;2;12;34;56mrgb\x1b[0m\x1b[K")
    assert output.toPlainText() == "indexed rgb"
    assert output.document().find("indexed").charFormat().foreground().color().name() == "#ff0000"
    assert output.document().find("rgb").charFormat().foreground().color().name() == "#0c2238"
