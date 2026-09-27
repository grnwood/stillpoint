"""Bounded, UI-independent readers for tabular file previews."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import codecs
import csv


MAX_TABLE_ROWS = 10_000
MAX_TABLE_COLUMNS = 256
MAX_TABLE_CELLS = 250_000
MAX_CELL_CHARS = 4_096
CSV_SAMPLE_BYTES = 64 * 1024


@dataclass(frozen=True)
class TablePreview:
    rows: list[list[str]]
    truncated: bool = False
    delimiter: str | None = None
    has_header: bool = False
    sheet_names: tuple[str, ...] = ()
    sheet_name: str | None = None
    total_rows: int | None = None
    total_columns: int | None = None


def _display_cell(value) -> str:
    if value is None:
        return ""
    text = str(value)
    if len(text) > MAX_CELL_CHARS:
        return text[:MAX_CELL_CHARS] + "…"
    return text


def _bounded_rows(rows) -> tuple[list[list[str]], bool]:
    result: list[list[str]] = []
    cells = 0
    truncated = False
    for row in rows:
        if len(result) >= MAX_TABLE_ROWS or cells >= MAX_TABLE_CELLS:
            truncated = True
            break
        values = [_display_cell(value) for value in row[:MAX_TABLE_COLUMNS]]
        if len(row) > MAX_TABLE_COLUMNS:
            truncated = True
        if cells + len(values) > MAX_TABLE_CELLS:
            values = values[:MAX_TABLE_CELLS - cells]
            truncated = True
        result.append(values)
        cells += len(values)
    return result, truncated


def _text_encoding(path: Path) -> str:
    with path.open("rb") as stream:
        prefix = stream.read(4)
    if prefix.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    if prefix.startswith(codecs.BOM_UTF16_LE) or prefix.startswith(codecs.BOM_UTF16_BE):
        return "utf-16"
    return "utf-8-sig"


def read_delimited_preview(path: Path) -> TablePreview:
    """Read a bounded CSV/TSV preview without loading the whole file."""
    encoding = _text_encoding(path)
    with path.open("r", encoding=encoding, errors="replace", newline="") as stream:
        sample = stream.read(CSV_SAMPLE_BYTES)
        stream.seek(0)
        fallback = "\t" if path.suffix.casefold() in (".tsv", ".tab") else ","
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
        except csv.Error:
            dialect = csv.excel_tab if fallback == "\t" else csv.excel
        try:
            has_header = csv.Sniffer().has_header(sample)
        except csv.Error:
            has_header = False
        reader = csv.reader(stream, dialect)
        rows, truncated = _bounded_rows(reader)
    return TablePreview(rows, truncated, dialect.delimiter, has_header)


def read_workbook_preview(path: Path, sheet_name: str | None = None) -> TablePreview:
    """Read one workbook sheet through the optional, lazily imported engine."""
    try:
        from python_calamine import CalamineWorkbook
    except ImportError as exc:  # pragma: no cover - depends on optional runtime packaging
        raise RuntimeError("Spreadsheet preview requires python-calamine") from exc

    with CalamineWorkbook.from_path(path) as workbook:
        names = tuple(workbook.sheet_names)
        if not names:
            return TablePreview([], sheet_names=names)
        selected = sheet_name if sheet_name in names else names[0]
        sheet = workbook.get_sheet_by_name(selected)
        rows, truncated = _bounded_rows(sheet.iter_rows())
        return TablePreview(
            rows,
            truncated,
            sheet_names=names,
            sheet_name=selected,
            total_rows=sheet.total_height + 1 if sheet.end is not None else 0,
            total_columns=sheet.total_width + 1 if sheet.end is not None else 0,
        )
