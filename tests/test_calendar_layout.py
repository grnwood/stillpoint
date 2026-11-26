import pytest
from PySide6.QtCore import QDate, QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QHeaderView, QListWidgetItem, QSpinBox

from sp.app import config
from sp.app.ui.calendar_panel import CalendarPanel


@pytest.fixture
def panel(qtbot, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "GLOBAL_CONFIG", tmp_path / "preferences.json")
    widget = CalendarPanel()
    qtbot.addWidget(widget)
    monkeypatch.setattr(widget, "_update_due_tasks", lambda *args: None)
    monkeypatch.setattr(widget, "_populate_recent_modified", lambda *args, **kwargs: None)
    return widget


def test_narrow_calendar_stacks_content_without_hiding_tasks(panel):
    panel.resize(408, 900)
    QTest.qWait(50)
    panel.update_calendar_layout()
    assert panel.width() == 408
    assert panel.main_splitter.orientation() == Qt.Vertical
    assert panel.day_insights.isVisible()
    assert panel.journal_tabs.isVisible()
    assert panel.tasks_due_list.isVisible()
    panel.journal_tabs.setCurrentIndex(1)
    assert panel.activity_panel.isVisible()
    assert panel.activity_scope_combo.isVisible()
    panel.resize(1000, 700)
    QTest.qWait(50)
    assert panel.main_splitter.orientation() == Qt.Horizontal
    assert panel.journal_tabs.isVisible()
    panel.resize(408, 900)
    QTest.qWait(50)
    assert panel.main_splitter.orientation() == Qt.Vertical
    assert panel.journal_tabs.isVisible()


def test_month_controls_navigate_and_today_resets_selection(panel):
    panel.set_calendar_date(2026, 11, 20)
    panel.next_month_button.click()
    assert (panel.calendar.yearShown(), panel.calendar.monthShown()) == (2026, 12)
    assert panel.month_title.text() == QDate(2026, 12, 1).toString("MMMM yyyy")
    panel.previous_month_button.click()
    assert panel.calendar.monthShown() == 11
    assert panel.today_btn.isVisible()
    panel.today_btn.click()
    assert panel.calendar.selectedDate() == QDate.currentDate()
    assert panel.multi_selected_dates == {QDate.currentDate()}
    assert panel.today_btn.isVisible()


def test_month_picker_changes_view_without_opening_a_page(panel):
    activated = []
    panel.dateActivated.connect(lambda *args: activated.append(args))
    selected = panel.calendar.selectedDate()

    def choose():
        dialog = QApplication.activeModalWidget()
        dialog.findChild(QComboBox).setCurrentIndex(1)
        dialog.findChild(QSpinBox).setValue(2028)
        dialog.accept()

    QTimer.singleShot(0, choose)
    panel.month_title.click()
    assert (panel.calendar.yearShown(), panel.calendar.monthShown()) == (2028, 2)
    assert panel.calendar.selectedDate() == selected
    assert activated == []


def test_open_journal_uses_selected_date_without_writing_files(panel, tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    panel.vault_root = str(root)
    panel.set_calendar_date(2026, 11, 20)
    panel._update_insights(QDate(2026, 11, 20))
    activated = []
    panel.dateActivated.connect(lambda *args: activated.append(args))
    panel.open_journal_button.click()
    assert activated == [(2026, 11, 20)]
    assert list(root.iterdir()) == []
    assert panel.headings_list.empty_label.isVisible()
    assert not panel.insight_tags.isVisible()


def test_empty_states_do_not_add_fake_list_items(panel):
    assert panel.headings_list.count() == 0
    assert not panel.headings_list.empty_label.isHidden()
    panel.headings_list.addItem(QListWidgetItem("Priorities"))
    assert panel.headings_list.empty_label.isHidden()
    panel.headings_list.clear()
    assert not panel.headings_list.empty_label.isHidden()
    assert panel.headings_list.count() == 0
    assert panel.tasks_due_list.header().sectionResizeMode(1) == QHeaderView.Stretch


def test_compact_splitter_size_is_saved_separately(panel, monkeypatch):
    saved = []
    monkeypatch.setattr(config, "save_splitter_sizes", lambda key, sizes: saved.append((key, sizes)))
    panel.resize(408, 900)
    QTest.qWait(40)
    panel._save_splitter_sizes()
    assert saved[-1][0] == f"{panel._splitter_key}_compact"
    panel.resize(1000, 700)
    QTest.qWait(40)
    panel._save_splitter_sizes()
    assert saved[-1][0] == panel._splitter_key
