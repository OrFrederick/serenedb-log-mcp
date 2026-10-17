"""Text embeddings for find_patterns (T26, #7 Decision proposal): optional, local, no API key.

The Pattern catalogue's template texts are embedded once by `scripts/embed_patterns.py`
(`make embed-patterns`) and stored in SereneDB (`pattern_vector`, a FLOAT[DIMENSIONS] column);
find_patterns embeds only the question, here in the server process, and lets SereneDB rank the
stored vectors by cosine distance. Both sides use the same `Embedder`, so the vectors compare.

The default embedder is `fastembed` (ONNX Runtime, pure pip) with `MODEL`. It is an optional
dependency: `uv sync --extra vectors` installs it, and the model (about 90 MB) is downloaded on the
first `make embed-patterns` into `cache_dir()`. The server never downloads: at query time it loads
the model from that cache only, so a missing model is an actionable `EmbeddingUnavailable` rather
than a minute-long stall inside a tool call.

`set_embedder` replaces the embedder process-wide; the tests use it to run find_patterns and the
embedding script without fastembed or a model download.
"""

from __future__ import annotations

import functools
import math
import os
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any, Protocol

__all__ = [
    "CACHE_ENV",
    "DIMENSIONS",
    "INSTALL_HINT",
    "MODEL",
    "Embedder",
    "EmbeddingUnavailable",
    "FastEmbedder",
    "cache_dir",
    "document_text",
    "get_embedder",
    "set_embedder",
]

MODEL = "sentence-transformers/all-MiniLM-L6-v2"
"""The fastembed model: of the local models #7 measured, the smallest and fastest (90 MB, about 5 ms
per query) within noise of the best (R@10 0.765 against 0.783 for granite-small-r2)."""
DIMENSIONS = 384
CACHE_ENV = "FASTEMBED_CACHE_PATH"
"""fastembed's own variable for its model cache; when unset, `cache_dir()` picks a stable default
(fastembed's default is a temporary directory that the OS may clear)."""
INSTALL_HINT = (
    "find_patterns needs the optional vectors extra: run `uv sync --extra vectors`, then "
    "`make embed-patterns` once, which downloads the embedding model (about 90 MB) and stores the "
    "Pattern vectors in SereneDB."
)


class EmbeddingUnavailable(RuntimeError):
    """The embedder cannot run: fastembed is not installed or its model is not in the cache."""


class Embedder(Protocol):
    """Turns texts into vectors of `dimensions` floats; `model` names it in the stored vectors."""

    @property
    def model(self) -> str: ...

    @property
    def dimensions(self) -> int: ...

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


def cache_dir() -> Path:
    """Where fastembed keeps its models: FASTEMBED_CACHE_PATH, else ~/.cache/serenedb-log-mcp/fastembed."""
    configured = os.environ.get(CACHE_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".cache" / "serenedb-log-mcp" / "fastembed"


def document_text(component: str | None, template_text: str) -> str:
    """The text embedded for one Pattern: its most frequent Component, a colon and its template text
    without the `<*>` masks, for example 'KERNEL: instruction cache parity error corrected'.

    Measured on #7's 124 labelled queries (docs/results/find-patterns.md): the Component prefix and
    the dropped masks each raised all-MiniLM-L6-v2's recall a little over the bare template text.
    """
    text = " ".join(template_text.replace("<*>", " ").split())
    return f"{component}: {text}" if component else text


def _unit(vector: Iterable[Any]) -> list[float]:
    values = [float(v) for v in vector]
    norm = math.sqrt(sum(v * v for v in values))
    return [v / norm for v in values] if norm > 0 else values


class FastEmbedder:
    """fastembed's TextEmbedding with `MODEL`, unit-length output."""

    def __init__(self, model: str = MODEL, *, download: bool = False) -> None:
        try:
            from fastembed import TextEmbedding
        except ImportError as exc:
            raise EmbeddingUnavailable(f"fastembed is not installed. {INSTALL_HINT}") from exc
        try:
            # One ONNX thread: a query is one short text, the catalogue a few hundred, and on a loaded
            # host (load average near 30) the default thread pool spun for minutes on 465 texts that
            # one thread embeds in under a second.
            self._model = TextEmbedding(
                model, cache_dir=str(cache_dir()), local_files_only=not download, threads=1
            )
        except Exception as exc:
            raise EmbeddingUnavailable(
                f"The embedding model {model} is not in the cache {cache_dir()} "
                f"({type(exc).__name__}). {INSTALL_HINT}"
            ) from exc
        self._name = model

    @property
    def model(self) -> str:
        return self._name

    @property
    def dimensions(self) -> int:
        return DIMENSIONS

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [_unit(vector) for vector in self._model.embed(list(texts))]

    def embed_query(self, text: str) -> list[float]:
        (vector,) = self._model.query_embed([text])
        return _unit(vector)


_override: Embedder | None = None


@functools.cache
def _default() -> Embedder:
    return FastEmbedder()


def get_embedder() -> Embedder:
    """The process-wide embedder: the one `set_embedder` installed, else fastembed from the cache.

    Raises EmbeddingUnavailable when fastembed or its model is missing; a later call tries again,
    so `make embed-patterns` takes effect without restarting the server.
    """
    if _override is not None:
        return _override
    return _default()


def set_embedder(embedder: Embedder | None) -> None:
    """Replace the process-wide embedder (None restores fastembed)."""
    global _override
    _override = embedder
