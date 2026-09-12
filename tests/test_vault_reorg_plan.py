from __future__ import annotations

import pytest

from sp.app.ui.vault_reorg_plan import StagingPlanHistory


def test_plan_history_undo_redo_and_branching() -> None:
    history = StagingPlanHistory()
    assert history.perform("Stage A", lambda plan: plan.append({"source_path": "/A"}))
    assert history.perform("Stage B", lambda plan: plan.append({"source_path": "/B"}))

    assert history.undo() == "Stage B"
    assert history.plan == [{"source_path": "/A"}]
    assert history.redo() == "Stage B"
    assert [item["source_path"] for item in history.plan] == ["/A", "/B"]

    history.undo()
    history.perform("Stage C", lambda plan: plan.append({"source_path": "/C"}))
    assert history.can_redo is False
    assert [item["source_path"] for item in history.plan] == ["/A", "/C"]


def test_plan_history_treats_batch_as_one_command() -> None:
    history = StagingPlanHistory()
    history.perform(
        "Stage 3 pages",
        lambda plan: plan.extend({"source_path": f"/{name}"} for name in ("A", "B", "C")),
    )

    assert history.command_count == 1
    assert history.undo() == "Stage 3 pages"
    assert history.plan == []
    assert history.redo() == "Stage 3 pages"
    assert len(history.plan) == 3


def test_plan_history_bounds_commands_without_splitting_latest() -> None:
    history = StagingPlanHistory(max_commands=2, max_bytes=1024)
    for name in ("A", "B", "C"):
        history.perform(
            f"Stage {name}",
            lambda plan, value=name: plan.append(
                {"source_path": f"/{value}", "payload": value * 800}
            ),
        )

    assert history.command_count == 1
    assert history.undo() == "Stage C"
    assert [item["source_path"] for item in history.plan] == ["/A", "/B"]


def test_noop_does_not_create_history() -> None:
    history = StagingPlanHistory()

    assert history.perform("No-op", lambda _plan: None) is False
    assert history.can_undo is False


def test_failed_compound_mutation_is_atomic() -> None:
    history = StagingPlanHistory()
    history.plan.append({"source_path": "/A"})

    def fail_midway(plan: list[dict]) -> None:
        plan.append({"source_path": "/B"})
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        history.perform("Broken compound edit", fail_midway)

    assert history.plan == [{"source_path": "/A"}]
    assert not history.can_undo
    assert not history.can_redo
