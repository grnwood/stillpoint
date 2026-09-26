"""Persistent, minimal filename catalog for Folder Navigator."""
from __future__ import annotations

from pathlib import Path
import os
import sqlite3
import time
from typing import Iterable


CATALOG_DIRECTORY = ".sp_folder"
CATALOG_FILENAME = "catalog.sqlite3"
SCHEMA_VERSION = 2


class CatalogError(RuntimeError):
    """Raised when a root cannot host its Folder Navigator catalog."""


def _like_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class FolderCatalog:
    """A small per-root SQLite index containing filename metadata only.

    Connections are intentionally short-lived so the UI and catalog worker can
    use the same database without sharing sqlite connection objects across
    threads.
    """

    def __init__(self, root: Path):
        self.root = root.resolve(strict=True)
        self.directory = self.root / CATALOG_DIRECTORY
        self.path = self.directory / CATALOG_FILENAME
        try:
            self.directory.mkdir(exist_ok=True)
            self._initialize()
        except (OSError, sqlite3.Error) as exc:
            raise CatalogError(f"Could not create {self.path}: {exc}") from exc

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS files (
                    relative_path TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    parent TEXT NOT NULL,
                    mtime_ns INTEGER NOT NULL,
                    size INTEGER NOT NULL,
                    hidden INTEGER NOT NULL DEFAULT 0,
                    ignored INTEGER NOT NULL DEFAULT 0,
                    generation INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS files_name_idx
                    ON files(name COLLATE NOCASE);
                CREATE INDEX IF NOT EXISTS files_parent_idx
                    ON files(parent);
                CREATE INDEX IF NOT EXISTS files_generation_idx
                    ON files(generation);
                CREATE TABLE IF NOT EXISTS skipped_directories (
                    relative_path TEXT PRIMARY KEY,
                    entry_count INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    generation INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS skipped_generation_idx
                    ON skipped_directories(generation);
                """
            )
            connection.execute(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )

    def count(self) -> int:
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) FROM files").fetchone()
        return int(row[0]) if row else 0

    def begin_refresh(self) -> int:
        """Allocate a generation for an incremental full-tree refresh."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM metadata WHERE key = 'generation'"
            ).fetchone()
            generation = (int(row[0]) if row else 0) + 1
            connection.execute(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES('generation', ?)",
                (str(generation),),
            )
            connection.execute(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES('scan_state', 'running')"
            )
        return generation

    def current_generation(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM metadata WHERE key = 'generation'"
            ).fetchone()
        return int(row[0]) if row else 0

    def upsert_paths(
        self,
        paths: Iterable[Path],
        ignored: set[Path] | None = None,
        generation: int | None = None,
    ) -> int:
        """Insert existing in-root files and return the number recorded."""
        preserve_ignored = ignored is None
        ignored = ignored or set()
        generation = self.current_generation() if generation is None else generation
        rows = []
        for candidate in paths:
            try:
                path = candidate.resolve(strict=True)
                relative = path.relative_to(self.root)
                if not path.is_file() or CATALOG_DIRECTORY in relative.parts:
                    continue
                stat = path.stat()
            except (OSError, ValueError):
                continue
            relative_text = relative.as_posix()
            hidden = any(part.startswith(".") for part in relative.parts)
            rows.append(
                (
                    relative_text,
                    path.name,
                    relative.parent.as_posix() if relative.parent != Path(".") else "",
                    stat.st_mtime_ns,
                    stat.st_size,
                    int(hidden),
                    int(candidate in ignored or path in ignored),
                    generation,
                )
            )
        if not rows:
            return 0
        ignored_update = "files.ignored" if preserve_ignored else "excluded.ignored"
        with self._connect() as connection:
            connection.executemany(
                f"""
                INSERT INTO files(
                    relative_path, name, parent, mtime_ns, size,
                    hidden, ignored, generation
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(relative_path) DO UPDATE SET
                    name = excluded.name,
                    parent = excluded.parent,
                    mtime_ns = excluded.mtime_ns,
                    size = excluded.size,
                    hidden = excluded.hidden,
                    ignored = {ignored_update},
                    generation = excluded.generation
                """,
                rows,
            )
        return len(rows)

    def finish_refresh(self, generation: int, *, complete: bool) -> None:
        """Commit a completed generation, preserving old rows on cancellation."""
        with self._connect() as connection:
            if complete:
                connection.execute(
                    "DELETE FROM files WHERE generation != ?", (generation,)
                )
                connection.execute(
                    "DELETE FROM skipped_directories WHERE generation != ?",
                    (generation,),
                )
                connection.execute(
                    "INSERT OR REPLACE INTO metadata(key, value) VALUES('last_full_scan_ns', ?)",
                    (str(time.time_ns()),),
                )
            connection.execute(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES('scan_state', ?)",
                ("complete" if complete else "canceled",),
            )

    def record_skipped_directories(
        self,
        directories: Iterable[tuple[Path, int]],
        generation: int,
    ) -> int:
        rows = []
        for path, entry_count in directories:
            try:
                relative = path.resolve().relative_to(self.root).as_posix()
            except (OSError, ValueError):
                continue
            rows.append((relative, int(entry_count), "entry_limit", generation))
        if not rows:
            return 0
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT INTO skipped_directories(
                    relative_path, entry_count, reason, generation
                ) VALUES(?, ?, ?, ?)
                ON CONFLICT(relative_path) DO UPDATE SET
                    entry_count = excluded.entry_count,
                    reason = excluded.reason,
                    generation = excluded.generation
                """,
                rows,
            )
        return len(rows)

    def skipped_directories(self, limit: int = 100) -> list[tuple[Path, int]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT relative_path, entry_count
                FROM skipped_directories
                ORDER BY relative_path COLLATE NOCASE
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        return [(self.root / relative, int(count)) for relative, count in rows]

    def set_ui_states(self, values: dict[str, str]) -> None:
        rows = [(f"ui.{key}", value) for key, value in values.items()]
        if not rows:
            return
        with self._connect() as connection:
            connection.executemany(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES(?, ?)", rows
            )

    def ui_states(self) -> dict[str, str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT key, value FROM metadata WHERE key LIKE 'ui.%'"
            ).fetchall()
        return {key[3:]: value for key, value in rows}

    def candidates(
        self,
        query: str,
        scope: Path,
        *,
        include_excluded: bool = False,
        limit: int = 4000,
    ) -> list[Path]:
        """Return a bounded fuzzy-compatible candidate set from cached rows."""
        try:
            scope_relative = scope.resolve().relative_to(self.root)
        except (OSError, ValueError):
            return []
        clauses = []
        parameters: list[object] = []
        if scope_relative != Path("."):
            prefix = _like_escape(scope_relative.as_posix()) + "/%"
            clauses.append("relative_path LIKE ? ESCAPE '\\'")
            parameters.append(prefix)
        if not include_excluded:
            clauses.append("hidden = 0 AND ignored = 0")
        normalized = query.strip().casefold()
        if normalized:
            # A subsequence LIKE keeps the SQL result compatible with the
            # in-memory fuzzy scorer while bounding work for very large roots.
            pattern = "%" + "%".join(_like_escape(char) for char in normalized) + "%"
            clauses.append("lower(relative_path) LIKE ? ESCAPE '\\'")
            parameters.append(pattern)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        parameters.append(max(1, int(limit)))
        sql = (
            "SELECT relative_path FROM files"
            + where
            + " ORDER BY name COLLATE NOCASE, relative_path COLLATE NOCASE LIMIT ?"
        )
        with self._connect() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return [self.root / row[0] for row in rows]

    def is_excluded(self, path: Path) -> bool:
        try:
            relative = path.resolve().relative_to(self.root).as_posix()
        except (OSError, ValueError):
            return True
        with self._connect() as connection:
            row = connection.execute(
                "SELECT hidden, ignored FROM files WHERE relative_path = ?",
                (relative,),
            ).fetchone()
        return bool(row and (row[0] or row[1]))
