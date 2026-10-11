from pathlib import Path

from app.config import Settings
from app.ingestion import chunk_document, extract_pdf
from app.retrieval.embeddings import LocalEmbedder
from app.retrieval.store import ChunkStore
from app.schemas.ingestion import ChunkingConfig
from app.schemas.retrieval import IndexResult


def create_store(settings: Settings | None = None) -> ChunkStore:
    config = settings or Settings()
    return ChunkStore(
        config.resolved_chroma_path,
        LocalEmbedder(
            config.embedding_model,
            config.embedding_revision,
            config.embedding_batch_size,
        ),
        config.chroma_collection,
    )


def ingest_pdf(
    path: str | Path,
    store: ChunkStore,
    config: ChunkingConfig | None = None,
) -> IndexResult:
    document = extract_pdf(path)
    chunks = chunk_document(document, config)
    result = store.replace_document(document.doc_id, chunks)
    result.warnings = document.warnings
    return result
