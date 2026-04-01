from PySide6.QtCore import Qt, QEvent
from PySide6.QtGui import QKeyEvent
from PySide6.QtTest import QTest

from sp.app.ui.markdown_editor import MarkdownEditor


def _force_initial_paint(editor: MarkdownEditor, qapp) -> None:
    editor.resize(400, 300)
    editor.show()
    for _ in range(5):
        qapp.processEvents()
        QTest.qWait(10)
        editor.repaint()
    qapp.processEvents()


def _wait_for(predicate, qapp, timeout_ms: int = 400) -> bool:
    for _ in range(max(1, timeout_ms // 10)):
        if predicate():
            return True
        qapp.processEvents()
        QTest.qWait(10)
    return predicate()


def _force_vi_navigation_mode(editor: MarkdownEditor, qapp) -> None:
    editor.set_vi_mode_enabled(True)
    if not _wait_for(lambda: editor._vi_mode_active, qapp, timeout_ms=150):
        editor._vi_has_painted = True
        editor._vi_pending_activation = False
        editor._enter_vi_navigation_mode(force_emit=True)


def test_alt_b_requests_link_relations_popup_for_current_page(qapp) -> None:
    editor = MarkdownEditor()
    editor.set_context("/vault", "/PageA/PageA.md")
    _force_initial_paint(editor, qapp)
    requested: list[str] = []
    editor.linkRelationsPopupRequested.connect(requested.append)

    QTest.keyClick(editor, Qt.Key_B, Qt.AltModifier)

    assert requested == ["/PageA/PageA.md"]
    editor.close()


def test_alt_b_prefers_link_under_cursor_when_present(qapp, monkeypatch) -> None:
    editor = MarkdownEditor()
    editor.set_context("/vault", "/PageA/PageA.md")
    _force_initial_paint(editor, qapp)
    requested: list[str] = []
    editor.linkRelationsPopupRequested.connect(requested.append)
    monkeypatch.setattr(editor, "_link_under_cursor", lambda *_args, **_kwargs: ":PageB")

    QTest.keyClick(editor, Qt.Key_B, Qt.AltModifier)

    assert requested == [":PageB"]
    editor.close()


def test_vi_zb_requests_link_relations_popup_for_current_page(qapp) -> None:
    editor = MarkdownEditor()
    editor.set_context("/vault", "/PageA/PageA.md")
    _force_initial_paint(editor, qapp)
    _force_vi_navigation_mode(editor, qapp)
    requested: list[str] = []
    editor.linkRelationsPopupRequested.connect(requested.append)

    assert editor._handle_vi_keypress(QKeyEvent(QEvent.KeyPress, Qt.Key_Z, Qt.NoModifier, "z"))
    assert editor._handle_vi_keypress(QKeyEvent(QEvent.KeyPress, Qt.Key_B, Qt.NoModifier, "b"))

    assert requested == ["/PageA/PageA.md"]
    editor.close()


def test_vi_zb_prefers_link_under_cursor_relations(qapp, monkeypatch) -> None:
    editor = MarkdownEditor()
    editor.set_context("/vault", "/PageA/PageA.md")
    _force_initial_paint(editor, qapp)
    _force_vi_navigation_mode(editor, qapp)
    requested: list[str] = []
    editor.linkRelationsPopupRequested.connect(requested.append)
    monkeypatch.setattr(editor, "_link_under_cursor", lambda *_args, **_kwargs: ":PageB")

    assert editor._handle_vi_keypress(QKeyEvent(QEvent.KeyPress, Qt.Key_Z, Qt.NoModifier, "z"))
    assert editor._handle_vi_keypress(QKeyEvent(QEvent.KeyPress, Qt.Key_B, Qt.NoModifier, "b"))

    assert requested == [":PageB"]
    editor.close()


def test_editor_alt_up_routes_to_hierarchy_navigation(monkeypatch, qapp) -> None:
    editor = MarkdownEditor()
    editor.set_context("/vault", "/PageA/Child1/Child1.md")
    _force_initial_paint(editor, qapp)

    calls: list[str] = []

    class StubWindow:
        def _navigate_hierarchy_up(self) -> None:
            calls.append("up")

        def _navigate_hierarchy_down(self) -> None:
            calls.append("down")

    monkeypatch.setattr(editor, "window", lambda: StubWindow())

    editor._trigger_history_navigation(Qt.Key_Up)

    assert calls == ["up"]
    editor.close()


def test_editor_alt_down_routes_to_hierarchy_navigation(monkeypatch, qapp) -> None:
    editor = MarkdownEditor()
    editor.set_context("/vault", "/PageA/PageA.md")
    _force_initial_paint(editor, qapp)

    calls: list[str] = []

    class StubWindow:
        def _navigate_hierarchy_up(self) -> None:
            calls.append("up")

        def _navigate_hierarchy_down(self) -> None:
            calls.append("down")

    monkeypatch.setattr(editor, "window", lambda: StubWindow())

    editor._trigger_history_navigation(Qt.Key_Down)

    assert calls == ["down"]
    editor.close()
