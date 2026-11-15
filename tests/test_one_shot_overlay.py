from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QTextEdit, QWidget

from sp.app.ui.one_shot_overlay import OneShotPromptOverlay, selection_replacement_handler


def test_selection_prompts_before_sending_and_supports_refinement(qtbot, monkeypatch):
    from sp.app.ui import ai_chat_panel

    workers = []

    class Worker(QObject):
        chunk = Signal(str)
        finished = Signal(str)
        failed = Signal(str)

        def __init__(self, server, messages, model, **kwargs):
            super().__init__(kwargs.get('parent'))
            self.messages = messages
            workers.append(self)

        def start(self):
            pass

        def request_cancel(self):
            pass

    monkeypatch.setattr(ai_chat_panel, 'ApiWorker', Worker)
    parent = QWidget()
    qtbot.addWidget(parent)
    accepted = []
    overlay = OneShotPromptOverlay(parent=parent, server_config={}, model='test',
                                   system_prompt='Edit text', on_accept=accepted.append)
    qtbot.addWidget(overlay)
    original = '  Original **selection**\nwith details.  '
    overlay.open_with_selection(original)
    assert workers == []
    assert overlay.original_view.toPlainText() == original
    assert not overlay.accept_btn.isEnabled()
    overlay._accept_last_message()
    assert accepted == []
    overlay.input.setPlainText('Make it shorter')
    overlay._send_input()
    assert original in workers[0].messages[-2]['content']
    assert workers[0].messages[-1]['content'] == 'Make it shorter'
    assert not overlay.accept_btn.isEnabled()
    workers[0].finished.emit('Short version')
    assert overlay.accept_btn.isEnabled()
    overlay.input.setPlainText('Less formal')
    overlay._send_input()
    assert {'role': 'assistant', 'content': 'Short version'} in workers[1].messages
    workers[1].finished.emit('Friendly version')
    overlay._previous_version()
    assert overlay._last_assistant_text() == 'Short version'
    overlay._next_version()
    assert overlay._last_assistant_text() == 'Friendly version'
    overlay._previous_version()
    overlay.input.setPlainText('Keep the original tone')
    overlay._send_input()
    assert 'Short version' in workers[2].messages[-2]['content']
    workers[2].finished.emit('Final version')
    overlay._previous_version()
    overlay._previous_version()
    overlay._accept_last_message()
    assert accepted == ['Short version']


def test_cancel_leaves_original_selection_unchanged(qtbot):
    editor = QTextEdit()
    qtbot.addWidget(editor)
    editor.setPlainText('Keep this text')
    editor.selectAll()
    overlay = OneShotPromptOverlay(parent=editor, server_config={}, model='test',
                                   system_prompt='',
                                   on_accept=selection_replacement_handler(editor, editor.textCursor()))
    qtbot.addWidget(overlay)
    overlay.open_with_selection(editor.toPlainText())
    overlay._versions = ['Discard this result']
    overlay._review_index = 0
    overlay.reject()
    assert editor.toPlainText() == 'Keep this text'


def test_selection_replacement_tracks_range_and_is_undoable(qtbot):
    editor = QTextEdit()
    qtbot.addWidget(editor)
    editor.setPlainText('Before original after')
    cursor = QTextCursor(editor.document())
    cursor.setPosition(7)
    cursor.setPosition(15, QTextCursor.KeepAnchor)
    replace = selection_replacement_handler(editor, cursor)
    prefix = QTextCursor(editor.document())
    prefix.insertText('Prefix ')
    replace('replacement')
    assert editor.toPlainText() == 'Prefix Before replacement after'
    editor.undo()
    assert editor.toPlainText() == 'Prefix Before original after'


def test_changed_selection_keeps_popup_open_and_document_intact(qtbot):
    editor = QTextEdit()
    qtbot.addWidget(editor)
    editor.setPlainText('original')
    editor.selectAll()
    replace = selection_replacement_handler(editor, editor.textCursor())
    overlay = OneShotPromptOverlay(parent=editor, server_config={}, model='test',
                                   system_prompt='', on_accept=replace)
    qtbot.addWidget(overlay)
    overlay.open_with_selection('original')
    overlay._versions = ['replacement']
    overlay._review_index = 0
    editor.insertPlainText('changed')
    overlay._accept_last_message()
    assert overlay.isVisible()
    assert 'selected text changed' in overlay._status.text()
    assert editor.toPlainText() == 'changed'
    overlay.reject()
    assert editor.toPlainText() == 'changed'


def test_no_selection_still_accepts_a_prompt_and_inserts_result(qtbot, monkeypatch):
    parent = QWidget()
    qtbot.addWidget(parent)
    accepted = []
    overlay = OneShotPromptOverlay(parent=parent, server_config={}, model='test',
                                   system_prompt='', on_accept=accepted.append)
    qtbot.addWidget(overlay)
    sent = []
    monkeypatch.setattr(overlay, '_start_assistant_reply', lambda: sent.append(overlay._api_messages[-1]))
    overlay.input.setPlainText('Write a sentence')
    overlay._send_input()
    assert sent == [{'role': 'user', 'content': 'Write a sentence'}]
    overlay._versions = ['A sentence.']
    overlay._review_index = 0
    overlay._accept_last_message()
    assert accepted == ['A sentence.']
