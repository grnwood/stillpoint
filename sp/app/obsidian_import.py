"""Plan Obsidian imports without changing either vault."""
from __future__ import annotations

import os
import posixpath
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

from sp.server.adapters.files import PAGE_SUFFIX

EXCLUDED = {".obsidian", ".git", ".trash"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp", ".avif"}


@dataclass(frozen=True)
class ImportAttachment:
    source: Path
    name: str


@dataclass
class ImportPage:
    source: Path
    rel_stem: str
    dest_path: str
    content: str = ""
    attachments: list[ImportAttachment] = field(default_factory=list)


@dataclass
class ImportPlan:
    pages: list[ImportPage] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def attachment_count(self) -> int:
        return sum(len(page.attachments) for page in self.pages)


def _key(value: str) -> str:
    # Spaces and underscores share a StillPoint page-link spelling.
    return unicodedata.normalize("NFC", value).replace(" ", "_").casefold()


def normalize_folder_path(path: str) -> str:
    parts = PurePosixPath(path.replace("\\", "/")).parts
    if ".." in parts:
        raise ValueError("Import destination cannot contain '..'.")
    return "/" + "/".join(p for p in parts if p not in {"/", "."})


def _dest_path(target_folder: str, rel_stem: str) -> str:
    rel = PurePosixPath(rel_stem)
    return f"{normalize_folder_path(target_folder).rstrip('/')}/{rel}/{rel.name}{PAGE_SUFFIX}"


def _page_target(dest_path: str) -> str:
    parts = PurePosixPath(dest_path).parent.parts
    return ":" + ":".join(p.replace(" ", "_") for p in parts if p != "/")


def _discover(root: Path) -> list[Path]:
    def scan_error(error: OSError) -> None:
        raise error

    files = []
    for directory, dirs, names in os.walk(root, followlinks=False, onerror=scan_error):
        dirs[:] = sorted(d for d in dirs if d.casefold() not in EXCLUDED
                         and not (Path(directory) / d).is_symlink())
        for name in sorted(names):
            path = Path(directory) / name
            # Symlinks are deliberately excluded, including links within the vault.
            if not path.is_symlink() and path.is_file():
                files.append(path)
    return files


class _Resolver:
    def __init__(self, root: Path, files: list[Path]):
        self.root = root
        self.exact = {p.relative_to(root).as_posix(): p for p in files}
        self.folded: dict[str, list[Path]] = defaultdict(list)
        self.basenames: dict[str, list[Path]] = defaultdict(list)
        for rel, path in self.exact.items():
            self.folded[unicodedata.normalize("NFC", rel).casefold()].append(path)
            self.basenames[path.name.casefold()].append(path)

    def resolve(self, target: str, page: Path, wiki: bool) -> tuple[Path | None, str]:
        if "\\" in target:
            target = target.replace("\\", "/")
        parent = page.parent.relative_to(self.root).as_posix()
        relative = posixpath.normpath(posixpath.join(parent, target))
        rooted = posixpath.normpath(target.lstrip("/"))
        if target.startswith("/"):
            candidates = [rooted]
        elif target.startswith(("./", "../")) or not wiki:
            candidates = [relative] if target.startswith(("./", "../")) else [relative, rooted]
        else:
            candidates = [rooted, relative] if "/" in target else [relative, rooted]
        for candidate in dict.fromkeys(candidates):
            if candidate == ".." or candidate.startswith("../"):
                continue
            if candidate in self.exact:
                return self.exact[candidate], ""
            matches = self.folded.get(unicodedata.normalize("NFC", candidate).casefold(), [])
            if len(matches) == 1:
                return matches[0], ""
            if matches:
                return None, "ambiguous target"
        if "/" not in target:
            matches = self.basenames.get(target.casefold(), [])
            if len(matches) == 1:
                return matches[0], ""
            if matches:
                return None, "ambiguous target"
        return None, "missing or excluded target"


# Match inline code before references, and match wiki/Markdown syntax in a single
# pass so generated StillPoint links are never processed a second time.
_INLINE = re.compile(
    r"(?P<code>(?P<ticks>`+)(?!`)(?:(?!(?P=ticks)).)*?(?P=ticks)(?!`))"
    r"|(?P<wiki>(?P<wbang>!)?\[\[(?P<wt>[^\]\n|]+)(?:\|(?P<wl>[^\]\n]*))?\]\])"
    r"|(?P<md>(?P<mbang>!)?\[(?P<ml>(?:\\.|[^\]\\\n])*)\]"
    r"\((?P<mt><[^>\n]+>|(?:\\.|[^()\n]|\([^()\n]*\))+?)\))",
    re.DOTALL,
)


def _convert(page: ImportPage, resolver: _Resolver, destinations: dict[Path, str],
             plan: ImportPlan, vault_root: Path | None, reserved: set[str]) -> str:
    raw = page.source.read_text(encoding="utf-8-sig")
    assigned: dict[Path, str] = {}
    page_file = PurePosixPath(page.dest_path)
    used = {_key(page_file.name), _key(page_file.with_suffix(".txt").name)} | reserved
    if vault_root:
        folder = vault_root / PurePosixPath(page.dest_path).parent.as_posix().lstrip("/")
        if folder.exists():
            used.update(_key(p.name) for p in folder.iterdir())

    warned: set[str] = set()

    def warn(target: str, reason: str) -> None:
        message = f"{page.source.relative_to(resolver.root).as_posix()}: {target}: {reason}"
        if message not in warned:
            warned.add(message)
            plan.warnings.append(message)

    def rewrite(match: re.Match) -> str:
        original = match.group(0)
        if match.group("code"):
            return original
        wiki = bool(match.group("wiki"))
        target = (match.group("wt") if wiki else match.group("mt")).strip()
        label = match.group("wl") if wiki else match.group("ml")
        embed = bool(match.group("wbang") if wiki else match.group("mbang"))
        title = ""
        if not wiki:
            # Keep optional Markdown titles on attachments.
            titled = re.fullmatch(r'(.*?)\s+((?:"[^"\n]*"|\'[^\'\n]*\'))', target)
            if titled:
                target, title = titled.groups()
                title = " " + title
            target = target.removeprefix("<").removesuffix(">")
            target = re.sub(r"\\([() ])", r"\1", target)
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target) or target.startswith("//"):
            return original
        base, sep, fragment = target.partition("#")
        base = unquote(base)
        if "^" in base or fragment.startswith("^"):
            warn(target, "block references are preserved as source text")
            return original
        if not base:
            return original
        suffix = PurePosixPath(base).suffix.lower()
        lookup = base if suffix else base + ".md"
        resolved, reason = resolver.resolve(lookup, page.source, wiki)
        # Extensionless attachment names are valid too.
        if resolved is None and not suffix:
            resolved, reason = resolver.resolve(base, page.source, wiki)
        if resolved is None:
            warn(target, reason)
            return original
        if resolved.suffix.lower() == ".md":
            if embed:
                warn(target, "note transclusions are preserved as source text")
                return original
            if resolved not in destinations:
                warn(target, "target note was skipped")
                return original
            link = _page_target(destinations[resolved]) + (sep + fragment if sep else "")
            display = label if label is not None else match.group("wt")
            return f"[{link}|{display}]"
        width = ""
        if wiki and embed and label and re.fullmatch(r"\d+(?:x\d+)?", label):
            if "x" in label:
                warn(target, "two-dimensional image sizing is preserved as source text; attachment not copied")
                return original
            width = f"{{width={label}}}"
            label = None
        if resolved not in assigned:
            # StillPoint's inline image/file readers use literal paths, and its
            # image syntax excludes whitespace and parentheses. Assign portable
            # local names instead of emitting URL escapes the editor cannot open.
            base_name = re.sub(r'''[\s\[\]()#%?<>|"'\\]''', "_", resolved.name)
            name = base_name
            number = 2
            while _key(name) in used:
                stem, suffix = Path(base_name).stem, Path(base_name).suffix
                name = f"{stem}-{number}{suffix}"
                number += 1
            assigned[resolved] = name
            used.add(_key(name))
            page.attachments.append(ImportAttachment(resolved, name))
        local = "./" + assigned[resolved]
        if sep:
            local += "#" + fragment
        display = label if label is not None else resolved.name
        is_image = embed and resolved.suffix.lower() in IMAGE_EXTS
        if title and is_image:
            warn(target, "image title retained; StillPoint inline image rendering does not support titles")
        return f"{'!' if is_image else ''}[{display}]({local}{title}){width if is_image else ''}"

    # Protect YAML frontmatter and fenced code (including unclosed fences).
    lines = raw.splitlines(keepends=True)
    output: list[str] = []
    pending: list[str] = []
    fence: tuple[str, int] | None = None
    frontmatter = bool(lines and lines[0].strip() == "---")
    for index, line in enumerate(lines):
        if frontmatter:
            output.append(line)
            if index and line.strip() in {"---", "..."}:
                frontmatter = False
            continue
        opening = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line.rstrip("\r\n"))
        if fence:
            output.append(line)
            if re.fullmatch(r" {0,3}" + re.escape(fence[0]) + "{" + str(fence[1]) + r",}\s*", line):
                fence = None
        elif opening:
            output.append(_INLINE.sub(rewrite, "".join(pending)))
            pending.clear()
            fence = (opening[1][0], len(opening[1]))
            output.append(line)
        else:
            pending.append(line)
    output.append(_INLINE.sub(rewrite, "".join(pending)))
    return "".join(output)


def plan_import(source_path: Path, target_folder: str, vault_root: Path | None = None) -> ImportPlan:
    source = source_path.resolve()
    if not source.is_dir():
        raise ValueError("Select an Obsidian vault folder.")
    target_folder = normalize_folder_path(target_folder)
    if vault_root:
        vault_root = vault_root.resolve()
        if source == vault_root or source in vault_root.parents or vault_root in source.parents:
            raise ValueError("Source and destination vaults must not overlap.")
        destination = (vault_root / target_folder.lstrip("/")).resolve()
        if not destination.is_relative_to(vault_root):
            raise ValueError("Destination escapes the StillPoint vault.")
    files = _discover(source)
    resolver = _Resolver(source, files)
    plan = ImportPlan()
    groups: dict[str, list[ImportPage]] = defaultdict(list)
    for path in files:
        if path.suffix.lower() == ".md":
            stem = path.relative_to(source).with_suffix("").as_posix()
            page = ImportPage(path, stem, _dest_path(target_folder, stem))
            groups[_key(str(PurePosixPath(page.dest_path).parent))].append(page)
    spellings: dict[str, set[str]] = defaultdict(set)
    for pages in groups.values():
        for page in pages:
            folder = PurePosixPath(page.dest_path).parent
            for parent in (folder, *folder.parents):
                spellings[_key(str(parent))].add(str(parent))
    existing: dict[str, list[Path]] = defaultdict(list)
    if vault_root:
        # Include directories and non-page files: existing parent names and file
        # blockers must not be bypassed by case/underscore spelling differences.
        for directory, dirs, names in os.walk(vault_root, followlinks=False):
            dirs[:] = [d for d in dirs if d.casefold() not in EXCLUDED]
            for name in dirs + names:
                path = Path(directory) / name
                existing[_key(path.relative_to(vault_root).as_posix())].append(path)
    for pages in groups.values():
        for page in pages:
            reason = "conflicting source note names" if len(pages) > 1 else ""
            folder = PurePosixPath(page.dest_path).parent
            if any(len(spellings[_key(str(parent))]) > 1 for parent in (folder, *folder.parents)):
                reason = "conflicting source folder or note names"
            if vault_root and not reason:
                rel = PurePosixPath(page.dest_path.lstrip("/"))
                for parent in reversed(rel.parent.parents):
                    if str(parent) == ".":
                        continue
                    matches = existing.get(_key(str(parent)), [])
                    if any(p.is_symlink() or not p.is_dir() or p.relative_to(vault_root).as_posix() != str(parent) for p in matches):
                        reason = "conflicting or unsafe destination folder"
                matches = existing.get(_key(str(rel.parent)), [])
                if any(p.is_symlink() or not p.is_dir() or p.relative_to(vault_root).as_posix() != str(rel.parent) for p in matches):
                    reason = "conflicting or unsafe destination folder"
                for suffix in (".md", ".txt"):
                    if existing.get(_key(str(rel.with_suffix(suffix)))):
                        reason = "destination page already exists"
            if reason:
                plan.skipped.append(f"{page.rel_stem}: {reason}")
            else:
                plan.pages.append(page)
    destinations = {page.source: page.dest_path for page in plan.pages}
    reserved: dict[str, set[str]] = defaultdict(set)
    for destination in destinations.values():
        folder = PurePosixPath(destination).parent
        for child in (folder, *folder.parents):
            if child.name:
                reserved[str(child.parent)].add(_key(child.name))
    for page in plan.pages:
        page.content = _convert(page, resolver, destinations, plan, vault_root,
                                reserved[str(PurePosixPath(page.dest_path).parent)])
    return plan
