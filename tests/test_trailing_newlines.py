import pytest

from sp.app.ui.markdown_editor import MarkdownEditor


@pytest.fixture
def editor(qapp):
    ed = MarkdownEditor()
    yield ed
    ed.close()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", ""),
        ("hello", "hello\n"),
        ("hello\n", "hello\n"),
        ("hello\n\n", "hello\n\n"),
        ("hello\n\n\n", "hello\n\n\n"),
        ("hello\n\n\n\n", "hello\n\n\n"),  # capped to 3
        ("hello\n\n\n\n\n", "hello\n\n\n"),  # capped to 3
    ],
)
def test_doc_to_markdown_trailing_newlines_normalized(editor, text, expected):
    editor.setPlainText(text)
    assert editor._doc_to_markdown() == expected


def test_doc_to_markdown_does_not_append_on_repeat(editor):
    editor.setPlainText("hello\n")
    first = editor._doc_to_markdown()
    second = editor._doc_to_markdown()
    assert first == "hello\n"
    assert second == "hello\n"
