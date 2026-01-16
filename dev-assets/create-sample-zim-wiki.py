#!/usr/bin/env python3
from __future__ import annotations

import argparse
import random
import shutil
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable, List

from faker import Faker

FAKER = Faker()
PAGE_SUFFIX = ".txt"
ZIM_HEADER = [
    "Content-Type: text/x-zim-wiki",
    "Wiki-Format: zim 0.6",
    "Creation-Date: {created}",
    "",
]


@dataclass
class PageSpec:
    name: str  # e.g., "Home" or "Projects/Alpha"
    attachments: int = 2
    plus_children: list[str] | None = None


def zim_heading(level: int, text: str) -> str:
    """Return a zim heading line (H1 uses 6 '=', H5 uses 2)."""
    marks = 7 - max(1, min(level, 5))
    return f"{'=' * marks}  {text}  {'=' * marks}"


def format_task(summary: str, done: bool, due: date | None) -> str:
    state = "x" if done else " "
    due_part = f" >{due.isoformat()}" if due else ""
    return f"[{state}] {summary}{due_part}"


def sample_paragraphs(count: int) -> List[str]:
    paras: List[str] = []
    for _ in range(count):
        blob = FAKER.paragraph(nb_sentences=4)
        blob = blob.replace("Lorem", "**Lorem**")
        blob = blob.replace("ipsum", "//ipsum//")
        paras.append(blob)
    return paras


def build_page_content(
    title: str,
    page_links: Iterable[str],
    attachment_links: Iterable[str],
    plus_links: Iterable[str],
) -> str:
    today = date.today()
    lines: List[str] = []
    lines.extend(
        [
            zim_heading(1, title),
            f"{zim_heading(2, 'Overview')}",
            "This page mixes zim formatting, inline code like ''code_sample'', and links.",
            "",
        ]
    )
    lines.append(zim_heading(3, "Tasks"))
    tasks = [
        format_task(f"Draft overview for {title}", done=False, due=today + timedelta(days=2)),
        format_task(f"Review links in {title}", done=True, due=today - timedelta(days=1)),
        format_task("Sync attachments", done=False, due=today + timedelta(days=5)),
        format_task("Stretch idea", done=False, due=None),
    ]
    for task in tasks:
        lines.append(task)
    lines.append("")

    lines.append(zim_heading(3, "Body"))
    paras = sample_paragraphs(3)
    for para in paras:
        lines.append(para)
        lines.append("")

    lines.append(zim_heading(3, "Links & Attachments"))
    for link in page_links:
        lines.append(f"See also [[{link}|related page]].")
    for att in attachment_links:
        lines.append(f"Attached file: [[./{att}|{att.split('/')[-1]}]]")
    for child in plus_links:
        lines.append(f"Child page link: +{child}")
    lines.append("")

    if attachment_links:
        lines.append(zim_heading(4, "Inline Media"))
        first_att = attachment_links[0]
        # Zim-style inline image syntax
        lines.append(f"Embedded inline image: {{{{{./{first_att}}}}}")
        lines.append("")

    lines.append(zim_heading(4, "Formatting"))
    lines.append("**Bold**, //italic//, ~~strikethrough~~, and ''fixed width'' text.")
    lines.append("Nested emphasis: //**bold italic**// demonstrates combined styles.")
    lines.append("")

    return "\n".join(lines).strip() + "\n"


def write_attachment(file_path: Path) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_bytes(b"\x89PNG\r\n\x1a\nsample")


def write_page(root: Path, spec: PageSpec, links: Iterable[str]) -> None:
    parts = spec.name.split("/")
    stem = parts[-1]
    page_dir = root.joinpath(*parts[:-1]) if len(parts) > 1 else root
    page_dir.mkdir(parents=True, exist_ok=True)
    page_file = page_dir / f"{stem}{PAGE_SUFFIX}"

    # Attachments live alongside the page in a folder named after the stem
    attachment_dir = page_dir / stem
    attachment_links: List[str] = []
    for idx in range(1, spec.attachments + 1):
        att_name = f"{stem.lower()}_attachment_{idx}.png"
        full_path = attachment_dir / att_name
        write_attachment(full_path)
        rel_path = full_path.relative_to(page_dir).as_posix()
        attachment_links.append(rel_path)

    plus_links = spec.plus_children or []
    content = build_page_content(stem, links, attachment_links, plus_links)
    header = [line.format(created=FAKER.date_time().isoformat()) for line in ZIM_HEADER]
    payload = "\n".join(header) + content
    page_file.write_text(payload, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a sample Zim wiki folder for import testing.")
    parser.add_argument(
        "destination",
        nargs="?",
        default="./sample-zim-wiki",
        help="Output folder for the zim wiki (default: dev-assets/sample-zim-wiki)",
    )
    parser.add_argument("--force", action="store_true", help="Overwrite destination if it exists")
    args = parser.parse_args()

    dest = Path(args.destination).resolve()
    if dest.exists():
        if not args.force:
            raise SystemExit(f"{dest} already exists. Use --force to overwrite.")
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)

    random.seed(1234)
    FAKER.seed_instance(1234)

    specs = [
        PageSpec("Home", attachments=2, plus_children=["Journal"]),
        PageSpec("Home/Journal", attachments=1),
        PageSpec("Wiki", attachments=3, plus_children=["Tasks"]),
        PageSpec("Wiki/Tasks", attachments=2),
        PageSpec("Projects", attachments=2, plus_children=["Alpha", "Beta"]),
        PageSpec("Projects/Alpha", attachments=3),
        PageSpec("Projects/Beta", attachments=2),
    ]

    link_targets = [spec.name for spec in specs]
    for spec in specs:
        others = [name for name in link_targets if name != spec.name]
        random.shuffle(others)
        # Use zim-style links relative to names (no suffix)
        links = [o.replace("/", ":") for o in others[:3]]
        write_page(dest, spec, links)

    print(f"Sample zim wiki created at: {dest}")


if __name__ == "__main__":
    main()
