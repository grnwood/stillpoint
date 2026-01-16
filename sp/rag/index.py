from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RetrievedChunk:
    page_ref: str
    content: str
    score: float | None = None
    attachment_name: str | None = None


class VaultIndex:
    """Placeholder for a vault-level vector index."""

    def __init__(self, vault_id: str, base_path: str) -> None:
        self.vault_id = vault_id
        self.base_path = base_path

    def index_page(self, page_ref: str, markdown_text: str) -> None:
        raise NotImplementedError("Chroma integration not yet implemented")

    def delete_page(self, page_ref: str) -> None:
        raise NotImplementedError("Chroma integration not yet implemented")

    def index_attachment(self, page_ref: str, attachment_path: str) -> None:
        raise NotImplementedError("Chroma integration not yet implemented")

    def delete_attachment(self, page_ref: str, attachment_name: str) -> None:
        raise NotImplementedError("Chroma integration not yet implemented")

    def query(self, scope: dict, query_text: str, limit: int = 8) -> list[RetrievedChunk]:
        raise NotImplementedError("Chroma integration not yet implemented")

