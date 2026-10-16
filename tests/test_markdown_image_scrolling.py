from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, QUrl
from PySide6.QtGui import QImage, QTextCursor, QTextDocument, QTextImageFormat
from PySide6.QtTest import QTest

from sp.app.ui.markdown_editor import MarkdownEditor


def _editor_with_tall_image_row(qapp) -> MarkdownEditor:
    editor = MarkdownEditor()
    editor.resize(420, 300)
    editor.show()
    qapp.processEvents()

    document = editor.document()
    document.clear()
    image = QImage(100, 500, QImage.Format_ARGB32)
    image.fill(0xFF778899)
    document.addResource(
        QTextDocument.ResourceType.ImageResource,
        QUrl("test:tall-image"),
        image,
    )

    cursor = QTextCursor(document)
    cursor.insertText("text above")
    cursor.insertBlock()
    image_format = QTextImageFormat()
    image_format.setName("test:tall-image")
    image_format.setWidth(100)
    image_format.setHeight(500)
    # Multiple images on one line exercises the side-by-side layout that used
    # to make cursorRect() look like one enormous text caret.
    for _ in range(3):
        cursor.insertImage(image_format)
    cursor.insertBlock()
    cursor.insertText("text below")

    editor.setTextCursor(QTextCursor(document.findBlockByNumber(0)))
    editor.verticalScrollBar().setValue(0)
    qapp.processEvents()
    return editor


def _finish_scroll_animation(editor: MarkdownEditor) -> int:
    animation = editor._cursor_scroll_animation
    assert animation is not None
    target = int(animation.endValue())
    editor._stop_cursor_scroll_animation()
    editor.verticalScrollBar().setValue(target)
    return target


def _assert_image_row_centered(editor: MarkdownEditor) -> None:
    rect = editor._image_row_cursor_rect(editor.textCursor())
    assert abs(rect.center().y() - editor.viewport().rect().center().y()) <= 2


def _editor_with_three_consecutive_images(qapp) -> tuple[MarkdownEditor, list[int]]:
    editor = MarkdownEditor()
    editor.resize(1100, 800)
    editor.show()
    qapp.processEvents()

    document = editor.document()
    document.clear()
    image = QImage(493, 364, QImage.Format_ARGB32)
    image.fill(0xFF778899)
    document.addResource(
        QTextDocument.ResourceType.ImageResource,
        QUrl("test:493x364-image"),
        image,
    )
    image_format = QTextImageFormat()
    image_format.setName("test:493x364-image")
    image_format.setWidth(493)
    image_format.setHeight(364)

    cursor = QTextCursor(document)
    for line in range(18):
        cursor.insertText(f"leading text {line}")
        cursor.insertBlock()
    image_blocks = []
    for index in range(3):
        image_blocks.append(cursor.blockNumber())
        cursor.insertImage(image_format)
        if index == 1:
            cursor.insertText("kjjjjjjjjjjjjjjjjkk")
        cursor.insertBlock()
    cursor.insertText("end")

    editor._apply_scroll_past_end_margin()
    qapp.processEvents()
    editor.setTextCursor(QTextCursor(document.lastBlock()))
    editor.verticalScrollBar().setValue(editor.verticalScrollBar().maximum())
    qapp.processEvents()
    return editor, image_blocks


def test_arrow_navigation_enters_tall_image_row_without_full_height_jump(qapp) -> None:
    editor = _editor_with_tall_image_row(qapp)

    QTest.keyClick(editor, Qt.Key_Down)

    assert editor.textCursor().blockNumber() == 1
    assert editor.verticalScrollBar().value() == 0
    _finish_scroll_animation(editor)
    _assert_image_row_centered(editor)
    editor.close()


def test_vi_navigation_enters_tall_image_row_without_full_height_jump(qapp) -> None:
    editor = _editor_with_tall_image_row(qapp)

    editor._vi_move_cursor(QTextCursor.Down)

    assert editor.textCursor().blockNumber() == 1
    assert editor.verticalScrollBar().value() == 0
    _finish_scroll_animation(editor)
    _assert_image_row_centered(editor)
    editor.close()


def test_clicking_visible_image_smoothly_centers_full_image_row(qapp) -> None:
    editor = _editor_with_tall_image_row(qapp)

    QTest.mouseClick(
        editor.viewport(),
        Qt.LeftButton,
        pos=QPoint(60, 120),
    )

    assert editor.textCursor().blockNumber() == 1
    assert editor.verticalScrollBar().value() == 0
    _finish_scroll_animation(editor)
    _assert_image_row_centered(editor)
    editor.close()


def test_arrow_navigation_animates_between_image_row_and_following_text(qapp) -> None:
    editor = _editor_with_tall_image_row(qapp)
    editor._move_cursor_vertically(QTextCursor.Down)

    editor._move_cursor_vertically(QTextCursor.Down)

    animation = editor._cursor_scroll_animation
    assert editor.textCursor().blockNumber() == 2
    assert animation is not None
    assert editor.verticalScrollBar().value() == 0
    assert int(animation.endValue()) > 200

    editor._stop_cursor_scroll_animation()
    editor.close()


def test_up_navigation_uses_near_edge_of_tall_image_row(qapp) -> None:
    editor = _editor_with_tall_image_row(qapp)
    scrollbar = editor.verticalScrollBar()
    scrollbar.setValue(scrollbar.maximum())
    editor._cursor_scroll_guard = True
    try:
        editor.setTextCursor(QTextCursor(editor.document().findBlockByNumber(2)))
    finally:
        editor._cursor_scroll_guard = False
    start_value = scrollbar.value()

    editor._move_cursor_vertically(QTextCursor.Up)

    assert editor.textCursor().blockNumber() == 1
    assert scrollbar.value() == start_value
    centered_value = _finish_scroll_animation(editor)
    assert centered_value < start_value
    _assert_image_row_centered(editor)

    editor._move_cursor_vertically(QTextCursor.Up)

    animation = editor._cursor_scroll_animation
    assert editor.textCursor().blockNumber() == 0
    assert animation is not None
    assert scrollbar.value() == centered_value
    assert int(animation.endValue()) == 0
    editor._stop_cursor_scroll_animation()
    editor.close()


def test_three_493x364_image_rows_center_smoothly_in_both_directions(qapp) -> None:
    editor, image_blocks = _editor_with_three_consecutive_images(qapp)

    for expected_block in reversed(image_blocks):
        editor._move_cursor_vertically(QTextCursor.Up)
        assert editor.textCursor().blockNumber() == expected_block
        _finish_scroll_animation(editor)
        _assert_image_row_centered(editor)

    for expected_block in image_blocks[1:]:
        editor._move_cursor_vertically(QTextCursor.Down)
        assert editor.textCursor().blockNumber() == expected_block
        _finish_scroll_animation(editor)
        _assert_image_row_centered(editor)

    editor.close()
