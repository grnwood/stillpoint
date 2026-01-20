from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx
from PySide6.QtWidgets import QApplication

from sp.app import config
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


def _prompt_overlay() -> Optional[str]:
    app = QApplication.instance() or QApplication([])
    result: dict[str, Optional[str]] = {"text": None}

    def _on_capture(text: str) -> None:
        result["text"] = text
        app.quit()

    overlay = QuickCaptureOverlay(parent=None, on_capture=_on_capture)
    overlay.finished.connect(app.quit)
    overlay.show()
    overlay.raise_()
    overlay.activateWindow()
    overlay.input.setFocus()
    app.exec()
    return result["text"]


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


def _build_quick_capture_entry(text: str, timestamp: str) -> list[str]:
    lines = [line.rstrip() for line in text.splitlines()]
    if not lines:
        return []
    first = f"- *{timestamp}* - {lines[0].strip()}"
    rest = [f"  {line}" for line in lines[1:]]
    return [first] + rest + ["", "---"]


def _append_quick_capture_section(content: str, entry_lines: list[str]) -> str:
    if not entry_lines:
        return content
    section_title = "## Inbox / Captures"
    lines = content.splitlines()
    header_idx = next((i for i, line in enumerate(lines) if line.strip() == section_title), -1)
    if header_idx == -1:
        trimmed = content.rstrip("\n")
        spacer = "\n\n" if trimmed else ""
        return f"{trimmed}{spacer}{section_title}\n" + "\n".join(entry_lines) + "\n"
    insert_at = len(lines)
    for i in range(header_idx + 1, len(lines)):
        if lines[i].startswith("#"):
            insert_at = i
            break
    new_lines = lines[:insert_at] + entry_lines + lines[insert_at:]
    result = "\n".join(new_lines)
    if not result.endswith("\n"):
        result += "\n"
    return result


def _capture_to_files(vault_root: Path, page_mode: str, page_ref: Optional[str], text: str) -> str:
    config.init_settings()
    config.set_active_vault(str(vault_root))
    if page_mode == "today":
        target, _created = files.ensure_journal_today(vault_root, template=None)
        rel_path = f"/{target.relative_to(vault_root).as_posix()}"
    else:
        rel_path = _resolve_custom_page_ref(page_ref or "")
    content = files.read_file(vault_root, rel_path)
    now = datetime.now()
    if rel_path.startswith("/Journal/"):
        timestamp = now.strftime("%I:%M %p").lower()
    else:
        timestamp = f"{now:%Y-%m-%d}: {now.strftime('%I:%M%p').lower()}"
    entry_lines = _build_quick_capture_entry(text, timestamp)
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
    return rel_path


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


def _resolve_vault_path(vault_arg: Optional[str]) -> Path:
    if vault_arg:
        return Path(vault_arg).expanduser().resolve()
    configured = config.load_quick_capture_vault()
    if configured:
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
    if not capture_text and allow_overlay:
        api_base = _default_api_base()
        token = _load_local_ui_token()
        if _show_overlay_via_api(api_base, token):
            return 0
        capture_text = _prompt_overlay()
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
    if _capture_via_api(api_base, token, payload):
        return 0
    try:
        _capture_to_files(vault_root, page_mode, page_ref, capture_text)
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
