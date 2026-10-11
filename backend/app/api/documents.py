import hashlib
import logging
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, UploadFile

from app.api.answer import answer_question, get_pipeline
from app.api.document_store import DocumentStore
from app.api.terms import extract_terms, get_terms_pipeline
from app.config import PROJECT_ROOT
from app.retrieval.service import create_store, ingest_pdf
from app.schemas.answer import AnswerRequest, AnswerResponse
from app.schemas.documents import AskRequest, Document, DocumentList, ErrorResponse, ExtractRequest
from app.schemas.terms import ExtractionRequest, ExtractionResponse

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/documents",
    tags=["documents"],
    responses={code: {"model": ErrorResponse} for code in (404, 409, 413, 415, 500, 502, 503)},
)


def get_documents(request: Request) -> DocumentStore:
    with request.app.state.documents_lock:
        if request.app.state.documents is None:
            path = request.app.state.settings.documents_path
            catalog = DocumentStore(path if path.is_absolute() else PROJECT_ROOT / path)
            # Recovery assumes one API worker; background tasks are process-local.
            catalog.recover_interrupted()
            request.app.state.documents = catalog
    return request.app.state.documents


Catalog = Annotated[DocumentStore, Depends(get_documents)]


def require_document(doc_id: str, catalog: Catalog) -> Document:
    document = catalog.get(doc_id)
    if document is None:
        raise HTTPException(404, "Document not found")
    return document


def require_ready(document: Annotated[Document, Depends(require_document)]) -> Document:
    if document.status != "ready":
        raise HTTPException(409, f"Document is {document.status}; ingestion must finish first")
    return document


ReadyDocument = Annotated[Document, Depends(require_ready)]


def process_document(doc_id: str, catalog: DocumentStore, request: Request) -> None:
    # Serialize embedding work to avoid loading multiple models under concurrent uploads.
    with catalog.ingestion_lock:
        document = catalog.get(doc_id)
        if document is None or document.status != "queued":
            return
        document.status = "processing"
        catalog.save(document)
        try:
            if request.app.state.ingestion_store is None:
                request.app.state.ingestion_store = create_store(request.app.state.settings)
            result = ingest_pdf(catalog.root / f"{doc_id}.pdf", request.app.state.ingestion_store)
            document.status = "ready"
            document.chunks_stored = result.chunks_stored
            document.warnings = result.warnings
            document.error = None
        except Exception:
            logger.exception("Document ingestion failed for %s", doc_id)
            document.status = "failed"
            document.error = "Ingestion failed. Check server logs, then upload the PDF again."
        catalog.save(document)


@router.post("", response_model=Document, status_code=202)
def upload_document(
    file: UploadFile,
    background_tasks: BackgroundTasks,
    request: Request,
    catalog: Catalog,
) -> Document:
    path: Path | None = None
    try:
        digest = hashlib.sha256()
        size = 0
        with NamedTemporaryFile(dir=catalog.root, suffix=".upload", delete=False) as temporary:
            path = Path(temporary.name)
            while block := file.file.read(1024 * 1024):
                if size == 0 and b"%PDF-" not in block[:1024]:
                    raise HTTPException(415, "Upload must be a PDF")
                size += len(block)
                if size > request.app.state.settings.max_upload_bytes:
                    raise HTTPException(413, "PDF exceeds the upload size limit")
                digest.update(block)
                temporary.write(block)
        if not size:
            raise HTTPException(415, "PDF is empty")
        doc_id = digest.hexdigest()
        with catalog.upload_lock:
            previous = catalog.get(doc_id)
            if previous is not None and previous.status != "failed":
                return previous
            destination = catalog.root / f"{doc_id}.pdf"
            # A matching hash means the retained PDF already has these exact bytes.
            if not destination.exists():
                path.replace(destination)
            document = Document(
                doc_id=doc_id,
                filename=(file.filename or "contract.pdf")[:255],
                size_bytes=size,
                status="queued",
                created_at=previous.created_at if previous else datetime.now(UTC),
            )
            catalog.save(document)
            background_tasks.add_task(process_document, doc_id, catalog, request)
            return document
    finally:
        file.file.close()
        if path is not None:
            path.unlink(missing_ok=True)


@router.get("", response_model=DocumentList)
def list_documents(
    catalog: Catalog,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DocumentList:
    return DocumentList(documents=catalog.list(limit, offset), limit=limit, offset=offset)


@router.get("/{doc_id}", response_model=Document)
def document_status(document: Annotated[Document, Depends(require_document)]) -> Document:
    return document


@router.post("/{doc_id}/ask", response_model=AnswerResponse)
def ask_document(body: AskRequest, document: ReadyDocument, request: Request) -> AnswerResponse:
    return answer_question(
        AnswerRequest(doc_id=document.doc_id, **body.model_dump()), get_pipeline(request)
    )


@router.post("/{doc_id}/extract", response_model=ExtractionResponse)
def extract_document(
    body: ExtractRequest,
    document: ReadyDocument,
    request: Request,
    catalog: Catalog,
) -> ExtractionResponse:
    if not catalog.extraction_lock.acquire(blocking=False):
        raise HTTPException(409, "An extraction is already running; retry later")
    try:
        result = extract_terms(
            ExtractionRequest(doc_id=document.doc_id, **body.model_dump()),
            get_terms_pipeline(request),
        )
        catalog.save_terms(result)
        return result
    finally:
        catalog.extraction_lock.release()


@router.get("/{doc_id}/terms", response_model=ExtractionResponse)
def get_document_terms(document: ReadyDocument, catalog: Catalog) -> ExtractionResponse:
    result = catalog.get_terms(document.doc_id)
    if result is None:
        raise HTTPException(404, "No saved terms; run extraction first")
    return result
