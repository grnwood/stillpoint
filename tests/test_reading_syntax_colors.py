import pytest
from PySide6.QtGui import QColor, QFontInfo

from sp.app.ui.markdown_editor import MarkdownEditor
from sp.app.ui.markdown_preview import MarkdownReadingView


SOURCE = '''# **TitleOne**

## *TitleTwo*

Normal **BoldWord** and *ItalicWord* and ***BothWord*** and `iiii WWWW`.

| Item | Status |
| --- | --- |
| **TableBold** | *TableItalic* |

```text
FixedBlock
```
'''


@pytest.mark.parametrize('folder', [False, True])
@pytest.mark.parametrize('background', ['white', 'theme', 'dusk'])
def test_reader_keeps_editor_heading_and_inline_colors(qtbot, tmp_path, folder, background):
    if folder:
        from sp.app.folder_navigator.core import read_text
        from sp.app.folder_navigator.window import Tab
        path = tmp_path / 'colors.md'
        path.write_text(SOURCE)
        tab = Tab(path, read_text(path), markdown=True, root=tmp_path)
        qtbot.addWidget(tab)
        editor, reader = tab.editor, tab.reading_view
    else:
        editor = MarkdownEditor()
        reader = MarkdownReadingView()
        qtbot.addWidget(editor)
        qtbot.addWidget(reader)
        editor.set_markdown(SOURCE)
    highlighter = editor.highlighter
    for name, color in (
        ('bold_format', '#a13a11'), ('italic_format', '#a22676'),
        ('bold_italic_format', '#813ab1'), ('code_format', '#227a39'),
        ('code_block', '#336a5a'),
    ):
        getattr(highlighter, name).setForeground(QColor(color))
    highlighter.code_format.setBackground(QColor('#d3f2dd'))
    highlighter.code_block.setBackground(QColor('#d1e7e0'))
    highlighter.heading_styles[0].setForeground(QColor('#b03131'))
    highlighter.heading_styles[1].setForeground(QColor('#2755a8'))
    reader.match_editor_layout(editor)
    reader._apply_background(background)
    reader.set_markdown(editor.to_markdown())

    def fmt(word):
        cursor = reader.browser.document().find(word)
        assert not cursor.isNull(), word
        return cursor.charFormat()

    for word, color in (
        ('TitleOne', '#b03131'), ('TitleTwo', '#2755a8'),
        ('BoldWord', '#a13a11'), ('ItalicWord', '#a22676'),
        ('BothWord', '#813ab1'), ('iiii WWWW', '#227a39'),
        ('TableBold', '#a13a11'), ('TableItalic', '#a22676'),
        ('FixedBlock', '#336a5a'),
    ):
        assert fmt(word).foreground().color().name() == color, word
    assert fmt('BoldWord').fontWeight() >= 600
    assert fmt('ItalicWord').fontItalic()
    assert fmt('BothWord').fontItalic() and fmt('BothWord').fontWeight() >= 600
    assert QFontInfo(fmt('iiii WWWW').font()).fixedPitch()
    assert QFontInfo(fmt('FixedBlock').font()).fixedPitch()
    assert fmt('iiii WWWW').background().color().name() == '#d3f2dd'
    assert fmt('Normal').foreground().color() == reader._text_color
    assert reader.last_markdown.strip() == editor.to_markdown().strip()
    # Reflow and background changes must retain the captured syntax palette.
    reader._apply_reading_options({**reader.reading_options, 'size': 140, 'spacing': 'relaxed'})
    assert fmt('TitleOne').foreground().color().name() == '#b03131'
    assert fmt('BoldWord').foreground().color().name() == '#a13a11'
