from sp.server.adapters.tasks import extract_tasks


def sample_md():
    return "\n".join(
        [
            "# Tasks",
            "- [ ] write spec {due:2025-01-10 priority:high @product}",
            "- [x] ship feature #done",
            "- [ ] research idea",
        ]
    )


def test_extract_tasks_parses_metadata():
    tasks = extract_tasks(sample_md(), "/Notes/demo/demo.md")
    assert len(tasks) == 3

    todo = tasks[0]
    assert todo.due == "2025-01-10"
    assert "@product" in todo.tags
    assert todo.priority == "high"

    done = tasks[1]
    assert done.done is True
    assert "#done" in done.tags


def test_extract_tasks_handles_invalid_due():
    md = "- [ ] task {due:not-a-date}"
    tasks = extract_tasks(md, "/foo/foo.md")
    assert tasks[0].due is None
