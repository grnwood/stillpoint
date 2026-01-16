#!/usr/bin/env python3
from __future__ import annotations

import argparse
import textwrap
from pathlib import Path

PAGE_SUFFIX = ".md"

LOREM = [
    (
        "Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod tempor "
        "incididunt ut labore et dolore magna aliqua. Ut enim ad minim veniam, quis nostrud "
        "exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat."
    ),
    (
        "Duis aute irure dolor in reprehenderit in voluptate velit esse cillum dolore eu fugiat "
        "nulla pariatur. Excepteur sint occaecat cupidatat non proident, sunt in culpa qui "
        "officia deserunt mollit anim id est laborum."
    ),
    (
        "Curabitur pretium tincidunt lacus. Nulla gravida orci a odio. Nullam varius, turpis et "
        "commodo pharetra, est eros bibendum elit, nec luctus magna felis sollicitudin mauris."
    ),
    (
        "Integer in mauris eu nibh euismod gravida. Duis ac tellus et risus vulputate vehicula. "
        "Donec lobortis risus a elit. Etiam tempor."
    ),
]

TAG_POOL = [
    "alpha",
    "atlas",
    "beacon",
    "delta",
    "nexus",
    "orchard",
    "design",
    "ops",
    "review",
    "signal",
    "map",
    "flow",
    "links",
    "docs",
    "notes",
]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create a small StillPoint vault with deep links, tags, and lorem content."
    )
    parser.add_argument(
        "destination",
        nargs="?",
        default="./dev-assets/vault-links",
        help="Path where the vault should be created (default: vault-links)",
    )
    args = parser.parse_args()

    dest = Path(args.destination).resolve()
    dest.mkdir(parents=True, exist_ok=True)

    root_name = dest.name
    pages = _build_pages()
    pages.insert(0, [])  # root page

    colon_map = {tuple(parts): _colon_name(parts, root_name) for parts in pages}
    hierarchy = _build_hierarchy(pages)
    all_targets = [colon_map[tuple(parts)] for parts in pages]

    for idx, parts in enumerate(pages):
        title = root_name if not parts else parts[-1]
        key = tuple(parts)
        colon_path = colon_map[key]
        tags = _tags_for_index(idx)
        link_groups = _pick_links(key, hierarchy, colon_map, all_targets)
        content = _build_content(title, colon_path, tags, link_groups, idx)
        _write_page(dest, root_name, parts, content)

    print(f"Link-heavy sample vault created at: {dest}")


def _build_pages() -> list[list[str]]:
    pages: list[list[str]] = []
    pages.extend(
        [
            ["Alpha"],
            ["Alpha", "Research"],
            ["Alpha", "Research", "Notes"],
            ["Alpha", "Research", "Notes", "Summary"],
            ["Alpha", "Design"],
            ["Alpha", "Design", "Wireframes"],
            ["Atlas"],
            ["Atlas", "Routes"],
            ["Atlas", "Routes", "Ports"],
            ["Atlas", "Routes", "Ports", "Checklist"],
            ["Atlas", "Maps"],
            ["Beacon"],
            ["Beacon", "Signals"],
            ["Beacon", "Signals", "Radio"],
            ["Beacon", "Signals", "Radio", "Archive"],
            ["Beacon", "Stories"],
            ["Delta"],
            ["Delta", "Streams"],
            ["Delta", "Streams", "Sources"],
            ["Delta", "Streams", "Sources", "Index"],
            ["Delta", "Reviews"],
            ["Nexus"],
            ["Nexus", "Threads"],
            ["Nexus", "Threads", "Incidents"],
            ["Nexus", "Threads", "Incidents", "Timeline"],
            ["Nexus", "Boards"],
            ["Orchard"],
            ["Orchard", "Harvest"],
            ["Orchard", "Harvest", "Logs"],
            ["Orchard", "Harvest", "Logs", "Week01"],
            ["Orchard", "Plots"],
        ]
    )
    return pages


def _colon_name(parts: list[str], root_name: str) -> str:
    if not parts:
        return root_name
    return ":".join(parts)


def _build_hierarchy(pages: list[list[str]]) -> dict[tuple[str, ...], dict[str, list[tuple[str, ...]] | tuple[str, ...] | None]]:
    info: dict[tuple[str, ...], dict[str, list[tuple[str, ...]] | tuple[str, ...] | None]] = {}
    for parts in pages:
        key = tuple(parts)
        parent = tuple(parts[:-1]) if parts else None
        info[key] = {"parent": parent, "children": []}
    for key, meta in info.items():
        parent = meta["parent"]
        if parent is not None and parent in info:
            info[parent]["children"].append(key)
    return info


def _pick_links(
    current_key: tuple[str, ...],
    hierarchy: dict[tuple[str, ...], dict[str, list[tuple[str, ...]] | tuple[str, ...] | None]],
    colon_map: dict[tuple[str, ...], str],
    all_targets: list[str],
) -> dict[str, list[str]]:
    groups = {"parent": [], "children": [], "cross": []}
    meta = hierarchy[current_key]
    parent = meta["parent"]
    if parent is not None:
        groups["parent"].append(colon_map[parent])
    children = list(meta["children"])
    for child in children[:3]:
        groups["children"].append(colon_map[child])

    current_path = colon_map[current_key]
    if not all_targets:
        return groups
    idx = all_targets.index(current_path) if current_path in all_targets else 0
    cross_candidates = [
        all_targets[(idx + 3) % len(all_targets)],
        all_targets[(idx + 7) % len(all_targets)],
    ]
    for target in cross_candidates:
        if target not in groups["parent"] and target not in groups["children"] and target != current_path:
            groups["cross"].append(target)
    return groups


def _tags_for_index(index: int) -> list[str]:
    count = 3 + (index % 2)
    tags = []
    for offset in range(count):
        tags.append(TAG_POOL[(index + offset) % len(TAG_POOL)])
    return tags


def _format_tags(tags: list[str]) -> str:
    return " ".join(f"@{tag}" for tag in tags)


def _build_content(
    title: str,
    colon_path: str,
    tags: list[str],
    link_groups: dict[str, list[str]],
    index: int,
) -> str:
    lines = [
        f"# {title}",
        "",
        f"Path: :{colon_path}",
        f"Tags: {_format_tags(tags)}",
        "",
        "Links:",
    ]
    for label in ("parent", "children", "cross"):
        targets = link_groups.get(label, [])
        if not targets:
            continue
        lines.append(f"- {label.capitalize()}: " + ", ".join(f":{t}" for t in targets))

    paragraph_count = 2 + (index % 2)
    for idx in range(paragraph_count):
        lorem = LOREM[(index + idx) % len(LOREM)]
        wrapped = textwrap.fill(lorem, width=86)
        lines.extend(["", wrapped, f"Related tags: {_format_tags(tags[:2])}"])

    return "\n".join(lines) + "\n"


def _write_page(root: Path, root_name: str, parts: list[str], content: str) -> None:
    if not parts:
        folder = root / root_name
        folder.mkdir(parents=True, exist_ok=True)
        file_path = folder / f"{root_name}{PAGE_SUFFIX}"
        file_path.write_text(content, encoding="utf-8")
        return

    folder = root.joinpath(*parts)
    folder.mkdir(parents=True, exist_ok=True)
    file_path = folder / f"{parts[-1]}{PAGE_SUFFIX}"
    file_path.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    main()
