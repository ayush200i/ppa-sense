import logging
from threading import Lock

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.answer import router as answer_router
from app.api.documents import router as documents_router
from app.api.health import router as health_router
from app.api.terms import router as terms_router
from app.config import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings if settings is not None else Settings()
    application = FastAPI(title=config.app_name, version="0.1.0")
    application.state.settings = config
    application.state.answer_pipeline = None
    application.state.terms_pipeline = None
    application.state.answer_lock = Lock()
    application.state.documents_lock = Lock()
    application.state.documents = None
    application.state.ingestion_store = None
    application.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    @application.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logging.getLogger(__name__).error("Unhandled API error", exc_info=exc)
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    application.include_router(documents_router)
    application.include_router(health_router, prefix="/api")
    application.include_router(answer_router, prefix="/api")
    application.include_router(terms_router, prefix="/api")
    return application


app = create_app()
