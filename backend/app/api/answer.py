from anthropic import APIError
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError

from app.agent.anthropic_client import AnswerModelError
from app.agent.evidence import CitationValidationError, EvidenceUnavailableError
from app.agent.pipeline import AnswerPipeline
from app.agent.service import create_answer_pipeline
from app.schemas.answer import AnswerRequest, AnswerResponse

router = APIRouter(tags=["answers"])


def get_pipeline(request: Request) -> AnswerPipeline:
    with request.app.state.answer_lock:
        if request.app.state.answer_pipeline is None:
            try:
                request.app.state.answer_pipeline = create_answer_pipeline(
                    request.app.state.settings
                )
            except (ValueError, OSError) as exc:
                raise HTTPException(503, "Answer service is not configured or available") from exc
    return request.app.state.answer_pipeline


@router.post("/answer", response_model=AnswerResponse)
def answer_question(
    request: AnswerRequest,
    pipeline: AnswerPipeline = Depends(get_pipeline),
) -> AnswerResponse:
    try:
        return pipeline.answer(request)
    except EvidenceUnavailableError as exc:
        raise HTTPException(409, str(exc)) from exc
    except (CitationValidationError, AnswerModelError, ValidationError) as exc:
        raise HTTPException(502, "The model answer failed source or format checks") from exc
    except APIError as exc:
        raise HTTPException(502, "The answer provider is unavailable; retry later") from exc
