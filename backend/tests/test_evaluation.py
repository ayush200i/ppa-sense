import json
import sys
from types import SimpleNamespace

import pytest

from app.config import PROJECT_ROOT
from app.evaluation import __main__ as cli
from app.evaluation.models import Dataset, QuestionRun, Review, Run
from app.evaluation.scoring import digest, score
from app.schemas.answer import AnswerResponse, Citation
from app.schemas.ingestion import ExtractedDocument, PageText
from app.schemas.terms import CommercialTerms, ExtractionResponse


@pytest.fixture
def dataset():
    return Dataset.model_validate_json(
        (PROJECT_ROOT / "evaluation/sonrisa-2019.json").read_text(encoding="utf-8")
    )


def prediction(dataset, generated=False):
    return Run(
        dataset_sha256=digest(dataset.model_dump(mode="json")),
        source_sha256=dataset.source_sha256,
        created_at="2026-10-10T00:00:00Z",
        settings={"top_k": 5, "generation_requested": generated},
        questions=[
            QuestionRun(
                id=q.id, retrieved_pages=q.pages, retrieved_chunk_ids=["chunk"], hit=q.answerable
            )
            for q in dataset.questions
        ],
    )


def by_name(metrics):
    return {metric.name: metric for metric in metrics}


def test_retrieval_denominator_excludes_redacted_question(dataset):
    run = prediction(dataset)
    run.questions[0].retrieved_pages = [131]
    # Recompute from pages: a saved hit flag must not override the actual evidence.
    run.questions[0].hit = True
    metrics = by_name(score(dataset, run, {}))
    assert metrics["Retrieval page hit@5"].score == "92.9% (13/14)"
    assert metrics["Citation accuracy (page + quote)"].score == "N/A"
    assert all(metrics[f"Field: {name}"].score == "N/A" for name in dataset.fields)


def test_empty_citations_not_perfect_and_abstention_not_retrieval_success(dataset):
    run = prediction(dataset, generated=True)
    for row in run.questions:
        row.answer = AnswerResponse(answer="not found in the contract", citations=[])
    metrics = by_name(score(dataset, run, {}))
    assert metrics["Citation accuracy (page + quote)"].score == "N/A"
    assert metrics["Citation coverage"].score == "0.0% (0/14)"
    assert metrics["Abstention accuracy"].score == "100.0% (1/1)"


def test_citation_requires_correct_document_page_and_quote(dataset):
    run = prediction(dataset, generated=True)
    run.questions[0].answer = AnswerResponse(
        answer="Example answer",
        citations=[
            Citation(doc_id=dataset.source_sha256, page=9, section="Cover", quote="Valid quote"),
            Citation(doc_id="another", page=9, section="Cover", quote="Valid quote"),
            Citation(doc_id=dataset.source_sha256, page=10, section="Other", quote="Other text"),
            Citation(doc_id=dataset.source_sha256, page=9, section="Cover", quote="Invented text"),
        ],
    )
    metrics = by_name(score(dataset, run, {9: "Valid\nquote", 10: "Other text"}))
    assert metrics["Citation accuracy (page + quote)"].score == "25.0% (1/4)"
    assert metrics["Citation quote validity"].score == "50.0% (2/4)"
    assert metrics["Citation semantic accuracy"].score == "N/A"


def test_missing_and_changed_predictions_rejected(dataset):
    run = prediction(dataset)
    run.questions.pop()
    with pytest.raises(ValueError, match="every question"):
        score(dataset, run, {})
    run = prediction(dataset)
    run.dataset_sha256 = "changed"
    with pytest.raises(ValueError, match="dataset"):
        score(dataset, run, {})
    run = prediction(dataset)
    run.source_sha256 = "changed"
    with pytest.raises(ValueError, match="different PDF"):
        score(dataset, run, {})


def review_for(dataset, run):
    return Review(
        run_sha256=digest(run.model_dump(mode="json")),
        reviewer="Test reviewer",
        answers={q.id: None for q in dataset.questions},
        citation_support={
            q.id: [None] * len(q.answer.citations) if q.answer else [] for q in run.questions
        },
        fields={name: None for name in dataset.fields},
    )


def test_semantic_review_and_coverage_bound_to_immutable_run(dataset):
    run = prediction(dataset, generated=True)
    run.questions[0].answer = AnswerResponse(
        answer="Incorrect conclusion",
        citations=[
            Citation(doc_id=dataset.source_sha256, page=9, section="Cover", quote="Real quote")
        ],
    )
    review = review_for(dataset, run)
    review.answers["Q01"] = False
    review.citation_support["Q01"] = [False]
    metrics = by_name(score(dataset, run, {9: "Real quote"}, review))
    assert metrics["Citation accuracy (page + quote)"].correct == 1
    assert metrics["Citation semantic accuracy"].score == "0.0% (0/1)"
    assert metrics["Answer review coverage"].score == "6.7% (1/15)"
    run.questions[0].answer.answer = "Changed answer"
    with pytest.raises(ValueError, match="different predictions"):
        score(dataset, run, {9: "Real quote"}, review)


def test_field_exact_comparison_and_manual_rubric_not_lexical_matching(dataset):
    run = prediction(dataset, generated=True)
    fields = {
        name: {
            "value": None,
            "sources": [],
            "confidence": 0,
            "status": "not_found",
            "reason": "Missing",
            "attempts": 1,
        }
        for name in dataset.fields
    }
    fields["term_years"] = {
        "value": 20,
        "sources": [
            {
                "doc_id": dataset.source_sha256,
                "page": 9,
                "section": "Cover",
                "quote": "Twenty years",
            }
        ],
        "confidence": 0.9,
        "status": "validated",
        "reason": "Direct",
        "attempts": 1,
    }
    run.extraction = ExtractionResponse(
        doc_id=dataset.source_sha256, terms=CommercialTerms(**fields), needs_review=[], warnings=[]
    )
    metrics = by_name(score(dataset, run, {9: "Twenty years"}))
    assert metrics["Field: term_years"].score == "100.0% (1/1)"
    assert metrics["Field: effective_date"].score == "0.0% (0/1)"
    assert metrics["Field: payment_terms"].score == "N/A"
    review = review_for(dataset, run)
    review.fields["payment_terms"] = False
    assert by_name(score(dataset, run, {}, review))["Field: payment_terms"].score == "0.0% (0/1)"


def test_dataset_must_have_all_fields_and_valid_page_scope(dataset):
    data = dataset.model_dump()
    data["page_end"] = 90
    with pytest.raises(ValueError, match="100"):
        Dataset.model_validate(data)
    data = dataset.model_dump()
    data["questions"][0]["pages"] = [1]
    with pytest.raises(ValueError, match="within"):
        Dataset.model_validate(data)


def test_cli_isolates_index_scope_and_never_calls_llm_by_default(dataset, monkeypatch, tmp_path):
    captured = []
    fake_document = ExtractedDocument(
        doc_id=dataset.source_sha256,
        pages=[PageText(page_number=n, text=f"Clause on page {n}.") for n in range(1, 140)],
    )
    monkeypatch.setattr(cli, "extract_pdf", lambda path: fake_document)
    store = SimpleNamespace(
        embedder=SimpleNamespace(fingerprint="fake-test-embedder"),
        replace_document=lambda doc_id, chunks: captured.extend(chunks),
        search=lambda *args, **kwargs: [],
    )

    def create_store(settings):
        assert "evaluation" in str(settings.chroma_path)
        return store

    monkeypatch.setattr(cli, "create_store", create_store)
    monkeypatch.setattr(
        cli, "create_answer_pipeline", lambda *a: pytest.fail("No live model allowed")
    )
    output = tmp_path / "run.json"
    monkeypatch.setattr(sys, "argv", ["evaluate", "--output", str(output)])
    assert cli.main() == 0
    assert captured
    assert all(9 <= c.page_start <= c.page_end <= 131 for c in captured)
    report = json.loads(output.read_text())
    assert report["settings"]["generation_requested"] is False
    assert len(report["questions"]) == 15


def test_existing_output_rejected_before_loading_models_or_pdf(monkeypatch, tmp_path):
    output = tmp_path / "existing.json"
    output.write_text("preserve me", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["evaluate", "--generate", "--output", str(output)])
    monkeypatch.setattr(cli, "extract_pdf", lambda path: pytest.fail("Must reject output first"))
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert output.read_text(encoding="utf-8") == "preserve me"
