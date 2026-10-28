from __future__ import annotations

import difflib
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional


VERSION_TAG_PREFIX = "stillpoint/version/"
AUTOMATIC_BACKUP_PREFIX = "Automatic backup · "
DEFAULT_GITIGNORE_ENTRIES = (
    ".stillpoint/",
    ".codex/",
    ".mcp.json",
)


class GitError(RuntimeError):
    """A user-actionable Git failure."""


@dataclass(frozen=True)
class GitStatusEntry:
    path: str
    index: str
    worktree: str
    original_path: Optional[str] = None

    @property
    def conflicted(self) -> bool:
        code = self.index + self.worktree
        return "U" in code or code in {"AA", "DD"}


@dataclass(frozen=True)
class GitHistoryEntry:
    commit: str
    committed_at: str
    author: str
    subject: str
    version_name: Optional[str] = None
    version_tag: Optional[str] = None


@dataclass(frozen=True)
class GitRepositoryState:
    initialized: bool
    exact_root: bool
    branch: str = ""
    remote_name: str = ""
    remote_url: str = ""
    changed_files: int = 0
    ahead: int = 0
    behind: int = 0
    has_upstream: bool = False


def _command(*arguments: str) -> list[str]:
    executable = shutil.which("git") or "git"
    if (
        sys.platform == "darwin"
        and platform.machine() == "x86_64"
        and Path(executable).resolve() == Path("/usr/bin/git")
        and Path("/usr/bin/arch").exists()
    ):
        return ["/usr/bin/arch", "-arm64", "/usr/bin/git", *arguments]
    return [executable, *arguments]


def _run(
    root: str | Path,
    arguments: Iterable[str],
    *,
    timeout: int = 60,
    check: bool = True,
    input_bytes: Optional[bytes] = None,
) -> subprocess.CompletedProcess[bytes]:
    vault_root = Path(root).expanduser().resolve()
    try:
        result = subprocess.run(
            _command("-C", str(vault_root), *list(arguments)),
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise GitError("Git is not installed or is not available on PATH.") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitError("The Git operation timed out.") from exc
    except OSError as exc:
        raise GitError(f"Could not start Git: {exc}") from exc
    if check and result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        if not detail:
            detail = result.stdout.decode("utf-8", errors="replace").strip()
        raise GitError(detail or f"Git exited with code {result.returncode}.")
    return result


def _text(result: subprocess.CompletedProcess[bytes]) -> str:
    return result.stdout.decode("utf-8", errors="replace").strip()


def repository_root(root: str | Path) -> Optional[Path]:
    vault_root = Path(root).expanduser().resolve()
    result = _run(vault_root, ["rev-parse", "--show-toplevel"], check=False)
    if result.returncode:
        return None
    value = _text(result)
    return Path(value).resolve() if value else None


def is_exact_repository(root: str | Path) -> bool:
    vault_root = Path(root).expanduser().resolve()
    repo_root = repository_root(vault_root)
    return repo_root == vault_root


def require_exact_repository(root: str | Path) -> Path:
    vault_root = Path(root).expanduser().resolve()
    repo_root = repository_root(vault_root)
    if repo_root is None:
        raise GitError("This vault is not a Git repository yet.")
    if repo_root != vault_root:
        raise GitError(
            "The vault is inside a different Git repository. Initialize a repository "
            "whose root is the vault folder before enabling Git support."
        )
    return vault_root


def ensure_metadata_ignored(root: str | Path) -> None:
    """Ignore and stop tracking StillPoint/Codex device-local metadata."""
    vault_root = Path(root).expanduser().resolve()
    ignore_path = vault_root / ".gitignore"
    try:
        current = ignore_path.read_text(encoding="utf-8") if ignore_path.exists() else ""
        normalized = {
            line.strip().rstrip("/")
            for line in current.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        missing = [
            entry
            for entry in DEFAULT_GITIGNORE_ENTRIES
            if entry.rstrip("/") not in normalized
        ]
        if missing:
            separator = "" if not current or current.endswith(("\n", "\r")) else "\n"
            block = "\n".join(missing) + "\n"
            ignore_path.write_text(current + separator + block, encoding="utf-8")
    except OSError as exc:
        raise GitError(f"Could not update .gitignore: {exc}") from exc
    # Existing repositories may already track device-local databases, agent
    # settings, or MCP configuration. Stop tracking them without deleting the
    # local files. This is especially important when converting Homebase to Git.
    if repository_root(vault_root) == vault_root:
        protected_paths = [".stillpoint", ".codex", ".mcp.json"]
        tracked = _run(
            vault_root,
            ["ls-files", "-z", "--", *protected_paths],
            check=False,
        )
        if tracked.returncode == 0 and tracked.stdout:
            _run(
                vault_root,
                [
                    "rm",
                    "-r",
                    "--cached",
                    "--ignore-unmatch",
                    "--",
                    *protected_paths,
                ],
            )


def initialize_repository(root: str | Path, *, branch: str = "main") -> None:
    vault_root = Path(root).expanduser().resolve()
    existing = repository_root(vault_root)
    if existing is not None and existing != vault_root:
        raise GitError(
            f"This vault is currently inside the repository at {existing}. "
            "StillPoint only manages repositories rooted at the vault."
        )
    if existing is None:
        result = _run(vault_root, ["init", "-b", branch], check=False)
        if result.returncode:
            # Older Git versions do not understand init -b.
            _run(vault_root, ["init"])
            _run(vault_root, ["symbolic-ref", "HEAD", f"refs/heads/{branch}"])
    ensure_metadata_ignored(vault_root)


def set_remote(root: str | Path, url: str, *, name: str = "origin") -> None:
    vault_root = require_exact_repository(root)
    cleaned = str(url or "").strip()
    if not cleaned:
        raise GitError("Enter a remote repository URL.")
    existing = _run(vault_root, ["remote", "get-url", name], check=False)
    if existing.returncode == 0:
        _run(vault_root, ["remote", "set-url", name, cleaned])
    else:
        _run(vault_root, ["remote", "add", name, cleaned])


def remote_url(root: str | Path, *, name: str = "origin") -> str:
    vault_root = require_exact_repository(root)
    result = _run(vault_root, ["remote", "get-url", name], check=False)
    return _text(result) if result.returncode == 0 else ""


def preferred_remote(root: str | Path) -> tuple[str, str]:
    """Return origin, or the first configured remote when origin is absent."""
    vault_root = require_exact_repository(root)
    result = _run(vault_root, ["remote"], check=False)
    if result.returncode:
        return "", ""
    names = [line.strip() for line in _text(result).splitlines() if line.strip()]
    if not names:
        return "", ""
    name = "origin" if "origin" in names else names[0]
    return name, remote_url(vault_root, name=name)


def test_remote(root: str | Path, *, name: str = "origin") -> None:
    vault_root = require_exact_repository(root)
    if not remote_url(vault_root, name=name):
        raise GitError("No remote repository is configured.")
    # An empty but reachable repository has no HEAD yet; that is still a valid
    # destination for a newly initialized vault.
    _run(vault_root, ["ls-remote", name], timeout=45)


def parse_status(output: bytes | str) -> list[GitStatusEntry]:
    if isinstance(output, bytes):
        output = output.decode("utf-8", errors="surrogateescape")
    records = output.split("\0")
    entries: list[GitStatusEntry] = []
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if not record or len(record) < 4 or record[2] != " ":
            continue
        code = record[:2]
        path = record[3:]
        original = None
        if code[0] in "RC" or code[1] in "RC":
            if index < len(records) and records[index]:
                original = records[index]
                index += 1
        entries.append(GitStatusEntry(path, code[0], code[1], original))
    return entries


def status(root: str | Path) -> list[GitStatusEntry]:
    vault_root = require_exact_repository(root)
    result = _run(
        vault_root,
        ["status", "--porcelain=v1", "-z", "--untracked-files=all"],
        timeout=20,
    )
    return parse_status(result.stdout)


def _ahead_behind(root: Path) -> tuple[int, int, bool]:
    upstream = _run(root, ["rev-parse", "--abbrev-ref", "@{upstream}"], check=False)
    if upstream.returncode:
        return 0, 0, False
    counts = _run(root, ["rev-list", "--left-right", "--count", "HEAD...@{upstream}"], check=False)
    if counts.returncode:
        return 0, 0, True
    try:
        ahead, behind = (int(value) for value in _text(counts).split())
    except (TypeError, ValueError):
        return 0, 0, True
    return ahead, behind, True


def repository_state(root: str | Path) -> GitRepositoryState:
    vault_root = Path(root).expanduser().resolve()
    repo_root = repository_root(vault_root)
    if repo_root is None:
        return GitRepositoryState(False, False)
    exact = repo_root == vault_root
    if not exact:
        return GitRepositoryState(True, False)
    branch_result = _run(vault_root, ["branch", "--show-current"], check=False)
    branch = _text(branch_result) if branch_result.returncode == 0 else ""
    entries = status(vault_root)
    ahead, behind, has_upstream = _ahead_behind(vault_root)
    remote_name, detected_remote_url = preferred_remote(vault_root)
    return GitRepositoryState(
        True,
        True,
        branch=branch,
        remote_name=remote_name,
        remote_url=detected_remote_url,
        changed_files=len({entry.path for entry in entries}),
        ahead=ahead,
        behind=behind,
        has_upstream=has_upstream,
    )


def has_head(root: str | Path) -> bool:
    vault_root = require_exact_repository(root)
    return _run(vault_root, ["rev-parse", "--verify", "HEAD"], check=False).returncode == 0


def save_changes(root: str | Path, message: str) -> bool:
    vault_root = require_exact_repository(root)
    cleaned = str(message or "").strip()
    if not cleaned:
        raise GitError("Enter a name for these changes.")
    ensure_metadata_ignored(vault_root)
    _run(vault_root, ["add", "--all"])
    staged = _run(vault_root, ["diff", "--cached", "--quiet"], check=False)
    if staged.returncode == 0:
        return False
    if staged.returncode != 1:
        raise GitError("Could not inspect the changes waiting to be saved.")
    _run(vault_root, ["commit", "-m", cleaned], timeout=120)
    return True


def save_automatic_backup(root: str | Path, *, now: Optional[datetime] = None) -> bool:
    """Commit outstanding vault changes as an identifiable automatic backup."""
    vault_root = require_exact_repository(root)
    entries = status(vault_root)
    if any(entry.conflicted for entry in entries):
        raise GitError("Resolve Git conflicts before automatic backups can continue.")
    ensure_metadata_ignored(vault_root)
    _run(vault_root, ["add", "--all"])
    staged = _run(vault_root, ["diff", "--cached", "--quiet"], check=False)
    if staged.returncode == 0:
        return False
    if staged.returncode != 1:
        raise GitError("Could not inspect changes for automatic backup.")
    timestamp = (now or datetime.now().astimezone()).strftime("%Y-%m-%d %H:%M")
    _run(
        vault_root,
        [
            "commit",
            "-m",
            f"{AUTOMATIC_BACKUP_PREFIX}{timestamp}",
            "-m",
            "StillPoint-Backup: true",
        ],
        timeout=120,
    )
    return True


def publish_automatic_backup(root: str | Path, *, timeout: int = 15) -> bool:
    """Publish backups only when doing so requires no pull or merge."""
    vault_root = require_exact_repository(root)
    state = repository_state(vault_root)
    if not state.remote_url:
        raise GitError("No remote repository is configured; the backup remains local.")
    remote_name = state.remote_name or "origin"
    if state.has_upstream:
        _run(vault_root, ["fetch", remote_name], timeout=timeout)
        ahead, behind, _has_upstream = _ahead_behind(vault_root)
        if behind:
            raise GitError(
                "Remote updates are available. Use Get Git Updates before automatic backups can be published."
            )
        if not ahead:
            return False
        _run(vault_root, ["push", remote_name], timeout=timeout)
        return True
    if not state.branch:
        raise GitError("The current repository is not on a named branch.")
    _run(
        vault_root,
        ["push", "--set-upstream", remote_name, state.branch],
        timeout=timeout,
    )
    return True


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-.")
    return cleaned[:60] or "version"


def _unique_version_tag(root: Path, version_name: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    base = f"{VERSION_TAG_PREFIX}{timestamp}-{_slug(version_name)}"
    candidate = base
    counter = 2
    while _run(root, ["rev-parse", "--verify", f"refs/tags/{candidate}"], check=False).returncode == 0:
        candidate = f"{base}-{counter}"
        counter += 1
    return candidate


def save_version(root: str | Path, name: str, notes: str = "") -> tuple[str, bool]:
    vault_root = require_exact_repository(root)
    version_name = str(name or "").strip()
    if not version_name:
        raise GitError("Enter a version name.")
    committed = save_changes(vault_root, version_name)
    if not has_head(vault_root):
        raise GitError("The repository has no saved content to version.")
    tag = _unique_version_tag(vault_root, version_name)
    annotation = f"StillPoint-Version: {version_name}"
    cleaned_notes = str(notes or "").strip()
    if cleaned_notes:
        annotation += f"\n\n{cleaned_notes}"
    _run(vault_root, ["tag", "-a", tag, "-m", annotation])
    return tag, committed


def push(root: str | Path, *, include_versions: bool = False) -> None:
    vault_root = require_exact_repository(root)
    state = repository_state(vault_root)
    if not state.remote_url:
        raise GitError("No remote repository is configured.")
    arguments = ["push"]
    if not state.has_upstream:
        if not state.branch:
            raise GitError("The current repository is not on a named branch.")
        arguments.extend(["--set-upstream", state.remote_name or "origin", state.branch])
    _run(vault_root, arguments, timeout=180)
    if include_versions:
        tags = [
            line.strip()
            for line in _text(
                _run(vault_root, ["tag", "--list", f"{VERSION_TAG_PREFIX}*"])
            ).splitlines()
            if line.strip()
        ]
        if tags:
            _run(
                vault_root,
                ["push", state.remote_name or "origin", *[f"refs/tags/{tag}" for tag in tags]],
                timeout=180,
            )


def pull(root: str | Path) -> None:
    vault_root = require_exact_repository(root)
    if status(vault_root):
        raise GitError(
            "This vault has local changes. Use Save Changes to record them, or reset "
            "them, before getting updates. Git cannot combine remote updates with "
            "unsaved local changes."
        )
    state = repository_state(vault_root)
    if not state.remote_url:
        raise GitError("No remote repository is configured.")
    if not state.has_upstream:
        raise GitError("Publish this vault once before getting updates.")
    _run(vault_root, ["pull", "--no-rebase"], timeout=180)


def version_name_for_tag(root: Path, tag: str) -> str:
    result = _run(root, ["for-each-ref", "--format=%(contents)", f"refs/tags/{tag}"], check=False)
    if result.returncode:
        return tag.removeprefix(VERSION_TAG_PREFIX)
    first = _text(result).splitlines()[0] if _text(result) else ""
    prefix = "StillPoint-Version: "
    return first[len(prefix):].strip() if first.startswith(prefix) else tag.removeprefix(VERSION_TAG_PREFIX)


def _named_version_history(
    root: Path,
    relative_path: str,
    limit: int,
) -> list[GitHistoryEntry]:
    refs = _run(
        root,
        [
            "for-each-ref",
            "--sort=-creatordate",
            "--format=%(refname:short)%00",
            f"refs/tags/{VERSION_TAG_PREFIX}",
        ],
    )
    tags = [value.strip() for value in refs.stdout.decode("utf-8", errors="replace").split("\0") if value.strip()]
    entries: list[GitHistoryEntry] = []
    for tag in tags:
        if relative_path:
            exists = _run(root, ["cat-file", "-e", f"{tag}:{relative_path}"], check=False)
            if exists.returncode:
                continue
        details = _run(
            root,
            ["show", "-s", "--format=%H%x00%aI%x00%an%x00%s", f"{tag}^{{commit}}"],
        ).stdout.decode("utf-8", errors="replace").split("\0")
        if len(details) < 4:
            continue
        entries.append(
            GitHistoryEntry(
                details[0].strip(),
                details[1].strip(),
                details[2].strip(),
                details[3].strip(),
                version_name_for_tag(root, tag),
                tag,
            )
        )
    order_result = _run(root, ["rev-list", "--topo-order", "--all"], check=False)
    commit_order = {
        commit.strip(): index
        for index, commit in enumerate(_text(order_result).splitlines())
        if commit.strip()
    }
    entries.sort(key=lambda entry: (commit_order.get(entry.commit, 10**9), entry.version_tag or ""))
    return entries[:limit]


def history(
    root: str | Path,
    relative_path: str,
    *,
    versions_only: bool = True,
    include_automatic_backups: bool = False,
    limit: int = 200,
) -> list[GitHistoryEntry]:
    vault_root = require_exact_repository(root)
    cleaned_path = str(relative_path or "").strip().replace("\\", "/").strip("/")
    if versions_only:
        return _named_version_history(vault_root, cleaned_path, max(1, int(limit)))
    arguments = [
        "log",
        "--date=iso-strict",
        "--format=%H%x00%aI%x00%an%x00%s%x00",
        f"--max-count={max(1, int(limit))}",
    ]
    if cleaned_path:
        arguments.extend(["--follow", "--", cleaned_path])
    result = _run(vault_root, arguments)
    fields = result.stdout.decode("utf-8", errors="replace").split("\0")
    commits: list[GitHistoryEntry] = []
    for offset in range(0, len(fields) - 3, 4):
        commit = fields[offset].strip()
        if not commit:
            continue
        tags_result = _run(
            vault_root,
            ["tag", "--points-at", commit, "--list", f"{VERSION_TAG_PREFIX}*"],
            check=False,
        )
        tags = [line.strip() for line in _text(tags_result).splitlines() if line.strip()]
        if tags:
            for tag in tags:
                commits.append(
                    GitHistoryEntry(
                        commit,
                        fields[offset + 1].strip(),
                        fields[offset + 2].strip(),
                        fields[offset + 3].strip(),
                        version_name_for_tag(vault_root, tag),
                        tag,
                    )
                )
        else:
            subject = fields[offset + 3].strip()
            if include_automatic_backups or not subject.startswith(AUTOMATIC_BACKUP_PREFIX):
                commits.append(
                    GitHistoryEntry(
                        commit,
                        fields[offset + 1].strip(),
                        fields[offset + 2].strip(),
                        subject,
                    )
                )
    return commits


def diff_from_revision(root: str | Path, revision: str, relative_path: str) -> str:
    vault_root = require_exact_repository(root)
    result = _run(
        vault_root,
        ["diff", "--no-ext-diff", "--no-color", revision, "--", relative_path],
        check=False,
    )
    if result.returncode not in {0, 1}:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise GitError(detail or "Could not compare this version.")
    return result.stdout.decode("utf-8", errors="replace")


def working_diff(root: str | Path, relative_path: str) -> tuple[str, bool]:
    """Return a readable HEAD-to-working diff, including untracked new files."""
    vault_root = require_exact_repository(root)
    cleaned_path = str(relative_path or "").replace("\\", "/").strip("/")
    entry = next((item for item in status(vault_root) if item.path == cleaned_path), None)
    if entry is None:
        return "This file has no unsaved Git changes.", False
    if entry.index != "?":
        if not has_head(vault_root):
            revision = "--root"
        else:
            revision = "HEAD"
        if revision == "HEAD":
            rendered = diff_from_revision(vault_root, revision, cleaned_path)
            if "Binary files " in rendered and " differ" in rendered:
                deleted = "D" in {entry.index, entry.worktree}
                description = "Deleted binary file" if deleted else "Binary file changed"
                return f"{description}: {cleaned_path}\n", True
            return rendered, False
        # A repository without HEAD contains only new content. Handle it like
        # an untracked file instead of asking Git to diff an invalid revision.

    path = _safe_destination(vault_root, cleaned_path)
    if not path.is_file():
        return f"New file: {cleaned_path}\n", False
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise GitError(f"Could not read new file '{cleaned_path}': {exc}") from exc
    if b"\0" in data:
        return f"New binary file: {cleaned_path} ({len(data)} bytes)\n", True
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return f"New binary file: {cleaned_path} ({len(data)} bytes)\n", True
    lines = text.splitlines(keepends=True)
    diff = difflib.unified_diff(
        [],
        lines,
        fromfile="/dev/null",
        tofile=f"b/{cleaned_path}",
        lineterm="",
    )
    rendered = "\n".join(diff)
    return rendered or f"New empty file: {cleaned_path}\n", False


def restore_working_paths(root: str | Path, relative_paths: list[str]) -> list[str]:
    """Restore tracked working-copy paths to HEAD without touching untracked files."""
    vault_root = require_exact_repository(root)
    if not has_head(vault_root):
        raise GitError("This vault has no saved version to restore.")
    entries = {entry.path: entry for entry in status(vault_root)}
    paths: list[str] = []
    for value in relative_paths:
        cleaned = str(value or "").replace("\\", "/").strip("/")
        entry = entries.get(cleaned)
        if entry is None or entry.index == "?":
            continue
        _safe_destination(vault_root, cleaned)
        paths.append(cleaned)
        if entry.original_path:
            original = str(entry.original_path).replace("\\", "/").strip("/")
            _safe_destination(vault_root, original)
            paths.append(original)
    paths = list(dict.fromkeys(paths))
    if not paths:
        return []
    result = _run(
        vault_root,
        ["restore", "--source=HEAD", "--staged", "--worktree", "--", *paths],
        check=False,
    )
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise GitError(detail or "Could not restore the selected files.")
    return paths


def _safe_destination(root: Path, relative_path: str) -> Path:
    destination = (root / relative_path).resolve()
    try:
        destination.relative_to(root)
    except ValueError as exc:
        raise GitError("The selected path is outside the vault.") from exc
    return destination


def restore_path(root: str | Path, revision: str, relative_path: str) -> list[Path]:
    """Restore historical content without deleting newer working-tree files."""
    vault_root = require_exact_repository(root)
    cleaned_path = str(relative_path or "").replace("\\", "/").strip("/")
    if not cleaned_path or cleaned_path == ".stillpoint" or cleaned_path.startswith(".stillpoint/"):
        raise GitError("That path cannot be restored through version history.")
    listing = _run(
        vault_root,
        ["ls-tree", "-r", "--name-only", "-z", revision, "--", cleaned_path],
        check=False,
    )
    if listing.returncode:
        raise GitError("The selected path did not exist in that version.")
    paths = [
        value for value in listing.stdout.decode("utf-8", errors="surrogateescape").split("\0")
        if value and not (value == ".stillpoint" or value.startswith(".stillpoint/"))
    ]
    if not paths:
        blob = _run(vault_root, ["show", f"{revision}:{cleaned_path}"], check=False)
        if blob.returncode:
            raise GitError("The selected path did not exist in that version.")
        paths = [cleaned_path]
    restored: list[Path] = []
    for path in paths:
        blob = _run(vault_root, ["show", f"{revision}:{path}"])
        destination = _safe_destination(vault_root, path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as stream:
                stream.write(blob.stdout)
                temporary = Path(stream.name)
            temporary.replace(destination)
        except OSError as exc:
            raise GitError(f"Could not restore {path}: {exc}") from exc
        restored.append(destination)
    return restored
