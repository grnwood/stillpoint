"""Copy a Folder Navigator chat and its selected files into a local vault."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import shutil
import sqlite3
import uuid

from sp.ai.manager import AIManager
from sp.app.ui.ai_chat_panel import AIChatStore
from sp.vault_boundary import validate_vault_root


MAX_PROMOTED_FILE_BYTES = 20 * 1024 * 1024
MAX_PROMOTED_TOTAL_BYTES = 100 * 1024 * 1024


@dataclass(frozen=True)
class PromotionResult:
    vault: Path
    chat_id: int
    copied_files: int
    context_page: str | None


def _safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "-", text).strip("-")[:48] or "Folder-Chat"


def promote_folder_chat(
    *,
    source_root: Path,
    source_store: AIChatStore,
    source_session_id: int,
    selected_files: list[Path],
    destination_vault: Path,
    text_overrides: dict[Path, str] | None = None,
) -> PromotionResult:
    """Create a vault chat with a snapshot of the transcript and selected files."""
    source_root = source_root.resolve(strict=True)
    vault = validate_vault_root(destination_vault.resolve(strict=True))
    if not (vault / ".stillpoint" / "settings.db").is_file():
        raise ValueError("The selected folder is not an initialized StillPoint vault")
    session = source_store.get_session_by_id(source_session_id)
    if not session or session.get("type") != "chat":
        raise ValueError("The selected folder chat is unavailable")

    files: list[tuple[Path, Path]] = []
    seen: set[Path] = set()
    total_bytes = 0
    text_overrides = {path.resolve(): text for path, text in (text_overrides or {}).items()}
    for candidate in selected_files:
        path = candidate.resolve(strict=True)
        if not path.is_relative_to(source_root) or not path.is_file():
            raise ValueError(f"Selected file is outside the folder: {candidate}")
        if path in seen:
            continue
        seen.add(path)
        size = (len(text_overrides[path].encode("utf-8"))
                if path in text_overrides else path.stat().st_size)
        if size > MAX_PROMOTED_FILE_BYTES:
            raise ValueError(f"Selected file exceeds 20 MiB: {path.name}")
        total_bytes += size
        if total_bytes > MAX_PROMOTED_TOTAL_BYTES:
            raise ValueError("Selected files exceed the 100 MiB promotion limit")
        files.append((path, path.relative_to(source_root)))

    title = str(session.get("name") or "Folder Chat")
    transcript = source_store.get_messages(source_session_id)
    import_dir: Path | None = None
    context_page: str | None = None
    copied_names: list[str] = []
    if files:
        slug = _safe_name(title)
        import_root = vault / "Folder Chat Imports"
        if import_root.is_symlink():
            raise ValueError("Vault import folder cannot be a symbolic link")
        import_root.mkdir(exist_ok=True)
        if not import_root.resolve().is_relative_to(vault):
            raise ValueError("Vault import folder is outside the selected vault")
        import_dir = import_root / f"{slug}-{uuid.uuid4().hex[:8]}"
        import_dir.mkdir(parents=True, exist_ok=False)
        try:
            manifest = [
                f"# {title}", "",
                f"Imported from Folder Navigator root: `{source_root}`", "",
                "These are snapshots of the files selected when this chat was promoted.",
                "", "## Files", "",
            ]
            for index, (path, relative) in enumerate(files, 1):
                copied_name = f"{index:03d}-{path.name}"
                if len(copied_name) > 180:
                    copied_name = f"{index:03d}-{path.stem[:140]}{path.suffix}"
                if path in text_overrides:
                    (import_dir / copied_name).write_text(text_overrides[path], encoding="utf-8")
                else:
                    shutil.copy2(path, import_dir / copied_name)
                copied_names.append(copied_name)
                manifest.append(f"- `{relative.as_posix()}` → `{copied_name}`")
            anchor = import_dir / f"{import_dir.name}.md"
            anchor.write_text("\n".join(manifest) + "\n", encoding="utf-8")
            context_page = "/" + anchor.relative_to(vault).as_posix()
        except Exception:
            shutil.rmtree(import_dir)
            raise

    target_store = None
    target_id = None
    connection = None
    manager = None
    conversation_id = None
    try:
        target_store = AIChatStore(vault_root=str(vault))
        promoted = target_store.create_named_chat("/Folder Navigator", title)
        target_id = int(promoted["id"])
        for role, content in transcript:
            target_store.save_message(target_id, role, content)
        model = session.get("last_model")
        server = session.get("last_server")
        prompt = session.get("system_prompt")
        if model:
            target_store.update_session_last_model(target_id, str(model))
        if server:
            target_store.update_session_last_server(target_id, str(server))
        if prompt:
            target_store.update_session_system_prompt(target_id, str(prompt))
        if context_page:
            connection = sqlite3.connect(vault / ".stillpoint" / "settings.db")
            manager = AIManager(conn=connection)
            conversation = manager.create_global_chat(title)
            conversation_id = conversation.id
            manager.add_context_page(conversation.id, context_page)
            for copied_name in copied_names:
                manager.add_context_attachment(conversation.id, context_page, copied_name)
            target_store.update_session_ai_conversation(target_id, conversation.id)
    except Exception:
        if manager is not None and conversation_id is not None:
            manager.delete_conversation(conversation_id)
        if target_store is not None and target_id is not None:
            target_store.delete_session(target_id)
        if import_dir is not None:
            shutil.rmtree(import_dir)
        raise
    finally:
        if connection is not None:
            connection.close()
    return PromotionResult(vault, target_id, len(copied_names), context_page)
