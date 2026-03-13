from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QObject, QTimer, Signal

from sp.server import search_index
from sp.server.adapters.files import LEGACY_SUFFIX, PAGE_SUFFIX, PAGE_SUFFIXES


class PeriodicSearchIndexSync(QObject):
    statusReady = Signal(str, int)

    def __init__(
        self,
        parent: QObject,
        *,
        is_enabled: Callable[[], bool],
        is_remote_mode: Callable[[], bool],
        get_vault_root: Callable[[], Optional[str]],
        get_db_path: Callable[[], Optional[str]],
        log_fn: Callable[[str], None],
        interval_ms: int = 30 * 60 * 1000,
    ) -> None:
        super().__init__(parent)
        self._is_enabled = is_enabled
        self._is_remote_mode = is_remote_mode
        self._get_vault_root = get_vault_root
        self._get_db_path = get_db_path
        self._log = log_fn
        self._in_progress = False
        self._suspend_count = 0
        self._idle_event = threading.Event()
        self._idle_event.set()

        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.setSingleShot(False)
        self._timer.timeout.connect(self.maybe_run)

    def update_timer(self) -> None:
        if self._suspend_count > 0:
            if self._timer.isActive():
                self._timer.stop()
            return
        should_run = bool(self._is_enabled() and self._get_vault_root() and not self._is_remote_mode())
        if should_run:
            if not self._timer.isActive():
                self._timer.start()
                self.maybe_run()
        else:
            if self._timer.isActive():
                self._timer.stop()

    def stop(self) -> None:
        if self._timer.isActive():
            self._timer.stop()

    def suspend(self, reason: str = "") -> None:
        self._suspend_count += 1
        if self._timer.isActive():
            self._timer.stop()
        suffix = f" ({reason})" if reason else ""
        self._log(f"[SearchIndex] periodic sync suspended{suffix}")

    def resume(self, reason: str = "") -> None:
        if self._suspend_count > 0:
            self._suspend_count -= 1
        if self._suspend_count == 0:
            suffix = f" ({reason})" if reason else ""
            self._log(f"[SearchIndex] periodic sync resumed{suffix}")
            self.update_timer()

    def maybe_run(self) -> None:
        if self._suspend_count > 0:
            return
        if self._is_remote_mode() or not self._get_vault_root():
            return
        if not self._is_enabled():
            return
        if self._in_progress:
            self._log("[SearchIndex] periodic sync skipped: previous run still in progress")
            print("[SearchIndex] periodic sync skipped (already running)")
            return

        local_vault_root = self._get_vault_root()
        db_path = self._get_db_path()
        if not db_path or not local_vault_root:
            return

        self._in_progress = True
        self._idle_event.clear()
        self._log("[SearchIndex] periodic sync started")
        print(
            "[SearchIndex] periodic sync starting "
            f"vault={local_vault_root} db={db_path}"
        )

        def worker() -> None:
            run_started = time.perf_counter()
            files_scanned = 0
            stale_candidates = 0
            stale_processed = 0
            stale_deleted = 0
            pages_upserted = 0
            upsert_candidates = 0
            read_errors = 0
            delete_errors = 0
            upsert_errors = 0
            status = "ok"
            scan_log_every = 250
            upsert_log_every = 100
            stale_log_every = 100
            try:
                root = Path(local_vault_root)
                conn = sqlite3.connect(db_path, check_same_thread=False, timeout=0.75)
                try:
                    conn.execute("PRAGMA busy_timeout = 750")
                except Exception:
                    pass
                try:
                    file_map: dict[str, tuple[int, str]] = {}
                    for suffix in PAGE_SUFFIXES:
                        for page_file in sorted(root.rglob(f"*{suffix}")):
                            if suffix == LEGACY_SUFFIX and page_file.with_suffix(PAGE_SUFFIX).exists():
                                continue
                            rel_path = f"/{page_file.relative_to(root).as_posix()}"
                            try:
                                mtime = int(page_file.stat().st_mtime)
                                content = page_file.read_text(encoding="utf-8")
                                file_map[rel_path] = (mtime, content)
                                files_scanned += 1
                                if files_scanned % scan_log_every == 0:
                                    print(f"[SearchIndex] progress scanned={files_scanned}")
                            except Exception:
                                read_errors += 1
                                continue

                    existing_rows = conn.execute("SELECT path, mtime FROM pages_search_index").fetchall()
                    existing_map = {
                        row[0]: int(row[1])
                        for row in existing_rows
                        if row and row[0]
                    }
                    existing_paths = set(existing_map.keys())
                    current_paths = set(file_map.keys())
                    stale_candidates = len(existing_paths - current_paths)
                    upsert_candidates = sum(
                        1
                        for path, (mtime, _content) in file_map.items()
                        if existing_map.get(path) != mtime
                    )
                    unchanged_rows = len(file_map) - upsert_candidates
                    print(
                        "[SearchIndex] periodic sync plan "
                        f"files_on_disk={len(file_map)} indexed_rows={len(existing_paths)} "
                        f"upsert_candidates={upsert_candidates} unchanged={unchanged_rows} "
                        f"stale_candidates={stale_candidates}"
                    )

                    for stale_path in sorted(existing_paths - current_paths):
                        stale_processed += 1
                        try:
                            ok = search_index.delete_page(conn, stale_path)
                            if ok:
                                stale_deleted += 1
                            else:
                                delete_errors += 1
                        except Exception:
                            delete_errors += 1
                            continue
                        if stale_processed % stale_log_every == 0:
                            print(
                                "[SearchIndex] progress "
                                f"stale_processed={stale_processed}/{stale_candidates} "
                                f"stale_deleted={stale_deleted} delete_errors={delete_errors}"
                            )

                    upsert_processed = 0
                    for path, (mtime, content) in file_map.items():
                        if existing_map.get(path) == mtime:
                            continue
                        upsert_processed += 1
                        try:
                            ok = search_index.upsert_page(conn, path, mtime, content)
                            if ok:
                                pages_upserted += 1
                            else:
                                upsert_errors += 1
                        except Exception:
                            upsert_errors += 1
                            continue
                        if upsert_processed % upsert_log_every == 0:
                            print(
                                "[SearchIndex] progress "
                                f"upsert_processed={upsert_processed}/{upsert_candidates} "
                                f"upserted={pages_upserted} upsert_errors={upsert_errors}"
                            )
                finally:
                    conn.close()
            except Exception as exc:
                status = f"error: {exc}"
            finally:
                elapsed_ms = int((time.perf_counter() - run_started) * 1000)
                total_errors = read_errors + delete_errors + upsert_errors
                status_line = (
                    "Search index sync: "
                    f"scanned {files_scanned}, indexed {pages_upserted}, removed {stale_deleted}"
                )
                if total_errors:
                    status_line += f", errors {total_errors}"
                if status != "ok":
                    status_line = f"Search index sync failed: {status}"
                print(
                    "[SearchIndex] periodic sync finished "
                    f"status={status} scanned={files_scanned} upsert_candidates={upsert_candidates} "
                    f"upserted={pages_upserted} stale_candidates={stale_candidates} deleted={stale_deleted} "
                    f"read_errors={read_errors} delete_errors={delete_errors} upsert_errors={upsert_errors} "
                    f"duration_ms={elapsed_ms}"
                )
                self._log(
                    "[SearchIndex] periodic sync summary "
                    f"status={status} scanned={files_scanned} upsert_candidates={upsert_candidates} upserted={pages_upserted} "
                    f"stale_candidates={stale_candidates} deleted={stale_deleted} "
                    f"read_errors={read_errors} delete_errors={delete_errors} "
                    f"upsert_errors={upsert_errors} duration_ms={elapsed_ms}"
                )
                self.statusReady.emit(status_line, 4000)
                self._in_progress = False
                self._idle_event.set()

        threading.Thread(target=worker, daemon=True).start()

    def wait_for_idle(self, timeout_s: float = 5.0) -> bool:
        if not self._in_progress:
            return True
        return self._idle_event.wait(timeout=max(0.0, float(timeout_s)))
