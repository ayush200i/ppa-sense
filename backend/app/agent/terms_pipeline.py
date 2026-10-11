from typing import Any, Literal

from anthropic import APIError
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from app.agent.anthropic_client import AnswerModelError
from app.agent.evidence import prepare_evidence
from app.agent.pipeline import Retriever
from app.agent.term_fields import FIELD_GUIDANCE, FIELD_QUERIES
from app.agent.term_model import TermExtractor
from app.agent.term_validation import failed_field, validate_field
from app.schemas.answer import Evidence
from app.schemas.retrieval import SearchHit
from app.schemas.terms import (
    FIELD_TYPES,
    CommercialTerms,
    ExtractionRequest,
    ExtractionResponse,
    FieldDraft,
    FieldName,
    SourcedField,
)


class ExtractionState(BaseModel):
    request: ExtractionRequest
    fields: list[FieldName] = Field(default_factory=list)
    index: int = 0
    attempt: int = 1
    hits: list[SearchHit] = Field(default_factory=list)
    sources: list[Evidence] = Field(default_factory=list)
    candidate: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    feedback: str | None = None
    checked: SourcedField[Any] | None = None
    results: dict[FieldName, SourcedField[Any]] = Field(default_factory=dict)
    route: Literal["retry", "next", "done"] = "next"
    response: ExtractionResponse | None = None


class TermsPipeline:
    def __init__(self, retriever: Retriever, extractor: TermExtractor) -> None:
        self.retriever = retriever
        self.extractor = extractor
        graph = StateGraph(ExtractionState)
        for name in (
            "classify_contract_type",
            "plan_fields",
            "retrieve_for_each_field",
            "extract_field",
            "validate",
            "retry_or_flag_low_confidence",
            "assemble_result",
        ):
            graph.add_node(name, getattr(self, name))
        graph.add_edge(START, "classify_contract_type")
        graph.add_edge("classify_contract_type", "plan_fields")
        graph.add_edge("plan_fields", "retrieve_for_each_field")
        graph.add_edge("retrieve_for_each_field", "extract_field")
        graph.add_edge("extract_field", "validate")
        graph.add_edge("validate", "retry_or_flag_low_confidence")
        graph.add_conditional_edges(
            "retry_or_flag_low_confidence",
            lambda state: state.route,
            {
                "retry": "retrieve_for_each_field",
                "next": "retrieve_for_each_field",
                "done": "assemble_result",
            },
        )
        graph.add_edge("assemble_result", END)
        self.graph = graph.compile()

    def _retrieve(
        self,
        request: ExtractionRequest,
        field: FieldName,
        attempt: int,
        previous: list[SearchHit],
        kind: str | None = None,
    ) -> list[SearchHit]:
        query = FIELD_QUERIES[field]
        if field == "price_tariff" and kind:
            query = (
                (
                    "PPA electricity tariff energy capacity charges"
                    if kind == "PPA"
                    else "EPC total contract price lump sum milestones"
                )
                + " "
                + query
            )
        if attempt > 1:
            query += " definitions detailed schedule annex exceptions conditions referenced clause"
        hits = self.retriever.search(query, doc_id=request.doc_id, top_k=request.top_k * attempt)
        if any(h.chunk.doc_id != request.doc_id for h in hits):
            raise ValueError("Retriever returned a different document")
        # Keep earlier evidence available during retries instead of replacing it.
        unique = {h.chunk.chunk_id: h for h in previous}
        unique.update({h.chunk.chunk_id: h for h in hits})
        return list(unique.values())

    def _extract(
        self, field: FieldName, sources: list[Evidence], feedback: str | None
    ) -> dict[str, Any]:
        if not sources:
            return {
                "candidate": {
                    "value": None,
                    "evidence": [],
                    "confidence": 0,
                    "reason": "No retrieved evidence",
                },
                "error": None,
            }
        try:
            candidate = self.extractor.extract(
                field, FIELD_GUIDANCE[field], sources, FieldDraft[FIELD_TYPES[field]], feedback
            )
            return {"candidate": candidate, "error": None}
        except (AnswerModelError, APIError):
            return {"candidate": {}, "error": "Provider failed or returned incomplete output"}

    def classify_contract_type(self, state: ExtractionState) -> dict[str, Any]:
        hits = self._retrieve(state.request, "contract_type", 1, [])
        sources = prepare_evidence(hits)
        return {"hits": hits, "sources": sources, **self._extract("contract_type", sources, None)}

    def plan_fields(self, state: ExtractionState) -> dict[str, Any]:
        # Classification is tentative until it passes the same checks as every other field.
        return {"fields": list(FIELD_TYPES)}

    def retrieve_for_each_field(self, state: ExtractionState) -> dict[str, Any]:
        if state.index == 0 and state.attempt == 1:
            return {}
        classification = state.results.get("contract_type")
        kind = (
            classification.value
            if classification and classification.status == "validated"
            else None
        )
        hits = self._retrieve(
            state.request,
            state.fields[state.index],
            state.attempt,
            state.hits if state.attempt > 1 else [],
            kind,
        )
        return {"hits": hits, "sources": prepare_evidence(hits)}

    def extract_field(self, state: ExtractionState) -> dict[str, Any]:
        if state.index == 0 and state.attempt == 1:
            return {}
        return self._extract(state.fields[state.index], state.sources, state.feedback)

    def validate(self, state: ExtractionState) -> dict[str, Any]:
        checked = (
            failed_field("error", state.error, state.attempt)
            if state.error
            else validate_field(
                state.fields[state.index],
                state.candidate,
                state.sources,
                state.request.confidence_threshold,
                state.attempt,
            )
        )
        return {"checked": checked}

    def retry_or_flag_low_confidence(self, state: ExtractionState) -> dict[str, Any]:
        checked = state.checked
        assert checked is not None
        if checked.status != "validated" and state.attempt <= state.request.max_retries:
            return {
                "attempt": state.attempt + 1,
                "feedback": checked.reason or checked.status,
                "route": "retry",
            }
        results = {**state.results, state.fields[state.index]: checked}
        index = state.index + 1
        return {
            "results": results,
            "index": index,
            "attempt": 1,
            "hits": [],
            "sources": [],
            "candidate": {},
            "error": None,
            "feedback": None,
            "checked": None,
            "route": "done" if index == len(state.fields) else "next",
        }

    def assemble_result(self, state: ExtractionState) -> dict[str, Any]:
        terms = CommercialTerms.model_validate(
            {name: result.model_dump() for name, result in state.results.items()}
        )
        review = [name for name, result in state.results.items() if result.status != "validated"]
        return {
            "response": ExtractionResponse(
                doc_id=state.request.doc_id,
                terms=terms,
                needs_review=review,
                warnings=[
                    "Confidence is model-estimated, not a measured probability of correctness.",
                    "Validated means schema and quotes passed; meaning still needs review.",
                    "Not found means absent from retrieved evidence, not necessarily the PDF.",
                ],
            )
        }

    def extract(self, request: ExtractionRequest) -> ExtractionResponse:
        limit = 4 * len(FIELD_TYPES) * (request.max_retries + 1) + 10
        return self.graph.invoke({"request": request}, config={"recursion_limit": limit})[
            "response"
        ]
