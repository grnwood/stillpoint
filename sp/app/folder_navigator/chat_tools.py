"""Local, root-scoped tools for the detached Folder Navigator chat."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import httpx
from PySide6 import QtCore

from sp.app.ui.ai_api import build_api_request, is_unsupported_vision_response, with_vision_images
from sp.app.ui.agent_tool_loop import AGENT_MESSAGE_SCHEMA, parse_agent_message
from sp.rag.attachment_text import extract_attachment_text
from .core import DEFAULT_PRUNED_DIRECTORY_NAMES, ConflictError, atomic_save, read_text


MAX_FILE_BYTES = 256 * 1024
MAX_DOCUMENT_BYTES = 20 * 1024 * 1024
MAX_DOCUMENT_SCAN_CHARS = 500_000
MAX_DOCUMENT_REPLY_CHARS = 15_000
MAX_SEARCH_FILES = 4000
SKIP_DIRECTORIES = DEFAULT_PRUNED_DIRECTORY_NAMES
OCR_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"})
DOCUMENT_TEXT_SUFFIXES = frozenset({".pdf", ".docx", ".pptx", ".xls", ".xlsx", ".xlsm", ".xlsb", ".ods"})
EXTRACTABLE_SUFFIXES = DOCUMENT_TEXT_SUFFIXES | OCR_IMAGE_SUFFIXES

FOLDER_AGENT_PROMPT = f"""You are the Folder Navigator assistant. The selected folder is your only filesystem scope.
Respond with one JSON object matching this schema:
{json.dumps(AGENT_MESSAGE_SCHEMA, separators=(',', ':'))}
Return only JSON. Use type="tool_request" to call tools, then type="final" when done.

Available tools (all paths are relative to the selected folder):
- folder.search: {{"query":"text","limit":20}}. Search file names and text content.
- folder.read: {{"path":"relative/path","query":"optional search phrase"}}. Read a text file or
  extract text from a PDF, Word document, PowerPoint, spreadsheet, or image. For a long
  document, pass query to receive relevant excerpts instead of its opening text.
- folder.create: {{"path":"relative/path","content":"text"}}. Create a new text file; never overwrites.
- folder.write: {{"path":"relative/path","content":"text","mode":"replace|append","expected_mtime_ns":123}}.
  Edit an existing text file. Read it first and pass the returned mtime_ns to prevent stale writes.

Search or read before editing existing files. Use tools for requested file changes and report their actual results.
Attached file labels may begin with /; remove that leading slash in tool arguments.
Paths outside this folder and writes to unsaved editor buffers are rejected.
"""


class FolderChatTools:
    def __init__(self, root: Path, *, dirty_paths: set[Path] | None = None) -> None:
        self.root = root.resolve(strict=True)
        self.dirty_paths = {path.resolve() for path in (dirty_paths or set())}

    def _path(self, raw: object, *, existing: bool) -> Path:
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("A relative path is required")
        given = Path(raw)
        if given.is_absolute():
            raise ValueError("Use a path relative to the selected folder")
        path = (self.root / given).resolve(strict=existing)
        if path == self.root or not path.is_relative_to(self.root):
            raise ValueError("Path is outside the selected folder")
        if ".git" in path.relative_to(self.root).parts:
            raise ValueError("Git metadata is unavailable to folder chat tools")
        return path

    def _ref(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def _read(self, path: Path) -> str:
        if not path.is_file():
            raise FileNotFoundError(self._ref(path))
        if path.suffix.casefold() in EXTRACTABLE_SUFFIXES:
            if path.stat().st_size > MAX_DOCUMENT_BYTES:
                raise ValueError("Document exceeds the 20 MiB extraction limit")
            content = extract_attachment_text(path, max_chars=MAX_DOCUMENT_SCAN_CHARS)
            if not content.strip():
                raise ValueError("No readable text could be extracted from this document")
            return content
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError("File exceeds the 256 KiB text limit")
        return read_text(path, limit=MAX_FILE_BYTES).text

    @staticmethod
    def _excerpts(content: str, query: str) -> tuple[str, int]:
        needle = query.casefold()
        lowered = content.casefold()
        excerpts = []
        position = 0
        used = 0
        matches = 0
        while (found := lowered.find(needle, position)) >= 0:
            matches += 1
            position = found + len(needle)
            if used >= MAX_DOCUMENT_REPLY_CHARS:
                continue
            start = max(0, found - 250)
            end = min(len(content), found + len(query) + 350)
            excerpt = content[start:end].strip()
            excerpts.append(excerpt)
            used += len(excerpt)
        return "\n\n[…]\n\n".join(excerpts)[:MAX_DOCUMENT_REPLY_CHARS], matches

    def call(self, name: str, args: dict) -> dict:
        try:
            if name == "folder.search":
                query = args.get("query")
                if not isinstance(query, str) or not query.strip():
                    raise ValueError("query is required")
                limit = args.get("limit", 20)
                if type(limit) is not int or not 1 <= limit <= 100:
                    raise ValueError("limit must be between 1 and 100")
                matches = []
                scanned = 0
                needle = query.casefold()
                for directory, dirs, files in os.walk(self.root, followlinks=False):
                    dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRECTORIES and
                                     (Path(directory) / d).resolve().is_relative_to(self.root))
                    for filename in sorted(files):
                        path = Path(directory) / filename
                        if not path.resolve().is_relative_to(self.root) or not path.is_file():
                            continue
                        scanned += 1
                        ref = self._ref(path)
                        if needle in ref.casefold():
                            matches.append({"path": ref, "line": None, "snippet": "Filename match"})
                        elif path.suffix.casefold() not in OCR_IMAGE_SUFFIXES and path.stat().st_size <= (
                            MAX_DOCUMENT_BYTES if path.suffix.casefold() in DOCUMENT_TEXT_SUFFIXES
                            else MAX_FILE_BYTES
                        ):
                            try:
                                content = self._read(path)
                            except (OSError, UnicodeError, ValueError):
                                continue
                            for line_no, line in enumerate(content.splitlines(), 1):
                                if needle in line.casefold():
                                    matches.append({"path": ref, "line": line_no, "snippet": line.strip()[:240]})
                                    if len(matches) >= limit:
                                        break
                        if len(matches) >= limit or scanned >= MAX_SEARCH_FILES:
                            break
                    if len(matches) >= limit or scanned >= MAX_SEARCH_FILES:
                        break
                return {"matches": matches[:limit], "scanned_files": scanned,
                        "truncated": scanned >= MAX_SEARCH_FILES or len(matches) >= limit}

            path = self._path(args.get("path"), existing=name != "folder.create")
            if name == "folder.read":
                content = self._read(path)
                query = args.get("query")
                if query is not None and (not isinstance(query, str) or not query.strip()):
                    raise ValueError("query must be a nonempty string")
                if query:
                    content, match_count = self._excerpts(content, query)
                else:
                    match_count = None
                    if path.suffix.casefold() in EXTRACTABLE_SUFFIXES:
                        content = content[:MAX_DOCUMENT_REPLY_CHARS]
                return {"path": self._ref(path), "content": content,
                        "mtime_ns": path.stat().st_mtime_ns, "match_count": match_count}
            if name not in {"folder.create", "folder.write"}:
                raise LookupError(f"Unknown tool: {name}")
            if path.suffix.casefold() in EXTRACTABLE_SUFFIXES:
                raise ValueError("Extracted documents are read only in folder chat")
            content = args.get("content")
            if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_FILE_BYTES:
                raise ValueError("content must be text of at most 256 KiB")
            if path in self.dirty_paths:
                raise FileExistsError("This file has unsaved editor changes; save or close it first")
            if name == "folder.create":
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("x", encoding="utf-8", newline="") as target:
                    target.write(content)
                return {"path": self._ref(path), "created": True}
            mode = args.get("mode", "replace")
            if mode not in {"replace", "append"}:
                raise ValueError("mode must be replace or append")
            expected = args.get("expected_mtime_ns")
            if type(expected) is not int:
                raise ValueError("Read the file and pass expected_mtime_ns before editing")
            original = read_text(path, limit=MAX_FILE_BYTES)
            if original.fingerprint[2] != expected:
                raise FileExistsError("File changed since it was read")
            updated = original.text + content if mode == "append" else content
            if len(updated.encode("utf-8")) > MAX_FILE_BYTES:
                raise ValueError("Updated file exceeds the 256 KiB text limit")
            atomic_save(path, updated, original)
            return {"path": self._ref(path), "written": True, "mtime_ns": path.stat().st_mtime_ns}
        except FileNotFoundError as exc:
            return {"error": {"code": "not_found", "message": str(exc)}}
        except FileExistsError as exc:
            return {"error": {"code": "conflict", "message": str(exc)}}
        except ConflictError as exc:
            return {"error": {"code": "conflict", "message": str(exc)}}
        except (OSError, UnicodeError, ValueError) as exc:
            return {"error": {"code": "invalid_args", "message": str(exc)}}
        except LookupError as exc:
            return {"error": {"code": "not_found", "message": str(exc)}}


class FolderAgentChatWorker(QtCore.QThread):
    toolMessage = QtCore.Signal(str)
    finalMessage = QtCore.Signal(str)
    failed = QtCore.Signal(str)
    visionFallback = QtCore.Signal()
    fileChanged = QtCore.Signal(str)

    def __init__(self, *, server_config: dict, model: str, system_prompt: str,
                 user_prompt: str, root: Path, current_path: str,
                 dirty_paths: set[Path], vision_images: list[str] | None = None,
                 ocr_system_prompt: str | None = None,
                 history: list[tuple[str, str]] | None = None, parent=None) -> None:
        super().__init__(parent)
        self.server_config = server_config
        self.model = model
        self.system_prompt = system_prompt
        self.user_prompt = user_prompt
        self.root = root
        self.current_path = current_path
        self.dirty_paths = dirty_paths
        self.vision_images = vision_images or []
        self.ocr_system_prompt = ocr_system_prompt
        self.history = history or []
        self._cancel_requested = False

    def request_cancel(self) -> None:
        self._cancel_requested = True

    def _send_llm(self, messages: list[dict]) -> str:
        url, headers, verify, timeout, payload = build_api_request(
            self.server_config, messages, self.model, stream=False,
        )
        response = httpx.post(url, json=payload, headers=headers, timeout=timeout, verify=verify)
        if self.vision_images and self.ocr_system_prompt and is_unsupported_vision_response(response):
            messages[0] = {"role": "system", "content": self.ocr_system_prompt}
            for message in messages:
                if message.get("role") == "user" and isinstance(message.get("content"), list):
                    message["content"] = next(
                        (part.get("text", "") for part in message["content"] if part.get("type") == "text"), "",
                    )
                    break
            self.vision_images = []
            self.visionFallback.emit()
            _, _, _, _, payload = build_api_request(
                self.server_config, messages, self.model, stream=False,
            )
            response = httpx.post(url, json=payload, headers=headers, timeout=timeout, verify=verify)
        response.raise_for_status()
        data = response.json()
        return ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "")

    def run(self) -> None:
        tools = FolderChatTools(self.root, dirty_paths=self.dirty_paths)
        messages = [
            {"role": "system", "content": self.system_prompt},
        ]
        messages.extend(
            {"role": role, "content": content[:15_000]}
            for role, content in self.history[-12:]
            if role in {"user", "assistant"} and content.strip()
        )
        messages.append({
            "role": "user",
            "content": f"Current file: {self.current_path}\n\nUser request:\n{self.user_prompt}",
        })
        if self.vision_images:
            messages = with_vision_images(messages, self.vision_images)
        wrote = False
        try:
            for _ in range(8):
                if self._cancel_requested:
                    self.failed.emit("Cancelled")
                    return
                reply = self._send_llm(messages)
                parsed = parse_agent_message(reply)
                if not parsed:
                    self.failed.emit("The model returned an invalid tool response")
                    return
                if parsed["type"] == "final":
                    self.finalMessage.emit(parsed["content"])
                    return
                for call in parsed["calls"]:
                    if self._cancel_requested:
                        self.failed.emit("Cancelled")
                        return
                    name = call["name"]
                    args = call["args"]
                    self.toolMessage.emit(f"Agent activity: [Agent: {name} {args.get('path') or args.get('query') or ''}…]")
                    output = tools.call(name, args)
                    success = "error" not in output
                    wrote |= success and name in {"folder.create", "folder.write"}
                    if success and name in {"folder.create", "folder.write"}:
                        self.fileChanged.emit(str(self.root / output["path"]))
                    result = {"type": "tool_result", "id": call.get("id") or str(uuid.uuid4()),
                              "name": name, "status": "ok" if success else "error"}
                    result["output" if success else "error"] = output
                    messages.append({"role": "assistant", "content": json.dumps(result)})
                    self.toolMessage.emit(f"Tool result: {name} status={result['status']}")
                messages.append({"role": "user", "content": "Continue using these tool results. Return a final answer when done."})
            if wrote:
                self.finalMessage.emit("The requested file change was made.")
            else:
                self.failed.emit("Folder tool loop exceeded its step limit")
        except Exception as exc:
            self.failed.emit(str(exc))
