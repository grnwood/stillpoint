"""Filesystem operations shared by local and remote attachment management."""
from pathlib import Path


def rename_attachment(root: Path, page_path: str, attachment_path: str, name: str) -> Path:
    """Rename a sibling attachment without replacing the page or another file."""
    if not name.strip() or name in {".", ".."} or any(c in name for c in '/\\\x00\r\n'):
        raise ValueError("Enter a filename without folders or line breaks.")
    root = root.resolve()
    page = (root / page_path.lstrip("/")).resolve()
    source = (root / attachment_path.lstrip("/")).resolve()
    if not page.is_relative_to(root) or not source.is_relative_to(root):
        raise ValueError("The attachment must be inside the vault.")
    if source == page or source.parent != page.parent or (
        source.exists() and page.exists() and source.samefile(page)
    ):
        raise ValueError("Select an attachment belonging to this page.")
    if not source.is_file():
        raise FileNotFoundError(f"Attachment not found: {source.name}")
    destination = source.with_name(name)
    if destination == page:
        raise ValueError("The attachment cannot replace the page file.")
    if destination == source:
        return source
    if destination.exists() or destination.is_symlink():
        case_only = name.casefold() == source.name.casefold() and destination.samefile(source)
        if not case_only:
            raise FileExistsError(f"A file named {name} already exists.")
    source.rename(destination)
    return destination
