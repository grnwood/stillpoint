from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Dict, Iterable, List, Mapping, Optional, Set, Tuple
from urllib.parse import unquote

from sp.server.adapters.files import PAGE_SUFFIX, strip_page_suffix


IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp"}


@dataclass
class ImportPage:
    source: Path
    rel_stem: str  # e.g., "Folder/Note"
    dest_path: str  # vault-relative file path (with .md), leading slash
    content: str
    attachments: List[Path]


def normalize_folder_path(path: str) -> str:
    cleaned = (path or "").strip().replace("\\", "/")
    cleaned = cleaned.lstrip("/")
    cleaned = cleaned.rstrip("/")
    return f"/{cleaned}" if cleaned else "/"


def _dest_path(target_folder: str, rel_stem: str) -> str:
    """Create vault dest like /target/Folder/Note/Note.md from rel_stem Folder/Note."""
    base = normalize_folder_path(target_folder).lstrip("/")
    base_path = Path(base) if base else Path()
    rel = PurePosixPath(rel_stem)
    dest_dir = base_path.joinpath(rel).joinpath(rel.name)
    dest_file = dest_dir / f"{rel.name}{PAGE_SUFFIX}"
    return f"/{dest_file.as_posix()}"


def _path_to_colon(file_path: str) -> str:
    cleaned = file_path.strip().strip("/")
    if not cleaned:
        return ""
    parts = cleaned.split("/")
    if parts:
        parts[-1] = strip_page_suffix(parts[-1])
    if len(parts) >= 2 and parts[-1] == parts[-2]:
        parts = parts[:-1]
    parts = [p.replace(" ", "_") for p in parts]
    return ":".join(parts)


def _ensure_root_colon(link: str) -> str:
    text = (link or "").strip()
    if not text:
        return text
    if text.startswith(":") or text.startswith("#"):
        return text
    if "#" in text:
        base, anchor = text.split("#", 1)
        base = base.lstrip(":")
        return f":{base}#{anchor}"
    return f":{text.lstrip(':')}"


def _build_page_map(md_files: Iterable[Path], source_root: Path, target_folder: str) -> Dict[str, str]:
    page_map: Dict[str, str] = {}
    for file_path in md_files:
        rel = file_path.relative_to(source_root)
        rel_stem = rel.with_suffix("").as_posix()
        dest_path = _dest_path(target_folder, rel_stem)
        colon = _path_to_colon(dest_path)
        key = rel_stem.strip("/").lower()
        page_map[key] = colon
        stem_key = Path(rel_stem).name.lower()
        page_map.setdefault(stem_key, colon)
    return page_map


def _resolve_attachment_path(source_root: Path, page_rel: str, target: str) -> Optional[Path]:
    cleaned = unquote(target).strip()
    if not cleaned:
        return None
    # Absolute/URL references are not copied
    if re.match(r"^[a-zA-Z]+://", cleaned):
        return None
    page_dir = source_root / page_rel
    if page_dir.suffix:
        page_dir = page_dir.parent
    candidate = (page_dir / cleaned).resolve()
    try:
        candidate.relative_to(source_root.resolve())
    except Exception:
        return None
    if candidate.exists() and candidate.is_file():
        return candidate
    return None


def _rewrite_wiki_and_embeds(
    text: str, page_rel: str, page_map: Dict[str, str], attachments: Set[Path], source_root: Path
) -> str:
    def resolve_page(target: str) -> Optional[str]:
        anchor = ""
        base = target
        if "#" in target:
            base, anchor = target.split("#", 1)
        cleaned = base.strip().strip("/")
        key_candidates = [
            cleaned.lower(),
            Path(cleaned).name.lower(),
        ]
        for key in key_candidates:
            if key in page_map:
                colon = _ensure_root_colon(page_map[key])
                if anchor:
                    colon = f"{colon}#{anchor}"
                return colon
        return None

    def handle_embed(target: str) -> str:
        # Treat embeds as attachments if they look like files; otherwise fall back to page link
        target_clean = target.strip()
        if not target_clean:
            return target
        if re.search(r"\.[A-Za-z0-9]{1,8}$", target_clean):
            name = Path(target_clean).name
            resolved = _resolve_attachment_path(source_root, page_rel, target_clean)
            if resolved:
                attachments.add(resolved)
            return f"![]({name})"
        resolved_colon = resolve_page(target_clean)
        if resolved_colon:
            return f"[{resolved_colon}|{Path(target_clean).name}]"
        return f"[[{target}]]"

    def replacer(match: re.Match[str]) -> str:
        bang = match.group("bang")
        target = (match.group("target") or "").strip()
        label = (match.group("label") or "").strip()
        if not target:
            return match.group(0)
        if bang:
            return handle_embed(target)
        # Image/file link via wiki syntax
        if re.search(r"\.[A-Za-z0-9]{1,8}$", target):
            name = Path(target).name
            resolved = _resolve_attachment_path(source_root, page_rel, target)
            if resolved:
                attachments.add(resolved)
            return f"![]({name})"
        resolved_colon = resolve_page(target)
        display = label or target
        if resolved_colon:
            return f"[{resolved_colon}|{display}]"
        return display

    pattern = r"(?P<bang>!)?\[\[(?P<target>[^\]|]+)(?:\|(?P<label>[^\]]*))?\]\]"
    return re.sub(pattern, replacer, text)


def _rewrite_markdown_images(
    text: str, page_rel: str, attachments: Set[Path], source_root: Path
) -> str:
    def replacer(match: re.Match[str]) -> str:
        src = (match.group("src") or "").strip()
        alt = match.group("alt") or ""
        if not src or re.match(r"^[a-zA-Z]+://", src) or src.startswith("data:"):
            return match.group(0)
        resolved = _resolve_attachment_path(source_root, page_rel, src)
        name = Path(unquote(src)).name
        if resolved:
            attachments.add(resolved)
            return f"![]({name})"
        return match.group(0)

    return re.sub(r"!\[(?P<alt>[^\]]*)\]\((?P<src>[^)]+)\)", replacer, text)


def convert_content(
    raw: str,
    page_rel: str,
    page_map: Dict[str, str],
    source_root: Path,
) -> Tuple[str, List[Path]]:
    attachments: Set[Path] = set()
    text = _rewrite_wiki_and_embeds(raw, page_rel, page_map, attachments, source_root)
    text = _rewrite_markdown_images(text, page_rel, attachments, source_root)
    return text, sorted(attachments)


def plan_import(
    source_path: Path,
    target_folder: str,
) -> Tuple[List[ImportPage], int]:
    if not source_path.exists():
        raise FileNotFoundError(source_path)
    source_root = source_path if source_path.is_dir() else source_path.parent
    md_files = [p for p in sorted(source_root.rglob("*.md")) if p.is_file()]
    if not md_files:
        return [], 0

    page_map = _build_page_map(md_files, source_root, target_folder)
    pages: List[ImportPage] = []
    attachment_count = 0

    for file_path in md_files:
        rel = file_path.relative_to(source_root)
        rel_stem = rel.with_suffix("").as_posix()
        dest_path = _dest_path(target_folder, rel_stem)
        raw = file_path.read_text(encoding="utf-8")
        content, attachments = convert_content(raw, rel_stem, page_map, source_root)
        attachment_count += len(attachments)
        pages.append(
            ImportPage(
                source=file_path,
                rel_stem=rel_stem,
                dest_path=dest_path,
                content=content,
                attachments=list(attachments),
            )
        )
    return pages, attachment_count
