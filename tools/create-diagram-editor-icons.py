#!/usr/bin/env python3
"""Generate StillPoint diagram-editor icons from the checked-in SVG marks."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "sp" / "assets"
ICON_DIR = ASSETS / "icons"
PNG_DIR = ICON_DIR / "linux-png"
BASE = PNG_DIR / "stillpoint-1024x1024.png"
SIZES = (16, 24, 32, 48, 64, 128, 256, 512, 1024)
EDITORS = {
    "plantuml": (ASSETS / "puml_logo.svg", "PlantUML"),
    "mermaid": (ASSETS / "mmd-favicon.svg", "Mermaid"),
    "excalidraw": (ASSETS / "excalidraw-icon.svg", "Excalidraw"),
}


def render_svg(source: Path, destination: Path) -> None:
    inkscape = shutil.which("inkscape")
    if not inkscape:
        raise SystemExit("Inkscape is required to render the editor SVG assets")
    subprocess.run(
        [
            inkscape,
            str(source),
            "--export-area-drawing",
            "--export-background-opacity=0",
            "--export-width=390",
            f"--export-filename={destination}",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def composite(mark_path: Path) -> Image.Image:
    base = Image.open(BASE).convert("RGBA")
    mark = Image.open(mark_path).convert("RGBA")
    mark.thumbnail((390, 390), Image.Resampling.LANCZOS)

    badge_size = 472
    badge_left = 536
    badge_top = 536
    shadow = Image.new("RGBA", base.size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.ellipse(
        (badge_left + 10, badge_top + 14,
         badge_left + badge_size + 10, badge_top + badge_size + 14),
        fill=(0, 20, 55, 150),
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(18))
    base.alpha_composite(shadow)

    badge = Image.new("RGBA", base.size, (0, 0, 0, 0))
    badge_draw = ImageDraw.Draw(badge)
    badge_draw.ellipse(
        (badge_left, badge_top, badge_left + badge_size, badge_top + badge_size),
        fill=(250, 253, 255, 255),
        outline=(18, 61, 112, 255),
        width=18,
    )
    base.alpha_composite(badge)

    mark_x = badge_left + (badge_size - mark.width) // 2
    mark_y = badge_top + (badge_size - mark.height) // 2
    base.alpha_composite(mark, (mark_x, mark_y))
    return base


def write_editor_assets(slug: str, display_name: str, master: Image.Image) -> None:
    generated: dict[int, Path] = {}
    for size in SIZES:
        output = PNG_DIR / f"{slug}-{size}x{size}.png"
        resized = master.resize((size, size), Image.Resampling.LANCZOS)
        resized.save(output, optimize=True)
        generated[size] = output

    master.save(
        ICON_DIR / f"{display_name}.ico",
        format="ICO",
        sizes=[(size, size) for size in SIZES if size <= 256],
    )
    master.save(ICON_DIR / f"{display_name}.icns", format="ICNS")


def main() -> None:
    if not BASE.is_file():
        raise SystemExit(f"StillPoint icon master not found: {BASE}")
    PNG_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="stillpoint-editor-icons-") as temp_dir:
        temp = Path(temp_dir)
        for slug, (svg_path, display_name) in EDITORS.items():
            rendered = temp / f"{slug}.png"
            render_svg(svg_path, rendered)
            write_editor_assets(slug, display_name, composite(rendered))
    print(f"Created diagram editor icons in {ICON_DIR}")


if __name__ == "__main__":
    main()
