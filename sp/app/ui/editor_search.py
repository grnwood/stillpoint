from __future__ import annotations

from PySide6.QtGui import QTextCursor, QTextDocument


class SearchEngine:
    """Search/replace shared by rich-text and plain-text editor surfaces."""

    def __init__(self, editor) -> None:
        self.editor = editor
        self.last_query: str = ""
        self.last_replacement: str = ""
        self.last_forward: bool = True
        self.last_case_sensitive: bool = False

    @staticmethod
    def _normalize_text(text: str) -> str:
        return text.replace("\u2029", "\n")

    def find_next(
        self,
        query: str,
        *,
        backwards: bool = False,
        wrap: bool = True,
        case_sensitive: bool = False,
    ) -> tuple[bool, bool]:
        query = query or self.last_query
        if not query:
            return False, False
        doc = self.editor.document()
        cursor = QTextCursor(self.editor.textCursor())
        flags = QTextDocument.FindFlag(0)
        if backwards:
            flags |= QTextDocument.FindBackward
        if case_sensitive:
            flags |= QTextDocument.FindCaseSensitively
        match = doc.find(query, cursor, flags)
        wrapped = False
        if match.isNull() and wrap:
            restart = QTextCursor(doc.end() if backwards else doc.begin())
            match = doc.find(query, restart, flags)
            wrapped = not match.isNull()
        if match.isNull():
            return False, False
        self.editor.setTextCursor(match)
        self.editor.ensureCursorVisible()
        self.last_query = query
        self.last_forward = not backwards
        self.last_case_sensitive = case_sensitive
        return True, wrapped

    def repeat_last(self, reverse: bool = False) -> tuple[bool, bool, bool]:
        if not self.last_query:
            return False, False, False
        backwards = not self.last_forward
        if reverse:
            backwards = not backwards
        found, wrapped = self.find_next(
            self.last_query,
            backwards=backwards,
            wrap=True,
            case_sensitive=self.last_case_sensitive,
        )
        return found, wrapped, backwards

    def replace_current(self, replacement: str) -> bool:
        if self.editor.isReadOnly():
            return False
        cursor = self.editor.textCursor()
        if not cursor.hasSelection():
            return False
        selected = self._normalize_text(cursor.selectedText())
        if self.last_query:
            matches = selected == self.last_query if self.last_case_sensitive else selected.casefold() == self.last_query.casefold()
            if not matches:
                return False
        cursor.beginEditBlock()
        cursor.insertText(replacement)
        cursor.endEditBlock()
        self.last_replacement = replacement
        return True

    def replace_all(self, query: str, replacement: str, *, case_sensitive: bool = False) -> int:
        if self.editor.isReadOnly():
            return 0
        query = query or self.last_query
        if not query:
            return 0
        doc = self.editor.document()
        edit_cursor = QTextCursor(doc)
        cursor = QTextCursor(doc)
        original_cursor = QTextCursor(self.editor.textCursor())
        flags = QTextDocument.FindFlag(0)
        if case_sensitive:
            flags |= QTextDocument.FindCaseSensitively
        count = 0
        edit_cursor.beginEditBlock()
        while True:
            match = doc.find(query, cursor, flags)
            if match.isNull():
                break
            match.insertText(replacement)
            # Continue searching after the replacement to avoid re-visiting the same span
            cursor = QTextCursor(match)
            cursor.clearSelection()
            count += 1
        edit_cursor.endEditBlock()
        if count:
            self.last_query = query
            self.last_replacement = replacement
            self.last_forward = True
            self.last_case_sensitive = case_sensitive
            try:
                self.editor.setTextCursor(original_cursor)
            except Exception:
                pass
        return count


