import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

from app.retrieval.store import ChunkStore
from app.schemas.ingestion import Chunk


class TestEmbedder:
    __test__ = False
    fingerprint = "test-vectors-v1"
    dimension = 3

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [[1.0, 0.0, 0.0] if "tariff" in text.lower() else [0.0, 1.0, 0.0] for text in texts]


def chunk(doc: str, identity: str, text: str, section: str | None = None) -> Chunk:
    return Chunk(
        chunk_id=identity, doc_id=doc, page_start=2, page_end=3, section_title=section, text=text
    )


def test_multiple_documents_filter_and_metadata(tmp_path: Path) -> None:
    store = ChunkStore(tmp_path, TestEmbedder())
    a = chunk("a", "same-id", "tariff is USD 45", "Price")
    b = chunk("b", "same-id", "tariff is USD 60")
    store.replace_document("a", [a])
    store.replace_document("b", [b])
    assert store.count() == 2
    assert {h.chunk.doc_id for h in store.search("tariff")} == {"a", "b"}
    hits = store.search("tariff", doc_id="a")
    assert [h.chunk for h in hits] == [a]
    assert hits[0].cosine_distance == pytest.approx(0, abs=1e-5)
    assert store.search("tariff", doc_id="b")[0].chunk.section_title is None
    assert store.search("tariff", doc_id="unknown") == []


def test_reingestion_upsert_and_stale_removal(tmp_path: Path) -> None:
    store = ChunkStore(tmp_path, TestEmbedder())
    old = [chunk("a", "one", "tariff 45"), chunk("a", "two", "old terms")]
    store.replace_document("a", old)
    store.replace_document("b", [chunk("b", "one", "other contract")])
    store.replace_document("a", old)
    assert store.count("a") == 2
    new = [chunk("a", "one", "tariff 55")]
    result = store.replace_document("a", new)
    assert result.stale_chunks_removed == 1
    assert store.count() == 2
    assert store.count("b") == 1
    assert store.search("tariff", doc_id="a")[0].chunk.text == "tariff 55"


def test_persistence_in_fresh_process(tmp_path: Path) -> None:
    store = ChunkStore(tmp_path, TestEmbedder())
    store.replace_document("a", [chunk("a", "one", "tariff 45")])
    script = """
import chromadb, sys
client = chromadb.PersistentClient(path=sys.argv[1])
collection = client.get_collection('contract_chunks', embedding_function=None)
assert collection.count() == 1
assert collection.get(include=['metadatas'])['metadatas'][0]['doc_id'] == 'a'
"""
    subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_model_mismatch_rejected(tmp_path: Path) -> None:
    ChunkStore(tmp_path, TestEmbedder())
    other = TestEmbedder()
    other.fingerprint = "different-model"
    with pytest.raises(ValueError, match="different embedding"):
        ChunkStore(tmp_path, other)


def test_bad_input_and_failed_embedding_preserve_stored_document(tmp_path: Path) -> None:
    embedder = TestEmbedder()
    store = ChunkStore(tmp_path, embedder)
    original = chunk("a", "one", "tariff 45")
    store.replace_document("a", [original])
    for chunks in ([], [original, original], [chunk("b", "one", "wrong doc")]):
        with pytest.raises(ValueError):
            store.replace_document("a", chunks)
    for question, k in (("", 1), ("tariff", 0), ("tariff", 101)):
        with pytest.raises(ValueError):
            store.search(question, top_k=k)
    embedder.embed = lambda texts: [[float("nan"), 0, 0] for _ in texts]
    with pytest.raises(ValueError, match="Invalid embedding"):
        store.replace_document("a", [chunk("a", "new", "tariff 50")])
    assert store.count("a") == 1
    store.embedder = TestEmbedder()
    assert store.search("tariff", doc_id="a")[0].chunk == original


def test_empty_store(tmp_path: Path) -> None:
    assert ChunkStore(tmp_path, TestEmbedder()).search("tariff") == []


def test_page_offsets_survive_storage_and_retrieval(tmp_path: Path) -> None:
    from app.agent.evidence import prepare_evidence
    from app.ingestion import chunk_document
    from app.schemas.ingestion import ExtractedDocument, PageText

    chunks = chunk_document(
        ExtractedDocument(
            doc_id="a",
            pages=[
                PageText(page_number=1, text="1. Tariff\nThe tariff is 45."),
                PageText(page_number=2, text="Payment is due within 30 days."),
            ],
        )
    )
    store = ChunkStore(tmp_path, TestEmbedder())
    store.replace_document("a", chunks)
    hits = store.search("tariff", doc_id="a")
    assert hits[0].chunk.page_spans == chunks[0].page_spans
    sources = prepare_evidence(hits)
    assert sources[0].page == 1 and "tariff is 45" in sources[0].text
    assert sources[1].page == 2 and "30 days" in sources[1].text


def test_batch_boundaries_and_large_stale_cleanup(tmp_path: Path) -> None:
    store = ChunkStore(tmp_path, TestEmbedder())
    chunks = [chunk("a", str(i), f"tariff clause {i}") for i in range(130)]
    store.replace_document("a", chunks)
    assert store.count("a") == 130
    result = store.replace_document("a", chunks[:1])
    assert result.stale_chunks_removed == 129
    assert store.count("a") == 1


@pytest.mark.skipif(os.getenv("RUN_MODEL_TESTS") != "1", reason="opt-in model download")
def test_real_local_model_and_pdf_pipeline(tmp_path: Path) -> None:
    import pymupdf

    from app.retrieval.embeddings import LocalEmbedder
    from app.retrieval.service import ingest_pdf

    pdf_path = tmp_path / "contract.pdf"
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((50, 50), "1. Tariff\nThe electricity price is USD 45 per MWh.")
        page.insert_text((50, 150), "2. Termination\nEither party may terminate after breach.")
        pdf.save(pdf_path)
    store = ChunkStore(tmp_path / "db", LocalEmbedder())
    result = ingest_pdf(pdf_path, store)
    ingest_pdf(pdf_path, store)
    assert store.count() == result.chunks_stored == 2
    hits = store.search("What is the electricity price?", doc_id=result.doc_id, top_k=1)
    assert "45" in hits[0].chunk.text
    assert hits[0].chunk.page_start == 1
    model = store.embedder
    long_text = "The buyer pays for electricity every month. " * 150
    windows = model._windows(long_text)
    assert len(windows) > 1
    assert "".join(windows) == long_text
    assert all(model._token_count(w) <= model.model.max_seq_length for w in windows)
    assert len(model.embed([long_text])[0]) == model.dimension
