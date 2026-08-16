from __future__ import annotations

from pathlib import Path

import pytest

from sp.app.capture_triage import (
    TriageConflictError,
    capture_header,
    list_triage_items,
    list_quick_capture_chunks,
    parse_triage_items,
    parse_quick_capture_chunks,
    process_quick_capture_chunk,
    process_triage_item,
    suggest_triage_outcomes,
)
from sp.app.task_mutations import undo_file_mutation


def _page(header: str, body: str = "An idea") -> str:
    return f"# Day\n\n## QuickCaptures\n{header}\n  {body}\n\n---\n"


def test_parse_explicit_triage_capture() -> None:
    content = _page(capture_header("10:42 am", capture_id="capture-1", inbox=True))

    items = parse_triage_items(content, "/Journal/2026/08/15/15.md")

    assert len(items) == 1
    assert items[0].id == "capture-1"
    assert items[0].text == "An idea"


def test_inbox_page_capture_is_implicit() -> None:
    content = _page(capture_header("10:42 am"))

    items = parse_triage_items(content, "/Inbox/Inbox.md")

    assert len(items) == 1
    assert items[0].implicit is True


def test_suggestions_are_deterministic_and_non_executing() -> None:
    assert suggest_triage_outcomes("Call Sarah tomorrow")[0] == {
        "action": "task",
        "label": "Make Task",
        "reason": "Starts with an action verb.",
    }
    assert suggest_triage_outcomes("Background for :Projects:Launch")[0]["action"] == "file"


def test_keep_as_note_removes_triage_state_without_duplicate_separator(tmp_path: Path) -> None:
    journal = tmp_path / "Journal" / "2026" / "08" / "15" / "15.md"
    journal.parent.mkdir(parents=True)
    journal.write_text(
        _page(capture_header("10:42 am", capture_id="capture-1", inbox=True)),
        encoding="utf-8",
    )
    item = list_triage_items(tmp_path)[0]

    process_triage_item(
        tmp_path,
        path=item["path"],
        item_id=item["id"],
        expected_hash=item["expected_hash"],
        action="note",
        text="Edited idea",
    )

    updated = journal.read_text(encoding="utf-8")
    assert "@inbox" not in updated
    assert "sp:capture" not in updated
    assert "Edited idea" in updated
    assert "---\n---" not in updated
    assert list_triage_items(tmp_path) == []


def test_make_task_converts_capture_in_place(tmp_path: Path) -> None:
    inbox = tmp_path / "Inbox" / "Inbox.md"
    inbox.parent.mkdir(parents=True)
    inbox.write_text(
        _page(capture_header("10:42 am", capture_id="capture-2", inbox=True), "Call Sarah"),
        encoding="utf-8",
    )
    item = list_triage_items(tmp_path)[0]

    process_triage_item(
        tmp_path,
        path=item["path"],
        item_id=item["id"],
        expected_hash=item["expected_hash"],
        action="task",
        text="Call Sarah",
        priority=2,
        tags=["phone"],
        due="2026-08-20",
    )

    updated = inbox.read_text(encoding="utf-8")
    assert "☐ Call Sarah !! @phone <2026-08-20" in updated
    assert "@inbox" not in updated


def test_process_rejects_changed_capture(tmp_path: Path) -> None:
    journal = tmp_path / "Journal" / "Day" / "Day.md"
    journal.parent.mkdir(parents=True)
    journal.write_text(
        _page(capture_header("10:42 am", capture_id="capture-3", inbox=True)),
        encoding="utf-8",
    )
    item = list_triage_items(tmp_path)[0]
    journal.write_text(journal.read_text(encoding="utf-8").replace("An idea", "Changed"), encoding="utf-8")

    with pytest.raises(TriageConflictError, match="changed after"):
        process_triage_item(
            tmp_path,
            path=item["path"],
            item_id=item["id"],
            expected_hash=item["expected_hash"],
            action="note",
            text="Edited",
        )


def test_file_moves_attachment_and_undo_restores_it(tmp_path: Path) -> None:
    journal = tmp_path / "Journal" / "2026" / "08" / "15" / "15.md"
    destination = tmp_path / "Projects" / "Launch" / "Launch.md"
    journal.parent.mkdir(parents=True)
    destination.parent.mkdir(parents=True)
    journal.write_text(
        _page(
            capture_header("10:42 am", capture_id="capture-4", inbox=True),
            "Screenshot ![](./image.png)",
        ),
        encoding="utf-8",
    )
    destination.write_text("# Launch\n", encoding="utf-8")
    source_image = journal.parent / "image.png"
    source_image.write_bytes(b"image")
    item = list_triage_items(tmp_path)[0]

    receipt = process_triage_item(
        tmp_path,
        path=item["path"],
        item_id=item["id"],
        expected_hash=item["expected_hash"],
        action="file",
        text=item["text"],
        destination="/Projects/Launch/Launch.md",
    )

    destination_image = destination.parent / "image.png"
    assert not source_image.exists()
    assert destination_image.read_bytes() == b"image"
    assert "![](./image.png)" in destination.read_text(encoding="utf-8")

    undo_file_mutation(tmp_path, receipt)
    assert source_image.read_bytes() == b"image"
    assert not destination_image.exists()


def test_marker_free_processor_finds_every_capture_chunk() -> None:
    content = (
        "# Day\n\n## QuickCaptures\n"
        "- *9:00 am*\n  First idea\n\n---\n"
        "- *10:00 am*\n  Second idea\n\n---\n"
    )

    items = parse_quick_capture_chunks(content, "/Journal/2026/08/16/16.md")

    assert [item.text for item in items] == ["First idea", "Second idea"]
    assert items[0].raw.endswith("---")
    assert items[0].start_line < items[1].start_line


def test_move_processor_preserves_complete_chunk_and_undo(tmp_path: Path) -> None:
    source = tmp_path / "Journal" / "2026" / "08" / "16" / "16.md"
    destination = tmp_path / "Projects" / "Launch" / "Launch.md"
    source.parent.mkdir(parents=True)
    destination.parent.mkdir(parents=True)
    source.write_text(
        "# Day\n\n## QuickCaptures\n"
        "- *9:00 am*\n  First idea\n\n---\n"
        "- *10:00 am*\n  Leave this here\n\n---\n",
        encoding="utf-8",
    )
    destination.write_text("# Launch\n", encoding="utf-8")
    item = list_quick_capture_chunks(tmp_path)[0]

    receipt = process_quick_capture_chunk(
        tmp_path,
        path=item["path"],
        start_line=item["start_line"],
        expected_hash=item["expected_hash"],
        action="move",
        destination="/Projects/Launch/Launch.md",
    )

    moved = destination.read_text(encoding="utf-8")
    assert "- *9:00 am*\n  First idea\n\n---" in moved
    assert "First idea" not in source.read_text(encoding="utf-8")
    assert "Leave this here" in source.read_text(encoding="utf-8")
    undo_file_mutation(tmp_path, receipt)
    assert "First idea" in source.read_text(encoding="utf-8")


def test_task_processor_requires_destination_and_removes_capture(tmp_path: Path) -> None:
    source = tmp_path / "Journal" / "2026" / "08" / "16" / "16.md"
    destination = tmp_path / "Projects" / "Launch" / "Launch.md"
    source.parent.mkdir(parents=True)
    destination.parent.mkdir(parents=True)
    source.write_text(_page(capture_header("9:00 am"), "Call Sarah"), encoding="utf-8")
    destination.write_text("# Launch\n", encoding="utf-8")
    item = list_quick_capture_chunks(tmp_path)[0]

    process_quick_capture_chunk(
        tmp_path,
        path=item["path"],
        start_line=item["start_line"],
        expected_hash=item["expected_hash"],
        action="task",
        destination="/Projects/Launch/Launch.md",
        text=item["text"],
        priority=2,
        tags=["phone"],
    )

    assert "Call Sarah" not in source.read_text(encoding="utf-8")
    assert "☐ Call Sarah !! @phone" in destination.read_text(encoding="utf-8")


def test_move_processor_can_process_adjacent_chunks_sequentially(tmp_path: Path) -> None:
    source = tmp_path / "Captures" / "Captures.md"
    destination = tmp_path / "Filed" / "Filed.md"
    source.parent.mkdir(parents=True)
    destination.parent.mkdir(parents=True)
    source.write_text(
        "# Captures\n\n## QuickCaptures\n"
        "- *9:00 am*\n  First\n\n---\n"
        "- *10:00 am*\n  Second\n\n---\n",
        encoding="utf-8",
    )
    destination.write_text("# Filed\n", encoding="utf-8")

    for expected in ("First", "Second"):
        item = list_quick_capture_chunks(tmp_path, paths={"/Captures/Captures.md"})[0]
        assert item["text"] == expected
        process_quick_capture_chunk(
            tmp_path,
            path=item["path"],
            start_line=item["start_line"],
            expected_hash=item["expected_hash"],
            action="move",
            destination="/Filed/Filed.md",
        )

    assert list_quick_capture_chunks(tmp_path, paths={"/Captures/Captures.md"}) == []
    filed = destination.read_text(encoding="utf-8")
    assert filed.index("First") < filed.index("Second")
    assert filed.count("---") == 2
