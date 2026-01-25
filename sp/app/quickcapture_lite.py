from __future__ import annotations

import argparse
from pathlib import Path
import re
from typing import Optional

from sp.app import config
from sp.app.ui.quick_capture_overlay import QuickCaptureOverlay
from sp.server.adapters import files
from sp.server.adapters.files import FileAccessError, PAGE_SUFFIX


def _parse_hotkey_text(text_arg: Optional[str]) -> Optional[str]:
    if text_arg is not None and text_arg.strip():
        return text_arg.strip()
    return None


def _prompt_overlay(
    *,
    vault_options: Optional[list[dict[str, str]]] = None,
    selected_vault: Optional[str] = None,
    show_vault_picker: bool = False,
) -> tuple[Optional[str], list[dict], Optional[str]]:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    result: dict[str, object] = {"text": None, "attachments": [], "vault_path": selected_vault}

    def _on_capture(text: str, attachments: list[dict], vault_path: Optional[str]) -> None:
        result["text"] = text
        result["attachments"] = attachments
        if vault_path:
            result["vault_path"] = vault_path
        app.quit()

    overlay = QuickCaptureOverlay(
        parent=None,
        on_capture=_on_capture,
        vault_options=vault_options if show_vault_picker else None,
        selected_vault=selected_vault,
    )
    overlay.finished.connect(app.quit)
    overlay.show()
    overlay.raise_()
    overlay.activateWindow()
    overlay.input.setFocus()
    app.exec()
    return (
        result.get("text"),
        result.get("attachments") or [],
        result.get("vault_path"),
    )


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
    if width and width > 600:
        return f"  ![](./{name}){{width=600}}"
    return f"  ![](./{name})"


def _build_quick_capture_entry(text: str, timestamp: str, images: Optional[list[dict]] = None) -> list[str]:
    lines = [line.rstrip() for line in text.splitlines()]
    if not lines:
        return []
    first = f"- *{timestamp}* - {lines[0].strip()}"
    rest = [f"  {line}" for line in lines[1:]]
    image_lines = []
    for entry in images or []:
        name = entry.get("name")
        if not name:
            continue
        width = entry.get("width")
        image_lines.append(_format_image_link(name, width))
    return [first] + image_lines + rest + ["", "---"]


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


def _resolve_page_mode(page_arg: Optional[str]) -> tuple[str, Optional[str]]:
    if page_arg:
        return "custom", page_arg
    mode = config.load_quick_capture_page_mode()
    if mode == "custom":
        return "custom", config.load_quick_capture_custom_page()
    return "today", None


def _capture_to_files(
    vault_root: Path,
    page_mode: str,
    page_ref: Optional[str],
    text: str,
    attachments: Optional[list[dict]] = None,
) -> str:
    config.init_settings()
    config.set_active_vault(str(vault_root))
    if page_mode == "today":
        target, _created = files.ensure_journal_today(vault_root, template=None)
        rel_path = f"/{target.relative_to(vault_root).as_posix()}"
    else:
        rel_path = _resolve_custom_page_ref(page_ref or "")
    content = files.read_file(vault_root, rel_path)
    from datetime import datetime

    now = datetime.now()
    if rel_path.startswith("/Journal/"):
        timestamp = now.strftime("%I:%M %p").lower()
    else:
        timestamp = f"{now:%Y-%m-%d}: {now.strftime('%I:%M%p').lower()}"
    saved_images = _persist_attachments(vault_root, rel_path, attachments or [])
    entry_lines = _build_quick_capture_entry(text, timestamp, saved_images)
    updated = _append_quick_capture_section(content, entry_lines)
    files.write_file(vault_root, rel_path, updated)
    return rel_path


def _resolve_local_vault_path(vault_arg: Optional[str]) -> Optional[str]:
    if vault_arg:
        if vault_arg.startswith("remote::"):
            return None
        return vault_arg
    configured = config.load_quick_capture_vault()
    if configured:
        if configured.startswith("remote::"):
            return None
        return configured
    last = config.load_last_vault()
    if isinstance(last, str) and not last.startswith("remote::"):
        return last
    return None


def _local_vault_options() -> list[dict[str, str]]:
    options: list[dict[str, str]] = []
    configured = config.load_quick_capture_vault()
    if configured and not configured.startswith("remote::"):
        options.append({"name": Path(configured).name, "path": configured})
    last = config.load_last_vault()
    if isinstance(last, str) and last and not last.startswith("remote::"):
        if not any(opt.get("path") == last for opt in options):
            options.append({"name": Path(last).name, "path": last})
    for entry in config.load_known_vaults():
        path = entry.get("path")
        if not path or path.startswith("remote::"):
            continue
        if any(opt.get("path") == path for opt in options):
            continue
        options.append({"name": entry.get("name") or Path(path).name, "path": path})
    return options


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
            saved.append({"name": target_name, "width": entry.get("width")})
            continue
        image = entry.get("image")
        if image is None:
            continue
        target_name = next_paste_name()
        target_path = folder / target_name
        if image.save(str(target_path), "PNG"):
            saved.append({"name": target_name, "width": entry.get("width")})
    return saved


def run_quick_capture_lite(
    *,
    vault: Optional[str],
    page: Optional[str],
    text: Optional[str],
) -> int:
    config.init_settings()
    path = _resolve_local_vault_path(vault)
    if not path:
        print("Quick Capture error: no local vault configured.")
        return 1
    capture_text = _parse_hotkey_text(text)
    attachments: list[dict] = []
    selected_vault = path
    if not capture_text:
        options = _local_vault_options()
        capture_text, attachments, selected_vault = _prompt_overlay(
            vault_options=options,
            selected_vault=path,
            show_vault_picker=vault is None,
        )
    if not capture_text:
        return 0
    page_mode, page_ref = _resolve_page_mode(page)
    if page_mode == "custom" and not page_ref:
        print("Quick Capture error: custom page not configured.")
        return 1
    try:
        _capture_to_files(
            Path(str(selected_vault or path)).expanduser().resolve(),
            page_mode,
            page_ref,
            capture_text,
            attachments,
        )
    except FileAccessError as exc:
        print(f"Quick Capture error: {exc}")
        return 1
    except Exception as exc:
        print(f"Quick Capture error: {exc}")
        return 1
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="StillPoint Quick Capture (Lite)")
    parser.add_argument("--vault", help="Vault path or remote:: ref (optional)")
    parser.add_argument("--page", help="Custom page (colon path or /path)")
    parser.add_argument("--text", help="Capture text (omit to read from stdin)")
    args = parser.parse_args(argv)
    return run_quick_capture_lite(vault=args.vault, page=args.page, text=args.text)


if __name__ == "__main__":
    raise SystemExit(main())
