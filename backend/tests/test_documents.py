from datetime import UTC, datetime
from types import SimpleNamespace

import pymupdf
import pytest
from fastapi.testclient import TestClient

from app.api.document_store import DocumentStore
from app.config import Settings
from app.main import create_app
from app.schemas.answer import AnswerResponse
from app.schemas.documents import Document
from app.schemas.retrieval import IndexResult
from app.schemas.terms import CommercialTerms, ExtractionResponse


class Index:
    def __init__(self):
        self.calls = []

    def replace_document(self, doc_id, chunks):
        if not chunks:
            raise ValueError("No text to index")
        self.calls.append((doc_id, chunks))
        return IndexResult(doc_id=doc_id, chunks_stored=len(chunks), stale_chunks_removed=0)


@pytest.fixture
def api(tmp_path):
    settings = Settings(_env_file=None, documents_path=tmp_path / "documents")
    app = create_app(settings)
    index = Index()
    app.state.ingestion_store = index
    return app, TestClient(app), index


def pdf(text="1. PRICE\nTariff is USD 45 per MWh."):
    with pymupdf.open() as document:
        document.new_page().insert_text((72, 72), text)
        return document.tobytes()


def upload(client, content=None):
    return client.post(
        "/documents", files={"file": ("../contract.pdf", content or pdf(), "application/pdf")}
    )


def test_upload_ingest_duplicate_and_persist(api):
    app, client, index = api
    content = pdf()
    response = upload(client, content)
    assert response.status_code == 202
    queued = response.json()
    assert queued["status"] == "queued"
    doc_id = queued["doc_id"]
    ready = client.get(f"/documents/{doc_id}").json()
    assert ready["status"] == "ready" and ready["chunks_stored"] > 0
    assert index.calls[0][1][0].page_start == 1
    assert upload(client, content).json()["doc_id"] == doc_id
    assert len(index.calls) == 1
    assert len(client.get("/documents").json()["documents"]) == 1
    assert client.get("/documents?offset=1").json()["documents"] == []
    restarted = TestClient(create_app(app.state.settings))
    assert restarted.get(f"/documents/{doc_id}").json() == ready


def test_response_is_sent_before_indexing(api):
    app, _, index = api
    events = []
    original = index.replace_document

    def index_after_response(doc_id, chunks):
        assert events == ["response_finished"]
        events.append("indexing")
        return original(doc_id, chunks)

    index.replace_document = index_after_response

    async def observed_app(scope, receive, send):
        async def observed_send(message):
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                events.append("response_finished")
            await send(message)

        await app(scope, receive, observed_send)

    assert upload(TestClient(observed_app)).status_code == 202
    assert events == ["response_finished", "indexing"]


@pytest.mark.parametrize("content", [b"not a PDF", b""])
def test_reject_non_pdf(api, content):
    app, client, _ = api
    response = client.post("/documents", files={"file": ("bad.pdf", content)})
    assert response.status_code == 415
    assert client.get("/documents").json()["documents"] == []
    assert not list(app.state.settings.documents_path.glob("*.upload"))


def test_size_limit(api):
    app, client, _ = api
    app.state.settings.max_upload_bytes = 10
    assert upload(client).status_code == 413
    assert client.get("/documents").json()["documents"] == []


def test_failed_ingestion_retry_and_not_ready(api):
    app, client, _ = api
    content = b"%PDF-1.7\nnot a valid document"
    doc_id = upload(client, content).json()["doc_id"]
    assert client.get(f"/documents/{doc_id}").json()["status"] == "failed"
    assert client.post(f"/documents/{doc_id}/ask", json={"question": "Price?"}).status_code == 409
    assert client.post(f"/documents/{doc_id}/extract", json={}).status_code == 409
    assert upload(client, content).json()["status"] == "queued"
    assert len(client.get("/documents").json()["documents"]) == 1


def test_ask_scoped_and_validation(api):
    app, client, _ = api
    calls = []

    def answer(body):
        calls.append(body)
        return AnswerResponse(answer="not found in the contract", citations=[])

    app.state.answer_pipeline = SimpleNamespace(answer=answer)
    doc_id = upload(client).json()["doc_id"]
    route = f"/documents/{doc_id}/ask"
    assert client.post(route, json={"question": "Price?"}).status_code == 200
    assert calls[0].doc_id == doc_id
    for body in (
        {"question": " "},
        {"question": "Price?", "doc_id": "other"},
        {"question": "Price?", "top_k": 100},
    ):
        assert client.post(route, json=body).status_code == 422
    assert client.post("/documents/missing/ask", json={"question": "Price?"}).status_code == 404


def test_extract_save_get_and_restart(api):
    app, client, _ = api
    doc_id = upload(client).json()["doc_id"]
    fields = {
        name: {
            "value": None,
            "sources": [],
            "confidence": 0,
            "status": "not_found",
            "reason": "No evidence",
            "attempts": 1,
        }
        for name in CommercialTerms.model_fields
    }
    result = ExtractionResponse(
        doc_id=doc_id, terms=CommercialTerms(**fields), needs_review=list(fields), warnings=[]
    )
    calls = []

    def extract(body):
        calls.append(body)
        return result

    app.state.terms_pipeline = SimpleNamespace(extract=extract)
    assert client.get(f"/documents/{doc_id}/terms").status_code == 404
    response = client.post(f"/documents/{doc_id}/extract", json={})
    assert response.status_code == 200
    assert calls[0].doc_id == doc_id
    restarted = TestClient(create_app(app.state.settings))
    assert restarted.get(f"/documents/{doc_id}/terms").json() == response.json()


def test_interrupted_job_recovery(tmp_path):
    settings = Settings(_env_file=None, documents_path=tmp_path)
    catalog = DocumentStore(tmp_path)
    catalog.save(
        Document(
            doc_id="interrupted",
            filename="a.pdf",
            size_bytes=100,
            status="processing",
            created_at=datetime.now(UTC),
        )
    )
    client = TestClient(create_app(settings))
    response = client.get("/documents/interrupted").json()
    assert response["status"] == "failed"
    assert "restart" in response["error"]


def test_cors_and_missing_provider(api):
    app, client, _ = api
    app.state.settings.anthropic_api_key = ""
    app.state.settings.llm_model = ""
    doc_id = upload(client).json()["doc_id"]
    assert client.post(f"/documents/{doc_id}/ask", json={"question": "Price?"}).status_code == 503
    for origin, status in [("http://localhost:5173", 200), ("https://untrusted.test", 400)]:
        response = client.options(
            "/documents", headers={"Origin": origin, "Access-Control-Request-Method": "POST"}
        )
        assert response.status_code == status


def test_unexpected_errors_are_sanitized(api):
    app, client, _ = api
    doc_id = upload(client).json()["doc_id"]

    def broken(body):
        raise RuntimeError("secret detail")

    app.state.answer_pipeline = SimpleNamespace(answer=broken)
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(f"/documents/{doc_id}/ask", json={"question": "Price?"})
    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}
