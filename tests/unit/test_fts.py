"""`fts.py`: the full-text predicate over an inverted-index text column, shared by the tools."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import pytest
from psycopg import sql

from serenedb_log_mcp.fts import (
    MAX_FUZZY_WORDS,
    MAX_QUERY_CHARS,
    QUERY_MODES,
    filter_any,
    filter_at_or_after,
    filter_before,
    fuzzy_terms,
    normalise_query,
    text_match,
)
from serenedb_log_mcp.scope import ScopeError

MESSAGE = sql.SQL("e.{}").format(sql.Identifier("message"))
COMPONENT = sql.SQL("e.{}").format(sql.Identifier("component"))
TS = sql.SQL("e.{}").format(sql.Identifier("ts"))
EPOCH = sql.SQL("e.{}").format(sql.Identifier("epoch_utc"))


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
        text_match(MESSAGE, "error", mode="regex")  # type: ignore[arg-type]


@pytest.mark.parametrize("blank", [None, "", "   ", "\n\t"])
def test_a_blank_query_means_no_full_text_filter(blank: str | None) -> None:
    assert normalise_query(blank) is None


def test_a_query_is_stripped() -> None:
    assert normalise_query("  data TLB  ") == "data TLB"


def test_a_query_longer_than_the_limit_is_rejected_with_a_hint() -> None:
    with pytest.raises(ScopeError, match=f"at most {MAX_QUERY_CHARS} characters"):
        normalise_query("a" * (MAX_QUERY_CHARS + 1))


def test_the_modes_are_all_words_phrase_any_words_and_fuzzy() -> None:
    assert QUERY_MODES == ("all_words", "phrase", "any_words", "fuzzy")


def test_any_words_passes_the_bare_text_so_the_dictionary_splits_it_into_alternatives() -> None:
    predicate, params = text_match(MESSAGE, "ciod idoproxy", mode="any_words")

    assert predicate.as_string(None) == 'e."message" @@ %s'
    assert params == ["ciod idoproxy"]


def test_fuzzy_needs_every_word_within_one_edit_and_short_words_exactly() -> None:
    predicate, params = text_match(MESSAGE, "Faild ciod TLB", mode="fuzzy")

    assert predicate.as_string(None) == (
        'e."message" @@ (ts_levenshtein(%s, 1) && ts_levenshtein(%s, 1) && ts_levenshtein(%s, 0))'
    )
    assert params == ["faild", "ciod", "tlb"]


def test_one_fuzzy_word_needs_no_conjunction() -> None:
    predicate, params = text_match(MESSAGE, "intrupt", mode="fuzzy")

    assert predicate.as_string(None) == 'e."message" @@ ts_levenshtein(%s, 1)'
    assert params == ["intrupt"]


def test_fuzzy_words_split_like_the_index_dictionary() -> None:
    # Hyphens, slashes and colons split words; dots between word characters and underscores do not.
    assert fuzzy_terms("R02-M1-N0 ciod: /p/gb2 172.16.96.116 dat32k_060205") == [
        ("r02", 0),
        ("m1", 0),
        ("n0", 0),
        ("ciod", 1),
        ("p", 0),
        ("gb2", 0),
        ("172.16.96.116", 1),
        ("dat32k_060205", 1),
    ]


def test_fuzzy_repeated_words_are_searched_once() -> None:
    assert fuzzy_terms("error Error ERROR") == [("error", 1)]


def test_too_many_fuzzy_words_are_refused_with_a_hint() -> None:
    words = " ".join(f"word{i}" for i in range(MAX_FUZZY_WORDS + 1))

    with pytest.raises(ScopeError, match=f"at most {MAX_FUZZY_WORDS} words"):
        text_match(MESSAGE, words, mode="fuzzy")


@pytest.mark.parametrize("punctuation", ["::", "-- ..", '""'])
def test_a_query_without_any_word_is_rejected_with_a_hint(punctuation: str) -> None:
    with pytest.raises(ScopeError, match="no words to search for"):
        normalise_query(punctuation)


def test_filter_any_matches_exact_values_without_changing_the_relevance_score() -> None:
    predicate, params = filter_any(COMPONENT, ["KERNEL", "APP"])

    assert predicate.as_string(None) == 'e."component" @@ ts_any(%s::VARCHAR[])::score(NULL)'
    assert params == [["KERNEL", "APP"]]


def test_time_filters_are_score_neutral_half_open_and_cast_to_the_bound_type() -> None:
    lower, lower_params = filter_at_or_after(TS, datetime(2005, 7, 1))
    upper, upper_params = filter_before(TS, datetime(2005, 8, 1))

    assert lower.as_string(None) == 'e."ts" @@ ts_ge(%s::TIMESTAMP)::score(NULL)'
    assert upper.as_string(None) == 'e."ts" @@ ts_lt(%s::TIMESTAMP)::score(NULL)'
    assert (lower_params, upper_params) == ([datetime(2005, 7, 1)], [datetime(2005, 8, 1)])


def test_an_epoch_bound_is_cast_to_double() -> None:
    lower, params = filter_at_or_after(EPOCH, 1_120_176_000.0)
    upper, _ = filter_before(EPOCH, 1_122_854_400.0)

    assert lower.as_string(None) == 'e."epoch_utc" @@ ts_ge(%s::DOUBLE)::score(NULL)'
    assert upper.as_string(None) == 'e."epoch_utc" @@ ts_lt(%s::DOUBLE)::score(NULL)'
    assert params == [1_120_176_000.0]


@pytest.mark.parametrize("bound", [datetime(2005, 7, 1, 12, 0, 0, 363779), 1_130_662_800.363779])
def test_a_stored_bound_keeps_a_type_its_cast_reads(bound: datetime | float) -> None:
    # The Evidence Ledger stores parameters as JSON (#71): a TIMESTAMP comes back as its ISO string
    # and a DOUBLE as the same number, so the cast in the stored SQL reads either form.
    predicate, params = filter_at_or_after(TS if isinstance(bound, datetime) else EPOCH, bound)
    stored = json.loads(json.dumps(params, default=lambda value: value.isoformat()))

    cast = predicate.as_string(None).split("::")[1].split(")")[0]
    if cast == "TIMESTAMP":
        assert datetime.fromisoformat(stored[0]) == bound
    else:
        assert cast == "DOUBLE"
        assert stored == [bound]


@pytest.mark.parametrize("bound", ["2005-07-01", 1_120_176_000, True, None])
def test_a_bound_of_another_type_is_a_programming_error(bound: Any) -> None:
    # Only the clock's two bound types (clock.utc_bound): a cast the bound does not fit would fail
    # at run time, and an uncast bound fails when the Evidence is replayed (#71).
    with pytest.raises(TypeError, match="datetime or a float"):
        filter_at_or_after(TS, bound)
    with pytest.raises(TypeError, match="datetime or a float"):
        filter_before(TS, bound)


@pytest.mark.parametrize(
    "value",
    [
        "1-2",
        "0-0",
        "2005-06-03",
        "1-2.5",
        "11111111-1111-1111-1111-111111111111",
        # Also XX000 on 26.10.1 (#61 review): a digit, a hyphen and a digit anywhere in the value.
        "1-2x",
        "2005-06-03T10",
        "12345678-1234-5678-9012-34567890abcd",
    ],
)
def test_numbers_joined_by_hyphens_use_a_plain_comparison(value: str) -> None:
    # SereneDB 26.10.1 fails such a TSQUERY term on a verbatim column with XX000 "Attempted to
    # dereference shared_ptr that is NULL!"; a plain comparison returns the same rows.
    predicate, params = filter_any(COMPONENT, ["KERNEL", value])

    assert predicate.as_string(None) == 'e."component" = ANY(%s::VARCHAR[])'
    assert params == [["KERNEL", value]]


@pytest.mark.parametrize(
    "value", ["1--2", "-1", "1-", "x-1", "1-x", "b9000564-fe1a-409b-b8cc-1e88b294cd1d", "12345"]
)
def test_other_values_keep_the_score_neutral_tsquery(value: str) -> None:
    predicate, _ = filter_any(COMPONENT, [value])

    assert "::score(NULL)" in predicate.as_string(None)
