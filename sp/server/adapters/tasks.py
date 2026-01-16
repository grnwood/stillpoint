from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Iterable, List, Optional

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
    priority: Optional[str]
    tags: List[str]


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
    for idx, line in enumerate(markdown.splitlines(), start=1):
        match = TASK_PATTERN.match(line)
        if not match:
            continue
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
        pri_matches = re.findall(r"!{1,3}", remainder)
        priority = min(max((len(m) for m in pri_matches), default=0), 3)
        remainder = re.sub(r"!{1,3}", " ", remainder)
        remainder = re.sub(r"\s{2,}", " ", remainder).strip()
        task = Task(
            id=f"{path}:{idx}",
            path=path,
            line=idx,
            text=remainder,
            done=state.lower() == "x",
            due=due_value,
            priority=priority,
            tags=sorted(tags),
        )
        items.append(task)
    return items


def aggregate_tasks(files: Iterable[tuple[str, str]]) -> List[Task]:
    tasks: List[Task] = []
    for path, content in files:
        tasks.extend(extract_tasks(content, path))
    return tasks
