from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path

from sp.server.adapters.files import list_files_activity_between


def _write_page(root: Path, rel: str) -> Path:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# test\n", encoding="utf-8")
    return target


def test_list_files_activity_between_modes(tmp_path: Path) -> None:
    page_created_only = _write_page(tmp_path, "Journal/2026/02/13/Alpha/Alpha.md")
    page_edited_today = _write_page(tmp_path, "Journal/2026/02/13/Beta/Beta.md")

    now = datetime.now()
    yesterday = now - timedelta(days=1)
    # Keep creation time as "now" but move modified time to yesterday.
    os.utime(page_created_only, (yesterday.timestamp(), yesterday.timestamp()))

    start = now.date()
    end = now.date()

    edited = list_files_activity_between(tmp_path, start, end, mode="edited")
    edited_paths = {entry["path"] for entry in edited}
    assert "/Journal/2026/02/13/Beta/Beta.md" in edited_paths
    assert "/Journal/2026/02/13/Alpha/Alpha.md" not in edited_paths

    created = list_files_activity_between(tmp_path, start, end, mode="created")
    created_paths = {entry["path"] for entry in created}
    assert "/Journal/2026/02/13/Alpha/Alpha.md" in created_paths
    assert "/Journal/2026/02/13/Beta/Beta.md" in created_paths

    both = list_files_activity_between(tmp_path, start, end, mode="both")
    both_paths = {entry["path"] for entry in both}
    assert "/Journal/2026/02/13/Alpha/Alpha.md" in both_paths
    assert "/Journal/2026/02/13/Beta/Beta.md" in both_paths
    for entry in both:
        assert "event" in entry
        assert "event_time" in entry
