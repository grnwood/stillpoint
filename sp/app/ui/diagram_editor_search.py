"""StillPoint find/replace controls shared by the two diagram windows."""
from PySide6.QtCore import QObject, QEvent, Qt
from PySide6.QtGui import QKeySequence, QShortcut, QTextCursor

from sp.app import config
from .editor_search import SearchEngine
from .find_replace_bar import FindReplaceBar


class DiagramEditorSearch(QObject):
    def __init__(self, window, editor, layout):
        super().__init__(window)
        self.window, self.editor = window, editor
        self.engine = SearchEngine(editor)
        self.bar = FindReplaceBar(window, palette_source=editor)
        layout.addWidget(self.bar)
        window.find_bar = self.bar
        self.bar.findNextRequested.connect(self.find)
        self.bar.replaceRequested.connect(self.replace)
        self.bar.replaceAllRequested.connect(self.replace_all)
        self.bar.closed.connect(lambda: editor.setFocus(Qt.ShortcutFocusReason))
        editor.findBarRequested.connect(self.show)
        editor.searchRepeatRequested.connect(self.repeat)
        editor.searchWordRequested.connect(self.find_word)
        self.shortcuts = []
        self._shortcut(QKeySequence.Find, lambda: self.show(False))
        replace_keys = {sequence.toString(QKeySequence.PortableText): sequence for sequence in (
            [QKeySequence("Ctrl+H")] + QKeySequence.keyBindings(QKeySequence.Replace))}
        for sequence in replace_keys.values():
            self._shortcut(sequence, lambda: self.show(True))
        used = set()
        for standard, extras, backwards in (
            (QKeySequence.FindNext, ["F3"], False),
            (QKeySequence.FindPrevious, ["Shift+F3"], True),
        ):
            sequences = QKeySequence.keyBindings(standard) + [QKeySequence(value) for value in extras]
            for sequence in sequences:
                key = sequence.toString(QKeySequence.PortableText)
                if key and key not in used:
                    used.add(key)
                    self._shortcut(sequence, lambda backwards=backwards: self.find(
                        self.bar.current_query() or self.engine.last_query, backwards, self.bar.case_checkbox.isChecked()))
        window.installEventFilter(self)

    def _shortcut(self, sequence, callback):
        shortcut = QShortcut(QKeySequence(sequence), self.window)
        shortcut.setContext(Qt.WindowShortcut)
        shortcut.activated.connect(callback)
        self.shortcuts.append(shortcut)

    def _status(self, text):
        self.window.statusBar().showMessage(text, 2500)

    def show(self, replace=False, backwards=False, seed=""):
        self.editor._pending_d = self.editor._pending_y = self.editor._pending_g = False
        selected = self.editor.textCursor().selectedText().replace("\u2029", "\n")
        query = seed or selected or self.engine.last_query
        self.bar.show_bar(replace=replace, query=query, backwards=backwards)

    def find(self, query, backwards=False, case_sensitive=False):
        query = query or self.engine.last_query
        if not query:
            self._status("Enter text to find.")
            self.bar.focus_query()
            return
        self.bar.query_edit.setText(query)
        found, wrapped = self.engine.find_next(query, backwards=backwards, case_sensitive=case_sensitive)
        if not found:
            self._status("No match found.")
        elif wrapped:
            self._status(f"Wrapped to {'end' if backwards else 'beginning'} of document.")
        else:
            self.window.statusBar().clearMessage()

    def replace(self, replacement):
        if not self.engine.last_query:
            self._status("Find text before replacing.")
        elif not self.engine.replace_current(replacement):
            self._status("No current match to replace.")

    def replace_all(self, query, replacement, case_sensitive):
        if not (query or self.engine.last_query):
            self._status("Enter text to find.")
            self.bar.focus_query()
            return
        count = self.engine.replace_all(query, replacement, case_sensitive=case_sensitive)
        self._status(f"Replaced {count} {'occurrence' if count == 1 else 'occurrences'}.")

    def repeat(self, reverse=False):
        if not self.engine.last_query:
            self._status("No previous search.")
            return
        backwards = not self.engine.last_forward
        self.find(self.engine.last_query, not backwards if reverse else backwards, self.engine.last_case_sensitive)

    def find_word(self, backwards=False):
        cursor = QTextCursor(self.editor.textCursor())
        cursor.select(QTextCursor.WordUnderCursor)
        word = cursor.selectedText()
        if word:
            self.find(word, backwards)
        else:
            self._status("No word under cursor.")

    def eventFilter(self, watched, event):
        if watched is self.window and event.type() == QEvent.WindowActivate:
            enabled = config.load_vi_mode_enabled()
            if enabled != self.editor._vi_feature_enabled:
                self.editor.set_vi_mode_enabled(enabled)
            style = config.load_vi_cursor_style()
            if style != self.editor._vi_cursor_style:
                self.editor.set_vi_cursor_style(style)
            self.window._vi_enabled = enabled
            if hasattr(self.window, "_update_vi_badge_visibility"):
                self.window._update_vi_badge_visibility()
        return super().eventFilter(watched, event)
