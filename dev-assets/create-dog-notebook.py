#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

PAGE_SUFFIX = ".md"
IMAGE_WIDTH = 300
IMAGE_HEIGHT = 200


@dataclass(frozen=True)
class Breed:
    name: str
    origin: str
    temperament: str
    notes: str
    energy: str
    coat: str
    size: str


BREEDS = [
    Breed(
        name="Golden Retriever",
        origin="Scotland",
        temperament="Friendly, eager, steady",
        notes="Golden Retrievers are known for their soft mouths and reliable temperament, which makes them a favorite for families and service work.",
        energy="Medium-high",
        coat="Dense, water-repellent",
        size="Large",
    ),
    Breed(
        name="German Shepherd",
        origin="Germany",
        temperament="Confident, loyal, focused",
        notes="German Shepherds combine drive and biddability, which is why they show up in working roles and structured sport training.",
        energy="High",
        coat="Double coat",
        size="Large",
    ),
    Breed(
        name="Labrador Retriever",
        origin="Canada",
        temperament="Outgoing, gentle, reliable",
        notes="Labs are adaptable and food-motivated, which keeps training sessions upbeat and consistent.",
        energy="High",
        coat="Short, dense",
        size="Large",
    ),
    Breed(
        name="French Bulldog",
        origin="France",
        temperament="Bright, affectionate, clownish",
        notes="French Bulldogs are compact companions with a calm presence, ideal for urban households and shorter walks.",
        energy="Low-medium",
        coat="Short",
        size="Small",
    ),
    Breed(
        name="Beagle",
        origin="United Kingdom",
        temperament="Curious, cheerful, nose-driven",
        notes="Beagles thrive with scent games and structured routines, but need clear boundaries to keep their noses in check.",
        energy="Medium",
        coat="Short",
        size="Small-medium",
    ),
    Breed(
        name="Poodle",
        origin="Germany/France",
        temperament="Smart, athletic, people-focused",
        notes="Poodles learn quickly and need mental work as much as physical exercise. Grooming plans matter as much as training plans.",
        energy="Medium-high",
        coat="Curly, low-shed",
        size="Varies",
    ),
    Breed(
        name="Australian Shepherd",
        origin="United States",
        temperament="Driven, loyal, alert",
        notes="Aussies need jobs. When they are given consistent structure, they turn into laser-focused partners.",
        energy="High",
        coat="Medium, weather-resistant",
        size="Medium",
    ),
    Breed(
        name="Dachshund",
        origin="Germany",
        temperament="Bold, stubborn, loyal",
        notes="Dachshunds bring big-dog attitude in a small frame. Their long backs demand smart exercise choices.",
        energy="Medium",
        coat="Short/long/wire",
        size="Small",
    ),
    Breed(
        name="Rottweiler",
        origin="Germany",
        temperament="Calm, confident, protective",
        notes="Rottweilers are steady guardians when properly socialized, with a strong need for trust-based handling.",
        energy="Medium",
        coat="Short, dense",
        size="Large",
    ),
    Breed(
        name="Shiba Inu",
        origin="Japan",
        temperament="Independent, alert, clean",
        notes="Shibas are cat-like in their independence. Building trust early keeps training positive and cooperative.",
        energy="Medium",
        coat="Double coat",
        size="Small-medium",
    ),
]

TAG_POOL = [
    "research",
    "temperament",
    "training",
    "health",
    "nutrition",
    "grooming",
    "family",
    "fieldnotes",
    "writing",
    "review",
    "breeders",
    "behavior",
    "story",
    "vet",
    "interview",
]

PALETTE = [
    "#f6d365",
    "#fda085",
    "#fbc2eb",
    "#a6c0fe",
    "#cfd9df",
    "#d4fc79",
    "#84fab0",
    "#8fd3f4",
    "#fddb92",
]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create a marketing-ready StillPoint vault centered on dog breeds."
    )
    parser.add_argument(
        "destination",
        nargs="?",
        default="dev-assets/dog-vault/A Dogs Life",
        help="Where to create the vault (default: ./A Dogs Life)",
    )
    args = parser.parse_args()

    dest = Path(args.destination).resolve()
    dest.mkdir(parents=True, exist_ok=True)

    root_name = "A Dogs Life"
    root_folder = dest / root_name
    root_folder.mkdir(parents=True, exist_ok=True)

    _write_page(root_folder, root_name, _build_root_page())

    _write_page(root_folder / "Breeds", "Breeds", _build_breeds_index())
    for idx, breed in enumerate(BREEDS):
        _write_breed_page(root_folder, breed, PALETTE[idx % len(PALETTE)])

    _write_page(root_folder / "Research Board", "Research Board", _build_research_board())
    _write_page(root_folder / "Research Board" / "Training Notes", "Training Notes", _build_training_notes())
    _write_page(root_folder / "Research Board" / "Nutrition Notes", "Nutrition Notes", _build_nutrition_notes())
    _write_page(root_folder / "Research Board" / "Health Watchlist", "Health Watchlist", _build_health_notes())

    _write_journal_pages(root_folder)

    print(f"Dog notebook created at: {dest}")


def _safe_name(name: str) -> str:
    return name.strip()


def _link(parts: list[str], label: str | None = None) -> str:
    target = ":".join(part.replace(" ", "_") for part in parts)
    text = label or parts[-1]
    return f"[:{target}|{text}]"


def _write_page(folder: Path, title: str, content: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    file_path = folder / f"{_safe_name(title)}{PAGE_SUFFIX}"
    file_path.write_text(content, encoding="utf-8")


def _write_breed_page(root: Path, breed: Breed, color: str) -> None:
    folder = root / "Breeds" / breed.name
    attachments = folder / "attachments"
    attachments.mkdir(parents=True, exist_ok=True)
    image_name = f"{breed.name.lower().replace(' ', '-')}.svg"
    image_path = attachments / image_name
    image_path.write_text(_build_svg(breed.name, color), encoding="utf-8")

    tags = " ".join(f"@{tag}" for tag in _pick_tags(4))
    due = date.today() + timedelta(days=7)
    review = date.today() + timedelta(days=14)
    content = "\n".join(
        [
            f"# {breed.name}",
            "",
            f"Tags: {tags}",
            "",
            f"![{breed.name}](attachments/{image_name}){{width={IMAGE_WIDTH}}}",
            "",
            breed.notes,
            "",
            "## Quick profile",
            "",
            f"- Origin: {breed.origin}",
            f"- Temperament: {breed.temperament}",
            f"- Energy: {breed.energy}",
            f"- Coat: {breed.coat}",
            f"- Size: {breed.size}",
            "",
            "## Research tasks",
            "",
            f"- [ ] Outline temperament summary for website copy @writing @temperament <{due.isoformat()}",
            f"- [ ] Verify health screening recommendations @health @vet <{review.isoformat()}",
            f"- [ ] Collect quotes from owners about daily routine @interview @story >{date.today().isoformat()}",
            f"- [ ] Draft training snapshot for puppy owners @training @review <{(review + timedelta(days=7)).isoformat()}",
            "",
            "## Notes",
            "",
            f"- Compare with {_link(['Breeds', 'Golden Retriever'])} for family temperament notes.",
            f"- Related research: {_link(['Research Board', 'Training Notes'])}, {_link(['Research Board', 'Health Watchlist'])}.",
        ]
    )
    _write_page(folder, breed.name, content)


def _build_root_page() -> str:
    today = date.today()
    highlight = _link(["Breeds", "Golden Retriever"], "Golden Retriever")
    return "\n".join(
        [
            "# A Dogs Life",
            "",
            "A working notebook for dog breed research, editorial planning, and field notes.",
            "",
            "Tags: @research @writing @fieldnotes @review",
            "",
            "## Featured breeds",
            "",
            f"- {highlight}",
            f"- {_link(['Breeds', 'German Shepherd'])}",
            f"- {_link(['Breeds', 'Labrador Retriever'])}",
            f"- {_link(['Breeds', 'French Bulldog'])}",
            "",
            "## Editorial tasks",
            "",
            f"- [ ] Draft the breed overview carousel @writing <{(today + timedelta(days=3)).isoformat()}",
            f"- [ ] Verify AKC links for each breed @review <{(today + timedelta(days=5)).isoformat()}",
            f"- [ ] Schedule photo updates for top 5 breeds @research @story <{(today + timedelta(days=10)).isoformat()}",
            "",
            "## Jump points",
            "",
            f"- {_link(['Breeds'], 'All breeds')}",
            f"- {_link(['Research Board', 'Training Notes'], 'Training Notes')}",
            f"- {_link(['Research Board', 'Nutrition Notes'], 'Nutrition Notes')}",
            f"- {_link(['Research Board', 'Health Watchlist'], 'Health Watchlist')}",
            f"- {_link(['Journal', f'{today.year:04d}', f'{today.month:02d}', f'{today.day:02d}'], 'Today\'s journal')}",
            "",
            "## Current focus",
            "",
            "- Update the temperament matrix with real quotes from owners @interview",
            "- Prep the visual guide for puppy socialization milestones @training",
        ]
    )


def _build_breeds_index() -> str:
    lines = [
        "# Breeds",
        "",
        "All breed profiles in the notebook.",
        "",
        "Tags: @research @review",
        "",
    ]
    for breed in BREEDS:
        lines.append(f"- {_link(['Breeds', breed.name], breed.name)}")
    lines.append("")
    lines.append("## Editorial checklist")
    lines.append("")
    lines.append(f"- [ ] Confirm photos for each profile @story <{(date.today() + timedelta(days=6)).isoformat()}")
    lines.append(f"- [ ] Balance the size categories in the publish queue @writing <{(date.today() + timedelta(days=12)).isoformat()}")
    return "\n".join(lines)


def _build_research_board() -> str:
    today = date.today()
    return "\n".join(
        [
            "# Research Board",
            "",
            "Central hub for breed research, interviews, and writing sprints.",
            "",
            "Tags: @research @writing @review",
            "",
            "## Active threads",
            "",
            f"- Compare energy levels between {_link(['Breeds', 'Australian Shepherd'])} and {_link(['Breeds', 'German Shepherd'])}.",
            f"- Gather grooming notes for {_link(['Breeds', 'Poodle'])} and {_link(['Breeds', 'Shiba Inu'])}.",
            f"- Draft companion-friendly city list for {_link(['Breeds', 'French Bulldog'])} owners.",
            "",
            "## To do",
            "",
            f"- [ ] Build a research brief for working breeds @research <{(today + timedelta(days=4)).isoformat()}",
            f"- [ ] Schedule interviews with two breeders @interview <{(today + timedelta(days=9)).isoformat()}",
            f"- [ ] Collect new lifestyle photos @story <{(today + timedelta(days=15)).isoformat()}",
        ]
    )


def _build_training_notes() -> str:
    today = date.today()
    return "\n".join(
        [
            "# Training Notes",
            "",
            "Patterns and routines that show up across breeds.",
            "",
            "Tags: @training @fieldnotes",
            "",
            "## Highlights",
            "",
            f"- {_link(['Breeds', 'Beagle'])} sessions improve with scent games in short bursts.",
            f"- {_link(['Breeds', 'Dachshund'])} responds best to slow, low-impact exercises.",
            f"- {_link(['Breeds', 'Australian Shepherd'])} needs structured mental work daily.",
            "",
            "## Tasks",
            "",
            f"- [ ] Draft clicker training sidebar @writing <{(today + timedelta(days=5)).isoformat()}",
            f"- [ ] Collect quotes on recall training @interview <{(today + timedelta(days=8)).isoformat()}",
        ]
    )


def _build_nutrition_notes() -> str:
    today = date.today()
    return "\n".join(
        [
            "# Nutrition Notes",
            "",
            "Feeding guidance, weight management, and owner-friendly tips.",
            "",
            "Tags: @nutrition @health",
            "",
            f"- {_link(['Breeds', 'Labrador Retriever'])}: watch for weight gain in older dogs.",
            f"- {_link(['Breeds', 'French Bulldog'])}: avoid over-heating during meals and walks.",
            "",
            "## Tasks",
            "",
            f"- [ ] Review vet-approved portion tables @vet <{(today + timedelta(days=6)).isoformat()}",
            f"- [ ] Draft nutrition FAQ section @writing <{(today + timedelta(days=11)).isoformat()}",
        ]
    )


def _build_health_notes() -> str:
    today = date.today()
    return "\n".join(
        [
            "# Health Watchlist",
            "",
            "Top health considerations and screening notes.",
            "",
            "Tags: @health @review",
            "",
            f"- {_link(['Breeds', 'German Shepherd'])}: hips and joint monitoring.",
            f"- {_link(['Breeds', 'Rottweiler'])}: focus on weight and joint support.",
            f"- {_link(['Breeds', 'Dachshund'])}: spine safety reminders.",
            "",
            "## Tasks",
            "",
            f"- [ ] Draft vet checklist for high-risk breeds @vet <{(today + timedelta(days=7)).isoformat()}",
            f"- [ ] Source health screening links @review <{(today + timedelta(days=13)).isoformat()}",
        ]
    )


def _write_journal_pages(root: Path) -> None:
    today = date.today()
    days = [today - timedelta(days=1), today, today + timedelta(days=1)]
    for entry_date in days:
        day_folder = root / "Journal" / f"{entry_date.year:04d}" / f"{entry_date.month:02d}" / f"{entry_date.day:02d}"
        day_folder.mkdir(parents=True, exist_ok=True)
        content = _build_journal_day(entry_date)
        _write_page(day_folder, f"{entry_date.day:02d}", content)


def _build_journal_day(entry_date: date) -> str:
    breeds_today = [
        _link(["Breeds", "Golden Retriever"]),
        _link(["Breeds", "Beagle"]),
        _link(["Breeds", "German Shepherd"]),
    ]
    return "\n".join(
        [
            f"# {entry_date:%A, %B %d, %Y}",
            "",
            "Tags: @journal @fieldnotes",
            "",
            "## Morning",
            "",
            f"- Met with Nora at the rescue to talk temperament notes for {breeds_today[0]}.",
            f"- Logged training notes from last week on {breeds_today[1]} puppies.",
            "",
            "## Afternoon",
            "",
            f"- Drafted a short article section on {breeds_today[2]} behavior cues.",
            f"- Reviewed photo needs for {_link(['Breeds', 'French Bulldog'])} profiles.",
            "",
            "## Tasks",
            "",
            f"- [ ] Summarize interview notes @writing @interview <{(entry_date + timedelta(days=2)).isoformat()}",
            f"- [ ] Update tag list for training section @review <{(entry_date + timedelta(days=5)).isoformat()}",
        ]
    )


def _pick_tags(count: int) -> list[str]:
    return TAG_POOL[:count]


def _build_svg(title: str, color: str) -> str:
    return "\n".join(
        [
            f"<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"{IMAGE_WIDTH}\" height=\"{IMAGE_HEIGHT}\" viewBox=\"0 0 {IMAGE_WIDTH} {IMAGE_HEIGHT}\">",
            "  <defs>",
            "    <linearGradient id=\"bg\" x1=\"0\" y1=\"0\" x2=\"1\" y2=\"1\">",
            f"      <stop offset=\"0%\" stop-color=\"{color}\" stop-opacity=\"0.9\" />",
            "      <stop offset=\"100%\" stop-color=\"#ffffff\" stop-opacity=\"0.9\" />",
            "    </linearGradient>",
            "  </defs>",
            f"  <rect width=\"{IMAGE_WIDTH}\" height=\"{IMAGE_HEIGHT}\" rx=\"24\" fill=\"url(#bg)\" />",
            "  <circle cx=\"60\" cy=\"60\" r=\"22\" fill=\"#1c1b19\" opacity=\"0.15\" />",
            "  <circle cx=\"100\" cy=\"60\" r=\"22\" fill=\"#1c1b19\" opacity=\"0.15\" />",
            "  <circle cx=\"80\" cy=\"95\" r=\"24\" fill=\"#1c1b19\" opacity=\"0.15\" />",
            "  <text x=\"24\" y=\"150\" font-family=\"Verdana, sans-serif\" font-size=\"18\" fill=\"#1c1b19\" font-weight=\"600\">",
            f"    {title}",
            "  </text>",
            "  <text x=\"24\" y=\"175\" font-family=\"Verdana, sans-serif\" font-size=\"12\" fill=\"#1c1b19\" opacity=\"0.7\">",
            "    Breed profile image",
            "  </text>",
            "</svg>",
        ]
    )


if __name__ == "__main__":
    main()
