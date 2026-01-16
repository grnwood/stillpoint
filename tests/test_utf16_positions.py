from sp.app.ui.markdown_editor import IMAGE_PATTERN, _utf16_positions


def test_utf16_positions_non_bmp() -> None:
    text = "a🔧b"
    positions = _utf16_positions(text)
    assert positions == [0, 1, 3, 4]


def test_utf16_positions_image_span_after_emoji() -> None:
    text = "## 🔧 Title\n![a](./img.png)"
    match = IMAGE_PATTERN.search(text)
    assert match is not None
    positions = _utf16_positions(text)
    prefix = text[: match.start()]
    extra = sum(1 for ch in prefix if ord(ch) > 0xFFFF)
    assert positions[match.end()] == match.end() + extra
