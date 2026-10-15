"""Client for the isolated Qt WebEngine Mermaid rendering worker."""

from __future__ import annotations

import atexit
from collections import deque
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import uuid


class MermaidWebRendererError(RuntimeError):
    pass


class MermaidWebRendererClient:
    """Serialize render requests through one reusable browser subprocess."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None
        self._responses: queue.Queue[dict] = queue.Queue()
        self._stderr: deque[str] = deque(maxlen=40)

    @staticmethod
    def _command() -> list[str]:
        if bool(getattr(sys, "frozen", False)):
            return [sys.executable, "--mermaid-renderer"]
        return [sys.executable, "-m", "sp.app.mermaid_renderer_process"]

    def _reader(self, stream) -> None:
        for line in stream:
            try:
                payload = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(payload, dict):
                self._responses.put(payload)

    def _stderr_reader(self, stream) -> None:
        for line in stream:
            text = line.rstrip()
            if text:
                self._stderr.append(text)

    def _start(self) -> None:
        if self._process is not None and self._process.poll() is None:
            return
        self._stop()
        self._responses = queue.Queue()
        self._stderr.clear()
        env = os.environ.copy()
        # Folder Navigator disables WebEngine in its own long-lived process.
        # This dedicated child is deliberately the isolation boundary.
        env.pop("SP_MERMAID_DISABLE_INPROCESS_WEBENGINE", None)
        self._process = subprocess.Popen(
            self._command(),
            cwd=str(Path(__file__).resolve().parents[2]),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        assert self._process.stdout is not None
        assert self._process.stderr is not None
        threading.Thread(
            target=self._reader, args=(self._process.stdout,), daemon=True
        ).start()
        threading.Thread(
            target=self._stderr_reader, args=(self._process.stderr,), daemon=True
        ).start()
        deadline = time.monotonic() + 15.0
        while True:
            if self._process.poll() is not None:
                raise MermaidWebRendererError(
                    self._diagnostic("renderer exited during startup")
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise MermaidWebRendererError(
                    self._diagnostic("renderer startup timed out")
                )
            try:
                ready = self._responses.get(timeout=min(0.1, remaining))
                break
            except queue.Empty:
                continue
        if ready.get("type") != "ready":
            raise MermaidWebRendererError(self._diagnostic(ready.get("error") or "renderer failed to start"))

    def _diagnostic(self, message: str) -> str:
        details = [str(message)]
        if self._process is not None and self._process.poll() is not None:
            details.append(f"renderer exit code: {self._process.returncode}")
        if self._stderr:
            details.append("renderer stderr:\n" + "\n".join(self._stderr))
        return "\n".join(details)

    def _stop(self) -> None:
        process, self._process = self._process, None
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2.0)

    def close(self) -> None:
        with self._lock:
            self._stop()

    def render_svg(self, source: str, *, theme: str = "neutral") -> dict:
        """Return the worker response, restarting once after transport failure."""
        with self._lock:
            last_error: Exception | None = None
            for attempt in range(2):
                request_id = uuid.uuid4().hex
                try:
                    self._start()
                    assert self._process is not None
                    assert self._process.stdin is not None
                    self._process.stdin.write(json.dumps({
                        "id": request_id,
                        "source": source,
                        "theme": theme,
                    }, ensure_ascii=False) + "\n")
                    self._process.stdin.flush()
                    while True:
                        response = self._responses.get(timeout=20.0)
                        if response.get("id") == request_id:
                            return response
                except (BrokenPipeError, OSError, queue.Empty, MermaidWebRendererError) as exc:
                    last_error = exc
                    self._stop()
                    if attempt == 0:
                        continue
            raise MermaidWebRendererError(
                self._diagnostic(last_error or "renderer request failed")
            )


_CLIENT: MermaidWebRendererClient | None = None
_CLIENT_LOCK = threading.Lock()


def shared_mermaid_web_renderer() -> MermaidWebRendererClient:
    global _CLIENT
    with _CLIENT_LOCK:
        if _CLIENT is None:
            _CLIENT = MermaidWebRendererClient()
            atexit.register(_CLIENT.close)
        return _CLIENT
