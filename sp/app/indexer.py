from __future__ import annotations

import re
from pathlib import Path
import hashlib
from typing import List, Set, Optional, Dict

from sp.app import config
from sp.app.ui.path_utils import colon_to_path, normalize_link_target
from sp.server.adapters.files import PAGE_SUFFIX, PAGE_SUFFIXES

# Bump this when task parsing logic changes to force re-index even if file hash is unchanged.
INDEX_SCHEMA_VERSION = "task-parse-v5"

# Match @tags that are not part of email addresses or similar identifiers.
TAG_PATTERN = re.compile(r"(?<![\w.+-])@([A-Za-z0-9_]+)")
# Match URLs to exclude tags within them
URL_PATTERN = re.compile(r"https?://[^\s<>\"'\]\)]+")
# Markdown-style links: [label](target)
MARKDOWN_LINK_PATTERN = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
# Wiki-style links used by the editor's storage format: [target|label]
WIKI_LINK_PATTERN = re.compile(r"\[(?P<link>[^\]|]+)\|[^\]]*\]")
# Plain colon links written directly in text, e.g., :Journal:2024:01:05:05#Morning
PLAIN_COLON_LINK_PATTERN = re.compile(r"(?<!\w):(?P<link>[^\s\[\]<>\"'()]+)")
# Tasks: support markdown checkboxes "- [ ]" and "- [x]" plus symbol bullets "☐/☑"
TASK_PATTERN = re.compile(
    r"^(?P<indent>\s*)"
    r"(?:[-*]\s*\[(?P<state1>[ xX])\]|(?P<symbol>[☐☑]))"
    r"\s+(?P<body>.+)$"
)
DUE_PATTERN = re.compile(r"<([0-9]{4}-[0-9]{2}-[0-9]{2})")
START_PATTERN = re.compile(r">([0-9]{4}-[0-9]{2}-[0-9]{2})")
PRIORITY_PATTERN = re.compile(r"!{1,3}")


def _indent_width(indent: str) -> int:
    """Return a consistent width for mixed tabs/spaces indentation."""
    width = 0
    for ch in indent:
        width += 4 if ch == "\t" else 1
    return width


def _extract_tags(text: str) -> list[str]:
    """Extract @tags from text, excluding tags that appear within URLs.
    
    Example: "Check @issue http://example.com?@thread=123 @bug" returns ["issue", "bug"]
    """
    # Find all URL positions
    url_ranges = [(m.start(), m.end()) for m in URL_PATTERN.finditer(text)]
    
    # Find all tag matches
    all_tags = []
    for match in TAG_PATTERN.finditer(text):
        tag_pos = match.start()
        # Check if this tag is inside a URL
        in_url = any(start <= tag_pos < end for start, end in url_ranges)
        if not in_url:
            all_tags.append(match.group(1))
    
    return all_tags


def index_page(path: str, content: str) -> bool:
    """Index page metadata into the per-vault database.

    Returns True if the index was updated (content changed), False if skipped.
    """
    if not config.has_active_vault():
        return False
    # Fast short-circuit: if content hash unchanged, skip heavy parsing and DB writes
    digest = hashlib.md5((INDEX_SCHEMA_VERSION + content).encode("utf-8")).hexdigest()
    prev = config.get_page_hash(path)
    if prev == digest:
        return False

    tags = sorted(set(_extract_tags(content)))
    link_targets = _extract_link_targets(content, path)
    # Automatically add a link from the parent page to this page if it is a child
    parent = Path(path).parent
    if parent and parent.name and parent.as_posix() != ".":
        parent_file = parent / f"{parent.name}{PAGE_SUFFIX}"
        parent_path = f"/{parent_file.as_posix()}"
        # Only add if not already present
        if path not in link_targets:
            # Add backlink from parent to this page
            try:
                # Read parent content
                from sp.app import config as _config
                vault_root = _config.get_vault_root()
                if vault_root:
                    abs_parent = Path(vault_root) / parent_path.lstrip("/")
                    if abs_parent.exists():
                        with open(abs_parent, "r", encoding="utf-8") as f:
                            parent_content = f.read()
                        # Add link if not present
                        if path not in _extract_link_targets(parent_content, parent_path):
                            # Insert a link at the end and re-index parent
                            new_content = parent_content.rstrip() + f"\n[{path}|]"
                            with open(abs_parent, "w", encoding="utf-8") as f:
                                f.write(new_content)
                            # Recursively index parent
                            index_page(parent_path, new_content)
            except Exception:
                pass
    links = sorted(link_targets)
    tasks = extract_tasks(path, content)
    title = derive_title(path, content)
    config.update_page_index(path, title, tags, links, tasks)
    config.set_page_hash(path, digest)
    return True


def derive_title(path: str, content: str) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("# ")
    return Path(path).stem or Path(path).name


def _extract_link_targets(content: str, current_path: Optional[str] = None) -> Set[str]:
    """Extract page link targets from markdown and wiki-style links."""
    targets: Set[str] = set()
    for raw in MARKDOWN_LINK_PATTERN.findall(content):
        normalized = _normalize_page_link(raw)
        if normalized:
            targets.add(normalized)
    for match in WIKI_LINK_PATTERN.finditer(content):
        raw = match.group("link")
        end = match.end()
        if end < len(content) and content[end] == "(":
            continue
        normalized = _normalize_page_link(raw)
        if normalized:
            targets.add(normalized)
    for match in PLAIN_COLON_LINK_PATTERN.finditer(content):
        raw = match.group("link")
        normalized = _normalize_page_link(raw)
        if normalized:
            targets.add(normalized)

    # Extract CamelCase/plus-prefixed links: +PageName, only when separated by whitespace
    camel_pattern = re.compile(r"(?<!\S)\+(?P<link>[A-Za-z][\w]*)(?=\s|$)")
    for match in camel_pattern.finditer(content):
        link = match.group("link")
        if link:
            # Resolve relative to current page's folder
            if current_path:
                parent = Path(current_path).parent
                if parent.parts:
                    page_path = f"/{parent.as_posix()}/{link}{PAGE_SUFFIX}"
                else:
                    page_path = f"/{link}{PAGE_SUFFIX}"
            else:
                page_path = f"/{link}{PAGE_SUFFIX}"
            targets.add(page_path)
    return targets

def _normalize_page_link(link: str) -> Optional[str]:
    """Normalize a link target to a vault-relative page path with .md suffix.

    Returns None for external URLs or non-page resources.
    """
    raw = (link or "").strip()
    # Drop any label/extra text that might remain after parsing (e.g., "target|label")
    if "|" in raw:
        raw = raw.split("|", 1)[0]
    # Drop trailing delimiters (commas/semicolons/periods) that may follow inline lists
    raw = raw.rstrip(",.;")
    cleaned = normalize_link_target(raw).strip()
    if not cleaned:
        return None
    if cleaned.startswith(("http://", "https://", "mailto:", "ftp://")):
        return None
    base = cleaned.split("#", 1)[0]
    if not base:
        return None

    # Colon notation (PageA:PageB) is the preferred storage format
    if ":" in base and not base.startswith("/"):
        return colon_to_path(base)

    # Slash paths - ensure they are anchored at root and normalize underscores to spaces for consistency
    base = base.replace("_", " ")
    path = base if base.startswith("/") else f"/{base}"
    path_obj = Path(path)
    # Skip obvious non-page assets (images, docs, etc.)
    if path_obj.suffix and path_obj.suffix.lower() not in PAGE_SUFFIXES:
        return None
    if path_obj.suffix.lower() in PAGE_SUFFIXES:
        path_obj = path_obj.with_suffix(PAGE_SUFFIX)
    elif path_obj.suffix.lower() != PAGE_SUFFIX:
        leaf = path_obj.name or path_obj.parent.name
        if not leaf:
            return None
        path_obj = path_obj / f"{leaf}{PAGE_SUFFIX}"
    normalized = path_obj.as_posix()
    if not normalized.startswith("/"):
        normalized = "/" + normalized
    return normalized


def extract_tasks(path: str, content: str) -> List[dict]:
    tasks: List[dict] = []
    stack: List[tuple[int, dict]] = []

    for line_no, line in enumerate(content.splitlines(), start=1):
        match = TASK_PATTERN.match(line)
        if not match:
            continue

        indent_raw = match.group("indent") or ""
        indent_len = _indent_width(indent_raw)
        # Find the nearest parent with a smaller indent
        while stack and stack[-1][0] >= indent_len:
            stack.pop()
        parent = stack[-1][1] if stack else None

        body = match.group("body")
        state = match.group("state1")
        if not state:
            symbol = match.group("symbol")
            state = "x" if symbol == "☑" else " "
        # Inherit tags from parent, add own tags
        own_tags = set(_extract_tags(body))
        parent_tags = set(parent["tags"]) if parent else set()
        tags = sorted(parent_tags | own_tags)
        explicit_due = _first_match(DUE_PATTERN, body)
        start = _first_match(START_PATTERN, body)
        pri_matches = PRIORITY_PATTERN.findall(body)
        explicit_priority = min(max((len(m) for m in pri_matches), default=0), 3)
        clean_text = TAG_PATTERN.sub(" ", body)
        clean_text = DUE_PATTERN.sub(" ", clean_text)
        clean_text = START_PATTERN.sub(" ", clean_text)
        clean_text = PRIORITY_PATTERN.sub(" ", clean_text)
        clean_text = re.sub(r"\s{2,}", " ", clean_text).strip()

        effective_due = explicit_due or (parent.get("due") if parent else None)
        inherited_priority = parent.get("priority", 0) if parent else 0
        effective_priority = explicit_priority if explicit_priority > 0 else inherited_priority

        task_id = f"{path}:{line_no}"
        task = {
            "id": task_id,
            "line": line_no,
            "text": clean_text,
            "status": "done" if state.lower() == "x" else "todo",
            "priority": effective_priority,
            "due": effective_due,
            "start": start,
            "tags": tags,
            "parent": parent["id"] if parent else None,
            "level": len(stack),
        }
        tasks.append(task)
        stack.append((indent_len, task))

    children: Dict[str, list[dict]] = {}
    for task in tasks:
        parent_id = task.get("parent")
        if parent_id:
            children.setdefault(parent_id, []).append(task)

    def has_open_descendants(node: dict) -> bool:
        """Return True if any descendant is still a todo."""
        for child in children.get(node["id"], []):
            if child["status"] != "done" or has_open_descendants(child):
                return True
        return False

    for task in tasks:
        task["actionable"] = task["status"] != "done" and not has_open_descendants(task)

    return tasks


def _first_match(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text)
    return match.group(1) if match else None
