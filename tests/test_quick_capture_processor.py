from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QDate, Qt
from PySide6.QtTest import QTest

from sp.app.ui.quick_capture_processor import (
    QuickCaptureProcessorDialog,
    _editor_task_symbols,
)
from sp.app import config


def _items() -> list[dict]:
    return [
        {
            "path": "/Journal/2026/08/15/15.md",
            "start_line": 4,
            "end_line": 7,
            "timestamp": "9:00 am",
            "text": "First",
        },
        {
            "path": "/Journal/2026/08/16/16.md",
            "start_line": 4,
            "end_line": 7,
            "timestamp": "10:00 am",
            "text": "Second",
        },
    ]


def test_processor_cycles_items_with_arrows_and_vi_keys(qtbot) -> None:
    activated: list[str] = []
    dialog = QuickCaptureProcessorDialog(
        item_provider=lambda _scope: (_items(), 3),
        activate_item=lambda item: activated.append(item["text"]),
        move_item=lambda _item, _destination: True,
        undo_last=lambda: True,
        page_search=lambda _query: [],
        vi_mode=True,
    )
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.next_button.setFocus()

    QTest.keyClick(dialog.next_button, Qt.Key_Down)
    assert dialog.current_item()["text"] == "Second"
    QTest.keyClick(dialog.next_button, Qt.Key_K)
    assert dialog.current_item()["text"] == "First"
    assert activated[-2:] == ["Second", "First"]


def test_destination_vi_keys_choose_only_selected_suggestion(qtbot, qapp) -> None:
    moved: list[str] = []
    dialog = QuickCaptureProcessorDialog(
        item_provider=lambda _scope: (_items()[:1], 0),
        activate_item=lambda _item: None,
        move_item=lambda _item, destination: moved.append(destination) or True,
        undo_last=lambda: True,
        page_search=lambda _query: [":Projects:Alpha", ":Projects:Beta"],
        vi_mode=True,
    )
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.destination.setFocus()
    QTest.keyClicks(dialog.destination, "proj")
    dialog._refresh_destinations()
    qapp.processEvents()

    assert dialog.destination.text() == "proj"
    QTest.keyClick(
        dialog.destination,
        Qt.Key_J,
        Qt.ControlModifier | Qt.ShiftModifier,
    )
    assert dialog.destination.text() == "proj"
    QTest.keyClick(dialog.destination, Qt.Key_Return)
    assert moved == [":Projects:Alpha"]


def test_destination_escape_clears_inline_picker(qtbot) -> None:
    dialog = QuickCaptureProcessorDialog(
        item_provider=lambda _scope: (_items()[:1], 0),
        activate_item=lambda _item: None,
        move_item=lambda _item, _destination: True,
        undo_last=lambda: True,
        page_search=lambda _query: [],
        vi_mode=True,
    )
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.destination.setFocus()
    QTest.keyClicks(dialog.destination, "changed")

    QTest.keyClick(dialog.destination, Qt.Key_Escape)
    assert dialog.isVisible()
    assert dialog.destination.text() == ""
    assert dialog.move_button.hasFocus()


def test_m_focuses_inline_destination(qtbot) -> None:
    dialog = QuickCaptureProcessorDialog(
        item_provider=lambda _scope: (_items()[:1], 0),
        activate_item=lambda _item: None,
        move_item=lambda _item, _destination: True,
        undo_last=lambda: True,
        page_search=lambda _query: [":Projects:Alpha"],
        vi_mode=True,
    )
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.move_button.setFocus()
    QTest.keyClick(dialog.move_button, Qt.Key_M)

    assert dialog.destination.hasFocus()
    assert dialog._destination_model.stringList() == [":Projects:Alpha"]
    assert dialog.move_button.text() == "M  Move"
    assert not hasattr(dialog, "task_button")


def test_preview_uses_editor_checkbox_symbols() -> None:
    assert _editor_task_symbols("- [ ] Open\n- [x] Done") == "☐ Open\n☑ Done"


def test_active_scope_includes_configured_page_and_calendar_week(main_window, monkeypatch) -> None:
    root = Path(main_window.vault_root)
    center = QDate(2026, 8, 16)
    main_window.right_panel.calendar_panel.calendar.setSelectedDate(center)
    custom = root / "Captures" / "Captures.md"
    today = root / "Journal" / "2026" / "08" / "16" / "16.md"
    older = root / "Journal" / "2026" / "07" / "01" / "01.md"
    for path, text in (
        (custom, "Configured"),
        (today, "Today"),
        (older, "Older"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"# Page\n\n## QuickCaptures\n- *9:00 am*\n  {text}\n\n---\n",
            encoding="utf-8",
        )
    monkeypatch.setattr(config, "load_quick_capture_page_mode", lambda: "custom")
    monkeypatch.setattr(config, "load_quick_capture_custom_page", lambda: ":Captures")

    items, outside = main_window._quick_capture_scope_items("active")

    assert [item["text"] for item in items] == ["Configured", "Today"]
    assert outside == 1
    monkeypatch.setattr(config, "load_quick_capture_page_mode", lambda: "today")
    items, outside = main_window._quick_capture_scope_items("active")
    assert [item["text"] for item in items] == ["Today", "Configured"]
    assert outside == 1
    all_items, outside = main_window._quick_capture_scope_items("all")
    assert {item["text"] for item in all_items} == {"Configured", "Today", "Older"}
    assert outside == 0
    assert main_window._action_process_quick_captures.text() == "Process Quick Captures…"


def test_move_revalidation_accepts_same_capture_after_format_only_change(main_window) -> None:
    root = Path(main_window.vault_root)
    capture = root / "Captures" / "Captures.md"
    capture.parent.mkdir(parents=True, exist_ok=True)
    capture.write_text(
        "# Captures\n\n## QuickCaptures\n- *9:00 am*\n  File this\n\n---\n",
        encoding="utf-8",
    )
    original = main_window._quick_capture_scope_items("all")[0][0]
    capture.write_text(
        "# Captures\n\n## QuickCaptures\n- *9:00 am*\n  File this   \n\n---\n",
        encoding="utf-8",
    )

    refreshed = main_window._fresh_quick_capture_item(original)

    assert refreshed["text"] == original["text"]
    assert refreshed["expected_hash"] != original["expected_hash"]


def test_main_move_callback_processes_consecutive_captures_without_stale_error(main_window) -> None:
    root = Path(main_window.vault_root)
    source = root / "Captures" / "Captures.md"
    destination = root / "Filed" / "Filed.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(
        "# Captures\n\n## QuickCaptures\n"
        "- *9:00 am*\n  First\n\n---\n"
        "- *10:00 am*\n  Second\n\n---\n",
        encoding="utf-8",
    )
    destination.write_text("# Filed\n", encoding="utf-8")
    main_window.current_path = "/PageA/PageA.md"

    for expected in ("First", "Second"):
        item = main_window._quick_capture_scope_items("all")[0][0]
        assert item["text"] == expected
        assert main_window._process_quick_capture_move(item, ":Filed") is True

    assert main_window._quick_capture_scope_items("all")[0] == []
    filed = destination.read_text(encoding="utf-8")
    assert filed.index("First") < filed.index("Second")


def test_processor_opens_capture_and_restores_original_page(main_window, monkeypatch) -> None:
    from sp.app.ui import main_window as main_window_module

    root = Path(main_window.vault_root)
    capture = root / "Captures" / "Captures.md"
    capture.parent.mkdir(parents=True, exist_ok=True)
    capture.write_text(
        "# Captures\n\n## QuickCaptures\n- *9:00 am*\n  File this\n\n---\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "load_quick_capture_page_mode", lambda: "custom")
    monkeypatch.setattr(config, "load_quick_capture_custom_page", lambda: ":Captures")
    monkeypatch.setattr(main_window, "_save_current_file", lambda *args, **kwargs: None)
    main_window.current_path = "/PageA/PageA.md"
    main_window.editor.set_markdown("# Original\n\nRemember this cursor.\n")
    cursor = main_window.editor.textCursor()
    cursor.setPosition(12)
    main_window.editor.setTextCursor(cursor)
    opened: list[str] = []

    def fake_open(path: str, **_kwargs) -> None:
        opened.append(path)
        main_window.current_path = path
        page = root / path.lstrip("/")
        main_window.editor.set_markdown(page.read_text(encoding="utf-8") if page.exists() else "# Original\n\nRemember this cursor.\n")

    monkeypatch.setattr(main_window, "_open_file", fake_open)

    class FakeProcessor:
        def __init__(self, _parent, **kwargs) -> None:
            items, _outside = kwargs["item_provider"]("active")
            assert items
            kwargs["activate_item"](items[0])

        def exec(self) -> int:
            return 0

    monkeypatch.setattr(main_window_module, "QuickCaptureProcessorDialog", FakeProcessor)

    main_window._process_quick_captures()

    assert opened[0] == "/Captures/Captures.md"
    assert opened[-1] == "/PageA/PageA.md"
    assert main_window.current_path == "/PageA/PageA.md"
    assert main_window.editor.textCursor().position() == 12
