from sp.server.adapters.tasks import extract_tasks

def sample_nested_md():
    return "\n".join([
        "# Nested Tasks",
        "- [ ] parent task",
        "    - [ ] child task 1",
        "    - [x] child task 2",
        "        - [ ] grandchild task",
        "- [x] another parent",
    ])

def test_extract_tasks_nested_structure():
    tasks = extract_tasks(sample_nested_md(), "/Notes/demo/nested.md")
    # Find parent and children by text
    parent = next(t for t in tasks if t["text"] == "parent task")
    child1 = next(t for t in tasks if t["text"] == "child task 1")
    child2 = next(t for t in tasks if t["text"] == "child task 2")
    grandchild = next(t for t in tasks if t["text"] == "grandchild task")
    another_parent = next(t for t in tasks if t["text"] == "another parent")

    # Parent has no parent
    assert parent["parent"] is None
    # Children have parent as their parent
    assert child1["parent"] == parent["id"]
    assert child2["parent"] == parent["id"]
    # Grandchild has child2 as parent
    assert grandchild["parent"] == child2["id"]
    # Another parent is top-level
    assert another_parent["parent"] is None
    # Levels
    assert parent["level"] == 0
    assert child1["level"] == 1
    assert child2["level"] == 1
    assert grandchild["level"] == 2
    assert another_parent["level"] == 0
    # Status
    assert parent["status"] == "todo"
    assert child2["status"] == "done"
    assert grandchild["status"] == "todo"
