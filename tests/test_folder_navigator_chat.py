"""Folder chat uses only the selected external root and explicit images."""

from __future__ import annotations

import base64
import pytest

from sp.app.folder_navigator.catalog import FolderCatalog
from sp.app.folder_navigator import chat as folder_chat
from sp.app.folder_navigator.promotion import promote_folder_chat
from sp.app.ui.ai_chat_panel import AIChatStore
from sp.ai.manager import AIManager
import sqlite3


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/"
    "lL8AAAAASUVORK5CYII="
)


def test_folder_chat_context_and_history(tmp_path, monkeypatch, qapp):
    root = tmp_path / "external"
    sub = root / "notes"
    sub.mkdir(parents=True)
    note = sub / "a.txt"
    note.write_text("Current text", encoding="utf-8")
    image = sub / "picture.png"
    image.write_bytes(PNG)
    catalog = FolderCatalog(root)
    catalog.upsert_paths([note, image])
    db_path = tmp_path / "chat.db"
    monkeypatch.setattr(folder_chat, "folder_chat_database", lambda _root: db_path)

    def open_panel():
        return folder_chat.FolderChatPanel(
            root,
            lambda query, scope: catalog.candidates(query, scope),
            lambda query, scope: catalog.directory_candidates(query, scope),
            lambda _path: None,
        )

    panel = open_panel()
    assert [candidate.display_label for candidate in panel._folder_candidates("!", "picture")] == [
        "Image: /notes/picture.png"
    ]
    assert all(candidate.attachment_name is None for candidate in panel._folder_candidates("@", ""))
    assert panel.add_path_to_context(sub)
    prompt, images, _fallback = panel._build_context_payload("Summarize")
    assert "Current text" in prompt
    assert "Folder listing is bounded" in prompt
    assert images == []
    assert "picture.png" not in prompt

    panel.set_current_page("/notes/picture.png")
    assert panel.context_refresh_btn.text() == "+ Image"
    panel._refresh_current_page_context()
    prompt, images, fallback = panel._build_context_payload("Describe")
    assert "picture.png" in prompt
    assert len(images) == 1 and images[0].startswith("data:image/png;base64,")
    assert fallback is not None
    assert panel._selected_files_for_chat(panel.current_session_id) == [note, image]
    panel.store.save_message(panel.current_session_id, "user", "Remember this")
    panel.close()

    restored = open_panel()
    sessions = [session for session in restored.store.get_sessions() if session["type"] == "chat"]
    assert len(sessions) == 1
    assert ("user", "Remember this") in restored.store.get_messages(sessions[0]["id"])
    assert len(restored._context_items) == 2
    restored.close()


def test_folder_chat_rejects_paths_outside_root(tmp_path, monkeypatch, qapp):
    root = tmp_path / "root"
    root.mkdir()
    other = tmp_path / "other.txt"
    other.write_text("private", encoding="utf-8")
    (root / "link.txt").symlink_to(other)
    monkeypatch.setattr(folder_chat, "folder_chat_database", lambda _root: tmp_path / "chat.db")
    panel = folder_chat.FolderChatPanel(root, lambda _q, _s: [], lambda _q, _s: [], lambda _p: None)
    assert panel._relative_ref(root / "link.txt") is None
    assert not panel.add_path_to_context(root / "link.txt")
    panel.close()


def test_folder_chat_rejects_oversized_vision_selection(tmp_path, monkeypatch, qapp):
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setattr(folder_chat, "folder_chat_database", lambda _root: tmp_path / "chat.db")
    panel = folder_chat.FolderChatPanel(root, lambda _q, _s: [], lambda _q, _s: [], lambda _p: None)
    for index in range(5):
        image = root / f"image-{index}.png"
        image.write_bytes(PNG)
        assert panel.add_path_to_context(image)
    with pytest.raises(ValueError, match="at most 4 images"):
        panel._build_context_payload("Describe")

    panel._context_items = panel._context_items[:2]
    monkeypatch.setattr(panel, "_read_context_image", lambda _page, _name: "x" * 11)
    monkeypatch.setattr("sp.app.ui.ai_chat_panel.MAX_VISION_PAYLOAD_CHARS", 20)
    with pytest.raises(ValueError, match="20 MiB request limit"):
        panel._build_context_payload("Describe")
    panel.close()


def test_folder_context_includes_utf16_text_but_skips_binary(tmp_path, monkeypatch, qapp):
    root = tmp_path / "root"
    root.mkdir()
    text_file = root / "unicode.txt"
    text_file.write_text("Café notes", encoding="utf-16")
    binary_file = root / "binary.dat"
    binary_file.write_bytes(b"\x00\x01\x02")
    catalog = FolderCatalog(root)
    catalog.upsert_paths([text_file, binary_file])
    monkeypatch.setattr(folder_chat, "folder_chat_database", lambda _root: tmp_path / "chat.db")
    panel = folder_chat.FolderChatPanel(
        root, lambda q, s: catalog.candidates(q, s),
        lambda q, s: catalog.directory_candidates(q, s), lambda _p: None,
    )
    assert panel._list_context_pages("/") == ["/unicode.txt"]
    assert panel._read_context_page("/unicode.txt") == "Café notes"
    panel.close()


def test_folder_context_waits_for_complete_catalog(tmp_path, monkeypatch, qapp):
    root = tmp_path / "root"
    root.mkdir()
    note = root / "note.txt"
    note.write_text("Indexed note", encoding="utf-8")
    catalog = FolderCatalog(root)
    catalog.upsert_paths([note])
    monkeypatch.setattr(folder_chat, "folder_chat_database", lambda _root: tmp_path / "chat.db")
    state = ["partial"]
    panel = folder_chat.FolderChatPanel(
        root, lambda q, s: catalog.candidates(q, s),
        lambda q, s: catalog.directory_candidates(q, s), lambda _p: None,
        index_state=lambda: state[0],
    )
    with pytest.raises(RuntimeError, match="index is incomplete"):
        panel._list_context_pages("/")
    state[0] = "complete"
    assert panel._list_context_pages("/") == ["/note.txt"]
    panel.close()


def test_image_catalog_filter_finds_images_after_many_text_files(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    files = []
    for index in range(1205):
        path = root / f"a{index:04d}.txt"
        path.write_text("text", encoding="utf-8")
        files.append(path)
    image = root / "z-picture.png"
    image.write_bytes(PNG)
    catalog = FolderCatalog(root)
    catalog.upsert_paths(files + [image])
    assert catalog.candidates("", root, suffixes={".png"}) == [image]
    assert image not in catalog.candidates("", root, exclude_suffixes={".png"})


def test_tif_image_uses_ocr_fallback(tmp_path, monkeypatch):
    from sp.rag import attachment_text

    image = tmp_path / "scan.tif"
    image.write_bytes(b"TIFF image bytes")
    monkeypatch.setattr(attachment_text, "_extract_text_from_image", lambda _path: "scanned words")
    assert attachment_text.extract_attachment_text(image) == "scanned words"


def test_promotion_copies_transcript_and_selected_file_snapshots(tmp_path, qapp):
    root = tmp_path / "external"
    root.mkdir()
    source_file = root / "draft.txt"
    source_file.write_text("Saved version", encoding="utf-8")
    image = root / "picture.png"
    image.write_bytes(PNG)
    utf16_file = root / "unicode.txt"
    utf16_file.write_text("Café source", encoding="utf-16")
    source_store = AIChatStore(db_path=tmp_path / "folder-chat.db")
    chat = source_store.create_named_chat("/", "Research")
    source_store.save_message(chat["id"], "user", "Explain these files")
    source_store.save_message(chat["id"], "assistant", "Here is a summary")

    vault = tmp_path / "vault"
    metadata = vault / ".stillpoint"
    metadata.mkdir(parents=True)
    sqlite3.connect(metadata / "settings.db").close()
    result = promote_folder_chat(
        source_root=root,
        source_store=source_store,
        source_session_id=chat["id"],
        selected_files=[source_file, image, utf16_file],
        destination_vault=vault,
        text_overrides={source_file: "Unsaved editor version"},
    )

    vault_store = AIChatStore(vault_root=str(vault))
    copied = vault_store.get_session_by_id(result.chat_id)
    assert copied["name"] == "Research"
    assert vault_store.get_messages(result.chat_id) == [
        ("user", "Explain these files"), ("assistant", "Here is a summary"),
    ]
    assert result.copied_files == 3
    import_dir = (vault / result.context_page.lstrip("/")).parent
    assert (import_dir / "001-draft.txt").read_text(encoding="utf-8") == "Unsaved editor version"
    assert (import_dir / "002-picture.png").read_bytes() == PNG
    assert (import_dir / "003-unicode.txt").read_text(encoding="utf-16") == "Café source"
    with sqlite3.connect(metadata / "settings.db") as connection:
        items = AIManager(conn=connection).list_context_items(copied["ai_conversation_id"])
    assert [(item.kind, item.attachment_name) for item in items] == [
        ("page", None), ("attachment", "001-draft.txt"),
        ("attachment", "002-picture.png"),
        ("attachment", "003-unicode.txt"),
    ]

    from sp.app import config
    from sp.app.ui.ai_chat_panel import AIChatPanel

    token = config.push_active_vault_context(str(vault))
    try:
        panel = AIChatPanel(api_client=None, store=vault_store)
        panel.set_vault_root(str(vault))
        panel._load_chat_messages(result.chat_id)
        prompt, images, _fallback = panel._build_context_payload("Continue")
        assert "Unsaved editor version" in prompt
        assert "Café source" in prompt
        assert len(images) == 1 and images[0].startswith("data:image/png;base64,")
        panel.close()
    finally:
        config.reset_active_vault_context(token)


def test_folder_navigator_shows_chat_when_enabled(tmp_path, monkeypatch, qapp):
    from sp.app import config
    from sp.app.folder_navigator.window import Window

    root = tmp_path / "folder"
    root.mkdir()
    note = root / "note.txt"
    note.write_text("Folder content", encoding="utf-8")
    monkeypatch.setattr(config, "load_enable_folder_navigator_chat", lambda: True)
    monkeypatch.setattr(folder_chat, "folder_chat_database", lambda _root: tmp_path / "chat.db")
    window = Window(root)
    window.settings_path = tmp_path / "navigator-settings.json"
    assert window.chat_panel is not None
    window._chat_add_path(note)
    assert "Folder content" in window.chat_panel._build_context_prompt("Summarize")
    window.close()


def test_chat_storage_failure_does_not_block_folder_navigator(tmp_path, monkeypatch, qapp):
    from sp.app import config
    from sp.app.folder_navigator.window import Window

    root = tmp_path / "folder"
    root.mkdir()
    blocker = tmp_path / "blocked"
    blocker.write_text("file", encoding="utf-8")
    monkeypatch.setattr(config, "load_enable_folder_navigator_chat", lambda: True)
    monkeypatch.setattr(folder_chat, "folder_chat_database", lambda _root: blocker / "chat.db")
    window = Window(root)
    window.settings_path = tmp_path / "navigator-settings.json"
    assert window.chat_panel is None
    assert window.chat_error
    assert "AI chat could not open" in window.statusBar().currentMessage()
    window.close()
