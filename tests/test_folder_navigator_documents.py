from pathlib import Path
import os
import subprocess
import time

from docx import Document
import pytest
from pptx import Presentation
from pptx.util import Inches

from sp.app.folder_navigator import documents


def _sample_docx(path: Path) -> None:
    document = Document()
    document.add_heading("Quarterly Review", level=1)
    paragraph = document.add_paragraph()
    paragraph.add_run("Important").bold = True
    paragraph.add_run(" details")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Item"
    table.cell(0, 1).text = "Amount"
    table.cell(1, 0).text = "Revenue"
    table.cell(1, 1).text = "$42"
    document.add_paragraph("After the table")
    document.save(path)


def test_docx_falls_back_to_portable_structured_html(tmp_path, monkeypatch):
    path = tmp_path / "review.docx"
    _sample_docx(path)
    monkeypatch.setattr(documents, "_libreoffice_executable", lambda: None)

    preview = documents.build_docx_preview(path)

    assert preview.backend == "python-docx"
    assert preview.pdf_path is None
    assert "Simplified preview" in preview.notice
    assert "<h1>Quarterly Review</h1>" in preview.html
    assert "<strong>Important</strong> details" in preview.html
    assert "<table>" in preview.html
    assert preview.html.index("Quarterly Review") < preview.html.index("Revenue")
    assert preview.html.index("Revenue") < preview.html.index("After the table")


def test_docx_libreoffice_conversion_is_fingerprint_cached(tmp_path, monkeypatch):
    path = tmp_path / "review.docx"
    _sample_docx(path)
    cache = tmp_path / "cache"
    monkeypatch.setattr(documents, "_libreoffice_executable", lambda: "/usr/bin/soffice")
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        output = Path(command[command.index("--outdir") + 1])
        (output / "review.pdf").write_bytes(b"%PDF-1.4\npreview\n")
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(documents.subprocess, "run", fake_run)

    first = documents.build_docx_preview(path, cache)
    second = documents.build_docx_preview(path, cache)

    assert first.backend == "libreoffice"
    assert first.pdf_path == second.pdf_path
    assert first.pdf_path.read_bytes().startswith(b"%PDF")
    assert "Quarterly Review" in first.html
    assert len(calls) == 1
    assert calls[0][1]["timeout"] == 45


def test_pptx_falls_back_to_portable_slide_preview(tmp_path, monkeypatch):
    path = tmp_path / "briefing.pptx"
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[5])
    slide.shapes.title.text = "Project Briefing"
    text_box = slide.shapes.add_textbox(Inches(1), Inches(1.5), Inches(5), Inches(1))
    paragraph = text_box.text_frame.paragraphs[0]
    paragraph.add_run().text = "Delivery status"
    paragraph.runs[0].font.bold = True
    table = slide.shapes.add_table(2, 2, Inches(1), Inches(3), Inches(5), Inches(1.5)).table
    table.cell(0, 0).text = "Workstream"
    table.cell(0, 1).text = "Status"
    table.cell(1, 0).text = "Preview"
    table.cell(1, 1).text = "Ready"
    slide.notes_slide.notes_text_frame.text = "Discuss the preview fallback."
    presentation.save(path)
    monkeypatch.setattr(documents, "_libreoffice_executable", lambda: None)

    preview = documents.build_pptx_preview(path)

    assert preview.backend == "python-pptx"
    assert preview.pdf_path is None
    assert "slide-layout fidelity" in preview.notice
    assert "Project Briefing" in preview.html
    assert "<strong>Delivery status</strong>" in preview.html
    assert "Workstream" in preview.html
    assert "Speaker notes" in preview.html
    assert "Discuss the preview fallback." in preview.html


@pytest.mark.parametrize(
    ("suffix", "builder_name", "backend"),
    [
        (".docx", "build_docx_preview", "python-docx"),
        (".pptx", "build_pptx_preview", "python-pptx"),
    ],
)
def test_folder_navigator_displays_portable_office_preview(
        tmp_path, monkeypatch, qapp, suffix, builder_name, backend):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QPushButton, QTextBrowser
    from sp.app.folder_navigator.window import Window

    path = tmp_path / f"portable{suffix}"
    path.write_bytes(b"placeholder")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(
        documents,
        builder_name,
        lambda _path: documents.DocxPreview(
            backend=backend,
            html="<h1>Portable preview</h1>",
            notice="Simplified preview notice",
        ),
    )
    window = Window(tmp_path)
    window.open_file(path, pinned=True)

    deadline = time.monotonic() + 2
    while not window.active_tab().findChildren(QTextBrowser):
        assert time.monotonic() < deadline
        qapp.processEvents()
        time.sleep(.01)

    viewer = window.active_tab().viewer
    browser = viewer.findChild(QTextBrowser)
    assert browser.toPlainText() == "Portable preview"
    assert browser.textInteractionFlags() & Qt.TextSelectableByMouse
    assert any(
        button.text() == "Open in Default Application"
        for button in viewer.findChildren(QPushButton)
    )
    menu = window._create_preview_context_menu(window.active_tab(), browser)
    assert "Reveal in Folder" in [action.text() for action in menu.actions()]
    window.close()


def test_office_preview_cache_is_bounded_by_file_count(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    cache.mkdir()
    files = [cache / f"{index}.pdf" for index in range(3)]
    for index, path in enumerate(files):
        path.write_bytes(b"pdf")
        os.utime(path, (100 + index, 100 + index))
    monkeypatch.setattr(documents, "MAX_OFFICE_CACHE_FILES", 2)
    monkeypatch.setattr(documents, "MAX_OFFICE_CACHE_BYTES", 1024)
    monkeypatch.setattr(documents, "MAX_OFFICE_CACHE_AGE_SECONDS", 10**12)

    documents._trim_office_cache(cache, preserve=files[-1])

    assert set(cache.iterdir()) == {files[-1], files[-2]}


@pytest.mark.parametrize("suffix", [".docx", ".pptx"])
def test_libreoffice_preview_can_switch_to_selectable_text(
        tmp_path, monkeypatch, qapp, suffix):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QPushButton, QTextBrowser, QWidget
    import sp.app.folder_navigator.window as window_module

    path = tmp_path / f"selectable{suffix}"
    path.write_bytes(b"placeholder")
    pdf = tmp_path / "preview.pdf"
    pdf.write_bytes(b"%PDF placeholder")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    builder_name = "build_pptx_preview" if suffix == ".pptx" else "build_docx_preview"
    monkeypatch.setattr(
        documents,
        builder_name,
        lambda _path: documents.DocumentPreview(
            backend="libreoffice",
            pdf_path=pdf,
            html="<p>Select this document text</p>",
        ),
    )

    def fake_pdf_view(_path):
        widget = QWidget()
        widget.zoom_in = lambda: None
        widget.zoom_out = lambda: None
        return widget

    monkeypatch.setattr(window_module, "pdf_view", fake_pdf_view)
    window = window_module.Window(tmp_path)
    window.open_file(path, pinned=True)

    deadline = time.monotonic() + 2
    selectable = None
    while selectable is None:
        assert time.monotonic() < deadline
        qapp.processEvents()
        viewer = getattr(window.active_tab(), "viewer", None)
        selectable = viewer.findChild(QTextBrowser) if viewer is not None else None
        time.sleep(.01)
    buttons = {
        button.text(): button
        for button in window.active_tab().viewer.findChildren(QPushButton)
    }
    buttons["Selectable Text"].click()

    assert selectable.toPlainText() == "Select this document text"
    assert selectable.textInteractionFlags() & Qt.TextSelectableByMouse
    assert window.active_tab().viewer.document_views.currentWidget() is selectable
    window.close()
