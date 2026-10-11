import json
from collections.abc import Sequence
from hashlib import sha256
from math import isfinite
from pathlib import Path

import chromadb
from filelock import FileLock

from app.retrieval.embeddings import Embedder
from app.schemas.ingestion import Chunk
from app.schemas.retrieval import IndexResult, SearchHit


class ChunkStore:
    def __init__(
        self,
        path: str | Path,
        embedder: Embedder,
        collection_name: str = "contract_chunks",
    ) -> None:
        directory = Path(path).resolve()
        directory.mkdir(parents=True, exist_ok=True)
        self.embedder = embedder
        self._lock = FileLock(str(directory / "ppa-sense.lock"), timeout=120)
        expected = {"embedding_fingerprint": embedder.fingerprint, "schema_version": 1}
        with self._lock:
            self._client = chromadb.PersistentClient(path=str(directory))
            self._collection = self._client.get_or_create_collection(
                name=collection_name,
                embedding_function=None,
                metadata=expected,
                configuration={"hnsw": {"space": "cosine"}},
            )
            if self._collection.metadata != expected:
                raise ValueError(
                    "Collection uses a different embedding model or schema; reindex "
                    "into a new CHROMA_COLLECTION"
                )
            if self._collection.configuration["hnsw"]["space"] != "cosine":
                raise ValueError("Collection must use cosine distance")

    @staticmethod
    def _record_id(chunk: Chunk) -> str:
        return sha256(f"{chunk.doc_id}\0{chunk.chunk_id}".encode()).hexdigest()

    def _embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = self.embedder.embed(texts)
        if len(vectors) != len(texts) or any(
            len(v) != self.embedder.dimension
            or not all(isfinite(x) for x in v)
            or not any(x != 0 for x in v)
            for v in vectors
        ):
            raise ValueError("Invalid embedding count, dimension, or values")
        return vectors

    def replace_document(self, doc_id: str, chunks: Sequence[Chunk]) -> IndexResult:
        if not doc_id.strip() or any(c.doc_id != doc_id for c in chunks):
            raise ValueError("Every chunk must belong to the requested doc_id")
        ids = [self._record_id(c) for c in chunks]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate chunk IDs in input")
        if not chunks or any(not c.text.strip() for c in chunks):
            raise ValueError("Refusing to replace a document with empty content")
        # Finish model work before changing the stored document.
        vectors: list[list[float]] = []
        for start in range(0, len(chunks), 64):
            vectors.extend(self._embed([c.text for c in chunks[start : start + 64]]))
        metadata = [
            {
                "doc_id": c.doc_id,
                "chunk_id": c.chunk_id,
                "page_start": c.page_start,
                "page_end": c.page_end,
                "section_title": c.section_title or "",
                "page_spans": json.dumps([span.model_dump() for span in c.page_spans]),
            }
            for c in chunks
        ]
        with self._lock:
            old_ids = self._collection.get(where={"doc_id": doc_id}, include=[])["ids"]
            batch_size = min(64, self._client.get_max_batch_size())
            for start in range(0, len(chunks), batch_size):
                end = start + batch_size
                self._collection.upsert(
                    ids=ids[start:end],
                    embeddings=vectors[start:end],
                    documents=[c.text for c in chunks[start:end]],
                    metadatas=metadata[start:end],
                )
            stale = sorted(set(old_ids) - set(ids))
            for start in range(0, len(stale), batch_size):
                self._collection.delete(ids=stale[start : start + batch_size])
        return IndexResult(
            doc_id=doc_id, chunks_stored=len(chunks), stale_chunks_removed=len(stale)
        )

    def count(self, doc_id: str | None = None) -> int:
        with self._lock:
            if doc_id is None:
                return self._collection.count()
            return len(self._collection.get(where={"doc_id": doc_id}, include=[])["ids"])

    def search(self, question: str, doc_id: str | None = None, top_k: int = 5) -> list[SearchHit]:
        if not question.strip() or not 1 <= top_k <= 100:
            raise ValueError("Supply a nonempty question and top_k between 1 and 100")
        where = {"doc_id": doc_id} if doc_id is not None else None
        with self._lock:
            size = self.count(doc_id)
            if not size:
                return []
            response = self._collection.query(
                query_embeddings=self._embed([question]),
                n_results=min(top_k, size),
                where=where,
                include=["documents", "metadatas", "distances"],
            )
        hits: list[SearchHit] = []
        for text, meta, distance in zip(
            response["documents"][0],
            response["metadatas"][0],
            response["distances"][0],
            strict=True,
        ):
            hits.append(
                SearchHit(
                    chunk=Chunk(
                        chunk_id=meta["chunk_id"],
                        doc_id=meta["doc_id"],
                        page_start=meta["page_start"],
                        page_end=meta["page_end"],
                        section_title=meta["section_title"] or None,
                        text=text,
                        page_spans=json.loads(meta.get("page_spans", "[]")),
                    ),
                    cosine_distance=distance,
                )
            )
        return hits
