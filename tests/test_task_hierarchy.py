from __future__ import annotations

import pytest

from sp.app import config, indexer


@pytest.fixture
def temp_db(tmp_path):
    """Create a temporary vault database for task parsing assertions."""
    config.set_active_vault(str(tmp_path))
    yield
    config.set_active_vault(None)


def test_extract_tasks_builds_hierarchy_and_inheritance() -> None:
    path = "/Party/Party.md"
    content = """
- [ ] Organize party <2017-08-19 !!
    - [ ] Send invitations by first of month <2017-08-01 !!
    - [ ] Cleanup living room
        - [ ] Get rid of moving boxes <2017-08-10
        - [ ] Buy vacuum cleaner <2017-08-15
    - [ ] Buy food & drinks
""".strip()
    tasks = indexer.extract_tasks(path, content)
    by_text = {t["text"]: t for t in tasks}

    assert by_text["Organize party"]["parent"] is None
    assert by_text["Organize party"]["actionable"] is False

    cleanup = by_text["Cleanup living room"]
    assert cleanup["parent"] == by_text["Organize party"]["id"]
    assert cleanup["priority"] == 2  # Inherits from parent (!!)
    assert cleanup["due"] == "2017-08-19"  # Inherits from parent
    assert cleanup["actionable"] is False  # Children still open

    send_invitations = by_text["Send invitations by first of month"]
    assert send_invitations["priority"] == 2  # Explicit !!
    assert send_invitations["due"] == "2017-08-01"  # Explicit earlier due
    assert send_invitations["actionable"] is True

    buy_food = by_text["Buy food & drinks"]
    assert buy_food["priority"] == 2
    assert buy_food["due"] == "2017-08-19"
    assert buy_food["actionable"] is True


def test_parent_becomes_actionable_when_children_done() -> None:
    path = "/Cleanup/Cleanup.md"
    content = """
- [ ] Cleanup living room <2017-08-19 !!
    - [x] Get rid of moving boxes <2017-08-10
    - [x] Buy vacuum cleaner <2017-08-15
""".strip()
    tasks = indexer.extract_tasks(path, content)
    by_text = {t["text"]: t for t in tasks}

    assert by_text["Cleanup living room"]["actionable"] is True
    assert by_text["Cleanup living room"]["priority"] == 2
    assert by_text["Cleanup living room"]["due"] == "2017-08-19"


def test_fetch_tasks_includes_ancestors_for_actionable_tasks(temp_db) -> None:
    path = "/Party/Party.md"
    content = """
- [ ] Organize party <2017-08-19 !!
    - [ ] Send invitations by first of month <2017-08-01 !!
    - [ ] Cleanup living room
        - [ ] Get rid of moving boxes <2017-08-10
        - [ ] Buy vacuum cleaner <2017-08-15
    - [ ] Buy food & drinks
""".strip()
    tasks = indexer.extract_tasks(path, content)
    config.update_page_index(path=path, title="Party", tags=[], links=[], tasks=tasks)

    fetched = config.fetch_tasks(include_done=False, include_ancestors=True, actionable_only=True)
    names = {t["text"]: t for t in fetched}

    assert names["Send invitations by first of month"]["actionable"] is True
    assert names["Get rid of moving boxes"]["actionable"] is True
    assert names["Organize party"]["actionable"] is False  # Included as ancestor context
    assert any(t.get("parent") == names["Organize party"]["id"] for t in fetched)


def test_tag_search_shows_non_actionable_matches(temp_db) -> None:
    """Searching by tag should surface matching parents even if they are not actionable."""
    path = "/Todos/Todos.md"
    content = """
- [ ] task one @todo
    - [ ] gimme a break
    - [ ] get some milk @wt
""".strip()
    tasks = indexer.extract_tasks(path, content)
    config.update_page_index(path=path, title="Todos", tags=[], links=[], tasks=tasks)

    # Default "active" view: actionable leaves are included, ancestors come along for context
    active_only = config.fetch_tasks(include_done=False, include_ancestors=True, actionable_only=True)
    names_active = {t["text"]: t for t in active_only}
    assert names_active["task one"]["actionable"] is False
    assert names_active["gimme a break"]["actionable"] is True

    # When filtering by tag, matching parents should appear even if not actionable
    filtered = config.fetch_tasks(
        query="",
        tags=["todo"],
        include_done=False,
        include_ancestors=True,
        actionable_only=False,
    )
    texts_filtered = {t["text"] for t in filtered}
    assert "task one" in texts_filtered


def test_actionable_filter_respects_non_actionable_tags(temp_db, monkeypatch) -> None:
    """Configured non-actionable tags should be excluded from actionable view."""
    monkeypatch.setattr(config, "load_non_actionable_task_tags", lambda: "@wt")
    path = "/Todos/Todos.md"
    content = """
- [ ] waiting around @wt
- [ ] do now
""".strip()
    tasks = indexer.extract_tasks(path, content)
    config.update_page_index(path=path, title="Todos", tags=[], links=[], tasks=tasks)

    actionable = config.fetch_tasks(include_done=False, include_ancestors=True, actionable_only=True)
    names_actionable = {t["text"] for t in actionable}

    assert "waiting around" not in names_actionable
    assert "do now" in names_actionable
