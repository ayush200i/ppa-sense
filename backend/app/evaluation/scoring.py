import hashlib
import json
from collections.abc import Mapping
from typing import Any

from app.agent.evidence import normalize
from app.evaluation.models import Dataset, Metric, Review, Run
from app.schemas.answer import NOT_FOUND, Citation


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def citation_exists(citation: Citation, doc_id: str, pages: Mapping[int, str]) -> bool:
    quote = normalize(citation.quote)
    return (
        bool(quote)
        and citation.doc_id == doc_id
        and citation.page in pages
        and quote in normalize(pages[citation.page])
    )


def score(
    dataset: Dataset, run: Run, pages: Mapping[int, str], review: Review | None = None
) -> list[Metric]:
    if run.dataset_sha256 != digest(dataset.model_dump(mode="json")):
        raise ValueError("Predictions belong to a different dataset version")
    if run.source_sha256 != dataset.source_sha256:
        raise ValueError("Predictions belong to a different PDF")
    ids = [q.id for q in dataset.questions]
    if [q.id for q in run.questions] != ids:
        raise ValueError("Predictions must cover every question exactly once, in dataset order")
    if run.extraction and run.extraction.doc_id != dataset.source_sha256:
        raise ValueError("Extraction belongs to a different document")
    if review:
        if review.run_sha256 != digest(run.model_dump(mode="json")):
            raise ValueError("Review belongs to different predictions")
        if set(review.answers) != set(ids) or set(review.citation_support) != set(ids):
            raise ValueError("Review must cover all question IDs")
        if set(review.fields) != set(dataset.fields):
            raise ValueError("Review must cover every extraction field")
        for prediction in run.questions:
            count = len(prediction.answer.citations) if prediction.answer else 0
            if len(review.citation_support[prediction.id]) != count:
                raise ValueError("Review citation count does not match predictions")
        if not review.reviewer.strip():
            raise ValueError("Name the reviewer before scoring a review")

    retrieval = Metric(
        name=f"Retrieval page hit@{run.settings['top_k']}",
        note="Answerable questions only; any gold page in top-k chunks",
    )
    exact_cites = Metric(
        name="Citation accuracy (page + quote)", note="Mechanical proxy, not semantic support"
    )
    grounded = Metric(
        name="Citation quote validity", note="Correct document and verbatim page text"
    )
    coverage = Metric(
        name="Citation coverage", note="Answerable questions with at least one citation"
    )
    abstention = Metric(name="Abstention accuracy", note="Unanswerable questions only")
    semantic = Metric(
        name="Citation semantic accuracy", note="Reviewed subset; see review coverage"
    )
    reviewed = Metric(name="Citation review coverage")
    answers = Metric(name="Answer accuracy (human review)", note="Reviewed subset only")
    answer_coverage = Metric(name="Answer review coverage")
    generated = bool(run.settings.get("generation_requested"))
    for gold, prediction in zip(dataset.questions, run.questions, strict=True):
        if gold.answerable:
            retrieval.total += 1
            retrieval.correct += bool(set(gold.pages) & set(prediction.retrieved_pages))
        if not generated:
            continue
        citations = prediction.answer.citations if prediction.answer else []
        if gold.answerable:
            coverage.total += 1
            coverage.correct += bool(citations)
        else:
            abstention.total += 1
            abstention.correct += bool(
                prediction.answer and prediction.answer.answer == NOT_FOUND and not citations
            )
        answer_coverage.total += 1
        if review and review.answers[gold.id] is not None:
            if prediction.answer is None:
                raise ValueError("Cannot manually grade an answer that was never returned")
            answers.total += 1
            answers.correct += bool(review.answers[gold.id])
            answer_coverage.correct += 1
        for index, citation in enumerate(citations):
            valid = citation_exists(citation, dataset.source_sha256, pages)
            grounded.total += 1
            grounded.correct += valid
            exact_cites.total += 1
            exact_cites.correct += valid and gold.answerable and citation.page in gold.pages
            reviewed.total += 1
            label = review.citation_support[gold.id][index] if review else None
            if label is not None:
                reviewed.correct += 1
                semantic.total += 1
                semantic.correct += valid and label
    metrics = [
        retrieval,
        exact_cites,
        grounded,
        coverage,
        abstention,
        semantic,
        reviewed,
        answers,
        answer_coverage,
    ]
    for name, gold in dataset.fields.items():
        metric = Metric(name=f"Field: {name}", note=f"{gold.mode}; one document")
        if not generated:
            metric.note += "; generation not run"
        elif run.extraction is None:
            metric.total = 1
            metric.note += "; extraction failed"
        else:
            field = getattr(run.extraction.terms, name)
            if gold.mode == "exact":
                metric.total = 1
                # Exact fields are simple typed values; prose is never compared as strings.
                metric.correct = int(
                    field.model_dump(mode="json")["value"] == gold.expected
                    and field.status in {"validated", "low_confidence"}
                )
            elif field.status in {"error", "invalid_evidence"}:
                metric.total = 1
                metric.note += "; pipeline failure"
            elif review and review.fields[name] is not None:
                metric.total = 1
                metric.correct = int(review.fields[name])
            else:
                metric.note += "; needs semantic review"
        metrics.append(metric)
    return metrics


def table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [max(len(row[i]) for row in [headers, *rows]) for i in range(len(headers))]

    def line(row: list[str]) -> str:
        return " | ".join(value.ljust(width) for value, width in zip(row, widths, strict=True))

    return "\n".join([line(headers), "-+-".join("-" * n for n in widths), *(line(r) for r in rows)])
