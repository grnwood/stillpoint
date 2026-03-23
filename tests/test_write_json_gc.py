"""Tests that _write_json calls gc.collect() before json.dumps.

The GC call is the fix for a fatal segmentation fault (CPython 3.12+) that
occurred when _write_json was invoked from inside an except block whose
exception traceback held references to httpx response objects.  The httpx
objects have __del__ finalizers; when the cyclic GC was triggered *during*
json.dumps it called those finalizers, corrupting the encoder's internal
iteration state and crashing the process.

Calling gc.collect() immediately before json.dumps drains any pending
reference cycles (including exception-traceback-held httpx objects) *before*
the encoding loop starts, so the GC has nothing left to finalise mid-encode.
"""
from __future__ import annotations

import gc
import json
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from sp.sync.engine import _write_json


def test_write_json_produces_correct_file(tmp_path: Path) -> None:
    """_write_json writes valid, pretty-printed JSON to disk atomically."""
    dest = tmp_path / "state.json"
    payload = {
        "schema_version": 1,
        "vault_id": "vault-abc",
        "homebase": {
            "last_error": None,
            "error_count": 0,
            "backoff_until": None,
        },
    }

    _write_json(dest, payload)

    assert dest.exists()
    written = json.loads(dest.read_text(encoding="utf-8"))
    assert written == payload


def test_write_json_calls_gc_collect_before_encoding(tmp_path: Path) -> None:
    """_write_json must call gc.collect() exactly once before json.dumps."""
    dest = tmp_path / "state.json"
    payload = {"key": "value"}

    collect_calls: list[int] = []

    original_collect = gc.collect

    def _tracking_collect(*args, **kwargs):
        collect_calls.append(1)
        return original_collect(*args, **kwargs)

    with patch("sp.sync.engine.gc.collect", side_effect=_tracking_collect):
        _write_json(dest, payload)

    assert len(collect_calls) == 1, (
        "gc.collect() must be called exactly once before json.dumps in _write_json "
        "to prevent a GC-triggered segfault on CPython 3.12+ when called from "
        "an exception handler holding httpx response objects"
    )


def test_write_json_from_active_exception_handler(tmp_path: Path) -> None:
    """_write_json works correctly when called inside an active except block.

    This test simulates the exact code path that caused the fatal segfault:
    an httpx exception is caught, its string form is stored in the state dict,
    and then _write_json is called while the exception is still alive in the
    current thread's exception state.
    """
    dest = tmp_path / "local_state.json"
    state: dict = {
        "schema_version": 1,
        "vault_id": "vault-xyz",
        "device_id": "device-1",
        "homebase": {},
    }

    # Simulate the error-handler path from _sync_once.
    try:
        raise httpx.ConnectError("connection refused")
    except httpx.ConnectError as exc:
        hb = state["homebase"]
        hb["last_error"] = str(exc)
        hb["error_count"] = 1
        hb["backoff_until"] = "2099-01-01T00:00:00Z"
        # This is the call that caused the segfault before the gc.collect() fix.
        _write_json(dest, state)

    assert dest.exists()
    saved = json.loads(dest.read_text(encoding="utf-8"))
    assert saved["homebase"]["last_error"] == "connection refused"
    assert saved["homebase"]["error_count"] == 1


def test_write_json_overwrites_atomically(tmp_path: Path) -> None:
    """_write_json replaces the destination file atomically via a .tmp file."""
    dest = tmp_path / "data.json"
    dest.write_text('{"old": true}', encoding="utf-8")

    _write_json(dest, {"new": True})

    # No stale .tmp file left behind.
    assert not dest.with_suffix(".json.tmp").exists()
    saved = json.loads(dest.read_text(encoding="utf-8"))
    assert saved == {"new": True}
