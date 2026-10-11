from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "PPA-Sense"
    environment: Literal["development", "test", "production"] = "development"
    documents_path: Path = PROJECT_ROOT / "data" / "documents"
    max_upload_bytes: int = Field(default=50 * 1024 * 1024, ge=1)
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_revision: str | None = None
    embedding_batch_size: int = Field(default=32, ge=1, le=256)
    chroma_path: Path = PROJECT_ROOT / "data" / "chroma"
    chroma_collection: str = "contract_chunks"
    llm_provider: str = "anthropic"
    llm_model: str = ""
    anthropic_api_key: SecretStr = SecretStr("")
    llm_max_tokens: int = Field(default=2000, ge=256, le=8000)
    llm_timeout_seconds: float = Field(default=60, gt=0, le=300)

    @field_validator("embedding_revision", mode="before")
    @classmethod
    def optional_revision(cls, value: str | None) -> str | None:
        return value or None

    @property
    def resolved_chroma_path(self) -> Path:
        if self.chroma_path.is_absolute():
            return self.chroma_path
        return PROJECT_ROOT / self.chroma_path
