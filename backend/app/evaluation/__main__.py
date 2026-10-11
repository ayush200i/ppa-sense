import argparse
import hashlib
import json
import sys
import urllib.request
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

from app.agent.pipeline import rerank
from app.agent.service import create_answer_pipeline, create_terms_pipeline
from app.config import PROJECT_ROOT, Settings
from app.evaluation.models import Dataset, QuestionRun, Review, Run
from app.evaluation.scoring import digest, score, table
from app.ingestion import chunk_document, extract_pdf
from app.retrieval.service import create_store
from app.schemas.answer import AnswerRequest
from app.schemas.ingestion import ChunkingConfig
from app.schemas.terms import ExtractionRequest


def download(dataset: Dataset, path: Path) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".download")
    print("Downloading the public source PDF...", flush=True)
    try:
        request = urllib.request.Request(
            dataset.source_url, headers={"User-Agent": "PPA-Sense/0.1"}
        )
        with (
            urllib.request.urlopen(request, timeout=30) as response,
            temporary.open("wb") as stream,
        ):
            size = 0
            while block := response.read(1024 * 1024):
                size += len(block)
                if size > 100 * 1024 * 1024:
                    raise ValueError("Source PDF exceeds 100 MiB")
                stream.write(block)
        if hashlib.sha256(temporary.read_bytes()).hexdigest() != dataset.source_sha256:
            raise ValueError("Downloaded source changed; review its labels before evaluating")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate a source-checked PPA development set")
    parser.add_argument(
        "--dataset", type=Path, default=PROJECT_ROOT / "evaluation" / "sonrisa-2019.json"
    )
    parser.add_argument(
        "--pdf", type=Path, default=PROJECT_ROOT / "data/evaluation/ava-sonrisa-scarlett.pdf"
    )
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--top-k", type=int, choices=range(1, 11), default=5)
    parser.add_argument("--rerank", action="store_true")
    parser.add_argument("--chunk-tokens", type=int, default=800)
    parser.add_argument("--overlap-tokens", type=int, default=100)
    parser.add_argument(
        "--generate", action="store_true", help="Call the configured LLM (may cost money)"
    )
    parser.add_argument(
        "--predictions", type=Path, help="Score an existing run without model calls"
    )
    parser.add_argument("--review", type=Path, help="Completed semantic review bound to that run")
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT
        / "data/evaluation/runs"
        / f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')}.json",
    )
    args = parser.parse_args()
    artifacts = [
        args.output,
        args.output.with_suffix(".metrics.json"),
        args.output.with_suffix(".review-template.json"),
    ]
    if any(path.exists() for path in artifacts):
        parser.error("Output artifacts already exist; choose a new --output before running")
    if args.generate and args.predictions:
        parser.error("Choose --generate or --predictions, not both")
    if args.review and not args.predictions:
        parser.error("Use --review with the existing --predictions it was created for")
    dataset = Dataset.model_validate_json(args.dataset.read_text(encoding="utf-8"))
    if args.download:
        download(dataset, args.pdf)
    document = extract_pdf(args.pdf)
    if document.doc_id != dataset.source_sha256 or len(document.pages) != dataset.source_page_count:
        raise ValueError(
            "PDF hash/page count mismatch: these gold labels belong to a different file"
        )
    document.pages = [
        p for p in document.pages if dataset.page_start <= p.page_number <= dataset.page_end
    ]
    pages = {p.page_number: p.text for p in document.pages}
    if args.predictions:
        run = Run.model_validate_json(args.predictions.read_text(encoding="utf-8"))
    else:
        settings = Settings()
        if args.generate and (
            not settings.llm_model or not settings.anthropic_api_key.get_secret_value()
        ):
            raise ValueError("Set LLM_MODEL and ANTHROPIC_API_KEY before --generate; no calls made")
        config = ChunkingConfig(target_tokens=args.chunk_tokens, overlap_tokens=args.overlap_tokens)
        # Keep experiments out of the user-facing application's Chroma collection.
        settings.chroma_path = PROJECT_ROOT / "data/evaluation/chroma"
        settings.chroma_collection = "evaluation_chunks"
        print("Indexing only the PPA page range with local embeddings...", flush=True)
        store = create_store(settings)
        chunks = chunk_document(document, config)
        store.replace_document(document.doc_id, chunks)
        answer_pipeline = create_answer_pipeline(settings, store) if args.generate else None
        code_hash = digest(
            {
                str(p.relative_to(PROJECT_ROOT / "backend")): hashlib.sha256(
                    p.read_bytes()
                ).hexdigest()
                for p in sorted((PROJECT_ROOT / "backend/app").rglob("*.py"))
            }
        )
        run = Run(
            dataset_sha256=digest(dataset.model_dump(mode="json")),
            source_sha256=document.doc_id,
            created_at=datetime.now(UTC).isoformat(),
            questions=[],
            settings={
                "top_k": args.top_k,
                "rerank": args.rerank,
                "chunking": config.model_dump(),
                "chunks": len(chunks),
                "indexed_page_start": dataset.page_start,
                "indexed_page_end": dataset.page_end,
                "embedding_fingerprint": store.embedder.fingerprint,
                "generation_requested": args.generate,
                "model": settings.llm_model if args.generate else None,
                "provider": settings.llm_provider if args.generate else None,
                "llm_max_tokens": settings.llm_max_tokens,
                "extraction_max_retries": 1,
                "code_sha256": code_hash,
                "packages": {
                    name: version(name)
                    for name in (
                        "PyMuPDF",
                        "chromadb",
                        "sentence-transformers",
                        "langgraph",
                        "anthropic",
                    )
                },
            },
        )
        for gold in dataset.questions:
            print(f"Retrieving {gold.id}...", flush=True)
            hits = store.search(
                gold.question,
                doc_id=document.doc_id,
                top_k=args.top_k * 3 if args.rerank else args.top_k,
            )
            if args.rerank:
                hits = rerank(gold.question, hits)
            hits = hits[: args.top_k]
            retrieved_pages = sorted({span.page for hit in hits for span in hit.chunk.page_spans})
            prediction = QuestionRun(
                id=gold.id,
                retrieved_pages=retrieved_pages,
                retrieved_chunk_ids=[h.chunk.chunk_id for h in hits],
                hit=bool(set(gold.pages) & set(retrieved_pages)) if gold.answerable else None,
            )
            if answer_pipeline:
                try:
                    prediction.answer = answer_pipeline.answer(
                        AnswerRequest(
                            question=gold.question,
                            doc_id=document.doc_id,
                            top_k=args.top_k,
                            rerank=args.rerank,
                        )
                    )
                except Exception as exc:
                    prediction.error = type(exc).__name__
            run.questions.append(prediction)
        if args.generate:
            print("Extracting all commercial fields...", flush=True)
            try:
                run.extraction = create_terms_pipeline(settings, store).extract(
                    ExtractionRequest(doc_id=document.doc_id, top_k=args.top_k)
                )
            except Exception as exc:
                run.extraction_error = type(exc).__name__
    review = (
        Review.model_validate_json(args.review.read_text(encoding="utf-8")) if args.review else None
    )
    metrics = score(dataset, run, pages, review)
    print("\n" + table(["Metric", "Result", "Scope"], [[m.name, m.score, m.note] for m in metrics]))
    print(
        "\n"
        + table(
            ["Question", "Gold PDF pages", "Retrieved PDF pages", "Hit"],
            [
                [
                    g.id,
                    ",".join(map(str, g.pages)),
                    ",".join(map(str, p.retrieved_pages)),
                    "N/A"
                    if not g.answerable
                    else "yes"
                    if set(g.pages) & set(p.retrieved_pages)
                    else "NO",
                ]
                for g, p in zip(dataset.questions, run.questions, strict=True)
            ],
        )
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(run.model_dump_json(indent=2), encoding="utf-8")
    args.output.with_suffix(".metrics.json").write_text(
        json.dumps([dict(m.model_dump(), result=m.score) for m in metrics], indent=2),
        encoding="utf-8",
    )
    template = Review(
        run_sha256=digest(run.model_dump(mode="json")),
        answers={q.id: None for q in run.questions},
        citation_support={
            q.id: [None] * len(q.answer.citations) if q.answer else [] for q in run.questions
        },
        fields={name: None for name in dataset.fields},
    )
    args.output.with_suffix(".review-template.json").write_text(
        template.model_dump_json(indent=2), encoding="utf-8"
    )
    print(f"\nSaved run: {args.output}\nSingle-document DEVELOPMENT set; not a held-out benchmark.")
    if not run.settings["generation_requested"]:
        print("No LLM calls made. Citation/extraction scores are N/A, not zero or 100%.")
    failures = sum(q.error is not None for q in run.questions)
    if failures or run.extraction_error:
        print(
            f"Generation failures: {failures} answers; extraction: {run.extraction_error or 'none'}"
        )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError) as exc:
        print(f"Evaluation stopped: {exc}", file=sys.stderr)
        sys.exit(1)
