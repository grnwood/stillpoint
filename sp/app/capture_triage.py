from __future__ import annotations

import hashlib
import re
import shutil
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal, Optional

from sp.app import config, indexer
from sp.app.quickcapture_common import quick_capture_section_ranges
from sp.app.task_mutations import _append_under_tasks
from sp.server.adapters import files
from sp.server.adapters.files import PAGE_SUFFIXES


CAPTURE_ID_RE = re.compile(r"<!--\s*sp:capture:(?P<id>[A-Za-z0-9_-]+)\s*-->")
CAPTURE_HEADER_RE = re.compile(r"^-\s+\*(?P<timestamp>[^*]+)\*(?P<tail>.*)$")
INBOX_TAG_RE = re.compile(r"(?<![A-Za-z0-9_])@inbox\b", re.IGNORECASE)
ACTION_RE = re.compile(
    r"^\s*(?:please\s+)?(?:call|email|message|text|ask|buy|book|schedule|send|"
    r"review|write|draft|finish|fix|update|create|make|check|research|investigate|"
    r"follow\s+up|pay|order|contact|plan|decide)\b",
    re.IGNORECASE,
)
TASK_MARKER_RE = re.compile(r"^\s*(?:☐|☑|[-*]\s*\[[ xX]\])")
PAGE_HINT_RE = re.compile(r"(?<!\S):(?P<page>[A-Za-z0-9_][A-Za-z0-9_:\- ]*)")
RELATIVE_ATTACHMENT_RE = re.compile(r"(?P<prefix>!?)\[(?P<label>[^\]]*)\]\(\./(?P<name>[^)]+)\)")
_TRIAGE_CACHE: dict[str, tuple[float, list[dict]]] = {}
_TRIAGE_CACHE_LOCK = threading.Lock()
_TRIAGE_CACHE_TTL = 2.0


class TriageConflictError(ValueError):
    pass


@dataclass(frozen=True)
class TriageItem:
    id: str
    path: str
    start_line: int
    end_line: int
    timestamp: str
    text: str
    expected_hash: str
    implicit: bool = False

    def to_dict(self) -> dict:
        recommendation = suggest_triage_outcomes(self.text)[0]
        return {
            "id": self.id,
            "path": self.path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "timestamp": self.timestamp,
            "text": self.text,
            "expected_hash": self.expected_hash,
            "implicit": self.implicit,
            "kind": "capture",
            "recommendation": recommendation,
        }


@dataclass(frozen=True)
class QuickCaptureChunk:
    """One marker-free capture bounded by the QuickCaptures section/rules."""

    id: str
    path: str
    start_line: int
    end_line: int
    timestamp: str
    text: str
    raw: str
    expected_hash: str

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "path": self.path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "timestamp": self.timestamp,
            "text": self.text,
            "raw": self.raw,
            "expected_hash": self.expected_hash,
            "kind": "quick_capture",
        }


def new_capture_id() -> str:
    return uuid.uuid4().hex


def capture_header(timestamp: str, *, capture_id: Optional[str] = None, inbox: bool = False) -> str:
    suffix: list[str] = []
    if inbox:
        suffix.append("@inbox")
    if capture_id:
        suffix.append(f"<!-- sp:capture:{capture_id} -->")
    return f"- *{timestamp}*" + (f" {' '.join(suffix)}" if suffix else "")


def suggest_triage_outcomes(text: str) -> list[dict]:
    value = str(text or "").strip()
    suggestions: list[dict] = []
    page_hint = PAGE_HINT_RE.search(value)
    if TASK_MARKER_RE.search(value):
        suggestions.append({"action": "task", "label": "Make Task", "reason": "Already uses task syntax."})
    elif ACTION_RE.search(value):
        suggestions.append({"action": "task", "label": "Make Task", "reason": "Starts with an action verb."})
    elif page_hint:
        suggestions.append(
            {
                "action": "file",
                "label": "File as Note",
                "reason": "Contains a page reference.",
                "destination": ":" + page_hint.group("page").strip(),
            }
        )
    suggestions.append(
        {
            "action": "note",
            "label": "Keep as Note",
            "reason": "Safe default; preserves the capture in place.",
        }
    )
    seen: set[str] = set()
    unique: list[dict] = []
    for item in suggestions:
        action = str(item["action"])
        if action in seen:
            continue
        seen.add(action)
        unique.append(item)
    return unique


def _block_hash(lines: list[str]) -> str:
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _is_inbox_page(path: str) -> bool:
    return PurePosixPath(path.replace("\\", "/")).stem.casefold() == "inbox"


def parse_triage_items(content: str, path: str) -> list[TriageItem]:
    lines = content.splitlines()
    items: list[TriageItem] = []
    implicit_page = _is_inbox_page(path)
    for section_start, section_end in quick_capture_section_ranges(lines):
        starts = [
            index
            for index in range(section_start + 1, section_end)
            if CAPTURE_HEADER_RE.match(lines[index])
        ]
        for offset, start in enumerate(starts):
            end = starts[offset + 1] if offset + 1 < len(starts) else section_end
            raw_block = lines[start:end]
            block = list(raw_block)
            while block and (not block[-1].strip() or block[-1].strip() == "---"):
                block.pop()
            if not block:
                continue
            header = CAPTURE_HEADER_RE.match(block[0])
            if not header:
                continue
            tail = header.group("tail") or ""
            explicit = bool(INBOX_TAG_RE.search(tail))
            if not explicit and not implicit_page:
                continue
            id_match = CAPTURE_ID_RE.search(tail)
            item_id = id_match.group("id") if id_match else hashlib.sha256(
                f"{path}:{start + 1}:{'|'.join(block)}".encode("utf-8")
            ).hexdigest()[:24]
            body_lines = [re.sub(r"^ {2}", "", value) for value in block[1:]]
            while body_lines and not body_lines[-1].strip():
                body_lines.pop()
            items.append(
                TriageItem(
                    id=item_id,
                    path=path,
                    start_line=start + 1,
                    end_line=end,
                    timestamp=(header.group("timestamp") or "").strip(),
                    text="\n".join(body_lines).strip(),
                    expected_hash=_block_hash(raw_block),
                    implicit=implicit_page and not explicit,
                )
            )
    return items


def parse_quick_capture_chunks(content: str, path: str) -> list[QuickCaptureChunk]:
    """Return every capture in a QuickCaptures section, without marker state."""
    lines = content.splitlines()
    items: list[QuickCaptureChunk] = []
    for section_start, section_end in quick_capture_section_ranges(lines):
        starts = [
            index
            for index in range(section_start + 1, section_end)
            if CAPTURE_HEADER_RE.match(lines[index])
        ]
        for offset, start in enumerate(starts):
            end = starts[offset + 1] if offset + 1 < len(starts) else section_end
            raw_lines = list(lines[start:end])
            while raw_lines and not raw_lines[-1].strip():
                raw_lines.pop()
            if not raw_lines:
                continue
            header = CAPTURE_HEADER_RE.match(raw_lines[0])
            if not header:
                continue
            body_lines = list(raw_lines[1:])
            while body_lines and (not body_lines[-1].strip() or body_lines[-1].strip() == "---"):
                body_lines.pop()
            body_lines = [re.sub(r"^ {2}", "", value) for value in body_lines]
            digest = _block_hash(raw_lines)
            items.append(
                QuickCaptureChunk(
                    id=hashlib.sha256(
                        f"{path}:{start + 1}:{digest}".encode("utf-8")
                    ).hexdigest()[:24],
                    path=path,
                    start_line=start + 1,
                    end_line=start + len(raw_lines),
                    timestamp=(header.group("timestamp") or "").strip(),
                    text="\n".join(body_lines).strip(),
                    raw="\n".join(raw_lines),
                    expected_hash=digest,
                )
            )
    return items


def list_quick_capture_chunks(
    vault_root: Path,
    *,
    paths: Optional[set[str]] = None,
) -> list[dict]:
    """Discover marker-free capture chunks, optionally within explicit pages."""
    candidates: list[Path] = []
    if paths is not None:
        for raw_path in sorted(paths):
            candidate = vault_root / str(raw_path).lstrip("/")
            if candidate.is_file() and candidate.suffix.lower() in PAGE_SUFFIXES:
                candidates.append(candidate)
    else:
        for candidate in vault_root.rglob("*"):
            if not candidate.is_file() or candidate.suffix.lower() not in PAGE_SUFFIXES:
                continue
            try:
                relative = candidate.relative_to(vault_root)
            except ValueError:
                continue
            if relative.parts and relative.parts[0].startswith("."):
                continue
            candidates.append(candidate)
    result: list[QuickCaptureChunk] = []
    for candidate in candidates:
        try:
            path = "/" + candidate.relative_to(vault_root).as_posix()
            content = candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeError, ValueError):
            continue
        result.extend(parse_quick_capture_chunks(content, path))
    result.sort(key=lambda item: (item.path, item.start_line))
    return [item.to_dict() for item in result]


def invalidate_triage_cache(vault_root: Optional[Path] = None) -> None:
    with _TRIAGE_CACHE_LOCK:
        if vault_root is None:
            _TRIAGE_CACHE.clear()
        else:
            _TRIAGE_CACHE.pop(str(vault_root.resolve()), None)


def list_triage_items(vault_root: Path, *, force: bool = False) -> list[dict]:
    cache_key = str(vault_root.resolve())
    now = time.monotonic()
    with _TRIAGE_CACHE_LOCK:
        cached = _TRIAGE_CACHE.get(cache_key)
        if not force and cached and now - cached[0] <= _TRIAGE_CACHE_TTL:
            return [dict(item) for item in cached[1]]
    result: list[TriageItem] = []
    for candidate in vault_root.rglob("*"):
        if not candidate.is_file() or candidate.suffix.lower() not in PAGE_SUFFIXES:
            continue
        try:
            relative = candidate.relative_to(vault_root)
        except ValueError:
            continue
        if relative.parts and relative.parts[0].startswith("."):
            continue
        path = "/" + relative.as_posix()
        if not (path.startswith("/Journal/") or _is_inbox_page(path)):
            continue
        try:
            content = candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        result.extend(parse_triage_items(content, path))
    result.sort(key=lambda item: (item.path, item.start_line))
    serialized = [item.to_dict() for item in result]
    with _TRIAGE_CACHE_LOCK:
        _TRIAGE_CACHE[cache_key] = (now, serialized)
    return [dict(item) for item in serialized]


def _locate_item(content: str, path: str, item_id: str, expected_hash: str) -> TriageItem:
    items = parse_triage_items(content, path)
    matches = [item for item in items if item.id == item_id]
    if not matches and expected_hash:
        matches = [item for item in items if item.expected_hash == expected_hash]
    if len(matches) != 1:
        raise TriageConflictError("The capture changed or can no longer be found. Reload Triage and try again.")
    item = matches[0]
    if expected_hash and item.expected_hash != expected_hash:
        raise TriageConflictError("The capture changed after it was loaded. Reload Triage before processing it.")
    return item


def _capture_lines(timestamp: str, text: str, *, keep_header: bool) -> list[str]:
    if not keep_header:
        return []
    body = [f"  {line}" for line in str(text or "").splitlines()]
    return [f"- *{timestamp}*", *body, "", "---"]


def _task_line(text: str, *, priority: int, tags: list[str], start: str, due: str) -> str:
    values = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    if not values:
        raise ValueError("Task text cannot be empty.")
    first = TASK_MARKER_RE.sub("", values[0], count=1).strip()
    if not first:
        raise ValueError("Task text cannot be empty.")
    parts = [first]
    priority = max(0, min(int(priority or 0), 3))
    if priority:
        parts.append("!" * priority)
    parts.extend("@" + value.strip().lstrip("@") for value in tags if value.strip())
    if start:
        parts.append(f">{start}")
    if due:
        parts.append(f"<{due}")
    task = "☐ " + " ".join(parts)
    if len(values) > 1:
        task += "\n" + "\n".join(f"  {value}" for value in values[1:])
    return task


def _plan_attachment_moves(
    vault_root: Path,
    source_path: str,
    destination_path: str,
    text: str,
) -> tuple[str, list[dict]]:
    if source_path == destination_path:
        return text, []
    source_dir = (vault_root / source_path.lstrip("/")).parent
    destination_dir = (vault_root / destination_path.lstrip("/")).parent
    destination_dir.mkdir(parents=True, exist_ok=True)
    replacements: dict[str, str] = {}
    moves: list[dict] = []
    for match in RELATIVE_ATTACHMENT_RE.finditer(text):
        name = match.group("name").strip()
        if not name or PurePosixPath(name).name != name or name in replacements:
            continue
        source = source_dir / name
        if not source.is_file():
            continue
        destination = destination_dir / name
        counter = 2
        while destination.exists():
            destination = destination_dir / f"{source.stem}_{counter}{source.suffix}"
            counter += 1
        replacements[name] = destination.name
        moves.append(
            {
                "source": str(source),
                "destination": str(destination),
                "source_page": source_path,
                "destination_page": destination_path,
            }
        )

    def replace_link(match: re.Match[str]) -> str:
        name = match.group("name").strip()
        replacement = replacements.get(name)
        if not replacement:
            return match.group(0)
        return f"{match.group('prefix')}[{match.group('label')}](./{replacement})"

    return RELATIVE_ATTACHMENT_RE.sub(replace_link, text), moves


def process_triage_item(
    vault_root: Path,
    *,
    path: str,
    item_id: str,
    expected_hash: str,
    action: Literal["task", "file", "note", "delete"],
    text: str,
    destination: Optional[str] = None,
    priority: int = 0,
    tags: Optional[list[str]] = None,
    start: str = "",
    due: str = "",
) -> dict:
    source_path = "/" + str(path or "").lstrip("/")
    content = files.read_file(vault_root, source_path)
    item = _locate_item(content, source_path, item_id, expected_hash)
    lines = content.splitlines()
    start_index = item.start_line - 1
    end_index = item.end_line
    destination_path = "/" + str(destination or source_path).lstrip("/")
    changed: dict[str, str] = {}
    before = {source_path: content}
    attachment_moves: list[dict] = []
    if action in {"task", "file"} and destination_path != source_path:
        text, attachment_moves = _plan_attachment_moves(
            vault_root, source_path, destination_path, text
        )
    if action == "note":
        replacement = _capture_lines(item.timestamp, text, keep_header=True)
        lines[start_index:end_index] = replacement
        changed[source_path] = "\n".join(lines).rstrip("\n") + "\n"
    elif action == "delete":
        del lines[start_index:end_index]
        changed[source_path] = "\n".join(lines).rstrip("\n") + "\n"
    elif action == "task":
        rendered = _task_line(
            text,
            priority=priority,
            tags=tags or [],
            start=start,
            due=due,
        )
        if destination_path == source_path:
            lines[start_index:end_index] = rendered.splitlines()
            changed[source_path] = "\n".join(lines).rstrip("\n") + "\n"
        else:
            del lines[start_index:end_index]
            destination_content = files.read_file(vault_root, destination_path)
            before[destination_path] = destination_content
            changed[source_path] = "\n".join(lines).rstrip("\n") + "\n"
            changed[destination_path] = _append_under_tasks(destination_content, rendered)
    elif action == "file":
        if not destination:
            raise ValueError("Choose a destination page before filing this capture.")
        if destination_path == source_path:
            raise ValueError("Choose a different destination page, or use Keep as Note.")
        del lines[start_index:end_index]
        destination_content = files.read_file(vault_root, destination_path)
        before[destination_path] = destination_content
        note = str(text or "").strip()
        spacer = "\n\n" if destination_content.rstrip("\n") else ""
        changed[source_path] = "\n".join(lines).rstrip("\n") + "\n"
        changed[destination_path] = destination_content.rstrip("\n") + spacer + note + "\n"
    else:
        raise ValueError(f"Unsupported Triage action: {action}")
    completed_moves: list[dict] = []
    try:
        for changed_path, changed_content in changed.items():
            files.write_file(vault_root, changed_path, changed_content)
        for move in attachment_moves:
            shutil.move(move["source"], move["destination"])
            completed_moves.append(move)
    except Exception:
        for move in reversed(completed_moves):
            destination_file = Path(move["destination"])
            source_file = Path(move["source"])
            if destination_file.exists() and not source_file.exists():
                shutil.move(str(destination_file), str(source_file))
        for changed_path, original_content in before.items():
            files.write_file(vault_root, changed_path, original_content)
        raise
    for move in attachment_moves:
        source_rel = "/" + Path(move["source"]).relative_to(vault_root).as_posix()
        destination_rel = "/" + Path(move["destination"]).relative_to(vault_root).as_posix()
        try:
            config.delete_attachment_entry(source_rel)
            config.upsert_attachment_entry(
                destination_path, destination_rel, move["destination"]
            )
        except Exception:
            pass
    for changed_path, changed_content in changed.items():
        try:
            indexer.index_page(changed_path, changed_content)
        except Exception:
            pass
    invalidate_triage_cache(vault_root)
    return {
        "ok": True,
        "paths": list(changed),
        "action": action,
        "before": before,
        "after": changed,
        "attachment_moves": attachment_moves,
    }


def _locate_quick_capture_chunk(
    content: str,
    path: str,
    *,
    start_line: int,
    expected_hash: str,
) -> QuickCaptureChunk:
    items = parse_quick_capture_chunks(content, path)
    exact = [
        item
        for item in items
        if item.start_line == int(start_line) and item.expected_hash == expected_hash
    ]
    matches = exact or [item for item in items if item.expected_hash == expected_hash]
    if len(matches) != 1:
        raise TriageConflictError(
            "The Quick Capture changed or can no longer be found. Reload and try again."
        )
    return matches[0]


def process_quick_capture_chunk(
    vault_root: Path,
    *,
    path: str,
    start_line: int,
    expected_hash: str,
    action: Literal["task", "move"],
    destination: str,
    text: str = "",
    priority: int = 0,
    tags: Optional[list[str]] = None,
    start: str = "",
    due: str = "",
    status: str = "todo",
) -> dict:
    """Move a complete capture or convert it to a task in another page."""
    source_path = "/" + str(path or "").lstrip("/")
    destination_path = "/" + str(destination or "").lstrip("/")
    if not destination or destination_path == "/":
        raise ValueError("Choose a destination page before processing this capture.")
    if destination_path == source_path:
        raise ValueError("Choose a destination page other than the capture source.")
    source_content = files.read_file(vault_root, source_path)
    item = _locate_quick_capture_chunk(
        source_content,
        source_path,
        start_line=start_line,
        expected_hash=expected_hash,
    )
    source_lines = source_content.splitlines()
    source_start = item.start_line - 1
    source_end = item.end_line
    destination_content = files.read_file(vault_root, destination_path)
    before = {
        source_path: source_content,
        destination_path: destination_content,
    }
    attachment_text = item.raw if action == "move" else (text or item.text)
    rewritten, attachment_moves = _plan_attachment_moves(
        vault_root,
        source_path,
        destination_path,
        attachment_text,
    )
    del source_lines[source_start:source_end]
    source_after = "\n".join(source_lines).rstrip("\n") + "\n"
    if action == "move":
        spacer = "\n\n" if destination_content.rstrip("\n") else ""
        destination_after = destination_content.rstrip("\n") + spacer + rewritten.rstrip("\n") + "\n"
    elif action == "task":
        rendered = _task_line(
            rewritten,
            priority=priority,
            tags=tags or [],
            start=start,
            due=due,
        )
        if str(status or "todo") == "done":
            rendered = rendered.replace("☐", "☑", 1)
        destination_after = _append_under_tasks(destination_content, rendered)
    else:
        raise ValueError(f"Unsupported Quick Capture action: {action}")
    after = {
        source_path: source_after,
        destination_path: destination_after,
    }
    completed_moves: list[dict] = []
    try:
        for changed_path, changed_content in after.items():
            files.write_file(vault_root, changed_path, changed_content)
        for move in attachment_moves:
            shutil.move(move["source"], move["destination"])
            completed_moves.append(move)
    except Exception:
        for move in reversed(completed_moves):
            destination_file = Path(move["destination"])
            source_file = Path(move["source"])
            if destination_file.exists() and not source_file.exists():
                shutil.move(str(destination_file), str(source_file))
        for changed_path, original_content in before.items():
            files.write_file(vault_root, changed_path, original_content)
        raise
    for move in attachment_moves:
        try:
            source_rel = "/" + Path(move["source"]).relative_to(vault_root).as_posix()
            destination_rel = "/" + Path(move["destination"]).relative_to(vault_root).as_posix()
            config.delete_attachment_entry(source_rel)
            config.upsert_attachment_entry(destination_path, destination_rel, move["destination"])
        except Exception:
            pass
    for changed_path, changed_content in after.items():
        try:
            indexer.index_page(changed_path, changed_content)
        except Exception:
            pass
    invalidate_triage_cache(vault_root)
    return {
        "ok": True,
        "paths": list(after),
        "action": action,
        "before": before,
        "after": after,
        "attachment_moves": attachment_moves,
    }
