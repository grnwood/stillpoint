from __future__ import annotations

from sp.app.ui.markdown_editor import MarkdownEditor
from sp.webserver.server import WebServer


def test_editor_renders_paren_checkbox_shortcut_as_task_symbol(qtbot) -> None:
    editor = MarkdownEditor()
    qtbot.addWidget(editor)

    display = editor._to_display("() follow up\n")
    assert display == "☐ follow up\n"

    is_task, indent, state, content = editor._is_task_line("() follow up")
    assert is_task is True
    assert indent == ""
    assert state == " "
    assert content == "follow up"



def test_webserver_renders_paren_checkbox_shortcut() -> None:
    rendered = WebServer._rewrite_task_and_dash_markers("() follow up")
    assert 'md-checkbox md-checkbox--unchecked' in rendered
    assert rendered.endswith("follow up")
