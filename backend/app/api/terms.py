from fastapi import APIRouter, Depends, HTTPException, Request

from app.agent.evidence import EvidenceUnavailableError
from app.agent.service import create_terms_pipeline
from app.agent.terms_pipeline import TermsPipeline
from app.schemas.terms import ExtractionRequest, ExtractionResponse

router = APIRouter(tags=["commercial terms"])


def get_terms_pipeline(request: Request) -> TermsPipeline:
    with request.app.state.answer_lock:
        if request.app.state.terms_pipeline is None:
            try:
                request.app.state.terms_pipeline = create_terms_pipeline(request.app.state.settings)
            except (ValueError, OSError) as exc:
                raise HTTPException(
                    503, "Extraction service is not configured or available"
                ) from exc
    return request.app.state.terms_pipeline


@router.post("/extract", response_model=ExtractionResponse)
def extract_terms(
    request: ExtractionRequest, pipeline: TermsPipeline = Depends(get_terms_pipeline)
) -> ExtractionResponse:
    try:
        return pipeline.extract(request)
    except EvidenceUnavailableError as exc:
        raise HTTPException(409, str(exc)) from exc
