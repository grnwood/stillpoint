from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Dict, Iterable, List, Tuple, Mapping, Optional

from sp.server.adapters.files import PAGE_SUFFIX, strip_page_suffix

ZIM_PAGE_SUFFIX = ".txt"


HEADER_PREFIXES = ("Content-Type:", "Wiki-Format:", "Creation-Date:")


@dataclass
class ImportPage:
    source: Path
    rel_stem: str  # e.g., "Home" or "Sub/Page"
    dest_path: str  # vault-relative file path (with .md), leading slash
    content: str
    attachments: List[Path]


def normalize_folder_path(path: str) -> str:
    cleaned = (path or "").strip().replace("\\", "/")
    cleaned = cleaned.lstrip("/")
    cleaned = cleaned.rstrip("/")
    return f"/{cleaned}" if cleaned else "/"


def _dest_path(target_folder: str, rel_stem: str) -> str:
    base = normalize_folder_path(target_folder).lstrip("/")
    base_path = Path(base) if base else Path()
    rel_path = PurePosixPath(rel_stem)
    dest_dir = base_path.joinpath(rel_path)
    dest_file = dest_dir / f"{rel_path.name}{PAGE_SUFFIX}"
    return f"/{dest_file.as_posix()}"


def _apply_rename_path(path: str, rename_map: Optional[Mapping[str, str]]) -> str:
    """Apply segment-wise renames (e.g., 9-Journal -> Journal) to a posix path."""
    if not rename_map:
        return path
    parts = [rename_map.get(part, part) for part in Path(path).parts]
    # Preserve leading slash if present in input
    prefix = "/" if path.startswith("/") else ""
    return prefix + "/".join(parts).lstrip("/")


def _apply_rename_colon(colon_path: str, rename_map: Optional[Mapping[str, str]]) -> str:
    """Apply renames to colon-separated links."""
    if not rename_map or not colon_path:
        return colon_path
    anchor = ""
    base = colon_path
    if "#" in colon_path:
        base, anchor = colon_path.split("#", 1)
    clean = base.lstrip(":")
    parts = [rename_map.get(p, p) for p in clean.split(":") if p]
    renamed = ":".join(parts)
    if anchor:
        renamed = f"{renamed}#{anchor}"
    return f":{renamed}" if renamed else colon_path


def _ensure_root_colon(link: str) -> str:
    text = (link or "").strip()
    if not text or text.startswith(":") or text.startswith("#"):
        return text
    if "#" in text:
        base, anchor = text.split("#", 1)
        base = base.lstrip(":")
        return f":{base}#{anchor}"
    return f":{text.lstrip(':')}"


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


def _strip_headers(lines: List[str]) -> List[str]:
    idx = 0
    while idx < len(lines):
        line = lines[idx].strip()
        if not line:
            idx += 1
            break
        if any(line.startswith(prefix) for prefix in HEADER_PREFIXES):
            idx += 1
            continue
        break
    return lines[idx:] if idx else lines


def _convert_headings(line: str) -> str:
    m = re.match(r"^(\s*)(=+)\s*(.*?)\s*=*\s*$", line)
    if not m:
        return line
    indent, marks, body = m.groups()
    level = max(1, min(5, 7 - len(marks)))  # 6 '=' -> H1, 2 '=' -> H5
    return f"{indent}{'#' * level} {body.strip()}"


def _convert_tasks(line: str) -> str:
    m = re.match(r"^(\s*)\[(?P<state>[ xX\*\>\<])\]\s*(.*)$", line)
    if not m:
        return line
    state = m.group("state").lower()
    rest = m.group(3)
    done = state in {"x", "*"}
    marker = "- [x]" if done else "- [ ]"
    return f"{m.group(1)}{marker} {rest}".rstrip()


def _convert_inline(text: str) -> str:
    converted = text
    # Bold+italic: //**text**// -> ***text***
    converted = re.sub(r"//\*\*(.+?)\*\*//", r"***\1***", converted, flags=re.DOTALL)
    # Italic: //text// -> *text*
    converted = re.sub(r"//(.+?)//", r"*\1*", converted, flags=re.DOTALL)
    # Fixed width: ''code'' -> `code`
    converted = re.sub(r"''(.+?)''", r"`\1`", converted, flags=re.DOTALL)
    return converted


def _rewrite_links(text: str, page_rel: str, page_map: Dict[str, str], rename_map: Optional[Mapping[str, str]]) -> str:
    def replacer(match: re.Match[str]) -> str:
        target = (match.group("target") or "").strip()
        label = match.group("label") or ""
        if not target:
            return match.group(0)
        if re.match(r"https?://", target, flags=re.IGNORECASE):
            display = label or target
            return f"[{target}|{display}]"
        # Attachment/file link (has extension)
        if re.search(r"\.[A-Za-z0-9]{1,8}$", target):
            display = label or target
            normalized = target.lstrip("./")
            return f"[{display}](./{normalized})"

        # Page link
        resolved = _resolve_page_target(target, page_rel, page_map, rename_map)
        if resolved:
            display = label or target
            return f"[{_ensure_root_colon(resolved)}|{display}]"
        display = label or target
        return f"[{target}|{display}]"

    return re.sub(r"\[\[(?P<target>[^\]|]+)(?:\|(?P<label>[^\]]*))?\]\]", replacer, text)


def _resolve_page_target(
    target: str, page_rel: str, page_map: Dict[str, str], rename_map: Optional[Mapping[str, str]]
) -> str | None:
    # Normalize and try relative to current page
    base_page = PurePosixPath(page_rel)
    target_clean = target.strip()
    target_clean = strip_page_suffix(target_clean)
    # Absolute colon link: apply rename and return
    if ":" in target_clean:
        renamed = _apply_rename_colon(target_clean, rename_map)
        return renamed
    plus_child = target_clean.startswith("+")
    if plus_child:
        target_clean = target_clean.lstrip("+")
        candidate_base = base_page  # +Child refers to a subpage of the current page
    else:
        candidate_base = base_page.parent

    candidate = candidate_base.joinpath(target_clean).as_posix().strip("/")
    keys = [
        candidate,
        candidate.lower(),
        target_clean.strip("/"),
        target_clean.strip("/").lower(),
    ]
    for key in keys:
        if key in page_map:
            return page_map[key]
    # Fallback: apply rename map to the candidate and return colon-path
    renamed_candidate = _apply_rename_path(candidate, rename_map)
    target_path = f"/{renamed_candidate}/{Path(renamed_candidate).name}{PAGE_SUFFIX}"
    return _path_to_colon(target_path)


def convert_content(raw: str, page_rel: str, page_map: Dict[str, str], rename_map: Optional[Mapping[str, str]]) -> str:
    lines = _strip_headers(raw.splitlines())
    converted_lines = []
    for line in lines:
        line = _convert_headings(line)
        line = _convert_tasks(line)
        line = _convert_inline(line)
        converted_lines.append(line)
    converted = "\n".join(converted_lines)
    converted = _convert_inline_images(converted, page_rel)
    converted = _rewrite_links(converted, page_rel, page_map, rename_map)
    converted = _convert_plus_links(converted, page_rel, page_map, rename_map)
    return converted.strip() + "\n"


def _convert_plus_links(
    text: str, page_rel: str, page_map: Dict[str, str], rename_map: Optional[Mapping[str, str]]
) -> str:
    """Convert +CamelCase links to root-colon links relative to the current page."""
    allowed_prefixes = {"(", "[", "{", "<", "'", '"'}
    base_page = PurePosixPath(page_rel)

    def replacer(match: re.Match[str]) -> str:
        start = match.start()
        if start > 0:
            prev = text[start - 1]
            if not prev.isspace() and prev not in allowed_prefixes:
                return match.group(0)
        link = match.group("link")
        if not link:
            return match.group(0)
        target_rel = base_page.joinpath(link).as_posix().strip("/")
        for key in (target_rel, target_rel.lower()):
            if key in page_map:
                colon = _ensure_root_colon(page_map[key])
                return f"[{colon}|{link}]"
        renamed_rel = _apply_rename_path(target_rel, rename_map)
        target_path = f"/{renamed_rel}/{Path(renamed_rel).name}{PAGE_SUFFIX}"
        colon = _ensure_root_colon(_path_to_colon(target_path))
        return f"[{colon}|{link}]"

    return re.sub(r"\+(?P<link>[A-Z][\w]*)", replacer, text)


def _convert_inline_images(text: str, page_rel: str) -> str:
    """Convert Zim-style inline images {{./img.png?400x300}} → markdown images."""
    def replacer(match: re.Match[str]) -> str:
        raw = (match.group("src") or "").strip()
        if not raw:
            return match.group(0)
        path = raw
        width_attr = ""
        if "?" in raw:
            base, query = raw.split("?", 1)
            path = base
            size = re.match(r"(?P<w>\d+)(x(?P<h>\d+))?", query)
            if size and size.group("w"):
                width_attr = f"{{width={size.group('w')}}}"
        # Keep relative paths as-is; Zim usually uses ./ relative to the page
        return f"![]({path}){width_attr}"

    return re.sub(
        r"\{\{\s*(?P<src>[^}\s][^}]*(?:\.(?:png|jpg|jpeg|gif|svg|webp|bmp))(?:\?[^}]*)?)\s*\}\}",
        replacer,
        text,
        flags=re.IGNORECASE,
    )


def _attachments_for_page(source_root: Path, rel_stem: str) -> List[Path]:
    attach_dir = source_root / rel_stem
    if not attach_dir.exists() or not attach_dir.is_dir():
        return []
    attachments: List[Path] = []
    for path in sorted(attach_dir.iterdir()):
        if path.is_file() and path.suffix.lower() != ZIM_PAGE_SUFFIX:
            attachments.append(path)
    return attachments


def _read_text_lossy(path: Path) -> str:
    """Read text with fallback encodings for legacy Zim exports."""
    encodings = ("utf-8", "utf-8-sig", "cp1252", "latin-1")
    for enc in encodings:
        try:
            return path.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="replace")


def plan_import(
    source_path: Path,
    target_folder: str,
    rename_map: Optional[Mapping[str, str]] = None,
) -> Tuple[List[ImportPage], int]:
    if not source_path.exists():
        raise FileNotFoundError(source_path)
    source_root = source_path if source_path.is_dir() else source_path.parent
    txt_files = [source_path] if source_path.is_file() else sorted(source_root.rglob(f"*{ZIM_PAGE_SUFFIX}"))
    txt_files = [p for p in txt_files if p.is_file()]
    if not txt_files:
        return [], 0

    page_map: Dict[str, str] = {}
    rel_map: Dict[Path, str] = {}
    for file_path in txt_files:
        rel = file_path.relative_to(source_root)
        rel_stem = rel.with_suffix("").as_posix()
        renamed_rel = _apply_rename_path(rel_stem, rename_map)
        dest_path = _dest_path(target_folder, renamed_rel)
        colon = _path_to_colon(dest_path)
        key = rel_stem.strip("/").lower()
        page_map[key] = colon
        rel_map[file_path] = rel_stem
        # Also map by leaf/stem only for convenience
        stem_key = Path(rel_stem).name.lower()
        page_map.setdefault(stem_key, colon)

    pages: List[ImportPage] = []
    attachment_count = 0
    for file_path in txt_files:
        rel_stem = rel_map[file_path]
        renamed_rel = _apply_rename_path(rel_stem, rename_map)
        dest_path = _dest_path(target_folder, renamed_rel)
        raw = _read_text_lossy(file_path)
        content = convert_content(raw, rel_stem, page_map, rename_map)
        attachments = _attachments_for_page(source_root, rel_stem)
        attachment_count += len(attachments)
        pages.append(
            ImportPage(
                source=file_path,
                rel_stem=rel_stem,
                dest_path=dest_path,
                content=content,
                attachments=attachments,
            )
        )
    return pages, attachment_count
