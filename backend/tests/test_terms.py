from collections import Counter
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.agent.anthropic_client import AnswerModelError
from app.agent.term_model import AnthropicTermExtractor
from app.agent.term_validation import validate_field
from app.agent.terms_pipeline import TermsPipeline
from app.api.terms import get_terms_pipeline
from app.config import Settings
from app.ingestion import chunk_document
from app.main import create_app
from app.schemas.answer import Evidence
from app.schemas.ingestion import ExtractedDocument, PageText
from app.schemas.retrieval import SearchHit
from app.schemas.terms import (
    FIELD_TYPES,
    CommercialTerms,
    ExtractionRequest,
    FieldDraft,
)

TEXT = """POWER PURCHASE AGREEMENT
Seller: Sun Ltd. Buyer: Grid Ltd.
Effective date: 2026-01-01.
Initial term: 20 years from COD.
Scheduled COD: 2027-01-01, subject to commissioning tests.
Tariff: USD 45 per MWh, excluding taxes.
Annual escalation: 2 percent.
Delivery point: Grid Substation A.
Guaranteed output: 100 GWh per year, subject to weather adjustments.
Either party may terminate for material breach after a 30-day cure period.
Delay liquidated damages: USD 1000 per day, capped at USD 100000.
Change in law: additional compliance costs are reimbursed by the buyer.
Force majeure: floods excuse delay subject to prompt notice and mitigation.
Invoices are payable within 30 days of receipt.
"""

VALUES = {
    "contract_type": "PPA",
    "parties": [{"name": "Sun Ltd.", "role": "Seller"}, {"name": "Grid Ltd.", "role": "Buyer"}],
    "effective_date": "2026-01-01",
    "term_years": 20,
    "COD": {
        "scheduled_date": "2027-01-01",
        "actual_date": None,
        "condition": "Subject to commissioning tests",
    },
    "price_tariff": {
        "kind": "tariff",
        "amount": 45,
        "currency": "USD",
        "unit": "MWh",
        "basis": "Excluding taxes",
    },
    "escalation": "2 percent annually",
    "delivery_point": "Grid Substation A",
    "guaranteed_output": "100 GWh per year, subject to weather adjustments",
    "termination_rights": "Material breach after a 30-day cure period",
    "liquidated_damages": "USD 1000 per day, capped at USD 100000",
    "change_in_law": "Buyer reimburses additional compliance costs",
    "force_majeure": "Floods excuse delay subject to notice and mitigation",
    "payment_terms": "Invoices payable within 30 days of receipt",
}
QUOTES = dict(zip(FIELD_TYPES, TEXT.strip().splitlines(), strict=True))


class Retriever:
    def __init__(self, text=TEXT, empty=False):
        chunks = chunk_document(
            ExtractedDocument(doc_id="a", pages=[PageText(page_number=3, text=text)])
        )
        self.hits = [] if empty else [SearchHit(chunk=c, cosine_distance=0.1) for c in chunks]
        self.calls = []

    def search(self, query, doc_id=None, top_k=4):
        self.calls.append((query, doc_id, top_k))
        return self.hits


class Extractor:
    def __init__(self, overrides=None):
        self.calls = Counter()
        self.overrides = overrides or {}
        self.feedback = []

    def extract(self, field, guidance, sources, schema, feedback):
        self.calls[field] += 1
        self.feedback.append(feedback)
        quote = QUOTES[field]
        source = next(s for s in sources if quote in s.text)
        result = {
            "value": VALUES[field],
            "evidence": [{"source_id": source.source_id, "quote": quote}],
            "confidence": 0.9,
            "reason": "Direct clause",
        }
        if field in self.overrides:
            return self.overrides[field](self.calls[field], result)
        return result


def test_complete_schema_and_every_field_has_provenance():
    extractor = Extractor()
    retriever = Retriever()
    response = TermsPipeline(retriever, extractor).extract(ExtractionRequest(doc_id="a"))
    assert isinstance(response.terms, CommercialTerms)
    assert response.needs_review == []
    assert len(CommercialTerms.model_fields) == 14
    assert response.terms.contract_type.value == "PPA"
    assert response.terms.effective_date.value.isoformat() == "2026-01-01"
    assert response.terms.price_tariff.value.amount == 45
    for field in FIELD_TYPES:
        result = getattr(response.terms, field)
        assert result.status == "validated" and result.confidence == 0.9
        assert result.sources[0].page == 3 and result.sources[0].quote in TEXT
        assert result.attempts == 1 and extractor.calls[field] == 1
    assert all(doc_id == "a" for _, doc_id, _ in retriever.calls)
    assert any("PPA electricity tariff" in query for query, _, _ in retriever.calls)


def test_invalid_quote_retried_with_more_evidence_then_recovered():
    def first_bad(attempt, result):
        if attempt == 1:
            result["evidence"][0]["quote"] = "Tariff: USD 999 per MWh."
        return result

    extractor = Extractor({"price_tariff": first_bad})
    retriever = Retriever()
    response = TermsPipeline(retriever, extractor).extract(ExtractionRequest(doc_id="a"))
    assert response.terms.price_tariff.attempts == 2
    assert response.terms.price_tariff.value.amount == 45
    assert extractor.calls["price_tariff"] == 2
    assert any(k == 8 for _, _, k in retriever.calls)
    assert any("exact-page" in text for text in extractor.feedback if text)


def test_low_confidence_flag_keeps_verified_value():
    extractor = Extractor({"term_years": lambda attempt, result: {**result, "confidence": 0.4}})
    response = TermsPipeline(Retriever(), extractor).extract(ExtractionRequest(doc_id="a"))
    assert response.terms.term_years.status == "low_confidence"
    assert response.terms.term_years.value == 20
    assert response.needs_review == ["term_years"]
    assert response.terms.term_years.attempts == 2


def test_invalid_evidence_never_releases_value_and_other_fields_continue():
    def forged(attempt, result):
        result["evidence"][0]["source_id"] = "invented"
        return result

    response = TermsPipeline(Retriever(), Extractor({"COD": forged})).extract(
        ExtractionRequest(doc_id="a", max_retries=0)
    )
    assert response.terms.COD.value is None and response.terms.COD.sources == []
    assert response.terms.COD.confidence == 0
    assert response.terms.COD.status == "invalid_evidence"
    assert response.terms.payment_terms.status == "validated"


def test_all_missing_bounded_retries_and_no_model_calls():
    extractor = Extractor()
    response = TermsPipeline(Retriever(empty=True), extractor).extract(
        ExtractionRequest(doc_id="unknown", max_retries=2)
    )
    assert len(response.needs_review) == 14
    assert extractor.calls == Counter()
    for name in FIELD_TYPES:
        result = getattr(response.terms, name)
        assert result.status == "not_found" and result.attempts == 3
        assert result.value is None and result.confidence == 0 and result.sources == []


def test_classification_can_retry_and_does_not_force_other_contract_types():
    def unclear(attempt, result):
        return {"value": None, "evidence": [], "confidence": 0, "reason": "Hybrid agreement"}

    extractor = Extractor({"contract_type": unclear})
    retriever = Retriever()
    response = TermsPipeline(retriever, extractor).extract(ExtractionRequest(doc_id="a"))
    assert response.terms.contract_type.value is None
    assert extractor.calls["contract_type"] == 2
    assert len(response.terms.model_dump()) == 14
    assert not any("PPA electricity tariff" in query for query, _, _ in retriever.calls)


def test_provider_failure_is_error_not_not_found():
    def broken(attempt, result):
        raise AnswerModelError("provider details must not leak")

    response = TermsPipeline(Retriever(), Extractor({"payment_terms": broken})).extract(
        ExtractionRequest(doc_id="a", max_retries=0)
    )
    assert response.terms.payment_terms.status == "error"
    assert "provider details" not in response.model_dump_json()


@pytest.mark.parametrize("value", ["not-a-date", "2026-02-30"])
def test_invalid_dates_rejected(value):
    result = validate_field(
        "effective_date",
        {
            "value": value,
            "evidence": [{"source_id": "S1", "quote": "Effective date"}],
            "confidence": 0.9,
            "reason": "",
        },
        [Evidence(source_id="S1", doc_id="a", page=1, section="Dates", text="Effective date")],
        0.75,
        1,
    )
    assert result.status == "error" and result.value is None


def test_confidence_and_request_limits():
    with pytest.raises(ValidationError):
        ExtractionRequest(doc_id="a", max_retries=10)
    with pytest.raises(ValidationError):
        FieldDraft[str](value="PPA", evidence=[], confidence=1.5, reason="")


def test_epc_classification_changes_price_query():
    title = "ENGINEERING PROCUREMENT CONSTRUCTION AGREEMENT"
    price = "Contract price: USD 1000000 lump sum."

    class EPCExtractor(Extractor):
        def extract(self, field, guidance, sources, schema, feedback):
            if field not in {"contract_type", "price_tariff"}:
                return super().extract(field, guidance, sources, schema, feedback)
            self.calls[field] += 1
            quote = title if field == "contract_type" else price
            source = next(s for s in sources if quote in s.text)
            value = (
                "EPC"
                if field == "contract_type"
                else {
                    "kind": "contract_price",
                    "amount": 1000000,
                    "currency": "USD",
                    "unit": "lump sum",
                    "basis": "Construction price",
                }
            )
            return {
                "value": value,
                "evidence": [{"source_id": source.source_id, "quote": quote}],
                "confidence": 0.9,
                "reason": "Direct clause",
            }

    text = TEXT.replace(QUOTES["contract_type"], title).replace(QUOTES["price_tariff"], price)
    retriever = Retriever(text)
    response = TermsPipeline(retriever, EPCExtractor()).extract(ExtractionRequest(doc_id="a"))
    assert response.terms.contract_type.value == "EPC"
    assert response.terms.price_tariff.value.kind == "contract_price"
    assert any("EPC total contract price" in query for query, _, _ in retriever.calls)


def test_extraction_blocks_wrong_document_and_wrong_page_quote():
    with pytest.raises(ValueError, match="different document"):
        TermsPipeline(Retriever(), Extractor()).extract(ExtractionRequest(doc_id="b"))
    sources = [
        Evidence(source_id="S1", doc_id="a", page=1, section="Price", text="Tariff is 45."),
        Evidence(source_id="S2", doc_id="a", page=2, section="Pay", text="Pay within 30 days."),
    ]
    result = validate_field(
        "payment_terms",
        {
            "value": "30 days",
            "evidence": [{"source_id": "S1", "quote": "Pay within 30 days."}],
            "confidence": 1,
            "reason": "",
        },
        sources,
        0.75,
        1,
    )
    assert result.status == "invalid_evidence" and result.value is None


def test_adapter_uses_field_specific_schema_and_feedback():
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            stop_reason="tool_use",
            content=[
                SimpleNamespace(
                    type="tool_use",
                    name="submit_field",
                    input={"value": None, "evidence": [], "confidence": 0, "reason": "missing"},
                )
            ],
        )

    client = SimpleNamespace(messages=SimpleNamespace(create=create))
    adapter = AnthropicTermExtractor(client, "configured-model")
    payload = adapter.extract(
        "effective_date",
        "Use ISO dates",
        [],
        FieldDraft[FIELD_TYPES["effective_date"]],
        "previous quote invalid",
    )
    assert payload["value"] is None
    assert captured["model"] == "configured-model"
    assert "previous quote invalid" in captured["messages"][0]["content"]
    assert (
        captured["tools"][0]["input_schema"]
        == FieldDraft[FIELD_TYPES["effective_date"]].model_json_schema()
    )


def test_http_extraction_and_missing_credentials():
    app = create_app(Settings(_env_file=None, llm_model="", anthropic_api_key=""))
    with TestClient(app) as client:
        assert client.post("/api/extract", json={"doc_id": "a"}).status_code == 503
        app.dependency_overrides[get_terms_pipeline] = lambda: TermsPipeline(
            Retriever(), Extractor()
        )
        response = client.post("/api/extract", json={"doc_id": "a", "max_retries": 0})
        assert response.status_code == 200
        assert response.json()["terms"]["COD"]["sources"][0]["page"] == 3
        assert client.post("/api/extract", json={"doc_id": " "}).status_code == 422
