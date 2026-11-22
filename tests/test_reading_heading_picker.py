from pathlib import Path

import pytest
from PySide6.QtCore import QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from sp.app import config
from sp.app.folder_navigator.window import HeadingPicker, Window


@pytest.mark.parametrize('vi_enabled', [False, True])
@pytest.mark.parametrize('accept', [False, True])
def test_folder_reading_t_picker_filters_navigates_and_restores_focus(qtbot, monkeypatch, tmp_path, vi_enabled, accept):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setattr(config, 'GLOBAL_CONFIG', tmp_path / 'preferences.json')
    monkeypatch.setattr(config, 'load_vi_mode_enabled', lambda: vi_enabled)
    page = tmp_path / 'headings.md'
    page.write_text('# First\n\nText\n\n## Last\n')
    window = Window(tmp_path)
    qtbot.addWidget(window)
    window.resize(1000, 700)
    window.open_file(page, pinned=True)
    tab = window.active_tab()
    tab.set_reading_view(True)
    window.activateWindow()
    tab.reading_view.focus_reader()
    QTest.qWait(120)
    navigated = []
    monkeypatch.setattr(tab.reading_view, 'scroll_to_source_line', navigated.append)
    observed = {}

    def operate_picker():
        picker = next((widget for widget in QApplication.topLevelWidgets()
                       if isinstance(widget, HeadingPicker) and widget.isVisible()), None)
        if picker is None:
            observed['opened'] = False
            return
        observed['opened'] = True
        observed['query_focus'] = picker.query.hasFocus()
        picker.query.setText('Last')
        observed['count'] = picker.results.count()
        QTest.keyClick(picker.query, Qt.Key_Return if accept else Qt.Key_Escape)

    QTimer.singleShot(20, operate_picker)
    # Close a misbehaving modal picker so a regression fails instead of hanging.
    timeout = QTimer(window)
    timeout.setSingleShot(True)
    timeout.timeout.connect(lambda: [widget.reject() for widget in QApplication.topLevelWidgets()
                                    if isinstance(widget, HeadingPicker) and widget.isVisible()])
    timeout.start(1500)
    QTest.keyClick(tab.reading_view.browser, Qt.Key_T)
    QTest.qWait(40)
    timeout.stop()
    assert observed == {'opened': True, 'query_focus': True, 'count': 1}
    assert navigated == ([5] if accept else [])
    assert tab.reading_view_enabled
    assert tab.reading_view.browser.hasFocus()
    window.close()


def test_main_reading_picker_escape_and_activation_keep_reader_focus(main_window):
    from PySide6.QtWidgets import QLineEdit

    window = main_window
    window.editor.set_markdown('# First\n\nText\n\n## Last')
    window._toc_headings = [
        {'title': 'First', 'line': 1, 'position': 0, 'level': 1},
        {'title': 'Last', 'line': 5, 'position': window.editor.document().findBlockByNumber(4).position(), 'level': 2},
    ]
    window.show()
    window._set_reading_view_enabled(True)
    window.activateWindow()
    window.reading_view.focus_reader()
    QTest.qWait(40)
    window._repair_focus_after_activation()
    assert window.reading_view.browser.hasFocus()
    for _ in range(2):
        QTest.keyClick(window.reading_view.browser, Qt.Key_T)
        QTest.qWait(30)
        picker = window._heading_picker
        assert picker.isVisible()
        QTest.keyClick(picker.findChild(QLineEdit), Qt.Key_Escape)
        QTest.qWait(30)
        assert not picker.isVisible()
        assert window._reading_view_enabled
        assert window.reading_view.browser.hasFocus()
