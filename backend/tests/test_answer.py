from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.agent.anthropic_client import SYSTEM_PROMPT, AnswerModelError, AnthropicGenerator
from app.agent.evidence import (
    CitationValidationError,
    EvidenceUnavailableError,
    prepare_evidence,
    verify_and_render,
)
from app.agent.pipeline import AnswerPipeline
from app.api.answer import get_pipeline
from app.config import Settings
from app.ingestion import chunk_document
from app.main import create_app
from app.schemas.answer import (
    NOT_FOUND,
    AnswerRequest,
    Claim,
    DraftAnswer,
    EvidenceReference,
)
from app.schemas.ingestion import ExtractedDocument, PageText
from app.schemas.retrieval import SearchHit


def sample_hits() -> list[SearchHit]:
    document = ExtractedDocument(
        doc_id="contract-A",
        pages=[
            PageText(page_number=4, text="1. Tariff\nThe price is USD 45 per MWh."),
            PageText(page_number=5, text="Payment is due within 30 days."),
        ],
    )
    return [SearchHit(chunk=c, cosine_distance=0.1) for c in chunk_document(document)]


def draft(source="S1", quote="The price is USD 45 per MWh.", text="The price is USD 45 per MWh."):
    return DraftAnswer(
        found=True,
        claims=[Claim(text=text, evidence=[EvidenceReference(source_id=source, quote=quote)])],
    )


class FakeRetriever:
    def __init__(self, hits):
        self.hits = hits
        self.calls = []

    def search(self, question, doc_id=None, top_k=5):
        self.calls.append((question, doc_id, top_k))
        return self.hits


class FakeGenerator:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def generate(self, question, evidence):
        self.calls.append((question, evidence))
        return self.result


def test_graph_answer_has_verified_inline_and_structured_citations():
    retriever = FakeRetriever(sample_hits())
    generator = FakeGenerator(draft())
    pipeline = AnswerPipeline(retriever, generator)
    response = pipeline.answer(AnswerRequest(question="What is the price?", doc_id="contract-A"))
    assert retriever.calls == [("What is the price?", "contract-A", 5)]
    assert response.answer.endswith("[contract-A, page 4, section 1. Tariff]")
    assert response.citations[0].model_dump() == {
        "doc_id": "contract-A",
        "page": 4,
        "section": "1. Tariff",
        "quote": "The price is USD 45 per MWh.",
    }
    assert len(generator.calls) == 1


def test_page_verification_rejects_quote_from_other_page():
    sources = prepare_evidence(sample_hits())
    assert [s.page for s in sources] == [4, 5]
    with pytest.raises(CitationValidationError, match="cited page"):
        verify_and_render(draft(quote="Payment is due within 30 days."), sources)
    answer = verify_and_render(
        draft("S2", "Payment is due within 30 days.", "Pay within 30 days."), sources
    )
    assert answer.citations[0].page == 5


@pytest.mark.parametrize(
    "result",
    [
        draft(source="S99"),
        draft(quote="The price is USD 99 per MWh."),
        draft(text="Price is 45 [fake, page 999, section X]"),
    ],
)
def test_fabricated_citations_fail_closed(result):
    with pytest.raises(CitationValidationError):
        verify_and_render(result, prepare_evidence(sample_hits()))


def test_empty_retrieval_skips_model_and_missing_evidence_abstains():
    generator = FakeGenerator(draft())
    response = AnswerPipeline(FakeRetriever([]), generator).answer(AnswerRequest(question="Price?"))
    assert response.answer == NOT_FOUND and response.citations == []
    assert generator.calls == []
    missing = FakeGenerator(DraftAnswer(found=False, claims=[]))
    response = AnswerPipeline(FakeRetriever(sample_hits()), missing).answer(
        AnswerRequest(question="Who is the insurer?")
    )
    assert response.answer == NOT_FOUND and response.citations == []


def test_legacy_multi_page_chunks_require_reingestion():
    hit = sample_hits()[0]
    hit.chunk = hit.chunk.model_copy(update={"page_spans": []})
    with pytest.raises(EvidenceUnavailableError, match="Re-ingest"):
        prepare_evidence([hit])


def test_rerank_limits_evidence_and_keeps_filter():
    a = sample_hits()[0]
    b = a.model_copy(
        update={
            "chunk": a.chunk.model_copy(
                update={
                    "chunk_id": "other",
                    "text": "Termination and breach.",
                    "page_start": 1,
                    "page_end": 1,
                    "section_title": "Termination",
                    "page_spans": [],
                }
            ),
            "cosine_distance": 0.09,
        }
    )
    retriever = FakeRetriever([b, a])
    generator = FakeGenerator(draft())
    AnswerPipeline(retriever, generator).answer(
        AnswerRequest(
            question="price tariff",
            doc_id="contract-A",
            top_k=1,
            rerank=True,
        )
    )
    assert retriever.calls[0][1:] == ("contract-A", 3)
    assert generator.calls[0][1][0].section == "1. Tariff"


def test_wrong_document_is_blocked():
    with pytest.raises(ValueError, match="different document"):
        AnswerPipeline(FakeRetriever(sample_hits()), FakeGenerator(draft())).answer(
            AnswerRequest(question="Price?", doc_id="contract-B")
        )


def test_anthropic_adapter_request_and_output():
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            stop_reason="tool_use",
            content=[
                SimpleNamespace(type="tool_use", name="submit_answer", input=draft().model_dump())
            ],
        )

    client = SimpleNamespace(messages=SimpleNamespace(create=create))
    generator = AnthropicGenerator(client, "model-from-environment")
    assert generator.generate("Price?", prepare_evidence(sample_hits())) == draft()
    assert captured["model"] == "model-from-environment"
    assert captured["tool_choice"]["name"] == "submit_answer"
    assert "untrusted DATA" in SYSTEM_PROMPT
    assert "not found in the contract" in SYSTEM_PROMPT
    assert "[doc, page X, section Y]" in SYSTEM_PROMPT


@pytest.mark.parametrize("reason", ["max_tokens", "refusal", "end_turn"])
def test_incomplete_model_output_is_error_not_missing(reason):
    client = SimpleNamespace(
        messages=SimpleNamespace(
            create=lambda **kwargs: SimpleNamespace(stop_reason=reason, content=[])
        )
    )
    with pytest.raises(AnswerModelError):
        AnthropicGenerator(client, "test").generate("Price?", [])


def test_response_structure_requires_evidence():
    with pytest.raises(ValidationError):
        DraftAnswer(found=True, claims=[])
    with pytest.raises(ValidationError):
        Claim(text="Unsupported", evidence=[])


def test_answer_http_success_and_validation_error():
    app = create_app(Settings(_env_file=None, environment="test"))
    generator = FakeGenerator(draft())
    app.dependency_overrides[get_pipeline] = lambda: AnswerPipeline(
        FakeRetriever(sample_hits()),
        generator,
    )
    with TestClient(app) as client:
        response = client.post("/api/answer", json={"question": "Price?", "doc_id": "contract-A"})
        assert response.status_code == 200
        assert response.json()["citations"][0]["page"] == 4
        assert client.post("/api/answer", json={"question": " "}).status_code == 422
        generator.result = draft(quote="invented")
        rejected = client.post("/api/answer", json={"question": "Price?"})
        assert rejected.status_code == 502
        assert "invented" not in rejected.text


def test_no_credentials_does_not_break_health():
    app = create_app(
        Settings(_env_file=None, environment="test", llm_model="", anthropic_api_key="")
    )
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
        assert client.post("/api/answer", json={"question": "Price?"}).status_code == 503
