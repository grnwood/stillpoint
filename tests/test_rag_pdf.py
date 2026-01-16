from pathlib import Path
from shutil import copy2

from pdfminer.high_level import extract_text

from sp.rag.chroma import ChromaRAG


def test_rag_retrieve_pdf(tmp_path: Path) -> None:
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    marker_page = vault_root / "PersonalNotes.md"
    marker_page.write_text("vault page marker", encoding="utf-8")
    pdf_source = Path("dev-assets/richesrestaurant.pdf")
    assert pdf_source.exists(), f"{pdf_source} not found for test"
    pdf_dest = vault_root / pdf_source.name
    copy2(pdf_source, pdf_dest)

    rag = ChromaRAG(str(vault_root))
    try:
        attachment_text = extract_text(str(pdf_dest))
        assert attachment_text.strip(), "Extracted attachment text should not be empty"
        assert "Riches" in attachment_text or "Restaurant" in attachment_text

        rag.index_text("/PersonalNotes.md", "page marker content", kind="page")
        rag.index_text("/PersonalNotes.md", attachment_text, kind="attachment", attachment=pdf_dest.name)

        chunks = rag.query_attachments("Riches", [pdf_dest.name], limit=4)
        assert chunks, "Attachment query should return the PDF chunk"
        assert pdf_dest.name == chunks[0].attachment_name
        assert "Riches" in chunks[0].content or "Restaurant" in chunks[0].content

        general = rag.query("restaurant", page_refs=["/PersonalNotes.md"], limit=4)
        assert general, "General query should still return at least one chunk"
        assert any(chunk.attachment_name == pdf_dest.name for chunk in general)
    finally:
        rag.close()
