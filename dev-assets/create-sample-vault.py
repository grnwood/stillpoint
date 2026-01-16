#!/usr/bin/env python3
from __future__ import annotations

import argparse
import calendar
import random
import textwrap
from pathlib import Path
from datetime import date, timedelta

from faker import Faker

PAGE_SUFFIX = ".md"
SECTION_DEFS = [
    ("Overview", "overview"),
    ("Ideas", "ideas"),
    ("Tasks", "tasks"),
    ("Workflow", "workflow"),
    ("Graph", "graph"),
    ("API", "api"),
    ("Performance", "perf"),
    ("FAQ", "faq"),
]

FAKER = Faker()
TAG_POOL: list[str] = []
_TAG_CURSOR = 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create a sample StillPoint vault with deep nesting, links, and tasks."
    )
    parser.add_argument(
        "destination",
        nargs="?",
        default="./vault-sample",
        help="Path where the vault should be created (default: vault-sample)",
    )
    args = parser.parse_args()

    dest = Path(args.destination).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    random.seed(42)
    FAKER.seed_instance(42)

    global TAG_POOL
    TAG_POOL = _build_tag_pool()

    root_name = dest.name
    pages: list[list[str]] = []

    # Include the vault root page
    pages.append([])

    # One very deep chain (10 levels)
    deep_root = "DeepDive"
    deep_chain = [deep_root] + [f"Layer{idx:02d}" for idx in range(1, 11)]
    pages.extend(_build_chain(deep_chain))

    # Nine other root pages with random depth 1-5
    root_topics = [
        "Overview",
        "Architecture",
        "Features",
        "Roadmap",
        "Workflows",
        "Testing",
        "Integrations",
        "Performance",
        "Ideas",
    ]
    for topic in root_topics:
        depth = random.randint(1, 5)
        chain = [topic] + [f"{topic}Sub{idx}" for idx in range(1, depth + 1)]
        pages.extend(_build_chain(chain))

    # Precompute colon names and hierarchy info for linking
    colon_map = {tuple(parts): _colon_name(parts, root_name) for parts in pages}
    hierarchy = _build_hierarchy(pages)
    all_colon_targets = list(colon_map.values())

    for parts in pages:
        title = root_name if not parts else parts[-1]
        key = tuple(parts)
        colon_path = colon_map[key]
        link_groups, ordered_links = _pick_links(
            key,
            hierarchy,
            colon_map,
            all_colon_targets,
            desired_total=8,
        )
        tasks = _sample_tasks()
        paragraphs = random.randint(3, 6)
        long_bonus = 0
        if len(parts) == 0:
            long_bonus = 2
        elif parts[:1] == [deep_root] and len(parts) > 5:
            long_bonus = 2
        content = _build_content(
            title,
            colon_path,
            link_groups,
            ordered_links,
            tasks,
            paragraphs + long_bonus,
        )
        _write_page(dest, root_name, parts, content)

    _generate_journal_calendar(dest, [t for t in all_colon_targets if not t.startswith("Journal")])

    print(f"Sample vault created at: {dest}")


def _build_chain(names: list[str]) -> list[list[str]]:
    """Return a list of path parts for every level in a chain."""
    path_parts: list[list[str]] = []
    current: list[str] = []
    for name in names:
        current.append(name)
        path_parts.append(current.copy())
    return path_parts


def _colon_name(parts: list[str], root_name: str) -> str:
    if not parts:
        return root_name
    return ":".join(parts)


def _build_hierarchy(pages: list[list[str]]) -> dict[tuple[str, ...], dict[str, list[tuple[str, ...]] | tuple[str, ...] | None]]:
    """Build parent/child relationships for every page."""
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
    desired_total: int = 6,
) -> tuple[dict[str, list[str]], list[str]]:
    groups = {"parent": [], "children": [], "siblings": [], "cross": []}
    current_meta = hierarchy[current_key]
    parent = current_meta["parent"]
    if parent is not None:
        groups["parent"].append(colon_map[parent])

    children = list(current_meta["children"])
    random.shuffle(children)
    groups["children"].extend(colon_map[ch] for ch in children[:3])

    if parent is not None and parent in hierarchy:
        siblings = [child for child in hierarchy[parent]["children"] if child != current_key]
        random.shuffle(siblings)
        groups["siblings"].extend(colon_map[sib] for sib in siblings[:3])

    seen: set[str] = {colon_map[current_key]}
    for label in ("parent", "children", "siblings"):
        seen.update(groups[label])

    # Cross-level picks: mix of deep nodes and random entries
    candidates = [key for key in hierarchy.keys() if key not in {current_key, parent}]
    random.shuffle(candidates)
    for key in candidates:
        colon = colon_map[key]
        if colon in seen:
            continue
        groups["cross"].append(colon)
        seen.add(colon)
        if len(groups["cross"]) >= max(4, desired_total // 2):
            break

    flat_links: list[str] = []
    flattened_order = ("parent", "children", "siblings", "cross")
    seen_flat: set[str] = set()
    for label in flattened_order:
        for link in groups[label]:
            if link in seen_flat:
                continue
            flat_links.append(link)
            seen_flat.add(link)

    extras = [t for t in all_targets if t not in seen_flat and t != colon_map[current_key]]
    random.shuffle(extras)
    for link in extras:
        flat_links.append(link)
        groups["cross"].append(link)
        seen_flat.add(link)
        if len(flat_links) >= desired_total:
            break

    return groups, flat_links


def _sample_tasks(reference_date: date | None = None) -> list[str]:
    """Generate a handful of tasks using a shared tag pool for even distribution."""
    global _TAG_CURSOR
    today = reference_date or date.today()
    due_dates = [
        today - timedelta(days=7),
        today - timedelta(days=1),
        today + timedelta(days=2),
        today + timedelta(days=10),
        today + timedelta(days=45),
    ]
    start_dates = [
        today - timedelta(days=3),
        today + timedelta(days=1),
        today + timedelta(days=5),
        today + timedelta(days=14),
    ]
    templates = [(_generate_task_text(), random.randint(0, 3)) for _ in range(6)]
    tasks: list[str] = []
    for text, priority in templates:
        tag_count = random.randint(1, 3)
        tags: list[str] = []
        while len(tags) < tag_count and TAG_POOL:
            tag = TAG_POOL[_TAG_CURSOR % len(TAG_POOL)]
            _TAG_CURSOR += 1
            if tag not in tags:
                tags.append(tag)
        tag_str = " ".join(f"@{tag}" for tag in tags) if tags else "@general"
        due = random.choice(due_dates)
        start = None
        include_due = True
        include_start = False
        include_due = bool(random.choice([True, False]))
        include_start = not include_due
        if include_start:
            start = random.choice(start_dates)
        state = " " if random.random() > 0.35 else "x"
        bangs = "!" * max(0, min(priority, 3))
        due_part = f" <{due.isoformat()}" if include_due else ""
        start_part = f" >{start.isoformat()}" if include_start and start else ""
        tasks.append(f"- [{'x' if state == 'x' else ' '}] {text} {bangs} {tag_str}{due_part}{start_part}")
    return tasks


def _sample_tags(min_count: int = 2, max_count: int = 5) -> list[str]:
    """Return a small list of tags for page content."""
    global _TAG_CURSOR
    if not TAG_POOL:
        return []
    count = random.randint(min_count, max_count)
    tags: list[str] = []
    while len(tags) < count and TAG_POOL:
        tag = TAG_POOL[_TAG_CURSOR % len(TAG_POOL)]
        _TAG_CURSOR += 1
        if tag not in tags:
            tags.append(tag)
    return tags


def _format_tags(tags: list[str]) -> str:
    return " ".join(f"@{tag}" for tag in tags)


def _generate_task_text() -> str:
    buzz = "-".join(FAKER.words(nb=random.randint(2, 4)))
    action = FAKER.bs()
    color = FAKER.color_name()
    cipher = FAKER.lexify(text="??-??")
    phrase = FAKER.catch_phrase()
    mashup = f"{buzz} {action} {color} {cipher} {phrase}"
    return mashup.strip().capitalize()


def _build_tag_pool(min_tags: int = 30, max_tags: int = 40) -> list[str]:
    """Build a shared pool of tags to keep distribution uniform."""
    count = random.randint(min_tags, max_tags)
    FAKER.unique.clear()
    tags: list[str] = []
    for _ in range(count * 2):  # over-generate to ensure uniqueness
        try:
            word = FAKER.unique.word().lower()
        except Exception:
            word = FAKER.word().lower()
        if word not in tags:
            tags.append(word)
        if len(tags) >= count:
            break
    while len(tags) < count:
        tags.append(f"tag{len(tags)+1}")
    random.shuffle(tags)
    return tags[:count]


def _build_content(
    title: str,
    colon_path: str,
    link_groups: dict[str, list[str]],
    ordered_links: list[str],
    tasks: list[str],
    paragraphs: int,
) -> str:
    anchor_slugs = [slug for _, slug in SECTION_DEFS]
    section_labels = {slug: label for label, slug in SECTION_DEFS}
    page_tags = _sample_tags(3, 6)
    rich_links: list[str] = []
    for target in ordered_links:
        rich_links.append(target)
        samples = random.sample(anchor_slugs, k=random.randint(1, min(3, len(anchor_slugs))))
        for slug in samples:
            rich_links.append(f"{target}#{slug}")
    # Ensure uniqueness but keep order
    seen = set()
    dedup_links: list[str] = []
    for link in rich_links:
        if link in seen:
            continue
        seen.add(link)
        dedup_links.append(link)

    inline_refs = []
    if len(dedup_links) >= 2:
        slug_a = random.choice(anchor_slugs)
        inline_refs.append(
            f"See also :{dedup_links[0]}#{slug_a} for background and related context."
        )
    if len(dedup_links) >= 3:
        slug_b = random.choice(anchor_slugs)
        inline_refs.append(f"Jump to :{dedup_links[2]}#{slug_b} for the task breakdown.")

    group_summaries: list[str] = []
    label_map = {
        "parent": "Parent",
        "children": "Children",
        "siblings": "Siblings",
        "cross": "Cross-level",
    }
    for key in ("parent", "children", "siblings", "cross"):
        entries = link_groups.get(key, [])
        if not entries:
            continue
        refs = ", ".join(f":{entry}" for entry in entries[:5])
        group_summaries.append(f"- **{label_map[key]}:** {refs}")

    anchor_examples: list[str] = []
    for link in ordered_links[: min(6, len(ordered_links))]:
        slug = random.choice(anchor_slugs)
        anchor_examples.append(
            f"- Deep link to :{link}#{slug} for {section_labels[slug].lower()} insights."
        )

    intro = [
        f"# {title}",
        "",
        f"This page lives at :{colon_path} in the StillPoint vault.",
        "It describes features, workflows, and ideas for StillPoint while acting as link data.",
        f"Tags: {_format_tags(page_tags) if page_tags else '@general'}",
        "",
        "Links to explore:",
        *[f"- [:{link}|{link}]" for link in dedup_links],
        "",
        "Relationship map:",
        *group_summaries,
        "",
        "Anchor quick links:",
        *anchor_examples,
        "",
        "Tasks:",
        *tasks,
        "",
        "Notes roadmap includes:",
        *[f"- {label}" for label, _ in SECTION_DEFS],
        "",
        *inline_refs,
    ]
    section_counts = {slug: 1 for _, slug in SECTION_DEFS}
    extra = max(0, paragraphs - len(SECTION_DEFS))
    for _ in range(extra):
        slug = random.choice(anchor_slugs)
        section_counts[slug] += 1

    body_sections: list[str] = []
    paragraph_counter = 0
    for label, slug in SECTION_DEFS:
        count = section_counts[slug]
        paragraphs_for_section = []
        for idx in range(count):
            extra_note = None
            if (paragraph_counter + idx) % 2 == 0 and ordered_links:
                target = random.choice(ordered_links)
                extra_note = (
                    f"Cross-reference :{target}#{slug} for aligned {label.lower()} notes."
                )
            tag_note = None
            if random.random() < 0.55:
                tag_note = f"Related tags: {_format_tags(_sample_tags(2, 4))}"
            level = ((paragraph_counter + idx) % 5) + 1
            paragraphs_for_section.append(_headered_paragraph(level, extra_note, tag_note))
        paragraph_counter += count
        section_body = "\n\n".join(paragraphs_for_section)
        body_sections.append(f"## {label}\n\n{section_body}")

    body = "\n\n".join(body_sections)
    return "\n".join(intro) + "\n\n" + body + "\n"


def _headered_paragraph(
    header_level: int,
    extra_note: str | None = None,
    tag_note: str | None = None,
) -> str:
    level = max(1, min(5, header_level))
    heading_words = " ".join(word.capitalize() for word in FAKER.words(nb=random.randint(2, 4)))
    heading = f"{'#' * level} {heading_words}"
    sentences = " ".join(FAKER.sentences(nb=random.randint(5, 9)))
    body = textwrap.fill(sentences, width=86)
    if extra_note:
        body = f"{body}\n\n{extra_note}"
    if tag_note:
        body = f"{body}\n\n{tag_note}"
    return f"{heading}\n\n{body}"


def _write_page(root: Path, root_name: str, parts: list[str], content: str) -> None:
    if not parts:
        # Root page lives in a folder with the same name: /vault-sample/vault-sample.txt
        folder = root / root_name
        folder.mkdir(parents=True, exist_ok=True)
        file_path = folder / f"{root_name}{PAGE_SUFFIX}"
        file_path.write_text(content, encoding="utf-8")
        return

    # All pages follow the pattern: /Folder/Folder.txt or /Parent/Child/Child.txt
    folder = root.joinpath(*parts)
    folder.mkdir(parents=True, exist_ok=True)
    file_path = folder / f"{parts[-1]}{PAGE_SUFFIX}"
    file_path.write_text(content, encoding="utf-8")


def _generate_journal_calendar(root: Path, cross_targets: list[str]) -> None:
    today = date.today()
    offsets = (-1, 0, 1)
    months = [_shift_month(today, offset) for offset in offsets]
    journal_root = root / "Journal"
    journal_root.mkdir(parents=True, exist_ok=True)

    grouped: dict[int, list[int]] = {}
    for year, month in months:
        grouped.setdefault(year, []).append(month)

    for year in sorted(grouped):
        year_dir = journal_root / f"{year:04d}"
        year_dir.mkdir(parents=True, exist_ok=True)
        month_details: list[tuple[int, list[date]]] = []
        for month in sorted(set(grouped[year])):
            day_dates, day_sub_links = _write_journal_month(year_dir, year, month, cross_targets)
            month_details.append((month, day_dates))
        year_page = year_dir / f"{year:04d}{PAGE_SUFFIX}"
        year_page.write_text(
            _build_journal_year_content(year, month_details, cross_targets),
            encoding="utf-8",
        )


def _shift_month(base: date, offset: int) -> tuple[int, int]:
    month = base.month + offset
    year = base.year
    while month < 1:
        month += 12
        year -= 1
    while month > 12:
        month -= 12
        year += 1
    return year, month


def _write_journal_month(
    year_dir: Path, year: int, month: int, cross_targets: list[str]
) -> tuple[list[date], dict[date, list[str]]]:
    month_dir = year_dir / f"{month:02d}"
    month_dir.mkdir(parents=True, exist_ok=True)
    day_count = calendar.monthrange(year, month)[1]
    day_dates: list[date] = []
    day_sub_links: dict[date, list[str]] = {}
    for day in range(1, day_count + 1):
        entry_date = date(year, month, day)
        day_dir = month_dir / f"{day:02d}"
        day_dir.mkdir(parents=True, exist_ok=True)
        sub_links = _write_journal_day_subpages(day_dir, entry_date, cross_targets)
        day_page = day_dir / f"{day:02d}{PAGE_SUFFIX}"
        day_page.write_text(
            _build_journal_day_content(entry_date, cross_targets, sub_links), encoding="utf-8"
        )
        day_sub_links[entry_date] = sub_links
        day_dates.append(entry_date)
    month_page = month_dir / f"{month:02d}{PAGE_SUFFIX}"
    month_page.write_text(
        _build_journal_month_content(year, month, day_dates, cross_targets, day_sub_links),
        encoding="utf-8",
    )
    return day_dates, day_sub_links


def _build_journal_year_content(
    year: int, month_details: list[tuple[int, list[date]]], cross_targets: list[str]
) -> str:
    colon_path = f"Journal:{year:04d}:{year:04d}"
    year_tags = _sample_tags(2, 4)
    cross_refs = _sample_cross_links(cross_targets, 5)
    lines = [
        f"# {year} Journal Overview",
        "",
        f"Annual tracker anchored at :{colon_path}.",
        f"Tags: {_format_tags(year_tags) if year_tags else '@journal'}",
        "Covered months:",
    ]
    for month, day_dates in month_details:
        name = date(year, month, 1).strftime("%B")
        lines.append(
            f"- {name}: :Journal:{year:04d}:{month:02d}:{month:02d} ({len(day_dates)} entries)"
        )
    if cross_refs:
        lines.extend(
            [
                "",
                "Project references:",
                *[f"- :{ref}" for ref in cross_refs],
            ]
        )
    lines.extend(
        [
            "",
            "## Themes",
            "",
            textwrap.fill(
                " ".join(FAKER.sentences(nb=random.randint(5, 7))),
                width=86,
            ),
        ]
    )
    if month_details:
        focus_month = month_details[0][0]
        lines.extend(
            [
                "",
                _headered_paragraph(
                    3,
                    f"Cross-check :Journal:{year:04d}:{focus_month:02d}:{focus_month:02d} for kickoff context.",
                ),
            ]
        )
    return "\n".join(lines).strip() + "\n"


def _build_journal_month_content(
    year: int,
    month: int,
    day_dates: list[date],
    cross_targets: list[str],
    day_sub_links: dict[date, list[str]],
) -> str:
    month_name = date(year, month, 1).strftime("%B")
    colon_path = f"Journal:{year:04d}:{month:02d}:{month:02d}"
    month_tags = _sample_tags(2, 5)
    focus_blurbs = ", ".join(FAKER.words(nb=4))
    cross_refs = _sample_cross_links(cross_targets, 4)
    lines = [
        f"# {month_name} {year}",
        "",
        f"Monthly tracker anchored at :{colon_path}.",
        f"This span includes {len(day_dates)} daily entries focused on {focus_blurbs}.",
        f"Tags: {_format_tags(month_tags) if month_tags else '@journal'}",
        "",
        "Daily links:",
    ]
    for entry in day_dates:
        summary = FAKER.sentence(nb_words=12)
        lines.append(
            f"- {entry:%Y-%m-%d}: :Journal:{entry:%Y}:{entry:%m}:{entry:%d}:{entry:%d} — {summary}"
        )
        sub_links = day_sub_links.get(entry) or []
        if sub_links:
            sub_line = ", ".join(f":{ref}" for ref in sub_links[:6])
            lines.append(f"  - Subpages: {sub_line}")
    lines.extend(
        [
            "",
            "## Highlights",
            "",
            textwrap.fill(
                " ".join(FAKER.sentences(nb=random.randint(4, 6))),
                width=86,
            ),
        ]
    )
    if cross_refs:
        lines.extend(
            [
                "",
                "Project references:",
                *[f"- :{ref}" for ref in cross_refs],
            ]
        )
    sample_days = random.sample(day_dates, k=min(3, len(day_dates))) if day_dates else []
    for sample in sample_days:
        lines.extend(
            [
                "",
                _headered_paragraph(
                    3,
                    f"Review :Journal:{sample:%Y}:{sample:%m}:{sample:%d}:{sample:%d} for supporting notes.",
                ),
            ]
        )
    return "\n".join(lines).strip() + "\n"


def _build_journal_day_content(entry_date: date, cross_targets: list[str], sub_links: list[str]) -> str:
    colon_path = f"Journal:{entry_date:%Y}:{entry_date:%m}:{entry_date:%d}:{entry_date:%d}"
    mood = random.choice(["calm", "focused", "energized", "curious", "heads-down"])
    energy = random.choice(["steady", "peaking", "variable", "recharging"])
    focus = FAKER.bs().capitalize()
    weather = random.choice(["clear", "rainy", "cloudy", "stormy", "crisp"])
    tasks = _sample_tasks(reference_date=entry_date)
    day_tasks = random.sample(tasks, k=min(4, len(tasks)))
    timeline = _generate_journal_timeline()
    cross_refs = _sample_cross_links(cross_targets, 6)
    day_tags = _sample_tags(3, 6)

    intro_lines = [
        f"# {entry_date:%A, %B %d, %Y}",
        "",
        f"Tracked at :{colon_path}.",
        f"Tags: {_format_tags(day_tags) if day_tags else '@journal'}",
        "",
        "Daily context:",
        f"- Mood: {mood}",
        f"- Energy: {energy}",
        f"- Focus: {focus}",
        f"- Weather: {weather}",
        "",
        "Timeline:",
        *timeline,
        "",
        "Tasks:",
        *day_tasks,
    ]
    if cross_refs:
        intro_lines.extend(
            [
                "",
                "Cross-links:",
                *[f"- :{ref}" for ref in cross_refs],
            ]
        )
    if sub_links:
        intro_lines.extend(
            [
                "",
                "Day subpages:",
                *[f"- :{ref}" for ref in sub_links],
            ]
        )

    sections = []
    anchors = [
        "Morning Focus",
        "Midday Collaboration",
        "Afternoon Delivery",
        "Evening Reflection",
    ]
    for label in anchors:
        sentences = " ".join(FAKER.sentences(nb=random.randint(3, 5)))
        body = textwrap.fill(sentences, width=86)
        sections.append(f"## {label}\n\n{body}")

    reflection = textwrap.fill(
        " ".join(FAKER.sentences(nb=random.randint(4, 6))),
        width=86,
    )

    base = "\n".join(intro_lines)
    body = "\n\n".join(sections)
    return f"{base}\n\n{body}\n\n## Reflection\n\n{reflection}\n"


def _generate_journal_timeline() -> list[str]:
    events = [
        f"Stand-up on {FAKER.word()} deliverables",
        f"Deep work sprint for {FAKER.bs()}",
        f"Pairing with {FAKER.first_name()} on {FAKER.word()} decisions",
        f"Retrospective and notes for {FAKER.catch_phrase().lower()}",
    ]
    hour = 8
    timeline: list[str] = []
    for summary in events:
        minute = random.choice([0, 15, 30, 45])
        timeline.append(f"- {hour:02d}:{minute:02d} — {summary}")
        hour = min(20, hour + random.randint(1, 3))
    return timeline


def _sample_cross_links(cross_targets: list[str], count: int) -> list[str]:
    if not cross_targets:
        return []
    picks = random.sample(cross_targets, k=min(count, len(cross_targets)))
    random.shuffle(picks)
    return picks


def _write_journal_day_subpages(day_dir: Path, entry_date: date, cross_targets: list[str]) -> list[str]:
    """Create a handful of subpages beneath a journal day to mimic real-world notes."""
    topics = [
        "Meetings",
        "Research",
        "Decisions",
        "Notes",
        "Ideas",
        "Risks",
        "Planning",
        "Review",
    ]
    count = random.randint(2, 4)
    picks = random.sample(topics, k=count)
    sub_links: list[str] = []
    for topic in picks:
        slug = topic.replace(" ", "")
        sub_dir = day_dir / slug
        sub_dir.mkdir(parents=True, exist_ok=True)
        file_path = sub_dir / f"{slug}{PAGE_SUFFIX}"
        colon_path = f"Journal:{entry_date:%Y}:{entry_date:%m}:{entry_date:%d}:{slug}"
        cross_refs = _sample_cross_links(cross_targets, random.randint(3, 6))
        sub_tags = _sample_tags(2, 5)
        body_parts = [
            f"# {topic} for {entry_date:%Y-%m-%d}",
            "",
            f"Tracked at :{colon_path}.",
            f"Tags: {_format_tags(sub_tags) if sub_tags else '@journal'}",
            "",
            "Highlights:",
            *[f"- {FAKER.sentence(nb_words=12)}" for _ in range(random.randint(3, 6))],
        ]
        if cross_refs:
            body_parts.extend(
                [
                    "",
                    "Related pages:",
                    *[f"- :{ref}" for ref in cross_refs],
                ]
            )
        # A few day-specific tasks in the subpage
        sub_tasks = _sample_tasks(reference_date=entry_date)
        if sub_tasks:
            body_parts.extend(["", "Tasks:", *random.sample(sub_tasks, k=min(3, len(sub_tasks)))])
        file_path.write_text("\n".join(body_parts) + "\n", encoding="utf-8")
        sub_links.append(colon_path)
    return sub_links


if __name__ == "__main__":
    main()
