import json
from collections.abc import Sequence
from functools import cached_property
from typing import TYPE_CHECKING, Protocol

import numpy as np

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer


class Embedder(Protocol):
    @property
    def fingerprint(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class LocalEmbedder:
    def __init__(
        self,
        model_name: str = "sentence-transformers/all-MiniLM-L6-v2",
        revision: str | None = None,
        batch_size: int = 32,
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        self.model_name = model_name
        self.revision = revision
        self.batch_size = batch_size

    @cached_property
    def model(self) -> "SentenceTransformer":
        from sentence_transformers import SentenceTransformer

        return SentenceTransformer(
            self.model_name, revision=self.revision, device="cpu", trust_remote_code=False
        )

    @property
    def dimension(self) -> int:
        return int(self.model.get_sentence_embedding_dimension())

    @cached_property
    def fingerprint(self) -> str:
        config = self.model[0].auto_model.config
        return json.dumps(
            {
                "model": self.model_name,
                "revision": getattr(config, "_commit_hash", None) or self.revision,
                "dimension": self.dimension,
                "max_tokens": self.model.max_seq_length,
                "strategy": "token-weighted-windows-v1",
            },
            sort_keys=True,
        )

    def _token_count(self, text: str, special: bool = True) -> int:
        return len(
            self.model.tokenizer.encode(
                text,
                add_special_tokens=special,
                truncation=False,
                verbose=False,
            )
        )

    def _windows(self, text: str) -> list[str]:
        if not text.strip():
            raise ValueError("Cannot embed empty text")
        if self._token_count(text) <= self.model.max_seq_length:
            return [text]
        if len(text) < 2:
            raise ValueError("Model token limit is too small")
        midpoint = len(text) // 2
        split = text.rfind(" ", 0, midpoint + 1)
        if split <= 0 or split < len(text) // 4:
            split = midpoint
        # Check every window with the real tokenizer; never silently truncate a clause.
        return [
            window
            for part in (text[:split], text[split:])
            if part.strip()
            for window in self._windows(part)
        ]

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        groups = [self._windows(text) for text in texts]
        windows = [window for group in groups for window in group]
        vectors = self.model.encode(
            windows,
            batch_size=self.batch_size,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        result: list[list[float]] = []
        offset = 0
        for group in groups:
            weights = [max(1, self._token_count(window, special=False)) for window in group]
            vector = np.average(vectors[offset : offset + len(group)], axis=0, weights=weights)
            norm = np.linalg.norm(vector)
            if not np.isfinite(vector).all() or not np.isfinite(norm) or norm == 0:
                raise ValueError("Embedding model returned an invalid vector")
            result.append((vector / norm).tolist())
            offset += len(group)
        return result
