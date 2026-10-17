"""Full-text predicates over an inverted-index text column, shared by every tool that takes `query`.

The API is deliberately narrow, so T5's `search_logs` can reuse it and extend the modes instead of
growing a second builder:

- `normalise_query(query)` turns a blank query into None and rejects an over-long one;
- `text_match(column, query, mode)` returns the predicate and its bound parameter.

Modes (plan section 2.6: a bare `@@ 'a b'` is OR, which is why neither mode uses it):

- `all_words` (default): `websearch_to_tsquery`. Every word must appear, in any order; a
  "double-quoted" part is a phrase, `OR` separates alternatives and `-word` excludes a word.
- `phrase`: `phraseto_tsquery`. The words must appear next to each other, in order.

The predicate resolves only against an inverted-indexed column, so a statement that uses it must
read the index relation (`corpus.datasets.relation(...).index`), not the base table. The query
text is always a bound parameter, never formatted into the SQL. Counting needs no relevance
score, so nothing here sorts by BM25 (a BM25 sort with a tie-break key crashes SereneDB 26.10.1).
"""

from __future__ import annotations

from typing import Any, Literal, get_args

from psycopg import sql

from serenedb_log_mcp.scope import ScopeError

__all__ = ["MAX_QUERY_CHARS", "QUERY_MODES", "QueryMode", "normalise_query", "text_match"]

QueryMode = Literal["all_words", "phrase"]
QUERY_MODES: tuple[QueryMode, ...] = get_args(QueryMode)

MAX_QUERY_CHARS = 500
"""Longest query text accepted; log searches are a few words, never a pasted document."""

_PARSERS: dict[str, sql.SQL] = {
    "all_words": sql.SQL("websearch_to_tsquery"),
    "phrase": sql.SQL("phraseto_tsquery"),
}


def normalise_query(query: str | None) -> str | None:
    """The query without surrounding whitespace; None when blank. ScopeError when too long."""
    if query is None or not query.strip():
        return None
    text = query.strip()
    if len(text) > MAX_QUERY_CHARS:
        raise ScopeError(
            f"query has {len(text):,} characters; pass at most {MAX_QUERY_CHARS} characters, for "
            "example the few words or the quoted phrase that the Log Entries you want contain."
        )
    return text


def text_match(
    column: sql.Composable, query: str, mode: QueryMode = "all_words"
) -> tuple[sql.Composed, list[Any]]:
    """`column @@ <parser>(%s)` and its parameter list; `column` must be an indexed text column."""
    parser = _PARSERS.get(mode)
    if parser is None:
        raise ValueError(f"unknown full-text mode {mode!r}; valid: {', '.join(QUERY_MODES)}")
    return sql.SQL("{} @@ {}(%s)").format(column, parser), [query]
