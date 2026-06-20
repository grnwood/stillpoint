from __future__ import annotations

from pathlib import Path
from typing import Iterable

from docx import Document
from pdfminer.high_level import extract_text as extract_pdf_text
from sp.app.ocr_utils import ocr_image_file
from sp.logging_flags import log_enabled


def _extract_text_from_image(image_path: Path) -> str:
    try:
        result = ocr_image_file(image_path)
        return result.text
    except Exception as exc:  # pragma: no cover - external tooling
        if log_enabled("rag_vector"):
            print(f"[Chroma] Failed to OCR {image_path}: {exc}")
        return ""


def _extract_docx_text(doc_path: Path) -> str:
    try:
        doc = Document(str(doc_path))
        return "\n".join(p.text for p in doc.paragraphs if p.text)
    except Exception as exc:  # pragma: no cover - external tooling
        if log_enabled("rag_vector"):
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
        if log_enabled("rag_vector"):
            print(f"[Chroma] Failed to extract {path}: {exc}")
        return ""
