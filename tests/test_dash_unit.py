import pytest
from PySide6.QtWidgets import QApplication

from sp.app.ui.markdown_editor import MarkdownEditor


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def editor(app):
    ed = MarkdownEditor()
    yield ed
    ed.close()


@pytest.mark.parametrize(
    ("input_text", "expected"),
    [
        ("- simple dash", (False, "", "")),
        ("  - indented dash", (False, "", "")),
        ("+ simple plus", (False, "", "")),
        ("* simple asterisk", (True, "", "simple asterisk")),
        ("  * indented asterisk", (True, "  ", "indented asterisk")),
        ("• simple bullet", (True, "", "simple bullet")),
        ("  • indented bullet", (True, "  ", "indented bullet")),
        ("*bold text*", (False, "", "")),
        ("-hyphenated-word", (False, "", "")),
    ],
)
def test_is_bullet_line(editor, input_text, expected):
    assert editor._is_bullet_line(input_text) == expected
