from __future__ import annotations

import threading

import httpx

from sp.server import file_ops
from sp.server.adapters import files


class _Response:
    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.request = httpx.Request("GET", "http://localhost/test")

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def test_link_rewrite_waits_for_editor_write_and_preserves_latest_content(tmp_path) -> None:
    root = tmp_path / "vault"
    page = root / "Ref" / "Ref.md"
    page.parent.mkdir(parents=True)
    page.write_text("[Old|Old]\n", encoding="utf-8")

    result: list[list[str]] = []

    def rewrite() -> None:
        result.append(
            file_ops.update_links_on_disk(
                root,
                {"/Old/Old.md": "/New/New.md"},
            )
        )

    with files.file_content_lock(page):
        worker = threading.Thread(target=rewrite)
        worker.start()
        files.write_file(root, "/Ref/Ref.md", "[Old|Old]\nuser edit\n")
        assert worker.is_alive()

    worker.join(timeout=2)

    assert not worker.is_alive()
    assert result == [["/Ref/Ref.md"]]
    assert page.read_text(encoding="utf-8") == "[New|New]\nuser edit\n"


def test_window_serializes_background_link_update_jobs(main_window, monkeypatch) -> None:
    posted: list[dict] = []
    statuses = [
        {"status": "completed", "message": "Updated links in 1 page(s)"},
        {"status": "completed", "message": "Updated links in 2 page(s)"},
    ]

    def post(path: str, json=None):
        assert path == "/api/vault/update-links/background"
        posted.append(dict((json or {}).get("path_map") or {}))
        return _Response({"job_id": f"job-{len(posted)}"})

    def get(path: str):
        assert path.startswith("/api/vault/update-links/status/job-")
        return _Response(statuses.pop(0))

    monkeypatch.setattr(main_window.http, "post", post)
    monkeypatch.setattr(main_window.http, "get", get)
    main_window.rewrite_backlinks_on_move = True

    main_window._queue_background_link_update({"/A/A.md": "/B/B.md"})
    main_window._queue_background_link_update({"/B/B.md": "/C/C.md"})

    assert posted == [{"/A/A.md": "/B/B.md"}]
    assert main_window._link_update_job_id == "job-1"

    main_window._poll_background_link_update()

    assert posted == [
        {"/A/A.md": "/B/B.md"},
        {"/B/B.md": "/C/C.md"},
    ]
    assert main_window._link_update_job_id == "job-2"

    main_window._poll_background_link_update()

    assert main_window._link_update_job_id is None
    assert not main_window._link_update_poll_timer.isActive()


def test_server_background_job_reports_completion(tmp_path, monkeypatch) -> None:
    from sp.server import api

    job_id = "test-link-update-job"
    with api._LINK_UPDATE_JOBS_LOCK:
        api._LINK_UPDATE_JOBS[job_id] = {
            "status": "queued",
            "message": "Waiting to update links…",
            "touched": 0,
        }
    monkeypatch.setattr(
        api.file_ops,
        "update_links_on_disk",
        lambda root, path_map: ["/One/One.md", "/Two/Two.md"],
    )

    api._run_link_update_job(
        job_id,
        tmp_path,
        {"/Old/Old.md": "/New/New.md"},
    )

    with api._LINK_UPDATE_JOBS_LOCK:
        result = dict(api._LINK_UPDATE_JOBS.pop(job_id))
    assert result == {
        "status": "completed",
        "message": "Updated links in 2 page(s)",
        "touched": 2,
    }
