"""The curated synonym map and the query expansion of search_patterns (T14)."""

from __future__ import annotations

import re

import pytest

from serenedb_log_mcp.corpus import synonyms
from serenedb_log_mcp.corpus.synonyms import (
    MAX_SYNONYMS,
    STOPWORDS,
    SynonymMapError,
    expand,
    load_synonyms,
    parse_synonyms,
)

MAP = {"memory": ("ddr", "edram", "l3"), "crash": ("core", "signal"), "delete": ("deleting", "destroyed")}


def test_the_committed_map_loads_and_follows_its_rules() -> None:
    found = load_synonyms()

    assert 30 <= len(found) <= 100, "a small, reviewable map"
    for key, values in found.items():
        assert re.fullmatch(r"[a-z0-9_]+", key), key
        assert values, key
        assert all(re.fullmatch(r"[a-z0-9_]+", value) for value in values), key
        assert key not in values, f"{key} lists itself"
        assert len(set(values)) == len(values), f"{key} repeats a value"


def test_the_committed_map_covers_both_datasets_vocabulary() -> None:
    found = load_synonyms()

    assert "edram" in found["memory"]
    assert {"lustre", "nfs"} <= set(found["storage"])
    assert "token" in found["authentication"]


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("- memory\n- ddr\n", "mapping"),
        ("memory: ddr\n", "list"),
        ("memory: []\n", "list"),
        ("Memory: [ddr]\n", "lower-case word"),
        ("memory: [DDR]\n", "lower-case word"),
        ("memory: [no such file]\n", "lower-case word"),
        ("memory: [ddr, ddr]\n", "twice"),
        ("memory: [memory, ddr]\n", "itself"),
        ("memory: [ddr\n", "YAML"),
    ],
)
def test_a_broken_map_is_rejected_with_the_reason(text: str, problem: str) -> None:
    with pytest.raises(SynonymMapError, match=problem):
        parse_synonyms(text)


def test_an_empty_map_is_allowed() -> None:
    assert parse_synonyms("# nothing yet\n") == {}


def test_query_words_are_searched_and_their_synonyms_added() -> None:
    found = expand("memory errors", MAP)

    assert found.words == ("memory", "errors")
    assert found.synonyms == ("ddr", "edram", "l3")
    assert found.applied == {"memory": ("ddr", "edram", "l3")}
    assert found.ignored == ()


def test_words_are_lower_cased_and_deduplicated_in_order() -> None:
    assert expand("Memory MEMORY ECC memory", MAP).words == ("memory", "ecc")


def test_stopwords_are_left_out_but_reported() -> None:
    found = expand("why did the node crash", MAP)

    assert found.words == ("node", "crash")
    assert found.ignored == ("why", "did", "the")
    assert {"the", "a", "is", "of", "why"} <= STOPWORDS


def test_a_query_of_stopwords_only_keeps_its_words() -> None:
    found = expand("the of", MAP)

    assert found.words == ("the", "of")
    assert found.ignored == ()


def test_an_inflected_word_finds_the_synonyms_of_its_base_form() -> None:
    assert expand("crashed", MAP).applied == {"crashed": ("core", "signal")}
    assert expand("crashes", MAP).applied == {"crashes": ("core", "signal")}
    assert expand("deleted", MAP).applied == {"deleted": ("deleting", "destroyed")}
    assert expand("crashing", MAP).applied == {"crashing": ("core", "signal")}


def test_a_synonym_that_is_already_a_query_word_is_not_added_again() -> None:
    found = expand("memory ddr", MAP)

    assert found.words == ("memory", "ddr")
    assert found.synonyms == ("edram", "l3")


def test_synonyms_shared_by_two_words_appear_once() -> None:
    found = expand("memory crash", {"memory": ("core", "ddr"), "crash": ("core", "signal")})

    assert found.synonyms == ("core", "ddr", "signal")


def test_the_expansion_is_capped() -> None:
    many = {"memory": tuple(f"w{i}" for i in range(MAX_SYNONYMS + 10))}

    assert len(expand("memory", many).synonyms) == MAX_SYNONYMS


def test_punctuation_splits_words_like_the_index_does() -> None:
    assert expand("node-card/link: failed!", {}).words == ("node", "card", "link", "failed")


def test_the_default_map_is_the_committed_one() -> None:
    assert expand("memory").applied["memory"] == load_synonyms()["memory"]
    assert synonyms.SYNONYMS_FILE == "synonyms.yaml"
