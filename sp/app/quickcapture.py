from __future__ import annotations

import argparse
import re
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx
from PySide6.QtWidgets import QApplication

from sp.app import config
from sp.app.quickcapture_common import (
    QUICK_CAPTURE_ATTACHMENT_PLACEHOLDER_RE,
    append_quick_capture_section,
    format_attachment_link,
    resolve_attachment_placeholders,
)
from sp.app.ui.quick_capture_overlay import QuickCaptureOverlay
from sp.server import search_index
from sp.server.adapters import files
from sp.server.adapters.files import FileAccessError, PAGE_SUFFIX


def _default_api_base() -> str:
    base = os.getenv("ZIMX_API_BASE")
    if base:
        return base.rstrip("/")
    base_path = Path.home() / ".stillpoint" / "api-base"
    if base_path.exists():
        try:
            stored = base_path.read_text(encoding="utf-8").strip()
            if stored:
                return stored.rstrip("/")
        except Exception:
            pass
    host = os.getenv("SP_HOST", "127.0.0.1")
    port = os.getenv("ZIMX_PORT", "8765")
    return f"http://{host}:{port}"


def _load_local_ui_token() -> Optional[str]:
    env_token = os.getenv("ZIMX_LOCAL_UI_TOKEN")
    if env_token:
        return env_token.strip()
    token_path = Path.home() / ".stillpoint" / "local-ui-token"
    if token_path.exists():
        try:
            return token_path.read_text(encoding="utf-8").strip()
        except Exception:
            return None
    return None


def _parse_hotkey_text(text_arg: Optional[str]) -> Optional[str]:
    if text_arg is not None and text_arg.strip():
        return text_arg.strip()
    if sys.stdin and not sys.stdin.isatty():
        data = sys.stdin.read()
        if data and data.strip():
            return data.strip()
    return None


def _prompt_overlay() -> tuple[Optional[str], list[dict]]:
    app = QApplication.instance() or QApplication([])
    result: dict[str, object] = {"text": None, "attachments": []}

    def _on_capture(text: str, attachments: list[dict], _vault_path: Optional[str]) -> None:
        result["text"] = text
        result["attachments"] = attachments
        app.quit()

    overlay = QuickCaptureOverlay(parent=None, on_capture=_on_capture)
    overlay.finished.connect(app.quit)
    overlay.show()
    overlay.raise_()
    overlay.activateWindow()
    overlay.input.setFocus()
    app.exec()
    return result["text"], result.get("attachments") or []


def _colon_to_page_path(colon_path: str) -> str:
    cleaned = (colon_path or "").strip()
    if cleaned.startswith(":"):
        cleaned = cleaned.lstrip(":")
    if "#" in cleaned:
        cleaned = cleaned.split("#", 1)[0]
    cleaned = cleaned.strip()
    if not cleaned:
        raise ValueError("Custom capture page is required.")
    parts = [part.strip() for part in cleaned.split(":") if part.strip()]
    if not parts:
        raise ValueError("Custom capture page is required.")
    parts = [part.replace("_", " ") for part in parts]
    folder_path = "/".join(parts)
    file_name = f"{parts[-1]}{PAGE_SUFFIX}"
    return f"/{folder_path}/{file_name}"


def _resolve_custom_page_ref(page_ref: str) -> str:
    raw = (page_ref or "").strip()
    if not raw:
        raise ValueError("Custom capture page is required.")
    if raw.startswith("/"):
        return raw
    if "/" in raw:
        return f"/{raw}"
    return _colon_to_page_path(raw)


def _format_image_link(name: str, width: Optional[int]) -> str:
    return format_attachment_link(name, width, is_image=True)


def _resolve_attachment_placeholders(text: str, images: Optional[list[dict]] = None) -> tuple[str, list[str]]:
    return resolve_attachment_placeholders(text, images)


def _build_quick_capture_entry(text: str, timestamp: str, images: Optional[list[dict]] = None) -> list[str]:
    text, trailing_image_lines = _resolve_attachment_placeholders(text, images)
    lines = [line.rstrip() for line in text.splitlines()]
    if not lines:
        return []
    first = f"- *{timestamp}*"
    note_lines = [f"  {line}" for line in lines]
    return [first] + note_lines + trailing_image_lines + ["", "---"]


def _persist_attachments(vault_root: Path, page_path: str, attachments: list[dict]) -> list[dict]:
    if not attachments:
        return []
    rel_file_path = page_path.lstrip("/")
    folder = (vault_root / rel_file_path).resolve().parent
    folder.mkdir(parents=True, exist_ok=True)
    existing = {p.name for p in folder.iterdir() if p.is_file()}
    saved: list[dict] = []

    def sanitize_name(name: str) -> str:
        cleaned = (name or "").strip().replace("\\", "_").replace("/", "_")
        cleaned = re.sub(r"\s+", "_", cleaned)
        cleaned = re.sub(r"[^A-Za-z0-9._()-]", "_", cleaned)
        if cleaned in ("", ".", ".."):
            cleaned = "attachment"
        return cleaned

    def unique_name(base: str) -> str:
        base = sanitize_name(base)
        if base not in existing:
            existing.add(base)
            return base
        stem = Path(base).stem
        suffix = Path(base).suffix
        idx = 1
        while True:
            candidate = f"{stem}_{idx}{suffix}"
            if candidate not in existing:
                existing.add(candidate)
                return candidate
            idx += 1

    def next_paste_name() -> str:
        idx = 1
        while True:
            candidate = f"paste_image_{idx:03d}.png"
            if candidate not in existing:
                existing.add(candidate)
                return candidate
            idx += 1

    for entry in attachments:
        if entry.get("kind") == "file":
            path = entry.get("path")
            if not isinstance(path, Path):
                continue
            if not path.exists():
                continue
            target_name = unique_name(path.name)
            target_path = folder / target_name
            target_path.write_bytes(path.read_bytes())
            saved.append(
                {
                    "name": target_name,
                    "width": entry.get("width"),
                    "placeholder": entry.get("placeholder"),
                    "is_image": bool(entry.get("is_image", entry.get("width") is not None)),
                    "stored_path": str(target_path),
                }
            )
            continue
        image = entry.get("image")
        if image is None:
            continue
        target_name = next_paste_name()
        target_path = folder / target_name
        if image.save(str(target_path), "PNG"):
            saved.append(
                {
                    "name": target_name,
                    "width": entry.get("width"),
                    "placeholder": entry.get("placeholder"),
                    "is_image": True,
                    "stored_path": str(target_path),
                }
            )
    return saved


def _append_quick_capture_section(content: str, entry_lines: list[str]) -> str:
    return append_quick_capture_section(content, entry_lines)


def _capture_to_files(
    vault_root: Path,
    page_mode: str,
    page_ref: Optional[str],
    text: str,
    attachments: Optional[list[dict]] = None,
) -> str:
    return str(
        _capture_to_files_result(vault_root, page_mode, page_ref, text, attachments)["path"]
    )


_LOCAL_CAPTURE_UNDO: dict[str, dict] = {}


def _capture_to_files_result(
    vault_root: Path,
    page_mode: str,
    page_ref: Optional[str],
    text: str,
    attachments: Optional[list[dict]] = None,
) -> dict:
    config.init_settings()
    config.set_active_vault(str(vault_root))
    if page_mode == "today":
        target, _created = files.ensure_journal_today(vault_root, template=None)
        rel_path = f"/{target.relative_to(vault_root).as_posix()}"
    else:
        rel_path = _resolve_custom_page_ref(page_ref or "")
        # Ensure custom capture folder exists so read_file can scaffold page content.
        try:
            (vault_root / rel_path.lstrip("/")).resolve().parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
    content = files.read_file(vault_root, rel_path)
    now = datetime.now()
    if rel_path.startswith("/Journal/"):
        timestamp = now.strftime("%I:%M %p").lower()
    else:
        timestamp = f"{now:%Y-%m-%d}: {now.strftime('%I:%M%p').lower()}"
    saved_images = _persist_attachments(vault_root, rel_path, attachments or [])
    entry_lines = _build_quick_capture_entry(text, timestamp, saved_images)
    updated = _append_quick_capture_section(content, entry_lines)
    files.write_file(vault_root, rel_path, updated)
    db_path = vault_root / ".stillpoint" / "settings.db"
    try:
        import sqlite3

        conn = sqlite3.connect(db_path, check_same_thread=False)
        search_index.upsert_page(conn, rel_path, int(datetime.now().timestamp()), updated)
        conn.close()
    except Exception:
        pass
    capture_id = uuid.uuid4().hex
    _LOCAL_CAPTURE_UNDO[capture_id] = {
        "vault_root": str(vault_root),
        "path": rel_path,
        "before": content,
        "after": updated,
        "attachments": [item.get("stored_path") for item in saved_images if item.get("stored_path")],
    }
    if len(_LOCAL_CAPTURE_UNDO) > 50:
        _LOCAL_CAPTURE_UNDO.pop(next(iter(_LOCAL_CAPTURE_UNDO)))
    return {"ok": True, "id": capture_id, "path": rel_path}


def _undo_file_capture(capture_id: str) -> dict:
    receipt = _LOCAL_CAPTURE_UNDO.get(str(capture_id or ""))
    if not receipt:
        return {"ok": False, "error": "That capture can no longer be undone."}
    vault_root = Path(receipt["vault_root"])
    rel_path = str(receipt["path"])
    current = files.read_file(vault_root, rel_path)
    if current != receipt["after"]:
        return {"ok": False, "error": "The destination page changed after capture; undo was not applied."}
    files.write_file(vault_root, rel_path, receipt["before"])
    for raw_path in receipt.get("attachments") or []:
        path = Path(str(raw_path))
        try:
            if path.is_file():
                path.unlink()
        except OSError:
            pass
    _LOCAL_CAPTURE_UNDO.pop(capture_id, None)
    config.remove_quick_capture_history(capture_id)
    try:
        db_path = vault_root / ".stillpoint" / "settings.db"
        import sqlite3

        conn = sqlite3.connect(db_path, check_same_thread=False)
        search_index.upsert_page(conn, rel_path, int(datetime.now().timestamp()), receipt["before"])
        conn.close()
    except Exception:
        pass
    return {"ok": True, "path": rel_path}


def _capture_via_api(base: str, token: Optional[str], payload: dict) -> bool:
    headers = {"X-Local-UI-Token": token} if token else None
    try:
        with httpx.Client(base_url=base, timeout=2.0, headers=headers) as client:
            resp = client.get("/api/health")
            if resp.status_code != 200:
                return False
            post = client.post("/api/quick-capture", json=payload)
            if post.status_code == 200:
                return True
            if post.status_code in (401, 403):
                return False
    except httpx.HTTPError:
        return False
    return False


def _show_overlay_via_api(base: str, token: Optional[str]) -> bool:
    headers = {"X-Local-UI-Token": token} if token else None
    try:
        with httpx.Client(base_url=base, timeout=1.5, headers=headers) as client:
            resp = client.post("/api/ui/quick-capture")
            if resp.status_code == 200:
                return True
    except httpx.HTTPError:
        return False
    return False


def _resolve_homebase_ref_to_path(value: str) -> Optional[str]:
    ref = str(value or "").strip()
    if not ref.startswith("homebase::"):
        return None
    for profile in config.load_homebase_vault_profiles():
        profile_id = str(profile.get("id") or "").strip()
        if profile_id != ref:
            continue
        local_path = str(profile.get("path") or "").strip()
        return local_path or None
    # Backward-compatible fallback for refs shaped as:
    # homebase::<server_url>::<vault_id>::<local_path>
    parts = ref.split("::", 3)
    if len(parts) == 4:
        local_path = parts[3].strip()
        return local_path or None
    return None


def _resolve_vault_path(vault_arg: Optional[str]) -> Path:
    if vault_arg:
        if vault_arg.startswith("remote::"):
            raise ValueError("Remote vault refs are not supported for local file capture.")
        if vault_arg.startswith("homebase::"):
            local_path = _resolve_homebase_ref_to_path(vault_arg)
            if not local_path:
                raise ValueError("Homebase vault ref could not be resolved to a local path.")
            return Path(local_path).expanduser().resolve()
        return Path(vault_arg).expanduser().resolve()
    configured = config.load_quick_capture_vault()
    if configured:
        if configured.startswith("remote::"):
            configured = None
        elif configured.startswith("homebase::"):
            local_path = _resolve_homebase_ref_to_path(configured)
            if local_path:
                return Path(local_path).expanduser().resolve()
            configured = None
        else:
            return Path(configured).expanduser().resolve()
    last = config.load_last_vault()
    if last and isinstance(last, str) and not last.startswith("remote::"):
        return Path(last).expanduser().resolve()
    raise ValueError("No vault configured for Quick Capture.")


def _resolve_page_mode(page_arg: Optional[str]) -> tuple[str, Optional[str]]:
    if page_arg:
        return "custom", page_arg
    mode = config.load_quick_capture_page_mode()
    if mode == "custom":
        return "custom", config.load_quick_capture_custom_page()
    return "today", None


def run_quick_capture(
    *,
    vault: Optional[str],
    page: Optional[str],
    text: Optional[str],
    allow_overlay: bool = True,
) -> int:
    config.init_settings()
    capture_text = _parse_hotkey_text(text)
    attachments: list[dict] = []
    if not capture_text and allow_overlay:
        api_base = _default_api_base()
        token = _load_local_ui_token()
        if _show_overlay_via_api(api_base, token):
            return 0
        capture_text, attachments = _prompt_overlay()
    if not capture_text:
        return 0

    try:
        vault_root = _resolve_vault_path(vault)
    except Exception as exc:
        print(f"Quick Capture error: {exc}")
        return 1
    if not vault_root.exists():
        print("Quick Capture error: vault does not exist.")
        return 1

    page_mode, page_ref = _resolve_page_mode(page)
    if page_mode == "custom" and not page_ref:
        print("Quick Capture error: custom page not configured.")
        return 1

    api_base = _default_api_base()
    token = _load_local_ui_token()
    payload = {
        "vault_path": str(vault_root),
        "page_mode": page_mode,
        "page_ref": page_ref,
        "text": capture_text,
    }
    if not attachments and _capture_via_api(api_base, token, payload):
        return 0
    try:
        _capture_to_files(vault_root, page_mode, page_ref, capture_text, attachments)
    except FileAccessError as exc:
        print(f"Quick Capture error: {exc}")
        return 1
    except Exception as exc:
        print(f"Quick Capture error: {exc}")
        return 1
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="StillPoint Quick Capture")
    parser.add_argument("--vault", help="Vault path for capture")
    parser.add_argument("--page", help="Custom page (colon path or /path)")
    parser.add_argument("--text", help="Capture text (omit to read from stdin)")
    args = parser.parse_args(argv)
    return run_quick_capture(vault=args.vault, page=args.page, text=args.text, allow_overlay=True)


if __name__ == "__main__":
    raise SystemExit(main())
