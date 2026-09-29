from __future__ import annotations

from pathlib import Path
from typing import Iterable
import codecs

from sp.logging_flags import log_enabled


def _extract_text_from_image(image_path: Path) -> str:
    try:
        from sp.app.ocr_utils import ocr_image_file

        result = ocr_image_file(image_path)
        return result.text
    except Exception as exc:  # pragma: no cover - external tooling
        if log_enabled("rag_vector"):
            print(f"[Chroma] Failed to OCR {image_path}: {exc}")
        return ""


def _extract_docx_text(doc_path: Path) -> str:
    try:
        from docx import Document

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
            from pdfminer.high_level import extract_text as extract_pdf_text

            return extract_pdf_text(str(path))
        if suffix == ".docx":
            return _extract_docx_text(path)
        if suffix in (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"):  # images
            return _extract_text_from_image(path)
        data = path.read_bytes()
        if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
            return data.decode("utf-16")
        return data.decode("utf-8-sig", errors="ignore")
    except Exception as exc:
        if log_enabled("rag_vector"):
            print(f"[Chroma] Failed to extract {path}: {exc}")
        return ""
