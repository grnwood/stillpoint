"""Explicit speech-to-text requests using existing AI server configuration."""
from pathlib import Path
import shutil
import threading
from urllib.parse import urlsplit

import httpx
from PySide6.QtCore import QObject, Signal

from sp.app import config
from .ai_api import build_auth_headers, compose_url, format_api_error


def transcription_url(server: dict, path: str) -> str:
    base = str(server.get("base_url") or "").rstrip("/")
    if not base:
        raise ValueError("The transcription server has no base URL.")
    parsed = urlsplit(path)
    if parsed.scheme or parsed.netloc or not path.startswith("/") or path.startswith("//"):
        raise ValueError("The transcription API path must be a path on the selected server, such as /v1/audio/transcriptions.")
    # Both common server conventions work: https://host and https://host/v1.
    if base.endswith("/v1") and path.startswith("/v1/"):
        path = path[3:]
    return compose_url(base, path)


def transcribe_audio(source: Path, server: dict, model: str, path: str, *, transport=None) -> str:
    if not model.strip():
        raise ValueError("Choose a default model for transcription in Preferences → Audio.")
    url = transcription_url(server, path)
    headers = build_auth_headers(server)
    timeout = httpx.Timeout(connect=config.load_ai_chat_connect_timeout(), read=120, write=120, pool=5)
    media_type = {".m4a": "audio/mp4", ".wav": "audio/wav", ".mp3": "audio/mpeg", ".flac": "audio/flac", ".ogg": "audio/ogg"}.get(source.suffix.lower(), "application/octet-stream")
    try:
        with source.open("rb") as audio, httpx.Client(
            headers=headers, verify=bool(server.get("verify_ssl", True)), timeout=timeout, transport=transport,
        ) as client:
            response = client.post(url, data={"model": model, "response_format": "json"},
                                   files={"file": (source.name, audio, media_type)})
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPStatusError as exc:
        raise ValueError(format_api_error(exc, model)) from exc
    text = payload.get("text") if isinstance(payload, dict) else None
    if not isinstance(text, str) or not text.strip():
        raise ValueError("The transcription server returned no transcript text. Check that the selected model supports audio transcription.")
    return text.strip()


class _TranscriptionSignals(QObject):
    finished = Signal(object, str, str)


class TranscriptionWorker:
    def __init__(self, source, server, model, path, temporary_dir=None):
        self.signals = _TranscriptionSignals()
        self.source, self.server, self.model, self.path = source, server, model, path
        self.temporary_dir = temporary_dir
        self.cancelled = threading.Event()

    def start(self):
        # A remote inference call may outlive its popup. It must not keep Qt's
        # global thread pool (and application shutdown) waiting for that server.
        threading.Thread(target=self.run, name="voice-note-transcription", daemon=True).start()

    def run(self):
        text = error = ""
        try:
            if not self.cancelled.is_set():
                text = transcribe_audio(self.source, self.server, self.model, self.path)
        except Exception as exc:
            error = str(exc)
        finally:
            if self.cancelled.is_set() and self.temporary_dir:
                shutil.rmtree(self.temporary_dir, ignore_errors=True)
        if not self.cancelled.is_set():
            self.signals.finished.emit(self, text, error)
