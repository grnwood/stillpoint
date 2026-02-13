from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable, List, Optional

META_PATTERN = re.compile(r"\{([^}]*)\}\s*$")
TAG_PATTERN = re.compile(r"(?:^|\s)(#\w+|@\w+)")
# Tasks: support markdown checkboxes "- [ ]" and "- [x]"
TASK_PATTERN = re.compile(
    r"^(?P<indent>\s*)"
    r"(?:[-*]\s*\[(?P<state1>[ xX])\])"
    r"\s+(?P<body>.*)$"
)


@dataclass
class Task:
    id: str
    path: str
    line: int
    text: str
    done: bool
    due: Optional[str]
    priority: Optional[Any]
    tags: List[str]
    status: str = "todo"
    parent: Optional[str] = None
    level: int = 0
    actionable: bool = True

    def __getitem__(self, key: str):
        return getattr(self, key)


def _parse_meta(meta_blob: str) -> dict:
    fields = {}
    for chunk in meta_blob.split():
        if ":" in chunk:
            key, value = chunk.split(":", 1)
            fields[key.strip()] = value.strip()
        elif chunk.startswith("@") or chunk.startswith("#"):
            fields.setdefault("tags", []).append(chunk)
    return fields


def extract_tasks(markdown: str, path: str) -> List[Task]:
    items: List[Task] = []
    stack: list[tuple[int, str]] = []
    for idx, line in enumerate(markdown.splitlines(), start=1):
        match = TASK_PATTERN.match(line)
        if not match:
            continue
        indent = len((match.group("indent") or "").replace("\t", "    "))
        while stack and stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1] if stack else None
        level = len(stack)
        state = match.group("state1") or " "
        remainder = match.group("body") or ""
        meta_match = META_PATTERN.search(remainder)
        meta = {}
        if meta_match:
            meta = _parse_meta(meta_match.group(1))
            remainder = remainder[: meta_match.start()].rstrip()
        tags = set(meta.get("tags", []))
        tags.update(tag.strip() for tag in TAG_PATTERN.findall(remainder))
        due_value = meta.get("due")
        if due_value:
            try:
                _ = date.fromisoformat(due_value)
            except ValueError:
                due_value = None
        priority = (meta.get("priority") or "").strip().lower() or None
        pri_matches = re.findall(r"!{1,3}", remainder)
        if priority is None:
            priority = min(max((len(m) for m in pri_matches), default=0), 3)
        remainder = re.sub(r"!{1,3}", " ", remainder)
        remainder = re.sub(r"\s{2,}", " ", remainder).strip()
        done = state.lower() == "x"
        task = Task(
            id=f"{path}:{idx}",
            path=path,
            line=idx,
            text=remainder,
            done=done,
            due=due_value,
            priority=priority,
            tags=sorted(tags),
            status="done" if done else "todo",
            parent=parent,
            level=level,
            actionable=not done,
        )
        items.append(task)
        stack.append((indent, task.id))
    return items


def aggregate_tasks(files: Iterable[tuple[str, str]]) -> List[Task]:
    tasks: List[Task] = []
    for path, content in files:
        tasks.extend(extract_tasks(content, path))
    return tasks
