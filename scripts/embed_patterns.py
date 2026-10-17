#!/usr/bin/env python3
"""Embed the Pattern catalogue for find_patterns (T26): the optional vector step after `make derive`.

Usage (see `make help`):
    uv sync --extra vectors                     # once: fastembed and ONNX Runtime (about 100 MB)
    uv run python scripts/embed_patterns.py [--schema S]

It reads every Pattern of SLM_SCHEMA's catalogue (`pattern`, built by sql/derive/20_patterns.sql),
embeds its template text locally with fastembed (`corpus.embeddings`, no API key; the first run
downloads the model, about 90 MB, into FASTEMBED_CACHE_PATH or ~/.cache/serenedb-log-mcp/fastembed)
and replaces that model's rows of `pattern_vector` (sql/derive/25_pattern_vectors.sql) in one
transaction. It takes a few seconds for the 536 Patterns of the Base Corpus (the 538 of the catalogue
but the two Patterns of empty messages) and is re-runnable:
run it again after a derive that changed the Patterns. The Pattern of empty messages has no text and
gets no vector.

Environment: DATABASE_URL, SLM_SCHEMA (default slm), FASTEMBED_CACHE_PATH.
Exit codes: 0 done, 1 lost connection (re-run), 2 a problem to fix first (no fastembed, no catalogue).
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql

from derive import CONNECT_TIMEOUT_S, DeriveError, _quoted, render, split_statements
from serenedb_log_mcp.config import ConfigError, load_settings
from serenedb_log_mcp.corpus.datasets import PATTERN_VECTORS, PATTERNS
from serenedb_log_mcp.corpus.embeddings import (
    Embedder,
    EmbeddingUnavailable,
    FastEmbedder,
    document_text,
)

VECTORS_SQL = Path(__file__).resolve().parent.parent / "sql" / "derive" / "25_pattern_vectors.sql"
BATCH = 64


class EmbedError(Exception):
    """A problem the user can fix; printed without a traceback."""


def patterns(conn: psycopg.Connection[Any], slm: str) -> list[tuple[str, str, str]]:
    """(dataset, template_id, document text) of every Pattern with a template text, in a stable order."""
    try:
        rows = conn.execute(
            sql.SQL(
                "SELECT dataset, template_id, component, template_text FROM {} "
                "WHERE template_source <> 'empty' ORDER BY dataset, template_id"
            ).format(sql.Identifier(slm, PATTERNS))
        ).fetchall()
    except psycopg.errors.UndefinedTable as exc:
        raise EmbedError(
            f"schema {slm} has no Pattern catalogue ({slm}.{PATTERNS}); run `make derive` first"
        ) from exc
    return [(str(d), str(t), document_text(c, str(text))) for d, t, c, text in rows]


def embed(embedder: Embedder, found: Sequence[tuple[str, str, str]]) -> list[list[float]]:
    texts = [text for _, _, text in found]
    vectors: list[list[float]] = []
    for start in range(0, len(texts), BATCH):
        vectors += embedder.embed_documents(texts[start : start + BATCH])
    if any(len(v) != embedder.dimensions for v in vectors):
        raise EmbedError(f"the embedder returned vectors that are not {embedder.dimensions} long")
    return vectors


def store(
    conn: psycopg.Connection[Any],
    slm: str,
    model: str,
    dimensions: int,
    found: Sequence[tuple[str, str, str]],
    vectors: Sequence[Sequence[float]],
) -> None:
    """Replace `model`'s rows of the vector table with these vectors, in one transaction."""
    table = sql.Identifier(slm, PATTERN_VECTORS)
    cast = sql.SQL("::FLOAT[{}]").format(sql.Literal(int(dimensions)))
    rows = [(d, t, model, list(v)) for (d, t, _), v in zip(found, vectors, strict=True)]
    with conn.transaction(), conn.cursor() as cursor:
        cursor.execute(sql.SQL("DELETE FROM {} WHERE model = %s").format(table), [model])
        cursor.executemany(sql.SQL("INSERT INTO {} VALUES (%s, %s, %s, %s{})").format(table, cast), rows)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="embed_patterns.py", description="Embed the Pattern catalogue for find_patterns (optional)."
    )
    parser.add_argument("--schema", help="the derived schema (default: SLM_SCHEMA, else slm)")
    return parser


def main(argv: Sequence[str] | None = None, embedder: Embedder | None = None) -> int:
    """Embed and store; `embedder` replaces fastembed (the tests pass a fake one)."""
    args = build_parser().parse_args(argv)
    try:
        settings = load_settings()
        slm = args.schema or settings.slm_schema
        started = time.perf_counter()
        with psycopg.connect(
            settings.database_url, autocommit=True, connect_timeout=CONNECT_TIMEOUT_S
        ) as conn:
            for statement in split_statements(render(VECTORS_SQL.read_text(), {"slm": _quoted(slm)})):
                conn.execute(statement)
            found = patterns(conn, slm)
            if not found:
                raise EmbedError(f"{slm}.{PATTERNS} is empty; run `make derive` first")
            active = embedder if embedder is not None else FastEmbedder(download=True)
            vectors = embed(active, found)
            store(conn, slm, active.model, active.dimensions, found, vectors)
        datasets = sorted({d for d, _, _ in found})
        print(
            f"embed_patterns: {len(found)} Patterns of {', '.join(datasets)} embedded with "
            f"{active.model} into {slm}.{PATTERN_VECTORS} in {time.perf_counter() - started:.1f}s"
        )
    except psycopg.OperationalError as exc:
        print(f"embed_patterns: lost the database connection ({exc}); run it again", file=sys.stderr)
        return 1
    except (EmbedError, EmbeddingUnavailable, DeriveError, ConfigError, psycopg.Error) as exc:
        print(f"embed_patterns: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
