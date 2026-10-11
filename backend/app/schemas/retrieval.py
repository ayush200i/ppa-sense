from pydantic import BaseModel, Field

from app.schemas.ingestion import Chunk


class IndexResult(BaseModel):
    doc_id: str
    chunks_stored: int = Field(ge=0)
    stale_chunks_removed: int = Field(ge=0)
    warnings: list[str] = Field(default_factory=list)


class SearchHit(BaseModel):
    chunk: Chunk
    cosine_distance: float

    @property
    def similarity(self) -> float:
        """Similarity is a ranking score, not a probability of correctness."""
        return 1.0 - self.cosine_distance
