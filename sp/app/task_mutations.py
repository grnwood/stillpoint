from __future__ import annotations

import hashlib
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from sp.app import config, indexer
from sp.server.adapters import files


TASK_LINE_RE = re.compile(
    r"^(?P<indent>\s*)(?P<marker>☐|☑|[-*]\s*\[(?P<state>[ xX])\])\s+(?P<body>.*?)(?P<newline>\n?)$"
)
TAG_RE = re.compile(r"(?<![\w.+-])@[A-Za-z0-9_]+")
PRIORITY_RE = re.compile(r"(?<!\S)!{1,3}(?!\S)")
DATE_RE = re.compile(r"(?<!\S)[<>][0-9]{4}-[0-9]{2}-[0-9]{2}(?!\S)")


class TaskConflictError(ValueError):
    pass


@dataclass(frozen=True)
class TaskMutationTarget:
    path: str
    line: int
    expected_text: str
    expected_status: str


def line_hash(line: str) -> str:
    return hashlib.sha256(line.rstrip("\n").encode("utf-8")).hexdigest()


def _normalized_path(path: str) -> str:
    cleaned = str(path or "").replace("\\", "/").strip()
    return "/" + cleaned.lstrip("/")


def _parsed_task_at(path: str, line: str) -> Optional[dict]:
    parsed = indexer.extract_tasks(path, line.rstrip("\n"))
    return parsed[0] if parsed else None


def _matches_expected(path: str, line: str, target: TaskMutationTarget) -> bool:
    parsed = _parsed_task_at(path, line)
    if not parsed:
        return False
    return (
        str(parsed.get("text") or "").strip() == str(target.expected_text or "").strip()
        and str(parsed.get("status") or "todo") == str(target.expected_status or "todo")
    )


def _locate_task_line(lines: list[str], target: TaskMutationTarget) -> int:
    hinted = max(int(target.line or 1), 1) - 1
    if hinted < len(lines) and _matches_expected(target.path, lines[hinted], target):
        return hinted
    matches = [
        index
        for index, line in enumerate(lines)
        if _matches_expected(target.path, line, target)
    ]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise TaskConflictError("The task changed or can no longer be found. Reload Tasks and try again.")
    raise TaskConflictError("More than one matching task was found. Open the source page to edit it safely.")


def _clean_task_body(body: str) -> str:
    cleaned = TAG_RE.sub(" ", body)
    cleaned = PRIORITY_RE.sub(" ", cleaned)
    cleaned = DATE_RE.sub(" ", cleaned)
    return re.sub(r"\s{2,}", " ", cleaned).strip()


def remove_task_indicators(line: str) -> str:
    """Convert a task line to a plain dash item without task metadata."""
    match = TASK_LINE_RE.match(line)
    if not match:
        raise TaskConflictError("The selected source line is no longer a task.")
    body = _clean_task_body(match.group("body") or "")
    return f"{match.group('indent')}- {body}{match.group('newline')}"


def rewrite_task_line(
    line: str,
    *,
    text: Optional[str] = None,
    status: Optional[str] = None,
    priority: Optional[int] = None,
    tags: Optional[list[str]] = None,
    start: Optional[str] = None,
    due: Optional[str] = None,
) -> str:
    match = TASK_LINE_RE.match(line)
    if not match:
        raise TaskConflictError("The selected source line is no longer a task.")
    original_body = match.group("body") or ""
    parsed = _parsed_task_at("/task.md", line) or {}
    body_text = (
        str(parsed.get("text") or _clean_task_body(original_body)).strip()
        if text is None
        else str(text).strip()
    )
    if not body_text:
        raise ValueError("Task text cannot be empty.")
    if status is None:
        marker = match.group("marker")
    else:
        done = status == "done"
        original_marker = match.group("marker")
        if original_marker in ("☐", "☑"):
            marker = "☑" if done else "☐"
        else:
            bullet = original_marker[0] if original_marker else "-"
            marker = f"{bullet} [{'x' if done else ' '}]"
    if priority is None:
        priority_value = int(parsed.get("priority") or 0)
    else:
        priority_value = max(0, min(int(priority), 3))
    if tags is None:
        tag_values = ["@" + str(value).lstrip("@") for value in parsed.get("tags") or []]
    else:
        tag_values = []
        for raw in tags:
            value = str(raw or "").strip()
            if not value:
                continue
            value = "@" + value.lstrip("@")
            if value not in tag_values:
                tag_values.append(value)
    if start is None:
        start_value = str(parsed.get("start") or "")
    else:
        start_value = str(start).strip()
    if due is None:
        due_value = str(parsed.get("due") or "")
    else:
        due_value = str(due).strip()
    parts = [body_text]
    if priority_value:
        parts.append("!" * priority_value)
    parts.extend(tag_values)
    if start_value:
        parts.append(f">{start_value}")
    if due_value:
        parts.append(f"<{due_value}")
    return f"{match.group('indent')}{marker} {' '.join(parts)}{match.group('newline')}"


def _append_under_tasks(content: str, task_line: str) -> str:
    lines = content.splitlines()
    insert_at = len(lines)
    header_idx = next(
        (index for index, line in enumerate(lines) if line.strip().casefold() == "## tasks"),
        -1,
    )
    if header_idx >= 0:
        for index in range(header_idx + 1, len(lines)):
            if re.match(r"^#{1,2}\s+", lines[index]):
                insert_at = index
                break
        lines.insert(insert_at, task_line.rstrip("\n"))
        result = "\n".join(lines)
        return result if result.endswith("\n") else result + "\n"
    trimmed = content.rstrip("\n")
    spacer = "\n\n" if trimmed else ""
    return f"{trimmed}{spacer}## Tasks\n{task_line.rstrip()}\n"


def _task_block_end(lines: list[str], start: int) -> int:
    match = TASK_LINE_RE.match(lines[start])
    if not match:
        return start + 1
    base_indent = len((match.group("indent") or "").replace("\t", "    "))
    end = start + 1
    while end < len(lines):
        line = lines[end]
        if not line.strip():
            end += 1
            continue
        child = TASK_LINE_RE.match(line)
        indent = len((line[: len(line) - len(line.lstrip())]).replace("\t", "    "))
        if child and indent > base_indent:
            end += 1
            continue
        if indent > base_indent and not line.lstrip().startswith("#"):
            end += 1
            continue
        break
    return end


def mutate_task(
    vault_root: Path,
    target: TaskMutationTarget,
    *,
    text: Optional[str] = None,
    status: Optional[str] = None,
    priority: Optional[int] = None,
    tags: Optional[list[str]] = None,
    start: Optional[str] = None,
    due: Optional[str] = None,
    destination: Optional[str] = None,
    delete: bool = False,
    remove_indicators: bool = False,
) -> dict:
    source_path = _normalized_path(target.path)
    content = files.read_file(vault_root, source_path)
    lines = content.splitlines(keepends=True)
    line_index = _locate_task_line(lines, target)
    before_line = lines[line_index]
    block_end = _task_block_end(lines, line_index)
    original_tail = lines[line_index + 1 : block_end]
    updated_line = ""
    if remove_indicators:
        updated_line = remove_task_indicators(before_line)
    elif not delete:
        updated_line = rewrite_task_line(
            before_line,
            text=text,
            status=status,
            priority=priority,
            tags=tags,
            start=start,
            due=due,
        )
    destination_path = _normalized_path(destination) if destination else source_path
    before_files = {source_path: content}
    if delete:
        del lines[line_index:block_end]
        after_files = {source_path: "".join(lines)}
    elif destination_path == source_path:
        lines[line_index] = updated_line
        after_files = {source_path: "".join(lines)}
    else:
        destination_content = files.read_file(vault_root, destination_path)
        before_files[destination_path] = destination_content
        del lines[line_index:block_end]
        moved_block = updated_line + "".join(original_tail)
        after_files = {
            source_path: "".join(lines),
            destination_path: _append_under_tasks(destination_content, moved_block),
        }
    written: list[str] = []
    try:
        for path, new_content in after_files.items():
            files.write_file(vault_root, path, new_content)
            written.append(path)
    except Exception:
        for path in written:
            files.write_file(vault_root, path, before_files[path])
        raise
    for path, new_content in after_files.items():
        try:
            indexer.index_page(path, new_content)
        except Exception:
            pass
    return {
        "ok": True,
        "paths": list(after_files),
        "before": before_files,
        "after": after_files,
        "line_hash": line_hash(updated_line),
    }


def undo_file_mutation(vault_root: Path, receipt: dict) -> dict:
    before = dict(receipt.get("before") or {})
    after = dict(receipt.get("after") or {})
    if not before or set(before) != set(after):
        raise ValueError("This change has no valid undo information.")
    for path, expected_content in after.items():
        current = files.read_file(vault_root, path)
        if current != expected_content:
            raise TaskConflictError(
                f"{path} changed after the action, so undo was not applied."
            )
    attachment_moves = list(receipt.get("attachment_moves") or [])
    for move in attachment_moves:
        source = Path(str(move.get("source") or ""))
        destination = Path(str(move.get("destination") or ""))
        if not destination.is_file() or source.exists():
            raise TaskConflictError(
                "An attachment changed after the action, so undo was not applied."
            )
    for path, original_content in before.items():
        files.write_file(vault_root, path, original_content)
        try:
            indexer.index_page(path, original_content)
        except Exception:
            pass
    for move in reversed(attachment_moves):
        shutil.move(str(move["destination"]), str(move["source"]))
        try:
            source_rel = "/" + Path(move["source"]).relative_to(vault_root).as_posix()
            destination_rel = "/" + Path(move["destination"]).relative_to(vault_root).as_posix()
            config.delete_attachment_entry(destination_rel)
            config.upsert_attachment_entry(
                str(move.get("source_page") or ""), source_rel, str(move["source"])
            )
        except Exception:
            pass
    return {"ok": True, "paths": list(before)}
