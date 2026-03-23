from sp.app.ui.calendar_panel import CalendarPanel
from sp.app.ui.date_insert_dialog import (
    DateInsertDialog,
    JournalDateJumpDialog,
    _enforce_calendar_dialog_width,
)


def _required_dialog_width(dialog) -> int:
    margins = dialog.layout().contentsMargins()
    calendar_width = max(dialog.calendar.minimumSizeHint().width(), dialog.calendar.sizeHint().width())
    return calendar_width + margins.left() + margins.right()


def test_calendar_panel_selection_uses_vault_accent(qtbot) -> None:
    panel = CalendarPanel()
    qtbot.addWidget(panel)

    panel.set_vault_accent_color("#F97316")

    assert panel.calendar_delegate.highlight_color.name().lower() == "#f97316"
    assert "#f97316" in panel.calendar.styleSheet().lower()


def test_date_insert_dialog_calendar_uses_vault_accent(qtbot) -> None:
    dialog = DateInsertDialog(vault_accent_color="#10B981")
    qtbot.addWidget(dialog)

    assert "#10b981" in dialog.calendar.styleSheet().lower()


def test_date_insert_dialog_opens_wide_enough_for_calendar(qtbot) -> None:
    dialog = DateInsertDialog(vault_accent_color="#10B981")
    qtbot.addWidget(dialog)

    required_width = _required_dialog_width(dialog)

    assert dialog.minimumWidth() >= required_width
    assert dialog.width() >= required_width


def test_journal_date_jump_dialog_calendar_uses_vault_accent(qtbot) -> None:
    dialog = JournalDateJumpDialog(vault_accent_color="#3B82F6")
    qtbot.addWidget(dialog)

    assert "#3b82f6" in dialog.calendar.styleSheet().lower()


def test_journal_date_jump_dialog_opens_wide_enough_for_calendar(qtbot) -> None:
    dialog = JournalDateJumpDialog(vault_accent_color="#3B82F6")
    qtbot.addWidget(dialog)

    required_width = _required_dialog_width(dialog)

    assert dialog.minimumWidth() >= required_width
    assert dialog.width() >= required_width


def test_date_insert_dialog_reenforces_calendar_width_after_shrink(qtbot) -> None:
    dialog = DateInsertDialog(vault_accent_color="#10B981")
    qtbot.addWidget(dialog)

    dialog.setMinimumWidth(0)
    dialog.calendar.setMinimumWidth(0)
    dialog.resize(120, dialog.height())
    _enforce_calendar_dialog_width(dialog, dialog.calendar, 320)

    required_width = _required_dialog_width(dialog)

    assert dialog.minimumWidth() >= required_width
    assert dialog.width() >= required_width


def test_journal_date_jump_dialog_reenforces_calendar_width_after_shrink(qtbot) -> None:
    dialog = JournalDateJumpDialog(vault_accent_color="#3B82F6")
    qtbot.addWidget(dialog)

    dialog.setMinimumWidth(0)
    dialog.calendar.setMinimumWidth(0)
    dialog.resize(120, dialog.height())
    _enforce_calendar_dialog_width(dialog, dialog.calendar, 300)

    required_width = _required_dialog_width(dialog)

    assert dialog.minimumWidth() >= required_width
    assert dialog.width() >= required_width
