from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, UnidentifiedImageError
import pytesseract


SUPPORTED_OCR_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".bmp", ".tiff"})


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
    try:
        with Image.open(path) as img:
            text = pytesseract.image_to_string(img).strip()
            return OCRImageResult(text=text)
    except pytesseract.pytesseract.TesseractNotFoundError:
        return OCRImageResult(
            text="",
            error_code="tesseract_missing",
            message="Tesseract OCR is not available on this system.",
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
