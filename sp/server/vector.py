from __future__ import annotations

from pathlib import Path
from threading import RLock
from typing import Dict, Iterable, List, Optional

from sp.rag.chroma import ChromaRAG
from sp.rag.index import RetrievedChunk


class VectorIndexManager:
    def __init__(self) -> None:
        self._lock = RLock()
        self._instances: Dict[str, ChromaRAG] = {}

    def _key(self, root: Path) -> str:
        return str(root.resolve())

    def _get(self, root: Path) -> ChromaRAG:
        key = self._key(root)
        with self._lock:
            client = self._instances.get(key)
            if client is None:
                client = ChromaRAG(key)
                self._instances[key] = client
            return client

    def index_text(self, root: Path, page_ref: str, text: str, kind: str, attachment: Optional[str] = None) -> None:
        self._get(root).index_text(page_ref, text, kind=kind, attachment=attachment)

    def delete_text(self, root: Path, page_ref: str, kind: str, attachment: Optional[str] = None) -> None:
        self._get(root).delete_text(page_ref, kind=kind, attachment=attachment)

    def query(
        self,
        root: Path,
        query_text: str,
        page_refs: Optional[Iterable[str]] = None,
        limit: int = 4,
        kind: Optional[str] = None,
    ) -> List[RetrievedChunk]:
        return self._get(root).query(
            query_text,
            page_refs=list(page_refs) if page_refs else None,
            limit=limit,
            kind=kind,
        )

    def query_attachments(
        self,
        root: Path,
        query_text: str,
        attachment_names: Iterable[str],
        limit: int = 4,
        kind: Optional[str] = None,
    ) -> List[RetrievedChunk]:
        return self._get(root).query_attachments(query_text, list(attachment_names), limit=limit, kind=kind)


vector_manager = VectorIndexManager()
