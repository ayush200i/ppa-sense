from types import SimpleNamespace

import numpy as np
import pytest

from app.retrieval.embeddings import LocalEmbedder


class CharacterTokenizer:
    def encode(self, text, add_special_tokens=True, **kwargs):
        return list(range(len(text) + (2 if add_special_tokens else 0)))


class SmallModel:
    tokenizer = CharacterTokenizer()
    max_seq_length = 24

    def __init__(self):
        self.seen = []

    def __getitem__(self, index):
        return SimpleNamespace(
            auto_model=SimpleNamespace(config=SimpleNamespace(_commit_hash="test-revision"))
        )

    def get_sentence_embedding_dimension(self):
        return 2

    def encode(self, texts, **kwargs):
        self.seen.extend(texts)
        assert all(len(self.tokenizer.encode(t)) <= self.max_seq_length for t in texts)
        return np.array([[1.0, 0.0] if "z" not in t else [0.0, 1.0] for t in texts])


def test_long_chunks_keep_tail_and_produce_unit_vectors() -> None:
    embedder = LocalEmbedder()
    embedder.model = SmallModel()
    text = "payment due " * 30 + "zzzz"
    vector = embedder.embed([text])[0]
    assert "".join(embedder.model.seen) == text
    assert vector[1] > 0
    assert np.linalg.norm(vector) == pytest.approx(1)
    assert "test-revision" in embedder.fingerprint


def test_empty_and_short_embedding_input() -> None:
    embedder = LocalEmbedder()
    embedder.model = SmallModel()
    assert embedder.embed([]) == []
    with pytest.raises(ValueError, match="empty"):
        embedder.embed([" "])
    assert embedder.embed(["tariff"]) == [[1.0, 0.0]]
