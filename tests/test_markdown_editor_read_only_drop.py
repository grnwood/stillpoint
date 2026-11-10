import pytest
from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent

from sp.app.ui.markdown_editor import MarkdownEditor


@pytest.mark.parametrize("payload", ["page", "file", "text"])
@pytest.mark.parametrize("read_only_api", ["set_read_only_mode", "setReadOnly"])
def test_read_only_editor_rejects_drop_and_paste(qapp, tmp_path, payload, read_only_api):
    editor = MarkdownEditor()
    editor.setPlainText("Original page")
    editor.document().setModified(False)
    getattr(editor, read_only_api)(True)
    mime = QMimeData()
    if payload == "page":
        mime.setData("application/x-stillpoint-path", b"/Other/Other.md")
        mime.setData("application/x-stillpoint-label", b"Other page")
    elif payload == "file":
        attachment = tmp_path / "attachment.txt"
        attachment.write_text("attachment")
        mime.setUrls([QUrl.fromLocalFile(str(attachment))])
    else:
        mime.setText("Inserted text")
    attachments = []
    editor.attachmentDropped.connect(attachments.append)
    try:
        for event_type, handler, position in [
            (QDragEnterEvent, editor.dragEnterEvent, QPoint(5, 5)),
            (QDragMoveEvent, editor.dragMoveEvent, QPoint(5, 5)),
            (QDropEvent, editor.dropEvent, QPointF(5, 5)),
        ]:
            event = event_type(position, Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
            event.acceptProposedAction()
            handler(event)
            assert not event.isAccepted()
        editor.insertFromMimeData(mime)
        assert editor.toPlainText() == "Original page"
        assert not editor.document().isModified()
        assert attachments == []
    finally:
        editor.close()


def test_writable_editor_still_accepts_page_drop(qapp):
    editor = MarkdownEditor()
    mime = QMimeData()
    mime.setData("application/x-stillpoint-path", b"/Other/Other.md")
    try:
        event = QDropEvent(QPointF(5, 5), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
        editor.dropEvent(event)
        assert event.isAccepted()
        assert "Other" in editor.toPlainText()
    finally:
        editor.close()
