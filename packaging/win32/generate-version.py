import re
from pathlib import Path


def main() -> None:
    source = Path(__file__).resolve().parents[2] / "sp" / "__init__.py"
    text = source.read_text(encoding="utf-8")
    match = re.search(
        r"^VERSION\s*=\s*['\"]v?([0-9]+\.[0-9]+\.[0-9]+[a-zA-Z]?)['\"]\s*$",
        text,
        re.M,
    )
    if not match:
        raise SystemExit("VERSION not found in sp/__init__.py")

    version_iss = Path(__file__).resolve().parent / "version.iss"
    version_iss.write_text(f'#define AppVersion "{match.group(1)}"\n', encoding="utf-8")


if __name__ == "__main__":
    main()
