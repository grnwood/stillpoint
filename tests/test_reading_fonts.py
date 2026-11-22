import pytest
from PySide6.QtGui import QFont, QFontDatabase, QFontInfo, QFontMetricsF
from PySide6.QtTest import QTest

from sp.app import config
from sp.app.ui.markdown_preview import MarkdownReadingView


@pytest.mark.parametrize('size', [100, 160])
@pytest.mark.parametrize('theme', [False, True])
def test_reading_inline_and_fenced_code_use_real_fixed_width_fonts(qtbot, monkeypatch, size, theme):
    # A non-empty generic/system font name is not proof of fixed pitch. This
    # reproduces platforms where Qt's "monospace" alias resolves to a UI font.
    monkeypatch.setattr(QFontDatabase, 'systemFont', lambda role: QFont('Arial'))
    monkeypatch.setattr(config, 'load_reading_view_use_theme_colors', lambda: theme)
    view = MarkdownReadingView()
    qtbot.addWidget(view)
    view.resize(900, 500)
    view.show()
    view._apply_reading_options(config.normalize_reading_options({'size': size}))
    markdown = 'Normal prose with `iiii    WWWW`.\n\n```text\niiii    WWWW\n    indented\n```'
    view.set_markdown(markdown)
    QTest.qWait(30)
    assert QFontDatabase.isFixedPitch(view._code_font_family)
    assert view._code_font_family != 'Arial'
    assert view.browser.toPlainText().count('iiii    WWWW') == 2
    assert '    indented' in view.browser.toPlainText()
    cursor = view.browser.document().find('iiii')
    count = 0
    while not cursor.isNull():
        font = cursor.charFormat().font()
        assert QFontInfo(font).fixedPitch()
        metrics = QFontMetricsF(font)
        assert metrics.horizontalAdvance('iiii') == pytest.approx(metrics.horizontalAdvance('WWWW'))
        assert font.pointSizeF() == pytest.approx(12 * size / 100)
        count += 1
        cursor = view.browser.document().find('iiii', cursor)
    assert count == 2
    assert view.last_markdown == markdown
    # The source-copy controls still provide the original indentation/fences.
    button = next(button for button, _, kind in view._copy_buttons if kind == 'code')
    button.click()
    from PySide6.QtWidgets import QApplication
    assert QApplication.clipboard().text() == '```text\niiii    WWWW\n    indented\n```'


def test_reading_monospace_survives_appearance_reflow(qtbot):
    view = MarkdownReadingView()
    qtbot.addWidget(view)
    view.set_markdown('Prose `iiii WWWW`\n\n```\niiii WWWW\n```')
    family = view._code_font_family
    for options in ({'width': 'narrow', 'size': 140}, {'width': 'full', 'spacing': 'relaxed'}):
        view._apply_reading_options(config.normalize_reading_options(options))
        cursor = view.browser.document().find('iiii')
        while not cursor.isNull():
            assert QFontInfo(cursor.charFormat().font()).fixedPitch()
            cursor = view.browser.document().find('iiii', cursor)
        assert view._code_font_family == family
