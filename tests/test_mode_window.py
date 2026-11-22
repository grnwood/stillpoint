from __future__ import annotations

import pytest

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMainWindow
from shiboken6 import Shiboken

from sp.app.ui.markdown_editor import MarkdownEditor
from sp.app.ui.mode_window import ModeWindow


def _drain_events(wait_ms: int = 25, rounds: int = 4) -> None:
    for _ in range(rounds):
        QTest.qWait(wait_ms)


@pytest.mark.parametrize("mode", ["focus", "audience"])
def test_reading_startup_uses_scaled_font_before_editor_is_shown(qtbot, mode):
    from sp.app.ui.diagram_editor_icons import get_diagram_editor_icon
    base = MarkdownEditor()
    qtbot.addWidget(base)
    base.set_markdown("# Heading\n\nBody text")
    window = ModeWindow(mode, base, vault_root=None, page_path="/Page.md",
                        read_only=False, heading_provider=lambda: [],
                        settings={"reading_view": True, "font_size": 20, "font_scale": 1.5})
    qtbot.addWidget(window)
    assert not window.windowIcon().isNull()
    assert window.windowIcon().cacheKey() == get_diagram_editor_icon(mode).cacheKey()
    _drain_events()
    initial = window.reading_view._base_font_point_size
    assert initial == 30
    window._set_reading_view(False, persist=False)
    _drain_events()
    window._set_reading_view(True, persist=False)
    _drain_events()
    assert window.reading_view._base_font_point_size == initial
    window.close()


@pytest.mark.parametrize("mode", ["focus", "audience"])
def test_mode_shortcut_focuses_reader_for_keyboard_scrolling(qtbot, monkeypatch, mode):
    from PySide6.QtWidgets import QApplication
    from sp.app import config

    monkeypatch.setattr(config, f"save_{mode}_mode_settings", lambda value: None)
    base = MarkdownEditor()
    qtbot.addWidget(base)
    base.set_markdown("\n\n".join(f"Paragraph {number}" for number in range(200)))
    window = ModeWindow(mode, base, vault_root=None, page_path="/Page.md",
                        read_only=False, heading_provider=lambda: [], settings={})
    qtbot.addWidget(window)
    _drain_events()
    window.activateWindow()
    window.editor.setFocus()
    QTest.keyClick(window.editor, Qt.Key_Space, Qt.ControlModifier)
    _drain_events()
    assert QApplication.focusWidget() is window.reading_view.browser
    bar = window.reading_view.browser.verticalScrollBar()
    before = bar.value()
    QTest.keyClick(QApplication.focusWidget(), Qt.Key_PageDown)
    _drain_events()
    assert bar.value() > before
    QTest.keyClick(QApplication.focusWidget(), Qt.Key_Space, Qt.ControlModifier)
    _drain_events()
    assert QApplication.focusWidget() is window.editor
    window.close()


@pytest.mark.parametrize("mode", ["focus", "audience"])
def test_mode_reading_controls_persist_and_preserve_edits(qtbot, monkeypatch, mode):
    from sp.app import config

    saved = []
    monkeypatch.setattr(config, f"save_{mode}_mode_settings", lambda value: saved.append(dict(value)))
    state = {}
    monkeypatch.setattr(config, "_read_global_config", lambda: state)
    monkeypatch.setattr(config, "_update_global_config", state.update)
    base = MarkdownEditor()
    qtbot.addWidget(base)
    base.set_markdown("# Original\n\nText")
    window = ModeWindow(mode, base, vault_root=None, page_path="/Page.md",
                        read_only=False, heading_provider=lambda: [],
                        settings={"reading_view": True})
    qtbot.addWidget(window)
    _drain_events()
    assert window._reading_toggle.isChecked()
    assert window.editor.isHidden()
    assert "Original" in window.reading_view.browser.toPlainText()
    window.reading_view._background_actions["paper"].trigger()
    assert state[f"{mode}_reading"]["background"] == "paper"
    assert "reading_view_background" not in state
    other_mode = "audience" if mode == "focus" else "focus"
    assert config.load_mode_reading_preferences(other_mode)["background"] == "theme"
    options = dict(window.reading_view.reading_options, width="wide")
    window.reading_view.set_reading_options(options)
    assert config.load_mode_reading_preferences(mode)["options"]["width"] == "wide"
    assert config.load_mode_reading_preferences(other_mode)["options"]["width"] != "wide"
    assert "reading_options" not in state
    before = [widget.geometry() for widget in
              (window._reading_toggle, window._reading_background, window._reading_options)]
    window._reading_toggle.click()
    _drain_events()
    assert before == [widget.geometry() for widget in
                      (window._reading_toggle, window._reading_background, window._reading_options)]
    assert not window.editor.isHidden()
    assert not window._reading_options.isEnabled()
    assert window._reading_options_opacity.opacity() == 0.35
    assert window._reading_background.isHidden()
    from PySide6.QtGui import QPalette
    editor_background = window.editor.palette().color(QPalette.Base)
    editor_text = window.editor.palette().color(QPalette.Text)
    window.reading_view._background_actions["dusk"].trigger()
    assert window.editor.palette().color(QPalette.Base) == editor_background
    assert window.editor.palette().color(QPalette.Text) == editor_text
    window.reading_view._background_actions["paper"].trigger()
    assert window.editor.palette().color(QPalette.Base) == editor_background
    window.editor.set_markdown("# Edited\n\nPreserved")
    QTest.keyClick(window.editor, Qt.Key_Space, Qt.ControlModifier)
    assert "Edited" in window.reading_view.browser.toPlainText()
    assert window._reading_options.isEnabled()
    assert not window._reading_background.isHidden()
    assert window.reading_view._paper_color.name() == "#f5f0e6"
    assert window._reading_options_opacity.opacity() == 1.0
    assert saved[-1]["reading_view"] is True
    window.close()
    _drain_events()
    assert "Edited" in base.to_markdown()
    from sp.app.ui.markdown_preview import MarkdownReadingView
    restored = MarkdownReadingView(preference_scope=mode)
    main_reader = MarkdownReadingView()
    qtbot.addWidget(restored)
    qtbot.addWidget(main_reader)
    assert restored._reading_background == "paper"
    assert restored.reading_options["width"] == "wide"
    assert main_reader._reading_background == "white"


@pytest.mark.parametrize("mode", ["focus", "audience"])
def test_mode_window_has_no_native_owner_in_fullscreen_or_windowed_mode(qtbot, mode) -> None:
    main = QMainWindow()
    base_editor = MarkdownEditor()
    main.setCentralWidget(base_editor)
    qtbot.addWidget(main)
    window = ModeWindow(
        mode, base_editor, vault_root=None, page_path="/Page.md",
        read_only=False, heading_provider=lambda: [], parent=main,
    )
    qtbot.addWidget(window)
    _drain_events()

    flags = window.windowFlags()
    assert not flags & Qt.FramelessWindowHint
    for windowed in (False, True, False):
        if window._windowed != windowed:
            window._toggle_window_mode()
            _drain_events()
        assert window.isWindow()
        assert window.parentWidget() is None
        assert window.windowHandle().transientParent() is None
        assert window.windowModality() == Qt.NonModal
        assert window.windowFlags() == flags
        assert window.isFullScreen() is not windowed
        assert window._window_button.toolTip() == ("Enter full screen" if windowed else "Exit full screen")

    # Native window controls must keep the custom fullscreen button in sync.
    window.showNormal()
    _drain_events()
    assert window._windowed
    window.showMinimized()
    _drain_events()
    assert window.isMinimized()
    assert window._windowed
    window.showNormal()
    _drain_events()
    assert window.isVisible()
    assert not window.isFullScreen()
    assert window.windowFlags() == flags

    window._status_message("Mode status")
    assert main.statusBar().currentMessage() == "Mode status"
    window.editor.set_markdown("Edited in independent window\n")
    window.close()
    _drain_events()
    assert base_editor.to_markdown() == "Edited in independent window\n"
    assert base_editor.document().isModified()


def test_mode_window_close_preserves_edited_overlay_buffer_as_dirty(qtbot) -> None:
    base_editor = MarkdownEditor()
    qtbot.addWidget(base_editor)
    base_editor.set_markdown("# Page\n\nOriginal body\n")
    base_editor.document().setModified(False)

    window = ModeWindow(
        "focus",
        base_editor,
        vault_root=None,
        page_path="/Page.md",
        read_only=False,
        heading_provider=lambda: [],
    )
    qtbot.addWidget(window)

    try:
        window._mark_ready()
        window.editor.set_markdown("# Page\n\nEdited in overlay\n")
        window.close()
        _drain_events()

        assert base_editor.to_markdown() == "# Page\n\nEdited in overlay\n"
        assert base_editor.document().isModified() is True
    finally:
        try:
            if Shiboken.isValid(window):
                window.close()
        except Exception:
            pass
        try:
            if Shiboken.isValid(base_editor):
                base_editor.close()
        except Exception:
            pass


@pytest.mark.parametrize("mode", ["focus", "audience"])
@pytest.mark.parametrize("reading", [False, True])
def test_audience_heading_picker_is_centered_near_top_and_ignores_cursor(qtbot, mode, reading) -> None:
    base_editor = MarkdownEditor()
    qtbot.addWidget(base_editor)
    base_editor.set_markdown("# First\n" + ("body\n" * 100) + "## Last\n")
    window = ModeWindow(
        mode,
        base_editor,
        vault_root=None,
        page_path="/Page.md",
        read_only=False,
        heading_provider=lambda: [
            {"title": "First", "line": 1, "level": 1, "position": 0},
            {"title": "Last", "line": 102, "level": 2, "position": 500},
        ],
    )
    qtbot.addWidget(window)
    try:
        window.resize(1100, 760)
        window.show()
        _drain_events()
        window._set_reading_view(reading, persist=False)
        window.showNormal()
        window.resize(760, 560)
        _drain_events()

        window.editor.moveCursor(QTextCursor.MoveOperation.Start)
        if reading:
            QTest.keyClick(window.reading_view.browser, Qt.Key_T)
        else:
            window._request_heading_picker_popup()
        _drain_events()
        picker = window._heading_picker
        first_position = picker.pos()

        viewport = window.reading_view if reading else window.editor
        viewport_center = viewport.mapToGlobal(viewport.rect().center())
        viewport_top = viewport.mapToGlobal(QPoint(0, 0)).y()
        assert abs(picker.frameGeometry().center().x() - viewport_center.x()) <= 1
        assert abs(picker.y() - (viewport_top + viewport.height() // 4)) <= 2
        assert picker.width() >= 420
        assert picker.height() >= 260

        picker.close()
        window.editor.moveCursor(QTextCursor.MoveOperation.End)
        window._request_heading_picker_popup()
        _drain_events()

        assert window._heading_picker.pos() == first_position
    finally:
        window.close()
        try:
            if Shiboken.isValid(base_editor):
                base_editor.close()
        except Exception:
            pass
