from __future__ import annotations

from pathlib import Path

import pytest

from sp.app.task_mutations import (
    TaskConflictError,
    TaskMutationTarget,
    mutate_task,
    remove_task_indicators,
    rewrite_task_line,
    undo_file_mutation,
)


def test_rewrite_task_line_updates_all_editable_fields() -> None:
    line = "  ☐ Call Sarah ! @phone >2026-08-20 <2026-08-21\n"

    updated = rewrite_task_line(
        line,
        text="Send the proposal",
        status="done",
        priority=3,
        tags=["work", "@email"],
        start="2026-08-22",
        due="2026-08-23",
    )

    assert updated == "  ☑ Send the proposal !!! @work @email >2026-08-22 <2026-08-23\n"


def test_remove_task_indicators_preserves_plain_text_and_indentation() -> None:
    line = "  ☐ Email alex@example.com !! @phone >2026-08-20 <2026-08-21 important!\n"

    assert remove_task_indicators(line) == "  - Email alex@example.com important!\n"


def test_mutate_task_removes_indicators_and_can_be_undone(tmp_path: Path) -> None:
    page = tmp_path / "Page" / "Page.md"
    page.parent.mkdir()
    original = "# Page\n\n☐ Call Sarah !! @phone >2026-08-20 <2026-08-21\n"
    page.write_text(original, encoding="utf-8")

    receipt = mutate_task(
        tmp_path,
        TaskMutationTarget("/Page/Page.md", 3, "Call Sarah", "todo"),
        remove_indicators=True,
    )

    assert page.read_text(encoding="utf-8") == "# Page\n\n- Call Sarah\n"
    undo_file_mutation(tmp_path, receipt)
    assert page.read_text(encoding="utf-8") == original


def test_mutate_task_relocates_after_line_shift(tmp_path: Path) -> None:
    page = tmp_path / "Page" / "Page.md"
    page.parent.mkdir()
    page.write_text("# Page\n\n☐ First\n☐ Target @work\n", encoding="utf-8")
    target = TaskMutationTarget("/Page/Page.md", 3, "Target", "todo")

    result = mutate_task(tmp_path, target, status="done", due="2026-08-25")

    assert result["ok"] is True
    assert "☑ Target @work <2026-08-25" in page.read_text(encoding="utf-8")


def test_mutate_task_rejects_stale_or_ambiguous_target(tmp_path: Path) -> None:
    page = tmp_path / "Page" / "Page.md"
    page.parent.mkdir()
    page.write_text("☐ Same\n☐ Same\n", encoding="utf-8")
    target = TaskMutationTarget("/Page/Page.md", 99, "Same", "todo")

    with pytest.raises(TaskConflictError, match="More than one"):
        mutate_task(tmp_path, target, status="done")


def test_mutate_task_can_move_to_existing_page(tmp_path: Path) -> None:
    source = tmp_path / "Source" / "Source.md"
    destination = tmp_path / "Destination" / "Destination.md"
    source.parent.mkdir()
    destination.parent.mkdir()
    source.write_text("# Source\n\n☐ Move me\n", encoding="utf-8")
    destination.write_text("# Destination\n", encoding="utf-8")

    mutate_task(
        tmp_path,
        TaskMutationTarget("/Source/Source.md", 3, "Move me", "todo"),
        destination="/Destination/Destination.md",
        priority=2,
    )

    assert "Move me" not in source.read_text(encoding="utf-8")
    assert "## Tasks\n☐ Move me !!" in destination.read_text(encoding="utf-8")


def test_undo_requires_unchanged_post_action_content(tmp_path: Path) -> None:
    page = tmp_path / "Page" / "Page.md"
    page.parent.mkdir()
    page.write_text("☐ Task\n", encoding="utf-8")
    receipt = mutate_task(
        tmp_path,
        TaskMutationTarget("/Page/Page.md", 1, "Task", "todo"),
        status="done",
    )

    undo_file_mutation(tmp_path, receipt)
    assert page.read_text(encoding="utf-8") == "☐ Task\n"

    receipt = mutate_task(
        tmp_path,
        TaskMutationTarget("/Page/Page.md", 1, "Task", "todo"),
        status="done",
    )
    page.write_text("☑ Task changed\n", encoding="utf-8")
    with pytest.raises(TaskConflictError, match="changed after"):
        undo_file_mutation(tmp_path, receipt)


def test_delete_and_move_include_nested_task_block(tmp_path: Path) -> None:
    source = tmp_path / "Source" / "Source.md"
    destination = tmp_path / "Destination" / "Destination.md"
    source.parent.mkdir()
    destination.parent.mkdir()
    source.write_text("☐ Parent\n  detail\n  ☐ Child\n☐ Keep\n", encoding="utf-8")
    destination.write_text("# Destination\n", encoding="utf-8")

    mutate_task(
        tmp_path,
        TaskMutationTarget("/Source/Source.md", 1, "Parent", "todo"),
        destination="/Destination/Destination.md",
    )

    assert source.read_text(encoding="utf-8") == "☐ Keep\n"
    moved = destination.read_text(encoding="utf-8")
    assert "☐ Parent\n  detail\n  ☐ Child" in moved

    mutate_task(
        tmp_path,
        TaskMutationTarget("/Source/Source.md", 1, "Keep", "todo"),
        delete=True,
    )
    assert source.read_text(encoding="utf-8") == ""
