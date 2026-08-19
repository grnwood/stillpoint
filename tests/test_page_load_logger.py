from __future__ import annotations

import io
import json

from sp.app.ui.page_load_logger import PageLoadLogger


def _records(stream: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in stream.getvalue().splitlines()]


def test_page_load_logger_emits_steps_and_summary(monkeypatch) -> None:
    ticks = iter((10.0, 10.025, 10.075, 10.080))
    cpu_ticks = iter((3.0, 3.020))
    monkeypatch.setattr("sp.app.ui.page_load_logger.time.perf_counter", lambda: next(ticks))
    monkeypatch.setattr("sp.app.ui.page_load_logger.time.process_time", lambda: next(cpu_ticks))
    stream = io.StringIO()

    logger = PageLoadLogger("/Busy/Busy.md", enabled=True, stream=stream)
    logger.mark("api read complete bytes=1234")
    logger.end("ready for edit")

    records = _records(stream)
    assert [record["type"] for record in records] == [
        "page_load_step",
        "page_load_step",
        "page_load_step",
        "page_load_summary",
    ]
    assert records[1]["step_ms"] == 25.0
    assert records[-1]["elapsed_ms"] == 80.0
    assert records[-1]["cpu_ms"] == 20.0
    assert records[-1]["unattributed_wait_ms"] == 60.0
    assert records[-1]["slowest"][0] == {"label": "ready for edit", "step_ms": 50.0}


def test_page_load_logger_is_silent_when_disabled() -> None:
    stream = io.StringIO()
    logger = PageLoadLogger("/Page/Page.md", enabled=False, stream=stream)
    logger.mark("work")
    logger.end()
    assert stream.getvalue() == ""


def test_page_load_logger_output_failure_never_breaks_navigation() -> None:
    class BrokenStream(io.StringIO):
        def write(self, value: str) -> int:
            raise OSError("disk full")

    logger = PageLoadLogger("/Page/Page.md", enabled=True, stream=BrokenStream())
    logger.mark("still safe")
    logger.end()
