import re
from PySide6.QtCore import QRegularExpression

def test_file_markdown_link_pattern_allows_spaces():
    # Import the pattern from the editor module
    from sp.app.ui.markdown_editor import FILE_MARKDOWN_LINK_PATTERN
    text = "[My File Final Report.txt](./My File Final Report.txt)"
    match = FILE_MARKDOWN_LINK_PATTERN.match(text)
    assert match is not None, "Pattern should match filenames with spaces"
    captured = match.captured("file")
    assert captured == "./My File Final Report.txt"


def test_file_markdown_link_pattern_simple():
    from sp.app.ui.markdown_editor import FILE_MARKDOWN_LINK_PATTERN
    text = "[Report.pdf](./Report.pdf)"
    match = FILE_MARKDOWN_LINK_PATTERN.match(text)
    assert match is not None
    assert match.captured("file") == "./Report.pdf"


def test_file_markdown_link_pattern_no_leading_dot():
    from sp.app.ui.markdown_editor import FILE_MARKDOWN_LINK_PATTERN
    text = "[Image.png](Image.png)"
    match = FILE_MARKDOWN_LINK_PATTERN.match(text)
    assert match is not None
    assert match.captured("file") == "Image.png"


def test_file_markdown_link_pattern_excludes_newline():
    from sp.app.ui.markdown_editor import FILE_MARKDOWN_LINK_PATTERN
    text = "[Bad Link](./Bad\nLink.txt)"  # newline should prevent match
    match = FILE_MARKDOWN_LINK_PATTERN.match(text)
    assert not match.hasMatch(), "Pattern should not span newlines"
