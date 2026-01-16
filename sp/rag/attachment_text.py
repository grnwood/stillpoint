from __future__ import annotations

from pathlib import Path
from typing import Iterable

from docx import Document
from PIL import Image
import pytesseract
from pdfminer.high_level import extract_text as extract_pdf_text


def _extract_text_from_image(image_path: Path) -> str:
    try:
        with Image.open(image_path) as img:
            return pytesseract.image_to_string(img)
    except Exception as exc:  # pragma: no cover - external tooling
        print(f"[Chroma] Failed to OCR {image_path}: {exc}")
        return ""


def _extract_docx_text(doc_path: Path) -> str:
    try:
        doc = Document(str(doc_path))
        return "\n".join(p.text for p in doc.paragraphs if p.text)
    except Exception as exc:  # pragma: no cover - external tooling
        print(f"[Chroma] Failed to parse {doc_path}: {exc}")
        return ""


def extract_attachment_text(path: Path) -> str:
    """Extract readable text from an attachment for indexing."""
    suffix = path.suffix.lower()
    try:
        if suffix == ".pdf":
            return extract_pdf_text(str(path))
        if suffix == ".docx":
            return _extract_docx_text(path)
        if suffix in (".png", ".jpg", ".jpeg", ".bmp", ".tiff"):  # images
            return _extract_text_from_image(path)
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception as exc:
        print(f"[Chroma] Failed to extract {path}: {exc}")
        return ""
