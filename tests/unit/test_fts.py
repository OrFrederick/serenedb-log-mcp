"""`fts.py`: the full-text predicate over an inverted-index text column, shared by the tools."""

from __future__ import annotations

import pytest
from psycopg import sql

from serenedb_log_mcp.fts import MAX_QUERY_CHARS, normalise_query, text_match
from serenedb_log_mcp.scope import ScopeError

MESSAGE = sql.SQL("e.{}").format(sql.Identifier("message"))


def test_all_words_is_the_default_and_uses_websearch_syntax() -> None:
    predicate, params = text_match(MESSAGE, "data TLB")

    assert predicate.as_string(None) == 'e."message" @@ websearch_to_tsquery(%s)'
    assert params == ["data TLB"]


def test_phrase_mode_matches_the_words_in_order() -> None:
    predicate, params = text_match(MESSAGE, "data TLB error", mode="phrase")

    assert predicate.as_string(None) == 'e."message" @@ phraseto_tsquery(%s)'
    assert params == ["data TLB error"]


def test_the_query_text_is_bound_never_formatted_into_the_sql() -> None:
    predicate, params = text_match(MESSAGE, "x'); DROP TABLE t; --")

    assert "DROP" not in predicate.as_string(None)
    assert params == ["x'); DROP TABLE t; --"]


def test_an_unknown_mode_is_a_programming_error() -> None:
    with pytest.raises(ValueError, match="mode"):
        text_match(MESSAGE, "error", mode="fuzzy")  # type: ignore[arg-type]


@pytest.mark.parametrize("blank", [None, "", "   ", "\n\t"])
def test_a_blank_query_means_no_full_text_filter(blank: str | None) -> None:
    assert normalise_query(blank) is None


def test_a_query_is_stripped() -> None:
    assert normalise_query("  data TLB  ") == "data TLB"


def test_a_query_longer_than_the_limit_is_rejected_with_a_hint() -> None:
    with pytest.raises(ScopeError, match=f"at most {MAX_QUERY_CHARS} characters"):
        normalise_query("a" * (MAX_QUERY_CHARS + 1))
