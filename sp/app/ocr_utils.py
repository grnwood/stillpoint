from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import sys

from PIL import Image, UnidentifiedImageError
import pytesseract


SUPPORTED_OCR_IMAGE_SUFFIXES = frozenset(
    {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}
)


def _platform_tesseract_candidates() -> tuple[Path, ...]:
    if sys.platform == "darwin":
        return (
            Path("/opt/homebrew/bin/tesseract"),
            Path("/usr/local/bin/tesseract"),
            Path("/opt/local/bin/tesseract"),
        )
    if sys.platform == "win32":
        roots = (
            os.environ.get("ProgramFiles"),
            os.environ.get("ProgramFiles(x86)"),
            os.environ.get("LOCALAPPDATA"),
        )
        return tuple(
            Path(root) / "Tesseract-OCR" / "tesseract.exe"
            for root in roots
            if root
        )
    return (Path("/usr/bin/tesseract"), Path("/usr/local/bin/tesseract"))


def find_tesseract_command() -> str | None:
    """Locate Tesseract even when a desktop app does not inherit shell PATH."""
    configured = os.environ.get("TESSERACT_CMD", "").strip()
    if configured:
        candidate = Path(configured).expanduser()
        if candidate.is_file():
            return str(candidate)
    discovered = shutil.which("tesseract")
    if discovered:
        return discovered
    for candidate in _platform_tesseract_candidates():
        if candidate.is_file():
            return str(candidate)
    return None


def tesseract_install_message() -> str:
    return (
        "Tesseract OCR could not be found. Install it, then restart StillPoint:\n\n"
        "macOS (Homebrew):  brew install tesseract\n"
        "Windows (winget):  winget install UB-Mannheim.TesseractOCR\n"
        "Ubuntu/Debian:  sudo apt install tesseract-ocr\n\n"
        "If it is installed in a custom location, set TESSERACT_CMD to the full "
        "path of the Tesseract executable before starting StillPoint."
    )


@dataclass(slots=True)
class OCRImageResult:
    text: str
    error_code: str | None = None
    message: str | None = None

    @property
    def ok(self) -> bool:
        return self.error_code is None


def ocr_image_file(image_path: Path) -> OCRImageResult:
    path = Path(image_path)
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_OCR_IMAGE_SUFFIXES:
        return OCRImageResult(
            text="",
            error_code="unsupported_format",
            message="This image format is not supported for OCR.",
        )
    if not path.exists() or not path.is_file():
        return OCRImageResult(
            text="",
            error_code="missing_file",
            message="Could not locate the image file for OCR.",
        )
    command = find_tesseract_command()
    if command is None:
        return OCRImageResult(
            text="",
            error_code="tesseract_missing",
            message=tesseract_install_message(),
        )
    pytesseract.pytesseract.tesseract_cmd = command
    try:
        with Image.open(path) as img:
            text = pytesseract.image_to_string(img).strip()
            return OCRImageResult(text=text)
    except pytesseract.pytesseract.TesseractNotFoundError:
        return OCRImageResult(
            text="",
            error_code="tesseract_missing",
            message=tesseract_install_message(),
        )
    except UnidentifiedImageError:
        return OCRImageResult(
            text="",
            error_code="unsupported_format",
            message="This image format is not supported for OCR.",
        )
    except Exception as exc:
        return OCRImageResult(
            text="",
            error_code="ocr_failed",
            message=f"OCR failed: {exc}",
        )
