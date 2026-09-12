from __future__ import annotations

import os
import re
import shutil
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from typing import Dict, Iterable, Optional, Tuple

_ANSI_BLUE = "\033[94m"
_ANSI_RESET = "\033[0m"

from sp.app import config
from sp.server.adapters.files import (
    FileAccessError,
    LEGACY_SUFFIX,
    PAGE_SUFFIX,
    PAGE_SUFFIXES,
    file_content_lock,
    strip_page_suffix,
)


_LOCKS: Dict[str, RLock] = {}
_REGISTRY_LOCK = RLock()


def _normalize_folder_path(path: str) -> str:
    cleaned = (path or "").strip().replace("\\", "/")
    cleaned = cleaned.lstrip("/")
    if any(cleaned.endswith(suffix) for suffix in PAGE_SUFFIXES):
        cleaned = str(Path(cleaned).parent)
    cleaned = cleaned.rstrip("/")
    return f"/{cleaned}" if cleaned else "/"


def _parent_folder_path(folder_path: str) -> str:
    normalized = _normalize_folder_path(folder_path)
    if normalized == "/":
        return "/"
    parent = Path(normalized.lstrip("/")).parent
    return f"/{parent.as_posix()}" if parent.as_posix() else "/"


def _resolve_folder(root: Path, folder_path: str) -> Path:
    rel = folder_path.lstrip("/")
    target = (root / rel).resolve()
    if root not in target.parents and target != root:
        raise FileAccessError("Attempted access outside the vault root")
    return target


def _atomic_replace_bytes(path: Path, content: bytes, *, tag: str) -> None:
    """Durably replace one file from a same-directory temporary file."""
    temporary = path.with_name(f".{path.name}.{tag}.tmp")
    try:
        with temporary.open("wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except (AttributeError, OSError):
            # Directory fsync is unavailable on some supported platforms.
            pass
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


@contextmanager
def _lock_paths(paths: Iterable[str]):
    unique = sorted(set(_normalize_folder_path(p) for p in paths if p is not None))
    acquired: list[RLock] = []
    with _REGISTRY_LOCK:
        for path in unique:
            lock = _LOCKS.setdefault(path, RLock())
            acquired.append(lock)
    try:
        for lock in acquired:
            lock.acquire()
        yield
    finally:
        for lock in reversed(acquired):
            try:
                lock.release()
            except Exception:
                pass


def preflight(root: Path, op: str, path: str, dest: Optional[str] = None) -> Tuple[bool, Optional[str]]:
    src_folder = _normalize_folder_path(path)
    if src_folder == "/":
        return False, "Cannot operate on the vault root"
    try:
        src_dir = _resolve_folder(root, src_folder)
    except FileAccessError as exc:
        return False, str(exc)
    if not src_dir.exists():
        return False, "Source does not exist"
    if op in {"rename", "move"}:
        if not dest:
            return False, "Destination is required"
        dest_folder = _normalize_folder_path(dest)
        if dest_folder == "/":
            return False, "Cannot target the vault root"
        if dest_folder == src_folder:
            return False, "Destination matches source"
        if dest_folder.startswith(f"{src_folder}/"):
            return False, "Destination is inside source subtree"
        if op == "rename" and _parent_folder_path(dest_folder) != _parent_folder_path(src_folder):
            return False, "Rename must stay within the same parent"
        try:
            dest_dir = _resolve_folder(root, dest_folder)
        except FileAccessError as exc:
            return False, str(exc)
        if dest_dir.exists():
            return False, "Destination already exists"
        if not dest_dir.parent.exists():
            return False, "Destination parent does not exist"
    return True, None


def delete_folder(root: Path, folder_path: str) -> dict:
    normalized = _normalize_folder_path(folder_path)
    if normalized == "/":
        raise FileAccessError("Cannot delete the vault root")
    target = _resolve_folder(root, normalized)
    if not target.exists():
        raise FileNotFoundError(target)
    with _lock_paths([normalized]):
        shutil.rmtree(target)
        config.delete_tree_index(normalized)
        version = config.bump_tree_version()
    return {"deleted": [normalized], "version": version}


def rename_folder(root: Path, from_path: str, to_path: str) -> dict:
    """Rename within the same parent."""
    return _move_folder(root, from_path, to_path, set_new_parent_order=False)


def move_folder(root: Path, from_path: str, to_path: str, rewrite_links: bool = True) -> dict:
    """Move to a new parent (may also rename)."""
    return _move_folder(root, from_path, to_path, set_new_parent_order=True, rewrite_links=rewrite_links)


def _move_folder(root: Path, from_path: str, to_path: str, *, set_new_parent_order: bool, rewrite_links: bool = True) -> dict:
    src_folder = _normalize_folder_path(from_path)
    dest_folder = _normalize_folder_path(to_path)
    print(f"{_ANSI_BLUE}[FILE_OPS] _move_folder: from={src_folder} to={dest_folder}{_ANSI_RESET}")
    if src_folder == "/":
        raise FileAccessError("Cannot move the vault root")
    if dest_folder.startswith(f"{src_folder}/"):
        raise FileAccessError("Cannot move a folder into its own subtree")
    src_dir = _resolve_folder(root, src_folder)
    dest_dir = _resolve_folder(root, dest_folder)
    print(f"{_ANSI_BLUE}[FILE_OPS] src_dir={src_dir}, dest_dir={dest_dir}{_ANSI_RESET}")
    print(f"{_ANSI_BLUE}[FILE_OPS] src_dir.exists()={src_dir.exists()}, dest_dir.exists()={dest_dir.exists()}{_ANSI_RESET}")
    if not src_dir.exists():
        raise FileNotFoundError(src_dir)
    if dest_dir.exists():
        raise FileAccessError("Destination already exists")
    dest_parent = dest_dir.parent
    if not dest_parent.exists():
        raise FileAccessError(f"Destination parent missing: {dest_parent}")
    
    # Validate the move BEFORE making filesystem changes
    try:
        config.validate_move_tree_index(src_folder, dest_folder)
    except RuntimeError as exc:
        raise FileAccessError(str(exc))
    
    with _lock_paths([src_folder, dest_folder]):
        print(f"{_ANSI_BLUE}[FILE_OPS] About to shutil.move from {src_dir} to {dest_dir}{_ANSI_RESET}")
        shutil.move(str(src_dir), str(dest_dir))
        print(f"{_ANSI_BLUE}[FILE_OPS] shutil.move completed{_ANSI_RESET}")
        # Ensure the page file matches the new folder name
        old_leaf = src_dir.name
        new_leaf = dest_dir.name
        old_page = dest_dir / f"{old_leaf}{PAGE_SUFFIX}"
        new_page = dest_dir / f"{new_leaf}{PAGE_SUFFIX}"
        legacy_old = dest_dir / f"{old_leaf}{LEGACY_SUFFIX}"
        if old_page.exists() and old_page != new_page:
            try:
                old_page.rename(new_page)
            except Exception:
                pass
        elif legacy_old.exists() and not new_page.exists():
            try:
                legacy_old.rename(new_page)
            except Exception:
                pass
        # Keep a conventional title heading synchronized, but only when it still
        # unambiguously represents the old page name.
        _rewrite_heading_if_matches(new_page, old_leaf, new_leaf)
        try:
            moved = config.move_tree_index(src_folder, dest_folder, root, set_new_parent_order=set_new_parent_order)
        except RuntimeError as exc:
            raise FileAccessError(str(exc))
        
        # Conditionally rewrite links based on user preference
        if rewrite_links:
            try:
                config.update_link_paths(moved.get("path_map") or {})
            except Exception:
                pass
        
        version = config.bump_tree_version()
    return {
        "from": src_folder,
        "to": dest_folder,
        "page_map": moved.get("path_map", {}),
        "display_orders": moved.get("orders", {}),
        "version": version,
    }


def _rewrite_heading_if_matches(page_path: Path, old_leaf: str, new_leaf: str) -> bool:
    """Update a leading H1 that exactly represents the old page leaf name."""
    if not page_path.exists() or old_leaf == new_leaf:
        return False
    try:
        with file_content_lock(page_path):
            content = page_path.read_bytes().decode("utf-8")
            lines = content.splitlines(keepends=True)
            for idx, line in enumerate(lines):
                line_body = line.rstrip("\r\n")
                visible_body = line_body.removeprefix("\ufeff")
                if not visible_body.strip():
                    continue
                match = re.fullmatch(
                    r"(?P<prefix>\ufeff? {0,3}#[ \t]+)(?P<title>.*?)(?P<trailing>[ \t]*)(?P<ending>\r\n|\n|\r)?",
                    line,
                )
                if not match or match.group("title") != old_leaf:
                    return False
                lines[idx] = (
                    f'{match.group("prefix")}{new_leaf}'
                    f'{match.group("trailing")}{match.group("ending") or ""}'
                )
                page_path.write_bytes("".join(lines).encode("utf-8"))
                return True
            return False
    except Exception:
        return False


def _path_to_colon(page_path: str) -> str:
    """Convert /Foo/Bar/Bar.md -> Foo:Bar."""
    cleaned = page_path.strip().strip("/")
    if not cleaned:
        return ""
    parts = cleaned.split("/")
    if parts:
        parts[-1] = strip_page_suffix(parts[-1])
    if len(parts) >= 2 and parts[-1] == parts[-2]:
        parts = parts[:-1]
    parts = [p.replace(" ", "_") for p in parts]
    return ":".join(parts)


def _link_leaf(link: str) -> str:
    """Extract the leaf name from a link target (colon or path)."""
    text = (link or "").strip()
    if not text:
        return ""
    if text.startswith(":"):
        text = text.lstrip(":")
    if "#" in text:
        text = text.split("#", 1)[0]
    if "/" in text:
        p = Path(text)
        if p.suffix.lower() in PAGE_SUFFIXES:
            return p.stem
        return p.name
    parts = text.split(":")
    return parts[-1] if parts else text


def _link_replacements(path_map: dict[str, str]) -> list[tuple[str, str]]:
    if not path_map:
        return []
    replacements: list[tuple[str, str]] = []
    seen_pairs: set[tuple[str, str]] = set()
    for old_path, new_path in path_map.items():
        raw_old_path = old_path
        raw_new_path = new_path
        try:
            from sp.app.config import _collapse_duplicate_leaf_path
            old_path = _collapse_duplicate_leaf_path(old_path)
            new_path = _collapse_duplicate_leaf_path(new_path)
        except Exception:
            pass
        try:
            old_colon = _path_to_colon(old_path)
            new_colon = _path_to_colon(new_path)
        except Exception:
            old_colon = ""
            new_colon = ""
        if raw_old_path and raw_new_path and (raw_old_path, raw_new_path) not in seen_pairs:
            replacements.append((raw_old_path, raw_new_path))
            seen_pairs.add((raw_old_path, raw_new_path))
        if raw_old_path.endswith(PAGE_SUFFIX):
            raw_legacy_old = raw_old_path[: -len(PAGE_SUFFIX)] + LEGACY_SUFFIX
            if (raw_legacy_old, raw_new_path) not in seen_pairs:
                replacements.append((raw_legacy_old, raw_new_path))
                seen_pairs.add((raw_legacy_old, raw_new_path))
        if old_path and new_path and (old_path, new_path) not in seen_pairs:
            replacements.append((old_path, new_path))
            seen_pairs.add((old_path, new_path))
        if old_path.endswith(PAGE_SUFFIX):
            legacy_old = old_path[: -len(PAGE_SUFFIX)] + LEGACY_SUFFIX
            if (legacy_old, new_path) not in seen_pairs:
                replacements.append((legacy_old, new_path))
                seen_pairs.add((legacy_old, new_path))
        if old_colon and new_colon and (old_colon, new_colon) not in seen_pairs:
            replacements.append((old_colon, new_colon))
            seen_pairs.add((old_colon, new_colon))
            # Older vaults can contain wiki targets that repeat the page leaf
            # (``Page:Page``), matching the folder/page-file representation.
            # Preserve support for that spelling without doing a global prose
            # replacement of the bare page name.
            raw_old_parts = raw_old_path.strip("/").split("/")
            raw_new_parts = raw_new_path.strip("/").split("/")
            if raw_old_parts and raw_new_parts:
                raw_old_leaf = strip_page_suffix(raw_old_parts[-1]).replace(" ", "_")
                raw_new_leaf = strip_page_suffix(raw_new_parts[-1]).replace(" ", "_")
                if len(raw_old_parts) >= 2 and raw_old_leaf == raw_old_parts[-2].replace(" ", "_"):
                    duplicated_old = f"{old_colon}:{raw_old_leaf}"
                    duplicated_new = f"{new_colon}:{raw_new_leaf}"
                    if (duplicated_old, duplicated_new) not in seen_pairs:
                        replacements.append((duplicated_old, duplicated_new))
                        seen_pairs.add((duplicated_old, duplicated_new))
            # For root-level pages (no colons in old path), also add :PageName format
            if ":" not in old_colon:
                old_with_colon = f":{old_colon}"
                new_with_colon = f":{new_colon}"
                if (old_with_colon, new_with_colon) not in seen_pairs:
                    replacements.append((old_with_colon, new_with_colon))
                    seen_pairs.add((old_with_colon, new_with_colon))
    # Rewrite leading-colon spellings before their bare equivalents. Otherwise
    # a root-page wiki rewrite can create a suffix that the later ``:Page``
    # replacement sees again (for example Old -> Topics:Old -> Topics:Topics:Old).
    return sorted(
        replacements,
        key=lambda pair: (0 if pair[0].startswith(":") else 1, -len(pair[0])),
    )


def _rewrite_link_content(content: str, replacements: list[tuple[str, str]]) -> str:
    wiki_pattern = re.compile(r"\[(?P<link>[^\]|]+)\|(?P<label>[^\]]*)\]")
    markdown_pattern = re.compile(r"(?P<prefix>\[[^\]]*\]\()(?P<link>[^)]+)(?P<suffix>\))")
    lookup = {old: new for old, new in replacements if old and new and old != new}

    def mapped_target(target: str) -> tuple[str, str, str] | None:
        raw = target.strip()
        wrapped = raw.startswith("<") and raw.endswith(">")
        if wrapped:
            raw = raw[1:-1]
        base, marker, anchor = raw.partition("#")
        mapped = lookup.get(base)
        if mapped is None:
            return None
        final = mapped + (marker + anchor if marker else "")
        if wrapped:
            final = f"<{final}>"
        return base, mapped, final

    def replace_wiki(match: re.Match) -> str:
        target = mapped_target(match.group("link"))
        if target is None:
            return match.group(0)
        old, new, final = target
        label = match.group("label")
        old_leaf = _link_leaf(old)
        normalized_label = label.strip()
        new_label = label
        if normalized_label and old_leaf:
            if normalized_label in {old_leaf, old_leaf.replace("_", " ")}:
                new_label = _link_leaf(new).replace("_", " ")
        return f"[{final}|{new_label}]"

    def replace_markdown(match: re.Match) -> str:
        target = mapped_target(match.group("link"))
        if target is None:
            return match.group(0)
        return f"{match.group('prefix')}{target[2]}{match.group('suffix')}"

    updated = wiki_pattern.sub(replace_wiki, content)
    updated = markdown_pattern.sub(replace_markdown, updated)

    # Plain internal links always have a leading colon. Rewrite them in one
    # pass over the already structured-link-updated text so replacements cannot
    # cascade into one another.
    colon_lookup = {old: new for old, new in lookup.items() if old.startswith(":")}
    if colon_lookup:
        alternatives = "|".join(re.escape(old) for old in sorted(colon_lookup, key=len, reverse=True))
        colon_pattern = re.compile(rf"(?<![\w:])(?P<link>{alternatives})(?![:\w])")
        updated = colon_pattern.sub(lambda match: colon_lookup[match.group("link")], updated)
    return updated


def plan_link_updates(
    root: Path,
    path_map: dict[str, str],
    *,
    strict: bool = True,
) -> dict[str, bytes]:
    """Return final-path page bytes that must change for a combined path map."""
    replacements = _link_replacements(path_map)
    if not replacements:
        return {}
    updates: dict[str, bytes] = {}
    for suffix in PAGE_SUFFIXES:
        for txt_file in sorted(root.rglob(f"*{suffix}")):
            if suffix == LEGACY_SUFFIX and txt_file.with_suffix(PAGE_SUFFIX).exists():
                continue
            if ".stillpoint" in txt_file.parts:
                continue
            try:
                with file_content_lock(txt_file):
                    content = txt_file.read_bytes().decode("utf-8")
                    updated = _rewrite_link_content(content, replacements)
                    if updated != content:
                        rel = f"/{txt_file.relative_to(root).as_posix()}"
                        updates[rel] = updated.encode("utf-8")
            except Exception as exc:
                if strict:
                    raise
                print(f"{_ANSI_BLUE}[API] Skipped unreadable link source {txt_file}: {exc}{_ANSI_RESET}")
    return updates


def apply_link_updates(
    root: Path,
    updates: dict[str, bytes],
    *,
    strict: bool = True,
) -> list[str]:
    """Atomically apply a precomputed link rewrite plan."""
    touched: list[str] = []
    for rel_path, content in sorted(updates.items()):
        txt_file = _resolve_folder(root, str(Path(rel_path.lstrip("/")).parent)) / Path(rel_path).name
        try:
            if not txt_file.exists():
                raise FileNotFoundError(txt_file)
            with file_content_lock(txt_file):
                _atomic_replace_bytes(txt_file, content, tag="reorg-link")
            touched.append(rel_path)
            print(f"{_ANSI_BLUE}[API] Link rewrite file: {rel_path}{_ANSI_RESET}")
        except Exception as exc:
            if strict:
                raise
            print(f"{_ANSI_BLUE}[API] Failed to rewrite links for {txt_file}: {exc}{_ANSI_RESET}")
    return touched


def update_links_on_disk(root: Path, path_map: dict[str, str]) -> list[str]:
    """Rewrite page links across the vault based on a path map."""
    if not path_map:
        return []
    print(f"{_ANSI_BLUE}[API] /api/vault/update-links start{_ANSI_RESET}")
    updates = plan_link_updates(root, path_map, strict=False)
    touched = apply_link_updates(root, updates, strict=False)
    print(f"{_ANSI_BLUE}[API] /api/vault/update-links complete touched={len(touched)}{_ANSI_RESET}")
    return touched
