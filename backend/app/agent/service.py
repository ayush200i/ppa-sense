from anthropic import Anthropic

from app.agent.anthropic_client import AnthropicGenerator
from app.agent.pipeline import AnswerPipeline
from app.agent.term_model import AnthropicTermExtractor
from app.agent.terms_pipeline import TermsPipeline
from app.config import Settings
from app.retrieval.service import create_store
from app.retrieval.store import ChunkStore


def create_answer_pipeline(settings: Settings, store: ChunkStore | None = None) -> AnswerPipeline:
    if settings.llm_provider != "anthropic":
        raise ValueError("Step 4 uses LLM_PROVIDER=anthropic")
    if not settings.llm_model.strip() or not settings.anthropic_api_key.get_secret_value().strip():
        raise ValueError("Set LLM_MODEL and ANTHROPIC_API_KEY in the root .env")
    client = Anthropic(
        api_key=settings.anthropic_api_key.get_secret_value(),
        timeout=settings.llm_timeout_seconds,
        max_retries=1,
    )
    return AnswerPipeline(
        store if store is not None else create_store(settings),
        AnthropicGenerator(
            client,
            settings.llm_model,
            settings.llm_max_tokens,
        ),
    )


def create_terms_pipeline(settings: Settings, store: ChunkStore | None = None) -> TermsPipeline:
    if settings.llm_provider != "anthropic":
        raise ValueError("Extraction uses LLM_PROVIDER=anthropic")
    if not settings.llm_model.strip() or not settings.anthropic_api_key.get_secret_value().strip():
        raise ValueError("Set LLM_MODEL and ANTHROPIC_API_KEY in the root .env")
    client = Anthropic(
        api_key=settings.anthropic_api_key.get_secret_value(),
        timeout=settings.llm_timeout_seconds,
        max_retries=1,
    )
    return TermsPipeline(
        store if store is not None else create_store(settings),
        AnthropicTermExtractor(
            client,
            settings.llm_model,
            settings.llm_max_tokens,
        ),
    )
