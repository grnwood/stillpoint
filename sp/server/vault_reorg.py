from __future__ import annotations

import base64
import hashlib
import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

from sp.app import config
from sp.server import file_ops, search_index
from sp.server.adapters.files import PAGE_SUFFIX, file_content_lock


_VAULT_LOCKS: dict[str, threading.RLock] = {}
_VAULT_LOCKS_GUARD = threading.RLock()
_JOURNAL_DAY_RE = re.compile(r"^/Journal/(?P<year>\d{4})/(?P<month>\d{1,2})/(?P<day>\d{1,2})(?:/|$)", re.IGNORECASE)
_JOURNAL_DAY_EXACT_RE = re.compile(r"^/Journal/\d{4}/\d{1,2}/\d{1,2}$", re.IGNORECASE)
_JOURNAL_CONTAINER_RE = re.compile(r"^/Journal(?:/\d{4}(?:/\d{1,2}(?:/\d{1,2})?)?)?$", re.IGNORECASE)
_INVALID_NAME_CHARS = set('/\\:*?"<>|')
_RESERVED_NAMES = re.compile(r"(?i)^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$")


class ReorganizationError(RuntimeError):
    def __init__(self, message: str, *, errors: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.errors = errors or []


def _vault_lock(root: Path) -> threading.RLock:
    key = str(root.resolve())
    with _VAULT_LOCKS_GUARD:
        return _VAULT_LOCKS.setdefault(key, threading.RLock())


@contextmanager
def structural_operation(root: Path):
    """Serialize structural filesystem operations for one vault."""
    lock = _vault_lock(root)
    with lock:
        yield


def _folder_for_page(page_path: str) -> str:
    path = Path((page_path or "").lstrip("/"))
    if path.suffix:
        parent = path.parent.as_posix()
        return f"/{parent}" if parent and parent != "." else "/"
    return file_ops._normalize_folder_path(page_path)


def _valid_leaf(name: str) -> str | None:
    cleaned = (name or "").strip()
    if not cleaned or cleaned in {".", ".."}:
        return None
    if any(char in _INVALID_NAME_CHARS or ord(char) < 32 for char in cleaned):
        return None
    if cleaned.endswith((" ", ".")) or _RESERVED_NAMES.fullmatch(cleaned):
        return None
    return cleaned


def _rebase_folder(path: str, old: str, new: str) -> str:
    if path == old:
        return new
    if path.startswith(old.rstrip("/") + "/"):
        return new.rstrip("/") + path[len(old):]
    return path


def _compose_page_map(combined: dict[str, str], latest: dict[str, str]) -> None:
    previous_values = set(combined.values())
    for original, current in list(combined.items()):
        if current in latest:
            combined[original] = latest[current]
    for old, new in latest.items():
        if old not in previous_values and old not in combined:
            combined[old] = new


def _journal_day_for_source(source: str) -> tuple[str, str] | None:
    match = _JOURNAL_DAY_RE.match(source)
    if not match:
        return None
    day_folder = f"/Journal/{match.group('year')}/{match.group('month')}/{match.group('day')}"
    if source.rstrip("/").casefold() == day_folder.casefold():
        return None
    day_page = f"{day_folder}/{match.group('day')}{PAGE_SUFFIX}"
    return day_folder, day_page


def _is_journal_day(path: str) -> bool:
    return bool(_JOURNAL_DAY_EXACT_RE.fullmatch((path or "").rstrip("/")))


def _is_protected_journal_container(path: str) -> bool:
    return bool(_JOURNAL_CONTAINER_RE.fullmatch((path or "").rstrip("/")))


def _candidate_operation_type(folder_path: str) -> str:
    return "add_reference" if _is_journal_day(folder_path) else "move"


def _matching_markdown_heading(folder_path: str, query: str) -> str:
    """Return the best ATX heading whose text contains every query term."""
    if not _is_journal_day(folder_path):
        return ""
    active_root = config.get_active_vault()
    if not active_root:
        return ""
    page_path = config.folder_to_page_path(folder_path)
    try:
        content = (Path(active_root) / page_path.lstrip("/")).read_text(encoding="utf-8")
    except Exception:
        return ""
    terms = [term.casefold() for term in query.split() if term.strip()]
    if not terms:
        return ""
    matches: list[tuple[tuple[int, int, int], str]] = []
    for line_number, line in enumerate(content.splitlines()):
        match = re.match(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if not match:
            continue
        heading = match.group(2).strip()
        heading_cf = heading.casefold()
        if not all(term in heading_cf for term in terms):
            continue
        joined = " ".join(terms)
        if heading_cf == joined:
            match_rank = 0
        elif heading_cf.startswith(joined):
            match_rank = 1
        else:
            match_rank = 2
        matches.append(((match_rank, len(match.group(1)), line_number), heading))
    return min(matches, default=((0, 0, 0), ""), key=lambda item: item[0])[1]


def _day_has_subtree_link(day_page: str, source: str) -> bool:
    db_path = config._vault_db_path()
    if not db_path:
        return False
    source_page = config.folder_to_page_path(source)
    prefix = source.rstrip("/") + "/%"
    conn = sqlite3.connect(db_path, check_same_thread=False)
    try:
        row = conn.execute(
            "SELECT 1 FROM links WHERE from_path = ? AND (to_path = ? OR to_path LIKE ?) LIMIT 1",
            (day_page, source_page, prefix),
        ).fetchone()
        return bool(row)
    finally:
        conn.close()


def _metadata_candidates(query: str, *, journal_only: bool, limit: int) -> list[dict[str, Any]]:
    db_path = config._vault_db_path()
    if not db_path:
        return []
    terms = [term.casefold() for term in query.split() if term.strip()]
    if not terms:
        return []
    conn = sqlite3.connect(db_path, check_same_thread=False)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(pages)").fetchall()}
        deleted_clause = "AND COALESCE(deleted, 0) = 0" if "deleted" in columns else ""
        rows = conn.execute(
            f"SELECT path, COALESCE(title, '') FROM pages WHERE 1=1 {deleted_clause}"
        ).fetchall()
    finally:
        conn.close()

    ranked: list[tuple[tuple[int, str], dict[str, Any]]] = []
    for page_path, title in rows:
        if not isinstance(page_path, str) or not page_path.startswith("/"):
            continue
        if journal_only and not page_path.casefold().startswith("/journal/"):
            continue
        folder = _folder_for_page(page_path)
        if folder == "/":
            continue
        leaf = Path(folder.rstrip("/")).name
        fields = f"{leaf} {title or ''} {page_path}".casefold()
        if not all(term in fields for term in terms):
            continue
        joined = " ".join(terms)
        leaf_cf = leaf.casefold()
        title_cf = str(title or "").casefold()
        if joined in {leaf_cf, title_cf}:
            priority = 0
        elif leaf_cf.startswith(joined) or title_cf.startswith(joined):
            priority = 1
        elif joined in leaf_cf or joined in title_cf:
            priority = 2
        else:
            priority = 3
        ranked.append(
            (
                (priority, page_path.casefold()),
                {
                    "path": page_path,
                    "folder_path": folder,
                    "title": title or leaf,
                    "match_type": "title_path",
                    "snippet": "",
                },
            )
        )
    ranked.sort(key=lambda item: item[0])
    results = [item[1] for item in ranked[:limit]]
    for item in results:
        item["operation_type"] = _candidate_operation_type(str(item.get("folder_path") or ""))
        item["matched_heading"] = _matching_markdown_heading(
            str(item.get("folder_path") or ""), query
        )
    return results


def search_candidates(
    query: str,
    *,
    include_content: bool = False,
    journal_only: bool = False,
    limit: int = 100,
) -> dict[str, Any]:
    query = (query or "").strip()
    if not query:
        return {"results": [], "content_index_available": False}
    results = _metadata_candidates(query, journal_only=journal_only, limit=limit)
    seen = {item["path"] for item in results}
    content_available = False
    if include_content and len(results) < limit:
        db_path = config._vault_db_path()
        if db_path:
            conn = sqlite3.connect(db_path, check_same_thread=False)
            try:
                try:
                    count = conn.execute("SELECT COUNT(*) FROM pages_search_index").fetchone()[0]
                    content_available = bool(count)
                except sqlite3.OperationalError:
                    content_available = False
                if content_available:
                    subtree = "/Journal" if journal_only else None
                    for item in search_index.search_pages(conn, query, subtree, limit):
                        page_path = str(item.get("path") or "")
                        if not page_path or page_path in seen:
                            continue
                        folder = _folder_for_page(page_path)
                        if folder == "/":
                            continue
                        result = {
                                "path": page_path,
                                "folder_path": folder,
                                "title": Path(folder.rstrip("/")).name,
                                "match_type": "content",
                                "snippet": str(item.get("snippet") or ""),
                                "operation_type": _candidate_operation_type(folder),
                                "matched_heading": _matching_markdown_heading(folder, query),
                            }
                        results.append(result)
                        seen.add(page_path)
                        if len(results) >= limit:
                            break
            finally:
                conn.close()
    return {"results": results[:limit], "content_index_available": content_available}


def _topological_order(operations: list[dict[str, Any]]) -> list[int]:
    edges: dict[int, set[int]] = {idx: set() for idx in range(len(operations))}
    for idx, op in enumerate(operations):
        for other_idx, other in enumerate(operations):
            if idx == other_idx:
                continue
            # A destination occupied by another source must be vacated first.
            if op["destination_path"] == other["source_path"]:
                edges[other_idx].add(idx)
            # Moving into a source subtree must happen before that subtree moves.
            if op["destination_parent"] == other["source_path"] or op["destination_parent"].startswith(
                other["source_path"] + "/"
            ):
                edges[idx].add(other_idx)
            # A parent created by another move must exist first.
            if op["destination_parent"] == other["destination_path"] or op["destination_parent"].startswith(
                other["destination_path"] + "/"
            ):
                edges[other_idx].add(idx)
    indegree = {idx: 0 for idx in edges}
    for targets in edges.values():
        for target in targets:
            indegree[target] += 1
    ready = sorted(idx for idx, degree in indegree.items() if degree == 0)
    ordered: list[int] = []
    while ready:
        current = ready.pop(0)
        ordered.append(current)
        for target in sorted(edges[current]):
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
                ready.sort()
    if len(ordered) != len(operations):
        raise ReorganizationError("The staged moves contain a structural cycle.")
    return ordered


def _resolve_final_destinations(operations: list[dict[str, Any]]) -> None:
    resolving: set[int] = set()
    resolved: set[int] = set()

    def resolve(index: int) -> str:
        if index in resolved:
            return operations[index]["destination_path"]
        if index in resolving:
            raise ReorganizationError("The staged moves contain a structural cycle.")
        resolving.add(index)
        op = operations[index]
        destination = op["raw_destination_path"]
        for other_index, other in enumerate(operations):
            source = other["source_path"]
            if destination.startswith(source + "/"):
                other_final = resolve(other_index)
                destination = _rebase_folder(destination, source, other_final)
        op["destination_path"] = destination
        op["final_destination_path"] = destination
        resolving.remove(index)
        resolved.add(index)
        return destination

    for idx in range(len(operations)):
        resolve(idx)


def _plan_token(tree_version: int, operations: Iterable[dict[str, Any]]) -> str:
    stable = [
        {
            "operation_type": op.get("operation_type", "move"),
            "source_path": op["source_path"],
            "destination_parent": op["destination_parent"],
            "new_name": op["new_name"],
            "destination_path": op["destination_path"],
            "journal_reference_action": op.get("journal_reference_action", "none"),
        }
        for op in operations
    ]
    payload = json.dumps({"tree_version": tree_version, "operations": stable}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def preflight_plan(root: Path, raw_operations: list[dict[str, Any]], tree_version: int | None = None) -> dict[str, Any]:
    current_version = config.get_tree_version()
    tree_version_changed = tree_version is not None and int(tree_version) != current_version
    errors: list[dict[str, Any]] = []
    if recovery_status(root).get("recovery_required"):
        errors.append({"row": None, "message": "An incomplete reorganization requires recovery before continuing."})
    operations: list[dict[str, Any]] = []
    for row, raw in enumerate(raw_operations):
        operation_type = str(raw.get("operation_type") or "move").strip().lower()
        source = file_ops._normalize_folder_path(str(raw.get("from") or raw.get("source_path") or ""))
        parent = file_ops._normalize_folder_path(str(raw.get("destination_parent") or "/"))
        raw_name = str(raw.get("new_name") or "").strip()
        if operation_type not in {"move", "add_reference"}:
            errors.append({"row": row, "message": "Unknown reorganization operation."})
            operation_type = "move"
        if operation_type == "add_reference":
            name = raw_name or Path(source.rstrip("/")).name or "Journal entry"
            destination = parent
        else:
            name = _valid_leaf(raw_name)
            if source == "/":
                errors.append({"row": row, "message": "Cannot move the vault root."})
            if _is_protected_journal_container(source):
                errors.append(
                    {
                        "row": row,
                        "message": "Journal day pages and calendar containers are historical records and cannot be moved.",
                    }
                )
            if not name:
                errors.append({"row": row, "message": "Enter a valid destination name."})
                name = Path(source.rstrip("/")).name or "Invalid"
            destination = f"/{name}" if parent == "/" else f"{parent}/{name}"
        operations.append(
            {
                "row": row,
                "operation_type": operation_type,
                "source_path": source,
                "destination_parent": parent,
                "new_name": name,
                "raw_destination_path": destination,
                "destination_path": destination,
            }
        )
    move_operations = [op for op in operations if op["operation_type"] == "move"]
    reference_operations = [op for op in operations if op["operation_type"] == "add_reference"]
    sources = [op["source_path"] for op in move_operations]
    if len(sources) != len(set(sources)):
        errors.append({"row": None, "message": "The plan contains duplicate sources."})
    reference_pairs = [(op["source_path"], op["destination_parent"]) for op in reference_operations]
    if len(reference_pairs) != len(set(reference_pairs)):
        errors.append({"row": None, "message": "The plan contains duplicate Journal references."})
    for idx, source in enumerate(sources):
        for other_idx, other in enumerate(sources):
            if idx != other_idx and source.startswith(other + "/"):
                errors.append({"row": move_operations[idx]["row"], "message": "A staged ancestor already includes this page."})
                break
    try:
        _resolve_final_destinations(move_operations)
    except ReorganizationError as exc:
        errors.append({"row": None, "message": str(exc)})
    destinations = [op["destination_path"] for op in move_operations]
    if len(destinations) != len(set(destinations)):
        errors.append({"row": None, "message": "Multiple rows have the same final destination."})

    source_set = set(sources)
    destination_set = set(destinations)
    for op in move_operations:
        row = op["row"]
        source = op["source_path"]
        destination = op["destination_path"]
        source_dir = file_ops._resolve_folder(root, source)
        if not source_dir.exists():
            errors.append({"row": row, "message": "Source no longer exists."})
        if destination == source:
            errors.append({"row": row, "message": "Destination is unchanged."})
        if destination.startswith(source + "/"):
            errors.append({"row": row, "message": "Cannot move a page into its own subtree."})
        destination_dir = file_ops._resolve_folder(root, destination)
        if destination_dir.exists() and destination not in source_set:
            errors.append({"row": row, "message": "Destination already exists."})
        parent = op["destination_parent"]
        parent_exists = file_ops._resolve_folder(root, parent).exists()
        parent_planned = parent in destination_set or any(parent.startswith(dest + "/") for dest in destination_set)
        parent_source = parent in source_set or any(parent.startswith(src + "/") for src in source_set)
        if not parent_exists and not parent_planned and not parent_source:
            errors.append({"row": row, "message": "Destination parent does not exist."})

        journal = _journal_day_for_source(source)
        action = "none"
        day_page = None
        if journal and not destination.startswith(journal[0] + "/"):
            day_folder, day_page = journal
            if not (root / day_page.lstrip("/")).exists():
                errors.append({"row": row, "message": f"Journal day page is missing: {day_page}"})
            action = "rewrite_existing" if _day_has_subtree_link(day_page, source) else "append"
            op["journal_day_path"] = day_folder
        op["journal_page_path"] = day_page
        op["journal_reference_action"] = action

    moved_paths = set(sources) | set(destinations)
    for op in reference_operations:
        row = op["row"]
        source = op["source_path"]
        target = op["destination_parent"]
        source_dir = file_ops._resolve_folder(root, source)
        if not _is_journal_day(source):
            errors.append({"row": row, "message": "Only a canonical Journal day page can use Add reference."})
        if not source_dir.exists():
            errors.append({"row": row, "message": "Journal day page no longer exists."})
        source_page = config.folder_to_page_path(source)
        if not (root / source_page.lstrip("/")).exists():
            errors.append({"row": row, "message": f"Journal day page is missing: {source_page}"})
        if target == "/":
            errors.append({"row": row, "message": "Choose an existing destination page for the Journal reference."})
        target_page = config.folder_to_page_path(target)
        if target != "/" and not (root / target_page.lstrip("/")).exists():
            errors.append({"row": row, "message": "The Journal reference destination page does not exist."})
        if target == source:
            errors.append({"row": row, "message": "A Journal day page cannot reference itself."})
        if target in moved_paths or any(target.startswith(path.rstrip("/") + "/") for path in moved_paths):
            errors.append(
                {
                    "row": row,
                    "message": "Apply the destination page move before adding a Journal reference to it.",
                }
            )
        op["journal_page_path"] = source_page
        op["reference_target_page"] = target_page
        op["journal_reference_action"] = "add_reference"

    execution_order: list[int] = []
    try:
        execution_order = [move_operations[index]["row"] for index in _topological_order(move_operations)]
    except ReorganizationError as exc:
        errors.append({"row": None, "message": str(exc)})

    page_map: dict[str, str] = {}
    if not errors:
        db_path = config._vault_db_path()
        if db_path:
            conn = sqlite3.connect(db_path, check_same_thread=False)
            try:
                for op in move_operations:
                    source_page = config.folder_to_page_path(op["source_path"])
                    rows = conn.execute(
                        "SELECT path FROM pages WHERE COALESCE(deleted, 0) = 0 AND (path = ? OR path LIKE ?)",
                        (source_page, op["source_path"].rstrip("/") + "/%"),
                    ).fetchall()
                    for (old_path,) in rows:
                        page_map[old_path] = config._rebase_page_path(
                            old_path, op["source_path"], op["destination_path"]
                        )
            finally:
                conn.close()
    token = _plan_token(current_version, operations) if not errors else ""
    return {
        "ok": not errors,
        "errors": errors,
        "operations": operations,
        "execution_order": execution_order,
        "page_map": page_map,
        "tree_version": current_version,
        "tree_version_changed": tree_version_changed,
        "plan_token": token,
        "journal_append_count": sum(op.get("journal_reference_action") == "append" for op in operations),
        "journal_reference_count": len(reference_operations),
    }


def _colon_target(page_path: str) -> str:
    return file_ops._path_to_colon(page_path)


def _append_section_entries(page_path: Path, heading: str, entries: list[str]) -> None:
    with file_content_lock(page_path):
        raw = page_path.read_bytes()
        text = raw.decode("utf-8")
        newline = "\r\n" if "\r\n" in text else "\n"
        if all(entry in text for entry in entries):
            return
        lines = text.splitlines()
        heading_line = f"# {heading}"
        heading_index = next((idx for idx, line in enumerate(lines) if line == heading_line), None)
        if heading_index is None:
            if lines and lines[-1].strip():
                lines.append("")
            lines.extend([heading_line, ""])
            lines.extend(entry for entry in entries if entry not in lines)
        else:
            insert_at = len(lines)
            for idx in range(heading_index + 1, len(lines)):
                if re.match(r"^#\s+", lines[idx]):
                    insert_at = idx
                    break
            missing = [entry for entry in entries if entry not in lines[heading_index + 1 : insert_at]]
            if missing:
                if insert_at > heading_index + 1 and lines[insert_at - 1].strip():
                    missing.insert(0, "")
                lines[insert_at:insert_at] = missing
        result = newline.join(lines)
        if text.endswith(("\n", "\r")):
            result += newline
        page_path.write_bytes(result.encode("utf-8"))


def _append_moved_page_links(page_path: Path, links: list[tuple[str, str]]) -> None:
    entries = [f"- [{_colon_target(target)}|{label}]" for target, label in links]
    _append_section_entries(page_path, "Moved Pages", entries)


def _page_display_title(page_path: Path) -> str:
    try:
        for line in page_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("# ") and line[2:].strip():
                return line[2:].strip()
    except Exception:
        pass
    return page_path.stem


def _append_journal_reference(
    target_page: Path,
    *,
    journal_page_path: str,
    journal_title: str,
    note: str,
) -> None:
    entry = f"- [{_colon_target(journal_page_path)}|{journal_title}]"
    if note and note.casefold() != journal_title.casefold():
        entry += f" — {note}"
    _append_section_entries(target_page, "Journal References", [entry])


def _write_manifest(root: Path, payload: dict[str, Any]) -> Path:
    directory = root / ".stillpoint"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "reorg-recovery.json"
    temporary = directory / "reorg-recovery.json.tmp"
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)
    return path


def commit_plan(
    root: Path,
    raw_operations: list[dict[str, Any]],
    *,
    tree_version: int,
    plan_token: str,
) -> dict[str, Any]:
    with structural_operation(root):
        checked = preflight_plan(root, raw_operations, tree_version)
        if not checked["ok"]:
            raise ReorganizationError("Reorganization preflight failed.", errors=checked["errors"])
        if not plan_token or plan_token != checked["plan_token"]:
            raise ReorganizationError("The staged plan changed; validate it again.")
        operations = checked["operations"]
        journal_pages = sorted(
            {op["journal_page_path"] for op in operations if op.get("journal_reference_action") == "append"}
        )
        reference_target_pages = sorted(
            {
                op["reference_target_page"]
                for op in operations
                if op.get("operation_type") == "add_reference"
            }
        )
        content_backup_pages = sorted(set(journal_pages) | set(reference_target_pages))
        journal_backups = {
            path: base64.b64encode((root / path.lstrip("/")).read_bytes()).decode("ascii")
            for path in content_backup_pages
        }
        manifest: dict[str, Any] = {
            "status": "running",
            "tree_version": tree_version,
            "operations": operations,
            "completed": [],
            "journal_backups": journal_backups,
        }
        manifest_path = _write_manifest(root, manifest)
        completed: list[tuple[str, str]] = []
        combined_map: dict[str, str] = {}
        display_orders: dict[str, int] = {}
        try:
            for index in checked["execution_order"]:
                op = operations[index]
                current_source = op["source_path"]
                current_destination = op["raw_destination_path"]
                result = file_ops.move_folder(root, current_source, current_destination, rewrite_links=False)
                completed.append((current_source, current_destination))
                _compose_page_map(combined_map, dict(result.get("page_map") or {}))
                display_orders.update(result.get("display_orders") or {})
                manifest["completed"] = completed
                _write_manifest(root, manifest)

            append_by_page: dict[str, list[tuple[str, str]]] = {}
            for op in operations:
                if op.get("journal_reference_action") != "append":
                    continue
                final_page = config.folder_to_page_path(op["destination_path"])
                append_by_page.setdefault(op["journal_page_path"], []).append((final_page, op["new_name"]))
            for day_page, links in append_by_page.items():
                _append_moved_page_links(root / day_page.lstrip("/"), links)

            for op in operations:
                if op.get("operation_type") != "add_reference":
                    continue
                journal_file = root / op["journal_page_path"].lstrip("/")
                _append_journal_reference(
                    root / op["reference_target_page"].lstrip("/"),
                    journal_page_path=op["journal_page_path"],
                    journal_title=_page_display_title(journal_file),
                    note=op["new_name"],
                )

            config.update_link_paths(combined_map)
            touched_content_pages = sorted(set(append_by_page) | set(reference_target_pages))
            for day_page in touched_content_pages:
                try:
                    content = (root / day_page.lstrip("/")).read_text(encoding="utf-8")
                    from sp.app import indexer as app_indexer

                    app_indexer.index_page(day_page, content)
                    db_path = config._vault_db_path()
                    if db_path:
                        conn = sqlite3.connect(db_path, check_same_thread=False)
                        try:
                            search_index.upsert_page(conn, day_page, 0, content)
                        finally:
                            conn.close()
                except Exception:
                    pass
            manifest["status"] = "completed"
            _write_manifest(root, manifest)
            try:
                manifest_path.unlink()
            except OSError:
                pass
            return {
                "ok": True,
                "page_map": combined_map,
                "display_orders": display_orders,
                "journal_paths": sorted(append_by_page),
                "touched_paths": touched_content_pages,
                "version": config.get_tree_version(),
                "operations": operations,
            }
        except Exception as exc:
            rollback_errors: list[str] = []
            for old_folder, new_folder in reversed(completed):
                try:
                    file_ops.move_folder(root, new_folder, old_folder, rewrite_links=False)
                except Exception as rollback_exc:
                    rollback_errors.append(f"{new_folder} -> {old_folder}: {rollback_exc}")
            for day_page, encoded in journal_backups.items():
                try:
                    (root / day_page.lstrip("/")).write_bytes(base64.b64decode(encoded))
                except Exception as rollback_exc:
                    rollback_errors.append(f"restore {day_page}: {rollback_exc}")
            manifest["status"] = "recovery_required" if rollback_errors else "rolled_back"
            manifest["error"] = str(exc)
            manifest["rollback_errors"] = rollback_errors
            _write_manifest(root, manifest)
            if not rollback_errors:
                try:
                    manifest_path.unlink()
                except OSError:
                    pass
            detail = f"Reorganization failed: {exc}"
            if rollback_errors:
                detail += "; automatic recovery was incomplete"
            raise ReorganizationError(detail) from exc


def recovery_status(root: Path) -> dict[str, Any]:
    path = root / ".stillpoint" / "reorg-recovery.json"
    if not path.exists():
        return {"recovery_required": False}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        payload = {"status": "unknown"}
    return {"recovery_required": payload.get("status") not in {"completed", "rolled_back"}, "manifest": payload}


def recover_incomplete(root: Path) -> dict[str, Any]:
    """Retry rollback from the persisted manifest left by an interrupted commit."""
    path = root / ".stillpoint" / "reorg-recovery.json"
    if not path.exists():
        return {"ok": True, "recovered": False}
    with structural_operation(root):
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ReorganizationError(f"Recovery manifest is unreadable: {exc}") from exc
        errors: list[str] = []
        completed = [tuple(item) for item in manifest.get("completed") or [] if isinstance(item, list) and len(item) == 2]
        for old_folder, new_folder in reversed(completed):
            try:
                new_dir = file_ops._resolve_folder(root, str(new_folder))
                old_dir = file_ops._resolve_folder(root, str(old_folder))
                if not new_dir.exists() and old_dir.exists():
                    continue
                file_ops.move_folder(root, str(new_folder), str(old_folder), rewrite_links=False)
            except Exception as exc:
                errors.append(f"{new_folder} -> {old_folder}: {exc}")
        for day_page, encoded in (manifest.get("journal_backups") or {}).items():
            try:
                (root / str(day_page).lstrip("/")).write_bytes(base64.b64decode(encoded))
            except Exception as exc:
                errors.append(f"restore {day_page}: {exc}")
        if errors:
            manifest["status"] = "recovery_required"
            manifest["rollback_errors"] = errors
            _write_manifest(root, manifest)
            raise ReorganizationError("Recovery remains incomplete: " + "; ".join(errors))
        try:
            path.unlink()
        except OSError as exc:
            raise ReorganizationError(f"Recovery completed but the manifest could not be cleared: {exc}") from exc
        return {"ok": True, "recovered": True, "version": config.get_tree_version()}
