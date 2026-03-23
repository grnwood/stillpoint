from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QMenu, QWidget

from sp.app.ui.markdown_editor import MarkdownEditor
from sp.app.ui.plantuml_editor_window import ViPlainTextEdit
from sp.app.ui.theme import apply_menu_theme


def _sample_palette() -> QPalette:
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#fbfbfb"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#151515"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#151515"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#2f6fed"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.Mid, QColor("#c4c8d0"))
    palette.setColor(QPalette.ColorRole.Midlight, QColor("#daddE3"))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor("#7c7c7c"))
    return palette


def test_apply_menu_theme_uses_palette_colors(qtbot) -> None:
    source = QWidget()
    qtbot.addWidget(source)
    source.setPalette(_sample_palette())

    menu = QMenu(source)
    apply_menu_theme(menu, source)

    style = menu.styleSheet().lower()
    assert "qmenu::item:selected" in style
    assert "#fbfbfb" in style
    assert "#151515" in style
    assert "#2f6fed" in style
    assert menu.palette().color(QPalette.ColorRole.Window).name().lower() == "#fbfbfb"


def test_markdown_editor_standard_context_menu_is_themed(qtbot) -> None:
    editor = MarkdownEditor()
    qtbot.addWidget(editor)
    editor.setPalette(_sample_palette())

    menu = editor.createStandardContextMenu()

    style = menu.styleSheet().lower()
    assert "qmenu::item:selected" in style
    assert "#fbfbfb" in style
    assert "#151515" in style


def test_vi_plain_text_editor_standard_context_menu_is_themed(qtbot) -> None:
    editor = ViPlainTextEdit()
    qtbot.addWidget(editor)
    editor.setPalette(_sample_palette())

    menu = editor.createStandardContextMenu()

    style = menu.styleSheet().lower()
    assert "qmenu::item:selected" in style
    assert "#fbfbfb" in style
    assert "#151515" in style