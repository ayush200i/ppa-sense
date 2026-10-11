import re
from typing import Protocol, TypedDict

from langgraph.graph import END, START, StateGraph

from app.agent.anthropic_client import AnswerGenerator
from app.agent.evidence import prepare_evidence, verify_and_render
from app.schemas.answer import NOT_FOUND, AnswerRequest, AnswerResponse, DraftAnswer, Evidence
from app.schemas.retrieval import SearchHit


class Retriever(Protocol):
    def search(
        self, question: str, doc_id: str | None = None, top_k: int = 5
    ) -> list[SearchHit]: ...


class AnswerState(TypedDict, total=False):
    request: AnswerRequest
    hits: list[SearchHit]
    evidence: list[Evidence]
    draft: DraftAnswer
    response: AnswerResponse


def rerank(question: str, hits: list[SearchHit]) -> list[SearchHit]:
    stop = {"the", "a", "an", "is", "are", "what", "when", "how", "of", "in", "to", "and"}
    words = set(re.findall(r"\w+", question.lower())) - stop

    def score(hit: SearchHit) -> float:
        tokens = set(re.findall(r"\w+", hit.chunk.text.lower()))
        overlap = len(words & tokens) / max(1, len(words))
        return hit.similarity + 0.2 * overlap

    return sorted(hits, key=score, reverse=True)


class AnswerPipeline:
    def __init__(self, retriever: Retriever, generator: AnswerGenerator) -> None:
        self.retriever = retriever
        self.generator = generator
        graph = StateGraph(AnswerState)
        graph.add_node("retrieve", self._retrieve)
        graph.add_node("prepare", self._prepare)
        graph.add_node("generate", self._generate)
        graph.add_node("verify", self._verify)
        graph.add_node(
            "missing", lambda state: {"response": AnswerResponse(answer=NOT_FOUND, citations=[])}
        )
        graph.add_edge(START, "retrieve")
        graph.add_conditional_edges(
            "retrieve", lambda state: "prepare" if state["hits"] else "missing"
        )
        graph.add_edge("prepare", "generate")
        graph.add_edge("generate", "verify")
        graph.add_edge("verify", END)
        graph.add_edge("missing", END)
        self.graph = graph.compile()

    def _retrieve(self, state: AnswerState) -> dict:
        request = state["request"]
        count = request.top_k * 3 if request.rerank else request.top_k
        hits = self.retriever.search(request.question, doc_id=request.doc_id, top_k=count)
        if request.doc_id is not None and any(h.chunk.doc_id != request.doc_id for h in hits):
            raise ValueError("Retriever returned a different document")
        if request.rerank:
            hits = rerank(request.question, hits)
        return {"hits": hits[: request.top_k]}

    def _prepare(self, state: AnswerState) -> dict:
        return {"evidence": prepare_evidence(state["hits"])}

    def _generate(self, state: AnswerState) -> dict:
        return {"draft": self.generator.generate(state["request"].question, state["evidence"])}

    def _verify(self, state: AnswerState) -> dict:
        return {"response": verify_and_render(state["draft"], state["evidence"])}

    def answer(self, request: AnswerRequest) -> AnswerResponse:
        return self.graph.invoke({"request": request})["response"]
