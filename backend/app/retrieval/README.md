# Step 3: local embeddings and persistent search

An embedding is a list of numbers that represents the meaning of text. Similar
ideas tend to produce similar lists. This lets a question about "ending the agreement"
find a clause called "Termination", even when the exact words differ.

We use sentence-transformers on the CPU. The first use downloads the model from
Hugging Face; later uses reuse its cache. No Gemini key or paid embedding API is needed.
The PDF text is passed to the local model, not sent to an embedding service.

## Run from the project root

```powershell
.\.venv\Scripts\python.exe -m pip install -e "./backend[dev]"
.\.venv\Scripts\python.exe -m app.retrieval ingest "C:\path\contract.pdf"
.\.venv\Scripts\python.exe -m app.retrieval search "When must the buyer pay?" --doc-id "ID_FROM_INGEST"
```

Ingest prints the document ID, number of chunks saved, removed stale chunks, and
extraction warnings. Search returns matching text, source pages, section, and distance.
Without `--doc-id`, it searches all documents. This step finds passages; it does not
generate answers. There are no upload/search HTTP routes yet.

## Python interface

```python
from app.retrieval.service import create_store, ingest_pdf

store = create_store()
result = ingest_pdf("contract.pdf", store)
hits = store.search("What is the tariff?", doc_id=result.doc_id, top_k=3)
for hit in hits:
    print(hit.chunk.text, hit.chunk.page_start, hit.chunk.page_end)
```

## How the data moves

PDF -> pages -> chunks -> local embeddings -> persistent Chroma collection.
Question -> same embedding model -> optional doc_id filter -> vector search -> passages.

Each Chroma record stores the vector, original chunk text, and metadata: `doc_id`,
`chunk_id`, `page_start`, `page_end`, `section_title`. A missing section is stored as
an empty string and returned as None. The record ID combines doc_id and chunk_id,
so IDs from two different documents cannot overwrite each other.

Chroma's local HNSW index is a network connecting nearby vectors. Search follows
these connections rather than comparing the question with every stored chunk.
It is approximate: it may miss a relevant result. Cosine similarity compares vector
directions; Chroma returns cosine distance = 1 - similarity. Smaller distance means
a closer match. This score is not an accuracy percentage or proof of the answer.

Filtering by doc_id prevents a similar clause in Contract B from answering a question
about Contract A. Pages and section names help display and check the source later.
This filter is not an authorization system; user access controls are future work.

## Long chunks and model limits

MiniLM normally accepts fewer tokens than the Step 2 chunk target. We check the real
model tokenizer, split long text into smaller windows until each fits, embed every
window, and combine their vectors using a token-weighted average, then normalize.
The original chunk and page range remain unchanged. This avoids silently dropping
the end of a clause. The tradeoff is that averaging can blur different topics; a
future comparison should test smaller stored chunks or models with longer inputs.

## Re-ingestion and storage

`replace_document` validates the document and embeds every chunk before writing.
It uses upsert (insert new IDs, update existing IDs), then removes stale IDs belonging
only to that document. Repeating a successful ingest leaves the same number of records.
Empty content is rejected so a scan without OCR does not erase an existing document.

Document IDs hash PDF bytes: the same PDF under a different filename has the same ID.
An edited PDF has a new ID and is kept as a separate version. Filename-based replacement
is deliberately not inferred. Applications can call replace_document with a stable
logical ID and chunks carrying that same ID if explicit version replacement is needed.

The model name, resolved revision, vector size, input limit, and window strategy are
saved as collection metadata. A mismatch is rejected. Set a new CHROMA_COLLECTION
and re-ingest when changing the model; even equal-sized vectors can be incompatible.

CHROMA_PATH defaults to data/chroma relative to the project root, not the current shell
directory. PersistentClient saves automatically. Docker uses named volumes for Chroma
and the downloaded models. `docker compose down` keeps those volumes; `down -v` deletes them.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests -q
$env:RUN_MODEL_TESTS = "1"
.\.venv\Scripts\python.exe -m pytest backend/tests/test_retrieval.py -q -k real_local_model
Remove-Item Env:RUN_MODEL_TESTS
```

The default tests use real Chroma with predictable test vectors, so they can check
filtering, persistence in a new process, upserts, stale cleanup, model mismatch, and
failure handling without downloading model weights. Separate adapter tests check
that long text reaches the embedder without losing its tail. The opt-in test uses
the actual sentence-transformers model and the PDF-to-search pipeline.

## What would need changing at scale?

- CPU embedding can be slow; use background jobs and reuse one model instance.
- Vectors are prepared in batches but held in memory for one whole document before
  writing. Huge documents need staging and bounded-memory processing.
- A local file lock serializes this wrapper's reads and writes. This is a simple
  single-machine design, not a multi-server database deployment.
- Chroma upserts and deletes are separate operations. A crash during writing can
  leave a partial replacement. Retrying the same ingest repairs the final state;
  production needs staged document versions with an atomic active-version switch.
- Re-ingestion recomputes embeddings. A content/model cache could save that work.
- Similarity search can miss exact numbers, exceptions, and definitions. Later
  evaluation should compare hybrid keyword/vector search and reranking.

## Interview answers

1. What is an embedding? A numerical representation of meaning. We turn both the
   question and contract chunks into vectors using the same model, then find close matches.
2. Why Chroma and metadata? Chroma persists vectors and searches them efficiently.
   Metadata limits search to the chosen contract and carries pages for source checking.
3. How do you prevent duplicates? Stable document/chunk IDs plus upsert. After saving
   the new set, remove old IDs only for that document. A retry gives the same final set.

References: https://www.sbert.net/docs/package_reference/sentence_transformer/model.html
and https://docs.trychroma.com/docs/collections/configure
