from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from chromadb import PersistentClient
from chromadb.config import Settings
from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
from sp.rag import telemetry
from sp.rag.index import RetrievedChunk


@dataclass
class ChromaRAG:
    vault_root: str
    collection_name: str = "vault-context"

    def __post_init__(self) -> None:
        base = Path(self.vault_root) / ".stillpoint" / "chroma"
        base.mkdir(parents=True, exist_ok=True)
        self.persist_dir = str(base)
        telemetry_impl = f"{telemetry.NoopTelemetryClient.__module__}.{telemetry.NoopTelemetryClient.__name__}"
        settings = Settings(
            chroma_api_impl="chromadb.api.rust.RustBindingsAPI",
            is_persistent=True,
            persist_directory=self.persist_dir,
            allow_reset=True,
            chroma_product_telemetry_impl=telemetry_impl,
            chroma_telemetry_impl=telemetry_impl,
            anonymized_telemetry=False,
        )
        self.client = PersistentClient(
            path=self.persist_dir,
            settings=settings,
        )
        self.collection = self._ensure_collection()

    def query(
        self,
        query_text: str,
        page_refs: Optional[list[str]] = None,
        limit: int = 4,
        kind: Optional[str] = None,
    ) -> list[RetrievedChunk]:
        if not query_text.strip():
            return []
        where: dict | None = None
        if page_refs and kind:
            where = {"$and": [{"page_ref": {"$in": page_refs}}, {"kind": kind}]}
        elif page_refs:
            where = {"page_ref": {"$in": page_refs}}
        elif kind:
            where = {"kind": kind}
        try:
            results = self.collection.query(
                query_texts=[query_text],
                n_results=limit,
                include=["documents", "metadatas", "distances"],
                where=where,
            )
        except Exception as exc:
            print(f"[Chroma] Query failed: {exc}")
            return []
        documents = results.get("documents", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]
        chunks: list[RetrievedChunk] = []
        for idx, doc in enumerate(documents):
            metadata = metadatas[idx] if idx < len(metadatas) else {}
            distance = distances[idx] if idx < len(distances) else None
            chunk = RetrievedChunk(
                page_ref=metadata.get("page_ref", ""),
                content=doc or "",
                score=distance,
                attachment_name=metadata.get("attachment_name"),
            )
            chunks.append(chunk)
        return chunks

    def query_attachments(
        self,
        query_text: str,
        attachment_names: list[str],
        limit: int = 4,
        kind: Optional[str] = None,
    ) -> list[RetrievedChunk]:
        if not query_text.strip() or not attachment_names:
            return []
        where: dict = {"attachment_name": {"$in": attachment_names}}
        if kind:
            where = {"$and": [where, {"kind": kind}]}
        try:
            results = self.collection.query(
                query_texts=[query_text],
                n_results=limit,
                include=["documents", "metadatas", "distances"],
                where=where,
            )
        except Exception as exc:
            print(f"[Chroma] Attachment query failed: {exc}")
            return []
        documents = results.get("documents", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]
        chunks: list[RetrievedChunk] = []
        for idx, doc in enumerate(documents):
            metadata = metadatas[idx] if idx < len(metadatas) else {}
            distance = distances[idx] if idx < len(distances) else None
            chunk = RetrievedChunk(
                page_ref=metadata.get("page_ref", ""),
                content=doc or "",
                score=distance,
                attachment_name=metadata.get("attachment_name"),
            )
            chunks.append(chunk)
        return chunks
    def _ensure_collection(self):
        if self.collection_name in [c.name for c in self.client.list_collections()]:
            return self.client.get_collection(name=self.collection_name)
        return self.client.create_collection(
            name=self.collection_name,
            embedding_function=DefaultEmbeddingFunction(),
        )

    def _doc_id(self, page_ref: str, kind: str, attachment: Optional[str] = None) -> str:
        if kind == "attachment" and attachment:
            return f"{page_ref}:{attachment}"
        return f"{page_ref}:{kind}"

    def _chunk_text(self, text: str, max_chars: int = 1200, overlap: int = 200) -> list[str]:
        cleaned = (text or "").strip()
        if not cleaned:
            return []
        chunks: list[str] = []
        start = 0
        length = len(cleaned)
        while start < length:
            end = min(start + max_chars, length)
            chunk = cleaned[start:end]
            if chunk.strip():
                chunks.append(chunk)
            if end >= length:
                break
            start = max(0, end - overlap)
        return chunks

    def _delete_scope(self, page_ref: str, kind: str, attachment: Optional[str] = None) -> None:
        where = {"$and": [{"page_ref": page_ref}, {"kind": kind}]}
        if attachment:
            where["$and"].append({"attachment_name": attachment})
        try:
            self.collection.delete(where=where)
            print(f"[Chroma] Delete request for {page_ref} ({kind})")
        except Exception as exc:
            print(f"[Chroma] Failed to delete {page_ref} ({kind}): {exc}")

    def index_text(self, page_ref: str, text: str, kind: str, attachment: Optional[str] = None) -> None:
        if not text.strip():
            print(f"[Chroma] Skipping empty text for {page_ref}")
            return
        self._delete_scope(page_ref, kind, attachment)
        base_id = self._doc_id(page_ref, kind, attachment)
        metadata = {"page_ref": page_ref, "kind": kind}
        if attachment:
            metadata["attachment_name"] = attachment
        chunks = self._chunk_text(text)
        if not chunks:
            return
        ids = [f"{base_id}:{idx}" for idx in range(len(chunks))]
        metadatas = [dict(metadata, chunk_index=idx) for idx in range(len(chunks))]
        self.collection.upsert(ids=ids, documents=chunks, metadatas=metadatas)
        print(f"[Chroma] Indexed {kind} context {base_id} chunks={len(chunks)}")

    def delete_text(self, page_ref: str, kind: str, attachment: Optional[str] = None) -> None:
        self._delete_scope(page_ref, kind, attachment)

    def close(self) -> None:
        self.client.persist()
