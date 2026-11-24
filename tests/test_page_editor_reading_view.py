import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit

from sp.app import config
from sp.app.ui.markdown_preview import MarkdownReadingView
from sp.app.ui.page_editor_window import PageEditorWindow


@pytest.fixture
def detached(qtbot, monkeypatch, tmp_path):
    state = {}
    content = {"text": "# Detached page\n\nOriginal body\n"}
    windows = []
    monkeypatch.setattr(config, "_read_global_config", lambda: state)
    monkeypatch.setattr(config, "_update_global_config", state.update)
    monkeypatch.setattr(config, "load_popup_editor_geometry", lambda: None)
    monkeypatch.setattr(config, "save_popup_editor_geometry", lambda value: None)
    monkeypatch.setattr(config, "load_popup_font_size", lambda default: 14)
    monkeypatch.setattr(PageEditorWindow, "_refresh_saved_state", lambda self: None)
    monkeypatch.setattr(PageEditorWindow, "_save_current_file", lambda self, **kwargs: None)

    def load(window):
        window.editor.set_markdown(content["text"])
        window._remember_saved_state(content["text"])

    monkeypatch.setattr(PageEditorWindow, "_load_content", load)

    def create(*, read_only=False):
        window = PageEditorWindow(
            api_base="http://127.0.0.1:1", vault_root=str(tmp_path), page_path="/Page/Page.md",
            read_only=read_only, open_in_main_callback=lambda *args, **kwargs: None,
        )
        window.resize(850, 550)
        qtbot.addWidget(window)
        windows.append(window)
        return window

    yield create, state, content
    for window in windows:
        window._read_only = True


@pytest.mark.parametrize("modifier", [Qt.ControlModifier, Qt.MetaModifier])
def test_toolbar_toggle_renders_unsaved_buffer_and_returns_to_editing(detached, qapp, modifier):
    create, state, content = detached
    window = create()
    window.editor.moveCursor(QTextCursor.End)
    window.editor.insertPlainText("Unsaved words")
    text = window.editor.to_markdown()
    dirty = window._is_dirty()
    window.activateWindow()
    window.editor.setFocus()
    qapp.processEvents()
    QTest.keyClick(window.editor, Qt.Key_Space, modifier)
    qapp.processEvents()
    assert window._reading_action.isChecked()
    assert window._editor_stack.currentWidget() is window.reading_view
    assert "Unsaved words" in window.reading_view.browser.toPlainText()
    assert window._reading_theme_action.isVisible()
    assert window._reading_options_action.isVisible()
    assert state["page_editor_reading_view"] is True
    assert window.editor.to_markdown() == text
    assert window._is_dirty() == dirty
    QTest.keyClick(window.reading_view.browser, Qt.Key_Space, modifier)
    qapp.processEvents()
    assert window._editor_stack.currentWidget() is window.editor
    assert not window._reading_options_action.isVisible()
    assert window.editor.to_markdown() == text
    assert state["page_editor_reading_view"] is False


def test_reading_settings_survive_reload_and_reopen_and_are_isolated(detached, qtbot):
    create, state, content = detached
    window = create()
    others = [MarkdownReadingView(preference_scope=scope) for scope in (None, "focus", "audience")]
    for view in others:
        qtbot.addWidget(view)
    original = [(view._reading_background, dict(view.reading_options)) for view in others]
    window._reading_action.trigger()
    window.reading_view._background_actions["paper"].trigger()
    options = {"width": "wide", "size": 150, "spacing": "relaxed", "stripe_tables": False}
    window.reading_view.set_reading_options(options)
    content["text"] = "# Reloaded page\n\nUpdated body"
    window._reload_current_page()
    QTest.qWait(20)
    assert "Updated body" in window.reading_view.browser.toPlainText()
    assert window._reading_view_enabled
    assert window.reading_view.reading_options == options
    window.close()
    reopened = create()
    assert reopened._reading_view_enabled
    assert reopened._reading_action.isChecked()
    assert reopened.reading_view._reading_background == "paper"
    assert reopened.reading_view.reading_options == options
    assert reopened._editor_stack.currentWidget() is reopened.reading_view
    assert [(view._reading_background, view.reading_options) for view in others] == original
    assert "reading_options" not in state
    assert "reading_view_background" not in state
    assert "focus_reading" not in state
    assert "audience_reading" not in state


def test_options_popup_and_zoom_change_only_detached_reading_preferences(detached):
    create, state, content = detached
    window = create()
    font = window.editor.font().pointSizeF()
    window._reading_action.trigger()
    window.reading_view.options_button.click()
    popup = window.reading_view.options_popup
    assert popup.isVisible()
    popup.width_choice.setCurrentIndex(popup.width_choice.findData("narrow"))
    popup.larger.click()
    popup.spacing_choice.setCurrentIndex(popup.spacing_choice.findData("compact"))
    popup.stripes.click()
    assert config.load_mode_reading_preferences("page_editor")["options"] == {
        "width": "narrow", "size": 110, "spacing": "compact", "stripe_tables": False,
    }
    window._adjust_font_size(1)
    assert config.load_mode_reading_preferences("page_editor")["options"]["size"] == 120
    assert window.editor.font().pointSizeF() == font
    window._set_reading_view(False)
    assert not popup.isVisible()


def test_read_only_page_can_use_both_views_without_enabling_edits(detached):
    create, state, content = detached
    window = create(read_only=True)
    window._reading_action.trigger()
    assert window._reading_view_enabled
    assert "Original body" in window.reading_view.browser.toPlainText()
    window.reading_view.editRequested.emit()
    assert not window._reading_view_enabled
    assert window.editor.isReadOnly()


def test_heading_picker_escape_restores_reading_focus(detached, qapp):
    create, state, content = detached
    window = create()
    window.editor._emit_heading_outline()
    window._reading_action.trigger()
    window.activateWindow()
    window.reading_view.focus_reader()
    qapp.processEvents()
    window._request_heading_picker_popup()
    popup = QApplication.activePopupWidget()
    assert popup is not None
    query = popup.findChild(QLineEdit)
    QTest.keyClick(query, Qt.Key_Escape)
    QTest.qWait(20)
    assert not popup.isVisible()
    assert window.reading_view.browser.hasFocus()
    assert window._reading_view_enabled


def test_config_rejects_invalid_reading_scope_and_parses_saved_mode(detached):
    create, state, content = detached
    state["page_editor_reading_view"] = "false"
    assert not config.load_page_editor_reading_view()
    state["page_editor_reading_view"] = "true"
    assert config.load_page_editor_reading_view()
    with pytest.raises(ValueError):
        config.save_mode_reading_preferences("unknown", options={})
