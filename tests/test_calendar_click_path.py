from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QDate, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QListWidgetItem

from sp.app.ui.calendar_panel import CalendarPanel, PATH_ROLE
from sp.app.ui.main_window import MainWindow
from sp.server.adapters.files import PAGE_SUFFIX


class _DummyEditor:
    def __init__(self) -> None:
        self.focus_calls = 0

    def setFocus(self, *args, **kwargs) -> None:
        self.focus_calls += 1


def test_open_journal_date_opens_existing_page_without_tree_rebuild(tmp_path: Path) -> None:
    vault_root = tmp_path / "vault"
    rel_path = f"/Journal/2026/03/05/05{PAGE_SUFFIX}"
    target = vault_root / rel_path.lstrip("/")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# Day\n", encoding="utf-8")

    opened: list[tuple[str, bool]] = []
    virtual_calls: list[tuple[str, int, int, int]] = []
    focus_borders: list[str] = []

    dummy = SimpleNamespace(
        vault_root=str(vault_root),
        _pending_selection=None,
        editor=_DummyEditor(),
        _alert=lambda message: pytest.fail(f"unexpected alert: {message}"),
        _open_file=lambda path, **kwargs: opened.append((path, kwargs.get("sync_calendar", True))),
        _open_virtual_journal_page=lambda rel, year, month, day: virtual_calls.append((rel, year, month, day)),
        _apply_focus_borders=lambda: focus_borders.append("applied"),
        _exit_vi_insert_on_activate=lambda: None,
        focusWidget=lambda: None,
        sender=lambda: None,
    )

    MainWindow._open_journal_date(dummy, 2026, 3, 5)

    assert dummy._pending_selection == rel_path
    assert opened == [(rel_path, False)]
    assert virtual_calls == []
    assert dummy.editor.focus_calls == 1
    assert focus_borders == ["applied"]


def test_calendar_day_click_emits_and_defers_secondary_refresh(qtbot, monkeypatch) -> None:
    panel = CalendarPanel()
    qtbot.addWidget(panel)

    emitted: list[tuple[int, int, int]] = []
    deferred: list[tuple[str, str | None, int]] = []

    panel.dateActivated.connect(lambda year, month, day: emitted.append((year, month, day)))

    monkeypatch.setattr(
        panel,
        "_schedule_selection_detail_refresh",
        lambda date=None, *, current_path=None, delay_ms=0: deferred.append(
            (
                date.toString("yyyy-MM-dd") if date and date.isValid() else "",
                current_path,
                delay_ms,
            )
        ),
    )
    monkeypatch.setattr(panel, "_apply_multi_selection_formats", lambda: pytest.fail("selection refresh should be deferred"))
    monkeypatch.setattr(panel, "_update_insights_for_selection", lambda *args, **kwargs: pytest.fail("insights refresh should be deferred"))
    monkeypatch.setattr(panel, "_sync_aux_calendars", lambda: pytest.fail("calendar sync should be deferred"))

    target = QDate(2026, 3, 6)
    panel._pending_shift_click = False
    panel._suppress_next_click = False

    panel._on_date_clicked(target)

    assert emitted == [(2026, 3, 6)]
    assert deferred == [("2026-03-06", None, 0)]
    assert panel.multi_selected_dates == {target}


def test_focus_calendar_tab_focuses_calendar_widget(main_window) -> None:
    main_window._focus_calendar_tab()
    QTest.qWait(20)

    assert main_window.right_panel.tabs.currentWidget() == main_window.right_panel.calendar_panel
    assert main_window.right_panel.calendar_panel.calendar.hasFocus()


def test_calendar_enter_opens_selected_day_after_keyboard_navigation(qtbot) -> None:
    panel = CalendarPanel()
    qtbot.addWidget(panel)
    panel.show()
    panel.raise_()
    panel.activateWindow()

    emitted: list[tuple[int, int, int]] = []
    panel.dateActivated.connect(lambda year, month, day: emitted.append((year, month, day)))

    start = QDate(2026, 3, 10)
    panel.calendar.setSelectedDate(start)
    panel.calendar.setFocus(Qt.OtherFocusReason)
    QTest.qWait(20)

    QTest.keyClick(panel.calendar, Qt.Key_Right)
    QTest.keyClick(panel.calendar, Qt.Key_Return)

    assert panel.calendar.selectedDate() == QDate(2026, 3, 11)
    assert emitted[-1] == (2026, 3, 11)


def test_calendar_shift_arrow_extends_multi_day_selection(qtbot) -> None:
    panel = CalendarPanel()
    qtbot.addWidget(panel)
    panel.show()

    start = QDate(2026, 3, 10)
    panel.calendar.setSelectedDate(start)
    panel._set_single_selection(start)
    panel.calendar.setFocus(Qt.OtherFocusReason)
    QTest.qWait(20)

    QTest.keyClick(panel.calendar, Qt.Key_Right, Qt.ShiftModifier)
    assert panel.calendar.selectedDate() == QDate(2026, 3, 11)
    assert panel._selection_anchor == start
    assert panel.multi_selected_dates == {QDate(2026, 3, 10), QDate(2026, 3, 11)}

    QTest.keyClick(panel.calendar, Qt.Key_Down, Qt.ShiftModifier)
    expected = {QDate.fromJulianDay(day) for day in range(QDate(2026, 3, 10).toJulianDay(), QDate(2026, 3, 18).toJulianDay() + 1)}
    assert panel.calendar.selectedDate() == QDate(2026, 3, 18)
    assert panel.multi_selected_dates == expected


def test_calendar_vi_keys_move_selection_and_enter_opens(qtbot, monkeypatch) -> None:
    panel = CalendarPanel()
    qtbot.addWidget(panel)
    panel.show()

    emitted: list[tuple[int, int, int]] = []
    panel.dateActivated.connect(lambda year, month, day: emitted.append((year, month, day)))
    monkeypatch.setattr(panel, "_is_vi_mode", lambda: True)

    start = QDate(2026, 3, 10)
    panel.calendar.setSelectedDate(start)
    panel.calendar.setFocus(Qt.OtherFocusReason)
    QTest.qWait(20)

    QTest.keyClick(panel.calendar, Qt.Key_L)
    assert panel.calendar.selectedDate() == QDate(2026, 3, 11)

    QTest.keyClick(panel.calendar, Qt.Key_J)
    assert panel.calendar.selectedDate() == QDate(2026, 3, 18)

    QTest.keyClick(panel.calendar, Qt.Key_Return)
    assert emitted[-1] == (2026, 3, 18)


def test_calendar_internal_view_keys_use_custom_navigation(qtbot, monkeypatch) -> None:
    panel = CalendarPanel()
    qtbot.addWidget(panel)
    panel.show()
    monkeypatch.setattr(panel, "_is_vi_mode", lambda: True)

    emitted: list[tuple[int, int, int]] = []
    panel.dateActivated.connect(lambda year, month, day: emitted.append((year, month, day)))

    start = QDate(2026, 3, 10)
    panel.calendar.setSelectedDate(start)
    panel._attach_calendar_view()
    assert panel.calendar_view is not None
    assert not panel.calendar.isDateEditEnabled()

    panel.calendar_view.setFocus(Qt.OtherFocusReason)
    QTest.qWait(20)

    QTest.keyClick(panel.calendar_view, Qt.Key_H)
    assert panel.calendar.selectedDate() == QDate(2026, 3, 9)

    QTest.keyClick(panel.calendar_view, Qt.Key_Down)
    assert panel.calendar.selectedDate() == QDate(2026, 3, 16)

    QTest.keyClick(panel.calendar_view, Qt.Key_Return)
    assert emitted[-1] == (2026, 3, 16)


def test_calendar_shift_vi_keys_extend_multi_day_selection(qtbot, monkeypatch) -> None:
    panel = CalendarPanel()
    qtbot.addWidget(panel)
    panel.show()
    monkeypatch.setattr(panel, "_is_vi_mode", lambda: True)

    start = QDate(2026, 3, 10)
    panel.calendar.setSelectedDate(start)
    panel._set_single_selection(start)
    panel._attach_calendar_view()
    assert panel.calendar_view is not None
    panel.calendar_view.setFocus(Qt.OtherFocusReason)
    QTest.qWait(20)

    QTest.keyClick(panel.calendar_view, Qt.Key_L, Qt.ShiftModifier)
    assert panel.calendar.selectedDate() == QDate(2026, 3, 11)
    assert panel.multi_selected_dates == {QDate(2026, 3, 10), QDate(2026, 3, 11)}

    QTest.keyClick(panel.calendar_view, Qt.Key_J, Qt.ShiftModifier)
    expected = {QDate.fromJulianDay(day) for day in range(QDate(2026, 3, 10).toJulianDay(), QDate(2026, 3, 18).toJulianDay() + 1)}
    assert panel.calendar.selectedDate() == QDate(2026, 3, 18)
    assert panel.multi_selected_dates == expected


def test_calendar_escape_clears_multi_day_selection(qtbot) -> None:
    panel = CalendarPanel()
    qtbot.addWidget(panel)
    panel.show()

    start = QDate(2026, 3, 10)
    end = QDate(2026, 3, 12)
    panel.calendar.setSelectedDate(end)
    panel._set_range_selection(start, end)
    panel.filter_btn.setVisible(True)
    panel.calendar.setFocus(Qt.OtherFocusReason)
    QTest.qWait(20)

    QTest.keyClick(panel.calendar, Qt.Key_Escape)
    QTest.qWait(20)

    assert panel.calendar.selectedDate() == end
    assert panel.multi_selected_dates == {end}
    assert not panel.filter_btn.isVisible()


def test_calendar_headings_escape_clears_multi_day_selection_and_returns_focus(qtbot) -> None:
    panel = CalendarPanel()
    qtbot.addWidget(panel)
    panel.show()
    panel._attach_calendar_view()

    heading = QListWidgetItem("Heading 1")
    heading.setData(PATH_ROLE, "/Journal/2026/03/10/10.md#heading-1")
    panel.headings_list.addItem(heading)

    start = QDate(2026, 3, 10)
    end = QDate(2026, 3, 12)
    panel.calendar.setSelectedDate(end)
    panel._set_range_selection(start, end)
    panel.filter_btn.setVisible(True)
    panel.headings_list.setCurrentRow(0)
    panel.headings_list.setFocus(Qt.OtherFocusReason)
    QTest.qWait(20)

    QTest.keyClick(panel.headings_list, Qt.Key_Escape)
    QTest.qWait(20)

    target = panel.calendar_view or panel.calendar
    assert target.hasFocus()
    assert panel.multi_selected_dates == {end}
    assert not panel.filter_btn.isVisible()


def test_calendar_t_key_jumps_to_today_from_internal_view(qtbot) -> None:
    panel = CalendarPanel()
    qtbot.addWidget(panel)
    panel.show()
    panel._attach_calendar_view()
    assert panel.calendar_view is not None

    start = QDate(2026, 3, 10)
    today = QDate.currentDate()
    panel.calendar.setSelectedDate(start)
    panel._set_single_selection(start)
    panel.calendar_view.setFocus(Qt.OtherFocusReason)
    QTest.qWait(20)

    QTest.keyClick(panel.calendar_view, Qt.Key_T)

    assert panel.calendar.selectedDate() == today
    assert panel.multi_selected_dates == {today}
    assert panel._selection_anchor == today


def test_calendar_slash_focuses_headings_area(qtbot) -> None:
    panel = CalendarPanel()
    qtbot.addWidget(panel)
    panel.show()
    panel._attach_calendar_view()
    assert panel.calendar_view is not None

    heading = QListWidgetItem("Heading 1")
    heading.setData(PATH_ROLE, "/Journal/2026/03/10/10.md#heading-1")
    panel.headings_list.addItem(heading)
    subpage = QListWidgetItem("Subpage 1")
    subpage.setData(PATH_ROLE, "/Journal/2026/03/10/Subpage.md")
    panel.subpage_list.addItem(subpage)

    panel.calendar_view.setFocus(Qt.OtherFocusReason)
    QTest.qWait(20)
    QTest.keyClick(panel.calendar_view, Qt.Key_Slash)

    assert panel.headings_list.hasFocus()
    assert panel.headings_list.currentRow() == 0


def test_calendar_headings_area_arrow_navigation_moves_between_columns(qtbot) -> None:
    panel = CalendarPanel()
    qtbot.addWidget(panel)
    panel.show()

    for idx in range(3):
        heading = QListWidgetItem(f"Heading {idx + 1}")
        heading.setData(PATH_ROLE, f"/Journal/2026/03/10/10.md#heading-{idx + 1}")
        panel.headings_list.addItem(heading)
        subpage = QListWidgetItem(f"Subpage {idx + 1}")
        subpage.setData(PATH_ROLE, f"/Journal/2026/03/10/Subpage-{idx + 1}.md")
        panel.subpage_list.addItem(subpage)

    panel.headings_list.setCurrentRow(0)
    panel.headings_list.setFocus(Qt.OtherFocusReason)
    QTest.qWait(20)

    QTest.keyClick(panel.headings_list, Qt.Key_Down)
    assert panel.headings_list.currentRow() == 1

    QTest.keyClick(panel.headings_list, Qt.Key_Right)
    assert panel.subpage_list.hasFocus()
    assert panel.subpage_list.currentRow() == 1

    QTest.keyClick(panel.subpage_list, Qt.Key_Left)
    assert panel.headings_list.hasFocus()
    assert panel.headings_list.currentRow() == 1


def test_calendar_headings_area_vi_navigation_moves_between_columns(qtbot, monkeypatch) -> None:
    panel = CalendarPanel()
    qtbot.addWidget(panel)
    panel.show()
    monkeypatch.setattr(panel, "_is_vi_mode", lambda: True)

    for idx in range(3):
        heading = QListWidgetItem(f"Heading {idx + 1}")
        heading.setData(PATH_ROLE, f"/Journal/2026/03/10/10.md#heading-{idx + 1}")
        panel.headings_list.addItem(heading)
        subpage = QListWidgetItem(f"Subpage {idx + 1}")
        subpage.setData(PATH_ROLE, f"/Journal/2026/03/10/Subpage-{idx + 1}.md")
        panel.subpage_list.addItem(subpage)

    panel.headings_list.setCurrentRow(0)
    panel.headings_list.setFocus(Qt.OtherFocusReason)
    QTest.qWait(20)

    QTest.keyClick(panel.headings_list, Qt.Key_J)
    assert panel.headings_list.currentRow() == 1

    QTest.keyClick(panel.headings_list, Qt.Key_L)
    assert panel.subpage_list.hasFocus()
    assert panel.subpage_list.currentRow() == 1

    QTest.keyClick(panel.subpage_list, Qt.Key_H)
    assert panel.headings_list.hasFocus()
    assert panel.headings_list.currentRow() == 1


def test_calendar_headings_escape_returns_focus_to_calendar(qtbot) -> None:
    panel = CalendarPanel()
    qtbot.addWidget(panel)
    panel.show()
    panel._attach_calendar_view()

    heading = QListWidgetItem("Heading 1")
    heading.setData(PATH_ROLE, "/Journal/2026/03/10/10.md#heading-1")
    panel.headings_list.addItem(heading)
    panel.headings_list.setCurrentRow(0)
    panel.headings_list.setFocus(Qt.OtherFocusReason)
    QTest.qWait(20)

    QTest.keyClick(panel.headings_list, Qt.Key_Escape)

    target = panel.calendar_view or panel.calendar
    assert target.hasFocus()


def test_open_journal_date_keyboard_focuses_editor(tmp_path: Path) -> None:
    vault_root = tmp_path / "vault"
    rel_path = f"/Journal/2026/03/05/05{PAGE_SUFFIX}"
    target = vault_root / rel_path.lstrip("/")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# Day\n", encoding="utf-8")

    calendar_widget = SimpleNamespace(focus_calls=0)
    calendar_widget.setFocus = lambda *args, **kwargs: setattr(calendar_widget, "focus_calls", calendar_widget.focus_calls + 1)

    class _Sender:
        def consume_activation_source(self):
            return "keyboard"

    dummy = SimpleNamespace(
        vault_root=str(vault_root),
        _pending_selection=None,
        editor=_DummyEditor(),
        _alert=lambda message: pytest.fail(f"unexpected alert: {message}"),
        _open_file=lambda path, **kwargs: None,
        _open_virtual_journal_page=lambda rel, year, month, day: pytest.fail("unexpected virtual page"),
        _apply_focus_borders=lambda: None,
        _exit_vi_insert_on_activate=lambda: None,
        focusWidget=lambda: calendar_widget,
        sender=lambda: _Sender(),
    )

    MainWindow._open_journal_date(dummy, 2026, 3, 5)

    assert dummy.editor.focus_calls == 1
    assert calendar_widget.focus_calls == 0


def test_open_calendar_page_keyboard_focuses_editor() -> None:
    editor = _DummyEditor()

    class _Sender:
        def consume_activation_source(self):
            return "keyboard"

    dummy = SimpleNamespace(
        editor=editor,
        right_panel=SimpleNamespace(tabs=SimpleNamespace(setCurrentWidget=lambda widget: None), calendar_panel=object()),
        focusWidget=lambda: None,
        sender=lambda: _Sender(),
        _split_link_anchor=lambda path: (path, ""),
        _normalize_editor_path=lambda path: path,
        _open_file=lambda path, **kwargs: None,
        _anchor_slug=lambda anchor: anchor,
        _scroll_to_anchor_slug=lambda slug: None,
        _exit_vi_insert_on_activate=lambda: None,
    )

    MainWindow._open_calendar_page(dummy, "/Journal/2026/03/10/10.md")

    assert editor.focus_calls == 1


def test_open_calendar_page_ctrl_enter_keeps_insight_focus(monkeypatch) -> None:
    scheduled_callbacks = []
    monkeypatch.setattr("sp.app.ui.main_window.QTimer.singleShot", lambda delay, callback: scheduled_callbacks.append(callback))

    class _FocusWidget:
        def __init__(self) -> None:
            self.focus_calls = 0

        def setFocus(self, *args, **kwargs) -> None:
            self.focus_calls += 1

    focus_widget = _FocusWidget()

    class _Sender:
        def consume_activation_source(self):
            return "keyboard_keep_panel"

    dummy = SimpleNamespace(
        editor=_DummyEditor(),
        right_panel=SimpleNamespace(tabs=SimpleNamespace(setCurrentWidget=lambda widget: None), calendar_panel=object()),
        focusWidget=lambda: focus_widget,
        sender=lambda: _Sender(),
        _split_link_anchor=lambda path: (path, ""),
        _normalize_editor_path=lambda path: path,
        _open_file=lambda path, **kwargs: None,
        _anchor_slug=lambda anchor: anchor,
        _scroll_to_anchor_slug=lambda slug: None,
        _exit_vi_insert_on_activate=lambda: pytest.fail("should not move editor focus for ctrl-enter"),
    )

    MainWindow._open_calendar_page(dummy, "/Journal/2026/03/10/10.md")

    assert dummy.editor.focus_calls == 0
    assert len(scheduled_callbacks) == 1
    scheduled_callbacks[0]()
    assert focus_widget.focus_calls == 1


def test_open_journal_date_ctrl_enter_keeps_calendar_focus(tmp_path: Path, monkeypatch) -> None:
    vault_root = tmp_path / "vault"
    rel_path = f"/Journal/2026/03/05/05{PAGE_SUFFIX}"
    target = vault_root / rel_path.lstrip("/")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# Day\n", encoding="utf-8")

    scheduled_callbacks = []
    monkeypatch.setattr("sp.app.ui.main_window.QTimer.singleShot", lambda delay, callback: scheduled_callbacks.append(callback))

    class _CalendarWidget:
        def __init__(self) -> None:
            self.focus_calls = 0

        def setFocus(self, *args, **kwargs) -> None:
            self.focus_calls += 1

    calendar_widget = _CalendarWidget()

    class _Sender:
        def __init__(self, calendar) -> None:
            self.calendar = calendar

        def consume_activation_source(self):
            return "keyboard_keep_panel"

    sender = _Sender(calendar_widget)
    dummy = SimpleNamespace(
        vault_root=str(vault_root),
        _pending_selection=None,
        editor=_DummyEditor(),
        _alert=lambda message: pytest.fail(f"unexpected alert: {message}"),
        _open_file=lambda path, **kwargs: None,
        _open_virtual_journal_page=lambda rel, year, month, day: pytest.fail("unexpected virtual page"),
        _apply_focus_borders=lambda: None,
        _exit_vi_insert_on_activate=lambda: pytest.fail("should not move to editor vi activate path"),
        focusWidget=lambda: calendar_widget,
        sender=lambda: sender,
    )

    MainWindow._open_journal_date(dummy, 2026, 3, 5)

    assert dummy.editor.focus_calls == 0
    assert len(scheduled_callbacks) == 1
    scheduled_callbacks[0]()
    assert calendar_widget.focus_calls == 1


def test_jump_to_journal_date_opens_selected_day(main_window, monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _DialogStub:
        def __init__(self, parent, *, anchor_pos, use_vi_keys, vault_accent_color, vault_root):
            captured["parent"] = parent
            captured["anchor_pos"] = anchor_pos
            captured["use_vi_keys"] = use_vi_keys
            captured["vault_accent_color"] = vault_accent_color
            captured["vault_root"] = vault_root

        def exec(self):
            return QDialog.Accepted

        def selected_qdate(self):
            return QDate(2026, 3, 12)

    opened: list[tuple[int, int, int]] = []
    monkeypatch.setattr("sp.app.ui.main_window.JournalDateJumpDialog", _DialogStub)
    monkeypatch.setattr(main_window, "_open_journal_date", lambda year, month, day: opened.append((year, month, day)))
    main_window._vi_enabled = True

    main_window._jump_to_journal_date()

    assert captured["parent"] is main_window
    assert captured["anchor_pos"] is not None
    assert captured["use_vi_keys"] is True
    assert captured["vault_accent_color"] == getattr(main_window, "_vault_accent_color", None)
    assert captured["vault_root"] == main_window.vault_root
    assert opened == [(2026, 3, 12)]


def test_jump_to_journal_date_ignores_cancel(main_window, monkeypatch) -> None:
    class _DialogStub:
        def __init__(self, parent, *, anchor_pos, use_vi_keys, vault_accent_color, vault_root):
            pass

        def exec(self):
            return QDialog.Rejected

        def selected_qdate(self):
            return QDate(2026, 3, 12)

    opened: list[tuple[int, int, int]] = []
    monkeypatch.setattr("sp.app.ui.main_window.JournalDateJumpDialog", _DialogStub)
    monkeypatch.setattr(main_window, "_open_journal_date", lambda year, month, day: opened.append((year, month, day)))

    main_window._jump_to_journal_date()

    assert opened == []
