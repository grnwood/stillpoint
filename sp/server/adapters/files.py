from __future__ import annotations
import datetime as dt
import os
import shutil
import traceback
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from threading import RLock
from typing import Dict, List

def assert_not_vault_root_write(path):
    """
    Raise an exception if attempting to write a file directly in a vault root folder.
    Only allow writing files inside subfolders of the vault root.
    """
    # Accept both Path and str
    if hasattr(path, 'parent'):
        parent = path.parent
        name = path.name
        path_str = str(path)
    else:
        parent = os.path.dirname(path)
        name = os.path.basename(path)
        path_str = path
    # If the file's parent contains no parent (i.e. is the vault root), block
    # Only allow files in vaultroot/somefolder/...
    if parent and os.path.isdir(parent):
        # If the parent folder contains no subfolders, it's likely the vault root
        if not any(os.path.isdir(os.path.join(parent, f)) for f in os.listdir(parent)):
            raise RuntimeError(f"Attempted to write file '{path_str}' in vault root folder!\n" + ''.join(traceback.format_stack()))


PAGE_SUFFIX = ".md"
LEGACY_SUFFIX = ".txt"
PAGE_SUFFIXES = (PAGE_SUFFIX, LEGACY_SUFFIX)


class FileAccessError(RuntimeError):
    pass


_FILE_LOCKS: Dict[str, RLock] = {}
_FILE_LOCKS_GUARD = RLock()


@contextmanager
def file_content_lock(path: Path):
    """Serialize in-process reads and writes that replace page content."""
    key = str(path.resolve())
    with _FILE_LOCKS_GUARD:
        lock = _FILE_LOCKS.setdefault(key, RLock())
    lock.acquire()
    try:
        yield
    finally:
        lock.release()


def _page_file_for(directory: Path, suffix: str = PAGE_SUFFIX) -> Path:
    return directory / f"{directory.name}{suffix}"


def is_page_suffix(suffix: str) -> bool:
    return suffix.lower() in PAGE_SUFFIXES


def strip_page_suffix(name: str) -> str:
    lowered = name.lower()
    for suffix in PAGE_SUFFIXES:
        if lowered.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _ensure_page_file(path: Path, *, allow_legacy: bool = True) -> None:
    suffix = path.suffix.lower()
    if suffix == PAGE_SUFFIX:
        return
    if allow_legacy and suffix == LEGACY_SUFFIX:
        return
    raise FileAccessError(
        f"Only page text files ({PAGE_SUFFIX} or {LEGACY_SUFFIX}) are supported."
    )


def _ensure_valid_page_name(path: Path, *, allow_legacy: bool = True) -> None:
    _ensure_page_file(path, allow_legacy=allow_legacy)
    parent = path.parent
    expected = f"{parent.name}{path.suffix}"
    if path.name != expected:
        raise FileAccessError("Page files must share the same name as their parent folder.")


def _ensure_page_scaffold(directory: Path) -> Path:
    page_file = _page_file_for(directory)
    if not page_file.exists():
        directory.mkdir(parents=True, exist_ok=True)
        page_file.write_text(f"# {directory.name}\n\n", encoding="utf-8")
    return page_file


def _resolve_page_for_read(target: Path) -> Path:
    if target.is_dir():
        preferred = _page_file_for(target, PAGE_SUFFIX)
        if preferred.exists():
            return preferred
        legacy = _page_file_for(target, LEGACY_SUFFIX)
        if legacy.exists():
            return legacy
        return preferred
    suffix = target.suffix.lower()
    if suffix == LEGACY_SUFFIX:
        preferred = target.with_suffix(PAGE_SUFFIX)
        if preferred.exists():
            return preferred
        if target.exists():
            return target
        return preferred
    if suffix == PAGE_SUFFIX:
        if target.exists():
            return target
        legacy = target.with_suffix(LEGACY_SUFFIX)
        if legacy.exists():
            return legacy
        return target
    return target


def _resolve(root: Path, relative_path: str) -> Path:
    if not relative_path:
        raise FileAccessError("Path must not be empty")
    rel = relative_path.lstrip("/")
    target = (root / rel).resolve()
    if root not in target.parents and target != root:
        raise FileAccessError("Attempted access outside the vault root")
    return target


def read_file(root: Path, path: str) -> str:
    target = _resolve(root, path)
    target = _resolve_page_for_read(target)
    if not target.exists():
        parent = target.parent
        if not parent.exists():
            raise FileNotFoundError(target)
        _ensure_valid_page_name(target, allow_legacy=False)
        target.write_text(f"# {parent.name}\n\n", encoding="utf-8")
    _ensure_valid_page_name(target)
    try:
        return target.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise FileAccessError("File is not UTF-8 encoded text.") from exc


def write_file(root: Path, path: str, content: str) -> None:
    target = _resolve(root, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_dir():
        target = _page_file_for(target, PAGE_SUFFIX)
    elif target.suffix.lower() == LEGACY_SUFFIX:
        target = target.with_suffix(PAGE_SUFFIX)
    if target.parent == root and target.suffix.lower() in PAGE_SUFFIXES:
        raise FileAccessError("Vault root files are not allowed; create a folder and save inside it.")
    _ensure_valid_page_name(target, allow_legacy=False)
    with file_content_lock(target):
        target.write_text(content, encoding="utf-8")


def list_dir(root: Path, subpath: str = "/", recursive: bool = True) -> List[Dict]:
    """List directories under the given subpath.

    Args:
        root: vault root
        subpath: vault-relative folder ("/" for root)
        recursive: when False, only include direct children and mark has_children
    """
    _ensure_page_scaffold(root)
    try:
        target = _resolve(root, subpath) if subpath and subpath != "/" else root
    except FileNotFoundError:
        return []
    if not target.exists() or not target.is_dir():
        return []

    def build(directory: Path) -> Dict:
        page_name = directory.name if directory != root else root.name
        rel_dir = directory.relative_to(root).as_posix() if directory != root else ""
        children = []
        for child in sorted(directory.iterdir()):
            if not child.is_dir():
                continue
            if child.name.startswith("."):
                continue
            if recursive:
                children.append(build(child))
            else:
                grand_dirs = [
                    d
                    for d in child.iterdir()
                    if d.is_dir() and not d.name.startswith(".")
                ]
                page_file = _resolve_page_for_read(child)
                rel_file = page_file.relative_to(root).as_posix()
                children.append(
                    {
                        "name": child.name,
                        "path": f"/{child.relative_to(root).as_posix()}",
                        "is_dir": bool(grand_dirs),
                        "has_children": bool(grand_dirs),
                        "open_path": f"/{rel_file}",
                        "children": [],
                    }
                )
        page_file = _resolve_page_for_read(directory)
        rel_file = page_file.relative_to(root).as_posix()
        has_children = bool(children)
        node = {
            "name": page_name,
            "path": f"/{rel_dir}" if rel_dir else "/",
            "is_dir": has_children,
            "has_children": has_children,
            "open_path": f"/{rel_file}",
            "children": children,
        }
        return node

    return [build(target)]


def ensure_journal_date(root: Path, day: date, template: str | None = None) -> tuple[Path, bool]:
    """Ensure one dated journal page exists and return its path and creation flag."""
    rel = Path("Journal") / f"{day:%Y}" / f"{day:%m}" / f"{day:%d}"
    page_dir = root / rel
    page_dir.mkdir(parents=True, exist_ok=True)
    page_file = page_dir / f"{page_dir.name}{PAGE_SUFFIX}"
    created = not page_file.exists()
    if created:
        content = template if template is not None else f"# {day:%A, %B %d, %Y}\n\n"
        page_file.write_text(content, encoding="utf-8")
    return page_file, created


def ensure_journal_today(root: Path, template: str | None = None) -> tuple[Path, bool]:
    """Ensure today's journal page exists and return its file path and creation flag.

    If a template string is provided and the page does not yet exist, the file
    will be created with that template content. Otherwise a simple default stub
    is used for first-time creation.

    Returns:
        tuple[Path, bool]: (page file path, True if the page was created)
    """
    return ensure_journal_date(root, dt.datetime.now().date(), template=template)


def list_files_modified_between(root: Path, start: date, end: date) -> List[Dict]:
    """Return page files whose mtime falls between the given dates (inclusive)."""
    results: List[Dict] = []
    if start > end:
        start, end = end, start
    seen_dirs: set[Path] = set()
    for suffix in PAGE_SUFFIXES:
        for path in root.rglob(f"*{suffix}"):
            page_dir = path.parent
            if page_dir in seen_dirs and suffix == LEGACY_SUFFIX:
                continue
            if suffix == PAGE_SUFFIX:
                seen_dirs.add(page_dir)
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            mod_dt = dt.datetime.fromtimestamp(mtime)
            mod_date = mod_dt.date()
            if start <= mod_date <= end:
                rel = f"/{path.relative_to(root).as_posix()}"
                results.append({"path": rel, "modified": mod_dt.isoformat()})
    return results


def list_files_activity_between(root: Path, start: date, end: date, mode: str = "both") -> List[Dict]:
    """Return page activity (created/updated) between dates (inclusive)."""
    results: List[Dict] = []
    if start > end:
        start, end = end, start
    mode_norm = (mode or "both").strip().lower()
    if mode_norm in {"edited", "updated"}:
        mode_norm = "edited"
    elif mode_norm == "created":
        mode_norm = "created"
    else:
        mode_norm = "both"

    seen_dirs: set[Path] = set()
    for suffix in PAGE_SUFFIXES:
        for path in root.rglob(f"*{suffix}"):
            page_dir = path.parent
            if page_dir in seen_dirs and suffix == LEGACY_SUFFIX:
                continue
            if suffix == PAGE_SUFFIX:
                seen_dirs.add(page_dir)
            try:
                stat = path.stat()
                mtime = stat.st_mtime
                ctime = getattr(stat, "st_birthtime", None)
                if not ctime:
                    ctime = stat.st_ctime
            except OSError:
                continue

            mod_dt = dt.datetime.fromtimestamp(mtime)
            created_dt = dt.datetime.fromtimestamp(ctime)
            mod_in_range = start <= mod_dt.date() <= end
            created_in_range = start <= created_dt.date() <= end

            include = False
            event = "updated"
            event_time = mod_dt
            if mode_norm == "edited":
                include = mod_in_range
                event = "updated"
                event_time = mod_dt
            elif mode_norm == "created":
                include = created_in_range
                event = "created"
                event_time = created_dt
            else:
                include = mod_in_range or created_in_range
                if created_in_range and (not mod_in_range or created_dt >= mod_dt):
                    event = "created"
                    event_time = created_dt
                else:
                    event = "updated"
                    event_time = mod_dt

            if not include:
                continue

            rel = f"/{path.relative_to(root).as_posix()}"
            results.append(
                {
                    "path": rel,
                    "modified": mod_dt.isoformat(),
                    "created": created_dt.isoformat(),
                    "event": event,
                    "event_time": event_time.isoformat(),
                }
            )
    return results


def create_directory(root: Path, path: str) -> None:
    target = _resolve(root, path)
    if target.exists():
        raise FileExistsError(target)
    target.mkdir(parents=True, exist_ok=False)
    _ensure_page_scaffold(target)


def create_markdown_file(root: Path, path: str, content: str = "") -> None:
    target = _resolve(root, path)
    if target.exists():
        raise FileExistsError(target)
    if target.is_dir():
        target = _page_file_for(target, PAGE_SUFFIX)
    elif target.suffix.lower() == LEGACY_SUFFIX:
        target = target.with_suffix(PAGE_SUFFIX)
    if target.parent == root and target.suffix.lower() in PAGE_SUFFIXES:
        raise FileAccessError("Vault root files are not allowed; create a folder and save inside it.")
    _ensure_valid_page_name(target, allow_legacy=False)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def delete_path(root: Path, path: str) -> None:
    target = _resolve(root, path)
    if target == root:
        raise FileAccessError("Cannot delete the vault root")
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target = _resolve_page_for_read(target)
        _ensure_valid_page_name(target)
        parent = target.parent
        if parent == root:
            raise FileAccessError("Cannot delete the root page")
        shutil.rmtree(parent, ignore_errors=False)
