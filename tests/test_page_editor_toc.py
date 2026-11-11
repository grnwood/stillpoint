import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QTextFormat
from PySide6.QtTest import QTest

from sp.app import config
from sp.app.ui.page_editor_window import PageEditorWindow
from sp.app.ui.toc_widget import TableOfContentsWidget


@pytest.fixture
def page_editor(qtbot, monkeypatch):
    monkeypatch.setattr(PageEditorWindow, "_load_content", lambda self: None)
    monkeypatch.setattr(config, "load_toc_widget_enabled", lambda: True)
    monkeypatch.setattr(config, "load_page_editor_toc_widget_enabled", lambda: True)
    monkeypatch.setattr(config, "load_vault_accent_color", lambda: "#60a5fa")
    window = PageEditorWindow(
        api_base="http://127.0.0.1:1", vault_root="/tmp/vault", page_path="/Page/Page.md",
        read_only=False, open_in_main_callback=lambda *args, **kwargs: None,
    )
    window.resize(900, 500)
    qtbot.addWidget(window)
    window.editor.set_markdown("# First\n" + "Body text\n" * 100 + "## Last\nLast body\n")
    window._last_saved_content = window.editor.to_markdown()
    window.editor.document().setModified(False)
    window.editor._emit_heading_outline()
    QTest.qWait(40)
    yield window
    window._read_only = True


def test_page_editor_uses_shared_toc_visuals_and_tracks_viewport(page_editor, qtbot):
    window = page_editor
    toc = window.toc_widget
    reference = TableOfContentsWidget()
    qtbot.addWidget(reference)
    reference.set_vault_accent_color("#60a5fa")
    assert isinstance(toc, TableOfContentsWidget)
    assert toc.isVisible()
    assert toc.styleSheet() == reference.styleSheet()
    assert toc._idle_opacity == reference._idle_opacity
    assert toc._hover_opacity == reference._hover_opacity
    assert toc.tree.topLevelItem(0).text(0) == "First"
    assert toc.tree.topLevelItem(0).child(0).text(0) == "Last"
    for size in ((900, 500), (1100, 600)):
        window.resize(*size)
        QTest.qWait(30)
        viewport = window.editor.viewport()
        top_left = window.mapFromGlobal(viewport.mapToGlobal(QPoint(0, 0)))
        assert toc.x() == top_left.x() + viewport.width() - toc.width() - 12
        assert toc.y() == top_left.y() + 12
    copied = []
    toc.linkCopied.connect(copied.append)
    toc._copy_link(toc.tree.topLevelItem(0).child(0).data(0, Qt.UserRole))
    assert copied == [":Page#last"]


def test_toc_click_scrolls_to_heading_flashes_and_keeps_buffer_clean(page_editor):
    window = page_editor
    item = window.toc_widget.tree.topLevelItem(0).child(0)
    target = item.data(0, Qt.UserRole)["position"]
    rect = window.toc_widget.tree.visualItemRect(item)
    QTest.mouseClick(window.toc_widget.tree.viewport(), Qt.LeftButton, pos=rect.center())
    for _ in range(30):
        QTest.qWait(10)
        flashes = [selection for selection in window.editor.extraSelections()
                   if selection.format.property(QTextFormat.UserProperty) == 9991]
        if flashes:
            break
    assert window.editor.textCursor().position() == target
    assert window.editor.verticalScrollBar().value() > 0
    assert window.editor.viewport().rect().intersects(window.editor.cursorRect())
    assert flashes
    assert flashes[-1].cursor.block().text().endswith("Last")
    assert flashes[-1].format.background().color().name() == "#60a5fa"
    assert not window._is_dirty()
    QTest.qWait(250)
    assert not any(selection.format.property(QTextFormat.UserProperty) == 9991
                   for selection in window.editor.extraSelections())


def test_toc_visibility_respects_both_preferences_and_content(page_editor, monkeypatch):
    window = page_editor
    assert window.toc_widget.isVisible()
    monkeypatch.setattr(config, "load_page_editor_toc_widget_enabled", lambda: False)
    window._refresh_toc_preferences()
    assert not window.toc_widget.isVisible()
    monkeypatch.setattr(config, "load_page_editor_toc_widget_enabled", lambda: True)
    window._refresh_toc_preferences()
    assert window.toc_widget.isVisible()
    monkeypatch.setattr(config, "load_toc_widget_enabled", lambda: False)
    window._refresh_toc_preferences()
    assert not window.toc_widget.isVisible()
    monkeypatch.setattr(config, "load_toc_widget_enabled", lambda: True)
    window.editor.set_markdown("# One short heading\nBody\n")
    window.editor._emit_heading_outline()
    QTest.qWait(30)
    assert not window.toc_widget.isVisible()


def test_toc_headings_refresh_after_page_content_changes(page_editor):
    window = page_editor
    window.editor.set_markdown("# Updated\n" + "Body\n" * 100 + "## New section\n")
    window.editor._emit_heading_outline()
    QTest.qWait(30)
    assert window.toc_widget.tree.topLevelItem(0).text(0) == "Updated"
    assert window.toc_widget.tree.topLevelItem(0).child(0).text(0) == "New section"
