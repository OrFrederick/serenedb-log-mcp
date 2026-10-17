"""Full-text predicates over an inverted index, shared by every tool that takes `query`.

The API is deliberately narrow, so every tool speaks one dialect of the same filter:

- `normalise_query(query)` turns a blank query into None and rejects an over-long query or one
  without a single word;
- `text_match(column, query, mode)` returns the predicate on an indexed text column and its bound
  parameters;
- `filter_any`, `filter_at_or_after` and `filter_before` are the score-neutral filters on the
  verbatim-indexed columns, for statements that rank by relevance (T5's `search_logs`). Every bound
  carries an explicit cast, so a statement stored in the Evidence Ledger, whose parameters are JSON
  (a TIMESTAMP becomes its ISO string), replays as stored (#71).

Modes (plan section 2.6: a bare `@@ 'a b'` is OR, which is why only `any_words` uses it), each
verified against the docs and SereneDB 26.10.1:

- `all_words` (default): `websearch_to_tsquery`. Every word must appear, in any order; a
  "double-quoted" part is a phrase, `OR` separates alternatives and `-word` excludes a word. A
  free-standing word without a letter or digit (`;`, `...`) is a term no Log Entry holds, so a
  query with one is refused instead of silently matching nothing (#80).
- `phrase`: `phraseto_tsquery`. The words must appear next to each other, in order.
- `any_words`: the bare text, which the column's dictionary splits into words that are OR-ed.
- `fuzzy`: one `ts_levenshtein` per word, AND-ed with `&&`. `ts_levenshtein` takes one term and
  does not run it through the dictionary (a multi-word or upper-case term matches nothing), so the
  words are split and lower-cased here the way the `log_text` dictionary splits them. A word of
  more than `EXACT_FUZZY_CHARS` characters may differ by one edit (insert, delete, substitute or
  swap two neighbours); a shorter one must match exactly, because one edit on two characters
  matches most tokens of a log.

Every text predicate resolves only against an inverted-indexed column, so a statement that uses it
must read the index relation (`corpus.datasets.relation(...).index`), not the base table. The query
text is always a bound parameter, never formatted into the SQL.

Relevance: a plain SQL filter on the index relation (`e.ts >= %s`) adds to the BM25 score (+2.0
per filter, #2), while a TSQUERY filter wrapped in `::score(NULL)` matches the same rows and leaves
the score to the text predicate alone. Nothing here sorts: a BM25 sort with a tie-break key after
the score crashes SereneDB 26.10.1 (#2 round 2), so the tools sort by the score alone.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import datetime
from typing import Any, Literal, get_args

from psycopg import sql

from serenedb_log_mcp.scope import ScopeError

__all__ = [
    "EXACT_FUZZY_CHARS",
    "MAX_FUZZY_WORDS",
    "MAX_QUERY_CHARS",
    "QUERY_MODES",
    "QueryMode",
    "filter_any",
    "filter_at_or_after",
    "filter_before",
    "fuzzy_terms",
    "normalise_query",
    "text_match",
]

QueryMode = Literal["all_words", "phrase", "any_words", "fuzzy"]
QUERY_MODES: tuple[QueryMode, ...] = get_args(QueryMode)

MAX_QUERY_CHARS = 500
"""Longest query text accepted; log searches are a few words, never a pasted document."""
MAX_FUZZY_WORDS = 8
"""Most words a fuzzy query may have: each one expands to every token within one edit."""
EXACT_FUZZY_CHARS = 3
"""Fuzzy words of at most this many characters must match exactly."""

_PARSERS: dict[str, sql.SQL] = {
    "all_words": sql.SQL("websearch_to_tsquery"),
    "phrase": sql.SQL("phraseto_tsquery"),
}

# Where websearch_to_tsquery splits the text outside quotes into words, measured on 26.10.1: at ASCII
# whitespace only. A no-break space (U+00A0) and the other Unicode spaces do not split, so 'error',
# a no-break space and ';' are one word, which the dictionary reads as error.
_WEBSEARCH_SPACE_RE = re.compile(r"[ \t\n\r\f\v]+")

# How the log_text dictionary (split_text, case 'lower') splits words, measured on 26.10.1 with
# exact ts_levenshtein(term, 0) lookups: hyphens, slashes, colons and spaces split
# ('R02-M1-N0' is r02, m1, n0); underscores and dots between word characters do not
# ('dat32k_060205' and '172.16.96.116' are one token each), and a word without a letter or digit
# ('_', '__') is no token at all.
_WORD_RE = re.compile(r"\w+(?:\.\w+)*")
_HAS_WORD_RE = re.compile(r"\w")
_LETTER_OR_DIGIT_RE = re.compile(r"[^\W_]")
_DIGIT_HYPHEN_DIGIT_RE = re.compile(r"[0-9]-[0-9]")


def normalise_query(query: str | None) -> str | None:
    """The query without surrounding whitespace; None when blank.

    ScopeError when it is too long or has no letter or digit at all (only punctuation or
    underscores), which no mode can search for: the dictionary drops such text, and
    `phraseto_tsquery` rejects such a query (22023 on 26.10.1, also for `_`).
    """
    if query is None or not query.strip():
        return None
    text = query.strip()
    if len(text) > MAX_QUERY_CHARS:
        raise ScopeError(
            f"query has {len(text):,} characters; pass at most {MAX_QUERY_CHARS} characters, for "
            "example the few words or the quoted phrase that the Log Entries you want contain."
        )
    if not _LETTER_OR_DIGIT_RE.search(text):
        raise ScopeError(
            f"query={text!r} has no words to search for: the index keeps words and numbers and "
            "drops punctuation. Pass the words the Log Entries contain, for example 'ciod failed'."
        )
    return text


def fuzzy_terms(query: str) -> list[tuple[str, int]]:
    """The words of a fuzzy query, lower-cased and deduplicated, each with its edit distance."""
    found = dict.fromkeys(_WORD_RE.findall(query.lower()))
    words = [word for word in found if _LETTER_OR_DIGIT_RE.search(word)]
    return [(word, 0 if len(word) <= EXACT_FUZZY_CHARS else 1) for word in words]


def text_match(
    column: sql.Composable, query: str, mode: QueryMode = "all_words"
) -> tuple[sql.Composed, list[Any]]:
    """`column @@ <query in mode>` and its parameter list; `column` must be an indexed text column."""
    if mode == "all_words":
        _check_unquoted_words(query)
        _check_quoted_phrases(query)
    if mode in _PARSERS:
        return sql.SQL("{} @@ {}(%s)").format(column, _PARSERS[mode]), [query]
    if mode == "any_words":
        return sql.SQL("{} @@ %s").format(column), [query]
    if mode == "fuzzy":
        return _fuzzy(column, query)
    raise ValueError(f"unknown full-text mode {mode!r}; valid: {', '.join(QUERY_MODES)}")


def _check_unquoted_words(query: str) -> None:
    """ScopeError for a word outside double quotes in an all_words query that holds no letter or digit.

    SereneDB 26.10.1's `websearch_to_tsquery` splits the text outside quotes into words at ASCII
    whitespace (`_WEBSEARCH_SPACE_RE`) and keeps a word without a letter or digit (`;`, `,`, `...`,
    `(`, `_`, `=>`, `…`) as a term, while the `log_text` dictionary indexes only words and numbers:
    on the Base Corpus `parity error` matches 121,310 BGL Log Entries and `parity error ;` none (#80).
    Not refused, because they change nothing: a word that starts with `-` (`-`, `--`, `->`, `-;`)
    excludes such a term, and punctuation attached to a word (`ciod:`, `(error)`) is analysed away
    with it. Splitting at the quotes puts the unquoted parts at the even positions.
    """
    found = [
        word
        for part in query.split('"')[::2]
        for word in _WEBSEARCH_SPACE_RE.split(part)
        if word and not word.startswith("-") and not _LETTER_OR_DIGIT_RE.search(word)
    ]
    if found:
        words = list(dict.fromkeys(found))
        listed = ", ".join(repr(word) for word in words)
        what, it = ("a word", "it") if len(words) == 1 else ("words", "them")
        raise ScopeError(
            f"query={query!r} has {what} without a letter or digit ({listed}): the index keeps only "
            "words and numbers, so no Log Entry contains such a word, and a query that requires it "
            f"matches none. Remove {it}; punctuation attached to a word, as in 'ciod:', is ignored."
        )


def _check_quoted_phrases(query: str) -> None:
    """ScopeError for a double-quoted part of an all_words query that holds no letter or digit.

    SereneDB 26.10.1's `websearch_to_tsquery` turns each quoted part, closed or open to the end of
    the query, into `ts_phrase(part)` and fails with 22023 ("produced no tokens after analysis") when
    the part has characters but no word, for example `error ";"` or `error " "`; an empty `""` and a
    lone `"` are accepted. Splitting at the quotes puts the quoted parts at the odd positions.
    """
    for part in query.split('"')[1::2]:
        if part and not _LETTER_OR_DIGIT_RE.search(part):
            raise ScopeError(
                f'query={query!r} has a quoted phrase without a word ("{part}"): a phrase in double '
                "quotes must hold the words to find in order. Remove those quotes, or put words "
                "between them."
            )


def _fuzzy(column: sql.Composable, query: str) -> tuple[sql.Composed, list[Any]]:
    terms = fuzzy_terms(query)
    if not terms:
        raise ScopeError(f"query={query!r} has no words to search for; pass the words to match.")
    if len(terms) > MAX_FUZZY_WORDS:
        raise ScopeError(
            f"mode='fuzzy' takes at most {MAX_FUZZY_WORDS} words, and query={query!r} has "
            f"{len(terms)}. Keep the few distinctive words, or use mode='all_words' for longer queries."
        )
    parts = [sql.SQL("ts_levenshtein(%s, {})").format(sql.Literal(distance)) for _, distance in terms]
    words = [word for word, _ in terms]
    if len(parts) == 1:
        return sql.SQL("{} @@ {}").format(column, parts[0]), words
    return sql.SQL("{} @@ ({})").format(column, sql.SQL(" && ").join(parts)), words


def filter_any(column: sql.Composable, values: Sequence[Any]) -> tuple[sql.Composed, list[Any]]:
    """`column` is one of `values` (exact, case-sensitive), without changing the relevance score.

    `column` must be indexed verbatim (one token per value), as every filter column of the entry
    indexes is (sql/derive/90_indexes.sql).

    SereneDB 26.10.1 fails a TSQUERY term on a verbatim column whose value has a digit, a hyphen and
    a digit in it ('1-2', '1-2x', '2005-06-03T10', a UUID such as '12345678-1234-…') with XX000
    "Attempted to dereference shared_ptr that is NULL!" (measured on the dev instance; '1-x', 'x-1'
    and '1--2' work). For such values the filter is a plain `= ANY`
    comparison on the same index relation, which returns the same rows and, also measured, leaves
    every BM25 score unchanged (178,284 'ciod error' matches with component 'APP').
    """
    listed = list(values)
    if any(isinstance(value, str) and _DIGIT_HYPHEN_DIGIT_RE.search(value) for value in listed):
        return sql.SQL("{} = ANY(%s::VARCHAR[])").format(column), [listed]
    return sql.SQL("{} @@ ts_any(%s::VARCHAR[])::score(NULL)").format(column), [listed]


def filter_at_or_after(column: sql.Composable, value: datetime | float) -> tuple[sql.Composed, list[Any]]:
    """`column >= value` on an indexed column, without changing the relevance score."""
    return _range("ts_ge", column, value)


def filter_before(column: sql.Composable, value: datetime | float) -> tuple[sql.Composed, list[Any]]:
    """`column < value` on an indexed column, without changing the relevance score."""
    return _range("ts_lt", column, value)


def _range(function: str, column: sql.Composable, value: datetime | float) -> tuple[sql.Composed, list[Any]]:
    """`column @@ <function>(%s::<type>)::score(NULL)`, the type that of the bound (`_bound_type`).

    The cast is what makes a stored statement replay: a live call binds a real TIMESTAMP, but the
    Evidence Ledger stores the parameter as an ISO string, which an uncast `ts_lt(%s)` refuses on
    26.10.1 ("ts_lt bound type VARCHAR is incompatible with column type TIMESTAMP", #71). Measured
    on the Base Corpus in both binding modes: the cast form matches the same Log Entries with the
    same BM25 scores, live and with JSON round-tripped parameters.
    """
    template = "{} @@ " + function + "(%s::" + _bound_type(value) + ")::score(NULL)"
    return sql.SQL(template).format(column), [value]


def _bound_type(value: object) -> str:
    """The SQL type of a range bound: TIMESTAMP for a datetime (ts), DOUBLE for a float (epoch_utc)."""
    if isinstance(value, datetime):
        return "TIMESTAMP"
    if isinstance(value, float):
        return "DOUBLE"
    raise TypeError(
        f"a range bound is a datetime or a float (clock.utc_bound), not {type(value).__name__} {value!r}"
    )
