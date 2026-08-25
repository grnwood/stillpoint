from __future__ import annotations

from pathlib import Path

from sp.app.capture_triage import capture_header
from sp.server import api


def test_task_mutation_api_and_undo(tmp_path: Path, monkeypatch) -> None:
    page = tmp_path / "Page" / "Page.md"
    page.parent.mkdir()
    page.write_text("# Page\n\n☐ Call Sarah\n", encoding="utf-8")
    monkeypatch.setattr(api, "_get_vault_root", lambda: tmp_path)
    monkeypatch.setattr(api, "_clear_task_cache", lambda: None)

    result = api.api_mutate_tasks(
        api.TaskMutationPayload(
            targets=[
                api.TaskMutationTargetPayload(
                    path="/Page/Page.md",
                    line=3,
                    expected_text="Call Sarah",
                    expected_status="todo",
                )
            ],
            status="done",
            priority=2,
        ),
        user=None,
    )

    assert "☑ Call Sarah !!" in page.read_text(encoding="utf-8")
    assert result["undo_id"]

    api.api_undo_task_mutation(result["undo_id"], user=None)
    assert page.read_text(encoding="utf-8") == "# Page\n\n☐ Call Sarah\n"


def test_task_mutation_api_removes_task_indicators(tmp_path: Path, monkeypatch) -> None:
    page = tmp_path / "Page" / "Page.md"
    page.parent.mkdir()
    page.write_text(
        "# Page\n\n☐ Call Sarah !! @phone >2026-08-20 <2026-08-21\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(api, "_get_vault_root", lambda: tmp_path)
    monkeypatch.setattr(api, "_clear_task_cache", lambda: None)

    result = api.api_mutate_tasks(
        api.TaskMutationPayload(
            targets=[
                api.TaskMutationTargetPayload(
                    path="/Page/Page.md",
                    line=3,
                    expected_text="Call Sarah",
                    expected_status="todo",
                )
            ],
            remove_indicators=True,
        ),
        user=None,
    )

    assert page.read_text(encoding="utf-8") == "# Page\n\n- Call Sarah\n"
    api.api_undo_task_mutation(result["undo_id"], user=None)
    assert "☐ Call Sarah !! @phone" in page.read_text(encoding="utf-8")


def test_triage_api_lists_and_processes_capture(tmp_path: Path, monkeypatch) -> None:
    journal = tmp_path / "Journal" / "2026" / "08" / "15" / "15.md"
    journal.parent.mkdir(parents=True)
    journal.write_text(
        "# Day\n\n## QuickCaptures\n"
        + capture_header("10:42 am", capture_id="capture-api", inbox=True)
        + "\n  Call Sarah\n\n---\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(api, "_get_vault_root", lambda: tmp_path)
    monkeypatch.setattr(api, "_clear_task_cache", lambda: None)

    listed = api.api_task_triage(user=None)
    item = listed["items"][0]
    result = api.api_process_task_triage(
        api.TriageProcessPayload(
            path=item["path"],
            item_id=item["id"],
            expected_hash=item["expected_hash"],
            action="task",
            text=item["text"],
        ),
        user=None,
    )

    assert result["undo_id"]
    assert "☐ Call Sarah" in journal.read_text(encoding="utf-8")
    assert api.api_task_triage(user=None)["count"] == 0


def test_marker_free_quick_capture_api_move_and_undo(tmp_path: Path, monkeypatch) -> None:
    journal = tmp_path / "Journal" / "2026" / "08" / "16" / "16.md"
    destination = tmp_path / "Projects" / "Launch" / "Launch.md"
    journal.parent.mkdir(parents=True)
    destination.parent.mkdir(parents=True)
    journal.write_text(
        "# Day\n\n## QuickCaptures\n- *9:00 am*\n  File this\n\n---\n",
        encoding="utf-8",
    )
    destination.write_text("# Launch\n", encoding="utf-8")
    monkeypatch.setattr(api, "_get_vault_root", lambda: tmp_path)
    monkeypatch.setattr(api, "_clear_task_cache", lambda: None)

    listed = api.api_quick_capture_chunks(user=None)
    item = listed["items"][0]
    result = api.api_process_quick_capture_chunk(
        api.QuickCaptureProcessPayload(
            path=item["path"],
            start_line=item["start_line"],
            expected_hash=item["expected_hash"],
            action="move",
            destination="/Projects/Launch/Launch.md",
        ),
        user=None,
    )

    assert "File this" in destination.read_text(encoding="utf-8")
    assert api.api_quick_capture_chunks(user=None)["count"] == 0
    api.api_undo_task_mutation(result["undo_id"], user=None)
    assert "File this" in journal.read_text(encoding="utf-8")
