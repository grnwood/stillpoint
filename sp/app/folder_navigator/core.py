"""Filesystem-only policies for Folder Navigator. No Stillpoint server imports."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import codecs
import fnmatch
import os
import re
import stat
import tempfile
from typing import Callable, Iterator

MAX_EDIT_BYTES = int(os.environ.get("STILLPOINT_FOLDER_MAX_EDIT_BYTES", 8 * 1024 * 1024))
MAX_SEARCH_BYTES = int(os.environ.get("STILLPOINT_FOLDER_MAX_SEARCH_BYTES", 2 * 1024 * 1024))
MAX_RESULTS = int(os.environ.get("STILLPOINT_FOLDER_MAX_RESULTS", 1000))
MAX_IMAGE_PIXELS = int(os.environ.get("STILLPOINT_FOLDER_MAX_IMAGE_PIXELS", 40_000_000))
MAX_CONCURRENT_WORK = int(os.environ.get("STILLPOINT_FOLDER_MAX_WORKERS", 2))


def inside(root: Path, candidate: Path) -> bool:
    """Resolve links before checking boundaries (also handles case on Windows)."""
    try:
        root_path = os.path.normcase(str(root.resolve(strict=True)))
        target = os.path.normcase(str(candidate.resolve(strict=True)))
        return os.path.commonpath((root_path, target)) == root_path
    except (OSError, ValueError):
        return False


def fingerprint(path: Path) -> tuple[int, int, int, int] | None:
    try:
        info = path.stat()
        return info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size
    except OSError:
        return None


@dataclass
class TextFile:
    text: str
    encoding: str
    newline: str
    fingerprint: tuple[int, int, int, int]


def read_text(path: Path, limit: int = MAX_EDIT_BYTES) -> TextFile:
    before = fingerprint(path)
    if before is None:
        raise OSError(f"File is unavailable: {path}")
    if before[3] > limit:
        raise ValueError(f"File exceeds the {limit // (1024 * 1024)} MiB text limit")
    data = path.read_bytes()
    encoding = "utf-8"
    if data.startswith(codecs.BOM_UTF8):
        encoding = "utf-8-sig"
    elif data.startswith(codecs.BOM_UTF16_LE) or data.startswith(codecs.BOM_UTF16_BE):
        encoding = "utf-16"
    if b"\0" in data[:8192] and encoding != "utf-16":
        raise ValueError("Binary content cannot be edited as text")
    try:
        text = data.decode(encoding)
    except UnicodeDecodeError as exc:
        raise ValueError("Text encoding is uncertain; open in the default application") from exc
    if any(ord(c) < 32 and c not in "\t\n\r\f" for c in text[:8192]):
        raise ValueError("Binary control characters cannot be edited as text")
    newline = "\r\n" if "\r\n" in text else ("\r" if "\r" in text else "\n")
    return TextFile(text.replace("\r\n", "\n").replace("\r", "\n"), encoding, newline, before)


class ConflictError(Exception):
    pass


def atomic_save(path: Path, text: str, loaded: TextFile, *, overwrite: bool = False) -> tuple[int, int, int, int]:
    if not overwrite and fingerprint(path) != loaded.fingerprint:
        raise ConflictError(f"{path.name} changed on disk since it was opened")
    data = text.replace("\n", loaded.newline).encode(loaded.encoding)
    mode = stat.S_IMODE(path.stat().st_mode)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        if not overwrite and fingerprint(path) != loaded.fingerprint:
            raise ConflictError(f"{path.name} changed while saving")
        os.replace(temporary, path)
        return fingerprint(path)  # type: ignore[return-value]
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def fuzzy_score(query: str, relative: str, *, recent: bool = False, opened: bool = False) -> int | None:
    query = query.casefold().replace("\\", "/")
    relative = relative.casefold().replace("\\", "/")
    name = relative.rsplit("/", 1)[-1]
    if not query:
        return (100 if opened else 0) + (50 if recent else 0)
    pos = -1
    score = 0
    for char in query:
        next_pos = relative.find(char, pos + 1)
        if next_pos < 0:
            return None
        score += 9 if next_pos == pos + 1 else 1
        if next_pos == 0 or relative[next_pos - 1] in "/._- ":
            score += 15
        pos = next_pos
    if query in name:
        score += 80
    if name.startswith(query):
        score += 40
    if query in relative:
        score += 15
    return score + (25 if opened else 0) + (12 if recent else 0) - len(relative) // 12


def ignored(relative: str, patterns: list[str]) -> bool:
    """Basic ignore matcher; git check-ignore is used for full Git semantics in a worktree."""
    result = False
    for pattern in patterns:
        rule = pattern.strip()
        if not rule or rule.startswith("#"):
            continue
        negate = rule.startswith("!")
        rule = rule.lstrip("!").lstrip("/")
        if fnmatch.fnmatch(relative, rule) or fnmatch.fnmatch(relative.rsplit("/", 1)[-1], rule):
            result = not negate
    return result


def walk_files(root: Path, scope: Path, *, hidden: bool = False, ignore: Callable[[Path], bool] | None = None,
               canceled: Callable[[], bool] = lambda: False) -> Iterator[Path]:
    """Walk without following directory links, yielding only canonical in-root files."""
    if not inside(root, scope):
        return
    for directory, directories, files in os.walk(scope, followlinks=False):
        if canceled():
            return
        base = Path(directory)
        directories[:] = [name for name in directories if (hidden or not name.startswith("."))
                          and inside(root, base / name) and not (base / name).is_symlink()
                          and not (ignore and ignore(base / name))]
        for name in files:
            if canceled():
                return
            path = base / name
            if (hidden or not name.startswith(".")) and inside(root, path) and not (ignore and ignore(path)):
                yield path


def content_matches(text: str, query: str, *, case: bool = False, whole: bool = False, regex: bool = False):
    flags = 0 if case else re.IGNORECASE
    pattern = query if regex else re.escape(query)
    if whole:
        pattern = rf"\b(?:{pattern})\b"
    expression = re.compile(pattern, flags)
    for number, line in enumerate(text.splitlines(), 1):
        if expression.search(line):
            yield number, line.strip()[:220]
