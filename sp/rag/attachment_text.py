from __future__ import annotations

from pathlib import Path
from typing import Iterable
import codecs

from sp.logging_flags import log_enabled


MAX_OFFICE_CONTEXT_CHARS = 15_000
MAX_SHEET_ROWS = 1_000
MAX_SHEET_COLUMNS = 64
MAX_CELL_CHARS = 256


def _document_text(lines: Iterable[str], kind: str, max_chars: int | None) -> str:
    """Keep chat extraction bounded without shortening other attachment readers."""
    parts: list[str] = []
    used = 0
    for line in lines:
        text = str(line).strip()
        if not text:
            continue
        remaining = max_chars - used if max_chars is not None else None
        if remaining is None:
            parts.append(text)
            continue
        if remaining <= 0:
            parts.append(f"[{kind} content shortened for chat context.]")
            break
        if len(text) + 1 > remaining:
            parts.append(text[:remaining])
            parts.append(f"[{kind} content shortened for chat context.]")
            break
        parts.append(text)
        used += len(text) + 1
    return "\n".join(parts)


def _extract_text_from_image(image_path: Path) -> str:
    try:
        from sp.app.ocr_utils import ocr_image_file

        result = ocr_image_file(image_path)
        return result.text
    except Exception as exc:  # pragma: no cover - external tooling
        if log_enabled("rag_vector"):
            print(f"[Chroma] Failed to OCR {image_path}: {exc}")
        return ""


def _extract_docx_text(doc_path: Path, max_chars: int | None = None) -> str:
    from docx import Document
    from docx.oxml.table import CT_Tbl
    from docx.oxml.text.paragraph import CT_P
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    document = Document(str(doc_path))

    def lines() -> Iterable[str]:
        for child in document.element.body.iterchildren():
            if isinstance(child, CT_P):
                yield Paragraph(child, document._body).text
            elif isinstance(child, CT_Tbl):
                table = Table(child, document._body)
                for row in table.rows:
                    yield " | ".join(cell.text.replace("\n", " ") for cell in row.cells)

    return _document_text(lines(), "Document", max_chars)


def _extract_pptx_text(path: Path, max_chars: int | None = None) -> str:
    from pptx import Presentation

    def shape_lines(shape) -> Iterable[str]:
        if getattr(shape, "has_table", False):
            for row in shape.table.rows:
                yield " | ".join(cell.text.replace("\n", " ") for cell in row.cells)
        elif getattr(shape, "has_text_frame", False):
            for paragraph in shape.text_frame.paragraphs:
                yield paragraph.text
        for child in getattr(shape, "shapes", ()):
            yield from shape_lines(child)

    presentation = Presentation(str(path))

    def lines() -> Iterable[str]:
        for number, slide in enumerate(presentation.slides, 1):
            title_shape = slide.shapes.title
            title = title_shape.text.strip() if title_shape is not None else ""
            yield f"Slide {number}: {title}" if title else f"Slide {number}"
            for shape in slide.shapes:
                if title_shape is not None and shape._element is title_shape._element:
                    continue
                yield from shape_lines(shape)
            if slide.has_notes_slide:
                notes = slide.notes_slide.notes_text_frame
                if notes is not None and notes.text.strip():
                    yield f"Notes: {notes.text.strip()}"

    return _document_text(lines(), "Presentation", max_chars)


def _extract_workbook_text(path: Path, max_chars: int | None = None) -> str:
    from python_calamine import CalamineWorkbook

    with CalamineWorkbook.from_path(path) as workbook:
        names = tuple(workbook.sheet_names)

        def lines() -> Iterable[str]:
            yield "Sheets: " + ", ".join(names)
            for name in names:
                yield f"Sheet: {name}"
                sheet = workbook.get_sheet_by_name(name)
                for number, row in enumerate(sheet.iter_rows(), 1):
                    if max_chars is not None and number > MAX_SHEET_ROWS:
                        yield f"[Additional rows in {name} omitted.]"
                        break
                    selected = row[:MAX_SHEET_COLUMNS] if max_chars is not None else row
                    cells = [str(value if value is not None else "").replace("\n", " ").replace("\t", " ")
                             for value in selected]
                    if max_chars is not None:
                        cells = [cell[:MAX_CELL_CHARS] for cell in cells]
                    yield f"{number}: " + " | ".join(cells)
                    if max_chars is not None and len(row) > MAX_SHEET_COLUMNS:
                        yield f"[Additional columns in row {number} omitted.]"

        return _document_text(lines(), "Spreadsheet", max_chars)


def extract_attachment_text(path: Path, *, max_chars: int | None = None) -> str:
    """Extract readable text from an attachment for indexing."""
    suffix = path.suffix.lower()
    try:
        if suffix == ".pdf":
            from pdfminer.high_level import extract_text as extract_pdf_text

            return extract_pdf_text(str(path))
        if suffix == ".docx":
            return _extract_docx_text(path, max_chars)
        if suffix == ".pptx":
            return _extract_pptx_text(path, max_chars)
        if suffix in (".xls", ".xlsx", ".xlsm", ".xlsb", ".ods"):
            return _extract_workbook_text(path, max_chars)
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
