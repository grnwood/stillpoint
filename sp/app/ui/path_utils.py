"""Utilities for converting between filesystem paths and colon notation."""
from __future__ import annotations

from pathlib import Path
from sp.server.adapters.files import PAGE_SUFFIX, strip_page_suffix
from sp.logging_flags import log_enabled


def trace_link_decision(location: str, **values: object) -> None:
    """Emit verbose link-decision tracing to stdout for debugging."""
    if not log_enabled("link_debug"):
        return
    details = " ".join(f"{key}={value!r}" for key, value in values.items())
    print(f"[LINK_DEBUG] {location} {details}".rstrip())


def strip_root_prefix(colon_path: str) -> str:
    """Remove a leading ':' that denotes a root-relative colon path."""
    if not colon_path:
        return ""
    return colon_path.lstrip(":").strip()


def ensure_root_colon_link(link: str) -> str:
    """Ensure a colon link is explicitly marked as root-relative with a leading ':'.

    This only affects the page portion (before any #anchor). Existing content that
    already includes ':' separators keeps working, but we now emit ':Page' so single
    pages are no longer mistaken for CamelCase relatives.
    """
    text = (link or "").strip()
    if not text or text.startswith("#"):
        return text  # Pure anchors or empty strings stay untouched

    anchor = None
    base = text
    if "#" in text:
        base, anchor = text.split("#", 1)
    base = (base or "").strip()
    if not base:
        return f"#{anchor}" if anchor else ""
    normalized = f":{base.lstrip(':')}"
    result = f"{normalized}#{anchor}" if anchor else normalized
    trace_link_decision(
        "sp/app/ui/path_utils.py:ensure_root_colon_link",
        input=link,
        base=base,
        anchor=anchor,
        result=result,
    )
    return result


def normalize_link_target(link: str) -> str:
    """Normalize link target by replacing spaces with underscores.

    Each colon-separated component is normalized independently. Anchors (after #)
    are preserved as-is. Case is preserved.
    """
    if not link:
        return ""
    text = link.strip()
    anchor = ""
    if "#" in text:
        base, anchor = text.split("#", 1)
    else:
        base = text
    has_root = base.startswith(":")
    cleaned_base = base.lstrip(":")
    parts = []
    for part in cleaned_base.split(":"):
        stripped = part.strip()
        if not stripped:
            continue
        underscored = "_".join(stripped.split())
        parts.append(underscored)
    normalized = ":".join(parts)
    if has_root and normalized:
        normalized = f":{normalized}"
    result = normalized
    if anchor:
        result = f"{result}#{anchor.strip()}"
    trace_link_decision(
        "sp/app/ui/path_utils.py:normalize_link_target",
        input=link,
        base=base,
        anchor=anchor,
        result=result,
    )
    return result


def should_use_full_target_label(target: str, label: str | None) -> bool:
    """Return True when an anchored internal target should keep its full target as label.

    This guards insert/edit flows where a journal-day auto label like ``23`` leaks back in
    even though the user did not provide a custom label for an anchored link target.
    """
    normalized_target = ensure_root_colon_link(normalize_link_target(target or ""))
    if "#" not in normalized_target:
        trace_link_decision(
            "sp/app/ui/path_utils.py:should_use_full_target_label",
            target=target,
            label=label,
            normalized_target=normalized_target,
            result=False,
            reason="no_anchor",
        )
        return False
    clean_label = (label or "").strip()
    if not clean_label:
        trace_link_decision(
            "sp/app/ui/path_utils.py:should_use_full_target_label",
            target=target,
            label=label,
            normalized_target=normalized_target,
            result=True,
            reason="empty_label",
        )
        return True
    if clean_label == normalized_target:
        trace_link_decision(
            "sp/app/ui/path_utils.py:should_use_full_target_label",
            target=target,
            label=label,
            normalized_target=normalized_target,
            result=True,
            reason="full_target_match",
        )
        return True
    base = normalized_target.split("#", 1)[0]
    leaf = base.lstrip(":").split(":")[-1] if base else ""
    result = clean_label == leaf
    trace_link_decision(
        "sp/app/ui/path_utils.py:should_use_full_target_label",
        target=target,
        label=label,
        normalized_target=normalized_target,
        leaf=leaf,
        result=result,
        reason="leaf_match_check",
    )
    return result


def path_to_colon(file_path: str) -> str:
    """Convert a filesystem path like /PageA/PageB/PageC/PageC.md to PageA:PageB:PageC.
    
    The structure is: Each page lives in a folder with the same name.
    /JoeBob/JoeBob2/JoeBob2.md should display as JoeBob:JoeBob2
    
    Args:
        file_path: Vault-relative path starting with / (e.g., "/PageA/PageB/PageC/PageC.md")
        
    Returns:
        Colon-separated page hierarchy (e.g., "PageA:PageB:PageC")
    """
    # Strip whitespace first, then split off any anchor.
    cleaned = (file_path or "").strip()
    anchor = ""
    if "#" in cleaned:
        cleaned, anchor = cleaned.split("#", 1)
    cleaned = cleaned.strip().strip("/")
    if not cleaned:
        return f"#{anchor}" if anchor else ""
    
    parts = cleaned.split("/")
    # Remove page suffix from last part if present
    if parts:
        parts[-1] = strip_page_suffix(parts[-1])
    
    # The filesystem structure is /Folder1/Folder2/Folder2.md
    # The last part (file name) matches the second-to-last (folder name)
    # We want to display this as Folder1:Folder2, not Folder1:Folder2:Folder2
    if len(parts) >= 2 and parts[-1] == parts[-2]:
        # Remove the duplicate file name
        parts = parts[:-1]
    
    # Convert spaces to underscores in each part for consistent link format
    parts = [part.replace(" ", "_") for part in parts]
    
    result = ":".join(parts)
    if anchor:
        return f"{result}#{anchor}"
    return result


def colon_to_path(colon_path: str, vault_root_name: str = "") -> str:
    """Convert colon notation like PageA:PageB:PageC to filesystem path.
    
    The structure is: Each page lives in a folder with the same name.
    PageA:PageB:PageC becomes /PageA/PageB/PageC/PageC.md
    Underscores in colon notation are converted to spaces to match actual folder names.
    
    Args:
        colon_path: Colon-separated page hierarchy (e.g., "PageA:PageB:PageC")
        vault_root_name: Name of the vault root (optional, for handling root page)
        
    Returns:
        Vault-relative filesystem path (e.g., "/PageA/PageB/PageC/PageC.md")
    """
    cleaned = (colon_path or "").strip()
    if "#" in cleaned:
        cleaned = cleaned.split("#", 1)[0]
    cleaned = strip_root_prefix(cleaned)
    if not cleaned:
        if vault_root_name:
            return f"/{vault_root_name}{PAGE_SUFFIX}"
        return "/"
    
    parts = cleaned.split(":")
    # Convert underscores to spaces in each part to match actual folder/file names
    parts = [part.replace("_", " ") for part in parts]
    # Each page lives in a folder with the same name
    # Final path is /Part1/Part2/.../PartN/PartN.md
    folder_path = "/".join(parts)
    file_name = f"{parts[-1]}{PAGE_SUFFIX}"
    return f"/{folder_path}/{file_name}"


def colon_to_folder_path(colon_path: str) -> str:
    """Convert colon notation to folder path (without the .md file).
    
    Underscores in colon notation are converted to spaces to match actual folder names.
    
    Args:
        colon_path: Colon-separated page hierarchy (e.g., "PageA:PageB:PageC")
        
    Returns:
        Folder path (e.g., "/PageA/PageB/PageC")
    """
    cleaned = (colon_path or "").strip()
    if "#" in cleaned:
        cleaned = cleaned.split("#", 1)[0]
    cleaned = strip_root_prefix(cleaned)
    if not cleaned:
        return "/"
    
    parts = cleaned.split(":")
    # Convert underscores to spaces in each part to match actual folder names
    parts = [part.replace("_", " ") for part in parts]
    return "/" + "/".join(parts)
