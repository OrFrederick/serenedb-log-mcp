"""A deterministic stand-in for fastembed, so no test downloads a model (T26).

`FakeEmbedder` hashes each lower-cased word into one of `DIMENSIONS` buckets (a bag of words with
the hashing trick) and returns the unit vector. Texts that share words get a high cosine similarity,
texts that share none get 0, so find_patterns' ranking, fusion and SQL can be tested exactly. It
knows nothing about meaning: tests that measure semantic quality use the real model and the
`embedding_model` marker instead.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence

from serenedb_log_mcp.corpus.embeddings import DIMENSIONS

MODEL = "test/hashed-bag-of-words"
WORD = re.compile(r"[a-z0-9]+")


def bucket(word: str) -> int:
    return int.from_bytes(hashlib.md5(word.encode()).digest()[:4], "big") % DIMENSIONS


class FakeEmbedder:
    """The `corpus.embeddings.Embedder` protocol without a model."""

    model = MODEL
    dimensions = DIMENSIONS

    def __init__(self) -> None:
        self.queries: list[str] = []

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * DIMENSIONS
        for word in WORD.findall(text.lower()):
            vector[bucket(word)] += 1.0
        norm = math.sqrt(sum(v * v for v in vector))
        return [v / norm for v in vector] if norm else vector

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        self.queries.append(text)
        return self._vector(text)
