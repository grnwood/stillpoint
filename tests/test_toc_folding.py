from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from sp.app import config
from sp.app.ui.main_window import MainWindow
from sp.app.ui.page_editor_window import PageEditorWindow
from sp.app.ui.toc_widget import TableOfContentsWidget


HEADINGS = [
    {"title": "First heading", "level": 1, "position": 0},
    {"title": "Second heading", "level": 2, "position": 20},
]


def test_toc_icon_folds_to_one_line_and_preserves_headings(qtbot):
    widget = TableOfContentsWidget()
    widget.set_headings(HEADINGS)
    qtbot.addWidget(widget)
    expanded_height = widget.height()
    changes = []
    widget.collapsedChanged.connect(changes.append)
    assert not widget.toggle_button.icon().isNull()
    QTest.mouseClick(widget.toggle_button, Qt.LeftButton)
    assert widget.isVisible()
    assert widget.collapsed()
    assert widget.tree.isHidden()
    assert widget.height() < expanded_height
    margins = widget.layout().contentsMargins()
    assert widget.height() <= widget.toggle_button.height() + margins.top() + margins.bottom() + widget.frameWidth() * 2
    assert widget.toggle_button.toolTip() == "Expand TOC"
    widget.set_headings(HEADINGS + [{"title": "New heading", "level": 1, "position": 40}])
    assert widget.collapsed()
    assert widget.tree.isHidden()
    QTest.mouseClick(widget.toggle_button, Qt.LeftButton)
    assert not widget.collapsed()
    assert widget.tree.isVisible()
    assert widget.tree.topLevelItem(1).text(0) == "New heading"
    assert changes == [True, False]


def test_main_and_page_editor_remember_fold_state_separately(qtbot, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "GLOBAL_CONFIG", tmp_path / "preferences.json")
    monkeypatch.setattr(config, "has_active_vault", lambda: False)
    monkeypatch.setattr(PageEditorWindow, "_load_content", lambda self: None)
    config.save_toc_widget_enabled(True)
    config.save_page_editor_toc_widget_enabled(True)
    config.save_toc_collapsed(True)
    config.save_page_editor_toc_collapsed(False)

    main = MainWindow("http://127.0.0.1:1")
    qtbot.addWidget(main)

    def new_page_editor():
        window = PageEditorWindow(
            api_base="http://127.0.0.1:1", vault_root="/tmp/vault", page_path="/Page/Page.md",
            read_only=False, open_in_main_callback=lambda *args, **kwargs: None,
        )
        qtbot.addWidget(window)
        return window

    popup = new_page_editor()
    assert main.toc_widget.collapsed()
    assert not popup.toc_widget.collapsed()
    main.toc_widget.set_collapsed(False)
    popup.toc_widget.set_collapsed(True)
    assert not config.load_toc_collapsed()
    assert config.load_page_editor_toc_collapsed()
    assert config.load_toc_widget_enabled()
    assert config.load_page_editor_toc_widget_enabled()
    another_popup = new_page_editor()
    assert another_popup.toc_widget.collapsed()
    assert another_popup.toc_widget.tree.isHidden()
    config.save_toc_widget_enabled(False)
    popup._refresh_toc_preferences()
    assert popup.toc_widget.isHidden()
    assert popup.toc_widget.collapsed()
    config.save_toc_widget_enabled(True)
    popup._refresh_toc_preferences()
    assert popup.toc_widget.collapsed()
    popup.toc_widget.set_collapsed(False)
    assert not config.load_page_editor_toc_collapsed()
    assert not config.load_toc_collapsed()
